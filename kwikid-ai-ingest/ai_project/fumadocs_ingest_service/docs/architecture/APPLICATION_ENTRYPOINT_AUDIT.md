# Application Entrypoint Audit — KwikID AI Ingest Service

**Date:** 2026-06-04
**Sprint:** 2.11.1
**Auditor:** Principal Staff Engineer / SRE Lead

---

## Search Results: Every FastAPI Instantiation in the Repository

### Search: `FastAPI(` in all `.py` files

```
api/app.py:113:    app = FastAPI(
app/main.py:302:app = FastAPI(
```

**Two FastAPI instances. Exactly two.**

### Search: `create_app(` in all `.py` files

```
api/app.py:36:     def create_app(                        ← definition
tests/test_sprint210_production.py:1108: app = create_app(
tests/test_sprint29_audit.py:342:        app = create_app(
tests/test_sprint211_operational.py:87:  app = create_app(
tests/test_sprint211_operational.py:1011: app = create_app(
tests/test_sprint27_api.py:178:          app = create_app(
tests/test_sprint27_api.py:419:          app = create_app(
tests/test_sprint28_security.py:214:     app = create_app(
```

`create_app` is called in **7 test files** and **0 non-test files**.

### Search: `uvicorn` in all deployment artifacts

```
Dockerfile:47:       CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
scripts_dev/run_local.sh:12:    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
scripts_dev/run_local.ps1:11:   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
LOCAL_SETUP_GUIDE.md:37:        uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
docs/local_setup_guide.md:185:  uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
docs/index.md:108:              uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Every uvicorn invocation in the repository — `Dockerfile`, shell scripts, documentation — points to `app.main:app`.

**`api/app.py` is never invoked by uvicorn anywhere.**

---

## Entrypoint Classification

### 1. `app/main.py` — PRODUCTION

| Property | Value |
|----------|-------|
| Module path | `app.main` |
| Object | `app` (module-level `FastAPI(...)` at line 302) |
| Instantiation | Module-level — created unconditionally on import |
| Served by | `uvicorn app.main:app` |
| Dockerfile CMD | `["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]` |
| Reachable in production | **YES** |

**Routes:**
```
GET  /health                 (unauthenticated)
GET  /health/live            (unauthenticated, NEW Sprint 2.11.1)
GET  /health/ready           (unauthenticated, NEW Sprint 2.11.1)
GET  /metrics                (unauthenticated)
GET  /ready                  (unauthenticated, legacy)
GET  /freshdesk/filter-options
POST /ingest                 (X-API-Key required)
POST /query                  (X-API-Key required)
POST /chat                   (X-API-Key required)
GET  /chat/suggestions       (X-API-Key required)
POST /train/chat             (X-API-Key required)
POST /train/commit           (X-API-Key required)
GET  /train/cards            (X-API-Key required)
GET  /train/cards/{id}       (X-API-Key required)
DELETE /train/cards/{id}     (X-API-Key required)
POST /rag/chat               (X-API-Key required, rate-limited)
POST /rag/chat/stream        (X-API-Key required, rate-limited)
POST /freshdesk/webhook      (HMAC-protected, no X-API-Key)
POST /feedback               (X-API-Key required)
```

**Middleware stack (outermost → innermost):**
```
RequestIdMiddleware         (Sprint 2.11.1, add_middleware)
api_key_auth_middleware     (@app.middleware("http"))
log_requests                (@app.middleware("http"))
CORSMiddleware              (add_middleware)
```

**Auth model:** Global middleware — `api_key_auth_middleware` intercepts every request except `_UNPROTECTED_PATHS`.

**Lifespan:** Validates security config; initialises Supabase client, OpenAI embedder, ChatGenerator, CaseService singletons at startup.

---

### 2. `api/app.py::create_app()` — ACTION GATEWAY (TEST-ONLY / UNREACHABLE IN PRODUCTION)

| Property | Value |
|----------|-------|
| Module path | `api.app` |
| Object | Returned by `create_app()` factory function |
| Instantiation | On-demand — only when `create_app()` is called |
| Served by uvicorn | **NEVER** — no Dockerfile, no shell script, no documentation command |
| Reachable in production | **NO** |
| Used in tests | YES — 7 test files |

**Routes:**
```
GET  /health                 (unauthenticated)
GET  /health/live            (unauthenticated)
GET  /health/ready           (unauthenticated)
GET  /metrics                (unauthenticated)
POST /webhook/freshdesk      (HMAC)
POST /actions                (require_approver)
GET  /actions                (require_approver)
GET  /actions/{id}           (require_approver)
POST /actions/{id}/approve   (require_approver)
POST /actions/{id}/reject    (require_approver)
POST /worker/run             (require_operator)
POST /watchdog/run           (require_operator)
GET  /audit/events           (require_admin)
GET  /admin/dead-letter      (require_admin)
```

**Middleware stack:**
```
RequestIdMiddleware (add_middleware)
```

**Auth model:** Per-route `Depends()` — `require_admin`, `require_approver`, `require_operator` injected per handler. No global auth middleware.

**Lifespan:** Builds action gateway runtime, audit stack (AuditLogger + AuditService + AuditOutbox + retry policy), authenticator.

**Note on the Sprint 2.7 CTO Report:** That report recommended `uvicorn api.app:create_app --factory` as the deployment command. This command was never implemented. `api/main.py` does not exist — the `DEPLOYMENT_READINESS_REVIEW.md` command `uvicorn api.main:app` references a non-existent file.

---

### 3. All Other `if __name__ == "__main__"` Scripts — CLI/SCRIPTS ONLY

```
app/ingest.py
scripts/reingest_v2.py
scripts/benchmark_retrieval_pipeline.py
scripts/validate_v2_ingestion.py
... (35 scripts total)
```

None of these are FastAPI applications. They are CLI scripts, benchmarks, and validation utilities. Not relevant to HTTP serving.

---

## Summary Table

| App | File | Type | Uvicorn Target | Reachable |
|-----|------|------|----------------|-----------|
| RAG Service | `app/main.py` | **PRODUCTION** | `app.main:app` | YES |
| Action Gateway | `api/app.py` | **TEST-ONLY** | Never wired | NO |
| Scripts | `scripts/*.py` etc. | CLI utilities | N/A | N/A |

---

## Critical Finding: The Action Gateway Is Unreachable

Every Sprint 2.7 through Sprint 2.11 feature of the action gateway — RBAC, dead-letter queue, watchdog, audit trail, approve/reject workflows — **has never served a real production HTTP request**.

The Dockerfile, every shell script, and all documentation point exclusively to `app.main:app`. The `api/app.py` factory produces a fully-functional FastAPI application, but nothing runs it.

The `DEPLOYMENT_READINESS_REVIEW.md` Sprint 2.11 command `uvicorn api.main:app` references a file (`api/main.py`) that does not exist.

---

## Recommendation: Option B — Consolidate

### Recommendation: Merge `api/app.py` routes into `app/main.py`

**This is the correct architectural decision for this system at this stage.**

---

### Why Option A (remain multi-app) fails

**1. The action gateway is already dead in production.**
No traffic reaches `/actions`, `/worker`, `/watchdog`, `/audit`, or `/admin` from any real client. Keeping the split means those features remain permanently non-functional until someone explicitly deploys a second uvicorn process — a deployment change that no current runbook or Dockerfile supports.

**2. Every shared concern requires duplication.**
Sprint 2.11.1 was three production bugs caused entirely by adding middleware and routes to `api/app.py` without adding them to `app/main.py`. This will happen again. The next sprint that adds a new cross-cutting feature (structured logging toggle, rate limit header, new health check) will face the same split. There is no mechanism in the codebase that enforces parity between the two apps.

**3. The authentication models are not fundamentally incompatible.**
`app/main.py` uses global middleware with an exempt-path list. `api/app.py` uses per-route `Depends()`. These can coexist in a single app: RBAC routes use `Depends(require_admin)`, unauthenticated routes are explicit. The two models are not architecturally opposed — they are just implemented in different places.

**4. The factory pattern is testable from either entry point.**
The reason `api/app.py` uses a `create_app()` factory is testability: tests inject mocked stacks, authenticators, and audit loggers. `app/main.py` can be refactored to the same factory pattern without changing any route logic. The test infrastructure follows the factory, not the other way around.

**5. There is no operational boundary.**
The two apps share the same Supabase instance, the same environment variables, and the same Python process (if they were ever served together). They are not two microservices — they are one service split across two files, with no API contract between them.

---

### Why Option B (consolidate) is the right call

**1. Single source of truth for routing, middleware, and auth.**
One app means one `_UNPROTECTED_PATHS`, one middleware stack, one lifespan. Cross-cutting changes happen once. The Sprint 2.11.1 incident becomes impossible.

**2. All features become reachable immediately.**
The action gateway's approve/reject/audit/watchdog endpoints become live without any new deployment configuration. Years of sprint work becomes accessible.

**3. Reduces cognitive load.**
Developers do not need to decide which app to modify when adding a feature. There is one app. It is `app/main.py`. End of decision.

**4. Deployment simplicity.**
One uvicorn command, one Dockerfile, one port. No internal service discovery, no cross-process auth propagation, no port coordination.

---

### How to Consolidate (migration path)

This is a non-trivial but bounded migration. The work is:

1. **Convert `app/main.py` to a factory pattern.** Wrap the existing module-level `app = FastAPI(...)` in a `create_app()` function. This makes it testable. Preserve `app = create_app()` at module level so `uvicorn app.main:app` continues to work.

2. **Port `api/app.py` routers into `app/main.py::create_app()`.** Add `app.include_router()` calls for the action gateway routers. The routes bring their own `Depends()` auth — they do not need the global middleware to be removed.

3. **Unify the lifespan.** Merge the two lifespan startup sequences. Both apps initialise a Supabase client — share a single singleton. The action gateway runtime, audit stack, and authenticator are built alongside the RAG singletons.

4. **Update test infrastructure.** Tests that use `create_app(stack=mock_stack)` continue to work. Tests that use `app/main.py` directly need the factory injection pattern. This is mechanical work.

5. **Remove `api/app.py`.** Once routers are ported and tests pass, `api/app.py` becomes dead code.

**Estimated risk:** Medium. The route logic is well-tested (1846 tests). The migration risk is in the lifespan unification and test factory updates. A clean migration preserves all existing tests.

**Estimated sprint cost:** 1 sprint.

---

### What NOT to do

Do not deploy `api/app.py` as a second uvicorn process on a second port. This solves reachability at the cost of doubling operational complexity (two processes, two health probes, two log streams, two Prometheus targets, cross-process auth for any shared state). It is the wrong direction for a system at this maturity level.

Do not leave the system in its current state beyond the next sprint. The split is not a deliberate microservices architecture — it is an accidental bifurcation that produced three production bugs in Sprint 2.11.1 and has left the entire action gateway unreachable since Sprint 2.7.

---

## Appendix: DEPLOYMENT_READINESS_REVIEW.md Correction Required

The document at `Big_Phase_2_documentations/DEPLOYMENT_READINESS_REVIEW.md`, line 233, contains:

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 4
```

This command **will fail**. `api/main.py` does not exist. The correct command for the production app is:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

The correct command for the action gateway factory (if ever deployed standalone):

```bash
uvicorn api.app:create_app --factory --host 0.0.0.0 --port 8001 --workers 2
```

This correction should be made before any production deployment runbook is executed from that document.
