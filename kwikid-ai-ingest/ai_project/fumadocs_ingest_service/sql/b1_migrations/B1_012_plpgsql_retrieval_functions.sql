-- B1_012_plpgsql_retrieval_functions.sql
--
-- ─── ROOT CAUSE ──────────────────────────────────────────────────────────────
-- match_b1_ticket_chunks_v2 and match_b1_sop_chunks_v2 (B1_010) are defined as
-- LANGUAGE sql STABLE. When called via Supabase/PostgREST, the driver uses
-- prepared statements. After 5 executions PostgreSQL switches to a GENERIC PLAN
-- where p_client and p_index_version appear as unresolved parameters ($1, $2).
--
-- In generic plan mode the planner cannot prove that $1 = 'unity_bank'
-- satisfies the partial index predicate (client = 'unity_bank'), so it falls
-- back to the global HNSW index and emits post-scan row filtering:
--
--   Index Scan using idx_rtc_embedding_hnsw   ← global, not partial
--   Filter: (client = 'unity_bank') AND (index_version = 'v2')
--   Rows Removed by Filter: 33
--
-- ─── FIX ─────────────────────────────────────────────────────────────────────
-- Rewrite both functions as LANGUAGE plpgsql with EXECUTE format(%L) which
-- EMBEDS p_client and p_index_version as SQL LITERALS in the dynamically
-- constructed query string. The planner always sees concrete values regardless
-- of whether a prepared statement or generic plan is used:
--
--   WHERE rtc.client = 'unity_bank'   ← literal, always visible to planner
--     AND rtc.index_version = 'v2'    ← literal, always visible to planner
--
-- Result:
--   Index Scan using idx_rtc_embedding_hnsw_unity_v2  ← partial index ✓
--   Rows Removed by Filter: 0                          ← zero waste ✓
--
-- ─── ALSO ADDRESSES ──────────────────────────────────────────────────────────
-- ACTIVE_INDEX_VERSION defaults to "v1" in app/config.py.
-- Queries therefore run with index_version='v1', but the B1_011 partial indexes
-- are for v2 data only. This migration also creates partial HNSW indexes for v1
-- so that v1-queried data also benefits from partial index optimisation.
--
-- Set ACTIVE_INDEX_VERSION=v2 in .env when the v2 ingest is confirmed stable.
-- Both v1 and v2 partial indexes are maintained; the planner selects the right
-- one based on the literal value embedded by EXECUTE format(%L).
--
-- ─── CONCURRENTLY REQUIREMENT ────────────────────────────────────────────────
-- CREATE INDEX CONCURRENTLY cannot run inside a transaction.
-- Run this file in the Supabase SQL Editor or via psql with autocommit ON.
-- Do NOT run through Flyway/Alembic/any migration runner that wraps in BEGIN/COMMIT.
--
-- Prerequisite:  B1_010 (defines the v2 function signatures)
--               B1_011 (creates partial HNSW indexes for v2 data)
-- Does NOT touch: match_all_b1_sources (B1_005) — v1 fallback path unchanged.

-- =============================================================================
-- Section 1: Rewrite match_b1_ticket_chunks_v2 as PL/pgSQL (literal injection)
-- =============================================================================
-- Design choices:
--   * EXECUTE format(..., %L, %L) embeds p_client and p_index_version as SQL
--     literals. The planner sees 'unity_bank' and 'v2' at plan time.
--   * p_query_embedding and p_match_count passed via USING as bind parameters
--     ($1, $2). Keeps the query string compact; type is known from PL/pgSQL
--     variable declarations so ORDER BY rtc.embedding <=> $1 uses HNSW correctly.
--   * $sql$ dollar-quoting for the format string avoids all single-quote escaping.
--   * STABLE PARALLEL SAFE preserved — function reads data only.
--   * Same RETURNS TABLE schema as LANGUAGE sql version — no Python changes needed.

