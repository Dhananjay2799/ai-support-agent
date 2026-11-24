import itertools
from pathlib import Path

from app.rag.chunking import chunk_document, chunk_fixed, estimate_tokens, load_kb

KB_DIR = Path(__file__).resolve().parents[3] / "kb"

DOC = """---
doc_id: sample
title: Sample policy
doc_type: policy
tiers: [free, pro, enterprise]
effective_date: 2026-10-01
version: "1"
---

# Sample policy

Intro paragraph.

## Limits

| Plan | Limit |
| --- | --- |
| Pro | $100 |

## Enterprise only
<!-- tiers: enterprise -->

Enterprise text.

### Nested detail

Inherits the enterprise tag.

## Back to everyone

Shared text.
"""


def test_sections_become_chunks_with_breadcrumbs() -> None:
    chunks = chunk_document(DOC)
    assert [c.section_path for c in chunks] == [
        "Sample policy",
        "Sample policy > Limits",
        "Sample policy > Enterprise only",
        "Sample policy > Enterprise only > Nested detail",
        "Sample policy > Back to everyone",
    ]
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_section_tier_override_applies_to_subsections_only_within_it() -> None:
    tiers = {c.section_path.split(" > ")[-1]: c.tiers for c in chunk_document(DOC)}
    assert tiers["Enterprise only"] == ("enterprise",)
    assert tiers["Nested detail"] == ("enterprise",)
    assert tiers["Back to everyone"] == ("free", "pro", "enterprise")


def test_tier_comment_is_not_part_of_the_content() -> None:
    assert all("<!--" not in c.content for c in chunk_document(DOC))


def test_tables_stay_whole() -> None:
    limits = next(c for c in chunk_document(DOC) if c.section_path.endswith("Limits"))
    assert limits.content.count("|") >= 9 and "$100" in limits.content


def test_long_sections_split_with_sentence_overlap() -> None:
    paragraphs = "\n\n".join(f"Paragraph {i}. It has a second sentence about rule {i}." for i in range(40))
    doc = DOC.replace("Shared text.", paragraphs)
    pieces = [c for c in chunk_document(doc, max_tokens=60, overlap_tokens=20) if "Back to" in c.section_path]
    assert len(pieces) > 1
    assert all(c.token_count <= 60 + 20 for c in pieces)
    # The opening of each later piece repeats the end of the one before it.
    for previous, current in itertools.pairwise(pieces):
        first_sentence = current.content.split("\n\n")[0]
        assert first_sentence in previous.content


def test_embedding_text_includes_breadcrumb() -> None:
    chunk = chunk_document(DOC)[1]
    assert chunk.embedding_text.startswith("Sample policy > Limits\n\n")


def test_fixed_baseline_ignores_headings() -> None:
    chunks = chunk_fixed(DOC, size_tokens=30, overlap_tokens=5)
    assert len(chunks) > 1
    assert {c.section_path for c in chunks} == {"Sample policy"}


def test_real_kb_chunks_are_well_formed() -> None:
    chunks = load_kb(KB_DIR)
    assert len({(c.doc_id, c.chunk_index) for c in chunks}) == len(chunks)
    assert all(0 < c.token_count <= 450 for c in chunks)
    enterprise_only = {c.section_path for c in chunks if c.tiers == ("enterprise",)}
    assert "Refunds and billing > Enterprise refunds and credits" in enterprise_only


def test_estimate_tokens_is_roughly_four_chars_per_token() -> None:
    assert estimate_tokens("x" * 400) == 100
