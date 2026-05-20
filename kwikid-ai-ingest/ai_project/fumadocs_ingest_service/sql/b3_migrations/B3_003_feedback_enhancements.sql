-- =============================================================================
-- B3_003_feedback_enhancements.sql
-- Phase B3: Extend rag_feedback_logs + create rag_review_queue
--
-- These changes are additive (ADD COLUMN IF NOT EXISTS) — existing B1/B2
-- feedback records remain valid with NULL in the new columns.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. Extend rag_feedback_logs with B3 knowledge fields
-- ---------------------------------------------------------------------------
ALTER TABLE rag_feedback_logs
    ADD COLUMN IF NOT EXISTS knowledge_article_id  TEXT,         -- FK to rag_knowledge_articles if feedback produced a VERIFIED_REPLY
    ADD COLUMN IF NOT EXISTS feedback_source       TEXT          -- 'freshdesk_webhook' | 'api' | 'manual'
                                                   DEFAULT 'freshdesk_webhook';

COMMENT ON COLUMN rag_feedback_logs.knowledge_article_id IS 'Set when APPROVED/EDITED feedback is ingested as a VERIFIED_REPLY knowledge article.';
COMMENT ON COLUMN rag_feedback_logs.feedback_source       IS 'Origin of the feedback signal.';

-- ---------------------------------------------------------------------------
-- 2. rag_review_queue — human review workflow table
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rag_review_queue (
    id                     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Source feedback record
    feedback_log_id        UUID REFERENCES rag_feedback_logs(id) ON DELETE SET NULL,

    -- Context
    client                 TEXT        NOT NULL,
    ticket_id              TEXT        NOT NULL,
    query_text             TEXT        NOT NULL,

    -- AI draft (pre-review)
    ai_draft               TEXT        NOT NULL,

    -- Human-reviewed content (set during review)
    final_response         TEXT,                -- NULL until reviewed

    -- Review status: PENDING | IN_REVIEW | APPROVED | REJECTED
    review_status          TEXT        NOT NULL DEFAULT 'PENDING',
    reviewer_id            TEXT,                -- agent identifier

    -- Asana integration (optional — graceful degradation if not configured)
    asana_task_id          TEXT,
    asana_task_url         TEXT,

    -- Timestamps
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at            TIMESTAMPTZ,

    -- Knowledge ingestion tracking
    ingested_as_knowledge  BOOLEAN     NOT NULL DEFAULT FALSE,
    knowledge_article_id   TEXT        REFERENCES rag_knowledge_articles(article_id) ON DELETE SET NULL
);

COMMENT ON TABLE  rag_review_queue IS 'Phase B3: Human review workflow. Reviewed items with final_response can be ingested as VERIFIED_REPLY knowledge articles.';
COMMENT ON COLUMN rag_review_queue.ingested_as_knowledge IS 'TRUE once this reviewed response has been ingested to rag_knowledge_chunks.';

-- Indexes
CREATE INDEX IF NOT EXISTS idx_rrq_client
    ON rag_review_queue(client);

CREATE INDEX IF NOT EXISTS idx_rrq_status
    ON rag_review_queue(review_status);

CREATE INDEX IF NOT EXISTS idx_rrq_created_at
    ON rag_review_queue(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_rrq_asana
    ON rag_review_queue(asana_task_id)
    WHERE asana_task_id IS NOT NULL;

-- RLS
ALTER TABLE rag_review_queue ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'rag_review_queue' AND policyname = 'rrq_service_role_all'
    ) THEN
        CREATE POLICY rrq_service_role_all ON rag_review_queue
            FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);
    END IF;
END$$;
