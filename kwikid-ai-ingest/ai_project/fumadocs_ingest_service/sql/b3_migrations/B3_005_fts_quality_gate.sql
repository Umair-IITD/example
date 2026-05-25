-- =============================================================================
-- B3_005_fts_quality_gate.sql
-- Raise knowledge-chunk quality gate in search_b1_sources_fts from 0.40 → 0.55.
--
-- WHY:
--   B3_004 raised the gate in match_all_b1_sources (semantic search RPC) to 0.55,
--   and the Python ingestion pipeline (B3_KNOWLEDGE_MIN_QUALITY_SCORE=0.55) now
--   refuses to ingest chunks below 0.55. However B1_008 (the FTS / keyword search
--   RPC) still carries the old hardcoded 0.40 threshold:
--
--     AND kc.quality_score >= 0.40    -- ← stale after B3_004
--
--   This means the FTS path returns knowledge chunks that the semantic path
--   excludes, creating retrieval drift between the two legs of the hybrid RRF
--   pipeline. Both paths must enforce the same gate.
--
-- EFFECT:
--   Recreates search_b1_sources_fts with quality_score >= 0.55.
--   No data is deleted — the gate change only affects query-time filtering.
--   Chunks with quality_score ∈ [0.40, 0.55) were already removed by B3_004.
--
-- SAFE TO RE-RUN: CREATE OR REPLACE is idempotent.
--
-- Prerequisites: B1_007, B1_008, B3_001, B3_004 must already be applied.
-- =============================================================================

CREATE OR REPLACE FUNCTION public.search_b1_sources_fts(
    p_query_text    TEXT,
    p_client        TEXT,
    p_match_count   INTEGER DEFAULT 20,
    p_index_version TEXT    DEFAULT 'v1'
)
RETURNS TABLE (
    id          UUID,
    content     TEXT,
    metadata    JSONB,
    chunk_type  TEXT,
    ts_rank     DOUBLE PRECISION
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    -- ── Ticket chunks (strict per-tenant) ──────────────────────────────────────
    SELECT
        rtc.id,
        rtc.content,
        jsonb_build_object(
            'source_table',   'rag_ticket_chunks',
            'ticket_id',      rtc.ticket_id,
            'chunk_type',     rtc.chunk_type,
            'has_rca',        rtc.has_rca,
            'has_sop',        rtc.has_sop,
            'query_type',     rtc.query_type,
            'client',         rtc.client
        ) AS metadata,
        rtc.chunk_type,
        ts_rank(
            rtc.fts,
            websearch_to_tsquery('simple', p_query_text),
            1
        )::DOUBLE PRECISION AS ts_rank
    FROM public.rag_ticket_chunks rtc
    WHERE
        rtc.client         = p_client
        AND rtc.index_version = p_index_version
        AND rtc.fts IS NOT NULL
        AND rtc.automation_label != 'ESCALATION'
        AND rtc.fts @@ websearch_to_tsquery('simple', p_query_text)

    UNION ALL

    -- ── SOP chunks (global or per-tenant) ──────────────────────────────────────
    SELECT
        sc.id,
        sc.content,
        jsonb_build_object(
            'source_table',   'rag_sop_chunks',
            'sop_id',         sc.sop_id,
            'chunk_heading',  sc.chunk_heading,
            'chunk_type',     'SOP_STEPS',
            'query_type',     sc.query_type,
            'clients',        sc.clients
        ) AS metadata,
        'SOP_STEPS'::TEXT AS chunk_type,
        ts_rank(
            sc.fts,
            websearch_to_tsquery('simple', p_query_text),
            1
        )::DOUBLE PRECISION AS ts_rank
    FROM public.rag_sop_chunks sc
    JOIN public.rag_sop_library sl ON sl.sop_id = sc.sop_id
    WHERE
        sc.index_version  = p_index_version
        AND sl.is_active  = TRUE
        AND sc.fts IS NOT NULL
        AND (sc.clients = '{}' OR p_client = ANY(sc.clients))
        AND sc.fts @@ websearch_to_tsquery('simple', p_query_text)

    UNION ALL

    -- ── Knowledge chunks (global or per-tenant) ─────────────────────────────────
    -- RAISED from 0.40 → 0.55 to match match_all_b1_sources (B3_004) and the
    -- B3_KNOWLEDGE_MIN_QUALITY_SCORE=0.55 environment variable.
    SELECT
        kc.id,
        kc.content,
        jsonb_build_object(
            'source_table',      'rag_knowledge_chunks',
            'article_id',        kc.article_id,
            'chunk_type',        'KNOWLEDGE',
            'knowledge_class',   kc.knowledge_class,
            'quality_score',     kc.quality_score,
            'clients',           kc.clients
        ) AS metadata,
        'KNOWLEDGE'::TEXT AS chunk_type,
        ts_rank(
            kc.fts,
            websearch_to_tsquery('simple', p_query_text),
            1
        )::DOUBLE PRECISION AS ts_rank
    FROM public.rag_knowledge_chunks kc
    JOIN public.rag_knowledge_articles ka ON ka.article_id = kc.article_id
    WHERE
        kc.index_version  = p_index_version
        AND ka.is_active  = TRUE
        AND kc.quality_score >= 0.55
        AND kc.fts IS NOT NULL
        AND (kc.clients = '{}' OR p_client = ANY(kc.clients))
        AND kc.fts @@ websearch_to_tsquery('simple', p_query_text)

    ORDER BY ts_rank DESC
    LIMIT p_match_count;
$$;

GRANT EXECUTE ON FUNCTION public.search_b1_sources_fts(TEXT, TEXT, INTEGER, TEXT)
    TO authenticated, service_role, anon;

COMMENT ON FUNCTION public.search_b1_sources_fts IS
'B3_005: knowledge_chunk quality gate raised 0.40→0.55 to match match_all_b1_sources (B3_004) and B3_KNOWLEDGE_MIN_QUALITY_SCORE=0.55 in .env. FTS and semantic paths now enforce the same quality gate.';

-- =============================================================================
-- Verification
-- =============================================================================
-- Confirm function body contains 0.55 (not 0.40):
--
-- SELECT pg_get_functiondef(oid)
-- FROM   pg_proc
-- WHERE  proname = 'search_b1_sources_fts'
--   AND  pronamespace = 'public'::regnamespace;
--
-- Smoke test (FTS should respect quality gate):
-- SELECT id, chunk_type, ts_rank, left(content, 80) AS snippet
-- FROM search_b1_sources_fts('OTP verification failed', 'unity_bank', 10, 'v1')
-- ORDER BY ts_rank DESC;
-- =============================================================================
