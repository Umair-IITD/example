-- =============================================================================
-- B1_007_fts_setup.sql
-- Phase B1/B3: Full-Text Search columns and GIN indexes for RAG tables
--
-- Adds a generated tsvector `fts` column to:
--   - rag_ticket_chunks
--   - rag_sop_chunks
--   - rag_knowledge_chunks
--
-- Required BEFORE running B1_008_fts_rpc.sql (which creates the FTS RPC).
-- Required BEFORE enabling B1_HYBRID_RETRIEVAL_ENABLED=true in the application.
--
-- Dictionary: 'simple'
--   Preserves exact token forms — no stemming, no stop-word removal.
--   Better for KwikID support queries that contain:
--     - Acronyms: OTP, KYC, VKYC, PAN, NEFT, UPI, NACH
--     - Error codes: ERR-4021, PGRST203
--     - Technical IDs: transaction IDs, ticket numbers
--
-- Trade-off: 'simple' does NOT match "verify" with "verification".
--   This is acceptable since support users query with exact terms.
--
-- These migrations are ADDITIVE and idempotent:
--   - ADD COLUMN IF NOT EXISTS guards against re-runs
--   - CREATE INDEX IF NOT EXISTS guards against re-runs
--   - The fts column is auto-maintained by PostgreSQL on every INSERT/UPDATE
--
-- After running:
--   1. Run B1_008_fts_rpc.sql to create the search_b1_sources_fts() function
--   2. Set B1_HYBRID_RETRIEVAL_ENABLED=true to activate hybrid retrieval
-- =============================================================================

-- =============================================================================
-- 1. rag_ticket_chunks
-- =============================================================================

ALTER TABLE public.rag_ticket_chunks
    ADD COLUMN IF NOT EXISTS fts tsvector
    GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(content, ''))
    ) STORED;

CREATE INDEX IF NOT EXISTS rag_ticket_chunks_fts_gin_idx
    ON public.rag_ticket_chunks
    USING gin(fts);

-- =============================================================================
-- 2. rag_sop_chunks
-- =============================================================================

ALTER TABLE public.rag_sop_chunks
    ADD COLUMN IF NOT EXISTS fts tsvector
    GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(content, ''))
    ) STORED;

CREATE INDEX IF NOT EXISTS rag_sop_chunks_fts_gin_idx
    ON public.rag_sop_chunks
    USING gin(fts);

-- =============================================================================
-- 3. rag_knowledge_chunks
-- =============================================================================

ALTER TABLE public.rag_knowledge_chunks
    ADD COLUMN IF NOT EXISTS fts tsvector
    GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(content, ''))
    ) STORED;

CREATE INDEX IF NOT EXISTS rag_knowledge_chunks_fts_gin_idx
    ON public.rag_knowledge_chunks
    USING gin(fts);

-- =============================================================================
-- Verification Queries (run after migration to confirm setup)
-- =============================================================================

-- Confirm all three fts columns exist:
-- SELECT table_name, column_name, data_type
-- FROM information_schema.columns
-- WHERE table_name IN ('rag_ticket_chunks', 'rag_sop_chunks', 'rag_knowledge_chunks')
--   AND column_name = 'fts'
-- ORDER BY table_name;

-- Confirm GIN indexes exist:
-- SELECT tablename, indexname
-- FROM pg_indexes
-- WHERE tablename IN ('rag_ticket_chunks', 'rag_sop_chunks', 'rag_knowledge_chunks')
--   AND indexname LIKE '%_fts_gin_idx'
-- ORDER BY tablename;

-- Test ticket chunks FTS (replace term with one from your data):
-- SELECT id, ts_rank(fts, websearch_to_tsquery('simple', 'KYC verification')) AS rank,
--        left(content, 100) AS snippet
-- FROM rag_ticket_chunks
-- WHERE fts @@ websearch_to_tsquery('simple', 'KYC verification')
-- ORDER BY rank DESC
-- LIMIT 5;

-- Test SOP chunks FTS:
-- SELECT id, ts_rank(fts, websearch_to_tsquery('simple', 'OTP expired')) AS rank,
--        left(content, 100) AS snippet
-- FROM rag_sop_chunks
-- WHERE fts @@ websearch_to_tsquery('simple', 'OTP expired')
-- ORDER BY rank DESC
-- LIMIT 5;

-- =============================================================================
-- Notes on dictionary choice
-- =============================================================================
-- To switch to English stemming (matches "verify" ↔ "verification"):
-- 1. Drop the generated column: ALTER TABLE <table> DROP COLUMN IF EXISTS fts;
-- 2. Recreate with 'english': ALTER TABLE <table> ADD COLUMN fts tsvector
--    GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED;
-- 3. Recreate the GIN index.
-- This is a separate migration — run after validating 'simple' recall in production.
-- =============================================================================
