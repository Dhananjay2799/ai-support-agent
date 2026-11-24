---
doc_id: plans-and-limits
title: Plans and limits
doc_type: reference
tiers: [free, pro, enterprise]
effective_date: 2026-10-01
version: "2026.10"
---

# Plans and limits

## Plan comparison

| | Free | Pro | Enterprise |
| --- | --- | --- | --- |
| Price | $0 | $29 per month per workspace | Annual contract, priced per seat |
| Storage | 5 GB | 1 TB | 5 TB plus 100 GB per seat |
| Members | 1 | Up to 10 | Unlimited |
| Largest single file | 250 MB | 10 GB | 50 GB |
| Version history | 7 days | 180 days | 1 year |
| Shared folders | View-only links | Full sharing with permissions | Full sharing, admin policies |
| API access | No | Yes | Yes, higher limits |
| Single sign-on (SAML) | No | No | Yes |
| Audit log | No | No | Yes |

## Upgrading

Upgrades take effect immediately. When a customer upgrades from Free to Pro, they are charged
the full monthly price that day and their billing date moves to the upgrade date. Moving from
Pro to Enterprise is handled by the sales team; open a ticket in the `customer_request`
category.

## Storage add-ons

Free plan customers can buy a 50 GB storage add-on for $9 as a one-off purchase that lasts 12
months. Pro and Enterprise customers who need more space should change plans or add seats
instead; add-ons are not sold on those plans.

## What happens when storage is full

When an account reaches its storage limit, existing files stay available and sharing keeps
working, but new uploads and edits fail with error `E-507` until space is freed or the plan
is upgraded. See the sync errors guide for the steps to free space.
