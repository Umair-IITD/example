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
| 16 | `b1_migrations/B1_007_fts_setup.sql` | Adds `fts` tsvector column + GIN indexes to `rag_ticket_chunks`, `rag_sop_chunks`, `rag_knowledge_chunks` — required for B1 hybrid retrieval | Yes — `ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS` |
| 17 | `b1_migrations/B1_008_fts_rpc.sql` | Creates `search_b1_sources_fts()` RPC — unified FTS across all three B1 RAG tables | Yes — `CREATE OR REPLACE FUNCTION` |

**Run order matters**: B1_001–B1_004 must precede B1_005. B3_001 must precede B3_002 and B3_003. B1_007 and B1_008 must run after all B1 and B3 table migrations.

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

# Phase 6: B1/B3 hybrid retrieval FTS (run after all B1+B3 tables exist and are populated)
16. b1_migrations/B1_007_fts_setup.sql        # FTS columns + GIN indexes on all 3 RAG chunk tables
17. b1_migrations/B1_008_fts_rpc.sql          # search_b1_sources_fts() RPC for hybrid retrieval
# After running B1_007 + B1_008: set B1_HYBRID_RETRIEVAL_ENABLED=true in .env
```

---

## Hybrid Retrieval Dependencies

### Legacy `documents` table hybrid retrieval (`HYBRID_RETRIEVAL_ENABLED=true`):
1. `fts_setup.sql` — FTS column + GIN index on `documents`
2. `fts_rpc.sql` — `search_documents_fts()` RPC for real ts_rank scores
3. `fix_match_documents_final.sql` — canonical `match_documents` RPC (already applied)

### B1/B3 RAG tables hybrid retrieval (`B1_HYBRID_RETRIEVAL_ENABLED=true`):
1. All B1 migrations (B1_001–B1_006) — core tables and indexes
2. All B3 migrations (B3_001–B3_004) — knowledge tables
3. `b1_migrations/B1_007_fts_setup.sql` — FTS on all three RAG chunk tables
4. `b1_migrations/B1_008_fts_rpc.sql` — `search_b1_sources_fts()` unified RPC

Both hybrid modes degrade gracefully — if the FTS RPC is unavailable, the retriever
falls back silently to semantic-only mode.

---

## v1 → v2 Reingestion (token-aware chunking)

To migrate B1 data from word-based (v1) to token-aware (v2) chunking:

```bash
# 1. Validate current state
python scripts/reingest_v2.py --client unity_bank --dry-run

# 2. Reingest a single tenant (writes index_version=v2 rows, leaves v1 intact)
python scripts/reingest_v2.py --client unity_bank

# 3. After validation, activate v2 serving:
#    Set ACTIVE_INDEX_VERSION=v2 (or B1_INDEX_VERSION=v2) and restart

# 4. Reingest all tenants:
python scripts/reingest_v2.py --all-clients

# 5. Rollback v2 if issues found:
python scripts/reingest_v2.py --client unity_bank --rollback
```

v1 and v2 rows coexist in the same tables. The application reads from whichever
index_version matches `B1_INDEX_VERSION` (default: v1). Flip to v2 after validation.

---

## RPC Inventory (current state)

| Function | Table(s) | Owner | Status |
|----------|---------|-------|--------|
| `match_documents(vector, int, float)` | `documents` | canonical | **ACTIVE** (applied via fix_match_documents_final.sql) |
| `match_all_b1_sources(vector, text, int, float, text)` | `rag_ticket_chunks` + `rag_sop_chunks` + `rag_knowledge_chunks` | B3 version | **ACTIVE** (applied via B3_002) |
| `search_documents_fts(text, int)` | `documents` | legacy hybrid | **REQUIRED** — apply `fts_rpc.sql` if not yet done |
| `search_b1_sources_fts(text, text, int, text)` | `rag_ticket_chunks` + `rag_sop_chunks` + `rag_knowledge_chunks` | B1/B3 hybrid | **PENDING** — apply `b1_migrations/B1_008_fts_rpc.sql` |

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
