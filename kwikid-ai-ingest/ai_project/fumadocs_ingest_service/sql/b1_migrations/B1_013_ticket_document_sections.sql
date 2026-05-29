-- =============================================================================
-- B1_013_ticket_document_sections.sql
-- Add semantic section columns to rag_ticket_documents
--
-- Why these columns:
--   reingest_v2.py reads per-section text to produce typed chunks
--   (ISSUE_HEADER / QUERY_BODY / RESOLUTION_RCA). Without these columns
--   stored on the document row, reingest must re-derive them from
--   document_text, losing section boundaries.
--
-- Migration is fully idempotent (ADD COLUMN IF NOT EXISTS).
-- Existing rows get NULL values; reingest_v2.py handles NULL gracefully
-- by falling back to document_text + generic QUERY_BODY chunk type.
--
-- Rollback:
--   ALTER TABLE public.rag_ticket_documents
--       DROP COLUMN IF EXISTS issue_header_text,
--       DROP COLUMN IF EXISTS query_body_text,
--       DROP COLUMN IF EXISTS resolution_rca_text;
-- =============================================================================

ALTER TABLE public.rag_ticket_documents
    ADD COLUMN IF NOT EXISTS issue_header_text   TEXT,
    ADD COLUMN IF NOT EXISTS query_body_text     TEXT,
    ADD COLUMN IF NOT EXISTS resolution_rca_text TEXT;

-- =============================================================================
-- Column comments
-- =============================================================================
COMMENT ON COLUMN public.rag_ticket_documents.issue_header_text IS
    'Short metadata-rich header section: ticket_id, query_type, issue_area, '
    'environment, priority, SOP/RCA flags. Populated by TicketDocumentBuilder. '
    'NULL on rows ingested before B1_013 migration; backfilled by reingest_v2.py.';

COMMENT ON COLUMN public.rag_ticket_documents.query_body_text IS
    'Customer complaint text extracted from the ticket subject + conversation body. '
    'Primary semantic match target. NULL on pre-migration rows.';

COMMENT ON COLUMN public.rag_ticket_documents.resolution_rca_text IS
    'Resolution steps and root cause analysis text. May be empty string if the '
    'ticket had no meaningful RCA. NULL on pre-migration rows.';

-- =============================================================================
-- Optional: backfill index so reingest_v2 can efficiently filter NULL rows
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_rag_ticket_docs_section_null
    ON public.rag_ticket_documents (client, index_version)
    WHERE issue_header_text IS NULL;
