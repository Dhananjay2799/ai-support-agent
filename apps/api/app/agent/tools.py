"""The agent's tools: typed, framework-free, and safe to call from chat or voice.

Each tool declares its arguments (a Pydantic model, which also becomes the JSON
schema the model sees), a human-readable step label for the UI, and preconditions
the runtime enforces before the handler runs:

- `requires_verified`: nothing about an account before identity is verified
- `is_action`: no account changes once a human escalation is open

Handlers return a ToolOutcome: what the model sees, what the UI shows on the step
chip, state updates, and any extra events (guardrail verdicts, escalations).
"""

import json
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, TypedDict

from openai.types.responses import FunctionToolParam
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contracts import AgentEvent, EscalationEvent, GuardrailEvent
from app.data import repo
from app.policy.config import ContractStatus, Policy, Tier
from app.policy.engine import RefundRequest, evaluate_refund
from app.rag.guardrails import SupportJudge, Thresholds, assess
from app.rag.retriever import HybridRetriever

TicketCategory = Literal[
    "refund_over_limit",
    "policy_exception",
    "low_confidence",
    "verification_failed",
    "tool_failure",
    "customer_request",
    "other",
]
TicketPriority = Literal["low", "normal", "high", "urgent"]


class Customer(TypedDict):
    id: int
    public_id: str
    name: str
    tier: Tier
    contract_status: ContractStatus
    renews_on: str | None


class Escalation(TypedDict):
    ticket_id: str
    category: str
    priority: str
    first_response_hours: int


class SearchRecord(TypedDict):
    query: str
    decision: Literal["answer", "escalate"]
    confidence: float
    citations: list[dict[str, str]]


@dataclass(frozen=True)
class ToolEnv:
    """Process-wide dependencies."""

    pool: AsyncConnectionPool[Any]
    retriever: HybridRetriever
    judge: SupportJudge
    thresholds: Thresholds
    policy: Policy
    demo_mode: bool
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class ToolSession:
    """What a tool may know about the conversation."""

    session_id: str
    persona_id: str | None
    customer: Customer | None
    failed_verifications: int
    escalation: Escalation | None


@dataclass
class ToolOutcome:
    output: dict[str, Any]
    status: Literal["completed", "failed"] = "completed"
    detail: str | None = None
    updates: dict[str, Any] = field(default_factory=dict)
    events: list[AgentEvent] = field(default_factory=list)


# --------------------------------------------------------------------------- arguments


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VerifyCustomerArgs(_Args):
    email: str | None = Field(description="Account email. null when the customer is signed in.")
    zip_code: str | None = Field(description="Billing ZIP or postal code. null when signed in.")


class GetAccountArgs(_Args):
    pass


class SearchPoliciesArgs(_Args):
    query: str = Field(
        description=(
            "A standalone question about Acme Cloud's documented policy, e.g. 'refund eligibility and "
            "limits on the Pro plan'. Leave out account specifics like amounts, dates and charge ids; "
            "get_account covers those."
        )
    )


class IssueRefundArgs(_Args):
    charge_id: str = Field(description="Charge id from get_account, e.g. 'ch_7HX2P2'.")
    amount: float = Field(description="Amount in USD.")
    reason: str = Field(description="Short reason, e.g. 'duplicate monthly charge'.")


class CreateTicketArgs(_Args):
    category: TicketCategory
    priority: TicketPriority
    summary: str = Field(description="What the specialist needs to know, in two or three sentences.")


# --------------------------------------------------------------------------- handlers


def _public_customer(customer: Customer) -> dict[str, Any]:
    return {k: customer[k] for k in ("name", "tier", "contract_status", "renews_on")}


def _customer(row: repo.CustomerRow) -> Customer:
    return Customer(
        id=row.id,
        public_id=row.public_id,
        name=row.full_name,
        tier=row.tier,
        contract_status=row.contract_status,
        renews_on=row.renews_on.isoformat() if row.renews_on else None,
    )


def _verified(customer: Customer, how: str) -> ToolOutcome:
    return ToolOutcome(
        output={"verified": True, "how": how, "customer": _public_customer(customer)},
        detail=" · ".join(
            [customer["name"], customer["tier"].title(), customer["contract_status"].replace("_", " ")]
        ),
        updates={"customer": customer},
    )


async def _open_ticket(
    env: ToolEnv, s: ToolSession, category: str, priority: str, summary: str
) -> tuple[Escalation, AgentEvent]:
    async with env.pool.connection() as conn:
        ticket = await repo.create_ticket(
            conn,
            customer_id=s.customer["id"] if s.customer else None,
            session_id=s.session_id,
            category=category,
            priority=priority,
            summary=summary,
        )
    tier: Tier = s.customer["tier"] if s.customer else "free"
    escalation = Escalation(
        ticket_id=ticket.public_id,
        category=ticket.category,
        priority=ticket.priority,
        first_response_hours=env.policy.support.first_response_hours[tier],
    )
    event = EscalationEvent(
        ticket_id=ticket.public_id,
        reason=summary,
        priority=ticket.priority,
        first_response_hours=escalation["first_response_hours"],
    )
    return escalation, event


