"""Runs a support call: GPT-Live talks, our agent graph decides.

GPT-Live (client delegation) listens and speaks. Whenever the caller asks for something,
it emits `session.delegation.created`, which carries no text, only `offset_ms`: where on
the session timeline it was delegated (the end of the caller's last word). Transcript
words carry timestamps on the same timeline, so the request is every word spoken before
that offset and after the previous one. Words can arrive after the delegation, so the
bridge waits until the transcript reaches the offset. It then runs the request through
the agent graph, on the same session as chat, streams the turn's events to the browser,
and hands the answer back with `session.commentary.append` for GPT-Live to say.

Delegations run one at a time, in order: two turns can't write the same checkpoint at
once. If the caller has already moved on to a new request when a turn finishes (they
interrupted), its result goes in as silent context (`session.thinking.append`) so the
voice doesn't answer a question the caller abandoned. If the call ends mid-turn, the turn
still finishes, so its answer and any refund or ticket show up in the conversation.
"""

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.agent.errors import error_event
from app.contracts import (
    AgentEvent,
    DoneEvent,
    TraceEvent,
    VoiceEndedEvent,
    VoiceEndReason,
    VoiceEvent,
    VoiceTurnEvent,
)
from app.observability.tracing import span
from app.observability.turn import TurnTelemetry
from app.observability.usage import live_cost
from app.voice.session import LIVE_MODEL, MAX_CALL_SECONDS

log = logging.getLogger(__name__)

CITATION_MARKER = re.compile(r"\s*\[[a-z0-9-]+#\d+\]")
MAX_SPOKEN_CHARS = 1_600  # commentary is capped at 500 tokens
DIDNT_CATCH = "I didn't catch that. Could you say it again?"
TRANSCRIPT_WAIT_S = 3.0  # most a request waits for its words to be transcribed
TRANSCRIPT_QUIET_S = 1.5  # ...or this long without new words, once some have arrived
WORD_END_SLACK_MS = 300  # a word ending this close to the offset means the transcript caught up
# GPT-Live may delegate a beat before the caller finishes ("...can you fix that" | "please").
# Words running straight on from a request that hasn't ended its sentence still belong to it.
TAIL_WAIT_S = 0.8
TAIL_MS = 1_000  # how far past the offset such a word may start
CONTIGUOUS_MS = 600  # the most silence between words of one request
SENTENCE_END = re.compile(r"[.?!]\W*$")
FINISH_TURN_S = 30.0  # after the call ends, how long an in-flight turn may still run
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


@dataclass(frozen=True)
class Word:
    text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class Delegation:
    id: str
    offset_ms: int


_CLOSED = object()  # the GPT-Live session is over
_IDLE = object()  # the worker finished a turn


