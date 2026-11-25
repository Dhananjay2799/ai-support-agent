# ruff: noqa: ASYNC110  - the script polls a live call's state on purpose
"""A real voice call against GPT-Live, with synthesized speech instead of a microphone.

    EVAL_DATABASE_URL=... uv run python -m scripts.voice_e2e

Runs the production voice bridge and agent graph against the eval database. Only the
transport differs from the browser: audio goes over GPT-Live's primary WebSocket here
(WebRTC needs UDP, which this environment may not have). The script checks:

1. Text to voice memory: a chat turn first, then the call starts from that history.
2. An action by voice: the duplicate charge is refunded and the answer is spoken.
3. Interruption: the caller talks over the spoken answer with a new question; the voice
   stops and answers the new question.
4. Voice to text memory: a chat turn after the call knows what happened on it.
"""

import asyncio
import base64
import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import websockets
from langchain_core.runnables import RunnableConfig
from openai import AsyncOpenAI
from openai.types.live import SessionConfigParam, SessionStartEventParam
from openai.types.live.audio_format_param import AudioPCM
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.agent.graph import run_turn
from app.contracts import AgentEvent
from app.llm.client import get_openai
from app.voice.bridge import VoiceBridge
from app.voice.session import LIVE_VOICE, history_from_items, session_config
from scripts.run_scenarios import local_graph

RATE = 24_000
PCM_24K: AudioPCM = {"type": "audio/pcm", "rate": 24_000}
CHUNK = RATE * 2 // 10  # 100 ms of mono PCM16
PERSONA = "priya"
ASK = "Hi, I think I was charged twice this month for my subscription. Can you fix that please?"
INTERRUPT = "Sorry to cut in. What does my plan include, storage-wise?"


async def tts(client: AsyncOpenAI, text: str) -> bytes:
    response = await client.audio.speech.create(
        model="gpt-4o-mini-tts", voice="alloy", input=text, response_format="pcm"
    )
    return response.content


@dataclass
class Observer:
    """Tees GPT-Live events to the bridge while recording what was said, and when."""

    conn: Any
    started: float = field(default_factory=time.monotonic)
    said: list[tuple[float, str]] = field(default_factory=list)
    heard: list[str] = field(default_factory=list)
    session_started: asyncio.Event = field(default_factory=asyncio.Event)
    answered: asyncio.Event = field(default_factory=asyncio.Event)
    timeline: list[dict[str, Any]] = field(default_factory=list)

    def now(self) -> float:
        return time.monotonic() - self.started

    async def send(self, event: Any) -> None:
        if event["type"] == "session.commentary.append":
            self.answered.set()
        if event["type"] in ("session.commentary.append", "session.close"):
            print(f"  [{self.now():5.1f}s] -> {event['type']}: {event.get('content', '')!r}")
        await self.conn.send(event)

    async def __aiter__(self) -> AsyncIterator[Any]:
        async for event in self.conn:
            kind = getattr(event, "type", "")
            if kind.endswith("_transcript.delta"):
                self.timeline.append(
                    {"type": kind, "delta": event.delta, "start_ms": event.start_ms, "end_ms": event.end_ms}
                )
            if kind == "session.started":
                self.session_started.set()
            elif kind == "session.output_transcript.delta":
                if not self.said:
                    print(f"  [{self.now():5.1f}s] <- voice starts speaking")
                self.said.append((self.now(), event.delta))
            elif kind == "session.input_transcript.delta":
                self.heard.append(event.delta)
            elif kind == "session.delegation.created":
                print(f"  [{self.now():5.1f}s] <- delegation {event.delegation.id}")
            elif kind == "error":
                print(f"  [{self.now():5.1f}s] <- error {event}")
            yield event

    def quiet_for(self, seconds: float) -> bool:
        last = self.said[-1][0] if self.said else 0.0
        return self.now() - last >= seconds

    def speech(self) -> list[str]:
        """Output transcript split into utterances at pauses over 0.8 s."""
        parts: list[str] = []
        last = -10.0
        for at, delta in self.said:
            if at - last > 0.8 or not parts:
                parts.append("")
            parts[-1] += delta
            last = at
        return [p.strip() for p in parts if p.strip()]


