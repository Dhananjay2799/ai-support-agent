---
doc_id: troubleshooting-sync-errors
title: Troubleshooting sync errors
doc_type: troubleshooting
tiers: [free, pro, enterprise]
effective_date: 2026-10-01
version: "2026.10"
---

# Troubleshooting sync errors

The desktop and mobile apps show an error code when a file fails to sync. Start by asking the
customer which code they see; it's shown next to the file and in **Activity > Sync issues**.

## E-401: Session expired

The app's sign-in has expired, usually after a password change or a 2FA reset. Sign out of the
desktop app from **Preferences > Account**, sign back in, and the queued files sync
automatically.

## E-403: Permission denied

The customer is trying to change a file in a folder that was shared with them as view-only, or
their access was removed. Ask the folder owner to give them edit access. On Enterprise, admin
policies can also block editing; the workspace admin can check **Admin console > Sharing
policies**.

## E-409: Conflicting edits

Two people edited the same file while one of them was offline. Acme Cloud keeps both versions:
the second one is saved next to the original with "(conflicted copy)" and the editor's name in
the file name. Open both, merge the changes, and delete the extra copy.

## E-413: File too large

The file is bigger than the plan's single-file limit: 250 MB on Free, 10 GB on Pro and 50 GB on
Enterprise. Split the file, compress it, or upgrade the plan.

## E-429: Too many requests

The app is sending requests too quickly, usually during a very large first sync or because of
a script using the API. Syncing pauses and resumes on its own within a few minutes. If it keeps
happening with an API integration, see the API rate limits guide.

## E-507: Storage full

The account has reached its storage limit. Existing files stay available, but new uploads and
edits fail. To free space:

1. Empty **Trash**; deleted files still count toward storage until the trash is emptied.
2. Check **Settings > Storage** for the largest folders.
3. Remove old versions of large files from the file's **Version history**.

Or upgrade the plan for more storage.

## Sync stuck on "Preparing…"

Quit and reopen the app. If it's still stuck, go to **Preferences > Advanced > Rebuild sync
index**. This doesn't delete any files but can take a while on large accounts.
