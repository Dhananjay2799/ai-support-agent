from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.policy.config import load_policy
from app.policy.engine import RefundRequest, evaluate_refund

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
POLICY = load_policy()

PRO_DUPLICATE = RefundRequest(
    tier="pro",
    contract_status="active",
    charge_amount=Decimal("29.00"),
    charge_status="succeeded",
    charge_created_at=NOW - timedelta(days=6),
    already_refunded=Decimal("0"),
    amount=Decimal("29.00"),
    now=NOW,
)


def test_pro_duplicate_charge_is_approved() -> None:
    decision = evaluate_refund(PRO_DUPLICATE, POLICY)
    assert decision.approved and decision.code == "approved"


@pytest.mark.parametrize(
    ("change", "code", "human_can_approve"),
    [
        ({"amount": Decimal("0")}, "invalid_amount", False),
        ({"contract_status": "past_due"}, "contract_inactive", False),
        ({"contract_status": "canceled"}, "contract_inactive", False),
        ({"tier": "free"}, "refunds_not_offered", True),
        ({"charge_status": "failed"}, "charge_not_refundable", False),
        ({"already_refunded": Decimal("29.00")}, "already_refunded", False),
        ({"amount": Decimal("29.01")}, "exceeds_charge", False),
        ({"charge_created_at": NOW - timedelta(days=31)}, "outside_window", True),
    ],
)
def test_each_rule_blocks_with_its_own_code(
    change: dict[str, object], code: str, human_can_approve: bool
) -> None:
    decision = evaluate_refund(replace(PRO_DUPLICATE, **change), POLICY)  # type: ignore[arg-type]
    assert not decision.approved
    assert decision.code == code
    assert decision.human_can_approve is human_can_approve


def test_enterprise_refund_over_the_auto_limit_needs_a_human() -> None:
    request = replace(
        PRO_DUPLICATE, tier="enterprise", charge_amount=Decimal("1200.00"), amount=Decimal("1200.00")
    )
    decision = evaluate_refund(request, POLICY)
    assert decision.code == "over_auto_limit" and decision.human_can_approve
    assert "$500.00" in decision.reason


def test_limits_and_windows_are_inclusive() -> None:
    at_limit = replace(PRO_DUPLICATE, charge_amount=Decimal("150"), amount=Decimal("100.00"))
    assert evaluate_refund(at_limit, POLICY).approved
    over = replace(at_limit, amount=Decimal("100.01"))
    assert evaluate_refund(over, POLICY).code == "over_auto_limit"

    last_day = replace(PRO_DUPLICATE, charge_created_at=NOW - timedelta(days=30))
    assert evaluate_refund(last_day, POLICY).approved


def test_partial_refund_respects_what_is_left() -> None:
    partly = replace(PRO_DUPLICATE, charge_status="partially_refunded", already_refunded=Decimal("20"))
    assert evaluate_refund(replace(partly, amount=Decimal("9")), POLICY).approved
    assert evaluate_refund(replace(partly, amount=Decimal("10")), POLICY).code == "exceeds_charge"


def test_prompt_injection_amount_is_still_capped() -> None:
    """The model can be talked into *asking*; the engine still says no."""
    injected = replace(PRO_DUPLICATE, charge_amount=Decimal("5000"), amount=Decimal("5000"))
    assert evaluate_refund(injected, POLICY).code == "over_auto_limit"
