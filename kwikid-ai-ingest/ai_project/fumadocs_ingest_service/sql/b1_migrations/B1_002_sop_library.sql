-- =============================================================================
-- B1_002_sop_library.sql
-- Phase B1: SOP (Standard Operating Procedure) document library
--
-- Design rationale:
--   - SOPs are the highest-quality retrieval source — when an SOP exists for
--     a query type, it should rank above historical tickets
--   - clients[] array allows multi-tenant SOP applicability without duplication
--   - version column supports incremental SOP updates (re-embed only changed SOPs)
--   - source tracks origin: fumadocs, knowledge_card, manual
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.rag_sop_library (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- SOP identity
    sop_id          TEXT NOT NULL UNIQUE,       -- Deterministic: slug from file path or sop_key
    title           TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,

    -- Classification for retrieval boosting
    query_type      TEXT,                       -- Maps to Freshdesk Query Type taxonomy
    issue_area      TEXT,

    -- Multi-tenant applicability
    -- Empty array = applies to ALL tenants (global SOP)
    -- Populated = only for listed clients
    clients         TEXT[] NOT NULL DEFAULT '{}',

    -- Content
    content         TEXT NOT NULL,              -- Full cleaned SOP text
    content_hash    TEXT NOT NULL,              -- SHA-256 for dedup

    -- Embedding
    embedding       VECTOR(1536),

    -- Source tracking
    source          TEXT NOT NULL DEFAULT 'fumadocs',  -- fumadocs | knowledge_card | manual
    source_path     TEXT,                       -- File path in Bitbucket repo (if from fumadocs)
    commit_sha      TEXT,                       -- Git commit of source file

    -- Timestamps
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ingested_at     TIMESTAMPTZ,
    index_version   TEXT NOT NULL DEFAULT 'v1',

    -- Active flag: soft-delete retired SOPs without removing embeddings
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

DROP TRIGGER IF EXISTS rag_sop_library_updated_at ON public.rag_sop_library;
CREATE TRIGGER rag_sop_library_updated_at
    BEFORE UPDATE ON public.rag_sop_library
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- =============================================================================
-- TABLE: rag_sop_chunks
-- SOPs chunked by section (H2/H3 headers = one chunk per step group)
-- =============================================================================
CREATE TABLE IF NOT EXISTS public.rag_sop_chunks (
    id              UUID PRIMARY KEY,           -- Deterministic UUID5

    -- Parent SOP link
    sop_id          TEXT NOT NULL REFERENCES public.rag_sop_library(sop_id) ON DELETE CASCADE,
    sop_version     INTEGER NOT NULL DEFAULT 1,

    -- Chunk position
    chunk_index     SMALLINT NOT NULL,
    chunk_heading   TEXT,                       -- H2/H3 section heading this chunk is from
    chunk_type      TEXT NOT NULL DEFAULT 'SOP_STEPS',

    -- Content
    content         TEXT NOT NULL,
    word_count      SMALLINT NOT NULL DEFAULT 0,
    content_hash    TEXT NOT NULL,

    -- Embedding
    embedding       VECTOR(1536),

    -- Retrieval metadata (inherited from parent SOP)
    query_type      TEXT,
    issue_area      TEXT,
    clients         TEXT[] NOT NULL DEFAULT '{}',

    -- Timestamps
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    index_version   TEXT NOT NULL DEFAULT 'v1',

    CONSTRAINT rag_sop_chunks_unique_position
        UNIQUE (sop_id, chunk_index, sop_version)
);

COMMENT ON TABLE public.rag_sop_library IS
    'Phase B1: SOP document library. SOPs are ingested from Fumadocs/Bitbucket and '
    'knowledge cards. Retrieved chunks from this table are weighted higher than '
    'ticket chunks during context assembly. New SOPs go live immediately after ingest.';

COMMENT ON COLUMN public.rag_sop_library.clients IS
    'Empty array = global SOP (all tenants). Populated = tenant-specific SOP. '
    'Retrieval filter: WHERE clients = {} OR :client = ANY(clients).';
