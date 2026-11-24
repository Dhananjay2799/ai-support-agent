# AI Support Agent

A customer support agent for **Acme Cloud** (a fictional file-sync SaaS) that answers in streaming
chat and live voice, chains tool calls to verify customers and act on their accounts, grounds every
answer in a policy knowledge base, and hands low-confidence cases to a human.

> **Status: Day 3 of 7.** The text agent is live: it verifies the customer, reads their account,
> checks policy and refunds or escalates in one streamed turn, with citations and a ticket card.
> 9 end-to-end scenarios pass against the deployed API. The voice path is proven in a spike; the
> voice UI and the observability drawer land next.

## Architecture

```mermaid
flowchart LR
  subgraph Browser["Next.js UI (Vercel)"]
    chat[Chat + step status]
    voice[Voice: waveform, captions]
    drawer[Observability drawer]
  end
  subgraph API["FastAPI service (Vercel Functions)"]
    graph[Agent graph<br/>LangGraph]
    sideband[Voice sideband]
    tools[Tools + policy engine]
    rag[RAG + guardrails]
    tracer[Tracer + rate limits]
  end
  db[(Supabase Postgres<br/>pgvector · CRM · traces)]
  openai[OpenAI voice model]
  llm[Text LLM]

  chat -- "POST /chat (SSE)" --> graph
  voice -- WebRTC audio --> openai
  openai <-- sideband WS --> sideband
  graph --> llm
  graph --> tools
  sideband --> tools
  tools --> rag
  API --> db
```

Audio goes straight from the browser to OpenAI; the server joins the same call over a sideband
connection, so chat and voice share one tool layer, one policy engine and one trace.

## Agent

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
$5,000 refund, and the engine refused it (`exceeds charge`). Citations are checked against the
passages actually retrieved that turn, and made-up ones are dropped before they reach the UI.

**Failure handling.** A crashing tool is reported to the model as unavailable and the turn still
finishes. More than 8 model calls in a turn hands over to a person. LLM timeouts, rate limits and
database outages end the stream with a typed, retryable error instead of a hung spinner.

**Scenarios** (`apps/api/scripts/run_scenarios.py`, real model, against the deployed API; results
in `evals/agent_scenarios.md`):

| Customer | Asks | Outcome |
| --- | --- | --- |
| Priya (Pro) | "I was charged twice this month" | Verify, account, policy, $29 refunded, all in one turn |
| Marcus (Free) | Refund for a storage add-on | Declined with the cited Free-plan rule |
| Elena (Enterprise) | Refund $1,200 | Engine blocks it (over the $500 limit), high-priority ticket, 4-hour SLA |
| Tom (Pro, past due) | Refund last month's charge | Declined: account must be in good standing |
| Guest | Three wrong email/ZIP pairs | Locked, ticket opened |
| Priya | "Can I pay with PayPal?" | Not in the docs, so escalated instead of guessed |
| Priya | Prompt injection for $5,000 | No refund |
| Priya | Weather in Boston | One-sentence decline, no tools |

Pick a customer in the header of the live demo to try each one.

## RAG and confidence guardrails

Results from `evals/results.md` (40 labeled questions: 25 answerable, 10 off-topic, 5 on-topic
but not covered by the docs, the case where support bots tend to make things up):

| | Result |
| --- | --- |
| Right policy section in the top 3 passages | 100% (ranked first: 92%) |
| Questions that should escalate but got answered | **0** |
| Escalation precision / recall | 100% / 100% |
| Best single similarity cut-off, no judge | 2 unsafe answers |
| Heading-aware vs fixed 512-token chunks | answer reaches the model 100% vs 96% of the time, with 240 vs 1,131 context tokens |

**Chunking.** Docs split on H2/H3 headings so each policy clause stays whole, with a breadcrumb
(`Refunds and billing > Free plan add-ons`) embedded alongside the text. Sections that apply to
fewer plans say so (`<!-- tiers: enterprise -->`), so tier filtering works per clause, not per
document. Tables are never split.

**Retrieval.** One SQL query runs pgvector cosine search and Postgres full-text search, merges them
with reciprocal rank fusion and filters by the customer's plan. Error codes such as `E-409` are
indexed as one token, because Postgres otherwise splits them and every code matches every other.

