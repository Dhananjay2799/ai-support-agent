"""Voice spike: does GPT-Live hand a spoken question to OUR backend and speak OUR answer?

    EVAL_DATABASE_URL=... uv run python -m scripts.voice_spike ["question to speak"]

No microphone needed:
1. Synthesize the customer's question with TTS (mono PCM16, 24 kHz).
2. Open a primary GPT-Live WebSocket with client delegation.
3. Stream the audio at real-time pace, then silence so turn detection fires.
4. On `session.delegation.created`, rebuild the user's words from transcript deltas,
   run our retrieval + guardrails, and reply with `session.commentary.append`.
5. Report what the voice model said back, plus timings.

Needs a database with the knowledge base loaded (run scripts.run_evals first).
"""

import asyncio
import base64
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Final

from openai import AsyncOpenAI
from openai.types.live import (
    CommentaryAppendedEvent,
    DelegationCreatedEvent,
    ErrorEvent,
    InputTranscriptDeltaEvent,
    OutputTranscriptDeltaEvent,
    SessionClosedEvent,
    SessionStartedEvent,
    SessionStartEventParam,
)
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.config import get_settings
from app.llm.client import get_openai
from app.rag.embeddings import OpenAIEmbedder
from app.rag.guardrails import OpenAISupportJudge, assess, format_passages, load_thresholds
from app.rag.retriever import HybridRetriever

SAMPLE_RATE: Final = 24_000
CHUNK_MS = 100
LIVE_MODEL = "gpt-live-1.1"
INSTRUCTIONS = """\
You are the voice of Acme Cloud customer support.
Delegate every customer question to the backend and wait for its result.
Never answer questions about Acme Cloud from your own knowledge.
When the backend's result arrives, say it briefly and naturally, keeping every number exact.
"""


@dataclass
class Timeline:
    started: float = field(default_factory=time.perf_counter)
    marks: dict[str, float] = field(default_factory=dict)

    def mark(self, name: str) -> None:
        self.marks.setdefault(name, round((time.perf_counter() - self.started) * 1000))


async def synthesize(client: AsyncOpenAI, text: str) -> bytes:
    response = await client.audio.speech.create(
        model="gpt-4o-mini-tts", voice="alloy", input=text, response_format="pcm"
    )
    return response.content


async def grounded_answer(client: AsyncOpenAI, question: str, tier: str | None) -> tuple[str, str]:
    settings = get_settings()
    embedder = OpenAIEmbedder(client)
    conn = await AsyncConnection.connect(
        os.environ["EVAL_DATABASE_URL"], row_factory=dict_row, autocommit=True
    )
    try:
        retrieval = await HybridRetriever(embedder).search(conn, question, tier=tier)
    finally:
        await conn.close()
    assessment = await assess(
        question,
        retrieval,
        judge=OpenAISupportJudge(client, settings.judge_model, settings.judge_reasoning_effort),
        thresholds=load_thresholds(),
    )
    if assessment.decision == "escalate":
        return (
            "I can't find that in our documentation, so I've asked a support specialist to follow up.",
            f"escalate ({assessment.reason}, cosine {assessment.top_cosine:.2f})",
        )
    response = await client.responses.create(
        model=settings.agent_model,
        instructions="Answer the customer's question in one or two short spoken sentences, "
        "using only these passages. Keep numbers exact.",
        input=f"Question: {question}\n\nPassages:\n{format_passages(retrieval.chunks)}",
    )
    return response.output_text, f"answer ({assessment.band} band, cosine {assessment.top_cosine:.2f})"


async def run(question: str, tier: str | None) -> None:
    client = get_openai()
    audio = await synthesize(client, question)
    print(f"spoken question: {question!r} ({len(audio) / 2 / SAMPLE_RATE:.1f}s of audio)")

    timeline = Timeline()
    heard: list[str] = []
    said: list[str] = []
    replies: list[asyncio.Task[None]] = []
    done = asyncio.Event()

    async with client.live.connect() as conn:

        async def stream_audio() -> None:
            step = SAMPLE_RATE * 2 * CHUNK_MS // 1000
            silence = bytes(step)
            for offset in range(0, len(audio), step):
                await conn.send(
                    {
                        "type": "session.input_audio.append",
                        "audio": base64.b64encode(audio[offset : offset + step]).decode(),
                    }
                )
                await asyncio.sleep(CHUNK_MS / 1000)
            timeline.mark("question_audio_sent")
            for _ in range(40):  # 4 s of silence lets turn detection end the turn
                await conn.send(
                    {"type": "session.input_audio.append", "audio": base64.b64encode(silence).decode()}
                )
                await asyncio.sleep(CHUNK_MS / 1000)

        async def reply(delegation_id: str) -> None:
            utterance = "".join(heard).strip() or question
            answer, decision = await grounded_answer(client, utterance, tier)
            timeline.mark("backend_answer_ready")
            print(f"backend decision: {decision}\nbackend answer:   {answer}")
            await conn.send(
                {
                    "type": "session.commentary.append",
                    "event_id": "spike_result",
                    "delegation_id": delegation_id,
                    "content": answer,
                }
            )

        start: SessionStartEventParam = {
            "type": "session.start",
            "session": {
                "model": LIVE_MODEL,
                "instructions": INSTRUCTIONS,
                "audio": {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE}},
                "delegation": {"type": "client"},
            },
        }
        await conn.send(start)
        sender: asyncio.Task[None] | None = None
        speaking = asyncio.Event()

        async def close_when_quiet() -> None:
            # Give the whole exchange 60 s; once it talks, close 4 s after it stops.
            timeout = 60.0
            while not done.is_set():
                try:
                    await asyncio.wait_for(speaking.wait(), timeout)
                except TimeoutError:
                    break
                speaking.clear()
                timeout = 4.0
            await conn.send({"type": "session.close"})

        guard = asyncio.create_task(close_when_quiet())
        async for event in conn:
            if isinstance(event, SessionStartedEvent):
                timeline.mark("session_started")
                sender = asyncio.create_task(stream_audio())
            elif isinstance(event, InputTranscriptDeltaEvent):
                heard.append(event.delta)
            elif isinstance(event, DelegationCreatedEvent):
                timeline.mark("delegation_created")
                print(f"delegation: {event.delegation.id} (target {event.delegation.target})")
                replies.append(asyncio.create_task(reply(event.delegation.id)))
            elif isinstance(event, OutputTranscriptDeltaEvent):
                if "backend_answer_ready" in timeline.marks:
                    timeline.mark("first_words_after_answer")
                said.append(event.delta)
                speaking.set()
            elif isinstance(event, CommentaryAppendedEvent):
                timeline.mark("commentary_acknowledged")
            elif isinstance(event, ErrorEvent):
                print(f"error event: {event}")
            elif isinstance(event, SessionClosedEvent):
                done.set()
                print(f"usage: {event.usage}")
                break
        guard.cancel()
        if sender:
            sender.cancel()
        for task in replies:
            await task

    print(f"\nvoice model heard: {''.join(heard).strip()!r}")
    print(f"voice model said:  {''.join(said).strip()!r}")
    print(f"timeline (ms):     {timeline.marks}")


if __name__ == "__main__":
    asked = sys.argv[1] if len(sys.argv) > 1 else "Hi, how long does a refund take to show up on my card?"
    asyncio.run(run(asked, tier="pro"))
