# AI Agent Handoff Document

**Purpose:** This document gives an AI coding assistant (Cursor, Claude Code, Copilot) complete context to continue development safely without historical chat memory.

**Date:** 2026-05-14  
**Status:** Pre-migration checkpoint. Read this entire document before making any code changes.

---

## What This System Does

This is **KwikID AI Ingest** — a production-grade multi-tenant RAG (Retrieval-Augmented Generation) platform built for Think360.ai's KwikID KYC support desk.

**Goal:** Transform fully-manual support (human reads ticket → investigates → responds) into AI-assisted support (AI drafts a grounded answer from the knowledge base → human reviews and approves).

**Current capability:**
- Ingest historical support tickets into a vector database
- Retrieve semantically similar past tickets when a new support query arrives
- Generate an AI draft response grounded in real historical resolutions
- Post the draft as a private Freshdesk note for human review

---

## System Architecture

```
Data Sources
  ├── Excel spreadsheet (support tickets: ~3,635 rows)
  ├── Freshdesk API (live tickets)
  ├── Markdown docs (Fumadocs/Bitbucket)
  └── JSON (StackOverflow Teams Q&A)
          │
          ▼
Ingestion Pipeline (rag_engine/ingestion/pipeline.py)
  Load → Normalize → Build RagTicketDocument → Chunk (3 types) →
  Dedup (SHA256) → Embed (OpenAI text-embedding-3-small) → Upsert (Supabase pgvector)
          │
          ▼
Vector Store (Supabase / PostgreSQL + pgvector)
  ├── rag_ticket_documents  (1,826 expected rows)
  └── rag_ticket_chunks     (5,817 expected rows, 1536-dim HNSW index)
          │
          ▼
Retrieval (rag_engine/retrieval/ticket_retriever.py)
  Embed query → Cosine similarity search → SOP boost (+0.15) → Rerank → Filter by confidence
          │
          ▼
Generation (rag_engine/generation/chat_generator.py) [DISABLED until B1 validated]
  Assemble context → Build prompt → GPT-4o-mini → Structured response
          │
          ▼
Freshdesk Webhook (app/freshdesk_webhook.py) [DISABLED until B1 validated]
  Receive ticket event → Resolve tenant → RAG pipeline → Post private note
```

---

## Repository Structure

```
fumadocs_ingest_service/
├── app/                          ← FastAPI application
│   ├── main.py                   ← API routes (all endpoints)
│   ├── config.py                 ← Settings (dataclass, env vars, validation)
│   ├── freshdesk_webhook.py      ← B3 webhook handler (DISABLED)
│   └── [legacy: chat.py, embedder.py, etc.]
├── rag_engine/                   ← Core RAG pipeline (B1)
│   ├── config/rag_settings.py    ← B1-specific settings
│   ├── schemas/                  ← Pydantic models (ticket_document, chunk_schema)
│   ├── document_builder/         ← Raw row → RagTicketDocument
│   ├── chunking/ticket_chunker.py ← 3-chunk strategy
│   ├── embedding/                ← OpenAI provider + batch processor
│   ├── ingestion/                ← Pipeline, dedup, delta, schema mapper
│   ├── retrieval/ticket_retriever.py ← Semantic search + reranking
│   ├── generation/               ← LLM generation (B2, DISABLED)
│   ├── feedback/feedback_loop.py ← Feedback RLHF loop (unused)
│   ├── utils/tokens.py           ← Token counting, splitting, truncation
│   └── cli/ingest_cli.py         ← CLI entrypoint
├── sql/
│   ├── b1_migrations/            ← B1_001 through B1_006 (NOT YET APPLIED)
│   └── [legacy SQL files]
├── scripts/
│   ├── validate_b1_*.py          ← 4 offline + 1 live-only validation scripts
│   └── security_scan.py          ← Secret scanner
├── docs/                         ← Architecture and process documentation
│   └── [this file and all other docs]
├── data/
│   ├── excel/                    ← Source Excel files (gitignored)
│   ├── processed/                ← Parquet outputs from dataset_pipeline
│   └── reports/                  ← Auto-generated validation reports
├── dataset_pipeline/             ← Data preprocessing pipeline (Excel → parquet)
├── .env.example                  ← Template (no real secrets)
└── .gitignore                    ← Created 2026-05-14
```

---

## Three-Chunk Strategy (CRITICAL — Do Not Change)

