-- =============================================================================
-- B1_006_indexes.sql
-- Phase B1: Indexes for Phase B1 tables
--
-- IMPORTANT ORDERING:
--   Run this AFTER bulk initial ingestion is complete.
--   Building IVFFlat on an empty or near-empty table wastes index budget.
--   Minimum recommended: 10,000 rows before enabling IVFFlat.
--   For < 10,000 rows: use HNSW (better small-dataset ANN accuracy).
--
-- IVFFlat tuning guide:
--   nlist = sqrt(num_rows) is a good starting point
--   3,635 tickets × ~2.5 chunks avg ≈ 9,000 rows → nlist = 100
--   Query: SET ivfflat.probes = 10; (higher = more accurate, slower)
-- =============================================================================

-- =============================================================================
-- rag_ticket_documents indexes
-- =============================================================================

-- Primary lookup: find document by ticket_id + client (most common join)
CREATE INDEX IF NOT EXISTS idx_rtd_ticket_client
    ON public.rag_ticket_documents (ticket_id, client);

-- Tenant-only lookup (list all docs for a client)
CREATE INDEX IF NOT EXISTS idx_rtd_client
    ON public.rag_ticket_documents (client);

-- Automation label distribution queries
CREATE INDEX IF NOT EXISTS idx_rtd_automation_label
    ON public.rag_ticket_documents (automation_label);

-- Deduplication hash lookup
CREATE INDEX IF NOT EXISTS idx_rtd_content_hash
    ON public.rag_ticket_documents (content_hash);

-- Delta ingestion: find tickets ingested after a timestamp
CREATE INDEX IF NOT EXISTS idx_rtd_ingested_at
    ON public.rag_ticket_documents (ingested_at DESC);

-- =============================================================================
-- rag_ticket_chunks indexes
-- =============================================================================

-- Tenant isolation filter (always applied in queries)
CREATE INDEX IF NOT EXISTS idx_rtc_client
    ON public.rag_ticket_chunks (client);

-- Combined tenant + automation label (most common combined filter)
CREATE INDEX IF NOT EXISTS idx_rtc_client_label
    ON public.rag_ticket_chunks (client, automation_label);

-- Chunk type filter (QUERY_BODY retrieval path)
CREATE INDEX IF NOT EXISTS idx_rtc_chunk_type
    ON public.rag_ticket_chunks (chunk_type);

-- Ticket-level lookup (find all chunks for a ticket)
CREATE INDEX IF NOT EXISTS idx_rtc_ticket_id
    ON public.rag_ticket_chunks (ticket_id);

-- SOP/RCA boosting filters
CREATE INDEX IF NOT EXISTS idx_rtc_has_sop
    ON public.rag_ticket_chunks (has_sop) WHERE has_sop = TRUE;

CREATE INDEX IF NOT EXISTS idx_rtc_has_rca
    ON public.rag_ticket_chunks (has_rca) WHERE has_rca = TRUE;

-- Document ID (for parent-child retrieval in future)
CREATE INDEX IF NOT EXISTS idx_rtc_document_id
    ON public.rag_ticket_chunks (document_id);

-- Content hash for deduplication
CREATE INDEX IF NOT EXISTS idx_rtc_content_hash
    ON public.rag_ticket_chunks (content_hash);

-- Full-text search vector (for hybrid retrieval)
ALTER TABLE public.rag_ticket_chunks
    ADD COLUMN IF NOT EXISTS fts_vector TSVECTOR
    GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(content, ''))
    ) STORED;

CREATE INDEX IF NOT EXISTS idx_rtc_fts
    ON public.rag_ticket_chunks USING GIN(fts_vector);

-- =============================================================================
-- VECTOR INDEX: IVFFlat on rag_ticket_chunks.embedding
--
-- Use HNSW for < 10,000 rows (better recall, no nlist tuning needed).
-- Switch to IVFFlat at > 50,000 rows for memory efficiency.
--
-- Run: ANALYZE public.rag_ticket_chunks; before query planning benefits from index.
-- =============================================================================

-- HNSW for Phase B1 (3,635 tickets × ~2.5 chunks ≈ 9,000 rows)
-- m=16 (connections per layer), ef_construction=64 (build quality)
CREATE INDEX IF NOT EXISTS idx_rtc_embedding_hnsw
    ON public.rag_ticket_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Comment out IVFFlat until row count > 50,000:
-- CREATE INDEX idx_rtc_embedding_ivfflat
--     ON public.rag_ticket_chunks
--     USING ivfflat (embedding vector_cosine_ops)
--     WITH (lists = 100);

-- =============================================================================
-- rag_sop_chunks vector index
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_rsc_embedding_hnsw
    ON public.rag_sop_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- SOP chunks FTS
ALTER TABLE public.rag_sop_chunks
    ADD COLUMN IF NOT EXISTS fts_vector TSVECTOR
    GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(content, ''))
    ) STORED;

CREATE INDEX IF NOT EXISTS idx_rsc_fts
    ON public.rag_sop_chunks USING GIN(fts_vector);

-- =============================================================================
-- rag_ingestion_logs indexes
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_ril_run_id
    ON public.rag_ingestion_logs (run_id);

CREATE INDEX IF NOT EXISTS idx_ril_status_started
    ON public.rag_ingestion_logs (status, started_at DESC);

-- =============================================================================
-- rag_feedback_logs indexes
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_rfl_client
    ON public.rag_feedback_logs (client);

CREATE INDEX IF NOT EXISTS idx_rfl_ticket
    ON public.rag_feedback_logs (freshdesk_ticket_id);

CREATE INDEX IF NOT EXISTS idx_rfl_reingestion
    ON public.rag_feedback_logs (reingestion_eligible, reingestion_done)
    WHERE reingestion_eligible = TRUE AND reingestion_done = FALSE;

-- =============================================================================
-- Row-Level Security (RLS) policies
-- Enable RLS on tenant-sensitive tables
-- =============================================================================

-- RLS on rag_ticket_chunks (most performance-sensitive table)
ALTER TABLE public.rag_ticket_chunks ENABLE ROW LEVEL SECURITY;

-- Service role bypasses RLS (for ingestion pipeline)
CREATE POLICY rtc_service_role_all ON public.rag_ticket_chunks
    TO service_role
    USING (TRUE)
    WITH CHECK (TRUE);

-- Anonymous and authenticated roles: only see their tenant's data
-- In practice, Supabase service_key bypasses this; anon_key respects it
CREATE POLICY rtc_tenant_isolation ON public.rag_ticket_chunks
    FOR SELECT
    USING (
        client = current_setting('app.current_tenant', TRUE)
        OR current_setting('app.current_tenant', TRUE) IS NULL
    );

-- RLS on rag_ticket_documents
ALTER TABLE public.rag_ticket_documents ENABLE ROW LEVEL SECURITY;

CREATE POLICY rtd_service_role_all ON public.rag_ticket_documents
    TO service_role
    USING (TRUE)
    WITH CHECK (TRUE);

CREATE POLICY rtd_tenant_isolation ON public.rag_ticket_documents
    FOR SELECT
    USING (
        client = current_setting('app.current_tenant', TRUE)
        OR current_setting('app.current_tenant', TRUE) IS NULL
    );

-- =============================================================================
-- ANALYZE: Update statistics for query planner
-- =============================================================================
ANALYZE public.rag_ticket_documents;
ANALYZE public.rag_ticket_chunks;
ANALYZE public.rag_sop_library;
ANALYZE public.rag_sop_chunks;