@dataclass
class VoiceBridge:
    link: LiveLink
    run_turn: TurnRunner
    max_seconds: float = MAX_CALL_SECONDS
    transcript_wait_s: float = TRANSCRIPT_WAIT_S
    transcript_quiet_s: float = TRANSCRIPT_QUIET_S
    finish_turn_s: float = FINISH_TURN_S
    on_trace: Callable[[TraceEvent], Awaitable[None]] | None = None  # e.g. save it

    _words: list[Word] = field(default_factory=list)
    _taken_until_ms: int = -1  # words starting before this belong to an earlier request
    _last_word_at: float = 0.0
    _word_arrived: asyncio.Event = field(default_factory=asyncio.Event)
    _delegations: asyncio.Queue[Delegation] = field(default_factory=asyncio.Queue)
    _out: asyncio.Queue[Any] = field(default_factory=asyncio.Queue)
    _closed: bool = False
    _in_turn: bool = False
    _reason: VoiceEndReason | None = None
    _usage: Any = None  # GPT-Live's final usage, from session.closed

    async def events(self) -> AsyncIterator[VoiceEvent]:
        """Yield the call's events until the session closes and any turn in flight is done."""
        started = time.monotonic()
        worker = asyncio.create_task(self._work())
        timer = asyncio.create_task(self._time_limit())
        reader = asyncio.create_task(self._read())
        reader.add_done_callback(lambda _: self._out.put_nowait(_CLOSED))
        finish_by: float | None = None
        try:
            while True:
                if finish_by is not None and not self._in_turn and self._out.empty():
                    break  # closed, no turn running, and everything it produced is sent
                timeout = None if finish_by is None else max(0.0, finish_by - time.monotonic())
                try:
                    item = await asyncio.wait_for(self._out.get(), timeout)
                except TimeoutError:
                    log.warning("voice turn still running %.0f s after the call ended", self.finish_turn_s)
                    break
                if item is _CLOSED:
                    self._closed = True
                    finish_by = time.monotonic() + self.finish_turn_s
                elif item is not _IDLE:
                    yield item
            if not reader.cancelled() and reader.exception() is not None:
                log.error("voice session connection failed", exc_info=reader.exception())
                self._reason = "error"
            yield self._ended(time.monotonic() - started)
        finally:
            for task in (reader, worker, timer):
                task.cancel()
            await asyncio.gather(reader, worker, timer, return_exceptions=True)

    # --- tasks -----------------------------------------------------------------------

    async def _read(self) -> None:
        async for event in self.link:
            kind = getattr(event, "type", None)
            if kind == "session.input_transcript.delta":
                if event.start_ms < self._taken_until_ms:
                    log.info("dropping late word %r of an earlier request", event.delta)
                    continue
                self._words.append(Word(event.delta, event.start_ms, event.end_ms))
                self._last_word_at = time.monotonic()
                self._word_arrived.set()
            elif kind == "session.delegation.created":
                self._delegations.put_nowait(Delegation(event.delegation.id, event.offset_ms))
            elif kind == "error":
                log.warning("GPT-Live error event: %s", getattr(event, "error", event))
            elif kind == "session.closed":
                self._usage = getattr(event, "usage", None)
                return

    async def _time_limit(self) -> None:
        await asyncio.sleep(self.max_seconds)
        self._reason = "time_limit"
        await self._send({"type": "session.close"})

    async def _work(self) -> None:
        while True:
            delegation = await self._delegations.get()
            if self._closed:
                return
            delegation_id = delegation.id
            with TurnTelemetry(channel="voice", delegation_id=delegation_id) as telemetry:
                with span("waiting for the transcript", "voice", offset_ms=delegation.offset_ms) as wait:
                    utterance = await self._take_utterance(delegation.offset_ms)
                    wait.set(utterance=utterance)
                log.info("delegation %s at %d ms: %r", delegation_id, delegation.offset_ms, utterance[:160])
                if not utterance:
                    await self._speak(delegation_id, DIDNT_CATCH)
                    continue
                if self._closed:
                    return
                telemetry.annotate(message=utterance)
                self._in_turn = True
                try:
                    self._out.put_nowait(VoiceTurnEvent(utterance=utterance))
                    answer, trace = await self._run(delegation_id, utterance, telemetry)
                finally:
                    self._in_turn = False
                    self._out.put_nowait(_IDLE)
            if self.on_trace is not None:
                await self.on_trace(trace)
            if self._closed:
                return  # nobody left to say it to; the answer is in the conversation
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

    async def _take_utterance(self, offset_ms: int) -> str:
        """The words of the request delegated at `offset_ms`.

        Waits until the transcript reaches the offset (a word ending near it, or one starting
        after it), or has been quiet for a while, or `transcript_wait_s` at most. Then takes
        the words that started before the offset; later ones belong to the next request.
        """

        def caught_up() -> bool:
            return any(
                w.end_ms >= offset_ms - WORD_END_SLACK_MS or w.start_ms >= offset_ms for w in self._words
            )

        deadline = time.monotonic() + self.transcript_wait_s
        while time.monotonic() < deadline and not caught_up():
            pending = any(w.start_ms < offset_ms for w in self._words)
            if pending and time.monotonic() - self._last_word_at >= self.transcript_quiet_s:
                break
            await self._next_word(deadline)

        before = sorted((w for w in self._words if w.start_ms < offset_ms), key=lambda w: w.start_ms)
        if before and not SENTENCE_END.search(before[-1].text):
            tail_deadline = time.monotonic() + TAIL_WAIT_S
            while time.monotonic() < tail_deadline and not self._tail_settled(before[-1], offset_ms):
                await self._next_word(tail_deadline)

        words = before
        later = sorted((w for w in self._words if w.start_ms >= offset_ms), key=lambda w: w.start_ms)
        for word in later:
            if word.start_ms >= offset_ms + TAIL_MS or word.start_ms - words[-1].end_ms > CONTIGUOUS_MS:
                break
            words = [*words, word]
        taken = set(map(id, words))
        self._words = [w for w in self._words if id(w) not in taken]
        self._taken_until_ms = max(self._taken_until_ms, offset_ms, words[-1].end_ms if words else -1)
        return re.sub(r"^[\s,;:.]+", "", "".join(w.text for w in words)).strip()

    async def _next_word(self, deadline: float) -> None:
        """Wait for the next transcript word, at most until `deadline` (and 0.1 s at a time)."""
        self._word_arrived.clear()
        timeout = min(0.1, max(0.0, deadline - time.monotonic()))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._word_arrived.wait(), timeout)

    def _tail_settled(self, last: Word, offset_ms: int) -> bool:
        """True once no more words can run on from `last`: a gap or a sentence end has arrived."""
        end = last.end_ms
        for word in sorted((w for w in self._words if w.start_ms >= offset_ms), key=lambda w: w.start_ms):
            if word.start_ms - end > CONTIGUOUS_MS or word.start_ms >= offset_ms + TAIL_MS:
                return True
            if SENTENCE_END.search(word.text):
                return True
            end = word.end_ms
        return False

    async def _run(
        self, delegation_id: str, utterance: str, telemetry: TurnTelemetry
    ) -> tuple[str, TraceEvent]:
        """One agent turn; streams its events (its trace just before `done`) and returns
        the text to speak and the trace."""
        started = time.time()
        text: list[str] = []
        trace: TraceEvent | None = None
        try:
            async for event in self.run_turn(utterance):
                if event.type == "done":
                    trace = telemetry.trace(event.turn_id)
                    self._out.put_nowait(trace)
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
            done = DoneEvent(
                turn_id=f"failed-{delegation_id}",
                usage=telemetry.meter.usage(),
                cost_usd=round(telemetry.meter.cost_usd, 6),
                latency_ms=round((time.time() - started) * 1000, 1),
            )
            trace = telemetry.trace(done.turn_id)
            self._out.put_nowait(trace)
            self._out.put_nowait(done)
            return FAILED, trace
        if trace is None:  # the graph always ends with done; this is just a safety net
            trace = telemetry.trace(f"voice-{delegation_id}")
        return speakable("".join(text)) or FAILED, trace

    def _ended(self, seconds: float) -> VoiceEndedEvent:
        usage = self._usage
        billed: float | None = getattr(usage, "seconds", None)
        cost: float | None = None
        if billed is not None:
            standby = float(getattr(usage, "paused_seconds", None) or 0)
            active = getattr(usage, "active_seconds", None)
            cost = round(
                live_cost(LIVE_MODEL, float(active) if active is not None else billed - standby, standby), 6
            )
        return VoiceEndedEvent(
            reason=self._reason or "hangup",
            duration_ms=round(seconds * 1000, 1),
            billed_seconds=billed,
            voice_cost_usd=cost,
        )

    async def _speak(self, delegation_id: str, content: str) -> None:
        await self._send(
            {"type": "session.commentary.append", "delegation_id": delegation_id, "content": content}
        )

    async def _send(self, event: dict[str, Any]) -> None:
        try:
            await self.link.send(event)
        except Exception:  # the session may already be closing; nothing to recover
            log.warning("could not send %s to GPT-Live", event.get("type"), exc_info=True)
