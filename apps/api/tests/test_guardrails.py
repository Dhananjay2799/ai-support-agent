from collections.abc import Sequence

import pytest

from app.rag.guardrails import SupportVerdict, Thresholds, assess, load_thresholds
from app.rag.retriever import RetrievalResult, RetrievedChunk

THRESHOLDS = Thresholds(low=0.3, high=0.6)


def _chunk(cosine: float, doc_id: str = "refunds-and-billing", index: int = 0) -> RetrievedChunk:
    return RetrievedChunk(
        id=index,
        doc_id=doc_id,
        chunk_index=index,
        doc_title="Refunds and billing",
        section_path="Refunds and billing > How refunds are paid",
        content="Refunds appear within 5 to 10 business days.",
        tiers=("pro",),
        cosine=cosine,
        semantic_rank=1,
        lexical_rank=None,
        rrf_score=0.016,
    )


def _result(*cosines: float) -> RetrievalResult:
    return RetrievalResult(
        query="q", tier="pro", chunks=tuple(_chunk(c, index=i) for i, c in enumerate(cosines))
    )


class FakeJudge:
    def __init__(self, verdict: SupportVerdict) -> None:
        self.verdict = verdict
        self.calls = 0

    async def judge(
        self, question: str, chunks: Sequence[RetrievedChunk], *, tier: str | None = None
    ) -> SupportVerdict:
        self.calls += 1
        return self.verdict


YES = SupportVerdict(answerable=True, supporting_ids=["refunds-and-billing#0"], missing=None)
NO = SupportVerdict(answerable=False, supporting_ids=[], missing="nothing about PayPal")


async def test_low_similarity_escalates_without_calling_the_judge() -> None:
    judge = FakeJudge(YES)
    result = await assess("weather?", _result(0.1, 0.05), judge=judge, thresholds=THRESHOLDS)
    assert result.decision == "escalate" and result.band == "low"
    assert judge.calls == 0


async def test_even_high_similarity_is_checked_by_the_judge() -> None:
    """A keyword-dense query can score high without the docs answering it."""
    judge = FakeJudge(NO)
    result = await assess(
        "PayPal payment method for Pro plan", _result(0.8), judge=judge, thresholds=THRESHOLDS
    )
    assert result.decision == "escalate" and judge.calls == 1

    confirmed = await assess("refund timing?", _result(0.8), judge=FakeJudge(YES), thresholds=THRESHOLDS)
    assert confirmed.decision == "answer" and confirmed.confidence == 1.0


async def test_middle_band_follows_the_judge() -> None:
    answered = await assess("q", _result(0.45), judge=FakeJudge(YES), thresholds=THRESHOLDS)
    assert answered.decision == "answer" and answered.judged
    assert answered.supporting_ids == ("refunds-and-billing#0",)

    escalated = await assess("q", _result(0.45), judge=FakeJudge(NO), thresholds=THRESHOLDS)
    assert escalated.decision == "escalate" and escalated.reason == "nothing about PayPal"
    assert escalated.confidence < 0.5


async def test_judge_cannot_vouch_for_passages_that_were_not_retrieved() -> None:
    invented = SupportVerdict(answerable=True, supporting_ids=["made-up-doc#3"], missing=None)
    result = await assess("q", _result(0.45), judge=FakeJudge(invented), thresholds=THRESHOLDS)
    assert result.decision == "escalate"


async def test_empty_retrieval_escalates() -> None:
    empty = RetrievalResult(query="q", tier=None, chunks=())
    result = await assess("q", empty, judge=FakeJudge(YES), thresholds=THRESHOLDS)
    assert result.decision == "escalate"


@pytest.mark.parametrize(
    ("cosine", "band"), [(0.29, "low"), (0.3, "middle"), (0.59, "middle"), (0.6, "high")]
)
def test_band_edges(cosine: float, band: str) -> None:
    assert THRESHOLDS.band(cosine) == band


def test_committed_thresholds_are_ordered() -> None:
    thresholds = load_thresholds()
    assert thresholds.low < thresholds.high
