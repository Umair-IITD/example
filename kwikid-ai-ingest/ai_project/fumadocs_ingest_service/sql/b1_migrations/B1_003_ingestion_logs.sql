-- =============================================================================
-- B1_003_ingestion_logs.sql
-- Phase B1: Ingestion run tracking and audit logs
--
-- Design rationale:
--   - Every ingestion run (full or delta) gets a UUID run_id
--   - Metrics per run: documents, chunks, embeddings, failures
--   - distribution_snapshot JSONB captures per-client and per-label counts
--   - Enables monitoring, replay on failure, and trend analysis
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.rag_ingestion_logs (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Run identity
    run_id                  TEXT NOT NULL UNIQUE,   -- UUID string set by pipeline
    run_mode                TEXT NOT NULL,          -- full | delta | gold | sop
    source_file             TEXT,                   -- Input file path
    triggered_by            TEXT DEFAULT 'cli',     -- cli | webhook | scheduler | manual

    -- Timing
    started_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at            TIMESTAMPTZ,
    duration_seconds        FLOAT,

    -- Document-level metrics
    total_source_rows       INTEGER DEFAULT 0,      -- Raw rows in source file
    documents_processed     INTEGER DEFAULT 0,      -- After filtering
    documents_skipped       INTEGER DEFAULT 0,      -- Below quality threshold
    documents_failed        INTEGER DEFAULT 0,

    -- Chunk-level metrics
    chunks_created          INTEGER DEFAULT 0,
    chunks_updated          INTEGER DEFAULT 0,
    chunks_skipped          INTEGER DEFAULT 0,      -- Hash-identical, no change
    chunks_failed           INTEGER DEFAULT 0,

    -- Embedding metrics
    embeddings_generated    INTEGER DEFAULT 0,
    embedding_api_calls     INTEGER DEFAULT 0,
    embedding_tokens_used   INTEGER DEFAULT 0,

    -- Distribution snapshots (for trend analysis)
    automation_label_counts JSONB DEFAULT '{}'::JSONB,
    -- e.g. {"AUTO_REPLY": 845, "HUMAN_REVIEW": 1078, "ESCALATION": 533}

    client_counts           JSONB DEFAULT '{}'::JSONB,
    -- e.g. {"unity_bank": 1800, "rbl_bank": 200, "bank_of_baroda": 183}

    query_type_counts       JSONB DEFAULT '{}'::JSONB,
    -- e.g. {"OTP Not Received": 221, "Video Related": 826, ...}

    -- Status
    status                  TEXT NOT NULL DEFAULT 'RUNNING',  -- RUNNING | COMPLETED | FAILED | PARTIAL
    error_count             INTEGER DEFAULT 0,
    error_summary           TEXT,                   -- First N error messages for debugging
    warnings                JSONB DEFAULT '[]'::JSONB,

    -- Tracking
    index_version           TEXT NOT NULL DEFAULT 'v1',
    git_commit_sha          TEXT,                   -- If SOP ingest: commit hash of SOP source

    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- TABLE: rag_ingestion_errors
-- Per-document error details from failed ingestion runs
-- Separate table to avoid bloating the main log with unbounded error text
-- =============================================================================
CREATE TABLE IF NOT EXISTS public.rag_ingestion_errors (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id          TEXT NOT NULL,              -- Links to rag_ingestion_logs.run_id
    ticket_id       TEXT,
    error_stage     TEXT NOT NULL,              -- document_build | chunking | embedding | upsert
    error_type      TEXT NOT NULL,              -- exception class name
    error_message   TEXT NOT NULL,
    error_detail    JSONB DEFAULT '{}'::JSONB,  -- Extra context (chunk_index, etc.)
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.rag_ingestion_logs IS
    'Phase B1: One row per ingestion run. Tracks document/chunk/embedding counts, '
    'distribution snapshots, status, and errors. Queryable for monitoring dashboards.';

COMMENT ON TABLE public.rag_ingestion_errors IS
    'Phase B1: Per-document error details from failed ingestion. '
    'Kept separate from rag_ingestion_logs to avoid unbounded row size.';
