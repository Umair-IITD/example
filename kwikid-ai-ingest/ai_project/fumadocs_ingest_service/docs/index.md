# KwikID AI Ingest — Master Documentation Index

**Project:** Think360.ai KwikID AI Support Automation  
**Repository:** `fumadocs_ingest_service/`  
**Last updated:** 2026-05-14  
**Current phase:** B1 implemented + offline-validated. Live validation blocked pending SQL migrations.

---

## System Overview

AI-powered knowledge ingestion and retrieval service for Think360's KwikID KYC platform.

**Stack:** FastAPI · Supabase pgvector · OpenAI text-embedding-3-small · GPT-4o-mini · httpx

**Data flow:**
```
Support tickets (Excel/Freshdesk) → Ingest pipeline → Supabase vector DB
New support query → Retrieve similar tickets → GPT-4o-mini draft → Freshdesk private note
```

**Tenants:** 24 client organizations. Dominant: `unity_bank`.  
**Scale:** 3,635 source rows → 1,826 documents → 5,817 chunks (offline dry-run).

---

## Current Status

| Phase | Status |
|-------|--------|
| **B1** — RAG ingestion + retrieval | OFFLINE VALIDATED · LIVE BLOCKED |
| **B2** — LLM generation | IMPLEMENTED · DISABLED |
| **B3** — Freshdesk webhook | IMPLEMENTED · DISABLED |
| **B1.5** — SOP ingestion + eval | PLANNED |

**Immediate blocker:** Apply SQL migrations B1_001–B1_006 to Supabase.

---

## Quick Links

### Setup & Migration
- [Local Setup Guide](local_setup_guide.md) — Fresh machine setup from zero to running
- [Manual Migration Steps](manual_migration_steps.md) — Exact Supabase migration instructions
- [Supabase State](supabase_state.md) — Tables, row counts, integrity validation SQL

### Phase Status & Roadmap
- [Current Project State](current_project_state.md) — Phase-by-phase status, gates, blockers
- [Development Rules](development_rules.md) — 12 binding rules for safe development
- [NEXT_PHASE_PLAN.md](../NEXT_PHASE_PLAN.md) — B1.5, B2, B3 detailed task breakdown

### AI Agent Context
- [AI Agent Handoff](ai_agent_handoff.md) — **START HERE** if you are an AI assistant picking up this project

### B1 Technical Reference
- [B1 Chunking Strategy](b1_chunking_strategy.md) — 3-chunk strategy, token flow, splitting algorithm
- [B1 Embedding Pipeline](b1_embedding_pipeline.md) — Batch lifecycle, retry, circuit breaker, streaming upsert
- [B1 Validation Status](b1_validation_status.md) — What passed, what was fixed, next commands

### Security
- [Security Review](security_review.md) — Full audit with severity ratings
- [Security Checklist](security_checklist.md) — Pre-commit, pre-migration, endpoint checklists

### Repository Health
- [Archive Candidates](archive_candidates.md) — Dead scripts and obsolete files for review
- [Final Pre-Migration Status](final_pre_migration_status.md) — Migration readiness snapshot + SAFE NEXT STEP CHECKLIST

---

## Key Files

| File | Purpose |
|------|---------|
| `rag_engine/ingestion/pipeline.py` | Ingestion orchestrator (load → chunk → dedup → embed → upsert) |
| `rag_engine/utils/tokens.py` | Token counting, safe splitting, recursive fallback |
| `rag_engine/embedding/openai_provider.py` | OpenAI embedding API with retry + resilience |
| `rag_engine/embedding/batch_processor.py` | Batch processor: circuit breaker + pre-embed validation |
| `rag_engine/retrieval/ticket_retriever.py` | Semantic search + SOP boost + reranking |
| `rag_engine/chunking/ticket_chunker.py` | 3-chunk strategy: ISSUE_HEADER | QUERY_BODY | RESOLUTION_RCA |
| `app/main.py` | FastAPI routes (all endpoints) |
| `app/config.py` | Settings dataclass + validation |
| `app/freshdesk_webhook.py` | B3 webhook handler (DISABLED) |
| `sql/b1_migrations/` | B1_001–B1_006 (NOT YET APPLIED) |

---

## Key CLI Commands

```powershell
# Offline validations (always safe to run)
python scripts/validate_b1_tokens.py
python scripts/validate_b1_infrastructure.py
python scripts/validate_b1_ingestion.py        # dry-run

# Security scan (run before every commit)
python scripts/security_scan.py

# Live ingestion (requires .env + SQL migrations applied)
python -m rag_engine.cli.ingest_cli --mode full --dry-run  # safe preview
python -m rag_engine.cli.ingest_cli --mode full            # live run (~$0.03)

# Live validations
python scripts/validate_b1_ingestion.py --live --report
python scripts/validate_b1_retrieval.py --live --client unity_bank --report
python scripts/validate_b1_db_integrity.py --live --report

# Start API server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## Architecture Decisions (Why Things Are the Way They Are)

| Decision | Why |
|----------|-----|
| 3-chunk strategy (not sliding window) | Different chunks answer different retrieval questions |
| UUID5 deterministic chunk IDs | Idempotent upserts without pre-checks |
| Streaming embed+upsert (not batch-then-upsert) | Resume on crash — dedup checker skips already-embedded chunks |
| `B1_INDEX_VERSION` env var | Clean rebuild without DROP TABLE — bump to `v2` when switching models |
| `FRESHDESK_WEBHOOK_ENABLED=false` default | B3 disabled until B1 live-validated |
| tiktoken + character fallback + recursive halving | Three-layer defense against 8192-token OpenAI limit |
| 7-check pre-embed validation | Reject pathological inputs before paying for an API call |
| Circuit breaker in BatchEmbeddingProcessor | Prevent hours of API hammering during outages |

---

## Contact & Context

**Organization:** Think360.ai  
**Service:** KwikID KYC platform support automation  
**Stage:** Active development — B1 hardening complete, pre-migration  
**Historical chat:** `C:\Users\HP\.claude\projects\C--Users-HP-Desktop-Think360\` (session logs)
