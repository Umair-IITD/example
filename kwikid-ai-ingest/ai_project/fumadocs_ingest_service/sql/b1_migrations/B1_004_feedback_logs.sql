-- =============================================================================
-- B1_004_feedback_logs.sql
-- Phase B1: Agent feedback signals for continuous learning loop
--
-- Design rationale:
--   - Every AI-generated response that an agent sees is logged here
--   - Agent actions (APPROVED / EDITED / REJECTED) update the row
--   - edited_response and quality_score drive re-ingestion decisions
--   - retrieved_chunks JSONB stores which chunks were used → enables
--     chunk-level quality attribution in future
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.rag_feedback_logs (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Freshdesk context
    freshdesk_ticket_id     TEXT NOT NULL,
    freshdesk_thread_id     TEXT,               -- Specific conversation thread
    client                  TEXT NOT NULL,      -- Tenant

    -- Query context
    query_text              TEXT NOT NULL,      -- The incoming support query
    query_type              TEXT,
    automation_label        TEXT NOT NULL,      -- AUTO_REPLY | HUMAN_REVIEW

    -- AI generation context
    ai_response             TEXT NOT NULL,      -- The AI-generated draft response
    ai_model                TEXT,               -- e.g. gpt-4o-mini
    generation_latency_ms   FLOAT,

    -- Retrieved context
    retrieved_chunks        JSONB DEFAULT '[]'::JSONB,
    -- [{"chunk_id": "...", "ticket_id": "...", "similarity": 0.82, "chunk_type": "RESOLUTION_RCA"}, ...]

    retrieval_count         SMALLINT DEFAULT 0,
    top_similarity_score    FLOAT,
    had_sop_context         BOOLEAN DEFAULT FALSE,

    -- Agent feedback
    agent_id                TEXT,               -- Freshdesk agent ID
    human_action            TEXT,               -- APPROVED | EDITED | REJECTED | ESCALATED | PENDING
    edited_response         TEXT,               -- Only if human_action = EDITED
    rejection_reason        TEXT,               -- Only if human_action = REJECTED
    quality_score           FLOAT,              -- 0.0–1.0 overall quality rating if provided

    -- Derived quality signals (computed on feedback)
    edit_distance           INTEGER,            -- Levenshtein distance between ai_response and edited_response
    edit_ratio              FLOAT,              -- edit_distance / len(ai_response); < 0.1 = minor edit
    was_useful              BOOLEAN,            -- TRUE if APPROVED or (EDITED and edit_ratio < 0.25)

    -- Timestamps
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),  -- When AI response was generated
    feedback_at             TIMESTAMPTZ,                        -- When agent acted on it
    re_ingested_at          TIMESTAMPTZ,                        -- When this ticket was re-ingested after feedback

    -- Learning loop status
    reingestion_eligible    BOOLEAN DEFAULT FALSE,  -- TRUE when feedback is actionable for re-ingest
    reingestion_done        BOOLEAN DEFAULT FALSE,
    reingestion_run_id      TEXT                    -- run_id from rag_ingestion_logs if re-ingested
);

COMMENT ON TABLE public.rag_feedback_logs IS
    'Phase B1: Agent feedback on AI-generated responses. '
    'APPROVED/EDITED rows with edit_ratio < 0.25 are gold training candidates. '
    'ESCALATED rows trigger automation_label update → ESCALATION for that ticket. '
    'Drives the continuous learning re-ingestion loop.';

COMMENT ON COLUMN public.rag_feedback_logs.retrieved_chunks IS
    'JSON array of chunks used for this response. Enables chunk-level quality '
    'attribution: if a response is rejected, the chunks that contributed are '
    'flagged for review. Format: [{chunk_id, ticket_id, similarity, chunk_type}]';

COMMENT ON COLUMN public.rag_feedback_logs.edit_ratio IS
    '< 0.10: essentially approved — agent just fixed formatting. '
    '0.10–0.25: minor edit — good training signal. '
    '0.25–0.60: significant edit — context was partial or SOP was wrong. '
    '> 0.60: major rewrite — retrieval missed the target; flag for diagnosis.';
