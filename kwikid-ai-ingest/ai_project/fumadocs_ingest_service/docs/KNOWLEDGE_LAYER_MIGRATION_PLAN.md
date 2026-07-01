# Knowledge Layer Production Migration Plan
## Sprint 2.33 — Phase A Freeze

**Date:** 2026-06-29  
**Status:** READY TO EXECUTE (pending user confirmation at each step)  
**Branch:** major-architecture-change  
**Corpus:** `C:\Users\Umair.Alam\Desktop\kwikid_support_system\stackoverflow\`

---

## Pre-conditions

All of these must be true before starting:

- [ ] You are on branch `major-architecture-change`
- [ ] `rapidocr-onnxruntime>=1.3.0` installed: `pip install rapidocr-onnxruntime`
- [ ] Supabase credentials in `.env` are the PRODUCTION credentials
- [ ] No active ingestion jobs running (check Supabase `ingestion_logs`)

---

## Step 1 — Pre-flight Validation

Run in Supabase SQL editor. All assertions must pass.

```sql
-- 1a. Verify B3_001 tables exist
SELECT COUNT(*) AS articles_table_exists
FROM information_schema.tables
WHERE table_name = 'rag_knowledge_articles';
-- Expected: 1

SELECT COUNT(*) AS chunks_table_exists
FROM information_schema.tables
WHERE table_name = 'rag_knowledge_chunks';
-- Expected: 1

-- 1b. Verify vector retrieval RPC exists
SELECT COUNT(*) AS rpc_exists
FROM pg_proc WHERE proname = 'match_all_b1_sources';
-- Expected: 1

-- 1c. Count existing knowledge data (should be 0 for a fresh migration)
SELECT COUNT(*) AS existing_articles FROM rag_knowledge_articles;
SELECT COUNT(*) AS existing_chunks FROM rag_knowledge_chunks;

-- 1d. Understand legacy SOP blast radius
SELECT COUNT(*) AS legacy_sop_count
FROM rag_sop_library WHERE source = 'fumadocs';

SELECT COUNT(*) AS legacy_sop_chunk_count
FROM rag_sop_chunks sc
JOIN rag_sop_library sl ON sl.sop_id = sc.sop_id
WHERE sl.source = 'fumadocs';
```

**Stop if:** tables missing, RPC missing, or existing article count is unexpectedly non-zero.

---

## Step 2 — Database Backup

**Require explicit confirmation before executing.**

```sql
-- 2a. Backup legacy SOP tables (non-destructive copies)
CREATE TABLE IF NOT EXISTS rag_sop_library_backup_20260629
    AS SELECT * FROM rag_sop_library;

CREATE TABLE IF NOT EXISTS rag_sop_chunks_backup_20260629
    AS SELECT * FROM rag_sop_chunks;

-- 2b. Verify counts match originals
SELECT
    (SELECT COUNT(*) FROM rag_sop_library) AS lib_original,
    (SELECT COUNT(*) FROM rag_sop_library_backup_20260629) AS lib_backup,
    (SELECT COUNT(*) FROM rag_sop_chunks) AS chunks_original,
    (SELECT COUNT(*) FROM rag_sop_chunks_backup_20260629) AS chunks_backup;
-- All originals must equal backups before proceeding.
```

---

## Step 3 — Soft-Disable Legacy SOPs

**Reversible.** Retrieval excludes `is_active = FALSE` rows.

```sql
UPDATE rag_sop_library SET is_active = FALSE
WHERE source = 'fumadocs';

-- Verify
SELECT COUNT(*) AS softdisabled
FROM rag_sop_library WHERE source = 'fumadocs' AND is_active = FALSE;

-- Rollback (if needed — before Step 7):
-- UPDATE rag_sop_library SET is_active = TRUE WHERE source = 'fumadocs';
```

---

## Step 4 — Apply SQL Migrations (in order)

Apply in Supabase SQL editor, one at a time, in this exact sequence. Wait for each to complete before the next.

| Order | File | What it does |
|---|---|---|
| 1 | `sql/b3_migrations/B3_001_knowledge_tables.sql` | Creates `rag_knowledge_articles` + `rag_knowledge_chunks` |
| 2 | `sql/b3_migrations/B3_002_extend_match_all_sources.sql` | Adds knowledge branch to `match_all_b1_sources` |
| 3 | `sql/b3_migrations/B3_003_feedback_enhancements.sql` | Review queue + feedback enhancements |
| 4 | `sql/b3_migrations/B3_004_raise_quality_gate.sql` | Raises retrieval quality gate 0.40→0.55 |
| 5 | `sql/b3_migrations/B3_005_fts_quality_gate.sql` | FTS path quality gate |
| 6 | **`sql/b3_migrations/B3_007_knowledge_fts.sql`** | Adds `fts TSVECTOR GENERATED` column to chunks — **MUST come before B3_006** |
| 7 | `sql/b3_migrations/B3_006_image_metadata.sql` | Adds `image_metadata JSONB` column + updates both RPCs (references `kc.fts`) |

> **ORDER CRITICAL:** B3_007 must be applied before B3_006. `search_b1_sources_fts` (created/replaced by B3_006) references `kc.fts`, which B3_007 creates. PostgreSQL defers column resolution to query execution time — the CREATE OR REPLACE will succeed if B3_006 is applied first, but the first live query against the FTS knowledge branch will fail with `column "fts" does not exist`.

**B3_006 and B3_007 MUST be applied before live ingestion.**  
All migrations are idempotent (safe to re-run).

After applying B3_007, verify:
```sql
SELECT column_name FROM information_schema.columns
WHERE table_name = 'rag_knowledge_chunks' AND column_name = 'fts';
-- Expected: 1 row
```

---

## Step 5 — Dry-Run Ingestion

```powershell
# From: C:\Users\Umair.Alam\Desktop\kwikid_support_system\kwikid-ai-ingest\ai_project\fumadocs_ingest_service
python scripts/ingest_knowledge.py `
    --dry-run `
    --source "C:\Users\Umair.Alam\Desktop\kwikid_support_system\stackoverflow" `
    --verbose
```

