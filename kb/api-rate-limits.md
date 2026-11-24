---
doc_id: api-rate-limits
title: API access and rate limits
doc_type: reference
tiers: [pro, enterprise]
effective_date: 2026-10-01
version: "2026.10"
---

# API access and rate limits

## Who can use the API

The Acme Cloud REST API is available on Pro and Enterprise. Create a personal access token
from **Settings > Developer > Tokens**. Tokens inherit the creator's permissions and can be
revoked at any time. Never share a token in a support conversation.

## Rate limits

| Plan | Requests per minute | Uploads per hour |
| --- | --- | --- |
| Pro | 600 | 2,000 |
| Enterprise | 3,000 | 20,000 |

Limits apply per workspace, across all of its tokens.

## Handling 429 responses

When a client goes over the limit, the API returns HTTP 429 with a `Retry-After` header giving
the number of seconds to wait. Every response also includes:

- `X-RateLimit-Limit`: the per-minute limit for the workspace
- `X-RateLimit-Remaining`: requests left in the current window
- `X-RateLimit-Reset`: when the window resets, as a Unix timestamp

Clients should wait for `Retry-After` and retry with exponential backoff rather than retrying
immediately.

## Raising the limit
<!-- tiers: enterprise -->

Enterprise customers who need higher limits for a migration or integration can ask their
account manager for a temporary increase of up to 30 days.
