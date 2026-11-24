# Retrieval and guardrail evaluation

Generated 2026-11-24 16:22 UTC by `apps/api/scripts/run_evals.py` over 40 labeled questions (`evals/questions.jsonl`). Embeddings: `text-embedding-3-small`; judge: `gpt-6-luna`; knowledge base: 40 chunks.

## Summary

| Metric | Value |
| --- | --- |
| Right section ranked first (answerable questions) | 92% |
| Right section in top 3 | 100% |
| Section MRR | 0.96 |
| Right document ranked first / in top 3 | 100% / 100% |
| Thresholds (margin rule, ±0.05) | low = 0.33, high = 0.48 |
| Unsafe answers (should have escalated) | 0 |
| Escalation precision | 100% |
| Escalation recall | 100% |
| Answerable questions answered | 100% |
| Questions checked by the judge (the rest were rejected by similarity alone) | 27 of 40 |

## Why two layers

Best single similarity cut-off without the judge (answer if top cosine >= 0.31): 2 unsafe answers, 0 answerable questions escalated. With the judge checking everything above the low cut-off: 0 unsafe, 0 missed.

| Slice | Questions | Top cosine min | median | max | Correct decisions |
| --- | --- | --- | --- | --- | --- |
| in_scope | 20 | 0.382 | 0.541 | 0.674 | 20/20 |
| tier_specific | 5 | 0.498 | 0.659 | 0.705 | 5/5 |
| out_of_scope | 10 | 0.048 | 0.120 | 0.223 | 10/10 |
| unanswerable_in_domain | 5 | 0.243 | 0.308 | 0.427 | 5/5 |

## Chunking comparison

Vector-only retrieval over each chunking, answerable questions. *Answer in top k* means the passages handed to the model contain the labeled answer text.

| Strategy | Chunks | Avg tokens | Answer in top 1 | Answer in top 3 | Context tokens (top 3) |
| --- | --- | --- | --- | --- | --- |
| heading-aware | 40 | 78 | 92% | 100% | 240 |
| fixed 512-token | 10 | 359 | 88% | 96% | 1131 |

## Every question