Expected output:
- `Articles processed: ~800`
- `Articles rejected: ~50-100` (low quality, no title, ESCALATION class)
- `Chunks would be created: ~1500-3000`
- `[DRY RUN] No data written.`
- Exit code: `0`

Stop if: import errors, articles_failed > 0, or chunk count is 0.

---

## Step 6 — Pre-Migration Spot-Check

Verify the B3_006 + B3_007 columns exist and the RPC is updated:

```sql
-- image_metadata column exists
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'rag_knowledge_articles'
  AND column_name = 'image_metadata';
-- Expected: 1 row, data_type=jsonb, column_default='[]'::jsonb

-- fts column exists on chunks
SELECT column_name
FROM information_schema.columns
WHERE table_name = 'rag_knowledge_chunks' AND column_name = 'fts';
-- Expected: 1 row

-- RPC body includes image_metadata (from B3_006 update)
SELECT pg_get_functiondef(oid) LIKE '%image_metadata%' AS has_image_metadata
FROM pg_proc WHERE proname = 'match_all_b1_sources';
-- Expected: true
```

---

## Step 7 — Live Ingestion

**Require explicit confirmation before executing.**

```powershell
python scripts/ingest_knowledge.py `
    --source "C:\Users\Umair.Alam\Desktop\kwikid_support_system\stackoverflow"
```

Post-ingestion verification:

```sql
-- Article count (~800 expected)
SELECT COUNT(*) FROM rag_knowledge_articles;

-- Chunk count with embeddings (~1500-3000)
SELECT COUNT(*) FROM rag_knowledge_chunks WHERE embedding IS NOT NULL;

-- Chunks above quality gate
SELECT COUNT(*) FROM rag_knowledge_chunks WHERE quality_score >= 0.55;

-- Knowledge class distribution
SELECT knowledge_class, COUNT(*) FROM rag_knowledge_articles
GROUP BY knowledge_class ORDER BY COUNT(*) DESC;
-- Expect: FAQ (largest), TROUBLESHOOTING, VERIFIED_REPLY, POLICY, RCA

-- Image metadata ingested
SELECT COUNT(*) FROM rag_knowledge_articles WHERE image_metadata != '[]'::JSONB;
-- Expected: non-zero (articles with images have metadata)

-- FTS column populated (GENERATED column auto-fills on insert)
SELECT COUNT(*) FROM rag_knowledge_chunks WHERE fts IS NOT NULL;
-- Expected: equals total chunk count

-- End-to-end retrieval test (run with a real query)
SELECT source_table, content, boosted_score,
       extra_metadata->>'image_metadata' AS img_meta
FROM match_all_b1_sources(
    -- paste a real 1536-dim embedding vector here, or use a test query
    NULL::VECTOR,  -- replace with actual embedding
    'unity_bank', 5, 0.0, 'v1'
)
WHERE source_table = 'rag_knowledge_chunks'
LIMIT 3;
```

---

## Step 8 — Hard-Delete Legacy SOPs

**IRREVERSIBLE — requires explicit user confirmation.**  
Only execute after Step 7 is verified and the new knowledge base is confirmed working.

```sql
-- Delete chunks first (FK CASCADE prevents orphan chunks, but explicit is safer)
DELETE FROM rag_sop_chunks
WHERE sop_id IN (
    SELECT sop_id FROM rag_sop_library WHERE source = 'fumadocs'
);

-- Delete parent library records
DELETE FROM rag_sop_library WHERE source = 'fumadocs';

-- Verify deletion
SELECT COUNT(*) AS remaining_legacy FROM rag_sop_library WHERE source = 'fumadocs';
-- Expected: 0

-- Drop backup tables (TRULY irreversible — only when fully confident)
-- DROP TABLE IF EXISTS rag_sop_library_backup_20260629;
-- DROP TABLE IF EXISTS rag_sop_chunks_backup_20260629;
```

**Rollback (only possible before backup tables are dropped):**
```sql
INSERT INTO rag_sop_library
    SELECT * FROM rag_sop_library_backup_20260629 ON CONFLICT DO NOTHING;
INSERT INTO rag_sop_chunks
    SELECT * FROM rag_sop_chunks_backup_20260629 ON CONFLICT DO NOTHING;
UPDATE rag_sop_library SET is_active = TRUE WHERE source = 'fumadocs';
```

---

## Migration Summary

| Step | Action | Reversible? | Confirmation |
|---|---|---|---|
| 1 | Pre-flight validation | N/A | Auto |
| 2 | Backup SOP tables | YES | Required |
| 3 | Soft-disable legacy SOPs | YES | Required |
| 4 | Apply B3_001–B3_007 migrations | YES (idempotent) | Required per file |
| 5 | Dry-run ingestion | YES (no-op) | Auto |
| 6 | Pre-migration spot-check | N/A | Auto |
| 7 | Live ingestion | YES (re-run or delete) | Required |
| 8 | Hard-delete legacy SOPs | **NO** | **Explicit human confirmation** |
