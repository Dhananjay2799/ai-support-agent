-- Better lexical matching for the knowledge base.
--
-- 1. Index error codes like "E-409" as one token. The default parser splits it into
--    'e' and '-409', so every code matches every other ('e' is everywhere). Folding
--    "E-409" to "E409" before tokenizing keeps the code intact. The retriever folds
--    the question the same way (app/rag/retriever.py).
-- 2. Weight the section breadcrumb (A) above the body (B): a heading is a strong
--    signal of what a chunk is about.

alter table public.kb_chunks drop column fts;  -- drops kb_chunks_fts_idx with it

alter table public.kb_chunks add column fts tsvector generated always as (
  setweight(to_tsvector('english', regexp_replace(section_path, '\m([A-Za-z])-(\d{3})\M', '\1\2', 'g')), 'A')
  || setweight(to_tsvector('english', regexp_replace(content, '\m([A-Za-z])-(\d{3})\M', '\1\2', 'g')), 'B')
) stored;

create index kb_chunks_fts_idx on public.kb_chunks using gin (fts);
