"""The agent graph with a scripted model, real tools, real Postgres and a real checkpointer."""

import json
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from openai.types.responses import FunctionToolParam

from app.agent import graph as graph_module
from app.agent.graph import MAX_MODEL_CALLS, build_graph, run_turn
from app.agent.llm import FunctionCall, Item, ModelTurn
from app.agent.tools import ToolEnv
from app.contracts import AgentEvent
from app.rag.guardrails import Thresholds

Step = ModelTurn | Callable[[Sequence[Item]], ModelTurn]


def call(name: str, **args: Any) -> ModelTurn:
    call_id = f"call_{uuid.uuid4().hex[:10]}"
    arguments = json.dumps(args)
    item = {"type": "function_call", "call_id": call_id, "name": name, "arguments": arguments}
    return ModelTurn(output_items=[item], text="", calls=[FunctionCall(call_id, name, arguments)])


def calls(*turns: ModelTurn) -> ModelTurn:
    """Several tool calls in one model step (parallel tool calls)."""
    return ModelTurn(
        output_items=[i for t in turns for i in t.output_items],
        text="",
        calls=[c for t in turns for c in t.calls],
    )


def say(text: str) -> ModelTurn:
    item = {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}
    return ModelTurn(output_items=[item], text=text)


class ScriptedModel:
    def __init__(self, *steps: Step) -> None:
        self.steps = list(steps)
        self.seen: list[tuple[str, int]] = []  # (instructions, number of items) per call

    async def respond(
        self,
        *,
        instructions: str,
        items: Sequence[Item],
        tools: list[FunctionToolParam],
        on_text: Callable[[str], None],
    ) -> ModelTurn:
        self.seen.append((instructions, len(items)))
        if not self.steps:
            raise AssertionError("the graph called the model more often than the script allows")
        step = self.steps.pop(0)
        turn = step(items) if callable(step) else step
        for word in re.findall(r"\S+\s*", turn.text):
            on_text(word)
        return turn


def last_passage_id(items: Sequence[Item]) -> str:
    outputs = [json.loads(i["output"]) for i in items if i.get("type") == "function_call_output"]
    search = next(o for o in reversed(outputs) if "passages" in o)
    return str(search["passages"][0]["id"])


async def turn(
    env: ToolEnv, model: ScriptedModel, session: str, message: str, persona: str | None
) -> list[Any]:
    saver = AsyncPostgresSaver(env.pool)
    graph = build_graph(model, env, saver)
    return [e async for e in run_turn(graph, session_id=session, message=message, persona_id=persona)]


def kinds(events: list[AgentEvent]) -> list[str]:
    return [f"{e.type}:{e.status}" if e.type == "step" else e.type for e in events]


async def test_priya_duplicate_charge_chains_four_tools(env: ToolEnv) -> None:
    answering = replace(env, thresholds=Thresholds(low=-1.0, high=-0.5))  # always "high" band
    model = ScriptedModel(
        call("verify_customer", email=None, zip_code=None),
        call("get_account"),
        call("search_policies", query="refund for a duplicate charge"),
        call("issue_refund", charge_id="ch_7HX2P2", amount=29.0, reason="duplicate monthly charge"),
        lambda items: say(
            f"I've refunded the duplicate $29.00 charge [{last_passage_id(items)}]. "
            "It reaches your card in 5 to 10 business days [made-up-doc#9]."
        ),
    )
    session = str(uuid.uuid4())
    events = await turn(answering, model, session, "I was charged twice this month", persona="priya")

    steps = [e for e in events if e.type == "step"]
    assert [(s.tool, s.status) for s in steps] == [
        (tool, status)
        for tool in ("verify_customer", "get_account", "search_policies", "issue_refund")
        for status in ("started", "completed")
    ]
    assert steps[-1].detail and steps[-1].detail.startswith("$29.00 refunded")
    assert "guardrail" in kinds(events) and kinds(events)[-1] == "done"

    citations = next(e for e in events if e.type == "citations").citations
    assert len(citations) == 1 and citations[0].id != "made-up-doc#9"  # unretrieved citation dropped

    async with env.pool.connection() as conn:
        refunds = await (await conn.execute("select count(*) as n from public.refunds")).fetchone()
    assert refunds is not None and refunds["n"] == 1


async def test_batched_calls_run_in_order_so_verification_unlocks_the_account(env: ToolEnv) -> None:
    model = ScriptedModel(
        calls(call("verify_customer", email=None, zip_code=None), call("get_account")),
        say("You have three recent charges."),
    )
    events = await turn(env, model, str(uuid.uuid4()), "what did you charge me?", persona="priya")

    completed = [(e.tool, e.detail) for e in events if e.type == "step" and e.status == "completed"]
    assert completed == [
        ("verify_customer", "Priya Shah · Pro · active"),
        ("get_account", "3 recent charges"),
    ]
    assert model.seen[1][1] == 5  # user message, two calls, two outputs


async def test_memory_carries_across_turns(env: ToolEnv) -> None:
    session = str(uuid.uuid4())
    first = ScriptedModel(
        call("verify_customer", email=None, zip_code=None), say("Hi Priya, how can I help?")
    )
    await turn(env, first, session, "hi", persona="priya")

    second = ScriptedModel(say("You're on the Pro plan."))
    await turn(env, second, session, "what plan am I on?", persona="priya")

    instructions, item_count = second.seen[0]
    assert "Verified customer: Priya Shah, Pro plan" in instructions
    assert item_count == 5  # user, call, call output, reply, new user message


async def test_undocumented_question_is_escalated_without_a_model_answer(env: ToolEnv) -> None:
    strict = replace(env, thresholds=Thresholds(low=0.99, high=1.0))
    model = ScriptedModel(call("search_policies", query="can I pay with paypal"))  # nothing after this
    events = await turn(strict, model, str(uuid.uuid4()), "Can I pay with PayPal?", persona=None)

    escalation = next(e for e in events if e.type == "escalation")
    text = "".join(e.text for e in events if e.type == "token")
    assert "rather than guess" in text and escalation.ticket_id in text
    assert model.steps == [] and len(model.seen) == 1

    async with env.pool.connection() as conn:
        ticket = await (await conn.execute("select category from public.tickets")).fetchone()
    assert ticket is not None and ticket["category"] == "low_confidence"


async def test_step_cap_hands_over_to_a_person(env: ToolEnv) -> None:
    looping = ScriptedModel(*[call("search_policies", query=f"attempt {i}") for i in range(MAX_MODEL_CALLS)])
    events = await turn(env, looping, str(uuid.uuid4()), "help", persona=None)
    assert len(looping.seen) == MAX_MODEL_CALLS
    assert any(e.type == "escalation" for e in events)
    assert "taking longer than it should" in "".join(e.text for e in events if e.type == "token")


async def test_tool_crash_is_reported_and_the_turn_still_finishes(
    env: ToolEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("database went away")

    monkeypatch.setattr(graph_module, "run_tool", broken)
    model = ScriptedModel(call("get_account"), say("Sorry, I can't reach your account right now."))
    events = await turn(env, model, str(uuid.uuid4()), "show my charges", persona="priya")

    failed = next(e for e in events if e.type == "step" and e.status == "failed")
    assert failed.detail == "temporarily unavailable"
    assert kinds(events)[-1] == "done"
