# RAG and confidence guardrails

Results from [`evals/results.md`](../evals/results.md) (40 labeled questions: 25 answerable, 10 off-topic, 5 on-topic
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
