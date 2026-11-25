import logging
import time
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

import anyio
from fastapi import APIRouter
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langchain_core.runnables import RunnableConfig

from app.agent import runtime
from app.agent.errors import error_event
from app.agent.graph import run_turn
from app.config import get_settings
from app.contracts import AgentEvent, TraceEvent, VoiceEndedEvent, VoiceEvent, VoiceRequest, VoiceSessionEvent
from app.data.repo import customer_for_persona
from app.db import get_pool
from app.llm.client import get_openai
from app.observability.turn import persist_trace
from app.voice.bridge import VoiceBridge
from app.voice.session import MAX_CALL_SECONDS, history_from_items, session_config

router = APIRouter(tags=["voice"])
log = logging.getLogger(__name__)


async def _caller_first_name(values: dict[str, Any], persona: str | None) -> str | None:
    customer = values.get("customer")
    if customer:
        return str(customer["name"]).split()[0]
    if persona:
        pool = await get_pool()
        async with pool.connection() as conn:
            row = await customer_for_persona(conn, persona)
        return row.full_name.split()[0] if row else None
    return None


@router.post("/voice", response_class=EventSourceResponse)
async def voice(request: VoiceRequest) -> AsyncIterable[ServerSentEvent]:
    """Run one voice call as Server-Sent Events.

    1. Create a GPT-Live WebRTC session for the browser's SDP offer, seeded with the
       conversation so far, and send back the SDP answer (`voice_session`).
    2. Attach a server sideband to the same session and stay on it for the whole call:
       each spoken request runs through the agent graph, whose events are streamed here.
    3. End with `voice_ended`. If the browser goes away first, the call is hung up.

    Audio never touches this server: it flows between the browser and GPT-Live.
    """
    settings = get_settings()
    persona = request.persona_id if settings.demo_mode else None
    session_id = str(request.session_id)
    client = get_openai()
    started = time.monotonic()
    live_id: str | None = None
    ended = False
    sequence = 0

    def sse(event: VoiceEvent) -> ServerSentEvent:
        nonlocal sequence
        sequence += 1
        return ServerSentEvent(event=event.type, id=str(sequence), data=event.model_dump(mode="json"))

    try:
        graph = await runtime.get_graph()
        config: RunnableConfig = {"configurable": {"thread_id": session_id}}
        values: dict[str, Any] = dict((await graph.aget_state(config)).values or {})
        created = await client.live.create(
            session=session_config(
                caller_first_name=await _caller_first_name(values, persona),
                history=history_from_items(values.get("items", [])),
            ),
            transport={"type": "webrtc", "sdp": request.sdp},
        )
        live_id = created.session.id
        log.info("voice call %s started for session %s", live_id, session_id)
        yield sse(
            VoiceSessionEvent(
                live_session_id=live_id, sdp=created.transport.sdp, max_seconds=MAX_CALL_SECONDS
            )
        )

        def turn(utterance: str) -> AsyncIterator[AgentEvent]:
            return run_turn(
                graph, session_id=session_id, message=utterance, persona_id=persona, channel="voice"
            )

        async with client.live.sideband.connect(session_id=live_id) as link:

            async def save(trace: TraceEvent) -> None:
                await persist_trace(session_id, trace)

            async for event in VoiceBridge(link, turn, on_trace=save).events():
                yield sse(event)
        ended = True
        log.info("voice call %s ended", live_id)
    except Exception as error:
        log.exception("voice call failed")
        yield sse(error_event(error))
        yield sse(VoiceEndedEvent(reason="error", duration_ms=round((time.monotonic() - started) * 1000, 1)))
    finally:
        if live_id and not ended:
            # The browser left (or we failed) mid-call: don't leave the session running.
            with anyio.move_on_after(5, shield=True):
                try:
                    await client.live.sessions.hangup(live_id)
                    log.info("voice call %s hung up: the browser left", live_id)
                except Exception:
                    log.warning("hangup failed for %s", live_id, exc_info=True)
