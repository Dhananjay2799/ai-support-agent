"""Runs a support call: GPT-Live talks, our agent graph decides.

GPT-Live (client delegation) listens and speaks. Whenever the caller asks for something,
it emits `session.delegation.created`, which carries no text: the request is whatever the
caller said since the previous delegation, rebuilt from input transcript deltas. The bridge
runs that utterance through the agent graph, on the same session as chat, streams the
turn's events to the browser, and hands the answer back with `session.commentary.append`
for GPT-Live to say.

Delegations run one at a time, in order: two turns can't write the same checkpoint at
once. If the caller has already moved on to a new request when a turn finishes (they
interrupted), its result goes in as silent context (`session.thinking.append`) so the
voice doesn't answer a question the caller abandoned.
"""

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.agent.errors import error_event
from app.contracts import AgentEvent, DoneEvent, VoiceEndedEvent, VoiceEndReason, VoiceEvent, VoiceTurnEvent
from app.observability.usage import UsageMeter, current_meter
from app.voice.session import MAX_CALL_SECONDS

log = logging.getLogger(__name__)

CITATION_MARKER = re.compile(r"\s*\[[a-z0-9-]+#\d+\]")
MAX_SPOKEN_CHARS = 1_600  # commentary is capped at 500 tokens
DIDNT_CATCH = "I didn't catch that. Could you say it again?"
FAILED = "Sorry, something went wrong on my side. Could you say that again?"


class LiveLink(Protocol):
    """The part of a GPT-Live connection (sideband or primary WebSocket) the bridge uses."""

    async def send(self, event: Any) -> None: ...

    def __aiter__(self) -> AsyncIterator[Any]: ...


TurnRunner = Callable[[str], AsyncIterator[AgentEvent]]


def speakable(text: str) -> str:
    """Answer text without citation markers, short enough for one commentary append."""
    spoken = re.sub(r"[ \t]{2,}", " ", CITATION_MARKER.sub("", text)).strip()
    if len(spoken) > MAX_SPOKEN_CHARS:
        spoken = spoken[:MAX_SPOKEN_CHARS].rsplit(" ", 1)[0] + "…"
    return spoken


@dataclass
class VoiceBridge:
    link: LiveLink
    run_turn: TurnRunner
    max_seconds: float = MAX_CALL_SECONDS
    settle_s: float = 0.4  # transcript quiet this long = the caller's words are in
    settle_max_s: float = 1.5

    _heard: list[str] = field(default_factory=list)
    _cut: int = 0
    _last_heard: float = 0.0
    _delegations: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    _out: asyncio.Queue[VoiceEvent | None] = field(default_factory=asyncio.Queue)
    _reason: VoiceEndReason | None = None

    async def events(self) -> AsyncIterator[VoiceEvent]:
        """Yield the call's events until GPT-Live closes the session."""
        started = time.monotonic()
        worker = asyncio.create_task(self._work())
        timer = asyncio.create_task(self._time_limit())
        reader = asyncio.create_task(self._read())
        reader.add_done_callback(lambda _: self._out.put_nowait(None))
        try:
            while (event := await self._out.get()) is not None:
                yield event
            if reader.exception() is not None:
                log.error("voice session connection failed", exc_info=reader.exception())
                self._reason = "error"
            yield VoiceEndedEvent(
                reason=self._reason or "hangup", duration_ms=round((time.monotonic() - started) * 1000, 1)
            )
        finally:
            for task in (reader, worker, timer):
                task.cancel()
            await asyncio.gather(reader, worker, timer, return_exceptions=True)

    # --- tasks -----------------------------------------------------------------------

    async def _read(self) -> None:
        async for event in self.link:
            kind = getattr(event, "type", None)
            if kind == "session.input_transcript.delta":
                self._heard.append(event.delta)
                self._last_heard = time.monotonic()
            elif kind == "session.delegation.created":
                self._delegations.put_nowait(event.delegation.id)
            elif kind == "error":
                log.warning("GPT-Live error event: %s", getattr(event, "error", event))
            elif kind == "session.closed":
                return

    async def _time_limit(self) -> None:
        await asyncio.sleep(self.max_seconds)
        self._reason = "time_limit"
        await self._send({"type": "session.close"})

    async def _work(self) -> None:
        while True:
            delegation_id = await self._delegations.get()
            utterance = await self._take_utterance()
            if not utterance:
                await self._speak(delegation_id, DIDNT_CATCH)
                continue
            self._out.put_nowait(VoiceTurnEvent(utterance=utterance))
            answer = await self._run(delegation_id, utterance)
            if self._delegations.empty():
                await self._speak(delegation_id, answer)
            else:  # the caller has already asked something else
                await self._send(
                    {
                        "type": "session.thinking.append",
                        "delegation_id": delegation_id,
                        "content": f"Result of an earlier request the caller moved on from: {answer}",
                    }
                )

    # --- helpers ---------------------------------------------------------------------

    async def _take_utterance(self) -> str:
        """Everything heard since the last request, once the transcript has caught up.

        Delegation can fire before the last words are transcribed, so wait until no new
        words have arrived for `settle_s` (at most `settle_max_s`).
        """
        deadline = time.monotonic() + self.settle_max_s
        while time.monotonic() < deadline:
            quiet = time.monotonic() - self._last_heard >= self.settle_s
            if quiet and len(self._heard) > self._cut:
                break
            await asyncio.sleep(0.05)
        utterance = "".join(self._heard[self._cut :]).strip()
        self._cut = len(self._heard)
        return utterance

    async def _run(self, delegation_id: str, utterance: str) -> str:
        """One agent turn; streams its events and returns the text to speak."""
        meter = UsageMeter()
        token = current_meter.set(meter)
        started = time.time()
        text: list[str] = []
        try:
            async for event in self.run_turn(utterance):
                self._out.put_nowait(event)
                if event.type == "token":
                    text.append(event.text)
                elif event.type == "step" and event.status != "started":
                    # Silent progress, so the voice can say what's happening if asked.
                    await self._send(
                        {
                            "type": "session.thinking.append",
                            "delegation_id": delegation_id,
                            "content": f"Progress: {event.label.rstrip('…')}"
                            + (f" ({event.detail})" if event.detail else ""),
                        }
                    )
        except Exception as error:
            log.exception("voice turn failed")
            self._out.put_nowait(error_event(error))
            self._out.put_nowait(
                DoneEvent(
                    turn_id=f"failed-{delegation_id}",
                    usage=meter.usage(),
                    cost_usd=round(meter.cost_usd, 6),
                    latency_ms=round((time.time() - started) * 1000, 1),
                )
            )
            return FAILED
        finally:
            with contextlib.suppress(ValueError):
                current_meter.reset(token)
        return speakable("".join(text)) or FAILED

    async def _speak(self, delegation_id: str, content: str) -> None:
        await self._send(
            {"type": "session.commentary.append", "delegation_id": delegation_id, "content": content}
        )

    async def _send(self, event: dict[str, Any]) -> None:
        try:
            await self.link.send(event)
        except Exception:  # the session may already be closing; nothing to recover
            log.warning("could not send %s to GPT-Live", event.get("type"), exc_info=True)
