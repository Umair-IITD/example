# Environment Audit — KwikID AI Ingest Service

**Date:** 2026-06-04
**Sprint:** 2.11.1
**Auditor:** Principal Staff Engineer / SRE Lead

---

## Methodology

1. `.env.example` parsed for declared variable names (158 keys)
2. `.env` parsed for actually-set variable names (161 keys)
3. Diff computed — secrets are NEVER read or logged; only key names are compared
4. Required vars checked against ConfigConsistencyValidator's `REQUIRED_IN_PRODUCTION` list

---

## Drift Summary

| Category | Count | Action Required |
|----------|-------|-----------------|
| Only in `.env.example` (missing from `.env`) | 3 | Add to `.env` |
| Only in `.env` (undocumented) | 6 | Add to `.env.example` |
| Total keys in `.env.example` | 158 | — |
| Total keys in `.env` | 161 | — |

---

## Variables Missing from `.env` (in `.env.example` only)

These variables are declared in `.env.example` but absent from `.env`. The service will use module defaults where available, or may behave unexpectedly.

| Variable | Default | Risk |
|----------|---------|------|
| `EMBEDDING_DIMENSIONS` | `1536` | Low — defaults to correct value for text-embedding-3-small |
| `OPENAI_TIMEOUT_S` | `25` | Low — fallback timeout present in code |
| `RETRIEVAL_FTS_TIMEOUT_S` | `0.4` | Low — FTS queries use code default if absent |

**Action:** Add these to `.env` to match `.env.example` explicitly.

---

## Undocumented Variables (in `.env` only)

These variables are set in `.env` but NOT documented in `.env.example`. They may be experimental or legacy.

| Variable | Likely Purpose | Action |
|----------|---------------|--------|
| `B1_HNSW_V2_ENABLED` | Enables HNSW v2 partial index optimisations | Add to `.env.example` with `false` default |
| `ADAPTIVE_CONTEXT_BUDGET` | Controls adaptive RAG context window | Add to `.env.example` |
| `ADAPTIVE_CONTEXT_ENABLED` | Enables adaptive context mode | Add to `.env.example` with `false` default |
| `ADAPTIVE_SOP_THRESHOLD` | SOP threshold for adaptive context | Add to `.env.example` |
| `FAST_PATH_ENABLED` | Enables fast-path retrieval (no rerank) | Add to `.env.example` with `false` default |
| `FAST_PATH_SOP_THRESHOLD` | SOP similarity threshold for fast path | Add to `.env.example` |

**Action:** Add these to `.env.example` so future deployments are aware of them. The `ConfigConsistencyValidator` logs a startup warning for each, which is the expected behaviour.

---

## Required Variables — Production Status

The `ConfigConsistencyValidator` checks these at startup (`validate_startup_config()`):

| Variable | Required | Status |
|----------|----------|--------|
| `SUPABASE_URL` | Yes | Present in `.env` |
| `SUPABASE_KEY` | Yes | Present in `.env` |
| `RAG_API_KEY` | Yes | Present in `.env` |

All required variables are present. The service will not abort on startup due to missing required vars.

---

## Security-Critical Variables — Production Checklist

| Variable | Required Value | Current (`.env`) | Status |
|----------|---------------|-----------------|--------|
| `AUTH_ENABLED` | N/A (app/main.py uses `RAG_API_KEY` middleware) | — | N/A for this app |
| `FRESHDESK_WEBHOOK_ENFORCE_HMAC` | `true` (if webhook enabled) | — | See note |
| `DEBUG_RAG` | `false` | Not read (not in `app/main.py` path for this setting) | Verify in `.env` |
| `FASTAPI_DOCS_ENABLED` | `false` | `false` (default) | OK |
| `AUDIT_BACKEND` | `supabase` | Check `.env` | Must not be `inmemory` in production |
| `ACTIVE_INDEX_VERSION` | `v2` | Set in `.env` | Verify matches ingested data |

**Note on `FRESHDESK_WEBHOOK_ENFORCE_HMAC`:** The `app/main.py` lifespan checks `FRESHDESK_WEBHOOK_ENFORCE_HMAC` and `FRESHDESK_WEBHOOK_SECRET` at startup. If `FRESHDESK_WEBHOOK_ENABLED=true` and `FRESHDESK_WEBHOOK_ENFORCE_HMAC=true` but `FRESHDESK_WEBHOOK_SECRET` is empty, the service REFUSES TO START with a `RuntimeError`.

---

## CRITICAL: Supabase Key Rotation Required

A Supabase service-role key was accidentally committed to git history via the now-deleted `supabasesuccess.py`. The key in git history remains compromised regardless of deletion.

**This must be completed before any production deployment:**

1. Log in to Supabase dashboard → Project Settings → API
2. Click "Regenerate" on the service-role key
3. Update `SUPABASE_KEY` in `.env` and all CI/CD environments
4. Verify no cached copies in environment secrets

This is documented in `DEPLOYMENT_READINESS_REVIEW.md` and has been carried forward as a CRITICAL blocker.

---

## Configuration Drift Detection — Automated

The `ConfigConsistencyValidator` (`security/env_consistency.py`) runs at startup via `validate_startup_config()`. It compares `.env` vs `.env.example` and logs:

```
env_consistency: Variable 'NEW_VAR' is in .env.example but missing from .env
```

Monitor these warnings in production logs. They indicate the deployment config has drifted from the documented defaults.

**Startup command to verify manually:**

```bash
python -c "
from security.config_validator import validate_startup_config
validate_startup_config()
print('Config OK')
"
```

---

## Sprint 2.11.1 Environment Changes

No environment variable changes are required for the Sprint 2.11.1 fixes. The three bugs were caused by code configuration (middleware and route registration), not environment drift.

However, the following is now guaranteed by code:
- `PROMETHEUS_ENABLED=false` no longer causes `/metrics` to return 404 — the endpoint is always active per design intent
- `/health/live` and `/health/ready` do not depend on any environment variables

The `_PROMETHEUS_ENABLED` flag in `app/main.py` is now only used for log diagnostics, not for gating the endpoint.
