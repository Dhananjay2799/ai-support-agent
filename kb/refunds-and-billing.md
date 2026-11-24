---
doc_id: refunds-and-billing
title: Refunds and billing
doc_type: policy
tiers: [free, pro, enterprise]
effective_date: 2026-10-01
version: "2026.10"
---

# Refunds and billing

## Refund limits by plan

Support agents and the Acme Cloud assistant can approve a refund on the spot when it falls
within the limits below. Requests outside these limits go to the billing team as a ticket.

| Plan | Refunds | Auto-approved up to | Request window |
| --- | --- | --- | --- |
| Free | Not available | — | — |
| Pro | Available | $100 | 30 days from the charge |
| Enterprise | Available | $500 | 60 days from the charge |

The limit applies per charge. A refund can never exceed the original charge amount, and
each charge can be refunded once.

## Who is eligible

A refund requires an account in good standing. Accounts that are **past due** or
**canceled** cannot receive refunds until the outstanding balance is paid. If a customer
with a past-due account asks for a refund, help them update their payment method first.

The person asking must be verified as the account owner or a billing admin before any
refund is discussed in detail.

## Duplicate charges

If the same plan fee was charged twice for the same billing period, the extra charge is
refundable in full as long as it is within the plan's refund window and auto-approve limit.
Refund the most recent duplicate and keep the original charge.

## Free plan add-ons
<!-- tiers: free -->

Free plan customers can buy one-off add-ons such as extra storage packs. Add-on purchases on
the Free plan are final and are not refundable. If a Free customer believes an add-on was
bought by mistake, offer to open a ticket so the billing team can review it as an exception.

## Enterprise refunds and credits
<!-- tiers: enterprise -->

Enterprise contracts are billed annually, with prorated charges when seats are added. Refunds
above the $500 auto-approve limit, including prorated seat charges, are handled by the
customer's account manager. Open a high-priority ticket with the charge id, amount and the
customer's reason; the account manager replies within the Enterprise response time.

## How refunds are paid

Approved refunds go back to the original payment method. They usually appear within 5 to 10
business days, depending on the customer's bank. Each refund gets a reference id starting with
`re_` that the customer can quote to their bank.

## Billing dates and failed payments

Monthly plans renew on the same day each month; annual plans renew on the contract anniversary.
If a payment fails, Acme Cloud retries it three times over seven days. The account becomes
past due after the first failed attempt and stays fully usable for 14 days, after which it is
limited to read-only access until the balance is paid.
