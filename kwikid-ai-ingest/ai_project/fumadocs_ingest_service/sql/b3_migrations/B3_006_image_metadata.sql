-- =============================================================================
-- B3_006_image_metadata.sql
-- Phase B3: Add image_metadata JSONB column to rag_knowledge_articles and
-- expose it through the match_all_b1_sources and search_b1_sources_fts RPCs.
--
-- WHY:
--   Subagent 1 (OCR pipeline) adds image_metadata: list[dict] to KnowledgeArticle.
--   Each dict contains: image_guid, image_path, image_class, ocr_required,
--   ocr_text (optional), ocr_confidence (optional), ocr_version.
--
--   Image metadata is article-level (images belong to the post, not a chunk).
--   Chunks reference articles via FK article_id, so retrieval must JOIN to the
--   article to surface image_metadata to the agent layer.
--
-- CHANGES:
--   1. ALTER rag_knowledge_articles: add image_metadata JSONB NOT NULL DEFAULT '[]'
--   2. UPDATE match_all_b1_sources (B3_004 version): Branch 3 extra_metadata JSONB
--      now includes image_metadata via a JOIN to rag_knowledge_articles.
--   3. UPDATE search_b1_sources_fts (B3_005 version): Knowledge branch metadata
--      JSONB now includes image_metadata via the existing JOIN on ka.
--
-- SAFE TO RE-RUN:
--   - ADD COLUMN IF NOT EXISTS is idempotent.
--   - CREATE OR REPLACE for both RPCs is idempotent.
--
-- BACKWARD COMPATIBILITY:
--   - Both RPCs keep their exact RETURNS TABLE signatures.
--   - image_metadata is embedded inside the existing extra_metadata / metadata JSONB
--     fields — no new columns are added to the RPC return shape.
--   - All existing callers continue to work unchanged.
--
-- Prerequisites: B3_001, B3_002, B3_003, B3_004, B3_005 must already be applied.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. Add image_metadata column to rag_knowledge_articles
-- ---------------------------------------------------------------------------
ALTER TABLE rag_knowledge_articles
    ADD COLUMN IF NOT EXISTS image_metadata JSONB NOT NULL DEFAULT '[]'::JSONB;

COMMENT ON COLUMN rag_knowledge_articles.image_metadata IS
'OCR-structured image metadata produced by the OCR pipeline (Subagent 1). '
'Array of objects: {image_guid, image_path, image_class, ocr_required, '
'ocr_text (nullable), ocr_confidence (nullable), ocr_version}. '
'Empty array when the article contains no images or OCR has not run.';

-- GIN index for article-level image metadata queries
-- (e.g., find all articles that contain at least one image with ocr_required=true)
CREATE INDEX IF NOT EXISTS idx_rka_image_metadata
    ON rag_knowledge_articles USING GIN(image_metadata);

DO $$
BEGIN
    RAISE NOTICE 'B3_006: Added image_metadata column to rag_knowledge_articles.';
END$$;

-- ---------------------------------------------------------------------------
-- 2. Update match_all_b1_sources — Branch 3 now includes image_metadata
--    from the parent rag_knowledge_articles row via JOIN.
--    All other branches (ticket, SOP) are unchanged.
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

    -- ── Branch 3: Knowledge chunks — now includes image_metadata from parent ──
    -- JOIN to rag_knowledge_articles to fetch image_metadata at query time.
    -- image_metadata is article-scoped (all chunks share the same parent images).
    -- Quality gate: quality_score >= 0.55 (unchanged from B3_004).
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
            'knowledge_class',  rkc.knowledge_class,
            'quality_score',    rkc.quality_score,
            'answer_score',     rkc.answer_score,
            'clients',          rkc.clients,
            'image_metadata',   COALESCE(ka.image_metadata, '[]'::JSONB)
        )                                                        AS extra_metadata
    FROM rag_knowledge_chunks rkc
    JOIN rag_knowledge_articles ka ON ka.article_id = rkc.article_id
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
'Phase B3 update (B3_006): Knowledge branch now JOINs rag_knowledge_articles to '
'include image_metadata in extra_metadata JSONB. Quality gate 0.55 (from B3_004) '
'unchanged. Same return-table signature — all callers backward-compatible.';

DO $$
BEGIN
    RAISE NOTICE 'B3_006: Updated match_all_b1_sources — Branch 3 now includes image_metadata.';
END$$;

-- ---------------------------------------------------------------------------
-- 3. Update search_b1_sources_fts — Knowledge branch now includes image_metadata
--    (already JOINs ka via existing JOIN rag_knowledge_articles ka).
-- ---------------------------------------------------------------------------
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

    -- ── Knowledge chunks (global or per-tenant) — now includes image_metadata ──
    -- The existing JOIN on ka (rag_knowledge_articles) already available — we
    -- extend the metadata JSONB to include ka.image_metadata.
    -- Quality gate 0.55 unchanged from B3_005.
    SELECT
        kc.id,
        kc.content,
        jsonb_build_object(
            'source_table',      'rag_knowledge_chunks',
            'article_id',        kc.article_id,
            'chunk_type',        'KNOWLEDGE',
            'knowledge_class',   kc.knowledge_class,
            'quality_score',     kc.quality_score,
            'clients',           kc.clients,
            'image_metadata',    COALESCE(ka.image_metadata, '[]'::JSONB)
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
'B3_006: Knowledge branch metadata now includes image_metadata from parent '
'rag_knowledge_articles (already JOINed). Quality gate 0.55 (from B3_005) unchanged.';

DO $$
BEGIN
    RAISE NOTICE 'B3_006: Updated search_b1_sources_fts — Knowledge branch now includes image_metadata.';
END$$;

-- =============================================================================
-- Verification queries (run after applying this migration):
-- =============================================================================
--
-- 1. Confirm column exists:
--    SELECT column_name, data_type, column_default
--    FROM information_schema.columns
--    WHERE table_name = 'rag_knowledge_articles'
--      AND column_name = 'image_metadata';
--
-- 2. Confirm default is empty array:
--    SELECT COUNT(*) FROM rag_knowledge_articles WHERE image_metadata != '[]'::JSONB;
--    -- Should be 0 for a fresh table; non-zero after OCR ingestion runs.
--
-- 3. Confirm match_all_b1_sources returns image_metadata in extra_metadata:
--    -- (requires at least one knowledge chunk to be present)
--    SELECT extra_metadata -> 'image_metadata' AS img_meta
--    FROM match_all_b1_sources(
--        '[0.1, 0.2, ...]'::VECTOR, 'unity_bank', 1, 0.0, 'v1'
--    )
--    WHERE source_table = 'rag_knowledge_chunks'
--    LIMIT 1;
--
-- =============================================================================
