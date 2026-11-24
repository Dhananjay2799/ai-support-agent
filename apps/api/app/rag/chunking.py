"""Heading-aware chunking for the policy knowledge base.

Why not fixed-size windows: policy clauses carry conditions ("within 30 days unless…")
that a fixed split can cut in half, and a chunk that lost its heading no longer says
which plan it applies to. So we:

1. split on H2/H3 headings, so each clause stays whole;
2. keep a breadcrumb (`Doc > Section > Subsection`) that is embedded with the text;
3. narrow `tiers` per section via `<!-- tiers: … -->` right under a heading;
4. pack paragraphs up to ~MAX_TOKENS and overlap only when a section must be split;
5. never split a table.

`chunk_fixed` is the naive baseline, kept only for the eval comparison.
"""

import math
import re
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.policy.config import Tier

MAX_TOKENS = 400
OVERLAP_TOKENS = 50
BREADCRUMB_SEPARATOR = " > "

_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_TIERS = re.compile(r"^<!--\s*tiers:\s*([a-z,\s]+?)\s*-->$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    chunk_index: int
    doc_title: str
    section_path: str
    content: str
    tiers: tuple[str, ...]
    doc_type: str
    effective_date: date
    version: str

    @property
    def token_count(self) -> int:
        return estimate_tokens(self.content)

    @property
    def embedding_text(self) -> str:
        """What gets embedded: the breadcrumb gives short chunks their context."""
        return f"{self.section_path}\n\n{self.content}"


@dataclass(frozen=True)
class _Section:
    path: tuple[str, ...]
    tiers: tuple[str, ...]
    blocks: tuple[str, ...]


def estimate_tokens(text: str) -> int:
    """~4 characters per token for English. Close enough for sizing chunks,
    and avoids downloading a tokenizer at runtime."""
    return max(1, math.ceil(len(text) / 4))


class DocMeta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_id: str = Field(pattern=r"^[a-z0-9-]+$")
    title: str
    doc_type: str = Field(pattern=r"^(policy|troubleshooting|reference)$")
    tiers: tuple[Tier, ...] = Field(min_length=1)
    effective_date: date
    version: str


def split_frontmatter(text: str) -> tuple[DocMeta, str]:
    if not text.startswith("---\n"):
        raise ValueError("document is missing YAML frontmatter")
    _, raw_meta, body = text.split("---\n", 2)
    return DocMeta.model_validate(yaml.safe_load(raw_meta)), body


def _blocks(lines: list[str]) -> list[str]:
    """Blank-line separated blocks; a table or list stays one block."""
    blocks, current = [], []
    for line in lines:
        if line.strip():
            current.append(line.rstrip())
        elif current:
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))
    return blocks


def _sections(body: str, title: str, doc_tiers: tuple[str, ...]) -> list[_Section]:
    sections: list[_Section] = []
    h2: str | None = None
    h2_tiers = doc_tiers
    path: tuple[str, ...] = (title,)
    tiers = doc_tiers
    lines: list[str] = []
    awaiting_tiers = False

    def flush() -> None:
        blocks = _blocks(lines)
        if blocks:
            sections.append(_Section(path, tiers, tuple(blocks)))

    for line in body.splitlines():
        heading = _HEADING.match(line)
        if heading:
            flush()
            lines = []
            level, text = len(heading.group(1)), heading.group(2)
            if level == 1:
                path, tiers, h2 = (title,), doc_tiers, None
            elif level == 2:
                h2, h2_tiers = text, doc_tiers
                path, tiers = (title, text), doc_tiers
            else:
                path = (title, h2, text) if h2 else (title, text)
                tiers = h2_tiers
            awaiting_tiers = level > 1
            continue

        override = _TIERS.match(line.strip())
        if awaiting_tiers and override:
            tiers = tuple(t.strip() for t in override.group(1).split(",") if t.strip())
            if len(path) == 2:
                h2_tiers = tiers
            awaiting_tiers = False
            continue
        if line.strip():
            awaiting_tiers = False
        lines.append(line)

    flush()
    return sections


def _split_oversized(block: str, max_tokens: int) -> list[str]:
    """Split a single block that's too big on sentence boundaries (tables never are)."""
    if estimate_tokens(block) <= max_tokens or block.lstrip().startswith("|"):
        return [block]
    pieces, current = [], ""
    for sentence in _SENTENCE_END.split(block):
        candidate = f"{current} {sentence}".strip()
        if current and estimate_tokens(candidate) > max_tokens:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def _tail(text: str, max_tokens: int) -> str:
    """Last whole sentences of `text` that fit in `max_tokens`, for overlap."""
    if text.lstrip().startswith("|"):
        return ""  # repeating half a table helps nobody
    tail = ""
    for sentence in reversed(_SENTENCE_END.split(text)):
        candidate = f"{sentence} {tail}".strip()
        if estimate_tokens(candidate) > max_tokens:
            break
        tail = candidate
    return tail


def _pack(blocks: tuple[str, ...], max_tokens: int, overlap_tokens: int) -> list[str]:
    pieces = [piece for block in blocks for piece in _split_oversized(block, max_tokens)]
    chunks: list[str] = []
    current: list[str] = []
    for piece in pieces:
        candidate = "\n\n".join([*current, piece])
        if current and estimate_tokens(candidate) > max_tokens:
            chunks.append("\n\n".join(current))
            overlap = _tail(current[-1], overlap_tokens)
            current = [overlap, piece] if overlap else [piece]
        else:
            current.append(piece)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _base_chunk(meta: DocMeta) -> Chunk:
    return Chunk(
        doc_id=meta.doc_id,
        chunk_index=0,
        doc_title=meta.title,
        section_path=meta.title,
        content="",
        tiers=meta.tiers,
        doc_type=meta.doc_type,
        effective_date=meta.effective_date,
        version=meta.version,
    )


def chunk_document(
    text: str, *, max_tokens: int = MAX_TOKENS, overlap_tokens: int = OVERLAP_TOKENS
) -> list[Chunk]:
    meta, body = split_frontmatter(text)
    base = _base_chunk(meta)
    chunks: list[Chunk] = []
    for section in _sections(body, meta.title, meta.tiers):
        for content in _pack(section.blocks, max_tokens, overlap_tokens):
            chunks.append(
                replace(
                    base,
                    chunk_index=len(chunks),
                    section_path=BREADCRUMB_SEPARATOR.join(section.path),
                    content=content,
                    tiers=section.tiers,
                )
            )
    return chunks


def chunk_fixed(text: str, *, size_tokens: int = 512, overlap_tokens: int = 50) -> list[Chunk]:
    """Baseline for the eval: fixed windows over the raw body, headings ignored."""
    meta, body = split_frontmatter(text)
    base = _base_chunk(meta)
    size, step = size_tokens * 4, (size_tokens - overlap_tokens) * 4
    windows = [body[i : i + size].strip() for i in range(0, max(len(body) - overlap_tokens * 4, 1), step)]
    return [replace(base, chunk_index=i, content=w) for i, w in enumerate(w for w in windows if w)]


def load_kb(kb_dir: Path, *, fixed: bool = False) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(kb_dir.glob("*.md")):
        if path.name == "README.md":
            continue
        text = path.read_text(encoding="utf-8")
        chunks.extend(chunk_fixed(text) if fixed else chunk_document(text))
    return chunks
