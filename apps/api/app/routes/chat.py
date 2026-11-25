import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.agent import runtime
from app.agent.errors import error_event
from app.agent.graph import run_turn
from app.config import get_settings
from app.contracts import AgentEvent, ChatRequest, DoneEvent, TraceEvent
from app.db import get_pool
from app.limits import limit_chat
from app.observability.faults import FaultPlan, current_faults
from app.observability.turn import TurnTelemetry, load_traces, persist_trace

router = APIRouter(tags=["chat"])
log = logging.getLogger(__name__)


@router.post("/chat", response_class=EventSourceResponse, dependencies=[Depends(limit_chat)])
async def chat(request: ChatRequest) -> AsyncIterable[ServerSentEvent]:
    """Stream one agent turn as Server-Sent Events.

    Each SSE message carries `event: <AgentEvent.type>` and the event as JSON in
    `data`. The turn's trace comes just before the final `done`, which is always
    sent, even after an error.
    """
    settings = get_settings()
    session_id = str(request.session_id)
    persona = request.persona_id if settings.demo_mode else None
    started = time.time()
    sequence = 0
    trace: TraceEvent | None = None

    def sse(event: AgentEvent) -> ServerSentEvent:
        nonlocal sequence
        sequence += 1
        return ServerSentEvent(event=event.type, id=str(sequence), data=event.model_dump(mode="json"))

    fault = request.simulate if settings.demo_mode else None
    fault_token = current_faults.set(FaultPlan(fault) if fault else None)
    with TurnTelemetry(
        channel="text", message=request.message, persona=persona, simulated=fault
    ) as telemetry:
        try:
            graph = await runtime.get_graph()
            async for event in run_turn(
                graph, session_id=session_id, message=request.message, persona_id=persona
            ):
                if event.type == "done":
                    trace = telemetry.trace(event.turn_id)
                    yield sse(trace)
                yield sse(event)
        except Exception as error:
            log.exception("chat turn failed")
            yield sse(error_event(error))
            done = DoneEvent(
                turn_id=f"failed-{uuid.uuid4().hex[:8]}",
                usage=telemetry.meter.usage(),
                cost_usd=round(telemetry.meter.cost_usd, 6),
                latency_ms=round((time.time() - started) * 1000, 1),
            )
            trace = telemetry.trace(done.turn_id)
            yield sse(trace)
            yield sse(done)
        finally:
            with contextlib.suppress(ValueError):
                current_faults.reset(fault_token)
    if trace is not None:
        await persist_trace(session_id, trace)


@router.get("/sessions/{session_id}/traces")
async def session_traces(session_id: UUID) -> list[dict[str, Any]]:
    """A conversation's turn traces, oldest first. The session id is the access key:
    it's a random UUID only the browser that started the conversation knows."""
    pool = await get_pool()
    async with pool.connection() as conn:
        return await load_traces(conn, str(session_id))
