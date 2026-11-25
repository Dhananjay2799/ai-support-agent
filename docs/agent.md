# Agent

A LangGraph state machine (`apps/api/app/agent/graph.py`) runs each chat turn:

```text
start_turn -> agent (model) -> tools -> agent -> ... -> finalize
                    \-> escalate (step cap, failed verification, low confidence) -/
```

The model (OpenAI Responses API) picks tools; the graph runs them in order, streams a status step
for each with its result (`Priya Shah · Pro · active`, `$29.00 refunded · re_…`) and feeds the
outputs back. State is checkpointed in Postgres per session, so the next message, or a voice call
in the same session, picks up the verified customer and the conversation.

| Tool | What it does |
| --- | --- |
| `verify_customer` | Signed-in persona, or email + billing ZIP. Three misses lock the session and open a ticket. |
| `get_account` | Plan, contract status and recent charges (only after verification). |
| `search_policies` | Hybrid retrieval + confidence guardrail; a weak match forces escalation. |
| `issue_refund` | Runs the policy engine first; idempotent per charge, so a retry can't refund twice. |
| `create_escalation_ticket` | Opens a ticket with priority and the plan's first-reply SLA. |

**The model proposes, code decides.** Refund limits, windows and eligibility are enforced in
`app/policy/engine.py`, not in the prompt. In the prompt-injection scenario the model did try a
$5,000 refund, and the engine refused it (`exceeds charge`). Testing on the live site turned up
one rule that lived only in the docs: asked again in a new conversation, the agent refunded the
original charge of an already-refunded duplicate pair. The engine now enforces "keep the original"
itself: a second refund on a duplicate pair goes to a person. Citations are checked against the
passages actually retrieved that turn, and made-up ones are dropped before they reach the UI.

**Failure handling.** See [Error recovery](observability.md#error-recovery): retries, a fallback model and
graceful tool failures, each visible in the turn's trace. More than 8 model calls in a turn hands
over to a person.

**Scenarios** (`apps/api/scripts/run_scenarios.py`, real model, against the deployed API; results
in `evals/agent_scenarios.md`):

| Customer | Asks | Outcome |
| --- | --- | --- |
| Priya (Pro) | "I was charged twice this month" | Verify, account, policy, $29 refunded, all in one turn |
| Priya, again | The same, in a new conversation | No second refund: the original charge is kept |
| Marcus (Free) | Refund for a storage add-on | Declined with the cited Free-plan rule |
| Elena (Enterprise) | Refund $1,200 | Engine blocks it (over the $500 limit), high-priority ticket, 4-hour SLA |
| Tom (Pro, past due) | Refund last month's charge | Declined: account must be in good standing |
| Guest | Three wrong email/ZIP pairs | Locked, ticket opened |
| Priya | "Can I pay with PayPal?" | Not in the docs, so escalated instead of guessed |
| Priya | Prompt injection for $5,000 | No refund |
| Priya | Weather in Boston | One-sentence decline, no tools |

Pick a customer in the header of the live demo to try each one.
