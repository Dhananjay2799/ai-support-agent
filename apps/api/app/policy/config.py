from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

Tier = Literal["free", "pro", "enterprise"]
ContractStatus = Literal["active", "past_due", "canceled"]

POLICY_PATH = Path(__file__).with_name("policy.yaml")


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TierRefundRule(_Frozen):
    refunds_allowed: bool
    auto_approve_limit: float = Field(ge=0, description="Largest refund the agent may issue without a human")
    window_days: int = Field(ge=0, description="Days after the charge during which a refund can be requested")


class RefundPolicy(_Frozen):
    eligible_contract_statuses: tuple[ContractStatus, ...]
    tiers: dict[Tier, TierRefundRule]


class SupportPolicy(_Frozen):
    first_response_hours: dict[Tier, int]


class VerificationPolicy(_Frozen):
    max_failed_attempts: int = Field(ge=1)


class Policy(_Frozen):
    currency: str
    refunds: RefundPolicy
    support: SupportPolicy
    verification: VerificationPolicy


@lru_cache
def load_policy(path: Path = POLICY_PATH) -> Policy:
    return Policy.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