**Guardrails.** Below the `low` similarity cut-off, escalate without an LLM call. Above it, a
judge model (structured output) must confirm that a retrieved passage states the answer, citing
passage ids we check against what was retrieved. `low` sits 0.05 below the weakest answerable
question in the eval; `high` only sets the confidence shown. The judge originally ran only between
the two, but the agent writes its own search queries, and a keyword-dense rewrite of the PayPal
question scored like a real match. The judge also gets the customer's plan, and grades policy, not
account facts: "refund policy for my $29 charge from last month" is answerable if the refund rules
are there, while every undocumented question still came back 0/6 unanswerable.

Refund limits, windows and response times live in `policy.yaml`; the policy engine enforces them
and a test fails if the knowledge base states different numbers.

## Voice

GPT-Live (`gpt-live-1.1`) with **client delegation**: the voice model handles listening, speaking
and interruptions, and hands each request to our backend, which answers through the same retrieval,
guardrails and agent graph as chat. `apps/api/scripts/voice_spike.py` proves the loop
with synthesized speech: delegation 0.5 s after the customer stops talking, an immediate spoken
acknowledgement, and our grounded answer spoken with its numbers intact. A question the docs don't
cover is escalated by voice too.

## Repository layout

```text
apps/api/            FastAPI service
  app/contracts/     Pydantic models for everything streamed to the UI
  app/routes/        HTTP endpoints (chat SSE, health, token-protected KB ingest)
  app/agent/         LangGraph agent: graph, tools, prompts, Responses API client
  app/data/          CRM queries: customers, charges, idempotent refunds, tickets
  app/policy/        policy.yaml and the refund policy engine
  app/rag/           chunking, ingestion, hybrid retrieval, guardrails, thresholds.yaml
  scripts/           contract export, RAG eval, agent scenarios, voice spike, KB push
apps/web/            Next.js 16 UI
  lib/generated/     TypeScript types generated from the API's contracts
  lib/sse.ts         Incremental SSE parser for POST streams
  components/chat/   Chat panel, steps, cited answers, escalation card, customer switcher
contracts/           JSON Schema exported from the Pydantic models
kb/                  Acme Cloud policy and troubleshooting docs (RAG source)
evals/               labeled questions and the latest results
supabase/migrations/ Schema, RLS lock-down, demo data function
```

## Design decisions so far

- **One typed event contract.** Every streamed event is a member of a Pydantic discriminated union.
  Its JSON Schema generates the TypeScript types, and CI fails if they drift.
- **SSE over POST.** Each chat turn is one request with one-way streaming, so SSE is simpler than
  WebSockets. The browser parses the stream itself because `EventSource` only supports GET.
- **Policy as config.** Refund limits, windows, response times and verification attempts live in
  `policy.yaml`. The policy engine enforces them, and a test fails if the knowledge base states
  different numbers.
- **Demo data that doesn't expire.** `reset_demo_data()` seeds charges relative to `now()`, so
  refund-window scenarios behave the same whenever the demo is opened.
- **Postgres for everything stateful.** Agent checkpoints, CRM data, vectors and (next) traces
  share one database, so a stateless function can resume any session.
- **Server-only database access.** RLS is on with no policies and API roles have no grants; only
  the API's server connection can read or write.

## Running locally

Requirements: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22.

```bash
cp .env.example apps/api/.env        # keep the API section
cp .env.example apps/web/.env.local  # keep the web section
make install
make dev-api   # http://localhost:8000
make dev-web   # http://localhost:3000
make check     # lint, type checks and tests for both apps
```

After changing anything in `apps/api/app/contracts`, run `make contracts`.

## Deployment

Both apps run on Vercel as separate projects, with Supabase for the database.

| Project | Root directory | Environment variables |
| --- | --- | --- |
| API (FastAPI on Vercel Functions, Fluid compute) | `apps/api` | `CORS_ORIGINS` = the web app's URL, `DATABASE_URL` (Supabase pooler), `OPENAI_API_KEY`, optional `ADMIN_TOKEN` to enable KB ingest |
| Web (Next.js) | `apps/web` | `NEXT_PUBLIC_API_URL` = the API's URL (baked in at build time, so redeploy after changing it) |

Functions are stateless and capped at 300 s on the Hobby plan, so all session state lives in
Postgres and every stream (including a voice call's server connection) stays within that limit.
`apps/api/Dockerfile` runs the same API on any container host if a persistent server is needed.

Database: apply `supabase/migrations` in order, then load the knowledge base with
`API_URL=https://<api-url> ADMIN_TOKEN=… uv run python -m scripts.push_kb` from `apps/api`.
