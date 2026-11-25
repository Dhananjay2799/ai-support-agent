"""The agent's model call, behind a small interface so tests can script it.

Conversation state is ours (checkpointed in Postgres), not OpenAI's: `store=False`,
and reasoning items come back encrypted so they can be replayed on the next call.

Recovery is explicit, so it shows in the turn's trace: a timeout, dropped connection,
rate limit or server error gets one more attempt after a short backoff, then the
fallback model. A call that already streamed text to the customer is never retried,
since that would repeat words they have seen.
"""

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

import openai
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

from app.observability.faults import model_failure
from app.observability.tracing import SpanRecord, clip, span
from app.observability.usage import cost_of, record

Item = dict[str, Any]
Effort = Literal["none", "low", "medium", "high"]

RETRYABLE = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,
)


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


def describe(error: BaseException) -> str:
    if isinstance(error, openai.APITimeoutError):
        return "timed out"
    if isinstance(error, openai.RateLimitError):
        return "rate limited (429)"
    if isinstance(error, openai.APIConnectionError):
        return "connection failed"
    if isinstance(error, openai.APIStatusError):
        return f"server error ({error.status_code})"
    return f"{type(error).__name__}: {error}"


def summarize_input(items: Sequence[Item], recent: int = 14) -> list[dict[str, Any]]:
    """The model's input as a readable list: messages, tool calls and their results."""
    shown = list(items)[-recent:]
    summary: list[dict[str, Any]] = []
    if len(items) > len(shown):
        summary.append({"earlier_items": len(items) - len(shown)})
    for item in shown:
        kind = item.get("type", "message")
        if kind == "message" or "role" in item:
            content = item.get("content")
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            summary.append({"role": item.get("role", "assistant"), "content": clip(content, 1_500)})
        elif kind == "function_call":
            summary.append({"tool_call": item.get("name"), "arguments": clip(item.get("arguments"), 600)})
        elif kind == "function_call_output":
            summary.append({"tool_result": clip(item.get("output"), 1_200)})
        elif kind == "reasoning":
            summary.append({"reasoning": "(encrypted)"})
    return summary


def _without_reasoning(items: Sequence[Item]) -> list[Item]:
    # Encrypted reasoning only replays on the model that produced it.
    return [item for item in items if item.get("type") != "reasoning"]


class OpenAIResponder:
    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        reasoning_effort: Effort | None = "low",
        *,
        fallback_model: str | None = None,
        attempts: int = 2,
        backoff_s: float = 0.6,
        timeout_s: float = 20.0,
    ) -> None:
        # Retries happen here, where they are visible, not silently inside the SDK.
        self._client = client.with_options(max_retries=0, timeout=timeout_s)
        self.model = model
        self.fallback_model = fallback_model if fallback_model != model else None
        self._effort = reasoning_effort
        self._attempts = attempts
        self._backoff_s = backoff_s

    async def respond(
        self,
        *,
        instructions: str,
        items: Sequence[Item],
        tools: list[FunctionToolParam],
        on_text: Callable[[str], None],
    ) -> ModelTurn:
        models = [self.model, *([self.fallback_model] if self.fallback_model else [])]
        failure: Exception | None = None
        for index, model in enumerate(models):
            fallback = index > 0
            for attempt in range(1, self._attempts + 1):
                streamed = False

                def forward(delta: str) -> None:
                    nonlocal streamed
                    streamed = True
                    on_text(delta)

                with span(
                    "model call", "llm", model=model, attempt=attempt, fallback=fallback, effort=self._effort
                ) as trace:
                    try:
                        if injected := model_failure(index, attempt):
                            trace.set(simulated=True)
                            raise injected
                        return await self._call(
                            model,
                            instructions=instructions,
                            items=_without_reasoning(items) if fallback else list(items),
                            tools=tools,
                            on_text=forward,
                            trace=trace,
                            keep_reasoning=not fallback,
                        )
                    except RETRYABLE as error:
                        if streamed:
                            raise
                        failure = error
                        last_attempt = attempt == self._attempts
                        next_step = (
                            "retry"
                            if not last_attempt
                            else ("fallback model" if index + 1 < len(models) else "give up")
                        )
                        trace.set(status="failed", error=describe(error), recovery=next_step)
                if attempt < self._attempts:
                    await asyncio.sleep(self._delay(attempt, failure))
        assert failure is not None
        raise failure

    def _delay(self, attempt: int, error: Exception | None) -> float:
        delay: float = self._backoff_s * 2.0 ** (attempt - 1)
        if isinstance(error, openai.RateLimitError):
            retry_after = error.response.headers.get("retry-after")
            if retry_after and retry_after.replace(".", "", 1).isdigit():
                delay = min(float(retry_after), 3.0)
        return delay

    async def _call(
        self,
        model: str,
        *,
        instructions: str,
        items: list[Item],
        tools: list[FunctionToolParam],
        on_text: Callable[[str], None],
        trace: SpanRecord,
        keep_reasoning: bool,
    ) -> ModelTurn:
        trace.set(prompt={"instructions": clip(instructions, 8_000), "input": summarize_input(items)})
        started = time.perf_counter()
        first_text: float | None = None
        stream = await self._client.responses.create(
            model=model,
            instructions=instructions,
            input=cast(ResponseInputParam, items),
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
                if first_text is None:
                    first_text = time.perf_counter()
                on_text(event.delta)
            elif isinstance(event, ResponseCompletedEvent):
                completed = event.response
            elif isinstance(event, ResponseFailedEvent | ResponseIncompleteEvent):
                raise RuntimeError(f"model response {event.type}")
        if completed is None:
            raise RuntimeError("model stream ended without a completed response")

        output = [item.model_dump(exclude_none=True) for item in completed.output]
        if not keep_reasoning:
            output = _without_reasoning(output)
        calls = [
            FunctionCall(call_id=i["call_id"], name=i["name"], arguments=i["arguments"])
            for i in output
            if i.get("type") == "function_call"
        ]
        attributes: dict[str, Any] = {
            "status": "ok",
            "output": {
                "text": clip(completed.output_text),
                "tool_calls": [{"name": c.name, "arguments": clip(c.arguments, 600)} for c in calls],
            },
        }
        if first_text is not None:
            attributes["ttft_ms"] = round((first_text - started) * 1000, 1)
        if usage := completed.usage:
            cached = usage.input_tokens_details.cached_tokens
            record(
                model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_tokens=cached,
            )
            attributes.update(
                input_tokens=usage.input_tokens,
                cached_tokens=cached,
                output_tokens=usage.output_tokens,
                reasoning_tokens=usage.output_tokens_details.reasoning_tokens,
                cost_usd=round(cost_of(model, usage.input_tokens, cached, usage.output_tokens), 6),
            )
        trace.set(**attributes)
        return ModelTurn(output_items=output, text=completed.output_text, calls=calls)