async def verify_customer(env: ToolEnv, s: ToolSession, args: VerifyCustomerArgs) -> ToolOutcome:
    if s.customer:
        return _verified(s.customer, "already verified")
    if args.email is None and args.zip_code is None:
        if s.persona_id and env.demo_mode:
            async with env.pool.connection() as conn:
                row = await repo.customer_for_persona(conn, s.persona_id)
            if row:
                return _verified(_customer(row), "signed in")
        return ToolOutcome(
            output={"verified": False, "next": "Ask for the account email and billing ZIP code."},
            detail="needs email and ZIP",
        )

    async with env.pool.connection() as conn:
        row = await repo.find_customer(conn, email=args.email or "", zip_code=args.zip_code or "")
    if row:
        return _verified(_customer(row), "email and ZIP")

    attempts = s.failed_verifications + 1
    limit = env.policy.verification.max_failed_attempts
    if attempts < limit:
        return ToolOutcome(
            output={"verified": False, "attempts_left": limit - attempts},
            status="failed",
            detail="details didn't match",
            updates={"failed_verifications": attempts},
        )
    escalation, event = await _open_ticket(
        env,
        s,
        "verification_failed",
        "high",
        f"Identity verification failed {attempts} times in one conversation.",
    )
    return ToolOutcome(
        output={"verified": False, "locked": True, "ticket_id": escalation["ticket_id"]},
        status="failed",
        detail="locked after too many attempts",
        updates={"failed_verifications": attempts, "escalation": escalation},
        events=[event],
    )


async def get_account(env: ToolEnv, s: ToolSession, args: GetAccountArgs) -> ToolOutcome:
    assert s.customer is not None  # enforced by requires_verified
    async with env.pool.connection() as conn:
        charges = await repo.recent_charges(conn, s.customer["id"])
    return ToolOutcome(
        output={
            "customer": _public_customer(s.customer),
            "today": env.now().date().isoformat(),
            "charges": [
                {
                    "charge_id": c.public_id,
                    "date": c.created_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC"),
                    "amount": f"{c.amount:.2f}",
                    "description": c.description,
                    "status": c.status,
                    "refunded": f"{c.refunded:.2f}",
                }
                for c in charges
            ],
        },
        detail=f"{len(charges)} recent charges",
    )


async def search_policies(env: ToolEnv, s: ToolSession, args: SearchPoliciesArgs) -> ToolOutcome:
    tier = s.customer["tier"] if s.customer else None
    async with env.pool.connection() as conn:
        retrieval = await env.retriever.search(conn, args.query, tier=tier)
    assessment = await assess(args.query, retrieval, judge=env.judge, thresholds=env.thresholds)
    passages = retrieval.chunks[:4]
    record = SearchRecord(
        query=args.query,
        decision=assessment.decision,
        confidence=assessment.confidence,
        citations=[
            {"id": c.citation_id, "doc_title": c.doc_title, "section": c.section_path} for c in passages
        ]
        if assessment.decision == "answer"
        else [],
    )
    guardrail = GuardrailEvent(
        confidence=assessment.confidence,
        decision="answer" if assessment.decision == "answer" else "escalate",
        reason=assessment.reason,
    )
    updates: dict[str, Any] = {"search": record}
    if assessment.decision == "escalate":
        updates["force_escalation"] = "low_confidence"
        return ToolOutcome(
            output={"answerable": False, "reason": assessment.reason},
            detail="no documented answer",
            updates=updates,
            events=[guardrail],
        )
    return ToolOutcome(
        output={
            "answerable": True,
            "passages": [
                {"id": c.citation_id, "section": c.section_path, "text": c.content} for c in passages
            ],
        },
        detail=f"{assessment.band} confidence · {len(passages)} passages",
        updates=updates,
        events=[guardrail],
    )


async def issue_refund(env: ToolEnv, s: ToolSession, args: IssueRefundArgs) -> ToolOutcome:
    assert s.customer is not None
    amount = Decimal(str(round(args.amount, 2)))
    async with env.pool.connection() as conn:
        charge = await repo.get_charge(conn, s.customer["id"], args.charge_id.strip())
        if charge is None:
            return ToolOutcome(
                output={"status": "not_found", "charge_id": args.charge_id},
                status="failed",
                detail="charge not found on this account",
            )
        # Idempotency first: a retried call returns the original refund rather than
        # being judged again against the charge's new "refunded" status.
        key = f"refund:{charge.public_id}"
        existing = await repo.refund_by_key(conn, key)
        if existing:
            return _refunded(existing, charge.public_id, created=False)
        decision = evaluate_refund(
            RefundRequest(
                tier=s.customer["tier"],
                contract_status=s.customer["contract_status"],
                charge_amount=charge.amount,
                charge_status=charge.status,
                charge_created_at=charge.created_at,
                already_refunded=charge.refunded,
                amount=amount,
                now=env.now(),
                duplicate_already_refunded=await repo.duplicate_refunded(
                    conn, s.customer["id"], charge, env.policy.refunds.duplicate_window_days
                ),
            ),
            env.policy,
        )
        if not decision.approved:
            return ToolOutcome(
                output={
                    "status": "not_allowed",
                    "code": decision.code,
                    "reason": decision.reason,
                    "human_can_approve": decision.human_can_approve,
                },
                detail=f"not allowed: {decision.code.replace('_', ' ')}",
            )
        refund, created = await repo.create_refund(
            conn,
            charge=charge,
            customer_id=s.customer["id"],
            amount=amount,
            reason=args.reason,
            idempotency_key=key,
            public_id=f"re_{secrets.token_hex(4).upper()}",
        )
    return _refunded(refund, charge.public_id, created=created)


