"""Load kb/*.md into the database named by DATABASE_URL (production or local).

    uv run python -m scripts.ingest_kb

Safe to re-run: the knowledge-base table is replaced in one transaction.
"""

import asyncio
from pathlib import Path

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.config import get_settings
from app.llm.client import get_openai
from app.rag.chunking import load_kb
from app.rag.embeddings import OpenAIEmbedder
from app.rag.ingest import ingest

KB_DIR = Path(__file__).resolve().parents[3] / "kb"


async def main() -> None:
    url = get_settings().database_url
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    # prepare_threshold=None: Supabase's transaction pooler can't use prepared statements.
    conn = await AsyncConnection.connect(url, row_factory=dict_row, autocommit=True, prepare_threshold=None)
    try:
        report = await ingest(conn, load_kb(KB_DIR), OpenAIEmbedder(get_openai()))
        cur = await conn.execute("select count(*) as n, count(embedding) as embedded from public.kb_chunks")
        row = await cur.fetchone()
    finally:
        await conn.close()
    print(f"loaded {report.chunks} chunks from {report.documents} documents with {report.embedding_model}")
    print(f"database now has {row['n'] if row else '?'} chunks, {row['embedded'] if row else '?'} embedded")


if __name__ == "__main__":
    asyncio.run(main())