CREATE OR REPLACE FUNCTION public.match_b1_ticket_chunks_v2(
    p_query_embedding  VECTOR(1536),
    p_client           TEXT,
    p_match_count      INTEGER  DEFAULT 40,
    p_index_version    TEXT     DEFAULT 'v1'
)
RETURNS TABLE (
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
)
LANGUAGE plpgsql STABLE PARALLEL SAFE AS $$
BEGIN
    -- p_client and p_index_version are embedded as SQL literals via %L.
    -- The planner therefore sees concrete values ('unity_bank', 'v2') and can
    -- match the partial HNSW index predicate, eliminating post-scan filtering.
    -- p_query_embedding ($1) and p_match_count ($2) are passed as bind parameters
    -- to keep the query string size small despite the 1536-dim vector.
    RETURN QUERY EXECUTE format(
        $sql$
        SELECT
            rtc.id,
            'rag_ticket_chunks'::TEXT              AS source_table,
            rtc.ticket_id,
            NULL::TEXT                              AS sop_id,
            rtc.chunk_type,
            rtc.content,
            (1 - (rtc.embedding <=> $1))::FLOAT    AS similarity,
            (1 - (rtc.embedding <=> $1))::FLOAT    AS boosted_score,
            rtc.has_rca,
            rtc.has_sop,
            rtc.extra_metadata
        FROM public.rag_ticket_chunks rtc
        WHERE rtc.client           = %L
          AND rtc.index_version    = %L
          AND rtc.embedding        IS NOT NULL
          AND rtc.automation_label != 'ESCALATION'
        ORDER BY rtc.embedding <=> $1
        LIMIT $2
        $sql$,
        p_client,
        p_index_version
    ) USING p_query_embedding, p_match_count;
END;
$$;

-- =============================================================================
-- Section 2: Rewrite match_b1_sop_chunks_v2 as PL/pgSQL (literal injection)
-- =============================================================================
-- Same approach. p_index_version and p_client are embedded as literals.
-- The partial index idx_rsc_embedding_hnsw_v2 predicate (index_version='v2',
-- embedding IS NOT NULL) is now visible to the planner at plan time.
-- SOP boost (+0.15) remains computed in SQL SELECT — does not prevent HNSW.
-- EXISTS subquery for is_active check is non-distance — does not prevent HNSW.

CREATE OR REPLACE FUNCTION public.match_b1_sop_chunks_v2(
    p_query_embedding  VECTOR(1536),
    p_client           TEXT,
    p_match_count      INTEGER  DEFAULT 40,
    p_index_version    TEXT     DEFAULT 'v1'
)
RETURNS TABLE (
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
)
LANGUAGE plpgsql STABLE PARALLEL SAFE AS $$
BEGIN
    RETURN QUERY EXECUTE format(
        $sql$
        SELECT
            sc.id,
            'rag_sop_chunks'::TEXT                                                  AS source_table,
            NULL::TEXT                                                               AS ticket_id,
            sc.sop_id,
            'SOP_STEPS'::TEXT                                                        AS chunk_type,
            sc.content,
            (1 - (sc.embedding <=> $1))::FLOAT                                      AS similarity,
            LEAST(1.0, (1 - (sc.embedding <=> $1)) + 0.15)::FLOAT                  AS boosted_score,
            FALSE                                                                    AS has_rca,
            TRUE                                                                     AS has_sop,
            '{}'::JSONB                                                              AS extra_metadata
        FROM public.rag_sop_chunks sc
        WHERE sc.embedding      IS NOT NULL
          AND sc.index_version  = %L
          AND (sc.clients = '{}' OR %L = ANY(sc.clients))
          AND EXISTS (
              SELECT 1
              FROM public.rag_sop_library sl
              WHERE sl.sop_id    = sc.sop_id
                AND sl.is_active = TRUE
          )
        ORDER BY sc.embedding <=> $1
        LIMIT $2
        $sql$,
        p_index_version,
        p_client
    ) USING p_query_embedding, p_match_count;
END;
$$;

