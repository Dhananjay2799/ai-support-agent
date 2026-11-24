-- Initial schema for the Acme Cloud support agent.
--
-- Access model: only the API's server-side connection reads or writes these tables.
-- RLS is enabled on every table with no policies, and API roles lose their grants,
-- so the public Data API (anon / authenticated keys) can read nothing.

create extension if not exists vector with schema extensions;

-- ---------------------------------------------------------------------------
-- Mock CRM and billing
-- ---------------------------------------------------------------------------

create table public.customers (
  id bigint generated always as identity primary key,
  public_id text not null unique,
  persona_key text unique,                -- demo persona switcher key, null for real customers
  full_name text not null,
  email text not null,
  zip_code text not null,
  tier text not null check (tier in ('free', 'pro', 'enterprise')),
  contract_status text not null check (contract_status in ('active', 'past_due', 'canceled')),
  renews_on date,
  created_at timestamptz not null default now()
);
create unique index customers_email_lower_key on public.customers (lower(email));

create table public.charges (
  id bigint generated always as identity primary key,
  public_id text not null unique,
  customer_id bigint not null references public.customers (id) on delete cascade,
  amount numeric(10, 2) not null check (amount > 0),
  currency text not null default 'usd',
  description text not null,
  status text not null check (status in ('succeeded', 'failed', 'refunded', 'partially_refunded')),
  created_at timestamptz not null default now()
);
create index charges_customer_id_created_at_idx on public.charges (customer_id, created_at desc);

create table public.refunds (
  id bigint generated always as identity primary key,
  public_id text not null unique,
  charge_id bigint not null references public.charges (id) on delete cascade,
  customer_id bigint not null references public.customers (id) on delete cascade,
  amount numeric(10, 2) not null check (amount > 0),
  reason text not null,
  idempotency_key text not null unique,   -- a retried tool call can never refund twice
  status text not null default 'succeeded' check (status in ('pending', 'succeeded', 'failed')),
  created_at timestamptz not null default now()
);
create index refunds_charge_id_idx on public.refunds (charge_id);
create index refunds_customer_id_idx on public.refunds (customer_id);

create table public.tickets (
  id bigint generated always as identity primary key,
  public_id text generated always as ('ESC-' || (1000 + id)::text) stored unique,
  customer_id bigint references public.customers (id) on delete set null,
  session_id uuid,
  category text not null check (
    category in ('refund_over_limit', 'policy_exception', 'low_confidence', 'verification_failed',
                 'tool_failure', 'customer_request', 'other')
  ),
  priority text not null default 'normal' check (priority in ('low', 'normal', 'high', 'urgent')),
  summary text not null,
  status text not null default 'open' check (status in ('open', 'in_progress', 'resolved')),
  created_at timestamptz not null default now()
);
create index tickets_customer_id_idx on public.tickets (customer_id);
create index tickets_session_id_idx on public.tickets (session_id);

-- ---------------------------------------------------------------------------
-- Knowledge base (RAG)
-- ---------------------------------------------------------------------------

create table public.kb_chunks (
  id bigint generated always as identity primary key,
  doc_id text not null,
  chunk_index integer not null check (chunk_index >= 0),
  doc_title text not null,
  section_path text not null,             -- breadcrumb, e.g. 'Refunds and billing > Refund limits by plan'
  content text not null,
  tiers text[] not null check (tiers <@ array['free', 'pro', 'enterprise']::text[] and cardinality(tiers) > 0),
  doc_type text not null check (doc_type in ('policy', 'troubleshooting', 'reference')),
  effective_date date not null,
  version text not null,
  token_count integer not null check (token_count > 0),
  embedding extensions.vector(1536),
  fts tsvector generated always as (to_tsvector('english', section_path || ' ' || content)) stored,
  created_at timestamptz not null default now(),
  unique (doc_id, chunk_index)
);
create index kb_chunks_embedding_hnsw_idx on public.kb_chunks
  using hnsw (embedding extensions.vector_cosine_ops);
create index kb_chunks_fts_idx on public.kb_chunks using gin (fts);
create index kb_chunks_tiers_idx on public.kb_chunks using gin (tiers);

-- ---------------------------------------------------------------------------
-- Observability
-- ---------------------------------------------------------------------------

create table public.traces (
  id bigint generated always as identity primary key,
  session_id uuid not null,
  turn_id text not null,
  span_id text not null,
  parent_span_id text,
  name text not null,
  kind text not null check (kind in ('node', 'llm', 'tool', 'retrieval', 'voice')),
  start_ms double precision not null,
  end_ms double precision,
  attributes jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index traces_session_id_created_at_idx on public.traces (session_id, created_at);
create index traces_turn_id_idx on public.traces (turn_id);

-- ---------------------------------------------------------------------------
-- Lock down the public Data API: server-side access only
-- ---------------------------------------------------------------------------

alter table public.customers enable row level security;
alter table public.charges enable row level security;
alter table public.refunds enable row level security;
alter table public.tickets enable row level security;
alter table public.kb_chunks enable row level security;
alter table public.traces enable row level security;

revoke all on public.customers, public.charges, public.refunds, public.tickets,
  public.kb_chunks, public.traces from anon, authenticated;
