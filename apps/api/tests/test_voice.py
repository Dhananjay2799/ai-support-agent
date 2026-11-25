import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.contracts import (
    AgentEvent,
    DoneEvent,
    StepEvent,
    TokenEvent,
    TraceEvent,
    Usage,
    VoiceEvent,
    voice_event_adapter,
)
from app.main import app
from app.voice.bridge import DIDNT_CATCH, FAILED, VoiceBridge, speakable
from app.voice.session import BROWSER_CHANNEL, HISTORY_MESSAGES, history_from_items, session_config

# --- a scripted GPT-Live connection ------------------------------------------------------


class FakeLink:
    """Plays server events the test pushes; records what the bridge sends."""

    def __init__(self) -> None:
        self.incoming: asyncio.Queue[Any] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []

    async def send(self, event: Any) -> None:
        self.sent.append(event)

    async def __aiter__(self) -> AsyncIterator[Any]:
        while True:
            yield await self.incoming.get()

    def heard(self, text: str, start_ms: int, end_ms: int) -> None:
        self.incoming.put_nowait(
            SimpleNamespace(
                type="session.input_transcript.delta", delta=text, start_ms=start_ms, end_ms=end_ms
            )
        )

    def delegate(self, delegation_id: str, offset_ms: int) -> None:
        self.incoming.put_nowait(
            SimpleNamespace(
                type="session.delegation.created",
                delegation=SimpleNamespace(id=delegation_id),
                offset_ms=offset_ms,
            )
        )

    def close(self, usage: Any = None) -> None:
        self.incoming.put_nowait(SimpleNamespace(type="session.closed", usage=usage))

    def spoken(self) -> list[tuple[str, str]]:
        return [
            (e["delegation_id"], e["content"]) for e in self.sent if e["type"] == "session.commentary.append"
        ]

    def silent(self) -> list[tuple[str, str]]:
        return [
            (e["delegation_id"], e["content"]) for e in self.sent if e["type"] == "session.thinking.append"
        ]


def answer_with(text: str, gate: asyncio.Event | None = None) -> Callable[[str], AsyncIterator[AgentEvent]]:
    async def run(utterance: str) -> AsyncIterator[AgentEvent]:
        yield StepEvent(id="s1", tool="get_account", label="Checking the account…", status="started")
        if gate:
            await gate.wait()
        yield StepEvent(
            id="s1", tool="get_account", label="Checking the account…", status="completed", detail="3 charges"
        )
        yield TokenEvent(text=text.format(utterance=utterance))
        yield DoneEvent(turn_id=uuid.uuid4().hex, usage=Usage(), cost_usd=0, latency_ms=1)

    return run


async def until(condition: Callable[[], bool]) -> None:
    """Poll the fake connection until the bridge has done something (at most 2 s)."""
    async with asyncio.timeout(2):
        while not condition():  # noqa: ASYNC110 - a test helper polling shared state
            await asyncio.sleep(0.01)


def bridge_for(link: FakeLink, runner: Callable[[str], AsyncIterator[AgentEvent]], **kw: Any) -> VoiceBridge:
    options: dict[str, Any] = {"transcript_wait_s": 0.5, "transcript_quiet_s": 0.2, **kw}
    return VoiceBridge(link, runner, **options)


async def run_call(bridge: VoiceBridge) -> asyncio.Task[list[VoiceEvent]]:
    async def consume() -> list[VoiceEvent]:
        return [event async for event in bridge.events()]

    return asyncio.create_task(consume())


# --- bridge ------------------------------------------------------------------------------


async def test_a_spoken_request_runs_the_agent_and_its_answer_is_spoken() -> None:
    link = FakeLink()
    call = await run_call(bridge_for(link, answer_with("You heard: {utterance} [refunds-and-billing#5]")))

    link.heard(", I was charged", 1000, 1600)
    link.heard(" twice.", 1600, 2000)
    link.delegate("d1", 2000)  # delegated where the caller's last word ended
    await until(lambda: bool(link.spoken()))
    link.close()
    events = await call

    assert [e.type for e in events] == ["voice_turn", "step", "step", "token", "trace", "done", "voice_ended"]
    assert events[0].type == "voice_turn" and events[0].utterance == "I was charged twice."
    # Spoken without the citation marker; the browser still gets the marked-up text.
    assert link.spoken() == [("d1", "You heard: I was charged twice.")]
    assert link.silent() == [("d1", "Progress: Checking the account (3 charges)")]
    assert events[-1].type == "voice_ended" and events[-1].reason == "hangup"


