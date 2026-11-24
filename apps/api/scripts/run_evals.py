"""Retrieval and guardrail evaluation over evals/questions.jsonl.

    EVAL_DATABASE_URL=postgresql://... uv run python -m scripts.run_evals [--calibrate]

1. Loads the knowledge base into the eval database with real embeddings.
2. Runs hybrid retrieval and the support judge once per question.
3. Sweeps (low, high) thresholds offline over those results.
   --calibrate writes the best pair to app/rag/thresholds.yaml.
4. Compares baselines: threshold-only gating and fixed-size chunking.
5. Writes evals/results.md and evals/results.json.

The eval database must not be production: the knowledge-base table is replaced.
"""

import argparse
import asyncio
import json
import os
import statistics
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.config import get_settings
from app.llm.client import get_openai
from app.rag.chunking import Chunk, load_kb
from app.rag.embeddings import OpenAIEmbedder, Vector, cosine
from app.rag.guardrails import (
    THRESHOLDS_PATH,
    OpenAISupportJudge,
    SupportVerdict,
    Thresholds,
    decide,
    load_thresholds,
)
from app.rag.ingest import ingest
from app.rag.retriever import HybridRetriever, RetrievalResult

REPO_ROOT = Path(__file__).resolve().parents[3]
QUESTIONS = REPO_ROOT / "evals" / "questions.jsonl"
RESULTS_MD = REPO_ROOT / "evals" / "results.md"
RESULTS_JSON = REPO_ROOT / "evals" / "results.json"
KB_DIR = REPO_ROOT / "kb"

Slice = Literal["in_scope", "tier_specific", "out_of_scope", "unanswerable_in_domain"]


@dataclass(frozen=True)
class Question:
    id: str
    slice: Slice
    tier: str | None
    question: str
    expected: Literal["answer", "escalate"]
    expected_docs: tuple[str, ...]


@dataclass(frozen=True)
class Observation:
    question: Question
    retrieval: RetrievalResult
    verdict: SupportVerdict

    @property
    def hit_rank(self) -> int | None:
        for rank, doc_id in enumerate(self.retrieval.doc_ids(), start=1):
            if doc_id in self.question.expected_docs:
                return rank
        return None


@dataclass(frozen=True)
class Score:
    thresholds: Thresholds
    unsafe_answers: int  # should have escalated, answered instead
    correct_answers: int  # should answer, did
    missed_answers: int  # should answer, escalated
    correct_escalations: int
    judge_calls: int

    @property
    def escalation_precision(self) -> float:
        escalated = self.correct_escalations + self.missed_answers
        return self.correct_escalations / escalated if escalated else 1.0

    @property
    def escalation_recall(self) -> float:
        should = self.correct_escalations + self.unsafe_answers
        return self.correct_escalations / should if should else 1.0

    @property
    def answer_rate(self) -> float:
        should = self.correct_answers + self.missed_answers
        return self.correct_answers / should if should else 1.0


