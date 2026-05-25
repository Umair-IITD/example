# Release Checklist — Big Phase 1

## Pre-Push Security Verification

- [ ] **CRITICAL**: Supabase service-role key rotated in Supabase dashboard (old key was in git history in `supabasesuccess.py`)
- [ ] New key updated in production `.env` (not committed)
- [ ] `git log --oneline` reviewed — no credentials in commit messages
- [ ] `git grep "eyJ"` returns no results (no JWTs in tracked files)
- [ ] `git grep "sk-"` returns no results (no OpenAI keys in tracked files)

## Environment Configuration

- [ ] `.env` created from `.env.example` with all required values filled
- [ ] `SUPABASE_URL` and `SUPABASE_KEY` set
- [ ] `OPENAI_API_KEY` set (or `EMBEDDING_API_KEY` + `OPENAI_CHAT_API_KEY` separately)
- [ ] `RAG_API_KEY` set (service refuses to start without this)
- [ ] `DEBUG_RAG=false`
- [ ] `FASTAPI_DOCS_ENABLED=false`
- [ ] `ACTIVE_INDEX_VERSION=v2` (after re-ingestion validated)
- [ ] `B1_INDEX_VERSION=v2` (in sync with ACTIVE_INDEX_VERSION)
- [ ] `CHAT_CONTEXT_CHUNK_MAX_CHARS=3500`

## Database

- [ ] Supabase project accessible with service-role key
- [ ] `documents` table exists with pgvector extension
- [ ] `rag_sop_chunks` table exists
- [ ] `rag_knowledge_articles` and `rag_knowledge_chunks` tables exist (if B3 enabled)
- [ ] `chat_messages` table exists (if `persist_history` used)
- [ ] HNSW index on `documents.embedding`
- [ ] B1_007_fts_setup.sql run (if hybrid retrieval enabled)
- [ ] B1_008_fts_rpc.sql run (if hybrid retrieval enabled)
- [ ] `match_all_b1_sources` RPC function exists
- [ ] `match_all_b1_sources_fts` RPC function exists (if FTS enabled)

## Ingestion

- [ ] SOP files ingested (`data/sop/*.md`)
- [ ] Fumadocs repo ingested (if `FUMADOCS_REPO_URL` configured)
- [ ] JSON/Excel data ingested (if applicable)
- [ ] Knowledge articles ingested from `data/raw/More_data/` (if B3 enabled)
- [ ] `scripts/validate_b1_db_integrity.py` run — integrity checks pass

## Validation (All Offline)

- [ ] `python -m compileall -q app/ rag_engine/ retrieval/ dataset_pipeline/ scripts/ observability/ query_router/` — PASS
- [ ] `python scripts/validate_response_governance.py` — 48/48 PASS
- [ ] `python scripts/validate_b1_tokens.py` — 19/19 PASS
- [ ] `python scripts/validate_b2_generation.py` — 41/41 PASS
- [ ] `python scripts/validate_b3_knowledge.py` — 46/46 PASS
- [ ] `python scripts/validate_b2_5_security.py` — 20/20 PASS (with correct .env)
- [ ] `python -c "from app.main import app; print('OK')"` — OK
- [ ] `docker compose config --quiet` — PASS

## Docker

- [ ] `docker compose build --no-cache` succeeds
- [ ] `docker compose up -d` starts without errors
- [ ] `curl http://localhost:8000/health` → `{"status": "ok"}`
- [ ] `curl http://localhost:8000/ready` → `{"status": "ready", "checks": {...}}`
- [ ] Log files appearing in `./logs/`
- [ ] On Linux: host directories chowned to UID 1001 for volume mounts

## API Authentication

- [ ] `curl -H "X-API-Key: WRONG" http://localhost:8000/rag/chat -d '...'` → 401
- [ ] `curl -H "X-API-Key: CORRECT" http://localhost:8000/rag/chat -d '...'` → 200
- [ ] Rate limiting: 21st request in 60s → 429

## Webhook (if enabling)

- [ ] `FRESHDESK_WEBHOOK_ENABLED=true`
- [ ] `FRESHDESK_WEBHOOK_SECRET` set to a strong random value
- [ ] `FRESHDESK_WEBHOOK_ENFORCE_HMAC=true`
- [ ] Freshdesk webhook URL configured to point to `https://your-service/freshdesk/webhook`
- [ ] Test webhook with valid HMAC signature → 200
- [ ] Test webhook with invalid HMAC signature → 403

## CI/CD

- [ ] `.github/workflows/main.yml` exists at repo root
- [ ] GitHub Actions run on push to `main`
- [ ] CI passes: compileall, docker compose config, governance validation
- [ ] GitHub Actions secrets wired: `SUPABASE_URL`, `SUPABASE_KEY`, `OPENAI_API_KEY`
- [ ] pytest step unblocked (remove `|| true` after secrets wired)

## n8n Workflow

- [ ] `n8n/kwikid_support_workflow.json` imported into n8n instance
- [ ] n8n environment variables set (from `n8n/environment.example`)
- [ ] `RAG_API_KEY` in n8n env matches service `RAG_API_KEY`
- [ ] `INGEST_SERVICE_BASE_URL` points to production service URL
- [ ] Test workflow end-to-end with a sample ticket
- [ ] Verify `/rag/chat` response flows correctly to Freshdesk

## Documentation

- [ ] `Big_Phase_1_documentations/` reviewed and accurate
- [ ] `PERSONAL_REPORT.md` complete
- [ ] `README.md` at repo root updated
- [ ] `n8n/environment.example` complete

## Final Sign-off

- [ ] All items above checked
- [ ] Supabase key rotation confirmed
- [ ] Production `.env` does not have debug flags enabled
- [ ] Repository is safe to push to organizational remote

**Release verdict**: ✅ Big Phase 1 is production-ready after key rotation.