Every ticket produces exactly 3 chunks:

| Chunk Type | Content | Embedding Purpose |
|------------|---------|-------------------|
| `ISSUE_HEADER` | Ticket ID, subject, client, type, area, status | "What kind of problem is this?" |
| `QUERY_BODY` | Full customer description (HTML-cleaned) | "What did the customer say?" |
| `RESOLUTION_RCA` | Agent resolution + root cause | "How was this fixed?" |

Chunk IDs are UUID5 deterministic: `ticket_id + chunk_index + B1_INDEX_VERSION`.  
**Changing this formula forces a full re-ingest.** Do not change it without updating `B1_INDEX_VERSION`.

---

## Phase Status (What You Can and Cannot Do)

### Phase B1 — STABLE (offline) / BLOCKED (live)

**CAN DO:**
- Run offline validation scripts
- Read/analyze any B1 code
- Fix bugs in B1 without changing the chunking strategy or chunk IDs
- Write new test cases for B1

**CANNOT DO YET (waiting for SQL migrations + live ingestion):**
- Run live ingestion
- Run live retrieval tests
- Enable B3 webhook
- Start B1.5 work

**BLOCKER:** Apply SQL migrations B1_001–B1_006 to Supabase. See `docs/manual_migration_steps.md`.

### Phase B2 — IMPLEMENTED, DISABLED

The `rag_engine/generation/` module and `/rag/chat` endpoint exist and compile correctly. Do NOT expose `/rag/chat` publicly until B1 live-validation is complete. Do NOT add features to B2 code.

### Phase B3 — IMPLEMENTED, DISABLED by feature flag

`app/freshdesk_webhook.py` exists. `FRESHDESK_WEBHOOK_ENABLED=false` in `.env.example`. Do NOT set this to `true` until B1 is live-validated.

### B1.5, B2, B3 extensions — NOT STARTED

Do not begin any new feature work until the B1 live-validation sequence completes.

---

## Key Design Decisions and Why

### 1. Streaming Embed+Upsert (not batch-then-upsert)
**Decision:** Each embedding batch is upserted to the DB immediately via `on_batch_embedded` callback.  
**Why:** If the process crashes mid-embedding, re-running re-embeds only the missed chunks. The deduplication checker identifies unchanged chunks via SHA256 content hash and skips them.

### 2. Pre-Embed Validation (7 checks before any API call)
**Decision:** Invalid chunks (empty, whitespace, repetitive, base64 blobs, control chars, oversized) are skipped before embedding.  
**Why:** OpenAI returns errors or garbage embeddings for pathological inputs. Detection is cheap; API call is not.

### 3. Token-Aware Splitting with Recursive Fallback
**Decision:** tiktoken-based splitting → word-based fallback → recursive halving for pathological blobs.  
**Why:** OpenAI's text-embedding-3-small has an 8192-token hard limit. The original crash was caused by oversized chunks. Three-layer defense ensures nothing oversized reaches the API.

### 4. Multi-Tenant Isolation (3 layers)
**Decision:** App-level `client` filter (mandatory) + Supabase RLS policies + RPC functions with row filtering.  
**Why:** `unity_bank` data must never appear in a query for `other_bank`. Single-layer isolation (RLS alone) is not enough if the service_role key is used.

### 5. `B1_INDEX_VERSION` for Clean Rebuilds
**Decision:** All chunk IDs include `B1_INDEX_VERSION` (default `v1`). Bump to `v2` to invalidate all old chunks without a DROP TABLE.  
**Why:** Allows switching embedding models or chunking strategies without corrupting existing data. Old chunks with `v1` remain until explicitly deleted.

### 6. Feature Flags for Incomplete Phases
**Decision:** `FRESHDESK_WEBHOOK_ENABLED=false` disables B3. `HYBRID_RETRIEVAL_ENABLED=false` disables hybrid search. `ENABLE_QUERY_ROUTER=false` disables query routing.  
**Why:** B2/B3 code is implemented but not validated. Feature flags prevent accidental use in production while preserving the implementation.

---

## What NOT to Modify

| File/Module | Reason |
|-------------|--------|
| `sql/b1_migrations/` | Migrations applied to production cannot be changed retroactively |
| `rag_engine/schemas/chunk_schema.py` `TicketChunk.build()` | UUID5 formula — changing breaks idempotency |
| `rag_engine/ingestion/deduplication.py` | Core idempotency mechanism |
| `rag_engine/utils/tokens.py` | Token safety — any change needs re-running validate_b1_tokens.py |
| `.env.example` | Must remain placeholder-only — no real values |
| `rag_engine/retrieval/ticket_retriever.py` `_fallback_search()` | Just fixed — warns loudly, filters RCA-only |

