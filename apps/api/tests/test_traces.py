import uuid

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.observability.tracing import span
from app.observability.turn import TurnTelemetry, load_traces, save_trace


async def test_a_turns_spans_are_saved_and_read_back_in_order(db: AsyncConnection[DictRow]) -> None:
    session = str(uuid.uuid4())
    for turn_id in ("t1", "t2"):
        with TurnTelemetry(channel="text", message=f"message {turn_id}") as telemetry:
            with span("tool: get_account", "tool", tool="get_account") as tool:
                with span("hybrid search", "retrieval", query="refund window"):
                    pass
                tool.set(status="completed")
            trace = telemetry.trace(turn_id)
        await save_trace(db, session, trace)

    turns = await load_traces(db, session)

    assert [t["turn_id"] for t in turns] == ["t1", "t2"]
    root, tool_span, search = turns[0]["spans"]
    assert (root["name"], tool_span["name"], search["name"]) == ("turn", "tool: get_account", "hybrid search")
    assert search["parent_id"] == tool_span["id"] and tool_span["parent_id"] == root["id"]
    assert root["attributes"]["message"] == "message t1"
    assert tool_span["attributes"] == {"tool": "get_account", "status": "completed"}
    assert await load_traces(db, str(uuid.uuid4())) == []
