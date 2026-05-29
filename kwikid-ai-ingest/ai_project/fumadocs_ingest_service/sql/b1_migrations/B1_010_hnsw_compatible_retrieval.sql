-- B1_010_hnsw_compatible_retrieval.sql
--
-- HNSW-compatible semantic retrieval functions for rag_ticket_chunks and rag_sop_chunks.
--
-- Problem with match_all_b1_sources (B1_005):
--   UNION ALL + ORDER BY boosted_score DESC prevents the HNSW indexes
--   (B1_006_indexes.sql) from being used — Postgres falls back to a
--   sequential scan across both tables.
--
-- Fix: two separate functions, each using:
--   ORDER BY embedding <=> p_query_embedding  (raw column — HNSW activates)
--   LIMIT p_match_count                        (early stop — HNSW terminates)
-- No UNION ALL. No distance threshold in SQL. Threshold applied in Python.
--
-- Activation (after running this migration in Supabase):
--   Set B1_HNSW_V2_ENABLED=true in .env
--   The Python fallback to match_all_b1_sources remains if v2 RPCs are absent.
--
-- Prerequisite:  B1_006_indexes.sql (HNSW indexes must exist)
-- Does NOT touch: match_all_b1_sources (B1_005) — rollback is instant via env flag.

-- ─────────────────────────────────────────────────────────────────────────────
-- Function 1: HNSW-compatible ticket chunk retrieval
-- ─────────────────────────────────────────────────────────────────────────────
-- Key design choices:
--   * ORDER BY rtc.embedding <=> p_query_embedding  → HNSW index scan activates
--   * LIMIT p_match_count                            → scan terminates early
--   * No similarity threshold in WHERE               → no forced full scan
--   * automation_label != 'ESCALATION' filter preserved (non-distance, safe)
--   * Same output schema as match_all_b1_sources so _row_to_chunk() is unchanged

CREATE OR REPLACE FUNCTION public.match_b1_ticket_chunks_v2(
    p_query_embedding  VECTOR(1536),
    p_client           TEXT,
    p_match_count      INTEGER  DEFAULT 40,
    p_index_version    TEXT     DEFAULT 'v1'
)
RETURNS TABLE (
    id              UUID,
    source_table    TEXT,
    ticket_id       TEXT,
    sop_id          TEXT,
    chunk_type      TEXT,
    content         TEXT,
    similarity      FLOAT,
    boosted_score   FLOAT,
    has_rca         BOOLEAN,
    has_sop         BOOLEAN,
    extra_metadata  JSONB
)
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT
        rtc.id,
        'rag_ticket_chunks'::TEXT                                   AS source_table,
        rtc.ticket_id,
        NULL::TEXT                                                   AS sop_id,
        rtc.chunk_type,
        rtc.content,
        (1 - (rtc.embedding <=> p_query_embedding))::FLOAT          AS similarity,
        (1 - (rtc.embedding <=> p_query_embedding))::FLOAT          AS boosted_score,
        rtc.has_rca,
        rtc.has_sop,
        rtc.extra_metadata
    FROM public.rag_ticket_chunks rtc
    WHERE rtc.client           = p_client
      AND rtc.index_version    = p_index_version
      AND rtc.embedding        IS NOT NULL
      AND rtc.automation_label != 'ESCALATION'
    ORDER BY rtc.embedding <=> p_query_embedding
    LIMIT p_match_count;
$$;

-- ─────────────────────────────────────────────────────────────────────────────
-- Function 2: HNSW-compatible SOP chunk retrieval
-- ─────────────────────────────────────────────────────────────────────────────
-- Key design choices:
--   * ORDER BY sc.embedding <=> p_query_embedding   → HNSW index scan activates
--   * EXISTS subquery for is_active check            → non-distance filter, HNSW-safe
--   * SOP boost (+0.15) computed in SELECT           → preserved; no WHERE distance filter
--   * Same output schema as match_all_b1_sources

CREATE OR REPLACE FUNCTION public.match_b1_sop_chunks_v2(
    p_query_embedding  VECTOR(1536),
    p_client           TEXT,
    p_match_count      INTEGER  DEFAULT 40,
    p_index_version    TEXT     DEFAULT 'v1'
)
RETURNS TABLE (
    id              UUID,
    source_table    TEXT,
    ticket_id       TEXT,
    sop_id          TEXT,
    chunk_type      TEXT,
    content         TEXT,
    similarity      FLOAT,
    boosted_score   FLOAT,
    has_rca         BOOLEAN,
    has_sop         BOOLEAN,
    extra_metadata  JSONB
)
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT
        sc.id,
        'rag_sop_chunks'::TEXT                                                  AS source_table,
        NULL::TEXT                                                               AS ticket_id,
        sc.sop_id,
        'SOP_STEPS'::TEXT                                                        AS chunk_type,
        sc.content,
        (1 - (sc.embedding <=> p_query_embedding))::FLOAT                       AS similarity,
        LEAST(1.0, (1 - (sc.embedding <=> p_query_embedding)) + 0.15)::FLOAT   AS boosted_score,
        FALSE                                                                    AS has_rca,
        TRUE                                                                     AS has_sop,
        '{}'::JSONB                                                              AS extra_metadata
    FROM public.rag_sop_chunks sc
    WHERE sc.embedding      IS NOT NULL
      AND sc.index_version  = p_index_version
      AND (sc.clients = '{}' OR p_client = ANY(sc.clients))
      AND EXISTS (
          SELECT 1
          FROM public.rag_sop_library sl
          WHERE sl.sop_id    = sc.sop_id
            AND sl.is_active = TRUE
      )
    ORDER BY sc.embedding <=> p_query_embedding
    LIMIT p_match_count;
$$;

-- ─────────────────────────────────────────────────────────────────────────────
-- Grants (mirror B1_005 grants)
-- ─────────────────────────────────────────────────────────────────────────────
GRANT EXECUTE ON FUNCTION public.match_b1_ticket_chunks_v2 TO authenticated;
GRANT EXECUTE ON FUNCTION public.match_b1_ticket_chunks_v2 TO service_role;
GRANT EXECUTE ON FUNCTION public.match_b1_sop_chunks_v2    TO authenticated;
GRANT EXECUTE ON FUNCTION public.match_b1_sop_chunks_v2    TO service_role;
