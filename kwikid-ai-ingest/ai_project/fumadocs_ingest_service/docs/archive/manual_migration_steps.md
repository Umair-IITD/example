# Manual Migration Steps

**Purpose:** Exact instructions for applying Phase B1 SQL migrations to Supabase.  
**Date:** 2026-05-14  
**Estimated time:** 5–10 minutes

---

## Prerequisites

Before starting:

- [ ] Supabase project is accessible (you can log in to the dashboard)
- [ ] `pgvector` extension is enabled: Dashboard → Database → Extensions → search "vector" → enable
- [ ] You have the `service_role` key for the `SUPABASE_KEY` env var
- [ ] You have the project URL for `SUPABASE_URL` env var
- [ ] `.env` file is created from `.env.example` with real values

---

## Step 1 — Enable pgvector Extension

In Supabase Dashboard:
1. Go to **Database** → **Extensions**
2. Search for `vector`
3. Click **Enable**

Or run in SQL editor:
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Verify: `SELECT * FROM pg_extension WHERE extname = 'vector';` — should return 1 row.

---

## Step 2 — Apply B1_001 (Core Tables)

File: `sql/b1_migrations/B1_001_ticket_documents.sql`

1. Open Supabase Dashboard → **SQL Editor**
2. Paste the full contents of `B1_001_ticket_documents.sql`
3. Click **Run**
4. Verify: no error output

Validation:
```sql
SELECT table_name FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_name IN ('rag_ticket_documents', 'rag_ticket_chunks');
-- Expected: 2 rows
```

---

## Step 3 — Apply B1_002 (SOP Library)

File: `sql/b1_migrations/B1_002_sop_library.sql`

Paste and run in SQL editor.

Validation:
```sql
SELECT table_name FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_name IN ('rag_sop_library', 'rag_sop_chunks');
-- Expected: 2 rows
```

---

## Step 4 — Apply B1_003 (Ingestion Logs)

File: `sql/b1_migrations/B1_003_ingestion_logs.sql`

Paste and run in SQL editor.

Validation:
```sql
SELECT table_name FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_name = 'rag_ingestion_logs';
-- Expected: 1 row
```

---

## Step 5 — Apply B1_004 (Feedback Logs)

File: `sql/b1_migrations/B1_004_feedback_logs.sql`

Paste and run in SQL editor.

Validation:
```sql
SELECT table_name FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_name = 'rag_feedback_logs';
-- Expected: 1 row
```

---

## Step 6 — Apply B1_005 (RPC Functions)

File: `sql/b1_migrations/B1_005_rpc_functions.sql`

Paste and run in SQL editor.

Validation:
```sql
SELECT routine_name FROM information_schema.routines
WHERE routine_schema = 'public'
AND routine_name LIKE '%b1%' OR routine_name LIKE 'match_%';
```

---

## Step 7 — Apply B1_006 (Indexes)

File: `sql/b1_migrations/B1_006_indexes.sql`

**IMPORTANT:** Apply this LAST. The HNSW index requires the tables from B1_001 to exist.

Paste and run in SQL editor. This may take a few seconds if there is existing data.

Validation:
```sql
SELECT indexname, tablename FROM pg_indexes 
WHERE tablename = 'rag_ticket_chunks'
AND indexname LIKE '%embed%' OR indexname LIKE '%hnsw%';
-- Expected: at least 1 index
```

---

## Step 8 — Final Verification

Run all 6 validation queries to confirm all migrations applied cleanly:

```sql
-- All 6 B1 tables exist
SELECT table_name FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_name IN (
  'rag_ticket_documents', 'rag_ticket_chunks',
  'rag_sop_library', 'rag_sop_chunks',
  'rag_ingestion_logs', 'rag_feedback_logs'
)
ORDER BY table_name;
-- Expected: 6 rows
```

---

## Step 9 — Configure `.env` and Run Ingestion

```powershell
# In repo root
copy .env.example .env
# Edit .env and set:
#   SUPABASE_URL=https://your-real-project.supabase.co
#   SUPABASE_KEY=your_service_role_key
#   OPENAI_API_KEY=sk-...
#   EMBEDDING_PROVIDER=openai

# Dry-run first (no DB writes)
python -m rag_engine.cli.ingest_cli --mode full --dry-run

# Live ingestion (~15 min, ~$0.03 cost)
python -m rag_engine.cli.ingest_cli --mode full

# Validate
python scripts/validate_b1_ingestion.py --live --report
python scripts/validate_b1_retrieval.py --live --client unity_bank --report
python scripts/validate_b1_db_integrity.py --live --report
```

---

## Rollback Instructions

If any migration causes errors and you need to undo:

```sql
-- Rollback in REVERSE order
DROP TABLE IF EXISTS rag_feedback_logs CASCADE;
DROP TABLE IF EXISTS rag_ingestion_logs CASCADE;
DROP TABLE IF EXISTS rag_sop_chunks CASCADE;
DROP TABLE IF EXISTS rag_sop_library CASCADE;
DROP TABLE IF EXISTS rag_ticket_chunks CASCADE;
DROP TABLE IF EXISTS rag_ticket_documents CASCADE;

-- Drop RPC functions (check names from B1_005)
-- DROP FUNCTION IF EXISTS match_rag_ticket_chunks(...);
-- DROP FUNCTION IF EXISTS _b1_audit_duplicate_chunks();
```

**Safe:** These tables are all new (B1). Dropping them does NOT affect `public.documents` (old RAG) or `chat_messages`.

---

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `extension "vector" does not exist` | pgvector not enabled | Enable via Dashboard → Extensions |
| `relation "rag_ticket_documents" already exists` | Migration already applied | Check which migrations are done; skip those |
| `function match_rag_ticket_chunks() does not exist` | B1_005 not applied | Apply B1_005 |
| `column "embedding" is of type vector but expression is of type text` | Wrong pgvector version | Ensure pgvector >= 0.5.0 |
