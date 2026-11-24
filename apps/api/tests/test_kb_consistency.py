"""The knowledge base must state the same numbers the policy engine enforces.

If someone edits policy.yaml without updating kb/ (or the reverse), the agent would
cite one rule and enforce another. These tests make that drift a CI failure.
"""

import re
from pathlib import Path

import pytest
import yaml

from app.policy.config import Policy, Tier, load_policy

KB_DIR = Path(__file__).resolve().parents[3] / "kb"
REQUIRED_FRONTMATTER = {"doc_id", "title", "doc_type", "tiers", "effective_date", "version"}


def _section_table(doc: str, heading: str) -> list[list[str]]:
    """Rows (header excluded) of the first pipe table under `## heading`."""
    text = (KB_DIR / doc).read_text(encoding="utf-8")
    section = text.split(f"## {heading}", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("|")]
    return [[cell.strip() for cell in row.strip("|").split("|")] for row in rows[2:]]


def _by_plan(rows: list[list[str]]) -> dict[Tier, list[str]]:
    plans: dict[str, Tier] = {"Free": "free", "Pro": "pro", "Enterprise": "enterprise"}
    return {plans[row[0]]: row[1:] for row in rows}


@pytest.fixture(scope="module")
def policy() -> Policy:
    return load_policy()


def test_refund_table_matches_policy(policy: Policy) -> None:
    table = _by_plan(_section_table("refunds-and-billing.md", "Refund limits by plan"))
    assert set(table) == set(policy.refunds.tiers)

    for tier, (availability, limit, window) in table.items():
        rule = policy.refunds.tiers[tier]
        assert (availability == "Available") is rule.refunds_allowed, tier
        if rule.refunds_allowed:
            assert limit == f"${rule.auto_approve_limit:,.0f}", tier
            assert window.startswith(f"{rule.window_days} days"), tier
        else:
            assert limit == window == "—", tier


def test_only_active_contracts_are_refund_eligible(policy: Policy) -> None:
    text = (KB_DIR / "refunds-and-billing.md").read_text(encoding="utf-8")
    assert policy.refunds.eligible_contract_statuses == ("active",)
    assert "**past due**" in text and "**canceled**" in text


def test_response_times_match_policy(policy: Policy) -> None:
    table = _by_plan(_section_table("support-and-sla.md", "Response times by plan"))
    for tier, hours in policy.support.first_response_hours.items():
        stated = re.match(r"(\d+) hours", table[tier][1])
        assert stated and int(stated.group(1)) == hours, tier


def test_verification_attempts_match_policy(policy: Policy) -> None:
    text = (KB_DIR / "account-security.md").read_text(encoding="utf-8")
    stated = re.search(r"After (\d+) failed attempts", text)
    assert stated and int(stated.group(1)) == policy.verification.max_failed_attempts


@pytest.mark.parametrize("path", sorted(KB_DIR.glob("*.md")), ids=lambda p: p.name)
def test_every_doc_has_valid_frontmatter(path: Path) -> None:
    if path.name == "README.md":
        pytest.skip("not a knowledge-base document")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "missing frontmatter"
    meta = yaml.safe_load(text.split("---\n", 2)[1])
    assert set(meta) >= REQUIRED_FRONTMATTER, REQUIRED_FRONTMATTER - set(meta)
    assert meta["doc_id"] == path.stem
    assert set(meta["tiers"]) <= {"free", "pro", "enterprise"}

    for declared in re.findall(r"<!-- tiers: ([a-z, ]+) -->", text):
        section_tiers = {tier.strip() for tier in declared.split(",")}
        assert section_tiers <= set(meta["tiers"]), f"section tiers {section_tiers} outside the doc's tiers"
