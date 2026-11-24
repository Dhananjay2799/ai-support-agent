from collections.abc import AsyncIterable

from fastapi import APIRouter
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.agent.scripted import scripted_turn
from app.contracts import ChatRequest

router = APIRouter(tags=["chat"])


@router.post("/chat", response_class=EventSourceResponse)
async def chat(request: ChatRequest) -> AsyncIterable[ServerSentEvent]:
    """Stream one agent turn as Server-Sent Events.

    Each SSE message carries `event: <AgentEvent.type>` and the event as JSON in
    `data`. The stream always ends with a `done` event.
    """
    sequence = 0
    async for event in scripted_turn(request.message):
        sequence += 1
        yield ServerSentEvent(event=event.type, id=str(sequence), data=event.model_dump(mode="json"))
