-- =============================================================================
-- B1_005_rpc_functions.sql
-- Phase B1: PostgreSQL RPC functions for vector search with tenant isolation
--
-- Functions:
--   match_ticket_chunks      — primary ANN search on rag_ticket_chunks
--   match_sop_chunks         — ANN search on rag_sop_library/rag_sop_chunks
--   match_all_b1_sources     — unified search across tickets + SOPs
--
-- Design principles:
--   - p_client is NOT NULL in all functions → tenant isolation is mandatory
--   - automation_label = ESCALATION is always excluded from auto-reply path
--   - SOP chunks can be fetched with global (empty clients[]) fallback
-- =============================================================================

-- =============================================================================
-- FUNCTION: match_ticket_chunks
-- Primary vector search for support ticket chunks.
-- Enforces tenant isolation via p_client parameter.
-- =============================================================================
CREATE OR REPLACE FUNCTION public.match_ticket_chunks(
    p_query_embedding   VECTOR(1536),
    p_client            TEXT,           -- REQUIRED: tenant slug (e.g. unity_bank)
    p_match_count       INTEGER DEFAULT 10,
    p_match_threshold   FLOAT   DEFAULT 0.25,
    p_exclude_escalation BOOLEAN DEFAULT TRUE,
    p_chunk_types       TEXT[]  DEFAULT NULL,   -- NULL = all; ['QUERY_BODY'] = only QUERY_BODY
    p_query_types       TEXT[]  DEFAULT NULL,   -- NULL = all query types
    p_has_rca           BOOLEAN DEFAULT NULL,   -- NULL = ignore; TRUE = only with RCA
    p_has_sop           BOOLEAN DEFAULT NULL,
    p_index_version     TEXT    DEFAULT 'v1'
)
RETURNS TABLE (
    id                  UUID,
    ticket_id           TEXT,
    chunk_type          TEXT,
    content             TEXT,
    similarity          FLOAT,
    automation_label    TEXT,
    query_type          TEXT,
    issue_area          TEXT,
    has_rca             BOOLEAN,
    has_sop             BOOLEAN,
    rca_quality_score   SMALLINT,
    extra_metadata      JSONB,
    ticket_created_at   TIMESTAMPTZ
)
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT
        rtc.id,
        rtc.ticket_id,
        rtc.chunk_type,
        rtc.content,
        (1 - (rtc.embedding <=> p_query_embedding))::FLOAT AS similarity,
        rtc.automation_label,
        rtc.query_type,
        rtc.issue_area,
        rtc.has_rca,
        rtc.has_sop,
        rtc.rca_quality_score,
        rtc.extra_metadata,
        rtc.ticket_created_at
    FROM public.rag_ticket_chunks rtc
    WHERE
        rtc.client = p_client
        AND rtc.index_version = p_index_version
        AND rtc.embedding IS NOT NULL
        AND (1 - (rtc.embedding <=> p_query_embedding)) >= p_match_threshold
        AND (NOT p_exclude_escalation OR rtc.automation_label != 'ESCALATION')
        AND (p_chunk_types IS NULL OR rtc.chunk_type = ANY(p_chunk_types))
        AND (p_query_types IS NULL OR rtc.query_type = ANY(p_query_types))
        AND (p_has_rca IS NULL OR rtc.has_rca = p_has_rca)
        AND (p_has_sop IS NULL OR rtc.has_sop = p_has_sop)
    ORDER BY rtc.embedding <=> p_query_embedding
    LIMIT p_match_count;
$$;

-- =============================================================================
-- FUNCTION: match_sop_chunks
-- Vector search on SOP library. Falls back to global SOPs if client has no
-- specific SOPs for the query type.
-- =============================================================================
CREATE OR REPLACE FUNCTION public.match_sop_chunks(
    p_query_embedding   VECTOR(1536),
    p_client            TEXT,           -- REQUIRED: tenant slug
    p_match_count       INTEGER DEFAULT 5,
    p_match_threshold   FLOAT   DEFAULT 0.30,
    p_query_type        TEXT    DEFAULT NULL,
    p_index_version     TEXT    DEFAULT 'v1'
)
RETURNS TABLE (
    id              UUID,
    sop_id          TEXT,
    chunk_heading   TEXT,
    content         TEXT,
    similarity      FLOAT,
    query_type      TEXT,
    clients         TEXT[],
    sop_version     INTEGER
)
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT
        sc.id,
        sc.sop_id,
        sc.chunk_heading,
        sc.content,
        (1 - (sc.embedding <=> p_query_embedding))::FLOAT AS similarity,
        sc.query_type,
        sc.clients,
        sc.sop_version
    FROM public.rag_sop_chunks sc
    JOIN public.rag_sop_library sl ON sl.sop_id = sc.sop_id
    WHERE
        sc.embedding IS NOT NULL
        AND sc.index_version = p_index_version
        AND sl.is_active = TRUE
        AND (1 - (sc.embedding <=> p_query_embedding)) >= p_match_threshold
        -- Tenant filter: global SOPs (empty clients) OR client-specific SOPs
        AND (sc.clients = '{}' OR p_client = ANY(sc.clients))
        AND (p_query_type IS NULL OR sc.query_type = p_query_type)
    ORDER BY sc.embedding <=> p_query_embedding
    LIMIT p_match_count;
