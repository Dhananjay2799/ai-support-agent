import json
from dataclasses import replace
from typing import Any

import pytest

from app.agent.tools import ToolEnv, ToolSession, run_tool, tool_schemas
from app.rag.guardrails import Thresholds

GUEST = ToolSession(
    session_id="00000000-0000-0000-0000-000000000001",
    persona_id=None,
    customer=None,
    failed_verifications=0,
    escalation=None,
)


async def call(env: ToolEnv, session: ToolSession, name: str, **args: Any) -> Any:
    return await run_tool(name, json.dumps(args), env, session)


async def signed_in(env: ToolEnv, persona: str) -> ToolSession:
    session = replace(GUEST, persona_id=persona)
    outcome = await call(env, session, "verify_customer", email=None, zip_code=None)
    return replace(session, customer=outcome.updates["customer"])


def test_schemas_are_strict_and_complete() -> None:
    schemas: dict[str, Any] = {s["name"]: s for s in tool_schemas()}
    assert set(schemas) == {
        "verify_customer",
        "get_account",
        "search_policies",
        "issue_refund",
        "create_escalation_ticket",
    }
    for schema in schemas.values():
        params = schema["parameters"]
        assert schema["strict"] is True and params["additionalProperties"] is False
        assert set(params["required"]) == set(params["properties"])


async def test_signed_in_persona_is_verified_without_asking(env: ToolEnv) -> None:
    outcome = await call(
        env, replace(GUEST, persona_id="priya"), "verify_customer", email=None, zip_code=None
    )
    assert outcome.output["verified"] and outcome.output["how"] == "signed in"
    assert outcome.updates["customer"]["tier"] == "pro"
    assert "email" not in outcome.output["customer"]


async def test_guest_without_details_is_asked_for_them(env: ToolEnv) -> None:
    outcome = await call(env, GUEST, "verify_customer", email=None, zip_code=None)
    assert outcome.output["verified"] is False and "customer" not in outcome.updates


async def test_email_and_zip_verify_case_insensitively(env: ToolEnv) -> None:
    outcome = await call(env, GUEST, "verify_customer", email="Elena.Ruiz@Example.com", zip_code="60601")
    assert outcome.updates["customer"]["tier"] == "enterprise"


async def test_three_failed_verifications_lock_and_escalate(env: ToolEnv) -> None:
    session = GUEST
    for _ in range(3):
        outcome = await call(env, session, "verify_customer", email="who@example.com", zip_code="00000")
        session = replace(session, failed_verifications=outcome.updates["failed_verifications"])
        assert outcome.status == "failed"
        assert "which" not in json.dumps(outcome.output)  # never says which detail was wrong
    assert outcome.output["locked"] and outcome.updates["escalation"]["category"] == "verification_failed"
    assert [e.type for e in outcome.events] == ["escalation"]


async def test_account_tools_require_verification(env: ToolEnv) -> None:
    outcome = await call(env, GUEST, "get_account")
    assert outcome.status == "failed" and outcome.output["error"] == "not_verified"


async def test_duplicate_charge_refund_is_idempotent(env: ToolEnv) -> None:
    priya = await signed_in(env, "priya")
    account = await call(env, priya, "get_account")
    duplicate = account.output["charges"][0]["charge_id"]

    first = await call(
        env, priya, "issue_refund", charge_id=duplicate, amount=29.0, reason="duplicate charge"
    )
    assert first.output["status"] == "refunded" and not first.output["already_existed"]

    retry = await call(
        env, priya, "issue_refund", charge_id=duplicate, amount=29.0, reason="duplicate charge"
    )
    assert retry.output["refund_id"] == first.output["refund_id"] and retry.output["already_existed"]

    async with env.pool.connection() as conn:
        cur = await conn.execute("select count(*) as n from public.refunds")
        row = await cur.fetchone()
    assert row is not None and row["n"] == 1


async def test_only_one_charge_of_a_duplicate_pair_is_refunded(env: ToolEnv) -> None:
    """Asked again in a new conversation, the agent must not refund the original charge too."""
    priya = await signed_in(env, "priya")
    first = await call(env, priya, "issue_refund", charge_id="ch_7HX2P2", amount=29.0, reason="duplicate")
    assert first.output["status"] == "refunded"

    later = await signed_in(env, "priya")  # a fresh session
    second = await call(env, later, "issue_refund", charge_id="ch_7HX2P1", amount=29.0, reason="duplicate")
    assert second.output["status"] == "not_allowed"
    assert second.output["code"] == "duplicate_already_refunded" and second.output["human_can_approve"]


@pytest.mark.parametrize(
    ("persona", "charge_id", "amount", "code"),
    [
        ("elena", "ch_6ER0E2", 1200.0, "over_auto_limit"),
        ("tom", "ch_1TB5P1", 29.0, "contract_inactive"),
        ("marcus", "ch_2MA9F1", 9.0, "refunds_not_offered"),
        ("priya", "ch_4KD1P0", 29.0, "outside_window"),
    ],
)
async def test_policy_blocks_are_reported_not_raised(
    env: ToolEnv, persona: str, charge_id: str, amount: float, code: str
) -> None:
    session = await signed_in(env, persona)
    outcome = await call(env, session, "issue_refund", charge_id=charge_id, amount=amount, reason="asked")
    assert outcome.status == "completed"
    assert outcome.output["status"] == "not_allowed" and outcome.output["code"] == code


async def test_another_customers_charge_is_not_found(env: ToolEnv) -> None:
    priya = await signed_in(env, "priya")
    outcome = await call(env, priya, "issue_refund", charge_id="ch_6ER0E2", amount=10.0, reason="x")
    assert outcome.output["status"] == "not_found"


async def test_no_account_actions_after_escalation(env: ToolEnv) -> None:
    priya = await signed_in(env, "priya")
    ticket = await call(
        env,
        priya,
        "create_escalation_ticket",
        category="customer_request",
        priority="normal",
        summary="Wants a person.",
    )
    escalated = replace(priya, escalation=ticket.updates["escalation"])
    assert ticket.updates["escalation"]["first_response_hours"] == 24  # Pro SLA from policy.yaml

    refund = await call(env, escalated, "issue_refund", charge_id="ch_7HX2P2", amount=29.0, reason="dup")
    assert refund.status == "failed" and refund.output["error"] == "escalated"

    again = await call(
        env, escalated, "create_escalation_ticket", category="other", priority="low", summary="x"
    )
    assert again.output["already_open"] and again.output["ticket_id"] == ticket.output["ticket_id"]


async def test_search_answers_with_citable_passages(env: ToolEnv) -> None:
    outcome = await call(env, GUEST, "search_policies", query="how long do refunds take to arrive")
    assert outcome.output["answerable"] and outcome.output["passages"]
    assert outcome.updates["search"]["decision"] == "answer"
    assert [e.type for e in outcome.events] == ["guardrail"]


async def test_undocumented_question_forces_escalation(env: ToolEnv) -> None:
    strict = replace(env, thresholds=Thresholds(low=0.99, high=1.0))
    outcome = await call(strict, GUEST, "search_policies", query="can I pay with paypal")
    assert outcome.output == {"answerable": False, "reason": "no relevant documentation found"}
    assert outcome.updates["force_escalation"] == "low_confidence"


async def test_bad_arguments_come_back_as_a_recoverable_error(env: ToolEnv) -> None:
    outcome = await run_tool("issue_refund", '{"charge_id": 5}', env, GUEST)
    assert outcome.status == "failed" and outcome.output["error"] in {"invalid arguments", "not_verified"}
