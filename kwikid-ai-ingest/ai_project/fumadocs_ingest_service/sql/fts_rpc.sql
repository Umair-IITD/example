-- =============================================================================
-- fts_rpc.sql
-- Full-Text Search RPC for Hybrid Retrieval — real ts_rank scores
-- =============================================================================
-- Run AFTER fts_setup.sql (which creates the fts tsvector column and GIN index).
--
-- Why this RPC?
--   PostgREST's .text_search() filter does not expose PostgreSQL ts_rank scores —
--   it only performs the filter, returning results in undefined order.
--   This RPC calls ts_rank() directly, providing accurate relevance scores for
--   Reciprocal Rank Fusion with the semantic search leg.
--
-- Dictionary choice: 'simple' instead of 'english'
--   - 'english' stems and strips stop words — bad for acronyms (OTP→otp, KYC→kyc is fine,
--     but "in" gets stripped, "processing" → "process", etc.)
--   - 'simple' preserves exact token forms — better for support queries containing
--     exact error codes, acronyms, ticket IDs, and technical identifiers.
--   - Trade-off: 'simple' won't match "verify" with "verification" (no stemming).
--     For support ticket search this is acceptable since users typically use exact terms.
--
-- Requires: fts_setup.sql applied first (creates the fts column and GIN index).
-- =============================================================================

CREATE OR REPLACE FUNCTION public.search_documents_fts(
    p_query_text  text,
    p_match_count integer DEFAULT 20
)
RETURNS TABLE (
    id       uuid,
    content  text,
    metadata jsonb,
    ts_rank  double precision
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT
        d.id,
        d.content,
        d.metadata,
        ts_rank(
            d.fts,
            websearch_to_tsquery('simple', p_query_text),
            -- Normalization option 1: divide rank by 1 + logarithm of document length.
            -- Prevents very long documents from dominating despite having many term matches.
            1
        )::double precision AS ts_rank
    FROM public.documents d
    WHERE
        d.fts IS NOT NULL
        AND d.fts @@ websearch_to_tsquery('simple', p_query_text)
    ORDER BY ts_rank DESC
    LIMIT p_match_count;
$$;

GRANT EXECUTE ON FUNCTION public.search_documents_fts(text, integer)
    TO authenticated, service_role, anon;

-- =============================================================================
-- Verification Queries
-- =============================================================================

-- Confirm function exists:
-- SELECT proname, pronargs, pg_get_function_arguments(oid)
-- FROM pg_proc
-- WHERE proname = 'search_documents_fts' AND pronamespace = 'public'::regnamespace;

-- Smoke-test (use an actual term from your documents):
-- SELECT id, ts_rank, left(content, 80) AS snippet
-- FROM search_documents_fts('OTP verification failed', 5);

-- Verify ts_rank ordering is sensible (should decrease monotonically):
-- SELECT id, ts_rank FROM search_documents_fts('PAN card mismatch', 10)
-- ORDER BY ts_rank DESC;

-- =============================================================================
-- Optional: Update fts column to use 'simple' dictionary
-- =============================================================================
-- If fts_setup.sql was run with 'english' dictionary and you want to switch to
-- 'simple' for better acronym handling, run this to rebuild:
--
-- ALTER TABLE documents DROP COLUMN IF EXISTS fts;
-- ALTER TABLE documents
--   ADD COLUMN fts tsvector
--   GENERATED ALWAYS AS (
--     to_tsvector('simple', coalesce(content, ''))
--   ) STORED;
-- CREATE INDEX documents_fts_gin_idx ON documents USING gin(fts);
-- -- This rebuilds the index — may take several minutes on large tables.
-- =============================================================================
