"""Shared fixtures. Database tests run against a real Postgres with pgvector.

Set TEST_DATABASE_URL to a disposable database that has had
supabase/local/bootstrap.sql and supabase/migrations/*.sql applied (CI does this).
Without it, database tests are skipped.
"""

import os
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.agent.tools import ToolEnv
from app.policy.config import load_policy
from app.rag.chunking import load_kb
from app.rag.embeddings import HashingEmbedder
from app.rag.guardrails import SupportVerdict, Thresholds
from app.rag.ingest import ingest
from app.rag.retriever import HybridRetriever, RetrievedChunk

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
KB_DIR = Path(__file__).resolve().parents[3] / "kb"


class YesJudge:
    async def judge(
        self, question: str, chunks: Sequence[RetrievedChunk], *, tier: str | None = None
    ) -> SupportVerdict:
        return SupportVerdict(answerable=True, supporting_ids=[chunks[0].citation_id], missing=None)


@pytest.fixture
async def env() -> AsyncIterator[ToolEnv]:
    """Tools wired to the test database: fresh demo data, KB loaded with the
    offline test embedder, and a judge that always says yes."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        TEST_DATABASE_URL,
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None},
        open=False,
    )
    await pool.open()
    embedder = HashingEmbedder()
    async with pool.connection() as conn:
        await conn.execute("select public.reset_demo_data()")
        await ingest(conn, load_kb(KB_DIR), embedder)
    yield ToolEnv(
        pool=pool,
        retriever=HybridRetriever(embedder),
        judge=YesJudge(),
        thresholds=Thresholds(low=-1.0, high=1.0),  # everything goes to the (fake) judge
        policy=load_policy(),
        demo_mode=True,
    )
    await pool.close()


@pytest.fixture
async def db() -> AsyncIterator[AsyncConnection[DictRow]]:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    conn = await AsyncConnection.connect(TEST_DATABASE_URL, row_factory=dict_row, autocommit=True)
    try:
        yield conn
    finally:
        await conn.close()
