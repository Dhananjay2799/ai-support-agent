import json
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.agent import scripted
from app.contracts import DoneEvent, StepEvent, agent_event_adapter
from app.main import app


@pytest.fixture(autouse=True)
def fast_script(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    original = scripted.scripted_turn

    def no_delay(message: str, *, step_delay_s: float = 0.0):  # type: ignore[no-untyped-def]
        return original(message, step_delay_s=0.0)

    monkeypatch.setattr("app.routes.chat.scripted_turn", no_delay)
    yield


def _parse_sse(body: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def test_chat_streams_a_multi_step_turn() -> None:
    client = TestClient(app)
    response = client.post("/chat", json={"session_id": str(uuid.uuid4()), "message": "I was charged twice"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(response.text)
    parsed = [agent_event_adapter.validate_python(data) for _, data in events]
    assert all(name == event.type for (name, _), event in zip(events, parsed, strict=True))

    steps = [e for e in parsed if isinstance(e, StepEvent)]
    assert [s.status for s in steps] == ["started", "completed"] * 3
    assert len({s.tool for s in steps}) == 3
    assert isinstance(parsed[-1], DoneEvent)


def test_chat_rejects_empty_message() -> None:
    client = TestClient(app)
    response = client.post("/chat", json={"session_id": str(uuid.uuid4()), "message": ""})
    assert response.status_code == 422


def test_health() -> None:
    assert TestClient(app).get("/health").json() == {"status": "ok"}
