-- =============================================================================
-- B1_008_fts_rpc.sql
-- Phase B1/B3: Unified FTS RPC across all B1 RAG tables
--
-- Prerequisites:
--   - B1_007_fts_setup.sql applied (fts columns + GIN indexes exist)
--   - B1_001, B1_002, B3_001 tables must exist
--
-- Function: search_b1_sources_fts
--   Returns keyword-matched rows from rag_ticket_chunks, rag_sop_chunks,
--   and rag_knowledge_chunks in a single ranked result set.
--
-- Called by: HybridTicketRetriever._search_keyword() in Python
--   The HybridTicketRetriever uses this RPC alongside the semantic
--   match_all_b1_sources RPC, then fuses results via Reciprocal Rank Fusion.
--
-- Tenant isolation:
--   - ticket_chunks:    client = p_client (strict per-tenant)
--   - sop_chunks:       clients = '{}' OR p_client = ANY(clients) (global or tenant)
--   - knowledge_chunks: clients = '{}' OR p_client = ANY(clients) (global or tenant)
--
-- Dictionary: 'simple' (matches B1_007 — must use same dictionary as the column)
--
-- Output columns match the row format expected by HybridTicketRetriever:
--   id, content, metadata (as jsonb), chunk_type, ts_rank
--
-- Performance notes:
--   - UNION ALL (not UNION) to avoid deduplication overhead
--   - GIN indexes on all three fts columns make WHERE fts @@ ... fast
--   - p_match_count limits each leg before the outer ORDER BY + LIMIT
--     to keep the result set manageable for fusion
-- =============================================================================

CREATE OR REPLACE FUNCTION public.search_b1_sources_fts(
    p_query_text    TEXT,
    p_client        TEXT,           -- REQUIRED: tenant slug (e.g. 'unity_bank')
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
            1   -- normalization: divide by 1 + log(doc_length) to prevent long-doc bias
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
        AND ka.is_active  = TRUE        -- is_active lives on the parent article, not the chunk
        AND kc.quality_score >= 0.55    -- quality gate matches match_all_b1_sources (B3_004/B3_005)
        AND kc.fts IS NOT NULL
        AND (kc.clients = '{}' OR p_client = ANY(kc.clients))
        AND kc.fts @@ websearch_to_tsquery('simple', p_query_text)

    ORDER BY ts_rank DESC
    LIMIT p_match_count;
$$;

-- Grant access to all roles used by the application
GRANT EXECUTE ON FUNCTION public.search_b1_sources_fts(TEXT, TEXT, INTEGER, TEXT)
    TO authenticated, service_role, anon;

-- =============================================================================
-- Verification Queries
-- =============================================================================

-- Confirm function exists with correct signature:
-- SELECT proname, pg_get_function_arguments(oid)
-- FROM pg_proc
-- WHERE proname = 'search_b1_sources_fts'
--   AND pronamespace = 'public'::regnamespace;

-- Smoke test (use a term present in your data):
-- SELECT id, chunk_type, ts_rank, left(content, 80) AS snippet
-- FROM search_b1_sources_fts('OTP verification failed', 'unity_bank', 10, 'v1')
-- ORDER BY ts_rank DESC;

-- Confirm tenant isolation (should return 0 rows for wrong tenant):
-- SELECT count(*) FROM search_b1_sources_fts('KYC', 'nonexistent_tenant', 5, 'v1');

-- =============================================================================
-- Notes
-- =============================================================================
-- The quality_score >= 0.55 filter on knowledge chunks matches the threshold set
-- by B3_004_raise_quality_gate.sql (match_all_b1_sources) and the env variable
-- B3_KNOWLEDGE_MIN_QUALITY_SCORE=0.55. B3_005_fts_quality_gate.sql applies this
-- same value to already-deployed databases. If the threshold is changed again,
-- update it in all three places: here, B3_004, and B3_005.
--
-- The ESCALATION filter on ticket chunks mirrors match_all_b1_sources —
-- escalated tickets are excluded from the auto-reply path by design.
-- =============================================================================
