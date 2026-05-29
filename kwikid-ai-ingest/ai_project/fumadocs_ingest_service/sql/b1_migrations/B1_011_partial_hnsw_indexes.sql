-- B1_011_partial_hnsw_indexes.sql
--
-- Tenant/version-specific partial HNSW indexes for rag_ticket_chunks and rag_sop_chunks.
--
-- ─── PROBLEM ────────────────────────────────────────────────────────────────
-- EXPLAIN ANALYZE on match_b1_ticket_chunks_v2 showed:
--
--   Index Scan using idx_rtc_embedding_hnsw  ← global index, all tenants
--   Filter: (client = 'unity_bank') AND (index_version = 'v2')
--   Rows Removed by Filter: 33
--
-- The global HNSW index (B1_006) contains vectors for every client and every
-- index_version in the same graph. During ANN traversal, the planner traverses
-- the full neighbourhood graph and then DISCARDS 33 vectors that belong to
-- other clients or older index versions AFTER the index scan.
--
-- ─── WHY PARTIAL INDEXES FIX THIS ───────────────────────────────────────────
-- A partial index with WHERE client='unity_bank' AND index_version='v2'
-- contains ONLY the vectors that satisfy those conditions. Two consequences:
--
--   1. Planner selectivity: The query WHERE clause (client=p_client,
--      index_version=p_index_version) implies the partial index predicate
--      exactly. Postgres can prove this at plan time (LANGUAGE sql STABLE
--      functions are inlined, so parameter values are visible to the planner)
--      and will prefer the smaller, more selective partial index over the
--      global one.
--
--   2. Reduced post-filter vector traversal cost: Because the HNSW graph
--      is built exclusively from vectors in the partial index, every node
--      the ANN algorithm visits is already a valid result. There are no
--      "wasted" traversals to vectors that will be filtered out afterward.
--      "Rows Removed by Filter" drops from 33 → 0.
--
-- The effect compounds at scale: as total rows grow across all tenants and
-- versions, the global index grows proportionally, but each partial index
-- grows only with its own tenant's data.
--
-- ─── CONCURRENTLY REQUIREMENT ───────────────────────────────────────────────
-- CREATE INDEX CONCURRENTLY cannot run inside an explicit transaction block.
-- Run this file in the Supabase SQL Editor (which auto-commits each statement)
-- or via psql with autocommit ON (the default).
-- Do NOT run through migration tools (Flyway, Alembic) that wrap in BEGIN/COMMIT.
--
-- ─── DOES NOT TOUCH ─────────────────────────────────────────────────────────
-- - Does NOT drop idx_rtc_embedding_hnsw or idx_rsc_embedding_hnsw (B1_006).
--   Global indexes remain as fallback for queries that cannot use partial indexes.
-- - Does NOT modify any SQL functions or Python retrieval logic.
--   The partial indexes are transparent to all existing queries.
-- - Does NOT affect governance, reranking, generation, or API contracts.
--
-- Prerequisite:  B1_006_indexes.sql, B1_010_hnsw_compatible_retrieval.sql
-- Rollback:      DROP INDEX CONCURRENTLY IF EXISTS <index_name>;  (see section 4)

-- =============================================================================
-- Section 1: Partial HNSW indexes for rag_ticket_chunks
-- =============================================================================
-- One index per (client, index_version) combination currently in production.
-- Add a new CREATE INDEX block for each new tenant when onboarded.
-- (See Section 3 for the template.)
--
-- m=16: max connections per HNSW layer — matches global index for consistency
-- ef_construction=64: build-time exploration width — matches global index
-- The partial index is a smaller graph so build time is proportionally faster.
-- =============================================================================

-- Primary production tenant: unity_bank, index_version=v2
-- (Update 'unity_bank' and 'v2' to reflect your active tenant + version.)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rtc_embedding_hnsw_unity_v2
    ON public.rag_ticket_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = '16', ef_construction = '64')
    WHERE client = 'unity_bank'
      AND index_version = 'v2'
      AND embedding IS NOT NULL;

-- Legacy version partial index for unity_bank (v1 data — read-only after cutover)
-- Uncomment if unity_bank v1 data still receives queries:
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rtc_embedding_hnsw_unity_v1
--     ON public.rag_ticket_chunks
--     USING hnsw (embedding vector_cosine_ops)
--     WITH (m = '16', ef_construction = '64')
--     WHERE client = 'unity_bank'
--       AND index_version = 'v1'
--       AND embedding IS NOT NULL;

-- =============================================================================
-- Section 2: Partial HNSW index for rag_sop_chunks
-- =============================================================================
-- rag_sop_chunks does not have a scalar client column — SOPs are shared across
-- tenants via the clients TEXT[] array column. Partial index on index_version
-- alone is the correct optimization: it reduces the graph to only active-version
-- SOP vectors, eliminating cross-version traversal waste.
--
-- The match_b1_sop_chunks_v2 WHERE clause:
--   WHERE sc.index_version = p_index_version AND sc.embedding IS NOT NULL ...
-- implies this partial predicate at plan time → partial index will be chosen.
-- =============================================================================

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rsc_embedding_hnsw_v2
    ON public.rag_sop_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = '16', ef_construction = '64')
    WHERE index_version = 'v2'
      AND embedding IS NOT NULL;

