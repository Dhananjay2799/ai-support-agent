"""Operator endpoints, disabled unless ADMIN_TOKEN is set.

`POST /admin/ingest` loads knowledge-base documents through the deployed API's own
database connection and OpenAI key, so no database credentials ever leave the server.
"""

import secrets
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db import get_pool
from app.llm.client import get_openai
from app.rag.chunking import chunk_document
from app.rag.embeddings import OpenAIEmbedder
from app.rag.ingest import ingest


def require_admin(authorization: Annotated[str | None, Header()] = None) -> None:
    token = get_settings().admin_token
    if not token:
        raise HTTPException(status.HTTP_404_NOT_FOUND)  # endpoint disabled: don't advertise it
    if not authorization or not secrets.compare_digest(authorization, f"Bearer {token}"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"})


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class KbDocument(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9-]+\.md$")
    content: str = Field(min_length=1, max_length=200_000)


class IngestRequest(BaseModel):
    documents: list[KbDocument] = Field(min_length=1, max_length=50)


class IngestResponse(BaseModel):
    documents: int
    chunks: int
    embedding_model: str


@router.post("/ingest")
async def ingest_kb(body: IngestRequest) -> IngestResponse:
    try:
        chunks = [chunk for doc in body.documents for chunk in chunk_document(doc.content)]
    except ValueError as error:  # includes pydantic's ValidationError for bad frontmatter
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    pool = await get_pool()
    async with pool.connection() as conn:
        report = await ingest(conn, chunks, OpenAIEmbedder(get_openai()))
    return IngestResponse(**asdict(report))
