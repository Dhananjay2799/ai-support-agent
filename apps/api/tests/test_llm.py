"""Model-call recovery: retries, the fallback model, and what the trace records."""

from collections.abc import AsyncIterator
from typing import Any

import httpx2 as httpx
import openai
import pytest
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseReasoningItem,
    ResponseTextDeltaEvent,
    ResponseUsage,
)
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

from app.agent.llm import OpenAIResponder
from app.observability.faults import Fault, FaultPlan, current_faults
from app.observability.turn import TurnTelemetry

REASONING: dict[str, Any] = {"type": "reasoning", "id": "rs_1", "encrypted_content": "opaque", "summary": []}
HISTORY: list[dict[str, Any]] = [{"role": "user", "content": "hi"}, REASONING]


def build(model: Any, **fields: Any) -> Any:
    """An SDK event or object without the fields these tests don't need."""
    return model.model_construct(**fields)


def delta(text: str) -> Any:
    return build(ResponseTextDeltaEvent, type="response.output_text.delta", delta=text)


def completed(text: str) -> Any:
    message = build(
        ResponseOutputMessage,
        id="msg_1",
        type="message",
        role="assistant",
        status="completed",
        content=[build(ResponseOutputText, type="output_text", text=text, annotations=[])],
    )
    usage = build(
        ResponseUsage,
        input_tokens=100,
        input_tokens_details=build(InputTokensDetails, cached_tokens=40),
        output_tokens=10,
        output_tokens_details=build(OutputTokensDetails, reasoning_tokens=4),
        total_tokens=110,
    )
    reasoning = build(ResponseReasoningItem, **REASONING)
    response = build(Response, output=[message, reasoning], usage=usage)
    return build(ResponseCompletedEvent, type="response.completed", response=response)


class FakeResponses:
    def __init__(self, script: list[Any]) -> None:
        self.script = script
        self.calls: list[dict[str, Any]] = []

    async def create(self, **params: Any) -> AsyncIterator[Any]:
        self.calls.append(params)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step

        async def stream() -> AsyncIterator[Any]:
            for event in step:
                if isinstance(event, Exception):
                    raise event
                yield event

        return stream()


class FakeClient:
    def __init__(self, script: list[Any]) -> None:
        self.responses = FakeResponses(script)

    def with_options(self, **_: Any) -> "FakeClient":
        return self


def responder(client: FakeClient) -> OpenAIResponder:
    return OpenAIResponder(client, "primary", "low", fallback_model="backup", backoff_s=0)  # type: ignore[arg-type]


async def respond(client: FakeClient, fault: Fault | None = None) -> tuple[str, list[Any], list[str]]:
    token = current_faults.set(FaultPlan(fault) if fault else None)
    streamed: list[str] = []
    try:
        with TurnTelemetry() as telemetry:
            turn = await responder(client).respond(
                instructions="Be helpful.", items=HISTORY, tools=[], on_text=streamed.append
            )
            spans = [s for s in telemetry.trace("t").spans if s.name == "model call"]
    finally:
        current_faults.reset(token)
    return turn.text, spans, streamed


async def test_a_successful_call_is_traced_with_prompt_tokens_and_cost() -> None:
    client = FakeClient([[delta("Hi"), completed("Hi")]])
    text, [span], streamed = await respond(client)

    assert text == "Hi" and streamed == ["Hi"]
    attributes = span.attributes
    assert attributes["status"] == "ok" and attributes["model"] == "primary" and attributes["attempt"] == 1
    assert attributes["input_tokens"] == 100 and attributes["cached_tokens"] == 40
    assert attributes["reasoning_tokens"] == 4 and "ttft_ms" in attributes
    prompt = attributes["prompt"]
    assert isinstance(prompt, dict) and prompt["instructions"] == "Be helpful."


async def test_a_timeout_is_retried_on_the_same_model() -> None:
    client = FakeClient([[completed("Recovered")]])
    text, spans, _ = await respond(client, fault="llm_timeout")

    assert text == "Recovered"
    assert [(s.attributes["attempt"], s.attributes["status"]) for s in spans] == [(1, "failed"), (2, "ok")]
    assert spans[0].attributes["error"] == "timed out" and spans[0].attributes["recovery"] == "retry"
    assert len(client.responses.calls) == 1  # the first attempt was the simulated timeout


async def test_persistent_rate_limits_fall_back_to_the_backup_model() -> None:
    client = FakeClient([[completed("From the backup")]])
    text, spans, _ = await respond(client, fault="rate_limit")

    assert text == "From the backup"
    assert [(s.attributes["model"], s.attributes["status"]) for s in spans] == [
        ("primary", "failed"),
        ("primary", "failed"),
        ("backup", "ok"),
    ]
    assert spans[1].attributes["recovery"] == "fallback model"
    [call] = client.responses.calls
    assert call["model"] == "backup"
    assert all(item.get("type") != "reasoning" for item in call["input"])  # primary's reasoning stays behind


async def test_a_call_that_already_streamed_text_is_not_retried() -> None:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    client = FakeClient([[delta("Your refund"), openai.APIConnectionError(request=request)]])

    with pytest.raises(openai.APIConnectionError):
        await respond(client)
    assert len(client.responses.calls) == 1


async def test_a_bad_request_is_not_retried() -> None:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    error = openai.BadRequestError("bad", response=httpx.Response(400, request=request), body=None)
    client = FakeClient([error])

    with pytest.raises(openai.BadRequestError):
        await respond(client)
    assert len(client.responses.calls) == 1