-- Legacy SOP v1 partial index — uncomment if v1 SOP queries are still live:
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rsc_embedding_hnsw_v1
--     ON public.rag_sop_chunks
--     USING hnsw (embedding vector_cosine_ops)
--     WITH (m = '16', ef_construction = '64')
--     WHERE index_version = 'v1'
--       AND embedding IS NOT NULL;

-- =============================================================================
-- Section 3: Template for new tenants
-- =============================================================================
-- When a new client (e.g., 'acme_corp') is onboarded and ingestion is complete:
--
--   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rtc_embedding_hnsw_acme_v2
--       ON public.rag_ticket_chunks
--       USING hnsw (embedding vector_cosine_ops)
--       WITH (m = '16', ef_construction = '64')
--       WHERE client = 'acme_corp'
--         AND index_version = 'v2'
--         AND embedding IS NOT NULL;
--
-- Timing: run AFTER initial ingestion for that tenant is complete.
-- Building HNSW on an empty or sparse index produces a low-quality graph.
-- Minimum recommended: 500+ rows per partial index for acceptable recall.
--
-- Naming convention: idx_rtc_embedding_hnsw_{client_slug}_{version}
-- =============================================================================

-- =============================================================================
-- Section 4: Rollback instructions
-- =============================================================================
-- To revert this migration (drops only the partial indexes; global indexes
-- from B1_006 are untouched and take over automatically):
--
--   DROP INDEX CONCURRENTLY IF EXISTS idx_rtc_embedding_hnsw_unity_v2;
--   DROP INDEX CONCURRENTLY IF EXISTS idx_rsc_embedding_hnsw_v2;
--
-- No application config change required — the planner falls back to the
-- global HNSW indexes transparently.
-- =============================================================================

-- =============================================================================
-- Section 5: Post-migration verification queries
-- =============================================================================
-- Run after the indexes finish building to verify:

-- 5a. Confirm partial indexes exist and are valid (indisvalid = true):
--   SELECT indexname, indisvalid, indisprimary
--   FROM pg_indexes
--   JOIN pg_class c ON c.relname = pg_indexes.indexname
--   JOIN pg_index i ON i.indexrelid = c.oid
--   WHERE indexname IN (
--       'idx_rtc_embedding_hnsw_unity_v2',
--       'idx_rsc_embedding_hnsw_v2',
--       'idx_rtc_embedding_hnsw',
--       'idx_rsc_embedding_hnsw'
--   );

-- 5b. Confirm the partial index is used (EXPLAIN ANALYZE on a v2 query):
--   EXPLAIN ANALYZE
--   SELECT * FROM public.match_b1_ticket_chunks_v2(
--       '[0.1, 0.2, ...]'::VECTOR(1536),  -- replace with a real embedding
--       'unity_bank',
--       40,
--       'v2'
--   );
--   Expected: "Index Scan using idx_rtc_embedding_hnsw_unity_v2"
--   Expected: "Rows Removed by Filter: 0"

-- 5c. Confirm scan counts are incrementing (run after a few requests):
--   SELECT indexrelname, idx_scan, idx_tup_read, idx_tup_fetch
--   FROM pg_stat_user_indexes
--   WHERE indexrelname IN (
--       'idx_rtc_embedding_hnsw_unity_v2',
--       'idx_rsc_embedding_hnsw_v2',
--       'idx_rtc_embedding_hnsw',
--       'idx_rsc_embedding_hnsw'
--   );
--   After B1_010+B1_011 are active:
--     idx_rtc_embedding_hnsw_unity_v2 → idx_scan should be > 0 and growing
--     idx_rtc_embedding_hnsw          → idx_scan should stop growing (unused)

-- 5d. Index size comparison:
--   SELECT indexrelname,
--          pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
--   FROM pg_stat_user_indexes
--   WHERE indexrelname IN (
--       'idx_rtc_embedding_hnsw_unity_v2',
--       'idx_rtc_embedding_hnsw'
--   );
--   Partial index should be significantly smaller than the global index.
-- =============================================================================

-- =============================================================================
-- Section 6: HNSW ef_search tuning (optional — apply per session or globally)
-- =============================================================================
-- ef_search controls how many candidate neighbours HNSW explores at query time.
-- Higher values → better recall, slower query. Default = 40.
-- With a partial index (smaller, denser graph), lower ef_search achieves the
-- same recall as higher ef_search on a global index.
--
-- To tune globally (Supabase → Database → Extensions → postgres.conf):
--   ALTER SYSTEM SET hnsw.ef_search = 80;
--   SELECT pg_reload_conf();
--
-- To tune per-transaction (no restart needed):
--   SET LOCAL hnsw.ef_search = 80;
-- =============================================================================

-- Run ANALYZE to update planner statistics after index creation:
ANALYZE public.rag_ticket_chunks;
ANALYZE public.rag_sop_chunks;
