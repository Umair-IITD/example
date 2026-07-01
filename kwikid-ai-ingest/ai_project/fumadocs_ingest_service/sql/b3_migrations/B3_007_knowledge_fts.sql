-- =============================================================================
-- B3_007_knowledge_fts.sql
-- Phase B3: Add fts TSVECTOR column to rag_knowledge_chunks.
--
-- WHY:
--   search_b1_sources_fts (introduced in B3_005, extended in B3_006) references
--   kc.fts for knowledge chunks, but B3_001 never added a fts column to
--   rag_knowledge_chunks. Without this column the FTS RPC raises a SQL error
--   for the knowledge branch and returns 0 rows silently (or errors).
--
-- WHAT:
--   1. Add fts TSVECTOR GENERATED ALWAYS AS (...) STORED — auto-populated from content.
--   2. Create GIN index on fts for tsvector matching.
--   3. Update knowledge_pipeline.py ingestion to explicitly set fts on INSERT
--      if the GENERATED column is not auto-handled by Supabase PostgREST.
--      (Supabase PostgREST upsert skips GENERATED columns — the DB populates them
--      on INSERT/UPDATE automatically; no application code change needed.)
--
-- SAFE TO RE-RUN:
--   - ADD COLUMN IF NOT EXISTS is idempotent.
--   - CREATE INDEX IF NOT EXISTS is idempotent.
--
-- Prerequisites: B3_001 must already be applied (rag_knowledge_chunks must exist).
-- Apply BEFORE B3_006 if starting fresh; apply AFTER B3_006 to fix an existing schema.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. Add fts GENERATED column to rag_knowledge_chunks
-- ---------------------------------------------------------------------------
ALTER TABLE public.rag_knowledge_chunks
    ADD COLUMN IF NOT EXISTS fts TSVECTOR
        GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED;

COMMENT ON COLUMN public.rag_knowledge_chunks.fts IS
'Full-text search vector generated from content column (simple dictionary). '
'Used by search_b1_sources_fts (B3_005/B3_006) to retrieve knowledge chunks '
'via keyword search. GENERATED ALWAYS — do NOT include in INSERT statements.';

DO $$
BEGIN
    RAISE NOTICE 'B3_007: Added fts TSVECTOR GENERATED column to rag_knowledge_chunks.';
END$$;

-- ---------------------------------------------------------------------------
-- 2. GIN index for FTS queries
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_rkc_fts
    ON public.rag_knowledge_chunks USING GIN(fts);

DO $$
BEGIN
    RAISE NOTICE 'B3_007: Created GIN index idx_rkc_fts on rag_knowledge_chunks.fts.';
END$$;

-- ---------------------------------------------------------------------------
-- Verification queries (run after applying):
-- ---------------------------------------------------------------------------
--
-- 1. Confirm column exists:
--    SELECT column_name, generation_expression
--    FROM information_schema.columns
--    WHERE table_name = 'rag_knowledge_chunks'
--      AND column_name = 'fts';
--
-- 2. Confirm index exists:
--    SELECT indexname, indexdef
--    FROM pg_indexes
--    WHERE tablename = 'rag_knowledge_chunks'
--      AND indexname = 'idx_rkc_fts';
--
-- 3. After ingestion, confirm rows have fts populated:
--    SELECT COUNT(*) FROM rag_knowledge_chunks WHERE fts IS NOT NULL;
--    -- Should equal total chunk count (GENERATED column auto-populated on insert)
--
-- 4. Spot-check FTS retrieval for a knowledge chunk:
--    SELECT id, ts_rank(fts, websearch_to_tsquery('simple', 'OTP not received'), 1)
--    FROM rag_knowledge_chunks
--    WHERE fts @@ websearch_to_tsquery('simple', 'OTP not received')
--    ORDER BY 2 DESC LIMIT 5;
-- =============================================================================
