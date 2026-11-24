-- LangGraph checkpoint tables (langgraph-checkpoint-postgres 3.x, migrations 0-9).
--
-- Created here instead of by AsyncPostgresSaver.setup() at runtime, so they are
-- versioned with the rest of the schema and locked down like every other table.
-- checkpoint_migrations is filled in, so setup() would find nothing left to do.
-- (Indexes are created without CONCURRENTLY: the tables are new and empty.)

create table if not exists public.checkpoint_migrations (
  v integer primary key
);

create table if not exists public.checkpoints (
  thread_id text not null,
  checkpoint_ns text not null default '',
  checkpoint_id text not null,
  parent_checkpoint_id text,
  type text,
  checkpoint jsonb not null,
  metadata jsonb not null default '{}',
  primary key (thread_id, checkpoint_ns, checkpoint_id)
);

create table if not exists public.checkpoint_blobs (
  thread_id text not null,
  checkpoint_ns text not null default '',
  channel text not null,
  version text not null,
  type text not null,
  blob bytea,
  primary key (thread_id, checkpoint_ns, channel, version)
);

create table if not exists public.checkpoint_writes (
  thread_id text not null,
  checkpoint_ns text not null default '',
  checkpoint_id text not null,
  task_id text not null,
  idx integer not null,
  channel text not null,
  type text,
  blob bytea not null,
  task_path text not null default '',
  primary key (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

create index if not exists checkpoints_thread_id_idx on public.checkpoints (thread_id);
create index if not exists checkpoint_blobs_thread_id_idx on public.checkpoint_blobs (thread_id);
create index if not exists checkpoint_writes_thread_id_idx on public.checkpoint_writes (thread_id);

insert into public.checkpoint_migrations (v)
select generate_series(0, 9)
on conflict (v) do nothing;

alter table public.checkpoint_migrations enable row level security;
alter table public.checkpoints enable row level security;
alter table public.checkpoint_blobs enable row level security;
alter table public.checkpoint_writes enable row level security;

revoke all on public.checkpoint_migrations, public.checkpoints, public.checkpoint_blobs,
  public.checkpoint_writes from anon, authenticated;
