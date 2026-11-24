"""The agent's model call, behind a small interface so tests can script it.

Conversation state is ours (checkpointed in Postgres), not OpenAI's: `store=False`,
and reasoning items come back encrypted so they can be replayed on the next call.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

from openai import AsyncOpenAI, omit
from openai.types.responses import (
    FunctionToolParam,
    Response,
    ResponseCompletedEvent,
    ResponseFailedEvent,
    ResponseIncompleteEvent,
    ResponseInputParam,
    ResponseTextDeltaEvent,
)

from app.observability.usage import record

Item = dict[str, Any]


@dataclass(frozen=True)
class FunctionCall:
    call_id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ModelTurn:
    output_items: list[Item]
    text: str
    calls: list[FunctionCall] = field(default_factory=list)


class Responder(Protocol):
    async def respond(
        self,
        *,
        instructions: str,
        items: Sequence[Item],
        tools: list[FunctionToolParam],
        on_text: Callable[[str], None],
    ) -> ModelTurn: ...


class OpenAIResponder:
    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        reasoning_effort: Literal["none", "low", "medium", "high"] | None = "low",
    ) -> None:
        self._client = client
        self.model = model
        self._effort = reasoning_effort

    async def respond(
        self,
        *,
        instructions: str,
        items: Sequence[Item],
        tools: list[FunctionToolParam],
        on_text: Callable[[str], None],
    ) -> ModelTurn:
        stream = await self._client.responses.create(
            model=self.model,
            instructions=instructions,
            input=cast(ResponseInputParam, list(items)),
            tools=tools,
            # Several calls per step save model round trips; the graph still runs them one at
            # a time, in order, so verify_customer listed first unlocks get_account after it.
            parallel_tool_calls=True,
            reasoning={"effort": self._effort} if self._effort else omit,
            include=["reasoning.encrypted_content"],
            store=False,
            stream=True,
        )
        completed: Response | None = None
        async for event in stream:
            if isinstance(event, ResponseTextDeltaEvent):
                on_text(event.delta)
            elif isinstance(event, ResponseCompletedEvent):
                completed = event.response
            elif isinstance(event, ResponseFailedEvent | ResponseIncompleteEvent):
                raise RuntimeError(f"model response {event.type}")
        if completed is None:
            raise RuntimeError("model stream ended without a completed response")

        if completed.usage:
            record(
                self.model,
                input_tokens=completed.usage.input_tokens,
                output_tokens=completed.usage.output_tokens,
                cached_tokens=completed.usage.input_tokens_details.cached_tokens,
            )
        output = [item.model_dump(exclude_none=True) for item in completed.output]
        calls = [
            FunctionCall(call_id=i["call_id"], name=i["name"], arguments=i["arguments"])
            for i in output
            if i.get("type") == "function_call"
        ]
        return ModelTurn(output_items=output, text=completed.output_text, calls=calls)
