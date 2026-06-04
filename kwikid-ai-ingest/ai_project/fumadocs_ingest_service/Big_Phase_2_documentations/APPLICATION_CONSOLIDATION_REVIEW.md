# Application Consolidation Review — Sprint 2.11.2 Phase 0

**Date:** 2026-06-04
**Sprint:** 2.11.2 — Production Consolidation & Deployment Correctness

---

## Executive Summary

A full audit of the KwikID AI Ingest Service revealed a **two-application architecture problem** that made all Sprint 2.7–2.11 action gateway features unreachable in production. Additionally, a metrics import-time frozen state bug prevented `PROMETHEUS_ENABLED=true` from ever taking effect. Both bugs are architectural in nature and silently existed since Sprint 2.7.

---

## 1. Architecture Problem: Two Parallel Applications

### Root Cause

The codebase contained two separate FastAPI application factories:

| File | Status | How served |
|------|--------|------------|
| `app/main.py` | **Production** — `app = FastAPI(...)` at module level | `uvicorn app.main:app` |
| `api/app.py` | **Test-only** — `create_app()` factory | Never served by uvicorn |

Every Sprint 2.7–2.11 feature (action gateway, RBAC, dead-letter queue, watchdog, audit trail, health providers, gateway metrics) was added to `api/app.py` only. The production uvicorn process served `app/main.py`, which had none of these routes registered.

### What was unreachable in production

The following routes existed in `api/app.py` but were never served:

- `POST /webhook/{client}` — Freshdesk HMAC webhook
- `GET /actions/{id}` — Action inspection
- `POST /actions/{id}/approve` — Approver flow
- `POST /actions/{id}/reject` — Rejection flow
- `POST /worker/tick` — Worker execution
- `POST /watchdog/run` — Watchdog expiry
- `GET /audit` — Audit query
- `GET /admin/dead-letter` — Dead letter inspection
- `GET /health` (gateway format), `GET /health/live`, `GET /health/ready` — Gateway health
- `GET /metrics` (gateway action metrics) — Action gateway Prometheus output

**All 19 test suites that tested these features were running against `api/app.py` and passing, giving false confidence.**

### Discovery Method

Traced the uvicorn entry point: `uvicorn app.main:app` → `app/main.py` → confirmed no `include_router()` calls for any gateway routers. Confirmed `api/app.py` was only imported in test fixtures via `from api.app import create_app`.

---

## 2. Metrics Bug: Import-Time Frozen State

### Root Cause

`observability/metrics.py` originally evaluated `_ENABLED = os.getenv("PROMETHEUS_ENABLED")` at **module import time**. The import chain:

```
app/main.py line 20
  → api/middleware/request_id.py
    → observability/structured_logger.py
      → observability/__init__.py
        → from . import metrics     ← module-level code runs HERE
```

This import chain fired **before** `app/config.py`'s `load_dotenv()` call at line 40 of `app/main.py`. Result: `os.environ` did not yet reflect `.env` values, so `PROMETHEUS_ENABLED` was always `False` at import time, regardless of what was in `.env`.

Setting `PROMETHEUS_ENABLED=true` in `.env` had no effect. Prometheus metrics were permanently disabled.

---

## 3. Test Suite False Confidence

The 19 test files testing Sprint 2.7–2.11 gateway features all used `from api.app import create_app` — the test-only factory. They never tested the actual production code path (`app/main.py`). All 1846 tests passed but the production service was a hollow shell.

---

## 4. Identified Fixes Required

| # | Fix | Files |
|---|-----|-------|
| 1 | Lazy metrics initialization (double-checked locking) | `observability/metrics.py` |
| 2 | Consolidate to single `create_app()` factory | `app/main.py`, `api/app.py` |
| 3 | Include all gateway routers in production app | `app/main.py` |
| 4 | Gateway paths bypass global rate limiter | `app/security.py` |
| 5 | Path-aware HTTPException handler | `app/main.py` |
| 6 | `/gateway` prefix for conflicting health/metrics routes | `app/main.py`, `app/security.py` |
| 7 | Fix `_ENABLED` import in regression tests | `tests/test_sprint2111_regression.py` |
| 8 | Update health/metrics test path references | Multiple test files |
