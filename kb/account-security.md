---
doc_id: account-security
title: Account security and two-factor authentication
doc_type: troubleshooting
tiers: [free, pro, enterprise]
effective_date: 2026-10-01
version: "2026.10"
---

# Account security and two-factor authentication

## Verifying a customer's identity

Before discussing account details or taking any action, support verifies the person is the
account owner. They must give the account email address and the billing ZIP or postal code,
and both must match. After 3 failed attempts in one conversation, stop, do not reveal
which detail was wrong, and open a `verification_failed` ticket.

Never ask a customer for their password, a full card number or a two-factor code.

## Resetting two-factor authentication with recovery codes

When two-factor authentication (2FA) is turned on, Acme Cloud gives the customer ten one-time
recovery codes. To sign in without their authenticator app:

1. On the sign-in page, choose **Use a recovery code**.
2. Enter one unused recovery code.
3. Go to **Settings > Security** and set up 2FA again on the new device.
4. Generate a fresh set of recovery codes and store them somewhere safe.

Each recovery code works once.

## Lost device and no recovery codes

If the customer has lost their device and their recovery codes, support can't turn off 2FA
in the conversation. Open a high-priority ticket. The security team verifies ownership through
a signed request from the account email, then removes 2FA within 2 business days. For
security, the account stays locked until then.

## Enterprise accounts
<!-- tiers: enterprise -->

On Enterprise, workspace admins can reset 2FA for members from **Admin console > Members >
Reset two-factor**. Members should contact their admin first. If the admin is the person
locked out, a second admin can reset them; if there is no other admin, open a high-priority
ticket.

## Suspicious activity

If a customer reports sign-ins they don't recognize, tell them to change their password,
sign out all other sessions from **Settings > Security > Active sessions**, and review recent
file activity. Open a high-priority ticket so the security team can review the account.