def _refunded(refund: repo.RefundRow, charge_id: str, *, created: bool) -> ToolOutcome:
    return ToolOutcome(
        output={
            "status": "refunded",
            "refund_id": refund.public_id,
            "amount": f"{refund.amount:.2f}",
            "charge_id": charge_id,
            "already_existed": not created,
            "arrives": "5 to 10 business days, back to the original payment method",
        },
        detail=f"${refund.amount:.2f} refunded · {refund.public_id}",
    )


async def create_escalation_ticket(env: ToolEnv, s: ToolSession, args: CreateTicketArgs) -> ToolOutcome:
    if s.escalation:
        return ToolOutcome(
            output={"ticket_id": s.escalation["ticket_id"], "already_open": True},
            detail=f"{s.escalation['ticket_id']} already open",
        )
    escalation, event = await _open_ticket(env, s, args.category, args.priority, args.summary)
    return ToolOutcome(
        output={
            "ticket_id": escalation["ticket_id"],
            "priority": escalation["priority"],
            "first_response_hours": escalation["first_response_hours"],
        },
        detail=f"{escalation['ticket_id']} · {escalation['priority']} priority",
        updates={"escalation": escalation},
        events=[event],
    )


# --------------------------------------------------------------------------- registry

Handler = Callable[[ToolEnv, ToolSession, Any], Awaitable[ToolOutcome]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args: type[_Args]
    label: str
    handler: Handler
    requires_verified: bool = False
    is_action: bool = False


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            "verify_customer",
            "Verify who the customer is and get their plan (tier) and contract status. "
            "Pass null for both fields when the customer is signed in.",
            VerifyCustomerArgs,
            "Verifying identity and account tier…",
            verify_customer,
        ),
        ToolSpec(
            "get_account",
            "The verified customer's plan and recent charges, with charge ids and refund status.",
            GetAccountArgs,
            "Checking the account and recent charges…",
            get_account,
            requires_verified=True,
        ),
        ToolSpec(
            "search_policies",
            "Search Acme Cloud's support knowledge base (policies, plans, billing, security, "
            "troubleshooting), filtered to the customer's plan. Returns citable passages, or says "
            "there is no documented answer.",
            SearchPoliciesArgs,
            "Searching the knowledge base…",
            search_policies,
        ),
        ToolSpec(
            "issue_refund",
            "Refund a charge. Refund rules are enforced by the system: it refuses anything outside "
            "policy and says whether a person could approve it.",
            IssueRefundArgs,
            "Checking policy limits and issuing the refund…",
            issue_refund,
            requires_verified=True,
            is_action=True,
        ),
        ToolSpec(
            "create_escalation_ticket",
            "Hand the conversation to a human specialist with a structured ticket.",
            CreateTicketArgs,
            "Creating an escalation ticket…",
            create_escalation_ticket,
        ),
    )
}


def _strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    properties = schema.get("properties", {})
    for prop in properties.values():
        prop.pop("title", None)
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def tool_schemas() -> list[FunctionToolParam]:
    return [
        FunctionToolParam(
            type="function",
            name=spec.name,
            description=spec.description,
            parameters=_strict_schema(spec.args),
            strict=True,
        )
        for spec in TOOLS.values()
    ]


async def run_tool(name: str, arguments: str, env: ToolEnv, session: ToolSession) -> ToolOutcome:
    """Validate, check preconditions, run. Never raises for model mistakes: the model
    gets a structured error it can recover from."""
    spec = TOOLS.get(name)
    if spec is None:
        return ToolOutcome(output={"error": f"unknown tool {name}"}, status="failed", detail="unknown tool")
    try:
        args = spec.args.model_validate(json.loads(arguments or "{}"))
    except (ValidationError, json.JSONDecodeError) as error:
        return ToolOutcome(
            output={"error": "invalid arguments", "details": str(error)[:500]}, status="failed"
        )
    if spec.requires_verified and session.customer is None:
        return ToolOutcome(
            output={"error": "not_verified", "next": "Call verify_customer first."},
            status="failed",
            detail="blocked: customer not verified",
        )
    if spec.is_action and session.escalation:
        return ToolOutcome(
            output={
                "error": "escalated",
                "next": "A specialist owns this case now; take no account actions.",
            },
            status="failed",
            detail=f"blocked: {session.escalation['ticket_id']} is open",
        )
    return await spec.handler(env, session, args)
