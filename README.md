# AI Support Agent

A customer support agent for **Acme Cloud** (a fictional file-sync SaaS) that answers in streaming
chat and live voice, chains tool calls to verify customers and act on their accounts, grounds every
answer in a policy knowledge base, and hands low-confidence cases to a human.

> **Status: Day 1 of 7.** The skeleton runs end to end: a typed event contract, a streaming chat
> API with a scripted multi-step turn, the chat UI, the database schema with demo personas, and the
> knowledge base. The LangGraph agent, RAG, voice and observability land over the next days.

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

## Repository layout

```text
apps/api/            FastAPI service
  app/contracts/     Pydantic models for everything streamed to the UI
  app/routes/        HTTP endpoints (chat SSE, health)
  app/agent/         Agent orchestration (Day 1: scripted turn)
  app/policy/        policy.yaml: refund limits, SLAs, verification rules
apps/web/            Next.js 16 UI
  lib/generated/     TypeScript types generated from the API's contracts
  lib/sse.ts         Incremental SSE parser for POST streams
  components/chat/   Chat panel, messages, step list
contracts/           JSON Schema exported from the Pydantic models
kb/                  Acme Cloud policy and troubleshooting docs (RAG source)
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
