# AI Support Agent

A customer support agent for **Acme Cloud**, a fictional file-sync SaaS. It answers in streaming
chat and live voice with one shared memory. It verifies the customer and acts on their account
through chained tool calls, grounds every answer in a policy knowledge base, and hands anything
it can't support to a person. Every turn is traced, and failures are recovered visibly.

**[Live demo](https://web-pied-five-42.vercel.app)** · [API health](https://api-five-pied-65.vercel.app/health) ·
Loom walkthrough: _link to come_

## Try it

Pick a customer in the header; the demo signs them in.

| Customer | Try | What happens |
| --- | --- | --- |
| Priya (Pro) | "I was charged twice this month" | Verifies her, reads the account, checks policy, refunds $29, cites the rule |
| Priya | The same again, or "Can I pay with PayPal?" | No second refund; PayPal isn't documented, so a person gets a ticket |
| Marcus (Free) | A refund for his storage add-on | Declined, citing the Free-plan rule |
| Elena (Enterprise) | Refund her $1,200 charge | Over the $500 limit: a high-priority ticket with a 4-hour SLA |
| Tom (Pro, past due) | Refund last month's charge | Declined until the account is in good standing |
| Guest | Ask for a refund | Must verify by email and ZIP; three misses lock the chat |

Then press the **phone** button and say the same things; interrupt it mid-answer. Open **Traces**
to see every model call, tool and check, with latency, tokens and cost, and use **Simulate a
failure** to watch a timeout, rate limit or outage get recovered. **Reset demo** restores the data.

## What the brief asked for

| Requirement | How | Proof |
| --- | --- | --- |
| Streaming chat with step indicators | SSE over POST; each tool streams a step with its result | Every event typed end to end ([contract](apps/api/app/contracts/events.py)) |
| Live voice: waveform, transcripts, interruption | GPT-Live over WebRTC; the server joins over a sideband and runs the same agent | Real call with synthesized speech, [docs/voice.md](docs/voice.md) |
| Observability drawer | Per-turn span tree: prompts, outputs, latency, TTFT, tokens, cost | [docs/observability.md](docs/observability.md) |
| RAG, custom chunking, confidence guardrails | Heading-aware chunks, hybrid pgvector + full-text search, a similarity gate and support judge | 0 unsafe answers in a 40-question eval, [docs/rag.md](docs/rag.md) |
| Autonomous loop with 3+ tools | LangGraph: verify → account → policy → refund or ticket, in one turn | 10/10 end-to-end scenarios on the live API, [docs/agent.md](docs/agent.md) |
| Error recovery | Visible retries, a fallback model, tool retries, graceful outages | Each path tested and simulated live, [docs/observability.md](docs/observability.md#error-recovery) |
| Shared memory across text and voice | One Postgres-checkpointed thread per conversation | Real call test: chat, then voice, then chat recalls the refund |

## Results

- **RAG** ([evals/results.md](evals/results.md)): the right policy section is in the top 3 for
  every answerable question; **0** of 15 should-escalate questions were answered (a similarity
  cut-off alone answered 2); escalation precision and recall 100%.
- **Agent** ([evals/agent_scenarios.md](evals/agent_scenarios.md)): 10/10 scenarios against the
  deployed API with the real model, including prompt injection and asking twice for a refund.
- **Voice**: a real GPT-Live call (synthesized speech) refunds by voice, survives an
  interruption, and the chat afterwards remembers it. The voice acknowledges within a second of
  the caller finishing; a full refund answer is ready in about 5 s.
- **Tests**: 121 API (pytest, real Postgres), 19 web (vitest); CI runs lint, types, formatting,
  tests, a contract-drift check and a secret scan.

## Architecture

![Architecture: the browser streams chat and voice requests to a FastAPI service running one LangGraph agent with tools, a policy engine and hybrid RAG; voice audio goes straight to GPT-Live over WebRTC while the API joins the call over a sideband; OpenAI hosts the models and Supabase Postgres holds all state](docs/architecture.svg)

Audio goes straight from the browser to GPT-Live. The API creates the session and joins it over
a sideband WebSocket: GPT-Live hands each spoken request to the API, which runs it through the same
agent, tools, policy engine, memory and tracing as a typed one, and sends back the answer to say.

**Stack:** Next.js 16, React 19, Tailwind and shadcn/ui · FastAPI, LangGraph, Pydantic · OpenAI
Responses API, GPT-Live and embeddings · Supabase Postgres with pgvector · Vercel.

## Design decisions

- **The model proposes, code decides.** Refund limits, windows, eligibility and "keep the
  original of a duplicate pair" are enforced by a policy engine fed from `policy.yaml`, not by the
  prompt. A test fails if the knowledge base states different numbers.
- **Guardrails before answers.** Weak retrieval matches escalate without an LLM call; anything
  else must be confirmed by a judge citing retrieved passage ids, and unverified citations are
  dropped.
- **One typed event contract.** Pydantic models export a JSON Schema that generates the UI's
  TypeScript types; CI fails on drift.
- **Postgres for all state.** CRM data, vectors, agent checkpoints, traces and rate limits share
  one database, so stateless serverless functions can serve any conversation.
- **Recovery you can see.** Retries and fallbacks happen in our code, not inside an SDK, so each
  attempt is a span in the trace.
- **Server-only data access.** RLS is on with no policies and public roles have no grants; the
  browser never talks to the database, and the OpenAI key never reaches it.

## Running locally

Requirements: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22, an OpenAI API key, and
Postgres with pgvector (a Supabase project works as is).

```bash
cp .env.example apps/api/.env        # keep the API section, add OPENAI_API_KEY and DATABASE_URL
cp .env.example apps/web/.env.local  # keep the web section
make install
make dev-api   # http://localhost:8000
make dev-web   # http://localhost:3000
make check     # lint, types, formatting and tests for both apps
```

Database: on plain Postgres run `supabase/local/bootstrap.sql` first (Supabase already has what it
sets up), then apply `supabase/migrations` in order; they also seed the demo customers. Load the
knowledge base with `uv run python -m scripts.ingest_kb` from `apps/api`, or, for a deployed API
whose database you can't reach directly, `API_URL=… ADMIN_TOKEN=… uv run python -m
scripts.push_kb`. After changing `apps/api/app/contracts`, run `make contracts`.

Evaluations, from `apps/api` (they call the real models):

```bash
EVAL_DATABASE_URL=… uv run python -m scripts.run_evals       # RAG: 40 labeled questions
EVAL_DATABASE_URL=… uv run python -m scripts.run_scenarios   # agent: 10 end-to-end scenarios
uv run python -m scripts.run_scenarios --api https://…       # the same against a deployed API
EVAL_DATABASE_URL=… uv run python -m scripts.voice_e2e       # a real voice call, synthesized speech
```

## Deployment

Both apps are Vercel projects; Supabase holds the database.

| Project | Root | Environment |
| --- | --- | --- |
| API | `apps/api` | `CORS_ORIGINS`, `DATABASE_URL` (pooler), `OPENAI_API_KEY`, optional `ADMIN_TOKEN`, limits (see `.env.example`) |
| Web | `apps/web` | `NEXT_PUBLIC_API_URL` (baked in at build time) |

Functions are stateless and capped at 300 s, so state lives in Postgres and voice calls end at
3 minutes. `apps/api/Dockerfile` runs the API on any container host.

## Repository layout

```text
apps/api/    FastAPI: agent/, voice/, rag/, policy/, data/, observability/, routes/, contracts/
apps/web/    Next.js 16: components/{chat,voice,observability}, lib/ (SSE, state, generated types)
contracts/   JSON Schema exported from the API's Pydantic models
kb/          Acme Cloud policy and troubleshooting docs (the RAG source)
evals/       Labeled questions, RAG results, agent scenario results
supabase/    Migrations: schema, RLS lock-down, demo data, rate limits
docs/        Agent, RAG, voice, observability and error recovery in depth
```