async def test_waits_for_words_transcribed_after_the_delegation() -> None:
    link = FakeLink()
    call = await run_call(bridge_for(link, answer_with("{utterance}"), transcript_quiet_s=5))

    link.heard("Can I get", 1000, 1400)
    link.delegate("d1", 2000)
    await asyncio.sleep(0.1)
    link.heard(" a refund?", 1400, 2000)  # on WebRTC the transcript can trail the delegation
    await until(lambda: bool(link.spoken()))
    link.close()
    await call

    assert link.spoken() == [("d1", "Can I get a refund?")]


async def test_requests_are_split_where_each_was_delegated() -> None:
    link = FakeLink()
    turns: list[str] = []
    gate = asyncio.Event()

    async def runner_with_gate(utterance: str) -> AsyncIterator[AgentEvent]:
        turns.append(utterance)
        async for event in answer_with("ok", gate if len(turns) == 1 else None)(utterance):
            yield event

    call = await run_call(bridge_for(link, runner_with_gate))
    # Both requests' words are in before the first one is handled.
    link.heard("What's my plan?", 1000, 2000)
    link.delegate("d1", 2000)
    link.heard(" And my charges?", 3000, 3600)
    link.delegate("d2", 3600)
    await until(lambda: len(turns) == 1)
    link.heard(" uh", 1800, 1900)  # a straggler for the first request, after it was taken: dropped
    await asyncio.sleep(0.05)
    gate.set()
    await until(lambda: len(turns) == 2 and len(link.spoken()) == 1)
    link.close()
    await call

    assert turns == ["What's my plan?", "And my charges?"]


async def test_words_running_on_past_the_delegation_stay_with_the_request() -> None:
    link = FakeLink()
    turns: list[str] = []

    async def runner(utterance: str) -> AsyncIterator[AgentEvent]:
        turns.append(utterance)
        async for event in answer_with("ok")(utterance):
            yield event

    call = await run_call(bridge_for(link, runner))
    link.heard("Can you fix that", 1000, 2000)
    link.delegate("d1", 2000)  # GPT-Live delegated a beat early...
    await asyncio.sleep(0.1)
    link.heard(", please?", 2100, 2500)  # ...and the caller wasn't quite done
    await until(lambda: len(turns) == 1)
    link.heard(" What's my plan?", 6000, 7000)
    link.delegate("d2", 7000)
    await until(lambda: len(turns) == 2)
    link.close()
    await call

    assert turns == ["Can you fix that, please?", "What's my plan?"]


async def test_an_answer_the_caller_moved_on_from_is_context_not_speech() -> None:
    link = FakeLink()
    gate = asyncio.Event()
    turns: list[str] = []

    async def runner(utterance: str) -> AsyncIterator[AgentEvent]:
        turns.append(utterance)
        first = len(turns) == 1
        async for event in answer_with("Answer to: {utterance}", gate if first else None)(utterance):
            yield event

    call = await run_call(bridge_for(link, runner))
    link.heard("What's my plan?", 1000, 2000)
    link.delegate("d1", 2000)
    await until(lambda: len(turns) == 1)
    link.heard(" Actually, refund my duplicate charge.", 4000, 6000)  # cuts in with a new request
    link.delegate("d2", 6000)
    await asyncio.sleep(0.05)
    gate.set()
    await until(lambda: bool(link.spoken()))
    link.close()
    await call

    assert turns == ["What's my plan?", "Actually, refund my duplicate charge."]
    assert link.spoken() == [("d2", "Answer to: Actually, refund my duplicate charge.")]
    assert ("d1", "Result of an earlier request the caller moved on from: Answer to: What's my plan?") in (
        link.silent()
    )


async def test_nothing_heard_asks_the_caller_to_repeat() -> None:
    link = FakeLink()
    call = await run_call(bridge_for(link, answer_with("unused"), transcript_wait_s=0.1))
    link.delegate("d1", 2000)
    await until(lambda: bool(link.spoken()))
    link.close()
    events = await call

    assert link.spoken() == [("d1", DIDNT_CATCH)]
    assert [e.type for e in events] == ["voice_ended"]


