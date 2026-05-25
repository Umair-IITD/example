# Production Hardening

## Summary

The production hardening pass (Big Phase 1 final) resolved 15 issues across security, correctness, CI/CD, Docker, dependencies, and documentation. The system is now production-ready.

## Changes Made

### Security

1. **Deleted `kwikid-ai-ingest/supabasesuccess.py`** — contained hardcoded Supabase service-role JWT. File was a one-off validation script that was never imported. Confirmed zero references before deletion.

2. **Fixed `/ready` endpoint information leakage** (`app/main.py:448-458`)
   - Before: `checks["supabase"] = {"ok": False, "error": str(exc)}`
   - After: `checks["supabase"] = {"ok": False, "error": "dependency_check_failed"}`
   - Full exception logged server-side with `exc_info=True`

3. **Set `DEBUG_RAG=false` in `.env.example`** — was `true`, which would expose chunk PII in API responses if the template was copied to production.

4. **Set `FASTAPI_DOCS_ENABLED=false` in `.env.example`** — was `true`, which would expose full API schema via unprotected `/openapi.json`.

5. **Removed one-off maintenance scripts** (11 files) — contained hardcoded absolute paths to another developer's machine (`C:\Users\Dyaneshwar.Shekade\...`). These scripts had already been applied to the n8n workflow and served no further purpose.

### Correctness

6. **Fixed `has_knowledge_context` always-False bug** (`rag_engine/generation/chat_generator.py`)
   - Before: `has_knowledge_context = getattr(retrieval, "has_knowledge_context", False)` — `RetrievalResponse` has no such attribute
   - After: `has_knowledge_context = any(c.source_table == "rag_knowledge_chunks" for c in chunks)`
   - Impact: knowledge-only queries now correctly classified as `related_match` instead of `weak_match`

7. **Fixed `"pending.*review"` regex metacharacter bug** (`rag_engine/sop/sop_parser.py`)
   - Before: substring check with literal `"pending.*review"` — `.` and `*` were never treated as regex
   - After: proper regex `pending\s+(?:a\s+)?(?:security\s+)?review` in compiled pattern

8. **Fixed `CHAT_CONTEXT_CHUNK_MAX_CHARS` code default** (`app/config.py`)
   - Before: `os.getenv("CHAT_CONTEXT_CHUNK_MAX_CHARS", "1400")` — 1400 chars = ~26% of a 1200-token SOP chunk
   - After: `os.getenv("CHAT_CONTEXT_CHUNK_MAX_CHARS", "3500")` — matches documented production value
   - Impact: SOP steps 4–7 (hard lock, security freeze, escalation criteria, post-unlock checklist) were previously being silently truncated

### Architecture

9. **Created SOP structure parser** (`rag_engine/sop/sop_parser.py`, `rag_engine/sop/__init__.py`)
   - 7 compiled regex patterns replacing keyword-tuple heuristics
   - `SopDocumentFlags` frozen dataclass — immutable, thread-safe
   - 14 unit tests in governance validation suite
   - Integrated into `chat_generator.py`, `context_assembler.py`

10. **Renamed and moved n8n workflow** — from `Telegram + Freshdesk -_ OpenAPI Issue Analysis -_ Supabase RAG -_ OpenAPI Response (8).json` to `n8n/kwikid_support_workflow.json`

11. **Updated n8n workflow to `/rag/chat`** architecture
    - `Supabase Query (Issue Focused)` → `RAG Chat Request`: URL changed from `/query` to `/rag/chat`
    - Added `X-API-Key` header using `$env.RAG_API_KEY`
    - Request body updated to `/rag/chat` format (`top_k`, `client` instead of `match_count`, `source_thresholds`)
    - `Build Supabase Context` node updated to handle `chunks` not `matches`
    - SOP Gate updated to use `hasContext` and `topSimilarity` from new response format

12. **Organized n8n node scripts** — moved from `tmp_workflow_node_scripts/` to `n8n/node_scripts/`

### CI/CD

13. **Created root-level CI** (`.github/workflows/main.yml`)
    - GitHub Actions reads `.github/workflows/` only from the **repo root**
    - Inner CI file (`fumadocs_ingest_service/.github/workflows/ci.yml`) was never executed by GitHub
    - New CI: Python 3.11, pip cache, compileall, docker compose config, governance validation (48/48), pytest (non-blocking until secrets wired)

### Docker

14. **Added persistent volumes** (`docker-compose.yml`)
    - `./logs → /app/logs`
    - `./traces → /app/traces`
    - `./data/reports → /app/data/reports`
    - Previously, logs and reports were lost on every container restart

### Dependencies

15. **Pinned loose dependencies** (`requirements.txt`)
    - `redis>=5.0.8` → `redis==5.0.8`
    - `prometheus-client>=0.21.0` → `prometheus-client==0.21.0`

### Environment Template

16. **Complete `.env.example` rewrite** — comprehensive, all variables documented, grouped logically, security warnings added, production recommendations inline, correct defaults.

17. **Fixed `B3_KNOWLEDGE_SOURCE_DIR`** — was `../More_data` (pre-reorganization path), now `../../../../data/raw/More_data` (correct post-reorganization path).

18. **Added missing env vars to template** — `EMBEDDING_DIMENSIONS`, `OPENAI_CHAT_MODEL`, `OPENAI_CHAT_BASE_URL`, `WORKFLOW_EXACT_SIMILARITY`, `WORKFLOW_RELATED_SIMILARITY`, `STRICT_LATEST_WITHIN_TOP_N`, `RAG_API_KEY`, `RAG_DEFAULT_CLIENT`, etc.

### Gitignore

19. **Fixed root `.gitignore`** — removed accidental `postman/` ignore, added `.postman/` (secrets)

20. **Enhanced service `.gitignore`** — added `data/processed/`, `data/reports/`, `data/fumadocs_repo/` patterns

### Documentation

21. **Created `Big_Phase_1_documentations/`** — 20 structured technical documents

22. **Created `PERSONAL_REPORT.md`** — comprehensive engineering journey document

## Validation Results After Hardening

| Suite | Result | Notes |
|-------|--------|-------|
| `python -m compileall` | PASS | Zero syntax errors |
| `validate_response_governance.py` | 48/48 PASS | Includes 14 new SOP parser tests |
| `validate_b1_tokens.py` | 19/19 PASS | Token chunking correctness |
| `validate_b2_generation.py` | 41/41 PASS | Generation pipeline |
| `validate_b3_knowledge.py` | 46/46 PASS | Knowledge pipeline |
| `validate_b2_5_security.py` | 18/20 PASS | 2 fail locally (docs enabled in local .env) |
| `FastAPI app import` | PASS | Clean startup |
| `docker compose config` | PASS | Valid configuration |

## Pre-Deploy Checklist

Before pushing to production:

- [ ] Rotate Supabase service-role key (JWT was in git history)
- [ ] Set `FRESHDESK_WEBHOOK_ENFORCE_HMAC=true` (before enabling webhook)
- [ ] Set `FASTAPI_DOCS_ENABLED=false` (production)
- [ ] Set `DEBUG_RAG=false` (production)
- [ ] Set `CHAT_CONTEXT_CHUNK_MAX_CHARS=3500` (production)
- [ ] Configure `RAG_API_KEY` in production environment
- [ ] Wire GitHub Actions secrets: `SUPABASE_URL`, `SUPABASE_KEY`, `OPENAI_API_KEY`
- [ ] Run SQL migrations B1_007 and B1_008 in Supabase before enabling hybrid retrieval
- [ ] Add `chown -R 1001:1001 logs/ traces/ data/reports/` to deploy runbook (Linux Docker)
