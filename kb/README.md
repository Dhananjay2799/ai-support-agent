# Acme Cloud knowledge base

Sample support documentation for **Acme Cloud**, a fictional file storage and sync
service. The agent answers only from these documents.

## Format

Each document is markdown with YAML frontmatter:

| Field | Meaning |
| --- | --- |
| `doc_id` | Stable id used in citations |
| `title` | Document title, used in chunk breadcrumbs |
| `doc_type` | `policy`, `troubleshooting` or `reference` |
| `tiers` | Plans the whole document applies to |
| `effective_date`, `version` | When this version took effect |

A section that applies to fewer plans than its document declares this right under its
heading, and the chunker narrows that section's `tiers` metadata:

```markdown
## Enterprise refunds
<!-- tiers: enterprise -->
```

## Source of truth

Refund limits, refund windows, support response times and the verification attempt limit
come from `apps/api/app/policy/policy.yaml`. `apps/api/tests/test_kb_consistency.py`
fails if these documents state different numbers.
