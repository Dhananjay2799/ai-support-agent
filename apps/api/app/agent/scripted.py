"""Day-1 stand-in for the agent: a scripted multi-step turn.

It emits the exact event sequence the real LangGraph agent will produce (steps,
guardrail, tokens, done) so the UI and deployment can be built and tested before
any LLM is wired in. Replaced by `app.agent.graph` on Day 3.
"""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator

from app.contracts import (
    AgentEvent,
    DoneEvent,
    GuardrailEvent,
    StepEvent,
    TokenEvent,
    Usage,
)

_STEPS: tuple[tuple[str, str], ...] = (
    ("verify_customer", "Verifying identity and account tier…"),
    ("search_policies", "Searching the knowledge base…"),
    ("issue_refund", "Checking refund policy limits…"),
)

_REPLY = (
    "This is a scripted Day-1 reply. The real agent will verify your account, "
    "look up the matching policy for your tier, and act within its limits."
)


async def scripted_turn(message: str, *, step_delay_s: float = 0.35) -> AsyncIterator[AgentEvent]:
    started = time.perf_counter()
    turn_id = uuid.uuid4().hex

    for tool, label in _STEPS:
        step_id = uuid.uuid4().hex[:12]
        step_started = time.perf_counter()
        yield StepEvent(id=step_id, tool=tool, label=label, status="started")
        await asyncio.sleep(step_delay_s)
        yield StepEvent(
            id=step_id,
            tool=tool,
            label=label,
            status="completed",
            duration_ms=round((time.perf_counter() - step_started) * 1000, 1),
        )

    yield GuardrailEvent(confidence=1.0, decision="answer", reason="scripted reply")

    for word in _REPLY.split(" "):
        yield TokenEvent(text=word + " ")
        await asyncio.sleep(step_delay_s / 10)

    yield DoneEvent(
        turn_id=turn_id,
        usage=Usage(input_tokens=len(message.split()), output_tokens=len(_REPLY.split())),
        cost_usd=0.0,
        latency_ms=round((time.perf_counter() - started) * 1000, 1),
    )
