-- =============================================================================
-- B3_001_knowledge_tables.sql
-- Phase B3: rag_knowledge_articles + rag_knowledge_chunks
--
-- Design decisions:
--   - Separate tables from ticket/SOP tables to avoid HNSW index corruption
--   - clients[] = [] means global (all tenants); otherwise tenant-scoped
--   - quality_score is pre-computed at ingestion time; used as a retrieval gate
--   - HNSW index created on knowledge chunks only (separate from ticket HNSW)
--   - All IF NOT EXISTS guards for idempotent re-runs
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 0. Prerequisites (must already exist from B1 migrations)
-- ---------------------------------------------------------------------------
-- uuid_generate_v4()  → pgcrypto or uuid-ossp extension
-- VECTOR(1536)        → pgvector extension
-- update_updated_at_column() trigger function (created in B1_001)
-- These are NOT re-created here; they must exist before running this file.

-- ---------------------------------------------------------------------------
-- 1. rag_knowledge_articles — one row per Q&A article (before chunking)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rag_knowledge_articles (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Source identifiers
    article_id          TEXT UNIQUE NOT NULL,          -- slug: "so_{post_id}"
    source              TEXT NOT NULL DEFAULT 'stackoverflow_for_teams',
    source_post_id      INTEGER NOT NULL,              -- original SO question post ID

    -- Article type: "qa_pair" | "question_only" | "wiki"
    article_type        TEXT NOT NULL DEFAULT 'qa_pair',

    -- Content (PII-redacted before storage)
    title               TEXT NOT NULL DEFAULT '',
    question_body       TEXT NOT NULL DEFAULT '',
    answer_body         TEXT,                          -- NULL if no answer available
    answer_post_id      INTEGER,                       -- SO answer post ID

    -- Vote / quality signals (kept for re-scoring without re-fetching SO)
    answer_score        INTEGER NOT NULL DEFAULT 0,
    question_score      INTEGER NOT NULL DEFAULT 0,
    view_count          INTEGER NOT NULL DEFAULT 0,
    accepted_answer_id  INTEGER,                       -- NULL if no accepted answer

    -- Taxonomy
    tags_raw            TEXT[]  NOT NULL DEFAULT '{}', -- original SO tags
    clients             TEXT[]  NOT NULL DEFAULT '{}', -- [] = global; else tenant slugs
    knowledge_class     TEXT    NOT NULL DEFAULT 'FAQ',
        -- VERIFIED_REPLY | TROUBLESHOOTING | FAQ | POLICY | RCA

    -- Quality gate
    quality_score       FLOAT   NOT NULL DEFAULT 0.0 CHECK (quality_score >= 0.0 AND quality_score <= 1.0),

    -- Dedup
    content_hash        TEXT    NOT NULL DEFAULT '',   -- SHA256(question_body + answer_body)

    -- Lifecycle
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    post_state          TEXT    NOT NULL DEFAULT 'Published',
    created_at_source   TIMESTAMPTZ,                   -- original SO post creation time
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Versioning (matches B1 INDEX_VERSION pattern)
    index_version       TEXT    NOT NULL DEFAULT 'v1',
    ingestion_run_id    TEXT                           -- UUID of the ingestion run
);

COMMENT ON TABLE  rag_knowledge_articles IS 'Phase B3: One row per SO-for-Teams Q&A article (PII-redacted). Parent of rag_knowledge_chunks.';
COMMENT ON COLUMN rag_knowledge_articles.clients IS 'Empty array = global knowledge (all tenants). Non-empty = tenant-scoped.';
COMMENT ON COLUMN rag_knowledge_articles.quality_score IS 'Pre-computed composite score [0,1]: 0.35*accepted + 0.25*vote_norm + 0.20*len_score + 0.10*q_score + 0.10*view_norm.';

-- Trigger: auto-update updated_at (reuses B1 trigger function)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'rag_knowledge_articles_updated_at'
          AND tgrelid = 'rag_knowledge_articles'::regclass
    ) THEN
        CREATE TRIGGER rag_knowledge_articles_updated_at
            BEFORE UPDATE ON rag_knowledge_articles
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END$$;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_rka_clients
    ON rag_knowledge_articles USING GIN(clients);

CREATE INDEX IF NOT EXISTS idx_rka_knowledge_class
    ON rag_knowledge_articles(knowledge_class);

CREATE INDEX IF NOT EXISTS idx_rka_content_hash
    ON rag_knowledge_articles(content_hash);

CREATE INDEX IF NOT EXISTS idx_rka_source_post_id
    ON rag_knowledge_articles(source_post_id);

CREATE INDEX IF NOT EXISTS idx_rka_quality
    ON rag_knowledge_articles(quality_score DESC)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_rka_index_version
    ON rag_knowledge_articles(index_version);

