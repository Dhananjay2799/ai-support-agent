"""Confidence guardrails: decide whether the knowledge base can answer a question.

Two layers, cheapest first:

1. Similarity gate on the best cosine score from retrieval.
   - below `low`: nothing relevant was found -> escalate, no LLM call
   - at or above `high`: a clearly relevant passage was found -> answer
   - in between: ask the judge
2. Support judge (an LLM with structured output) for the middle band: does any
   retrieved passage explicitly contain the answer? Its cited ids are checked
   against what was actually retrieved, so it can't vouch for passages it invented.

Thresholds come from `thresholds.yaml`, written by `scripts/run_evals.py --calibrate`
from the labeled eval set (see evals/results.md), not from intuition.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, Protocol

import yaml
from openai import AsyncOpenAI, Omit, omit
from openai.types.shared_params import Reasoning
from pydantic import BaseModel, ConfigDict, Field

from app.rag.retriever import RetrievalResult, RetrievedChunk

THRESHOLDS_PATH = Path(__file__).with_name("thresholds.yaml")

GateBand = Literal["high", "middle", "low"]
Decision = Literal["answer", "escalate"]


class Thresholds(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    low: float = Field(ge=-1, le=1)
    high: float = Field(ge=-1, le=1)

    def band(self, top_cosine: float) -> GateBand:
        if top_cosine < self.low:
            return "low"
        return "high" if top_cosine >= self.high else "middle"


@lru_cache
def load_thresholds(path: Path = THRESHOLDS_PATH) -> Thresholds:
    return Thresholds.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


class SupportVerdict(BaseModel):
    """Structured output from the support judge."""

    answerable: bool = Field(description="True only if the passages explicitly contain the answer")
    supporting_ids: list[str] = Field(description="Ids of the passages that contain the answer")
    missing: str | None = Field(description="What the passages don't cover, if anything")


class SupportJudge(Protocol):
    async def judge(self, question: str, chunks: Sequence[RetrievedChunk]) -> SupportVerdict: ...


JUDGE_INSTRUCTIONS = """\
You check whether support documentation answers a customer's question.

You get the question and numbered passages from Acme Cloud's knowledge base.
Decide whether the passages EXPLICITLY contain the information needed to answer it.

Rules:
- Use only the passages. Never use outside knowledge, even if you know the answer.
- A passage on the same topic that doesn't state the specific answer is NOT enough.
  Example: a passage about payment methods in general doesn't answer "Can I pay with PayPal?".
- Questions unrelated to Acme Cloud are never answerable.
- `supporting_ids` lists only the passage ids that contain the answer; empty if not answerable.
"""


def format_passages(chunks: Sequence[RetrievedChunk]) -> str:
    return "\n\n".join(f"[{c.citation_id}] {c.section_path}\n{c.content}" for c in chunks)


class OpenAISupportJudge:
    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        reasoning_effort: Literal["none", "low", "medium", "high"] | None = None,
    ) -> None:
        self._client = client
        self.model = model
        self._reasoning: Reasoning | Omit = {"effort": reasoning_effort} if reasoning_effort else omit

    async def judge(self, question: str, chunks: Sequence[RetrievedChunk]) -> SupportVerdict:
        response = await self._client.responses.parse(
            model=self.model,
            instructions=JUDGE_INSTRUCTIONS,
            input=f"Question: {question}\n\nPassages:\n\n{format_passages(chunks)}",
            text_format=SupportVerdict,
            reasoning=self._reasoning,
        )
        verdict = response.output_parsed
        if verdict is None:
            raise RuntimeError("support judge returned no structured output")
        return verdict


@dataclass(frozen=True)
class Assessment:
    decision: Decision
    band: GateBand
    top_cosine: float
    confidence: float  # 0..1, for the UI badge
    reason: str
    supporting_ids: tuple[str, ...] = ()
    judged: bool = False


def _confidence(top_cosine: float, thresholds: Thresholds) -> float:
    span = max(thresholds.high - thresholds.low, 1e-6)
    return round(min(max((top_cosine - thresholds.low) / span, 0.0), 1.0), 3)


def needs_judge(retrieval: RetrievalResult, thresholds: Thresholds) -> bool:
    return bool(retrieval.chunks) and thresholds.band(retrieval.top_cosine) == "middle"


def decide(retrieval: RetrievalResult, thresholds: Thresholds, verdict: SupportVerdict | None) -> Assessment:
    """Pure decision from retrieval scores and (for the middle band) a judge verdict.
    Shared by the live path and the eval's offline threshold sweep."""
    top = retrieval.top_cosine
    band = thresholds.band(top)
    confidence = _confidence(top, thresholds)

    if band == "low" or not retrieval.chunks:
        return Assessment("escalate", band, top, confidence, "no relevant documentation found")
    if band == "high":
        ids = tuple(c.citation_id for c in retrieval.chunks[:3])
        return Assessment("answer", band, top, confidence, "closely matching documentation", ids)

    if verdict is None:
        raise ValueError("middle-band decisions need a judge verdict")
    retrieved = {c.citation_id for c in retrieval.chunks}
    supported = tuple(i for i in verdict.supporting_ids if i in retrieved)
    if verdict.answerable and supported:
        return Assessment("answer", band, top, confidence, "judge confirmed support", supported, judged=True)
    reason = verdict.missing or "documentation doesn't contain the answer"
    return Assessment("escalate", band, top, min(confidence, 0.49), reason, judged=True)


async def assess(
    question: str,
    retrieval: RetrievalResult,
    *,
    judge: SupportJudge,
    thresholds: Thresholds,
) -> Assessment:
    verdict = await judge.judge(question, retrieval.chunks) if needs_judge(retrieval, thresholds) else None
    return decide(retrieval, thresholds, verdict)
