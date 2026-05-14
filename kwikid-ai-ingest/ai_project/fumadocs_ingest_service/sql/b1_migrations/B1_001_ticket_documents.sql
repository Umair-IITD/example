-- =============================================================================
-- B1_001_ticket_documents.sql
-- Phase B1: Core ticket document and chunk tables for the RAG ingestion system
--
-- Design rationale:
--   - rag_ticket_documents: one row per canonical ticket; normalized source of truth
--   - rag_ticket_chunks:    one row per embedded chunk; all retrieval-critical fields
--                           are real columns (not JSONB) for index efficiency and RLS
--   - Tenant isolation enforced via column-level constraint + RLS policy
--   - Deterministic UUIDs (uuid5) allow safe idempotent upserts
-- =============================================================================

-- Enable pgvector if not already enabled
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================================================================
-- TABLE: rag_ticket_documents
-- One row per canonical support ticket (post-preprocessing)
-- =============================================================================
CREATE TABLE IF NOT EXISTS public.rag_ticket_documents (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Source identity
    ticket_id           TEXT NOT NULL,          -- Freshdesk ticket ID (string)
    source_file         TEXT NOT NULL,          -- Which file this came from
    source_type         TEXT NOT NULL DEFAULT 'freshdesk',

    -- Tenant isolation (CRITICAL — never null)
    client              TEXT NOT NULL,          -- Normalized slug: unity_bank, rbl_bank, etc.

    -- Ticket classification
    query_type          TEXT,                   -- OTP Not Received, Video Related, etc.
    issue_area          TEXT,                   -- Sub-category
    environment         TEXT,                   -- production, uat, staging
    priority            TEXT,                   -- low, medium, high, urgent
    status              TEXT,                   -- open, closed, resolved

    -- Automation classification (from preprocessing)
    automation_label    TEXT NOT NULL,          -- AUTO_REPLY, HUMAN_REVIEW, ESCALATION
    escalation_flag     BOOLEAN NOT NULL DEFAULT FALSE,

    -- Knowledge signals
    has_rca             BOOLEAN NOT NULL DEFAULT FALSE,
    has_sop             BOOLEAN NOT NULL DEFAULT FALSE,
    sop_status          TEXT,                   -- SOP Present, No SOP Available, No SOP Required
    rca_quality_score   SMALLINT DEFAULT 0      -- 0–100; from preprocessing rca_quality_scorer
        CHECK (rca_quality_score >= 0 AND rca_quality_score <= 100),

    -- Document content
    subject             TEXT,
    document_text       TEXT NOT NULL,          -- Fully constructed RAG document text
    content_hash        TEXT NOT NULL,          -- SHA-256 of document_text; used for dedup

    -- Ticket metrics
    agent_interactions  SMALLINT DEFAULT 0,
    handling_time_mins  INTEGER,
    issue_recurrence    TEXT,                   -- Recurring issue / First time issue
    resolution_status   TEXT,                   -- Within SLA / SLA Violated

    -- Timestamps
    ticket_created_at   TIMESTAMPTZ,            -- When ticket was created in Freshdesk
    ticket_resolved_at  TIMESTAMPTZ,            -- When ticket was closed/resolved
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Ingestion tracking
    ingestion_run_id    TEXT,                   -- UUID of the ingestion run that created this
    index_version       TEXT NOT NULL DEFAULT 'v1',

    -- Uniqueness: one canonical row per ticket + source file
    CONSTRAINT rag_ticket_documents_ticket_source_unique
        UNIQUE (ticket_id, source_file)
);

-- Ensure updates to updated_at happen automatically
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS rag_ticket_documents_updated_at ON public.rag_ticket_documents;
CREATE TRIGGER rag_ticket_documents_updated_at
    BEFORE UPDATE ON public.rag_ticket_documents
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- =============================================================================
-- TABLE: rag_ticket_chunks
-- One row per embedded chunk derived from rag_ticket_documents
-- All retrieval-critical fields are real columns for indexing + RLS
-- =============================================================================
CREATE TABLE IF NOT EXISTS public.rag_ticket_chunks (
    id                  UUID PRIMARY KEY,       -- Deterministic UUID5 for idempotent upserts

    -- Parent document link
    document_id         UUID REFERENCES public.rag_ticket_documents(id) ON DELETE CASCADE,

    -- Source identity (denormalized for fast filter without JOIN)
    ticket_id           TEXT NOT NULL,
    source_type         TEXT NOT NULL DEFAULT 'freshdesk',

    -- Tenant isolation (CRITICAL — enforced at application AND database layer)
    client              TEXT NOT NULL,

    -- Chunk position
    chunk_index         SMALLINT NOT NULL,
    chunk_type          TEXT NOT NULL,          -- ISSUE_HEADER | QUERY_BODY | RESOLUTION_RCA
    chunk_total         SMALLINT NOT NULL DEFAULT 1,

    -- Content
    content             TEXT NOT NULL,
    word_count          SMALLINT NOT NULL DEFAULT 0,
    content_hash        TEXT NOT NULL,          -- SHA-256 of content; used for dedup

    -- Embedding (1536-dim for text-embedding-3-small / text-embedding-ada-002)
    embedding           VECTOR(1536),

    -- Retrieval filter columns (all indexed)
    automation_label    TEXT NOT NULL,          -- AUTO_REPLY | HUMAN_REVIEW | ESCALATION
    escalation_flag     BOOLEAN NOT NULL DEFAULT FALSE,
    query_type          TEXT,
    issue_area          TEXT,
    environment         TEXT,
    has_rca             BOOLEAN NOT NULL DEFAULT FALSE,
    has_sop             BOOLEAN NOT NULL DEFAULT FALSE,
    rca_quality_score   SMALLINT DEFAULT 0,

    -- Extended metadata in JSONB for low-cardinality fields
    extra_metadata      JSONB DEFAULT '{}'::JSONB,

    -- Timestamps
    ticket_created_at   TIMESTAMPTZ,
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Ingestion tracking
    ingestion_run_id    TEXT,
    index_version       TEXT NOT NULL DEFAULT 'v1',

    -- Uniqueness: deterministic by ticket + chunk position + index version
    CONSTRAINT rag_ticket_chunks_unique_position
        UNIQUE (ticket_id, chunk_index, index_version)
);

-- =============================================================================
-- Comments
-- =============================================================================
COMMENT ON TABLE public.rag_ticket_documents IS
    'Phase B1: Canonical ticket documents constructed from Freshdesk exports. '
    'One row per ticket. Source of truth for ticket metadata and deduplication.';

COMMENT ON TABLE public.rag_ticket_chunks IS
    'Phase B1: Embedded chunks derived from rag_ticket_documents. '
    'Each chunk carries all retrieval-critical metadata as real columns. '
    'Tenant isolation enforced via client column + RLS policy.';

COMMENT ON COLUMN public.rag_ticket_chunks.chunk_type IS
    'ISSUE_HEADER: short metadata-rich header chunk for classification. '
    'QUERY_BODY: customer complaint text — primary semantic match target. '
    'RESOLUTION_RCA: resolution steps + RCA — primary answer generation source.';

COMMENT ON COLUMN public.rag_ticket_chunks.embedding IS
    'text-embedding-3-small (1536-dim) or text-embedding-ada-002. '
    'IVFFlat index created in B1_006_indexes.sql after sufficient row count.';
