# SQL Migration Audit & Ordered Execution Plan

This document classifies every SQL file in the `/sql` directory and provides the
definitive ordered migration plan for a fresh Supabase deployment.

Last audited: 2026-05-20

---

## Classification

### REQUIRED — Run once, in order (fresh deployments)

| Order | File | Purpose | Safe to rerun? |
|-------|------|---------|---------------|
| 1 | `b1_migrations/B1_001_ticket_documents.sql` | Creates `rag_ticket_documents` table (B1 core) | Yes — uses `CREATE TABLE IF NOT EXISTS` |
| 2 | `b1_migrations/B1_002_sop_library.sql` | Creates `rag_sop_library` + `rag_sop_chunks` tables | Yes |
| 3 | `b1_migrations/B1_003_ingestion_logs.sql` | Creates `rag_ingestion_logs` + `rag_ingestion_errors` tables | Yes |
| 4 | `b1_migrations/B1_004_feedback_logs.sql` | Creates `rag_feedback_logs` table | Yes |
| 5 | `b1_migrations/B1_005_rpc_functions.sql` | Creates original `match_all_b1_sources` (ticket + SOP only) | Yes — `CREATE OR REPLACE FUNCTION` |
| 6 | `chat_messages.sql` | Creates `chat_messages` table for conversation history | Yes |
| 7 | `knowledge_cards.sql` | Creates `knowledge_cards` table for `/train/commit` endpoint | Yes |
| 8 | `fix_match_documents_final.sql` | **ALREADY RUN** — canonical `match_documents` RPC pointing to `documents` table | **DO NOT rerun** — will briefly drop the RPC (safe but causes 1–2s gap) |
| 9 | `fts_setup.sql` | Adds `fts` tsvector column + GIN index to `documents` (enables hybrid retrieval) | Yes — `ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS` |
| 10 | `fts_rpc.sql` | Creates `search_documents_fts()` RPC for real ts_rank scores | Yes — `CREATE OR REPLACE FUNCTION` |
| 11 | `b1_migrations/B1_006_indexes.sql` | HNSW + auxiliary indexes on B1 tables + RLS policies | Partial — `CREATE INDEX IF NOT EXISTS` is safe; `ALTER TABLE ENABLE ROW LEVEL SECURITY` is idempotent; `CREATE POLICY` will fail if policy already exists (add `IF NOT EXISTS` guard manually if rerunning) |
| 12 | `b3_migrations/B3_001_knowledge_tables.sql` | Creates `rag_knowledge_articles` + `rag_knowledge_chunks` tables | Yes |
| 13 | `b3_migrations/B3_002_extend_match_all_sources.sql` | Replaces B1_005 RPC with B3 version adding `rag_knowledge_chunks` UNION branch | Yes — `CREATE OR REPLACE FUNCTION` |
| 14 | `b3_migrations/B3_003_feedback_enhancements.sql` | Extends `rag_feedback_logs` + creates `rag_review_queue` | Yes — uses `ADD COLUMN IF NOT EXISTS` and `CREATE TABLE IF NOT EXISTS` |
| 15 | `b3_migrations/B3_004_raise_quality_gate.sql` | Adjusts quality gate thresholds for knowledge retrieval | Yes |

**Run order matters**: B1_001–B1_004 must precede B1_005. B3_001 must precede B3_002 and B3_003.

---

### OPTIONAL — Feature migrations (not required for core functionality)

| File | Purpose | When to apply |
|------|---------|--------------|
| `support_conversation_state.sql` | Conversation state tracking table | Apply if using stateful multi-turn conversations outside chat_messages |

---

### ALREADY RUN (do not rerun)

| File | Status | Risk if rerun |
|------|--------|--------------|
| `fix_match_documents_final.sql` | **APPLIED** (confirmed 2026-05-20) | LOW — drops and recreates RPC; causes a brief gap (~1s) in semantic search availability. Safe in maintenance window. |

---

### DANGEROUS / DO NOT RUN

| File | Why dangerous |
|------|--------------|
| `kb_chunks.sql` | Creates a `kb_chunks` table AND a `match_documents` function pointing to `kb_chunks` instead of `documents`. This is the ROOT CAUSE of the original RPC overload conflict. **Never run this again.** The conflict was fixed by `fix_match_documents_final.sql`. |
| `match_documents_documents.sql` | Earlier attempt to fix the overload — superseded by `fix_match_documents_final.sql`. Missing `SECURITY DEFINER` and does not drop all overload variants. Running would create an incomplete fix. |

---