---

## How to Continue Development Safely

### The Right Sequence

```
1. Read docs/current_project_state.md  ← understand what's done and what's blocked
2. Run offline validation scripts      ← confirm local environment is correct
3. Apply SQL migrations (manual)       ← follow docs/manual_migration_steps.md
4. Configure .env                      ← use docs/local_setup_guide.md
5. Run live ingestion + validation     ← confirm B1 is working end-to-end
6. THEN start B1.5 work                ← see NEXT_PHASE_PLAN.md
```

### Before Any Code Change

1. Understand which phase the change touches (B1, B1.5, B2, B3)
2. Confirm that phase's validation gate has been passed
3. Run the relevant validation script after the change
4. If changing token logic: re-run `python scripts/validate_b1_tokens.py` (must be 19/19)
5. If changing ingestion: re-run `python scripts/validate_b1_ingestion.py` (must be 6/6 dry-run)

### Before Any Git Commit

```powershell
python scripts/security_scan.py  # must exit 0
```

---

## Environment Variables — Critical Ones

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `SUPABASE_URL` | YES | (none) | Project URL — not a secret but sensitive |
| `SUPABASE_KEY` | YES | (none) | SERVICE ROLE KEY — treat as root password |
| `OPENAI_API_KEY` | YES (for live) | (none) | Used for embeddings AND chat |
| `EMBEDDING_PROVIDER` | YES | `ollama` | Set to `openai` for production |
| `EMBEDDING_MODEL` | YES | `nomic-embed-text` | Set to `text-embedding-3-small` for production |
| `B1_INDEX_VERSION` | YES | `v1` | NEVER change while live data exists with `v1` |
| `FRESHDESK_WEBHOOK_ENABLED` | NO | `false` | Keep `false` until B1 validated |
| `EMBEDDING_MAX_INPUT_TOKENS` | NO | `7000` | Hard cap per chunk — do not exceed 8192 |

---

## Testing Strategy

| Test Type | How to Run | When to Run |
|-----------|-----------|------------|
| Token safety (offline) | `python scripts/validate_b1_tokens.py` | After any change to `tokens.py` or chunking |
| Infrastructure (offline) | `python scripts/validate_b1_infrastructure.py` | On new machine setup |
| Ingestion dry-run | `python scripts/validate_b1_ingestion.py` | After any change to pipeline |
| Retrieval (offline) | `python scripts/validate_b1_retrieval.py` | After any change to retriever |
| DB integrity (live) | `python scripts/validate_b1_db_integrity.py --live` | After live ingestion |
| Security scan | `python scripts/security_scan.py` | Before every commit |

---

## Known Architectural Debt (Do Not Fix Now)

These are known issues in B1, documented in `REVIEW.md`, deferred to B1.5:

1. `_sha256()` duplicated in 3 files → needs `rag_engine/utils/hashing.py`
2. `_word_count()` duplicated in 2 files → needs `rag_engine/utils/text.py`
3. `MetricsCollector` in `rag_engine/observability/` is instantiated but never used
4. `sample_retrieval.py` in `rag_engine/cli/` is not imported or tested
5. Root-level `retrieval/`, `query_router/`, `observability/` are pre-B1 stubs, not connected

**Do not fix these during B1 validation.** They are low-risk and non-blocking. Fix them as part of B1.5 utility extraction.

---

## If Something Goes Wrong

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `table "rag_ticket_documents" does not exist` | SQL migrations not applied | See `docs/manual_migration_steps.md` |
| `OPENAI_API_KEY is missing` | `.env` not configured | `copy .env.example .env` then edit |
| Ingestion crash mid-run | Network error or API rate limit | Re-run — dedup checker resumes automatically |
| `validate_b1_tokens.py` fails on new machine | tiktoken not installed | `pip install tiktoken` |
| Webhook returns 503 | `FRESHDESK_WEBHOOK_ENABLED=false` | Expected — do not enable yet |
| `/rag/chat` returns empty or low-confidence | No live data in DB yet | Run ingestion first |
| circuit breaker trips | OpenAI API unavailable | Wait, then re-run — CB resets automatically |
