import hashlib
import math
import re
from collections.abc import Sequence
from typing import Protocol

from openai import AsyncOpenAI

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536

Vector = list[float]


class Embedder(Protocol):
    model: str

    async def embed(self, texts: Sequence[str]) -> list[Vector]: ...


class OpenAIEmbedder:
    model = EMBEDDING_MODEL

    def __init__(self, client: AsyncOpenAI, *, batch_size: int = 128) -> None:
        self._client = client
        self._batch_size = batch_size

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        vectors: list[Vector] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            response = await self._client.embeddings.create(model=self.model, input=batch)
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
        return vectors


class HashingEmbedder:
    """Deterministic bag-of-words embedder for tests: no network, and texts that
    share words still land near each other, so retrieval logic can be tested."""

    model = "hashing-test"

    def __init__(self, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        self._dimensions = dimensions

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> Vector:
        vector = [0.0] * self._dimensions
        for word in re.findall(r"[a-z0-9-]+", text.lower()):
            bucket = int.from_bytes(hashlib.blake2b(word.encode(), digest_size=4).digest(), "big")
            vector[bucket % self._dimensions] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


def to_pgvector(vector: Sequence[float]) -> str:
    """Text form accepted by pgvector (`'[0.1,0.2]'::vector`)."""
    return "[" + ",".join(f"{v:.7g}" for v in vector) + "]"


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0