-- =============================================================================
-- Section 3: Grants (same as B1_010)
-- =============================================================================
GRANT EXECUTE ON FUNCTION public.match_b1_ticket_chunks_v2 TO authenticated;
GRANT EXECUTE ON FUNCTION public.match_b1_ticket_chunks_v2 TO service_role;
GRANT EXECUTE ON FUNCTION public.match_b1_sop_chunks_v2    TO authenticated;
GRANT EXECUTE ON FUNCTION public.match_b1_sop_chunks_v2    TO service_role;

-- =============================================================================
-- Section 4: Partial HNSW indexes for v1 data
-- =============================================================================
-- The application default is ACTIVE_INDEX_VERSION=v1 (app/config.py line 258).
-- Until ACTIVE_INDEX_VERSION=v2 is explicitly set in .env, all queries use v1.
-- B1_011 only created v2 partial indexes, so v1 queries still hit the global
-- HNSW index and produce post-scan filtering.
--
-- These indexes cover v1 queries. Once ACTIVE_INDEX_VERSION=v2 is deployed and
-- stable, the v1 indexes may be dropped (CONCURRENTLY) to reclaim space.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rtc_embedding_hnsw_unity_v1
    ON public.rag_ticket_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = '16', ef_construction = '64')
    WHERE client       = 'unity_bank'
      AND index_version = 'v1'
      AND embedding    IS NOT NULL;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rsc_embedding_hnsw_v1
    ON public.rag_sop_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = '16', ef_construction = '64')
    WHERE index_version = 'v1'
      AND embedding    IS NOT NULL;

-- =============================================================================
-- Section 5: Post-migration statistics refresh
-- =============================================================================
ANALYZE public.rag_ticket_chunks;
ANALYZE public.rag_sop_chunks;

-- =============================================================================
-- Section 6: Verification queries (run after indexes are built)
-- =============================================================================

-- 6a. Confirm all partial HNSW indexes are valid:
--   SELECT indexname, pg_size_pretty(pg_relation_size(indexrelid)) AS idx_size
--   FROM pg_stat_user_indexes
--   WHERE indexrelname IN (
--       'idx_rtc_embedding_hnsw_unity_v1',
--       'idx_rtc_embedding_hnsw_unity_v2',
--       'idx_rsc_embedding_hnsw_v1',
--       'idx_rsc_embedding_hnsw_v2',
--       'idx_rtc_embedding_hnsw',
--       'idx_rsc_embedding_hnsw'
--   )
--   ORDER BY indexrelname;

-- 6b. EXPLAIN on the rewritten ticket function (with literal args):
--   EXPLAIN (ANALYZE, FORMAT TEXT, BUFFERS)
--   SELECT * FROM public.match_b1_ticket_chunks_v2(
--       '[0.01, 0.02, ...]'::VECTOR(1536),  -- replace with real embedding
--       'unity_bank',
--       40,
--       'v1'                                 -- or 'v2' when ACTIVE_INDEX_VERSION=v2
--   );
--   Expected (v1): "Index Scan using idx_rtc_embedding_hnsw_unity_v1"
--   Expected (v2): "Index Scan using idx_rtc_embedding_hnsw_unity_v2"
--   Expected:      "Rows Removed by Filter: 0"

-- 6c. Confirm scan counters increment after live traffic:
--   SELECT indexrelname, idx_scan
--   FROM pg_stat_user_indexes
--   WHERE indexrelname LIKE 'idx_rtc_embedding_hnsw_unity%'
--      OR indexrelname LIKE 'idx_rsc_embedding_hnsw%';
--   The global indexes (idx_rtc_embedding_hnsw, idx_rsc_embedding_hnsw) should
--   stop accumulating new scans once the partial indexes are active.

-- =============================================================================
-- Section 7: Rollback
-- =============================================================================
-- To revert this migration (the global HNSW indexes from B1_006 take over):
--
--   DROP INDEX CONCURRENTLY IF EXISTS idx_rtc_embedding_hnsw_unity_v1;
--   DROP INDEX CONCURRENTLY IF EXISTS idx_rsc_embedding_hnsw_v1;
--
-- The v2 partial indexes (B1_011) are unaffected. Restore the LANGUAGE sql
-- versions of the functions by re-running B1_010 in Supabase.
