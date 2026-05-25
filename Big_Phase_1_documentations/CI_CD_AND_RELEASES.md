# CI/CD and Releases

## GitHub Actions CI

The active CI workflow is at **`/.github/workflows/main.yml`** (repo root).

> **Note**: GitHub Actions ONLY reads `.github/workflows/` from the repository root. The file at `kwikid-ai-ingest/ai_project/fumadocs_ingest_service/.github/workflows/ci.yml` is NOT executed by GitHub — it is kept as a local reference only.

### Trigger Conditions

```yaml
on:
  push:
    branches: [main, production-hardening-final]
    paths:
      - "kwikid-ai-ingest/ai_project/fumadocs_ingest_service/**"
      - "docker-compose.yml"
      - ".github/workflows/main.yml"
  pull_request:
    paths:
      - "kwikid-ai-ingest/ai_project/fumadocs_ingest_service/**"
      - "docker-compose.yml"
```

Only runs when relevant files change (path filtering prevents spurious runs on docs-only changes).

### CI Steps

1. **Checkout** (actions/checkout@v4)
2. **Setup Python 3.11** — with pip cache keyed on `requirements.txt`
3. **Install dependencies** — `pip install -r requirements.txt`
4. **Compile-check** — `python -m compileall -q app/ rag_engine/ retrieval/ dataset_pipeline/ scripts/ observability/ query_router/`
5. **Docker compose config** — `docker compose config --quiet` (validates YAML)
6. **Governance validation** — `python scripts/validate_response_governance.py` (48/48 PASS)
7. **pytest** — `pytest tests/ -q --tb=short 2>&1 || true` (**non-blocking** until CI secrets wired)

### Why Non-Blocking pytest?

The test suite currently requires live Supabase + OpenAI environment variables not present in CI. The `|| true` suffix prevents CI failure until these secrets are wired:

```bash
# In GitHub repository → Settings → Secrets and variables → Actions:
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=<service-role-key>
OPENAI_API_KEY=<api-key>
```

After secrets are configured, remove `|| true` from the pytest step to make it blocking.

### CI Status Badge

Add to README.md after GitHub remote is configured:
```markdown
![CI](https://github.com/your-org/kwikid_support_system/actions/workflows/main.yml/badge.svg)
```

## Release Strategy

### Branch Strategy

```
main                ← Production-ready code
production-hardening-final  ← Current release branch
feature/*           ← Feature development
fix/*               ← Bug fixes
```

### Release Process

1. Feature/fix branch created from `main`
2. CI must pass (all offline tests)
3. Pull request created with:
   - Summary of changes
   - Test plan
   - Security review if applicable
4. Code review and approval
5. Merge to `main`
6. Tag release: `git tag v1.x.x && git push origin v1.x.x`
7. Deploy to production (manual or automated)

### Big Phase 1 Release

Current branch: `production-hardening-final`

This branch contains the complete Big Phase 1 implementation:
- B1: Hybrid retrieval pipeline (semantic + FTS + RRF)
- B2: RAG generation with governance (GPT-4o-mini)
- B3: Knowledge base ingestion (SO Teams)
- Security hardening (key auth, rate limiting, HMAC webhook)
- SOP parser (structure-aware regex)
- Token-aware chunking
- Redis rate limiting
- Prometheus metrics
- Docker hardening
- CI/CD baseline

**Merge to main** after:
1. Supabase key rotation complete
2. Production env variables verified
3. CI secrets wired (pytest unblocked)
4. Final stakeholder review

## Validation Before Release

Run all offline validation suites:

```bash
cd kwikid-ai-ingest/ai_project/fumadocs_ingest_service

# Syntax check
python -m compileall -q app/ rag_engine/ retrieval/ dataset_pipeline/ scripts/ observability/ query_router/

# Governance (48/48 required)
python scripts/validate_response_governance.py

# Token chunking (19/19 required)
python scripts/validate_b1_tokens.py

# Generation pipeline (41/41 required)
python scripts/validate_b2_generation.py

# Knowledge pipeline (46/46 required)
python scripts/validate_b3_knowledge.py

# Security (18/20 offline; 20/20 with correct .env)
python scripts/validate_b2_5_security.py

# FastAPI import
python -c "from app.main import app; print('OK')"

# Docker compose
cd ../../../..
docker compose config --quiet
```

Expected results: all PASS before any push to `main`.

## Deployment Checklist

- [ ] All CI steps pass on `production-hardening-final` branch
- [ ] Supabase key rotated (old key was in git history)
- [ ] Production `.env` configured (not committed)
- [ ] SQL migrations B1_007 and B1_008 run in Supabase
- [ ] `ACTIVE_INDEX_VERSION=v2` after re-ingestion validation
- [ ] `RAG_API_KEY` set in production environment
- [ ] `FRESHDESK_WEBHOOK_ENFORCE_HMAC=true` (before enabling webhook)
- [ ] `DEBUG_RAG=false` in production `.env`
- [ ] `FASTAPI_DOCS_ENABLED=false` in production `.env`
- [ ] n8n workflow imported and tested
- [ ] n8n `RAG_API_KEY` env var set to match service key
- [ ] Prometheus scraper configured (if `PROMETHEUS_ENABLED=true`)
- [ ] Redis configured (if `REDIS_RATE_LIMIT_ENABLED=true`)
- [ ] Health check endpoints verified: `/health` → 200, `/ready` → 200
- [ ] Rate limiting verified: 21st request in 60s → 429
- [ ] API key auth verified: missing key → 401

## Rollback Plan

If a deployment fails:
1. `docker compose down`
2. `git checkout <previous-stable-tag>`
3. `docker compose up --build`
4. Restore previous `ACTIVE_INDEX_VERSION` if index was migrated

The stateless design (all state in Supabase) means rollback only requires reverting the code + env. No data migration rollback is needed unless `ACTIVE_INDEX_VERSION` was changed.
