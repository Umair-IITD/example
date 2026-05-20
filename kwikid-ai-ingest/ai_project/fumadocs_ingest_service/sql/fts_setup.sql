-- =============================================================================
-- Full-Text Search Setup for Hybrid Retrieval
-- =============================================================================
-- Run this script once in the Supabase SQL editor to enable keyword search.
-- This is ADDITIVE — existing pgvector search is not affected.
--
-- Dictionary choice: 'simple'
--   'simple' preserves exact token forms (no stemming, no stop-word removal).
--   This is better for KwikID support queries which contain:
--     - Acronyms: OTP, KYC, VKYC, PAN, NEFT, UPI, NACH
--     - Error codes: ERR-4021, PGRST203
--     - Technical IDs: ticket IDs, API codes
--   'english' dictionary would stem "verification" → "verif", "processing" → "process",
--   and strip stop words like "in", "the" — degrading acronym and ID matching.
--
-- Trade-off: 'simple' does NOT match "verify" with "verification" (no stemming).
--   For support ticket search this is acceptable: users query with exact terms.
--
-- After applying:
--   - Run fts_rpc.sql to add the search_documents_fts() function for real ts_rank scores.
--   - Set HYBRID_RETRIEVAL_ENABLED=true in .env to activate hybrid mode.
--   - The 'fts' column is auto-maintained by PostgreSQL on insert/update.
-- =============================================================================

-- Step 1: Add a generated tsvector column for full-text search
-- Uses the 'simple' dictionary to preserve exact token forms (better for acronyms).
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS fts tsvector
  GENERATED ALWAYS AS (
    to_tsvector('simple', coalesce(content, ''))
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

-- Verify the GIN index exists
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'documents' AND indexname = 'documents_fts_gin_idx';

-- Test a sample full-text search (replace 'OTP verification' with your test query)
SELECT id, ts_rank(fts, websearch_to_tsquery('simple', 'OTP verification')) AS rank,
       left(content, 100) AS snippet
FROM documents
WHERE fts @@ websearch_to_tsquery('simple', 'OTP verification')
ORDER BY rank DESC
LIMIT 5;

-- =============================================================================
-- If you need English stemming (match "verify" with "verification"):
-- Drop and recreate using 'english' dictionary — run as a separate migration.
-- =============================================================================
-- ALTER TABLE documents DROP COLUMN IF EXISTS fts;
-- ALTER TABLE documents
--   ADD COLUMN fts tsvector
--   GENERATED ALWAYS AS (
--     to_tsvector('english', coalesce(content, ''))
--   ) STORED;
-- CREATE INDEX documents_fts_gin_idx ON documents USING gin(fts);
-- =============================================================================