class Mic:
    """A microphone that streams silence in real time, or speech when given some."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.speaking = asyncio.Event()

    def say(self, audio: bytes) -> None:
        self.queue.put_nowait(audio)

    async def run(self) -> None:
        try:
            await self._stream()
        except websockets.ConnectionClosed:
            return  # the call ended

    async def _stream(self) -> None:
        silence = bytes(CHUNK)
        while True:
            audio = b"" if self.queue.empty() else self.queue.get_nowait()
            if audio:
                self.speaking.set()
            for offset in range(0, max(len(audio), 1), CHUNK):
                chunk = audio[offset : offset + CHUNK] if audio else silence
                await self.conn.send(
                    {"type": "session.input_audio.append", "audio": base64.b64encode(chunk).decode()}
                )
                await asyncio.sleep(0.1)
            self.speaking.clear()


async def chat(graph: Any, session: str, message: str) -> str:
    text: list[str] = []
    steps: list[str] = []
    async for event in run_turn(graph, session_id=session, message=message, persona_id=PERSONA):
        if event.type == "token":
            text.append(event.text)
        elif event.type == "step" and event.status != "started":
            steps.append(f"{event.tool}({event.detail or ''})")
    print(f"  steps: {', '.join(steps) or 'none'}")
    return "".join(text).strip()


async def main() -> None:
    client = get_openai()
    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        os.environ["EVAL_DATABASE_URL"],
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None},
        open=False,
    )
    await pool.open()
    async with pool.connection() as conn:
        await conn.execute("select public.reset_demo_data()")
    graph = local_graph(pool)
    session = str(uuid.uuid4())
    ask, interrupt = await asyncio.gather(tts(client, ASK), tts(client, INTERRUPT))

    print("1. Chat before the call")
    print(f"  reply: {await chat(graph, session, 'Hi! Which plan am I on?')!r}")

    config: RunnableConfig = {"configurable": {"thread_id": session}}
    values = (await graph.aget_state(config)).values
    history = history_from_items(values.get("items", []))
    # Same config as the browser's WebRTC session, adapted to a WebSocket: audio format
    # instead of media negotiation, and no browser data-channel lockdown (WebRTC only).
    webrtc = session_config(caller_first_name="Priya", history=history)
    live: SessionConfigParam = {
        "model": webrtc["model"],
        "instructions": webrtc.get("instructions"),
        "delegation": webrtc.get("delegation"),
        "input": history,
        "audio": {"format": PCM_24K, "output": {"voice": LIVE_VOICE}},
    }
    print(f"\n2. Voice call (starts with {len(history)} history messages)")

    agent_steps: list[str] = []
    turns: list[str] = []

    def runner(utterance: str) -> AsyncIterator[AgentEvent]:
        turns.append(utterance)
        return run_turn(graph, session_id=session, message=utterance, persona_id=PERSONA, channel="voice")

    async with client.live.connect() as conn:
        observer = Observer(conn)
        start: SessionStartEventParam = {"type": "session.start", "session": live}
        await conn.send(start)
        mic = Mic(conn)
        bridge = VoiceBridge(observer, runner)

        async def script() -> None:
            await observer.session_started.wait()
            mic_task = asyncio.create_task(mic.run())
            # Let the greeting finish, then ask.
            while not (observer.said and observer.quiet_for(1.2)) and observer.now() < 15:
                await asyncio.sleep(0.1)
            print(f"  [{observer.now():5.1f}s] caller: {ASK!r}")
            mic.say(ask)
            # Wait until the refund answer is being spoken, then talk over it.
            while not observer.answered.is_set() and observer.now() < 90:
                await asyncio.sleep(0.05)
            answer_started = len(observer.said)
            while len(observer.said) == answer_started and observer.now() < 90:
                await asyncio.sleep(0.05)
            await asyncio.sleep(1.5)
            print(f"  [{observer.now():5.1f}s] caller interrupts: {INTERRUPT!r}")
            mic.say(interrupt)
            # Wait for the second answer, then hang up once the line is quiet.
            while len(turns) < 2 and observer.now() < 120:
                await asyncio.sleep(0.1)
            await asyncio.sleep(2)
            while not observer.quiet_for(3) and observer.now() < 150:
                await asyncio.sleep(0.1)
            await observer.send({"type": "session.close"})
            await asyncio.sleep(3)
            mic_task.cancel()

        driver = asyncio.create_task(script())
        async for event in bridge.events():
            if event.type == "voice_turn":
                print(f"  [{observer.now():5.1f}s] agent turn for: {event.utterance!r}")
            elif event.type == "step" and event.status != "started":
                agent_steps.append(f"{event.tool}({event.detail or ''})")
                print(f"  [{observer.now():5.1f}s]   step {event.tool}: {event.detail}")
            elif event.type in ("escalation", "error", "voice_ended"):
                print(f"  [{observer.now():5.1f}s]   {event.type}: {event.model_dump(exclude={'type'})}")
        driver.cancel()

    if path := os.environ.get("VOICE_TIMELINE"):
        await asyncio.to_thread(Path(path).write_text, json.dumps(observer.timeline, indent=1), "utf-8")
    print("\n  What the voice said:")
    for line in observer.speech():
        print(f"   - {line}")

    print("\n3. Chat after the call")
    print(f"  reply: {await chat(graph, session, 'Thanks. What did you just refund? When will I get it?')!r}")
    await pool.close()

    refunded = any(s.startswith("issue_refund(") and "refunded" in s for s in agent_steps)
    print(f"\nRefund issued by voice: {refunded}. Agent turns: {len(turns)}.")


if __name__ == "__main__":
    asyncio.run(main())