-- ---------------------------------------------------------------------------
-- 2. rag_knowledge_chunks — embedded chunks derived from articles
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rag_knowledge_chunks (
    id               UUID PRIMARY KEY,          -- UUID5 deterministic (see ingestion code)

    -- FK to parent article
    article_id       TEXT NOT NULL
                     REFERENCES rag_knowledge_articles(article_id) ON DELETE CASCADE,
    source_post_id   INTEGER NOT NULL,

    -- Chunk position
    chunk_index      SMALLINT NOT NULL,

    -- Chunk classification:
    -- FAQ_QUESTION | FAQ_ANSWER | TROUBLESHOOTING | VERIFIED_REPLY | RCA | POLICY
    chunk_type       TEXT NOT NULL,

    -- Content (PII-redacted; same redaction applied as parent article)
    content          TEXT NOT NULL,
    word_count       SMALLINT NOT NULL DEFAULT 0,
    content_hash     TEXT NOT NULL,            -- SHA256(content) for dedup

    -- Vector embedding (NULL until embedded; NULL rows excluded from retrieval)
    embedding        VECTOR(1536),

    -- Tenant isolation (copy from parent article for query efficiency)
    clients          TEXT[] NOT NULL DEFAULT '{}',
    tags_raw         TEXT[] NOT NULL DEFAULT '{}',

    -- Quality signals (denormalized from parent for retrieval-time access)
    knowledge_class  TEXT NOT NULL DEFAULT 'FAQ',
    quality_score    FLOAT NOT NULL DEFAULT 0.0 CHECK (quality_score >= 0.0 AND quality_score <= 1.0),
    question_score   INTEGER NOT NULL DEFAULT 0,
    answer_score     INTEGER NOT NULL DEFAULT 0,

    -- Provenance
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ingestion_run_id TEXT,
    index_version    TEXT NOT NULL DEFAULT 'v1',

    UNIQUE (article_id, chunk_index, index_version)
);

COMMENT ON TABLE  rag_knowledge_chunks IS 'Phase B3: Embedded chunks from rag_knowledge_articles. Retrieved by match_all_b1_sources UNION branch.';
COMMENT ON COLUMN rag_knowledge_chunks.embedding IS 'NULL rows are excluded from retrieval. Populated by KnowledgePipeline.';
COMMENT ON COLUMN rag_knowledge_chunks.quality_score IS 'Retrieval gate: rows with quality_score < 0.55 are excluded by the RPC WHERE clause (raised 0.40→0.55 by B3_004/B3_005).';

-- Indexes for retrieval-time filtering (evaluated before HNSW scan)
CREATE INDEX IF NOT EXISTS idx_rkc_clients
    ON rag_knowledge_chunks USING GIN(clients);

CREATE INDEX IF NOT EXISTS idx_rkc_chunk_type
    ON rag_knowledge_chunks(chunk_type);

CREATE INDEX IF NOT EXISTS idx_rkc_knowledge_class
    ON rag_knowledge_chunks(knowledge_class);

CREATE INDEX IF NOT EXISTS idx_rkc_article_id
    ON rag_knowledge_chunks(article_id);

CREATE INDEX IF NOT EXISTS idx_rkc_quality
    ON rag_knowledge_chunks(quality_score DESC);

CREATE INDEX IF NOT EXISTS idx_rkc_content_hash
    ON rag_knowledge_chunks(content_hash);

CREATE INDEX IF NOT EXISTS idx_rkc_index_version
    ON rag_knowledge_chunks(index_version);

-- HNSW index for cosine similarity search (separate from ticket HNSW index)
-- m=16, ef_construction=64 are conservative defaults for <50K rows (switch to
-- IVFFlat at 50K rows; the knowledge base is ~3K rows post-B3 ingestion).
-- Only index rows that have been embedded (embedding IS NOT NULL).
CREATE INDEX IF NOT EXISTS idx_rkc_embedding_hnsw
    ON rag_knowledge_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    WHERE embedding IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 3. Row-Level Security (mirrors B1 RLS pattern)
-- ---------------------------------------------------------------------------

-- Service-role bypass (ingestion and internal calls)
ALTER TABLE rag_knowledge_articles ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_knowledge_chunks   ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'rag_knowledge_articles' AND policyname = 'rka_service_role_all'
    ) THEN
        CREATE POLICY rka_service_role_all ON rag_knowledge_articles
            FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'rag_knowledge_chunks' AND policyname = 'rkc_service_role_all'
    ) THEN
        CREATE POLICY rkc_service_role_all ON rag_knowledge_chunks
            FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);
    END IF;
END$$;

-- Tenant isolation for anon/authenticated reads:
-- global rows (clients = '{}') are visible to all tenants.
-- tenant-scoped rows are visible only when app.current_tenant matches.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'rag_knowledge_articles' AND policyname = 'rka_tenant_isolation'
    ) THEN
        CREATE POLICY rka_tenant_isolation ON rag_knowledge_articles
            FOR SELECT
            USING (
                clients = '{}'
                OR current_setting('app.current_tenant', TRUE) = ANY(clients)
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'rag_knowledge_chunks' AND policyname = 'rkc_tenant_isolation'
    ) THEN
        CREATE POLICY rkc_tenant_isolation ON rag_knowledge_chunks
            FOR SELECT
            USING (
                clients = '{}'
                OR current_setting('app.current_tenant', TRUE) = ANY(clients)
            );
    END IF;
END$$;
