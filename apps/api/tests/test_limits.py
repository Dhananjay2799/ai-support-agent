import json
import uuid
from collections import Counter
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from starlette.requests import Request

from app import limits
from app.config import Settings
from app.limits import client_ip, hashed, limited


def _request(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": ("10.0.0.9", 1234)})


def test_client_ip_prefers_what_vercel_reports() -> None:
    assert client_ip(_request({"x-real-ip": "1.2.3.4", "x-forwarded-for": "5.6.7.8"})) == "1.2.3.4"
    assert client_ip(_request({"x-forwarded-for": "5.6.7.8, 10.0.0.1"})) == "5.6.7.8"
    assert client_ip(_request({})) == "10.0.0.9"


def test_ips_are_stored_hashed() -> None:
    assert hashed("1.2.3.4", "salt") == hashed("1.2.3.4", "salt")
    assert "1.2.3.4" not in hashed("1.2.3.4", "salt") and hashed("1.2.3.4", "other") != hashed(
        "1.2.3.4", "salt"
    )


@pytest.fixture
def guarded(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A tiny app behind the chat limiter, with in-memory counters and a settable spend."""
    state: dict[str, Any] = {"hits": Counter(), "spent": 0.0, "broken": False}
    settings = Settings(
        rate_limits_enabled=True, chat_per_session_per_minute=2, chat_per_ip_per_hour=3, daily_budget_usd=1.0
    )

    async def hit(bucket: str, window_s: int) -> int:
        if state["broken"]:
            raise OSError("database unreachable")
        state["hits"][bucket] += 1
        return int(state["hits"][bucket])

    async def spent_today() -> float:
        return float(state["spent"])

    monkeypatch.setattr(limits, "get_settings", lambda: settings)
    monkeypatch.setattr(limits, "hit", hit)
    monkeypatch.setattr(limits, "spent_today", spent_today)
    app = FastAPI()

    @app.post("/chat", dependencies=[Depends(limited("chat"))])
    async def chat() -> dict[str, str]:
        return {"ok": "yes"}

    state["client"] = TestClient(app)
    return state


def _send(state: dict[str, Any], session: str) -> Any:
    return state["client"].post("/chat", content=json.dumps({"session_id": session, "message": "hi"}))


def test_a_busy_conversation_is_slowed_down_with_retry_after(guarded: dict[str, Any]) -> None:
    session = str(uuid.uuid4())
    assert [_send(guarded, session).status_code for _ in range(2)] == [200, 200]
    refused = _send(guarded, session)
    assert refused.status_code == 429 and 1 <= int(refused.headers["retry-after"]) <= 60
    assert "this conversation" in refused.json()["detail"]


def test_one_network_is_capped_across_conversations(guarded: dict[str, Any]) -> None:
    codes = [_send(guarded, str(uuid.uuid4())).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_the_daily_budget_pauses_the_demo(guarded: dict[str, Any]) -> None:
    guarded["spent"] = 1.0
    refused = _send(guarded, str(uuid.uuid4()))
    assert refused.status_code == 503 and "budget" in refused.json()["detail"]
    assert int(refused.headers["retry-after"]) <= 86_401


def test_a_broken_limiter_lets_requests_through(guarded: dict[str, Any]) -> None:
    guarded["broken"] = True
    assert _send(guarded, str(uuid.uuid4())).status_code == 200


async def test_counters_are_per_bucket_and_window(db: AsyncConnection[DictRow]) -> None:
    bucket = f"test:{uuid.uuid4()}"
    counts = []
    for name in (bucket, bucket, f"{bucket}:other"):
        cur = await db.execute("select public.hit_rate_limit(%s, 3600) as hits", (name,))
        row = await cur.fetchone()
        assert row is not None
        counts.append(row["hits"])
    assert counts == [1, 2, 1]


async def test_spend_adds_up_todays_root_spans(
    db: AsyncConnection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    await db.execute("delete from public.traces")
    rows = [  # (name, parent span, cost, days ago)
        ("turn", None, 0.25, 0),
        ("voice call", None, 0.5, 0),
        ("model call", "parent", 9.0, 0),  # child spans are already in their turn's total
        ("turn", None, 7.0, 1),
    ]
    for name, parent, cost, days_ago in rows:
        await db.execute(
            """
            insert into public.traces
              (session_id, turn_id, span_id, parent_span_id, name, kind, start_ms, attributes, created_at)
            values (gen_random_uuid(), 't', 's', %s, %s, 'node', 0, %s, now() - make_interval(days => %s))
            """,
            (parent, name, json.dumps({"cost_usd": cost}), days_ago),
        )

    class Pool:
        def connection(self) -> Any:
            class Manager:
                async def __aenter__(self) -> AsyncConnection[DictRow]:
                    return db

                async def __aexit__(self, *exc: object) -> None:
                    return None

            return Manager()

    async def get_pool() -> Pool:
        return Pool()

    monkeypatch.setattr(limits, "get_pool", get_pool)
    assert await limits.spent_today() == pytest.approx(0.75)
