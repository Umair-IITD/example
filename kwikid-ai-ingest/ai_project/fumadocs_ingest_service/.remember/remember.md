# Handoff

## State
Sprint 2.36A COMPLETE. Knowledge Layer v1.0 CERTIFIED GO (2026-07-02). Mathematical audit passed all 12 phases: 325 articles / 1,314 chunks / 0 null embeddings / 0 orphans / 0 duplicates / 0 missing. One gap found and repaired (so_1517 "NRFSI zip recovery" — targeted mini-export re-ingestion). Benchmark: 30/30 queries 100%, retrieval reconciliation 99/100 articles (99%).

## Next
Pending tasks (priority order): #10 async FastAPI handlers (asyncio.to_thread), #11 Prometheus metrics, #12 Docker hardening (multi-stage, non-root, health checks), #13 Redis + v2 chunking config fields, #14 SQL migrations B1_007/B1_008 (FTS for RAG tables).

## Context
- `exclude_escalation=True` in `case_engine/knowledge/rag_adapter.py:83` — NEVER revert (security fix)
- `index_version='v2'` must be passed in every `RetrievalRequest` that should include knowledge chunks; default `'v1'` excludes them
- `B3_KNOWLEDGE_SOURCE_DIR=C:/Users/Umair.Alam/Desktop/kwikid_support_system/stackoverflow` (absolute path in .env)
- Knowledge corpus = 802 SO for Teams posts; 325 ingested, 428 validator/classifier rejected, 49 chunk-builder rejected
- `scripts/verify_runtime.py --skip-ocr` is the standard pre-flight check (38 PASS, 0 FAIL)
