"""The support agent as a LangGraph state machine.

    start_turn -> agent -> tools -> agent -> ... -> finalize
                    \\         \\
                     \\         -> escalate -> finalize   (undocumented answer, locked identity)
                      -> escalate                         (step cap reached)

The model decides which tool to call; the graph decides what's allowed:
- an undocumented answer never reaches the customer: the turn is routed to a human,
- the number of model calls per turn is capped,
- citations are checked against the passages actually retrieved.

Conversation state is checkpointed in Postgres per session (thread_id = session_id),
which is what gives chat and voice one shared memory.
"""

import json
import operator
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Annotated, Any, Literal, TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.llm import FunctionCall, Item, Responder
from app.agent.prompts import agent_instructions, escalation_message
from app.agent.tools import (
    TOOLS,
    Customer,
    Escalation,
    SearchRecord,
    ToolEnv,
    ToolOutcome,
    ToolSession,
    run_tool,
    tool_schemas,
)
from app.contracts import AgentEvent, Citation, CitationsEvent, DoneEvent, StepEvent, TokenEvent, Usage
from app.observability.usage import current_meter

MAX_MODEL_CALLS = 8
CITATION = re.compile(r"\[([a-z0-9-]+#\d+)\]")


class AgentState(TypedDict, total=False):
    # Conversation, kept across turns (and across chat and voice)
    items: Annotated[list[Item], operator.add]
    session_id: str
    persona_id: str | None
    customer: Customer | None
    failed_verifications: int
    escalation: Escalation | None
    # Reset at the start of every turn
    turn_id: str
    turn_started: float
    turn_model_calls: int
    turn_text: str
    turn_searches: list[SearchRecord]
    pending_calls: list[dict[str, str]]
    force_escalation: str | None


def _emit(event: AgentEvent) -> None:
    get_stream_writer()(event)


def _session(state: AgentState) -> ToolSession:
    return ToolSession(
        session_id=state["session_id"],
        persona_id=state.get("persona_id"),
        customer=state.get("customer"),
        failed_verifications=state.get("failed_verifications", 0),
        escalation=state.get("escalation"),
    )


