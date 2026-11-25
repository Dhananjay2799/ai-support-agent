"""Hybrid retrieval: vector similarity + full-text, merged with reciprocal rank fusion.

Vectors catch paraphrases ("money back" ≈ "refund"); full-text catches exact tokens
that embeddings blur, like error codes (`E-409`). RRF merges the two rankings without
having to put cosine scores and ts_rank on the same scale.

The plan filter runs in SQL, so a Pro customer never sees Enterprise-only clauses.
The confidence gate uses the best *cosine* score, because RRF ranks are relative and
say nothing about whether the best match is actually close.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.observability.tracing import clip, span
from app.rag.embeddings import Embedder, to_pgvector

RRF_K = 60  # standard constant from the original RRF paper
CANDIDATES = 20  # per retriever, before fusion
_ERROR_CODE = re.compile(r"\b([A-Za-z])-?(\d{3})\b")

# Turn the question into an OR of its lexemes: plainto_tsquery ANDs every word,
# which makes natural questions ("what's the refund policy on Pro?") match nothing.
# Error codes are folded ("E-409" -> "E409") exactly as the `fts` column does.
_SEARCH_SQL = r"""
with q as (
  select %(embedding)s::extensions.vector as embedding,
         nullif(replace(plainto_tsquery(
           'english', regexp_replace(%(question)s, '\m([A-Za-z])-(\d{3})\M', '\1\2', 'g')
         )::text, '&', '|'), '')::tsquery as lexemes,
         to_tsquery('english', %(codes)s) as codes
),
semantic as (
  select c.id,
         1 - (c.embedding <=> q.embedding) as cosine,
         row_number() over (order by c.embedding <=> q.embedding) as rank
  from public.kb_chunks c, q
  where %(tier)s::text is null or c.tiers @> array[%(tier)s::text]
  order by c.embedding <=> q.embedding
  limit %(candidates)s
),
lexical as (
  select c.id, row_number() over (
           order by coalesce(c.fts @@ q.codes, false) desc, ts_rank_cd(c.fts, q.lexemes) desc
         ) as rank
  from public.kb_chunks c, q
  where q.lexemes is not null and c.fts @@ q.lexemes
    and (%(tier)s::text is null or c.tiers @> array[%(tier)s::text])
  -- An exact error-code match outranks everything else: Postgres has no IDF, so a
  -- rare token like 'e409' otherwise counts no more than a common word like 'sync'.
  order by coalesce(c.fts @@ q.codes, false) desc, ts_rank_cd(c.fts, q.lexemes) desc
  limit %(candidates)s
)
select c.id, c.doc_id, c.chunk_index, c.doc_title, c.section_path, c.content, c.tiers,
       1 - (c.embedding <=> q.embedding) as cosine,
       s.rank as semantic_rank,
       l.rank as lexical_rank,
       coalesce(1.0 / (%(rrf_k)s + s.rank), 0) + coalesce(1.0 / (%(rrf_k)s + l.rank), 0) as rrf_score,
       coalesce(c.fts @@ q.codes, false) as code_match,
       -- Best semantic match in the whole (plan-filtered) KB, computed before the
       -- LIMIT: the confidence gate must not depend on how keyword fusion reorders.
       max(s.cosine) over () as best_cosine
from semantic s
full outer join lexical l on l.id = s.id
join public.kb_chunks c on c.id = coalesce(s.id, l.id)
cross join q
-- A section about the exact error code the customer quoted goes first; otherwise RRF.
order by code_match desc, rrf_score desc, cosine desc
limit %(top_n)s
"""


def error_code_query(question: str) -> str | None:
    """'Seeing E-409 and e413' -> 'e409 | e413' (a tsquery), or None if no codes."""
    codes = sorted({f"{letter}{digits}".lower() for letter, digits in _ERROR_CODE.findall(question)})
    return " | ".join(codes) or None


@dataclass(frozen=True)
class RetrievedChunk:
    id: int
    doc_id: str
    chunk_index: int
    doc_title: str
    section_path: str
    content: str
    tiers: tuple[str, ...]
    cosine: float
    semantic_rank: int | None
    lexical_rank: int | None
    rrf_score: float

    @property
    def citation_id(self) -> str:
        return f"{self.doc_id}#{self.chunk_index}"


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    tier: str | None
    chunks: tuple[RetrievedChunk, ...]
    best_cosine: float | None = None

    @property
    def top_cosine(self) -> float:
        """The confidence gate's input: the best semantic match in the eligible
        knowledge base (falls back to the returned chunks when not provided)."""
        if self.best_cosine is not None:
            return self.best_cosine
        return max((c.cosine for c in self.chunks), default=0.0)

    def doc_ids(self, k: int | None = None) -> list[str]:
        return [c.doc_id for c in self.chunks[:k]]


class HybridRetriever:
    def __init__(self, embedder: Embedder, *, top_n: int = 5) -> None:
        self._embedder = embedder
        self._top_n = top_n

    async def search(
        self, conn: AsyncConnection[DictRow], question: str, *, tier: str | None
    ) -> RetrievalResult:
        with span("hybrid search", "retrieval", query=clip(question, 500), tier=tier) as trace:
            [embedding] = await self._embedder.embed([question])
            result = await self.search_with_embedding(conn, question, embedding, tier=tier)
            trace.set(
                best_cosine=result.best_cosine,
                chunks=[
                    {
                        "id": c.citation_id,
                        "section": c.section_path,
                        "cosine": round(c.cosine, 3),
                        "semantic_rank": c.semantic_rank,
                        "lexical_rank": c.lexical_rank,
                        "rrf": round(c.rrf_score, 4),
                    }
                    for c in result.chunks[:6]
                ],
            )
            return result

    async def search_with_embedding(
        self,
        conn: AsyncConnection[DictRow],
        question: str,
        embedding: Sequence[float],
        *,
        tier: str | None,
    ) -> RetrievalResult:
        params = {
            "embedding": to_pgvector(embedding),
            "question": question,
            "codes": error_code_query(question),
            "tier": tier,
            "candidates": CANDIDATES,
            "rrf_k": RRF_K,
            "top_n": self._top_n,
        }
        async with conn.cursor() as cur:
            await cur.execute(_SEARCH_SQL, params)
            rows = await cur.fetchall()
        best = float(rows[0]["best_cosine"]) if rows and rows[0]["best_cosine"] is not None else None
        chunks = tuple(
            RetrievedChunk(
                id=row["id"],
                doc_id=row["doc_id"],
                chunk_index=row["chunk_index"],
                doc_title=row["doc_title"],
                section_path=row["section_path"],
                content=row["content"],
                tiers=tuple(row["tiers"]),
                cosine=float(row["cosine"]),
                semantic_rank=row["semantic_rank"],
                lexical_rank=row["lexical_rank"],
                rrf_score=float(row["rrf_score"]),
            )
            for row in rows
        )
        return RetrievalResult(query=question, tier=tier, chunks=chunks, best_cosine=best)
