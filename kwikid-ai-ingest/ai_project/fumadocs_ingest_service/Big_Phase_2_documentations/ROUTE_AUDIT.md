# Route Audit — KwikID AI Ingest Service

**Date:** 2026-06-04
**Sprint:** 2.11.1
**Auditor:** Principal Staff Engineer / SRE Lead

---

## Methodology

All routes were enumerated from their actual registration source, not from documentation or test assumptions. The production application entry point is confirmed to be `app/main.py` (module-level `app = FastAPI(...)`). The `api/app.py` factory (`create_app()`) is a separate application used for the Sprint 2.11 action-gateway subsystem — it is NOT the production HTTP server.

---

## Production App: `app/main.py`

### Unauthenticated Routes (no X-API-Key required)

| Method | Path | Status (post-fix) | Handler | Notes |
|--------|------|-------------------|---------|-------|
| GET | `/health` | 200 | `health()` | Legacy health check |
| GET | `/health/live` | 200 | `health_live()` | **NEW — Sprint 2.11.1 fix** |
| GET | `/health/ready` | 200 / 503 | `health_ready()` | **NEW — Sprint 2.11.1 fix** |
| GET | `/metrics` | 200 | `metrics_endpoint()` | **Fixed — Sprint 2.11.1** |
| GET | `/ready` | 200 / 503 | `ready()` | Legacy readiness check |
| GET | `/docs` | 200 / 404 | FastAPI auto | Only if FASTAPI_DOCS_ENABLED=true |
| GET | `/redoc` | 200 / 404 | FastAPI auto | Only if FASTAPI_DOCS_ENABLED=true |
| GET | `/openapi.json` | 200 / 404 | FastAPI auto | Only if FASTAPI_DOCS_ENABLED=true |
| GET | `/freshdesk/filter-options` | 200 | inline | No auth (public reference data) |
| POST | `/freshdesk/webhook` | 200 / 401 | `freshdesk_webhook()` | HMAC-protected, not API-key |

### Authenticated Routes (X-API-Key required)

| Method | Path | Handler | Notes |
|--------|------|---------|-------|
| POST | `/ingest` | `ingest_docs()` | Full re-index or incremental |
| POST | `/query` | `query_docs()` | Semantic search |
| POST | `/chat` | `chat_endpoint()` | Retrieval-augmented chat |
| GET | `/chat/suggestions` | `chat_suggestions()` | Top query suggestions |
| POST | `/train/chat` | `train_chat()` | Knowledge card drafting |
| POST | `/train/commit` | `train_commit()` | Commit knowledge card |
| GET | `/train/cards` | `train_cards_list()` | List knowledge cards |
| GET | `/train/cards/{card_id}` | `train_cards_get()` | Get single card |
| DELETE | `/train/cards/{card_id}` | `train_cards_delete()` | Delete card |
| POST | `/rag/chat` | `rag_chat()` | B1 RAG chat (rate-limited) |
| POST | `/rag/chat/stream` | `rag_chat_stream()` | B1 RAG streaming SSE |
| POST | `/feedback` | `post_feedback()` | Human feedback ingestion |

---

## Action Gateway App: `api/app.py` (create_app factory)

This app is NOT the production HTTP server. It is a separate FastAPI application used for the action-gateway subsystem. Routes listed for completeness.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| GET | `/health` | None | Sprint 2.11 legacy wrapper around stack.health.check() |
| GET | `/health/live` | None | Sprint 2.11 B1 liveness |
| GET | `/health/ready` | None | Sprint 2.11 B1 readiness |
| GET | `/metrics` | None | Sprint 2.11 MetricsService text |
| POST | `/webhook/freshdesk` | HMAC | Freshdesk inbound |
| POST, GET, DELETE | `/actions/*` | require_approver / require_operator | Action CRUD |
| POST | `/worker/run` | require_operator | Worker trigger |
| POST | `/watchdog/run` | require_operator | Watchdog trigger |
| GET | `/audit/events` | require_admin | Audit query |
| GET | `/admin/dead-letter` | require_admin | Dead-letter queue |

---

## Auth Exemption Audit

### `app/security.py:_UNPROTECTED_PATHS` (post-fix)

```python
_UNPROTECTED_PATHS: frozenset[str] = frozenset({
    "/health",
    "/health/live",    # NEW — Sprint 2.11.1
    "/health/ready",   # NEW — Sprint 2.11.1
    "/metrics",        # NEW — Sprint 2.11.1
    "/ready",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/freshdesk/webhook",
})
```

### Pre-fix state (the bug)

Before Sprint 2.11.1, `_UNPROTECTED_PATHS` contained only 6 paths. `/metrics`, `/health/live`, and `/health/ready` were absent, causing:
- Prometheus scraping: 401 (no X-API-Key)
- Kubernetes liveness probe: 404 (route not registered at all)
- Kubernetes readiness probe: 404 (route not registered at all)

---

## Path-matching behaviour

Starlette compares `request.url.path` against `_UNPROTECTED_PATHS` via set membership (exact string match, not prefix). Key implications:

- `/health/live` and `/health/ready` must be listed separately — they do NOT inherit `/health`'s exemption
- `/metrics/more` would NOT be exempt (exact match only)
- Path parameters (e.g., `/train/cards/{card_id}`) do not appear in the exempt set — their path variable expansion is not covered

---

## 404 Audit — Routes Missing from Production App

Before Sprint 2.11.1, the following paths returned 404 from `app/main.py`:

| Path | Reason | Fix |
|------|--------|-----|
| `GET /health/live` | Route not registered | Added to `app/main.py` |
| `GET /health/ready` | Route not registered | Added to `app/main.py` |

Routes that exist ONLY in `api/app.py` (not production) and return 404 from `app/main.py`:

| Path | Notes |
|------|-------|
| `POST /actions/*` | Action gateway — separate subsystem |
| `POST /worker/run` | Action gateway — separate subsystem |
| `POST /watchdog/run` | Action gateway — separate subsystem |
| `GET /audit/events` | Action gateway — separate subsystem |
| `GET /admin/dead-letter` | Action gateway — separate subsystem |

These are intentional — `app/main.py` is the RAG service; `api/app.py` is the action gateway. They are separate applications with separate concerns.
