# Sprint 2.11.2 CTO Report — Production Consolidation & Deployment Correctness

**Date:** 2026-06-04
**Sprint:** 2.11.2
**Status:** ✅ Complete — 1846 tests, 0 failures

---

## Executive Summary

Sprint 2.11.2 resolved two critical architectural defects that silently made the entire action gateway unreachable in production, and permanently disabled Prometheus metrics regardless of configuration. Both bugs had existed since Sprint 2.7 (action gateway) and went undetected because the test suite was running against a test-only code path, not the production entry point.

All 1846 tests now pass against the production `app/main.py::create_app()` factory. Every gateway feature built in Sprints 2.7–2.11 is now reachable from `uvicorn app.main:app`.

---

## Critical Bugs Fixed

### Bug 1: Two-Application Architecture (Severity: CRITICAL)

**Impact:** All Sprint 2.7–2.11 features were unreachable in production.

The codebase had two parallel FastAPI applications:
- `app/main.py` — served by uvicorn — RAG routes only
- `api/app.py` — test-only `create_app()` factory — all gateway routes

19 test suites (1846 tests) ran against `api/app.py` and passed, but uvicorn served `app/main.py` which had none of the gateway routes registered. Features built and tested for 4 sprints (RBAC, approval flows, dead-letter queue, watchdog, audit trail) were completely inaccessible in the deployed service.

**Fix:** Converted `app/main.py` to a `create_app()` factory with full dependency injection. All 8 gateway routers included. `api/app.py` replaced with a one-line re-export shim for backward compatibility.

### Bug 2: Import-Time Frozen Metrics (Severity: HIGH)

**Impact:** `PROMETHEUS_ENABLED=true` had no effect — metrics permanently disabled.

`observability/metrics.py` evaluated `os.getenv("PROMETHEUS_ENABLED")` at module import time. The import chain from `app/main.py` pulled in this module before `load_dotenv()` ran. Python module-level code executes once, on first import — so the flag was always frozen to `False`.

**Fix:** Lazy initialization via double-checked locking. `_ensure_initialized()` is called on first use of any `record_*` function, which happens during request processing — after `load_dotenv()` has populated `os.environ`.

---

## Technical Fixes Implemented

| Phase | Fix | Files Changed |
|-------|-----|--------------|
| 2 | Lazy metrics init (double-checked locking) | `observability/metrics.py` |
| 3 | Consolidate to single `create_app()` factory | `app/main.py` |
| 3 | `api/app.py` thin re-export wrapper | `api/app.py` |
| 3 | Gateway prefix bypass in rate limiter + auth | `app/security.py` |
| 3 | Path-aware HTTPException handler | `app/main.py` |
| 3 | `/gateway` prefix for health/metrics routes | `app/main.py`, `app/security.py` |
| 3 | Config check added to RAG `/health/ready` | `app/main.py` |
| 3 | Test paths updated to `/gateway/health`, `/gateway/metrics` | 2 test files |
| 3 | Fix `_ENABLED` import in regression test | `tests/test_sprint2111_regression.py` |

---

## Test Results

| Metric | Value |
|--------|-------|
| Total tests | 1846 |
| Passed | 1846 |
| Failed | **0** |
| Skipped | 4 |
| Warnings | 10 (deprecation only) |
| Runtime | ~50s |

Test suite history during this sprint:

| After fix | Passed | Failed |
|-----------|--------|--------|
| Initial (before consolidation) | 1846 | 0 (but test-only factory) |
| After consolidation attempt 1 | 1686 | 160 |
| After rate limiter + exception handler | 1827 | 19 |
| **Final (all fixes applied)** | **1846** | **0** |

---

## Route Inventory (Final)

31 routes registered in the production app (`app/main.py::create_app()`):

- **RAG routes:** `/health`, `/health/live`, `/health/ready`, `/metrics`, `/ready`, `/freshdesk/filter-options`, `/ingest`, `/query`, `/chat`, `/chat/suggestions`, `/train/chat`, `/train/commit`, `/train/cards`, `/train/cards/{id}`, `/feedback`, `/rag/chat`, `/rag/chat/stream`, `/freshdesk/webhook`
- **Gateway routes (direct):** `/webhook/{client}`, `/actions/{id}`, `/actions/{id}/approve`, `/actions/{id}/reject`, `/worker/tick`, `/watchdog/run`, `/audit`, `/admin/dead-letter`
- **Gateway routes (prefixed):** `/gateway/health`, `/gateway/health/live`, `/gateway/health/ready`, `/gateway/metrics`

---

## Architecture Decision: `/gateway` Prefix

Gateway `health` and `metrics` routes conflict with RAG's `GET /health` and `GET /metrics`. Including them at the same paths would create ambiguity in FastAPI's router (last registered wins). The `/gateway` prefix cleanly separates them:

- **`/health`** → RAG `{"status": "ok"}` — simple liveness
- **`/gateway/health`** → Gateway `{"is_healthy":..., "executor_count":..., "runtime_ready":..., }` — runtime health

- **`/metrics`** → RAG Prometheus instruments (HTTP, retrieval, LLM latency)
- **`/gateway/metrics`** → Gateway action lifecycle counters

Both pairs are unauthenticated and bypass rate limiting (via `_GATEWAY_PREFIXES` including `/gateway`).

---

## Security Posture Post-Consolidation

| Control | Status |
|---------|--------|
| API key auth (RAG routes) | ✅ Constant-time `hmac.compare_digest()` |
| RBAC (gateway routes) | ✅ Per-route `Depends()` — 4 roles |
| Freshdesk HMAC | ✅ Byte-exact raw body signing |
| Rate limiting | ✅ Per-IP sliding window |
| PII protection | ✅ Aadhaar masking, `DEBUG_RAG=false` |
| Stack traces in responses | ✅ Never exposed |
| Supabase key rotation | ⚠️ CRITICAL — key in git history, must rotate |

---

## Deployment Readiness

| Item | Status |
|------|--------|
| Production entry point (`app/main.py`) | ✅ Verified |
| All gateway routes reachable | ✅ Verified |
| Prometheus metrics (`PROMETHEUS_ENABLED=true`) | ✅ Fixed |
| Health checks (`/health/live`, `/health/ready`) | ✅ Unauthenticated |
| Request correlation (`X-Request-ID`) | ✅ All responses |
| Structured logging | ✅ Available (configure `STRUCTURED_LOGGING=true`) |
| Supabase key rotation | 🔴 **Required before production deployment** |

---

## Documentation Produced

| Document | Phase |
|----------|-------|
| `APPLICATION_CONSOLIDATION_REVIEW.md` | 0 — Audit |
| `METRICS_ROOT_CAUSE_ANALYSIS.md` | 1 — Root cause |
| `APPLICATION_CONSOLIDATION_REPORT.md` | 3 — Implementation |
| `ACTION_GATEWAY_DEPLOYMENT_VERIFICATION.md` | 4 — Verification |
| `OBSERVABILITY_VALIDATION_REPORT.md` | 5 — Observability |
| `MONITORING_DEPLOYMENT_GUIDE.md` | 6 — Prometheus/Grafana |
| `DEPLOYMENT_CORRECTNESS_AUDIT.md` | 7 — Deployment audit |
| `SECURITY_POST_CONSOLIDATION_REVIEW.md` | 9 — Security |
| `SPRINT_2_11_2_CTO_REPORT.md` | 10 — This document |