async def test_a_failed_turn_is_reported_and_the_caller_hears_an_apology() -> None:
    link = FakeLink()

    async def broken(utterance: str) -> AsyncIterator[AgentEvent]:
        raise RuntimeError("database down")
        yield  # pragma: no cover

    call = await run_call(bridge_for(link, broken))
    link.heard("Refund me.", 1000, 2000)
    link.delegate("d1", 2000)
    await until(lambda: bool(link.spoken()))
    link.close()
    events = await call

    assert [e.type for e in events] == ["voice_turn", "error", "trace", "done", "voice_ended"]
    assert link.spoken() == [("d1", FAILED)]


async def test_hanging_up_mid_turn_still_finishes_the_turn() -> None:
    link = FakeLink()
    gate = asyncio.Event()
    call = await run_call(bridge_for(link, answer_with("Refunded $29.", gate)))
    link.heard("Refund my duplicate charge.", 1000, 2000)
    link.delegate("d1", 2000)
    await asyncio.sleep(0.1)  # the turn is now running, held at the gate
    link.heard("Hello?", 5000, 5500)
    link.delegate("d2", 5500)  # queued behind the running turn
    link.close()  # the caller hangs up while the refund is still being worked on
    await asyncio.sleep(0.05)
    gate.set()
    events = await call

    assert [e.type for e in events] == ["voice_turn", "step", "step", "token", "trace", "done", "voice_ended"]
    assert link.spoken() == []  # nobody left to hear it; the answer is in the conversation


async def test_the_call_is_closed_at_the_time_limit() -> None:
    link = FakeLink()
    call = await run_call(bridge_for(link, answer_with("unused"), max_seconds=0.05))
    await until(lambda: {"type": "session.close"} in link.sent)
    link.close()
    events = await call

    assert events[-1].type == "voice_ended" and events[-1].reason == "time_limit"


async def test_each_voice_turn_is_traced_and_the_call_reports_its_voice_cost() -> None:
    link = FakeLink()
    traces: list[TraceEvent] = []

    async def save(trace: TraceEvent) -> None:
        traces.append(trace)

    call = await run_call(bridge_for(link, answer_with("ok"), on_trace=save))
    link.heard("What's my plan?", 1000, 2000)
    link.delegate("d1", 2000)
    await until(lambda: bool(link.spoken()))
    link.close(usage=SimpleNamespace(seconds=60.0, active_seconds=50, paused_seconds=10))
    events = await call

    [trace] = traces
    root, wait = trace.spans[0], trace.spans[1]
    assert root.attributes["channel"] == "voice" and root.attributes["message"] == "What's my plan?"
    assert wait.name == "waiting for the transcript" and wait.parent_id == root.id
    ended = events[-1]
    assert ended.type == "voice_ended" and ended.billed_seconds == 60.0
    assert ended.voice_cost_usd == pytest.approx(50 / 60 * 0.05 + 10 / 60 * 0.02)


def test_speakable_drops_citations_and_caps_length() -> None:
    assert speakable("Refunds take 5 to 10 days. [refunds-and-billing#5]") == "Refunds take 5 to 10 days."
    assert len(speakable("word " * 1000)) <= 1601


# --- session setup -----------------------------------------------------------------------


def test_history_keeps_only_the_conversation() -> None:
    items: list[dict[str, Any]] = [
        {"role": "user", "content": "I was charged twice"},
        {"type": "reasoning", "encrypted_content": "…"},
        {"type": "function_call", "name": "get_account", "arguments": "{}", "call_id": "c1"},
        {"type": "function_call_output", "call_id": "c1", "output": "{}"},
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Refunded $29."}],
        },
        {"role": "assistant", "content": "Ticket ESC-1001 is open."},
    ]
    history = history_from_items(items)
    assert [(h["role"], h["content"][0]["text"]) for h in history] == [  # type: ignore[index]
        ("user", "I was charged twice"),
        ("assistant", "Refunded $29."),
        ("assistant", "Ticket ESC-1001 is open."),
    ]
    many = [{"role": "user", "content": f"message {i}"} for i in range(50)]
    assert len(history_from_items(many)) == HISTORY_MESSAGES