| Id | Slice | Tier | Expected | Decision | Band | Top cosine | Section rank | Top section |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| refund-timing | in_scope | pro | answer | answer | high | 0.542 | 1 | Refunds and billing > How refunds are paid |
| failed-payment | in_scope | pro | answer | answer | middle | 0.422 | 1 | Refunds and billing > Billing dates and failed payments |
| pro-storage | in_scope | any | answer | answer | high | 0.645 | 2 | Plans and limits > Storage add-ons |
| recovery-code | in_scope | free | answer | answer | high | 0.641 | 1 | Account security and two-factor authentication > Resetting two-factor authentication with recovery codes |
| lost-phone | in_scope | pro | answer | answer | high | 0.629 | 1 | Account security and two-factor authentication > Lost device and no recovery codes |
| e409 | in_scope | pro | answer | answer | high | 0.524 | 1 | Troubleshooting sync errors > E-409: Conflicting edits |
| e507 | in_scope | free | answer | answer | high | 0.524 | 1 | Troubleshooting sync errors > E-507: Storage full |
| e401 | in_scope | pro | answer | answer | high | 0.667 | 1 | Troubleshooting sync errors > E-401: Session expired |
| export-files | in_scope | free | answer | answer | middle | 0.459 | 1 | Data export and account deletion > Exporting your files |
| restore-account | in_scope | free | answer | answer | middle | 0.382 | 1 | Data export and account deletion > Deleting an account |
| billing-records | in_scope | pro | answer | answer | high | 0.564 | 1 | Data export and account deletion > Deleting an account |
| response-time | in_scope | pro | answer | answer | high | 0.539 | 2 | Support channels and response times > When the assistant hands over to a person |
| talk-to-human | in_scope | free | answer | answer | middle | 0.395 | 1 | Support channels and response times > When the assistant hands over to a person |
| largest-file | in_scope | free | answer | answer | middle | 0.461 | 1 | Troubleshooting sync errors > E-413: File too large |
| free-space | in_scope | pro | answer | answer | high | 0.607 | 1 | Troubleshooting sync errors > E-507: Storage full |
| stuck-preparing | in_scope | pro | answer | answer | high | 0.549 | 1 | Troubleshooting sync errors > Sync stuck on "Preparing…" |
| downgrade-files | in_scope | pro | answer | answer | high | 0.674 | 1 | Cancellation and downgrades > Downgrading from Pro to Free |
| suspicious-login | in_scope | pro | answer | answer | middle | 0.480 | 1 | Account security and two-factor authentication > Suspicious activity |
| renewal-date | in_scope | pro | answer | answer | high | 0.611 | 1 | Refunds and billing > Billing dates and failed payments |
| duplicate-charge | in_scope | pro | answer | answer | high | 0.510 | 1 | Refunds and billing > Duplicate charges |
| free-addon-refund | tier_specific | free | answer | answer | high | 0.586 | 1 | Refunds and billing > Free plan add-ons |
| ent-2fa-reset | tier_specific | enterprise | answer | answer | high | 0.659 | 1 | Account security and two-factor authentication > Enterprise accounts |
| pro-api-limit | tier_specific | pro | answer | answer | high | 0.676 | 1 | API access and rate limits > Rate limits |
| ent-end-contract | tier_specific | enterprise | answer | answer | high | 0.498 | 1 | Cancellation and downgrades > Ending an Enterprise contract |
| ent-audit-export | tier_specific | enterprise | answer | answer | high | 0.705 | 1 | Data export and account deletion > Exporting audit logs |
| weather | out_of_scope | pro | escalate | escalate | low | 0.104 | — | Refunds and billing > Refund limits by plan |
| lasagna | out_of_scope | free | escalate | escalate | low | 0.051 | — | Account security and two-factor authentication > Verifying a customer's identity |
| world-cup | out_of_scope | any | escalate | escalate | low | 0.048 | — | Plans and limits > Storage add-ons |
| cloud-poem | out_of_scope | pro | escalate | escalate | low | 0.159 | — | API access and rate limits > Who can use the API |
| translate | out_of_scope | free | escalate | escalate | low | 0.073 | — | Data export and account deletion > Privacy requests |
| percent | out_of_scope | any | escalate | escalate | low | 0.175 | — | API access and rate limits > Raising the limit |
| laptop | out_of_scope | pro | escalate | escalate | low | 0.199 | — | Troubleshooting sync errors > E-409: Conflicting edits |
| capital | out_of_scope | enterprise | escalate | escalate | low | 0.068 | — | Account security and two-factor authentication > Suspicious activity |
| check-engine | out_of_scope | free | escalate | escalate | low | 0.223 | — | Troubleshooting sync errors > E-507: Storage full |
| python-framework | out_of_scope | pro | escalate | escalate | low | 0.135 | — | Refunds and billing > Who is eligible |
| linux-app | unanswerable_in_domain | pro | escalate | escalate | middle | 0.427 | — | Data export and account deletion > Exporting your files |
| paypal | unanswerable_in_domain | pro | escalate | escalate | low | 0.259 | — | Refunds and billing > Who is eligible |
| student-discount | unanswerable_in_domain | free | escalate | escalate | low | 0.308 | — | Refunds and billing > Free plan add-ons |
| family-plan | unanswerable_in_domain | free | escalate | escalate | middle | 0.410 | — | Plans and limits > Plan comparison |
| data-centers | unanswerable_in_domain | enterprise | escalate | escalate | low | 0.243 | — | Data export and account deletion > Privacy requests |

Caveat: thresholds are tuned on these same 40 questions, so treat the numbers as a sanity check on the design, not a held-out estimate.