### OBSOLETE / SUPERSEDED

| File | Superseded by |
|------|--------------|
| `kb_chunks.sql` | `fix_match_documents_final.sql` (for the RPC); `documents` table (for the data) |
| `match_documents_documents.sql` | `fix_match_documents_final.sql` |
| `b1_migrations/B1_005_rpc_functions.sql` | `b3_migrations/B3_002_extend_match_all_sources.sql` for `match_all_b1_sources`; `fix_match_documents_final.sql` for `match_documents` |

**Note**: `B1_005_rpc_functions.sql` must still be applied before `B3_002` on a fresh deployment since B3_002 uses `CREATE OR REPLACE` (not `CREATE`).

---

## Ordered Migration Plan (Fresh Deployment)

```bash
# Run these in order in Supabase SQL Editor or via psql:

# Phase 0: Extensions (should already exist in Supabase)
-- CREATE EXTENSION IF NOT EXISTS vector;
-- CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

# Phase 1: Core B1 tables
1. b1_migrations/B1_001_ticket_documents.sql
2. b1_migrations/B1_002_sop_library.sql
3. b1_migrations/B1_003_ingestion_logs.sql
4. b1_migrations/B1_004_feedback_logs.sql
5. b1_migrations/B1_005_rpc_functions.sql   # Initial RPC (ticket + SOP only)

# Phase 2: Shared infrastructure
6. chat_messages.sql
7. knowledge_cards.sql
8. fix_match_documents_final.sql             # Canonical match_documents → documents table

# Phase 3: Hybrid retrieval (run after data is indexed)
9. fts_setup.sql                             # FTS column + GIN index on documents
10. fts_rpc.sql                              # Real ts_rank RPC

# Phase 4: Performance (run after bulk ingestion complete)
11. b1_migrations/B1_006_indexes.sql         # HNSW + auxiliary + RLS

# Phase 5: Phase B3 (knowledge base)
12. b3_migrations/B3_001_knowledge_tables.sql
13. b3_migrations/B3_002_extend_match_all_sources.sql  # Replaces B1_005 RPC
14. b3_migrations/B3_003_feedback_enhancements.sql
15. b3_migrations/B3_004_raise_quality_gate.sql
```

---

## Hybrid Retrieval Dependencies

For `HYBRID_RETRIEVAL_ENABLED=true` to work correctly, these must all be applied:

1. `fts_setup.sql` — adds the `fts` tsvector column and GIN index to `documents`
2. `fts_rpc.sql` — adds `search_documents_fts()` for real ts_rank scores (optional but recommended)
3. `fix_match_documents_final.sql` — ensures `match_documents` RPC works (already applied)

Without `fts_setup.sql`, keyword search silently falls back to ILIKE which is less accurate.

---

## RPC Inventory (current state)

| Function | Table(s) | Owner | Status |
|----------|---------|-------|--------|
| `match_documents(vector, int, float)` | `documents` | canonical | **ACTIVE** (applied via fix_match_documents_final.sql) |
| `match_all_b1_sources(vector, text, int, float, text)` | `rag_ticket_chunks` + `rag_sop_chunks` + `rag_knowledge_chunks` | B3 version | **ACTIVE** (applied via B3_002) |
| `search_documents_fts(text, int)` | `documents` | hybrid retrieval | **REQUIRED** — apply `fts_rpc.sql` if not yet done |

---

## Schema Reference

| Table | Phase | Purpose |
|-------|-------|---------|
| `documents` | Legacy | Ingest target for MD/JSON/Excel/Freshdesk documents |
| `chat_messages` | Legacy | Conversation history for /chat endpoint |
| `knowledge_cards` | Legacy | Draft knowledge cards from /train endpoint |
| `rag_ticket_documents` | B1 | Freshdesk ticket metadata |
| `rag_ticket_chunks` | B1 | Chunked + embedded ticket content |
| `rag_sop_library` | B1 | SOP document metadata |
| `rag_sop_chunks` | B1 | Chunked + embedded SOP content |
| `rag_ingestion_logs` | B1 | Ingestion run tracking |
| `rag_ingestion_errors` | B1 | Per-batch ingestion errors |
| `rag_feedback_logs` | B1/B3 | Agent feedback on AI drafts |
| `rag_knowledge_articles` | B3 | Knowledge article metadata |
| `rag_knowledge_chunks` | B3 | Chunked + embedded knowledge content |
| `rag_review_queue` | B3 | Human review workflow |
| `kb_chunks` | OBSOLETE | Do not use — conflicts with match_documents RPC |
