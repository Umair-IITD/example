-- =============================================================================
-- CRITICAL: Canonicalize match_documents RPC — run once in Supabase SQL Editor
-- =============================================================================
--
-- Problem: Two (or three) conflicting overloads of match_documents exist:
--   • 2-arg (vector, integer)           — returns id TEXT, no threshold filter
--   • 3-arg (vector, integer, float)    — DEFAULT 0.5 threshold, strict >, id UUID
--   • kb_chunks.sql overload            — same signature but queries kb_chunks table
--
-- These conflicts cause:
--   1. PGRST203 "Multiple Choices" → Python falls back to 2-arg (no threshold)
--   2. kb_chunks overload may silently query a different (possibly empty) table
--   3. 3-arg default threshold 0.5 silently discards all matches below 0.5 similarity
--
-- This script:
--   1. Drops ALL existing match_documents overloads (covers both signature variants)
--   2. Creates ONE canonical function targeting public.documents
--   3. Default threshold = 0.0 (no filtering by default — Python handles thresholds)
--   4. Inclusive >= filter (not strict >)
--   5. Returns UUID id (matches documents.id column type)
--   6. SECURITY DEFINER + fixed search_path to prevent schema-injection
-- =============================================================================

-- Drop all known overloads (cover both vector type qualifications)
DROP FUNCTION IF EXISTS public.match_documents(public.vector,    integer, double precision);
DROP FUNCTION IF EXISTS public.match_documents(public.vector,    integer);
DROP FUNCTION IF EXISTS public.match_documents(vector,           integer, double precision);
DROP FUNCTION IF EXISTS public.match_documents(vector,           integer);

-- Create ONE canonical match_documents function
CREATE OR REPLACE FUNCTION public.match_documents(
    query_embedding public.vector(1536),
    match_count     integer          DEFAULT 10,
    match_threshold double precision DEFAULT 0.0
)
RETURNS TABLE (
    id         uuid,
    content    text,
    metadata   jsonb,
    similarity double precision
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
        (1.0 - (d.embedding <=> query_embedding))::double precision AS similarity
    FROM public.documents d
    WHERE d.embedding IS NOT NULL
      AND (1.0 - (d.embedding <=> query_embedding)) >= match_threshold
    ORDER BY d.embedding <=> query_embedding
    LIMIT match_count;
$$;

-- Grant execute to the roles Supabase uses
GRANT EXECUTE ON FUNCTION public.match_documents(public.vector, integer, double precision)
    TO authenticated, service_role, anon;

-- =============================================================================
-- Verification — run these after migration
-- =============================================================================

-- 1. Confirm exactly ONE overload exists:
--    SELECT proname, pronargs, pg_get_function_arguments(oid), prosrc
--    FROM pg_proc
--    WHERE proname = 'match_documents'
--      AND pronamespace = 'public'::regnamespace;
--    Expected: 1 row, 3 args, queries public.documents

-- 2. Smoke-test (replace zero vector with actual embedding in real usage):
--    SELECT id, similarity
--    FROM match_documents(array_fill(0.0, ARRAY[1536])::vector, 3, 0.0)
--    LIMIT 3;
--    Expected: 3 rows (or fewer if table is small), no error

-- 3. Check documents table has rows:
--    SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM public.documents;

-- 4. Check index_version distribution in indexed data:
--    SELECT metadata->>'index_version' AS iv, COUNT(*) AS cnt
--    FROM public.documents
--    GROUP BY iv
--    ORDER BY cnt DESC;
--    Expected: all rows show 'v1' (or whatever WRITE_INDEX_VERSION was set to during ingestion)
-- =============================================================================
