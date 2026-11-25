-- Abuse protection for the public demo: fixed-window request counters, and an index
-- for adding up today's spend from turn traces.

create table public.rate_limits (
  bucket text not null,              -- e.g. 'chat:ip:3f9a…' (client IPs are stored hashed)
  window_start timestamptz not null,
  hits integer not null default 0 check (hits >= 0),
  primary key (bucket, window_start)
);

alter table public.rate_limits enable row level security;
revoke all on public.rate_limits from anon, authenticated;

-- Count one hit in the current window and return the window's total, atomically.
create or replace function public.hit_rate_limit(p_bucket text, p_window_seconds integer)
returns integer
language sql
volatile
set search_path = ''
as $$
  insert into public.rate_limits as r (bucket, window_start, hits)
  values (
    p_bucket,
    to_timestamp(floor(extract(epoch from now()) / p_window_seconds) * p_window_seconds),
    1
  )
  on conflict (bucket, window_start) do update set hits = r.hits + 1
  returning hits;
$$;

revoke execute on function public.hit_rate_limit(text, integer) from public, anon, authenticated;

-- Every turn and voice call stores one root span with its cost; spend is summed from these.
create index traces_roots_created_at_idx on public.traces (created_at) where parent_span_id is null;
