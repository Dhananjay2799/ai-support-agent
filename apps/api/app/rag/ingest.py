"""Load the knowledge base into `kb_chunks`.

Re-running is safe: the whole table is replaced inside one transaction, so readers
see either the old knowledge base or the new one, never a mix. The KB is small, so
re-embedding everything costs a fraction of a cent.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.rag.chunking import Chunk
from app.rag.embeddings import Embedder, Vector, to_pgvector

_COLUMNS = (
    "doc_id, chunk_index, doc_title, section_path, content, tiers, doc_type, "
    "effective_date, version, token_count, embedding"
)


@dataclass(frozen=True)
class IngestReport:
    documents: int
    chunks: int
    embedding_model: str


def _row(chunk: Chunk, vector: Vector) -> tuple[object, ...]:
    return (
        chunk.doc_id,
        chunk.chunk_index,
        chunk.doc_title,
        chunk.section_path,
        chunk.content,
        list(chunk.tiers),
        chunk.doc_type,
        chunk.effective_date,
        chunk.version,
        chunk.token_count,
        to_pgvector(vector),
    )


async def ingest(conn: AsyncConnection[DictRow], chunks: Sequence[Chunk], embedder: Embedder) -> IngestReport:
    vectors = await embedder.embed([chunk.embedding_text for chunk in chunks])
    async with conn.transaction(), conn.cursor() as cur:
        await cur.execute("delete from public.kb_chunks")
        await cur.executemany(
            f"insert into public.kb_chunks ({_COLUMNS}) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::extensions.vector)",
            [_row(chunk, vector) for chunk, vector in zip(chunks, vectors, strict=True)],
        )
    return IngestReport(
        documents=len({c.doc_id for c in chunks}), chunks=len(chunks), embedding_model=embedder.model
    )


def _sql_literal(value: object) -> str:
    if isinstance(value, list):
        return "array[" + ",".join(_sql_literal(v) for v in value) + "]::text[]"
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def ingest_sql(chunks: Sequence[Chunk], vectors: Sequence[Vector], *, batch_size: int = 15) -> list[str]:
    """The same load as plain SQL statements, for applying through a SQL console or
    tool when no direct connection string is available. First statement deletes."""
    statements = ["delete from public.kb_chunks;"]
    rows = [_row(chunk, vector) for chunk, vector in zip(chunks, vectors, strict=True)]
    for start in range(0, len(rows), batch_size):
        values = []
        for row in rows[start : start + batch_size]:
            *fields, embedding = row
            literals = [_sql_literal(f) for f in fields]
            values.append("(" + ", ".join([*literals, f"'{embedding}'::extensions.vector"]) + ")")
        statements.append(f"insert into public.kb_chunks ({_COLUMNS}) values\n" + ",\n".join(values) + ";")
    return statements
