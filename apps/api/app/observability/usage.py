"""Per-turn token and cost accounting.

Every model call (agent, judge, embeddings) reports into the meter of the turn it
runs in, so a turn's cost includes everything it triggered. The meter travels in a
ContextVar; LangGraph copies the context into node tasks, and they all share the
same meter object.
"""

from contextvars import ContextVar
from dataclasses import dataclass, field

from app.contracts import Usage

# USD per 1M tokens: (input, cached input, output). Source: OpenAI pricing page,
# November 2026. Unknown models are counted but not priced.
PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-6-sol": (2.00, 0.20, 10.00),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-5.4-nano": (0.20, 0.02, 1.25),
    "text-embedding-3-small": (0.02, 0.02, 0.0),
}


@dataclass
class UsageMeter:
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    calls: list[tuple[str, int, int, int]] = field(default_factory=list)

    def add(self, model: str, *, input_tokens: int, output_tokens: int = 0, cached_tokens: int = 0) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cached_tokens += cached_tokens
        self.calls.append((model, input_tokens, cached_tokens, output_tokens))
        price = PRICES.get(model)
        if price:
            fresh = max(input_tokens - cached_tokens, 0)
            self.cost_usd += (fresh * price[0] + cached_tokens * price[1] + output_tokens * price[2]) / 1e6

    def usage(self) -> Usage:
        return Usage(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cached_tokens=self.cached_tokens,
        )


current_meter: ContextVar[UsageMeter | None] = ContextVar("current_meter", default=None)


def record(model: str, *, input_tokens: int, output_tokens: int = 0, cached_tokens: int = 0) -> None:
    meter = current_meter.get()
    if meter is not None:
        meter.add(model, input_tokens=input_tokens, output_tokens=output_tokens, cached_tokens=cached_tokens)
