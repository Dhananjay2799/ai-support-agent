---
doc_id: data-export-and-deletion
title: Data export and account deletion
doc_type: policy
tiers: [free, pro, enterprise]
effective_date: 2026-10-01
version: "2026.10"
---

# Data export and account deletion

## Exporting your files

Customers can download everything from **Settings > Data > Export**. Acme Cloud prepares a
ZIP archive of all files and folders, keeping the folder structure, and emails a download link
when it's ready. Large accounts can take up to 48 hours. The link works for 7 days.

Version history and comments are included as a separate JSON file in the archive.

## Exporting audit logs
<!-- tiers: enterprise -->

Enterprise admins can export the audit log as CSV from **Admin console > Audit log > Export**,
covering up to one year of activity.

## Deleting an account

The account owner can delete the account from **Settings > Account > Delete account**. Any
active subscription is canceled first. Deletion works like this:

1. The account is deactivated immediately and its sharing links stop working.
2. For 30 days, the owner can restore the account by signing in.
3. After 30 days, all files, version history and personal data are permanently erased.
   Erased data can't be recovered by support.

Billing records are kept for 7 years because tax law requires it, even after deletion.

## Privacy requests

Customers can ask for a copy of the personal data Acme Cloud holds about them, or ask for it to
be corrected or erased. Open a ticket in the `customer_request` category; the privacy team
responds within 30 days. Support can't promise faster handling or make legal judgments about
these requests.
