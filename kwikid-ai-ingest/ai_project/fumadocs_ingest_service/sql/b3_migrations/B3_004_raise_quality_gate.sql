-- =============================================================================
-- B3_004_raise_quality_gate.sql
-- Raise knowledge-chunk quality gate from 0.40 → 0.55.
--
-- WHY: B3_KNOWLEDGE_MIN_QUALITY_SCORE was set to 0.55 in .env after initial
-- ingestion ran at 0.40. This leaves 5 chunks with quality_score ∈ [0.40, 0.55)
-- that the Python ingestion pipeline would reject today, but the RPC still serves.
-- This migration brings the SQL gate in sync with the Python setting.
--
-- EFFECTS:
--   1. Deletes rag_knowledge_chunks rows with quality_score < 0.55 (soft removal
--      via parent article FK ON DELETE CASCADE is NOT triggered — we delete chunks
--      only, leaving the parent articles intact for potential future re-embedding
--      if the gate is lowered again).
--   2. Updates match_all_b1_sources RPC: Branch 3 WHERE clause raised to 0.55.
--
-- SAFE TO RE-RUN: The DELETE is idempotent (nothing to delete on second run).
-- The RPC is replaced with CREATE OR REPLACE.
--
-- Prerequisites: B3_001, B3_002, B3_003 must already be applied.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. Remove chunks that fall below the new quality gate
-- ---------------------------------------------------------------------------
-- Preview (run first to confirm count before deletion):
--   SELECT COUNT(*) FROM rag_knowledge_chunks WHERE quality_score < 0.55;
--
DELETE FROM rag_knowledge_chunks
WHERE quality_score < 0.55;

-- Log the cleanup
DO $$
BEGIN
    RAISE NOTICE 'B3_004: Deleted rag_knowledge_chunks rows with quality_score < 0.55';
END$$;

-- ---------------------------------------------------------------------------
-- 2. Update match_all_b1_sources RPC — raise Branch 3 gate to 0.55
-- ---------------------------------------------------------------------------
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
    -- RAISED from 0.40 → 0.55 to match B3_KNOWLEDGE_MIN_QUALITY_SCORE in .env.
    -- Chunks below 0.55 were removed by the DELETE above.
    SELECT
        rkc.id,
        'rag_knowledge_chunks'::TEXT                             AS source_table,
        NULL::TEXT                                               AS ticket_id,
        rkc.article_id                                          AS sop_id,
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

GRANT EXECUTE ON FUNCTION match_all_b1_sources(VECTOR, TEXT, INTEGER, FLOAT, TEXT)
    TO service_role;

COMMENT ON FUNCTION match_all_b1_sources IS
'Phase B3 update (B3_004): knowledge_chunk quality gate raised 0.40→0.55 to match B3_KNOWLEDGE_MIN_QUALITY_SCORE=0.55 in .env. Same signature — all callers backward-compatible.';