def load_questions() -> list[Question]:
    rows = [json.loads(line) for line in QUESTIONS.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [Question(**{**row, "expected_docs": tuple(row["expected_docs"])}) for row in rows]


def score(observations: list[Observation], thresholds: Thresholds) -> Score:
    counts: Counter[str] = Counter()
    for obs in observations:
        band = thresholds.band(obs.retrieval.top_cosine)
        counts["judge"] += band == "middle"
        decision = decide(obs.retrieval, thresholds, obs.verdict).decision
        expected = obs.question.expected
        key = {
            ("escalate", "answer"): "unsafe",
            ("answer", "answer"): "correct_answer",
            ("answer", "escalate"): "missed",
            ("escalate", "escalate"): "correct_escalation",
        }[(expected, decision)]
        counts[key] += 1
    return Score(
        thresholds=thresholds,
        unsafe_answers=counts["unsafe"],
        correct_answers=counts["correct_answer"],
        missed_answers=counts["missed"],
        correct_escalations=counts["correct_escalation"],
        judge_calls=counts["judge"],
    )


def sweep(observations: list[Observation]) -> tuple[Score, list[Score]]:
    """Pick thresholds lexicographically: fewest unsafe answers, then most correct
    answers, then fewest judge calls. Among ties, take the middle candidate so the
    pair isn't pinned against a single question's score."""
    grid = [round(x * 0.01, 2) for x in range(5, 91)]
    scores = [score(observations, Thresholds(low=lo, high=hi)) for lo in grid for hi in grid if hi > lo]
    best_key = min((s.unsafe_answers, -s.correct_answers, s.judge_calls) for s in scores)
    best = [s for s in scores if (s.unsafe_answers, -s.correct_answers, s.judge_calls) == best_key]
    best.sort(key=lambda s: (s.thresholds.low, s.thresholds.high))
    return best[len(best) // 2], scores


def threshold_only_baseline(scores: list[Score], observations: list[Observation]) -> Score:
    """Best single cut-off with no judge: answer iff top cosine >= t."""
    candidates = [s for s in scores if s.thresholds.high - s.thresholds.low <= 0.011]
    rescored = []
    for s in candidates:
        t = s.thresholds.high
        unsafe = sum(o.question.expected == "escalate" and o.retrieval.top_cosine >= t for o in observations)
        correct = sum(o.question.expected == "answer" and o.retrieval.top_cosine >= t for o in observations)
        total_answer = sum(o.question.expected == "answer" for o in observations)
        total_escalate = len(observations) - total_answer
        rescored.append(
            Score(
                Thresholds(low=t, high=t), unsafe, correct, total_answer - correct, total_escalate - unsafe, 0
            )
        )
    return min(rescored, key=lambda s: (s.unsafe_answers + s.missed_answers, s.unsafe_answers))


def vector_hit_at(k: int, chunks: list[Chunk], vectors: list[Vector], q: Question, q_vec: Vector) -> bool:
    eligible = [(c, v) for c, v in zip(chunks, vectors, strict=True) if q.tier is None or q.tier in c.tiers]
    ranked = sorted(eligible, key=lambda cv: cosine(q_vec, cv[1]), reverse=True)[:k]
    return any(c.doc_id in q.expected_docs for c, _ in ranked)


async def chunking_comparison(
    embedder: OpenAIEmbedder, questions: list[Question], q_vectors: list[Vector]
) -> dict[str, dict[str, float]]:
    answerable = [(q, v) for q, v in zip(questions, q_vectors, strict=True) if q.expected == "answer"]
    report = {}
    for name, chunks in {
        "heading-aware": load_kb(KB_DIR),
        "fixed 512-token": load_kb(KB_DIR, fixed=True),
    }.items():
        vectors = await embedder.embed([c.embedding_text for c in chunks])
        hits1 = sum(vector_hit_at(1, chunks, vectors, q, v) for q, v in answerable)
        hits3 = sum(vector_hit_at(3, chunks, vectors, q, v) for q, v in answerable)
        report[name] = {
            "chunks": len(chunks),
            "hit@1": hits1 / len(answerable),
            "hit@3": hits3 / len(answerable),
            "avg_tokens": statistics.mean(c.token_count for c in chunks),
        }
    return report


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def write_reports(
    observations: list[Observation],
    chosen: Score,
    baseline: Score,
    chunking: dict[str, dict[str, float]],
    meta: dict[str, object],
) -> None:
    answerable = [o for o in observations if o.question.expected == "answer"]
    ranks = [o.hit_rank for o in answerable]
    hit1 = sum(r == 1 for r in ranks) / len(answerable)
    hit3 = sum(r is not None and r <= 3 for r in ranks) / len(answerable)
    mrr = sum(1 / r for r in ranks if r) / len(answerable)
    t = chosen.thresholds

    lines = [
        "# Retrieval and guardrail evaluation",
        "",
        f"Generated {meta['generated']} by `apps/api/scripts/run_evals.py` over {len(observations)} labeled "
        f"questions (`evals/questions.jsonl`). Embeddings: `{meta['embedding_model']}`; "
        f"judge: `{meta['judge_model']}`; knowledge base: {meta['kb_chunks']} chunks.",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Retrieval hit@1 (answerable questions) | {_pct(hit1)} |",
        f"| Retrieval hit@3 (answerable questions) | {_pct(hit3)} |",
        f"| Retrieval MRR | {mrr:.2f} |",
        f"| Calibrated thresholds | low = {t.low:.2f}, high = {t.high:.2f} |",
        f"| Unsafe answers (should have escalated) | {chosen.unsafe_answers} |",
        f"| Escalation precision | {_pct(chosen.escalation_precision)} |",
        f"| Escalation recall | {_pct(chosen.escalation_recall)} |",
        f"| Answerable questions answered | {_pct(chosen.answer_rate)} |",
        f"| Questions that needed the judge | {chosen.judge_calls} of {len(observations)} |",
        "",
        "## Why two layers",
        "",
        "Best single similarity cut-off without the judge "
        f"(answer if top cosine >= {baseline.thresholds.high:.2f}): "
        f"{baseline.unsafe_answers} unsafe answers, "
        f"{baseline.missed_answers} answerable questions escalated. "
        f"With the judge on the middle band: {chosen.unsafe_answers} unsafe, {chosen.missed_answers} missed.",
        "",
        "| Slice | Questions | Top cosine min | median | max | Correct decisions |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for slice_name in ("in_scope", "tier_specific", "out_of_scope", "unanswerable_in_domain"):
        group = [o for o in observations if o.question.slice == slice_name]
        scores = [o.retrieval.top_cosine for o in group]
        correct = sum(decide(o.retrieval, t, o.verdict).decision == o.question.expected for o in group)
        lines.append(
            f"| {slice_name} | {len(group)} | {min(scores):.3f} | {statistics.median(scores):.3f} "
            f"| {max(scores):.3f} | {correct}/{len(group)} |"
        )
    lines += [
        "",
        "## Chunking comparison (vector-only retrieval, answerable questions)",
        "",
        "| Strategy | Chunks | Avg tokens | hit@1 | hit@3 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, row in chunking.items():
        lines.append(
            f"| {name} | {row['chunks']:.0f} | {row['avg_tokens']:.0f} "
            f"| {_pct(row['hit@1'])} | {_pct(row['hit@3'])} |"
        )
    lines += [
        "",
        "## Every question",
        "",
        "| Id | Slice | Tier | Expected | Decision | Band | Top cosine | Hit rank | Top section |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for o in observations:
        a = decide(o.retrieval, t, o.verdict)
        mark = "" if a.decision == o.question.expected else " ✗"
        top = o.retrieval.chunks[0].section_path if o.retrieval.chunks else "—"
        lines.append(
            f"| {o.question.id} | {o.question.slice} | {o.question.tier or 'any'} | {o.question.expected} "
            f"| {a.decision}{mark} | {a.band} | {o.retrieval.top_cosine:.3f} | {o.hit_rank or '—'} | {top} |"
        )
    lines += [
        "",
        "Caveat: thresholds are tuned on these same 40 questions, so treat the numbers as a sanity "
        "check on the design, not a held-out estimate.",
        "",
    ]
    RESULTS_MD.write_text("\n".join(lines), encoding="utf-8")

    RESULTS_JSON.write_text(
        json.dumps(
            {
                **meta,
                "thresholds": t.model_dump(),
                "metrics": {
                    "hit@1": hit1,
                    "hit@3": hit3,
                    "mrr": mrr,
                    **asdict(chosen) | {"thresholds": None},
                },
                "chunking": chunking,
                "questions": [
                    {
                        "id": o.question.id,
                        "top_cosine": round(o.retrieval.top_cosine, 4),
                        "hit_rank": o.hit_rank,
                        "verdict": o.verdict.model_dump(),
                        "top_docs": o.retrieval.doc_ids(5),
                    }
                    for o in observations
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


async def main(calibrate: bool) -> None:
    url = os.environ.get("EVAL_DATABASE_URL")
    if not url:
        raise SystemExit("Set EVAL_DATABASE_URL to a disposable database (the KB table gets replaced).")
    settings = get_settings()
    client = get_openai()
    embedder = OpenAIEmbedder(client)
    judge = OpenAISupportJudge(client, settings.judge_model)
    questions = load_questions()

    conn = await AsyncConnection.connect(url, row_factory=dict_row, autocommit=True)
    try:
        report = await ingest(conn, load_kb(KB_DIR), embedder)
        q_vectors = await embedder.embed([q.question for q in questions])
        retriever = HybridRetriever(embedder)
        retrievals = [
            await retriever.search_with_embedding(conn, q.question, v, tier=q.tier)
            for q, v in zip(questions, q_vectors, strict=True)
        ]
    finally:
        await conn.close()

    limit = asyncio.Semaphore(8)

    async def judged(q: Question, r: RetrievalResult) -> SupportVerdict:
        async with limit:
            return await judge.judge(q.question, r.chunks)

    verdicts = await asyncio.gather(*(judged(q, r) for q, r in zip(questions, retrievals, strict=True)))
    observations = [Observation(q, r, v) for q, r, v in zip(questions, retrievals, verdicts, strict=True)]

    chosen, all_scores = sweep(observations)
    if not calibrate:
        chosen = score(observations, load_thresholds())
    baseline = threshold_only_baseline(all_scores, observations)
    chunking = await chunking_comparison(embedder, questions, q_vectors)
    meta: dict[str, object] = {
        "generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "embedding_model": embedder.model,
        "judge_model": judge.model,
        "kb_chunks": report.chunks,
    }
    write_reports(observations, chosen, baseline, chunking, meta)

    if calibrate:
        t = chosen.thresholds
        THRESHOLDS_PATH.write_text(
            "# Calibrated by scripts/run_evals.py --calibrate on "
            f"{meta['generated']}; see evals/results.md.\n"
            f"low: {t.low:.2f}\nhigh: {t.high:.2f}\n",
            encoding="utf-8",
        )
    print(RESULTS_MD.read_text(encoding="utf-8").split("## Why two layers")[0])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--calibrate", action="store_true", help="write the best thresholds to thresholds.yaml"
    )
    asyncio.run(main(parser.parse_args().calibrate))
