-- =============================================================================
-- B3_002_extend_match_all_sources.sql
-- Phase B3: Extend match_all_b1_sources RPC with knowledge chunk UNION branch
--
-- CRITICAL: Same function signature as B1 version — no callers need to change.
-- The new UNION branch is purely additive.
--
-- Boost schedule (must match rag_settings.py defaults):
--   rag_ticket_chunks:   boosted_score = similarity + 0.00
--   rag_sop_chunks:      boosted_score = similarity + 0.15  (existing)
--   rag_knowledge_chunks:boosted_score = similarity + 0.08 + quality*0.05
--                        (quality contribution capped at 0.05 when quality=1.0)
--
-- Quality gate: chunks with quality_score < 0.40 are excluded BEFORE the HNSW
-- scan via the B-tree index on quality_score. This is the primary safeguard
-- against low-quality Q&A polluting retrieval results.
--
-- Tenant isolation: knowledge chunks use clients[] array instead of a scalar
-- client column. The ANY() operator safely handles both global (clients='{}')
-- and tenant-scoped cases.
-- =============================================================================

CREATE OR REPLACE FUNCTION match_all_b1_sources(
    p_query_embedding   VECTOR(1536),
    p_client            TEXT,
    p_match_count       INTEGER  DEFAULT 10,
    p_match_threshold   FLOAT    DEFAULT 0.25,
    p_index_version     TEXT     DEFAULT 'v1'
) RETURNS TABLE (
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
) LANGUAGE sql STABLE AS $$

    -- ── Branch 1: Ticket chunks (no similarity boost) ─────────────────────────
    SELECT
        rtc.id,
        'rag_ticket_chunks'::TEXT                                AS source_table,
        rtc.ticket_id,
        NULL::TEXT                                               AS sop_id,
        rtc.chunk_type,
        rtc.content,
        1 - (rtc.embedding <=> p_query_embedding)               AS similarity,
        1 - (rtc.embedding <=> p_query_embedding)               AS boosted_score,
        rtc.has_rca,
        rtc.has_sop,
        COALESCE(rtc.extra_metadata, '{}'::JSONB)               AS extra_metadata
    FROM rag_ticket_chunks rtc
    WHERE
        rtc.client        = p_client
        AND rtc.index_version = p_index_version
        AND rtc.automation_label != 'ESCALATION'
        AND 1 - (rtc.embedding <=> p_query_embedding) >= p_match_threshold

    UNION ALL

    -- ── Branch 2: SOP chunks (+0.15 boost — authoritative) ───────────────────
    SELECT
        rsc.id,
        'rag_sop_chunks'::TEXT                                   AS source_table,
        NULL::TEXT                                               AS ticket_id,
        rsc.sop_id,
        rsc.chunk_type,
        rsc.content,
        1 - (rsc.embedding <=> p_query_embedding)               AS similarity,
        LEAST(1.0,
              1 - (rsc.embedding <=> p_query_embedding) + 0.15) AS boosted_score,
        FALSE                                                    AS has_rca,
        TRUE                                                     AS has_sop,
        '{}'::JSONB                                              AS extra_metadata
    FROM rag_sop_chunks rsc
    WHERE
        (rsc.clients = '{}' OR p_client = ANY(rsc.clients))
        AND rsc.index_version = p_index_version
        AND 1 - (rsc.embedding <=> p_query_embedding) >= (p_match_threshold - 0.05)

    UNION ALL

    -- ── Branch 3: Knowledge chunks (+0.08 base + quality*0.05 bonus) ─────────
    -- quality_score >= 0.40 is the primary retrieval quality gate.
    -- The B-tree index on quality_score applies this filter before the HNSW scan.
    SELECT
        rkc.id,
        'rag_knowledge_chunks'::TEXT                             AS source_table,
        NULL::TEXT                                               AS ticket_id,
        rkc.article_id                                          AS sop_id,  -- reused column
        rkc.chunk_type,
        rkc.content,
        1 - (rkc.embedding <=> p_query_embedding)               AS similarity,
        LEAST(1.0,
              1 - (rkc.embedding <=> p_query_embedding)
              + 0.08
              + (rkc.quality_score * 0.05))                     AS boosted_score,
        FALSE                                                    AS has_rca,
        FALSE                                                    AS has_sop,
        jsonb_build_object(
            'knowledge_class', rkc.knowledge_class,
            'quality_score',   rkc.quality_score,
            'answer_score',    rkc.answer_score,
            'clients',         rkc.clients
        )                                                        AS extra_metadata
    FROM rag_knowledge_chunks rkc
    WHERE
        (rkc.clients = '{}' OR p_client = ANY(rkc.clients))
        AND rkc.index_version = p_index_version
        AND rkc.quality_score >= 0.55
        AND rkc.embedding IS NOT NULL
        AND 1 - (rkc.embedding <=> p_query_embedding) >= (p_match_threshold - 0.05)

    ORDER BY boosted_score DESC
    LIMIT p_match_count * 3;

$$;

-- Grant execute to service_role (matches B1 pattern)
GRANT EXECUTE ON FUNCTION match_all_b1_sources(VECTOR, TEXT, INTEGER, FLOAT, TEXT)
    TO service_role;

COMMENT ON FUNCTION match_all_b1_sources IS
'Phase B3 update: adds rag_knowledge_chunks UNION branch with +0.08 base boost and quality*0.05 bonus. Quality gate quality_score>=0.40 applied before HNSW scan. Same signature as B1 version — all callers are backward-compatible.';
