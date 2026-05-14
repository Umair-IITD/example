# Supabase State

**Date:** 2026-05-14  
**Status:** MIGRATIONS NOT APPLIED. All B1 tables are defined in SQL but do not yet exist in the Supabase project.

---

## Project Configuration

| Field | Value |
|-------|-------|
| Project URL | Stored in `SUPABASE_URL` env var (never hardcode) |
| Key type needed | `service_role` (bypasses RLS — server-side only) |
| Env var | `SUPABASE_KEY` |
| SDK | `supabase-py` (Python) |
| Vector extension | `pgvector` — must be enabled in Supabase before applying migrations |

---

## Tables: Pre-B1 (Already Exist in Production Supabase)

These tables were created before Phase B1 and may already exist:

| Table | Purpose | Notes |
|-------|---------|-------|
| `public.documents` | Old vector store (pre-B1 RAG) | 1536-dim vectors, `text-embedding-ada-002` |
| `chat_messages` | Chat history | Schema in `sql/chat_messages.sql` |

Do NOT drop or modify `public.documents` — it is still used by the old `/query` and `/chat` (legacy) endpoints.

---

## Tables: B1 (Defined, NOT YET APPLIED)

All 6 tables are defined in `sql/b1_migrations/`. They do NOT exist in Supabase yet.

| Table | Migration | Purpose |
|-------|-----------|---------|
| `rag_ticket_documents` | B1_001 | Parent document record per ticket |
| `rag_ticket_chunks` | B1_001 | Embedding chunks with pgvector (1536-dim) |
| `rag_sop_library` | B1_002 | SOP document records |
| `rag_sop_chunks` | B1_002 | SOP embedding chunks |
| `rag_ingestion_logs` | B1_003 | Per-run ingestion metrics and status |
| `rag_feedback_logs` | B1_004 | User feedback for RLHF loop |

---

## Migration Files (Apply in Order)

| File | Tables/Indexes Created | Must Apply Before |
|------|------------------------|-------------------|
| `sql/b1_migrations/B1_001_ticket_documents.sql` | `rag_ticket_documents`, `rag_ticket_chunks` | Everything else |
| `sql/b1_migrations/B1_002_sop_library.sql` | `rag_sop_library`, `rag_sop_chunks` | B1.5 SOP ingestion |
| `sql/b1_migrations/B1_003_ingestion_logs.sql` | `rag_ingestion_logs` | Live ingestion run |
| `sql/b1_migrations/B1_004_feedback_logs.sql` | `rag_feedback_logs` | B3 webhook |
| `sql/b1_migrations/B1_005_rpc_functions.sql` | RPC functions for vector search | Retrieval |
| `sql/b1_migrations/B1_006_indexes.sql` | HNSW vector index on embeddings | Retrieval performance |

---

## Vector Index Details

| Field | Value |
|-------|-------|
| Index type | HNSW (Hierarchical Navigable Small World) |
| Distance function | Cosine similarity |
| Column | `rag_ticket_chunks.embedding` |
| Dimensions | 1536 |
| Embedding model | `text-embedding-3-small` (OpenAI) |
| Index created by | `B1_006_indexes.sql` |

**IMPORTANT:** The HNSW index in B1_006 must be applied after B1_001 creates the table. Do not apply out of order.

---

## Expected Row Counts After First Successful Ingestion

| Table | Expected Rows | Notes |
|-------|---------------|-------|
| `rag_ticket_documents` | ~1,826 | From 3,635 source rows (ESCALATION + no-content excluded) |
| `rag_ticket_chunks` | ~5,817 | 3.19 avg chunks/doc |
| `rag_ingestion_logs` | 1 | One completed run |
| `rag_sop_library` | 0 | Not ingested yet (B1.5) |
| `rag_sop_chunks` | 0 | Not ingested yet (B1.5) |
| `rag_feedback_logs` | 0 | No feedback yet |

---

## Tenant Distribution (From Dry-Run)

| Tenant (client) | Source Rows | Est. Docs |
|-----------------|------------|-----------|
| unity_bank | 2,726 | ~1,373 |
| 23 other clients | 909 | ~453 |
| ESCALATION (excluded) | 1,304 | 0 |

The dominant tenant is `unity_bank`. All retrieval calls require a `client` field — there is no cross-tenant search.

---

## Integrity Validation Commands

Run these after applying migrations and ingesting data:

```sql
-- 1. Confirm all 6 tables exist
SELECT table_name FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_name LIKE 'rag_%'
ORDER BY table_name;

-- 2. Check row counts
SELECT 'rag_ticket_documents' AS tbl, count(*) FROM rag_ticket_documents
UNION ALL SELECT 'rag_ticket_chunks', count(*) FROM rag_ticket_chunks
UNION ALL SELECT 'rag_ingestion_logs', count(*) FROM rag_ingestion_logs;

-- 3. Check for null embeddings (should be 0 after successful run)
SELECT count(*) FROM rag_ticket_chunks WHERE embedding IS NULL;

-- 4. Check for orphaned chunks
SELECT count(*) FROM rag_ticket_chunks WHERE document_id IS NULL;

-- 5. Check for null client (tenant isolation)
SELECT count(*) FROM rag_ticket_chunks WHERE client IS NULL;

-- 6. Vector dimension check (must all be 1536)
SELECT array_length(embedding, 1) AS dims, count(*) AS chunk_count
FROM rag_ticket_chunks
WHERE embedding IS NOT NULL
GROUP BY dims
ORDER BY chunk_count DESC;

-- 7. Last ingestion run status
SELECT run_id, run_mode, status, documents_processed, chunks_created, completed_at
FROM rag_ingestion_logs
ORDER BY completed_at DESC
LIMIT 1;
```

---

## Rollback Strategy

If a migration fails or produces unexpected results:

1. **Do NOT run the next migration** — stop at the failing step
2. Log into Supabase SQL editor
3. Run the `DROP TABLE IF EXISTS` statements from the migration file to undo it
4. Fix the issue (network, credentials, syntax)
5. Re-run the migration from the beginning

The B1 migrations are additive (only CREATE TABLE, no ALTER on existing tables). They are safe to re-run if the tables were dropped first.

---

## Known Issues

- `rag_ticket_chunks.client` field must be non-null for tenant isolation. The pipeline always sets this from `RagTicketDocument.client`. If you see null clients after ingestion, the schema mapper failed to resolve the client slug.
- `rag_sop_library` and `rag_sop_chunks` tables will exist after B1_002 but will be empty until the B1.5 SOP ingestion pipeline is built.
- The `_b1_audit_duplicate_chunks` RPC function (used by `validate_b1_db_integrity.py`) is defined in B1_005. If the script falls back to a raw table scan for duplicates, B1_005 was not applied.
