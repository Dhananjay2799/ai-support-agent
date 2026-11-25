-- Guardrail checks get their own span kind in turn traces.
alter table public.traces drop constraint traces_kind_check;
alter table public.traces add constraint traces_kind_check
  check (kind in ('node', 'llm', 'tool', 'retrieval', 'guardrail', 'voice'));
