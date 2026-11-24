import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import openai
import pytest
from fastapi.testclient import TestClient

from app.contracts import AgentEvent, DoneEvent, StepEvent, TokenEvent, Usage, agent_event_adapter
from app.main import app


def _parse_sse(body: str) -> list[AgentEvent]:
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        event = agent_event_adapter.validate_python(json.loads(fields["data"]))
        assert fields["event"] == event.type
        events.append(event)
    return events


@pytest.fixture
def fake_agent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def get_graph() -> object:
        return object()

    async def run_turn(graph: object, **kwargs: Any) -> AsyncIterator[AgentEvent]:
        calls.append(kwargs)
        if kwargs["message"] == "boom":
            raise openai.RateLimitError("slow down", response=_fake_response(429), body=None)
        yield StepEvent(id="s1", tool="verify_customer", label="Verifying…", status="started")
        yield TokenEvent(text="Hello")
        yield DoneEvent(turn_id="t1", usage=Usage(), cost_usd=0, latency_ms=1)

    monkeypatch.setattr("app.routes.chat.runtime.get_graph", get_graph)
    monkeypatch.setattr("app.routes.chat.run_turn", run_turn)
    return calls


def _fake_response(status: int) -> Any:
    import httpx

    return httpx.Response(status, request=httpx.Request("POST", "https://api.openai.com/v1/responses"))


def test_chat_streams_agent_events(fake_agent: list[dict[str, Any]]) -> None:
    session = str(uuid.uuid4())
    response = TestClient(app).post(
        "/chat", json={"session_id": session, "message": "hi", "persona_id": "priya"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert [e.type for e in _parse_sse(response.text)] == ["step", "token", "done"]
    assert fake_agent == [{"session_id": session, "message": "hi", "persona_id": "priya"}]


def test_upstream_failure_becomes_a_retryable_error_and_still_ends_with_done(
    fake_agent: list[dict[str, Any]],
) -> None:
    response = TestClient(app).post("/chat", json={"session_id": str(uuid.uuid4()), "message": "boom"})
    events = _parse_sse(response.text)
    assert [e.type for e in events] == ["error", "done"]
    error = events[0]
    assert error.type == "error" and error.code == "rate_limited" and error.retryable


def test_chat_rejects_empty_message() -> None:
    response = TestClient(app).post("/chat", json={"session_id": str(uuid.uuid4()), "message": ""})
    assert response.status_code == 422


def test_health() -> None:
    assert TestClient(app).get("/health").json() == {"status": "ok"}
