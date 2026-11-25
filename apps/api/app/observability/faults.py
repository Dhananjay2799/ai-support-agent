"""Demo-only failure injection, so recovery can be shown instead of described.

A chat request may ask for one simulated failure (only when DEMO_MODE is on). It
applies to that turn only and goes through the same recovery code as a real one:

- `llm_timeout`: the first model call times out once, then the retry succeeds.
- `rate_limit`: the first model call is rate-limited on every attempt, so the turn
  falls back to the backup model.
- `tool_error`: the first tool call loses its database connection once and is retried.
- `tool_outage`: the account system is down for the whole turn (`get_account` and
  `issue_refund` fail every time); the agent has to handle it gracefully.
"""

from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import get_args

import httpx2 as httpx  # the OpenAI SDK's HTTP client
import openai
import psycopg

from app.contracts.chat import SimulatedFailure

Fault = SimulatedFailure

FAULTS: tuple[Fault, ...] = get_args(Fault)
OUTAGE_TOOLS = frozenset({"get_account", "issue_refund"})

_REQUEST = httpx.Request("POST", "https://api.openai.com/v1/responses")


@dataclass
class FaultPlan:
    fault: Fault
    _used: Counter[str] = field(default_factory=Counter)

    def model_failure(self, model_index: int, attempt: int) -> Exception | None:
        if self.fault == "llm_timeout" and model_index == 0 and self._once("llm"):
            return openai.APITimeoutError(request=_REQUEST)
        if self.fault == "rate_limit" and model_index == 0 and self._used["llm"] < 2:
            self._used["llm"] += 1
            return openai.RateLimitError(
                "Rate limit reached (simulated)", response=httpx.Response(429, request=_REQUEST), body=None
            )
        return None

    def tool_failure(self, tool: str) -> Exception | None:
        if self.fault == "tool_error" and self._once("tool"):
            return psycopg.OperationalError("connection to the account database was lost (simulated)")
        if self.fault == "tool_outage" and tool in OUTAGE_TOOLS:
            return psycopg.OperationalError("the account database is unreachable (simulated outage)")
        return None

    def _once(self, key: str) -> bool:
        self._used[key] += 1
        return self._used[key] == 1


current_faults: ContextVar[FaultPlan | None] = ContextVar("current_faults", default=None)


def model_failure(model_index: int, attempt: int) -> Exception | None:
    plan = current_faults.get()
    return plan.model_failure(model_index, attempt) if plan else None


def tool_failure(tool: str) -> Exception | None:
    plan = current_faults.get()
    return plan.tool_failure(tool) if plan else None
