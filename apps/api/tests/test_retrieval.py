from pathlib import Path

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.rag.chunking import load_kb
from app.rag.embeddings import HashingEmbedder
from app.rag.ingest import ingest
from app.rag.retriever import HybridRetriever

KB_DIR = Path(__file__).resolve().parents[3] / "kb"


async def _loaded(db: AsyncConnection[DictRow]) -> HybridRetriever:
    embedder = HashingEmbedder()
    report = await ingest(db, load_kb(KB_DIR), embedder)
    assert report.chunks > 30
    return HybridRetriever(embedder)


async def test_ingest_is_idempotent(db: AsyncConnection[DictRow]) -> None:
    await _loaded(db)
    await _loaded(db)
    count = await (await db.execute("select count(*) as n from public.kb_chunks")).fetchone()
    assert count is not None and count["n"] == len(load_kb(KB_DIR))


async def test_exact_error_code_is_found(db: AsyncConnection[DictRow]) -> None:
    retriever = await _loaded(db)
    result = await retriever.search(db, "my sync shows E-409", tier="pro")
    match = next(c for c in result.chunks if c.section_path.endswith("E-409: Conflicting edits"))
    # An exact code match must win the full-text ranking outright. (The fused order
    # also depends on embedding quality; the test embedder is deliberately crude.)
    assert match.lexical_rank == 1
    assert match in result.chunks[:3]


async def test_tier_filter_hides_other_plans_sections(db: AsyncConnection[DictRow]) -> None:
    retriever = await _loaded(db)
    question = "enterprise refunds over the auto-approve limit account manager"

    pro = await retriever.search(db, question, tier="pro")
    assert all("pro" in c.tiers for c in pro.chunks)
    assert not any(c.section_path.endswith("Enterprise refunds and credits") for c in pro.chunks)

    enterprise = await retriever.search(db, question, tier="enterprise")
    assert enterprise.chunks[0].section_path.endswith("Enterprise refunds and credits")


async def test_unverified_customers_search_every_plan(db: AsyncConnection[DictRow]) -> None:
    retriever = await _loaded(db)
    result = await retriever.search(db, "reset two-factor for a member admin console", tier=None)
    assert "account-security" in result.doc_ids(3)


async def test_scores_are_well_formed(db: AsyncConnection[DictRow]) -> None:
    retriever = await _loaded(db)
    result = await retriever.search(db, "how long do refunds take", tier="free")
    assert 0 < len(result.chunks) <= 5
    assert all(-1.0 <= c.cosine <= 1.0 for c in result.chunks)
    assert [c.rrf_score for c in result.chunks] == sorted((c.rrf_score for c in result.chunks), reverse=True)
    assert result.top_cosine == max(c.cosine for c in result.chunks)


def test_error_code_query() -> None:
    from app.rag.retriever import error_code_query

    assert error_code_query("Seeing E-409 and e413 again") == "e409 | e413"
    assert error_code_query("my files won't sync") is None


async def test_confidence_uses_best_semantic_match_not_fused_order(db: AsyncConnection[DictRow]) -> None:
    retriever = await _loaded(db)
    result = await retriever.search(db, "how long do refunds take to appear", tier="pro")
    [embedding] = await HashingEmbedder().embed(["how long do refunds take to appear"])
    from app.rag.embeddings import to_pgvector

    cur = await db.execute(
        "select max(1 - (embedding <=> %s::extensions.vector)) as best from public.kb_chunks "
        "where tiers @> array['pro']",
        (to_pgvector(embedding),),
    )
    row = await cur.fetchone()
    assert row is not None
    assert abs(result.top_cosine - row["best"]) < 1e-9