$$;

-- =============================================================================
-- FUNCTION: match_all_b1_sources
-- Unified search across ticket chunks AND SOP chunks.
-- Returns results with source_table tag for context assembly ordering.
-- SOP chunks are boosted by 0.15 similarity bonus to rank them higher.
-- =============================================================================
CREATE OR REPLACE FUNCTION public.match_all_b1_sources(
    p_query_embedding   VECTOR(1536),
    p_client            TEXT,
    p_match_count       INTEGER DEFAULT 10,
    p_match_threshold   FLOAT   DEFAULT 0.25,
    p_index_version     TEXT    DEFAULT 'v1'
)
RETURNS TABLE (
    id              UUID,
    source_table    TEXT,   -- 'rag_ticket_chunks' | 'rag_sop_chunks'
    ticket_id       TEXT,
    sop_id          TEXT,
    chunk_type      TEXT,
    content         TEXT,
    similarity      FLOAT,
    boosted_score   FLOAT,  -- similarity + 0.15 for SOP chunks
    has_rca         BOOLEAN,
    has_sop         BOOLEAN,
    extra_metadata  JSONB
)
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    -- Ticket chunks
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
    WHERE
        rtc.client = p_client
        AND rtc.index_version = p_index_version
        AND rtc.embedding IS NOT NULL
        AND rtc.automation_label != 'ESCALATION'
        AND (1 - (rtc.embedding <=> p_query_embedding)) >= p_match_threshold

    UNION ALL

    -- SOP chunks (boosted +0.15)
    SELECT
        sc.id,
        'rag_sop_chunks'::TEXT                                      AS source_table,
        NULL::TEXT                                                   AS ticket_id,
        sc.sop_id,
        'SOP_STEPS'::TEXT                                            AS chunk_type,
        sc.content,
        (1 - (sc.embedding <=> p_query_embedding))::FLOAT           AS similarity,
        LEAST(1.0, (1 - (sc.embedding <=> p_query_embedding)) + 0.15)::FLOAT AS boosted_score,
        FALSE                                                        AS has_rca,
        TRUE                                                         AS has_sop,
        '{}'::JSONB                                                  AS extra_metadata
    FROM public.rag_sop_chunks sc
    JOIN public.rag_sop_library sl ON sl.sop_id = sc.sop_id
    WHERE
        sc.embedding IS NOT NULL
        AND sc.index_version = p_index_version
        AND sl.is_active = TRUE
        AND (sc.clients = '{}' OR p_client = ANY(sc.clients))
        AND (1 - (sc.embedding <=> p_query_embedding)) >= p_match_threshold

    ORDER BY boosted_score DESC
    LIMIT p_match_count;
$$;

-- =============================================================================
-- FUNCTION: get_ingestion_run_summary
-- Returns summary stats for a given run_id (for CLI output + monitoring)
-- =============================================================================
CREATE OR REPLACE FUNCTION public.get_ingestion_run_summary(p_run_id TEXT)
RETURNS TABLE (
    run_id              TEXT,
    status              TEXT,
    run_mode            TEXT,
    documents_processed INTEGER,
    chunks_created      INTEGER,
    chunks_skipped      INTEGER,
    embeddings_generated INTEGER,
    duration_seconds    FLOAT,
    automation_labels   JSONB,
    client_distribution JSONB,
    error_count         INTEGER
)
LANGUAGE sql STABLE AS $$
    SELECT
        run_id, status, run_mode,
        documents_processed, chunks_created, chunks_skipped,
        embeddings_generated, duration_seconds,
        automation_label_counts, client_counts,
        error_count
    FROM public.rag_ingestion_logs
    WHERE run_id = p_run_id;
$$;