def build_graph(
    responder: Responder, env: ToolEnv, checkpointer: BaseCheckpointSaver[Any] | None
) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    schemas = tool_schemas()

    async def start_turn(state: AgentState) -> dict[str, Any]:
        return {
            "turn_id": uuid.uuid4().hex,
            "turn_started": time.time(),
            "turn_model_calls": 0,
            "turn_text": "",
            "turn_searches": [],
            "pending_calls": [],
            "force_escalation": None,
        }

    async def agent(state: AgentState) -> dict[str, Any]:
        calls_so_far = state.get("turn_model_calls", 0)
        if calls_so_far >= MAX_MODEL_CALLS:
            return {"force_escalation": "step_limit"}

        streamed: list[str] = []
        separator = "\n\n" if state.get("turn_text") else ""

        def on_text(delta: str) -> None:
            if not streamed and separator:
                _emit(TokenEvent(text=separator))
            streamed.append(delta)
            _emit(TokenEvent(text=delta))

        turn = await responder.respond(
            instructions=agent_instructions(
                customer=state.get("customer"),
                signed_in=bool(state.get("persona_id")),
                escalation=state.get("escalation"),
                today=env.now().date(),
            ),
            items=state.get("items", []),
            tools=schemas,
            on_text=on_text,
        )
        text = state.get("turn_text", "") + (separator + turn.text if turn.text else "")
        return {
            "items": turn.output_items,
            "turn_model_calls": calls_so_far + 1,
            "turn_text": text,
            "pending_calls": [vars(call) for call in turn.calls],
        }

    async def tools(state: AgentState) -> dict[str, Any]:
        session = _session(state)
        searches = list(state.get("turn_searches", []))
        outputs: list[Item] = []
        updates: dict[str, Any] = {}
        force: str | None = None

        for raw in state.get("pending_calls", []):
            call = FunctionCall(**raw)
            spec = TOOLS.get(call.name)
            label = spec.label if spec else call.name
            step_id = call.call_id[-12:]
            _emit(StepEvent(id=step_id, tool=call.name, label=label, status="started"))
            started = time.perf_counter()
            try:
                outcome = await run_tool(call.name, call.arguments, env, session)
            except Exception:  # infrastructure failure: tell the model, don't crash the turn
                outcome = ToolOutcome(
                    output={"error": "tool_unavailable", "next": "Apologize and offer a specialist."},
                    status="failed",
                    detail="temporarily unavailable",
                )
            _emit(
                StepEvent(
                    id=step_id,
                    tool=call.name,
                    label=label,
                    status=outcome.status,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                    detail=outcome.detail,
                )
            )
            for event in outcome.events:
                _emit(event)

            if "customer" in outcome.updates:
                session = replace(session, customer=outcome.updates["customer"])
                updates["customer"] = outcome.updates["customer"]
            if "failed_verifications" in outcome.updates:
                session = replace(session, failed_verifications=outcome.updates["failed_verifications"])
                updates["failed_verifications"] = outcome.updates["failed_verifications"]
            if "escalation" in outcome.updates:
                session = replace(session, escalation=outcome.updates["escalation"])
                updates["escalation"] = outcome.updates["escalation"]
                if outcome.updates["escalation"]["category"] == "verification_failed":
                    force = "verification_failed"
            if "search" in outcome.updates:
                searches.append(outcome.updates["search"])
            force = force or outcome.updates.get("force_escalation")

            import json

            outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(outcome.output),
                }
            )

        return {
            **updates,
            "items": outputs,
            "turn_searches": searches,
            "pending_calls": [],
            "force_escalation": force,
        }

    async def escalate(state: AgentState) -> dict[str, Any]:
        reason = state.get("force_escalation") or "other"
        escalation = state.get("escalation")
        updates: dict[str, Any] = {"force_escalation": None}
        if escalation is None:
            searches = state.get("turn_searches", [])
            summary = (
                f"No documented answer for: {searches[-1]['query']}"
                if reason == "low_confidence" and searches
                else f"Agent could not finish the request ({reason})."
            )
            category = "low_confidence" if reason == "low_confidence" else "other"

            call_id = f"escalate_{uuid.uuid4().hex[:8]}"
            spec = TOOLS["create_escalation_ticket"]
            _emit(StepEvent(id=call_id, tool=spec.name, label=spec.label, status="started"))
            started = time.perf_counter()
            outcome = await run_tool(
                spec.name,
                json.dumps({"category": category, "priority": "normal", "summary": summary}),
                env,
                _session(state),
            )
            _emit(
                StepEvent(
                    id=call_id,
                    tool=spec.name,
                    label=spec.label,
                    status=outcome.status,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                    detail=outcome.detail,
                )
            )
            for event in outcome.events:
                _emit(event)
            escalation = outcome.updates.get("escalation")
            updates["escalation"] = escalation
        if escalation is None:  # ticket creation failed too: still never leave silence
            message = "I'm having trouble right now. Please try again in a moment."
        else:
            message = escalation_message(reason, escalation)
        separator = "\n\n" if state.get("turn_text") else ""
        _emit(TokenEvent(text=separator + message))
        return {
            **updates,
            "items": [{"role": "assistant", "content": message}],
            "turn_text": state.get("turn_text", "") + separator + message,
        }

    async def finalize(state: AgentState) -> dict[str, Any]:
        known = {c["id"]: c for search in state.get("turn_searches", []) for c in search["citations"]}
        cited: list[Citation] = []
        for citation_id in CITATION.findall(state.get("turn_text", "")):
            if citation_id in known and all(c.id != citation_id for c in cited):
                source = known[citation_id]
                cited.append(
                    Citation(id=citation_id, doc_title=source["doc_title"], section=source["section"])
                )
        if cited:
            _emit(CitationsEvent(citations=cited))

        meter = current_meter.get()
        _emit(
            DoneEvent(
                turn_id=state["turn_id"],
                usage=meter.usage() if meter else Usage(),
                cost_usd=round(meter.cost_usd, 6) if meter else 0.0,
                latency_ms=round((time.time() - state["turn_started"]) * 1000, 1),
            )
        )
        return {}

    def after_agent(state: AgentState) -> Literal["tools", "escalate", "finalize"]:
        if state.get("force_escalation"):
            return "escalate"
        return "tools" if state.get("pending_calls") else "finalize"

    def after_tools(state: AgentState) -> Literal["agent", "escalate"]:
        return "escalate" if state.get("force_escalation") else "agent"

    builder = StateGraph(AgentState)
    builder.add_node("start_turn", start_turn)
    builder.add_node("agent", agent)
    builder.add_node("tools", tools)
    builder.add_node("escalate", escalate)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "start_turn")
    builder.add_edge("start_turn", "agent")
    builder.add_conditional_edges("agent", after_agent)
    builder.add_conditional_edges("tools", after_tools)
    builder.add_edge("escalate", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)


async def run_turn(
    graph: CompiledStateGraph[AgentState, None, AgentState, AgentState],
    *,
    session_id: str,
    message: str,
    persona_id: str | None,
) -> AsyncIterator[AgentEvent]:
    inputs: AgentState = {
        "items": [{"role": "user", "content": message}],
        "session_id": session_id,
        "persona_id": persona_id,
    }
    config: RunnableConfig = {
        "configurable": {"thread_id": session_id},
        "recursion_limit": 4 * MAX_MODEL_CALLS,
    }
    async for event in graph.astream(inputs, config, stream_mode="custom"):
        yield cast(AgentEvent, event)  # only our nodes write to the custom stream
