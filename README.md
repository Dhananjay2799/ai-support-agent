# AI Support Agent

A customer support agent for **Acme Cloud** (a fictional file-sync SaaS) that answers in streaming
chat and live voice, chains tool calls to verify customers and act on their accounts, grounds every
answer in a policy knowledge base, and hands low-confidence cases to a human.

> **Status: Day 2 of 7.** Running end to end: typed event contract, streaming chat API and UI,
> database with demo personas, and the RAG + guardrail engine with a labeled eval. The voice path
> is proven in a spike. The LangGraph agent, the voice UI and observability land next.

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

**Guardrails.** The best semantic match decides the band: below `low`, escalate without an LLM
call; at or above `high`, answer; in between, a judge model (structured output) must confirm that a
retrieved passage states the answer, citing passage ids we check against what was retrieved.
Thresholds use a margin rule rather than minimizing judge calls: `low` sits 0.05 below the weakest
answerable question and `high` 0.05 above the strongest question that should escalate. That way,
new questions near the boundary get checked by the judge rather than slipping through on score alone.

Refund limits, windows and response times live in `policy.yaml`; the policy engine enforces them
and a test fails if the knowledge base states different numbers.

## Voice

GPT-Live (`gpt-live-1.1`) with **client delegation**: the voice model handles listening, speaking
and interruptions, and hands each request to our backend, which answers through the same retrieval,
guardrails and (from Day 3) agent graph as chat. `apps/api/scripts/voice_spike.py` proves the loop
with synthesized speech: delegation 0.5 s after the customer stops talking, an immediate spoken
acknowledgement, and our grounded answer spoken with its numbers intact. A question the docs don't
cover is escalated by voice too.

## Repository layout

```text
apps/api/            FastAPI service
  app/contracts/     Pydantic models for everything streamed to the UI
  app/routes/        HTTP endpoints (chat SSE, health)
  app/agent/         Agent orchestration (Day 1: scripted turn)
  app/policy/        policy.yaml: refund limits, SLAs, verification rules
  app/rag/           chunking, ingestion, hybrid retrieval, guardrails, thresholds.yaml
  scripts/           contract export, eval runner, voice spike
apps/web/            Next.js 16 UI
  lib/generated/     TypeScript types generated from the API's contracts
  lib/sse.ts         Incremental SSE parser for POST streams
  components/chat/   Chat panel, messages, step list
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
| API (FastAPI on Vercel Functions, Fluid compute) | `apps/api` | `CORS_ORIGINS` = the web app's URL; from Day 2 `DATABASE_URL`, `OPENAI_API_KEY` |
| Web (Next.js) | `apps/web` | `NEXT_PUBLIC_API_URL` = the API's URL (baked in at build time, so redeploy after changing it) |

Functions are stateless and capped at 300 s on the Hobby plan, so all session state lives in
Postgres and every stream (including a voice call's server connection) stays within that limit.
`apps/api/Dockerfile` runs the same API on any container host if a persistent server is needed.

Database: apply `supabase/migrations` in order.
