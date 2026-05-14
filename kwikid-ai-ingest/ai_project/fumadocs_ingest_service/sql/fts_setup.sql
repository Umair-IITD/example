-- =============================================================================
-- Full-Text Search Setup for Hybrid Retrieval
-- =============================================================================
-- Run this script once in the Supabase SQL editor to enable keyword search.
-- This is ADDITIVE — existing pgvector search is not affected.
--
-- After applying:
--   - Set HYBRID_RETRIEVAL_ENABLED=true in .env to activate hybrid mode.
--   - The 'fts' column is auto-maintained by PostgreSQL on insert/update.
-- =============================================================================

-- Step 1: Add a generated tsvector column for full-text search
-- Uses the 'english' dictionary for stemming and stop-word removal.
-- coalesce ensures NULL content doesn't break the index.
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS fts tsvector
  GENERATED ALWAYS AS (
    to_tsvector('english', coalesce(content, ''))
  ) STORED;

-- Step 2: Create a GIN index on the fts column for fast text search
-- GIN (Generalized Inverted Index) is the standard index for tsvector.
CREATE INDEX IF NOT EXISTS documents_fts_gin_idx
  ON documents
  USING gin(fts);

-- =============================================================================
-- Verification Queries (run after migration to confirm setup)
-- =============================================================================

-- Verify the column exists
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'documents' AND column_name = 'fts';

-- Test a sample full-text search (replace 'pan mismatch' with your test query)
SELECT id, ts_rank(fts, websearch_to_tsquery('english', 'pan mismatch')) AS rank,
       left(content, 100) AS snippet
FROM documents
WHERE fts @@ websearch_to_tsquery('english', 'pan mismatch')
ORDER BY rank DESC
LIMIT 5;

-- =============================================================================
-- Optional: Custom dictionary for KYC/fintech acronyms
-- =============================================================================
-- If standard English stemming strips meaningful acronyms (e.g., VKYC → vkyc),
-- consider using 'simple' dictionary which preserves exact token forms:
--
-- ALTER TABLE documents
--   DROP COLUMN IF EXISTS fts;
--
-- ALTER TABLE documents
--   ADD COLUMN fts tsvector
--   GENERATED ALWAYS AS (
--     to_tsvector('simple', coalesce(content, ''))
--   ) STORED;
--
-- CREATE INDEX documents_fts_simple_gin_idx
--   ON documents USING gin(fts);
-- =============================================================================
