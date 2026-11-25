# Observability

Press **Traces** in the header, or the latency and cost under any reply. Each turn, typed or spoken,
is a tree of spans:

```text
turn                                   text · 6 model calls · $0.00045
├─ model call      gpt-6-luna · 1040 → 23 tok   prompt, output, TTFT, cached tokens, cost
├─ tool: verify_customer                Priya Shah · Pro · active
├─ tool: search_policies                high confidence · 4 passages
│  ├─ hybrid search                     query, top passages with cosine and both ranks
│  │  └─ embedding                      text-embedding-3-small · 10 tokens
│  └─ confidence check                  band, decision, reason
│     └─ support judge                  verdict, supporting passage ids
├─ tool: issue_refund                   $29.00 refunded · re_…
└─ model call      gpt-6-luna            the answer
```

A `Tracer` rides along with the turn in a context variable, like the usage meter, so the model
client, tools, retrieval and judge each open their own span without being handed anything, and
spans nest by context. The whole trace is streamed just before the turn's `done` event, saved to
the `traces` table, and readable again at `GET /sessions/{session_id}/traces`. A model call span
holds the exact instructions and input it was given (long fields clipped), its output and tool
calls, token counts including cached and reasoning tokens, time to first token and cost. Voice
turns add a span for waiting on the transcript, and a call reports the seconds GPT-Live billed
and their cost ($0.05 per active minute, $0.02 on standby).

The drawer adds up the session: cost (agent and voice), turns, latency, tokens, model calls and
recoveries, and shows each turn as a waterfall; click any span for its details.

## Error recovery

Recovery is explicit, so it can be seen and tested rather than happening inside an SDK:

| Failure | What happens | Where it shows |
| --- | --- | --- |
| Model timeout, dropped connection, 5xx | One retry after a short backoff | A failed `model call` span (`timed out → retry`), then a good one |
| Rate limit (429) | Retry honoring `Retry-After`, then `gpt-5.4-mini` as a fallback | Two failed spans, then the fallback model's |
| Transient database error in a tool | The tool is retried once (safe: refunds are idempotent, tickets dedupe) | The step reads "recovered after a retry" |
| A tool keeps failing | The model is told it's unavailable; it apologizes and hands over | Failed step, a ticket, attempts in the span |
| Anything else mid-turn | A typed, retryable `error` event, then `done`, never a hung stream | Retry button on the reply |

A call that has already streamed text is never retried, since the customer would see words twice.
The fallback model doesn't get the main model's encrypted reasoning, which only replays on the
model that produced it.

**Show it on demand.** In demo mode the drawer can inject one failure into the next message: a
model timeout, a rate limit, a database blip or an account-system outage. It goes through the
same recovery code as a real failure (`app/observability/faults.py`), and each path has a test
and passes against the real model.

## Abuse protection

The demo is public and every message spends OpenAI credit, so the API limits use before any
model is called (`app/limits.py`):

| Limit | Default | Response |
| --- | --- | --- |
| Messages per conversation | 20 a minute | `429` with `Retry-After` |
| Messages per network (IP) | 150 an hour | `429` with `Retry-After` |
| Voice calls per network | 8 an hour (each capped at 3 minutes) | `429` with `Retry-After` |
| Demo resets per network | 12 an hour | `429` with `Retry-After` |
| Daily spend, agent turns and voice | $5 (UTC day) | `503` until midnight UTC |

Counters are fixed-window rows in Postgres (`public.hit_rate_limit`), since serverless instances
don't share memory; client IPs are stored only as salted hashes. Spend is summed from the root
span every turn and voice call already stores with its cost. Limits are checked in a dependency,
before a stream starts, so the browser gets a real status code and the reason, which the UI
shows. If the limiter's database is unreachable it lets requests through rather than taking the
demo down. All limits are settings (see `.env.example`).
