-- B1_014_hnsw_diagnostic.sql
--
-- READ-ONLY diagnostic queries. Does NOT modify any schema.
-- Run in Supabase SQL Editor to verify HNSW indexes are healthy and active.
--
-- Run after B1_011_partial_hnsw_indexes.sql has been applied.

-- ── 1. Index existence and validity ──────────────────────────────────────────
-- All four indexes should appear with indisvalid = true.
-- If a partial index is missing, re-run B1_011.
-- If indisvalid = false, the index was interrupted during build — drop and rebuild.

SELECT
    i.indexrelname                                          AS index_name,
    pi2.indisvalid                                          AS is_valid,
    pg_size_pretty(pg_relation_size(i.indexrelid))          AS index_size,
    i.idx_scan                                              AS total_scans,
    i.idx_tup_read                                          AS tuples_read
FROM pg_stat_user_indexes  i
JOIN pg_index              pi2 ON pi2.indexrelid = i.indexrelid
WHERE i.indexrelname IN (
    'idx_rtc_embedding_hnsw',           -- global ticket index (B1_006)
    'idx_rsc_embedding_hnsw',           -- global SOP index (B1_006)
    'idx_rtc_embedding_hnsw_unity_v2',  -- partial ticket index (B1_011)
    'idx_rsc_embedding_hnsw_v2'         -- partial SOP index (B1_011)
)
ORDER BY i.indexrelname;


-- ── 2. Confirm partial index is being preferred over global ──────────────────
-- After running a few /rag/chat queries:
--   idx_rtc_embedding_hnsw_unity_v2 → idx_scan should be growing fast
--   idx_rtc_embedding_hnsw          → idx_scan should be flat (not used)
-- If the global index keeps growing, the partial index predicate isn't matching.

SELECT indexrelname, idx_scan, idx_tup_read
FROM pg_stat_user_indexes
WHERE indexrelname IN (
    'idx_rtc_embedding_hnsw',
    'idx_rtc_embedding_hnsw_unity_v2',
    'idx_rsc_embedding_hnsw',
    'idx_rsc_embedding_hnsw_v2'
)
ORDER BY idx_scan DESC;


-- ── 3. EXPLAIN plan on match_b1_ticket_chunks_v2 ─────────────────────────────
-- Replace the embedding placeholder with any real 1536-dim vector.
-- Expected: "Index Scan using idx_rtc_embedding_hnsw_unity_v2"
-- Expected: "Rows Removed by Filter: 0"
-- If you see idx_rtc_embedding_hnsw (global): partial index isn't active yet.

-- EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
-- SELECT * FROM public.match_b1_ticket_chunks_v2(
--     array_fill(0.01, ARRAY[1536])::VECTOR(1536),
--     'unity_bank',
--     40,
--     'v2'
-- );


-- ── 4. FTS index health (for B1_008 search_b1_sources_fts) ───────────────────
-- Checks whether full-text search indexes on rag_ticket_chunks exist.
-- If no rows are returned here, B1_007_fts_setup.sql has not been applied.

SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'rag_ticket_chunks'
  AND indexdef ILIKE '%tsvector%'
ORDER BY indexname;


-- ── 5. Table and index sizes ──────────────────────────────────────────────────
SELECT
    relname                                             AS table_name,
    pg_size_pretty(pg_total_relation_size(oid))         AS total_size,
    pg_size_pretty(pg_relation_size(oid))               AS table_size,
    pg_size_pretty(pg_indexes_size(oid))                AS indexes_size
FROM pg_class
WHERE relname IN ('rag_ticket_chunks', 'rag_sop_chunks', 'rag_knowledge_chunks')
ORDER BY pg_total_relation_size(oid) DESC;


-- ── 6. hnsw.ef_search current value ──────────────────────────────────────────
-- ef_search=40 is the default. With our partial index (3764 rows for unity_bank v2),
-- ef_search=40 gives excellent recall. Raise to 80 only if recall@10 drops below 75%.

SHOW hnsw.ef_search;


-- ── 7. Row counts per (client, index_version, chunk_type) ────────────────────
-- Confirms v2 ingest is complete and chunk distribution matches expected.

SELECT client, index_version, chunk_type, COUNT(*) AS chunk_count
FROM public.rag_ticket_chunks
GROUP BY client, index_version, chunk_type
ORDER BY client, index_version, chunk_type;
