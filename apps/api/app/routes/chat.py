import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterable

import openai
import psycopg
from fastapi import APIRouter
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.agent import runtime
from app.agent.graph import run_turn
from app.config import get_settings
from app.contracts import AgentEvent, ChatRequest, DoneEvent, ErrorEvent
from app.observability.usage import UsageMeter, current_meter

router = APIRouter(tags=["chat"])
log = logging.getLogger(__name__)


def _error_event(error: Exception) -> ErrorEvent:
    if isinstance(error, openai.APITimeoutError | openai.APIConnectionError):
        return ErrorEvent(
            code="llm_timeout", message="The AI service took too long to respond.", retryable=True
        )
    if isinstance(error, openai.RateLimitError):
        return ErrorEvent(code="rate_limited", message="The AI service is busy right now.", retryable=True)
    if isinstance(error, psycopg.OperationalError):
        return ErrorEvent(
            code="internal", message="Our account system is unreachable right now.", retryable=True
        )
    return ErrorEvent(code="internal", message="Something went wrong on our side.", retryable=True)


@router.post("/chat", response_class=EventSourceResponse)
async def chat(request: ChatRequest) -> AsyncIterable[ServerSentEvent]:
    """Stream one agent turn as Server-Sent Events.

    Each SSE message carries `event: <AgentEvent.type>` and the event as JSON in
    `data`. The stream always ends with a `done` event, even after an error.
    """
    meter = UsageMeter()
    token = current_meter.set(meter)
    started = time.time()
    sequence = 0

    def sse(event: AgentEvent) -> ServerSentEvent:
        nonlocal sequence
        sequence += 1
        return ServerSentEvent(event=event.type, id=str(sequence), data=event.model_dump(mode="json"))

    try:
        graph = await runtime.get_graph()
        persona = request.persona_id if get_settings().demo_mode else None
        async for event in run_turn(
            graph, session_id=str(request.session_id), message=request.message, persona_id=persona
        ):
            yield sse(event)
    except Exception as error:
        log.exception("chat turn failed")
        yield sse(_error_event(error))
        yield sse(
            DoneEvent(
                turn_id=f"failed-{uuid.uuid4().hex[:8]}",
                usage=meter.usage(),
                cost_usd=round(meter.cost_usd, 6),
                latency_ms=round((time.time() - started) * 1000, 1),
            )
        )
    finally:
        # The generator may be closed from another context; then there's nothing to restore.
        with contextlib.suppress(ValueError):
            current_meter.reset(token)