def test_the_browser_cannot_speak_for_the_backend() -> None:
    allowed = BROWSER_CHANNEL["data_channel"]["allowed_client_events"]
    assert "session.commentary.append" not in allowed and "session.instructions.append" not in allowed
    config = session_config(caller_first_name="Priya", history=[])
    assert config["delegation"] == {"type": "client"}
    assert "by name (Priya)" in str(config["instructions"])
    assert "input" not in config


# --- route -------------------------------------------------------------------------------


def _parse(body: str) -> list[VoiceEvent]:
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        events.append(voice_event_adapter.validate_python(json.loads(fields["data"])))
    return events


class FakeOpenAI:
    def __init__(self, link: FakeLink, create_error: Exception | None = None) -> None:
        self.created: list[dict[str, Any]] = []
        self.hung_up: list[str] = []
        outer = self

        class Sessions:
            async def hangup(self, session_id: str) -> None:
                outer.hung_up.append(session_id)

        class Sideband:
            def connect(self, *, session_id: str) -> Any:
                class Manager:
                    async def __aenter__(self) -> FakeLink:
                        return link

                    async def __aexit__(self, *exc: object) -> None:
                        return None

                return Manager()

        class Live:
            sessions = Sessions()
            sideband = Sideband()

            async def create(self, **params: Any) -> Any:
                if create_error:
                    raise create_error
                outer.created.append(params)
                return SimpleNamespace(
                    session=SimpleNamespace(id="live_123"), transport=SimpleNamespace(sdp="v=0 answer")
                )

        self.live = Live()


@pytest.fixture
def fake_voice(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeOpenAI]:
    def install(link: FakeLink, create_error: Exception | None = None) -> FakeOpenAI:
        client = FakeOpenAI(link, create_error)
        state = SimpleNamespace(
            values={
                "customer": {"name": "Priya Shah"},
                "items": [{"role": "user", "content": "Hi from chat"}],
            }
        )

        async def get_graph() -> Any:
            async def aget_state(config: Any) -> Any:
                return state

            return SimpleNamespace(aget_state=aget_state)

        async def run_turn(graph: object, **kwargs: Any) -> AsyncIterator[AgentEvent]:
            assert kwargs["channel"] == "voice"
            yield TokenEvent(text="Done.")
            yield DoneEvent(turn_id="t1", usage=Usage(), cost_usd=0, latency_ms=1)

        monkeypatch.setattr("app.routes.voice.get_openai", lambda: client)
        monkeypatch.setattr("app.routes.voice.runtime.get_graph", get_graph)
        monkeypatch.setattr("app.routes.voice.run_turn", run_turn)
        return client

    return install


def test_voice_call_streams_the_answer_then_the_call(fake_voice: Callable[..., FakeOpenAI]) -> None:
    link = FakeLink()
    link.close()  # the call ends straight away
    client = fake_voice(link)

    response = TestClient(app).post(
        "/voice", json={"session_id": str(uuid.uuid4()), "persona_id": "priya", "sdp": "v=0 offer"}
    )
    events = _parse(response.text)

    assert [e.type for e in events] == ["voice_session", "voice_ended"]
    assert events[0].type == "voice_session" and events[0].sdp == "v=0 answer"
    session = client.created[0]["session"]
    assert client.created[0]["transport"] == {"type": "webrtc", "sdp": "v=0 offer"}
    assert session["input"][0]["content"][0]["text"] == "Hi from chat"  # chat memory carried into voice
    assert "by name (Priya)" in session["instructions"]
    assert client.hung_up == []  # GPT-Live closed the session itself


def test_a_failed_session_setup_ends_the_call_with_an_error(fake_voice: Callable[..., FakeOpenAI]) -> None:
    client = fake_voice(FakeLink(), create_error=RuntimeError("bad sdp"))
    response = TestClient(app).post("/voice", json={"session_id": str(uuid.uuid4()), "sdp": "v=0 offer"})

    events = _parse(response.text)
    assert [e.type for e in events] == ["error", "voice_ended"]
    assert events[1].type == "voice_ended" and events[1].reason == "error"
    assert client.hung_up == []
