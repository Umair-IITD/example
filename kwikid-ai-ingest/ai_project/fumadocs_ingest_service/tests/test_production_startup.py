"""
tests/test_production_startup.py

Regression tests for the circular import that caused production startup failure:

  ImportError: cannot import name 'create_app' from partially initialized
  module 'app.main' (most likely due to a circular import)

Root cause:
  app.main  line 20  → from api.middleware.request_id import RequestIdMiddleware
  api package import → api/__init__.py: from api.app import create_app
  api/app.py         → from app.main import create_app
  app.main           → partially initialized → ImportError

Fix:
  api/__init__.py was importing api.app at package level, creating a cycle.
  Removed the eager import — api/__init__.py is now intentionally empty.
  All callers use explicit submodule imports (from api.app import create_app).

These tests FAIL on the broken code and PASS on the fixed code.
"""
from __future__ import annotations

import os
import sys

# ── Minimal env before any app import ────────────────────────────────────────
os.environ.setdefault("RAG_API_KEY", "test-startup-key")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-startup")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-supabase-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENABLED", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

import pytest


# ── Section 1: Import-time circular import regression ────────────────────────

class TestCircularImportRegression:
    """
    THE PRODUCTION REGRESSION:

    Before the fix, importing app.main caused:
      app.main → api.middleware.request_id → api (package) → api.app → app.main
    resulting in ImportError on every production startup.

    These tests document and guard against that exact failure.
    """

    def test_app_main_imports_without_error(self):
        """import app.main must succeed without ImportError or circular import."""
        # app.main is already imported (module-level env setup ran), but if it
        # weren't this would be the first import — exactly what uvicorn does.
        import app.main  # noqa: F401
        assert "app.main" in sys.modules

    def test_app_main_exports_create_app(self):
        """app.main.create_app must be a callable after import."""
        import app.main
        assert callable(app.main.create_app), (
            "app.main.create_app must be callable. "
            "If this fails, app.main was only partially initialized during import "
            "(circular import: app.main → api → api.app → app.main)."
        )

    def test_app_main_exports_app_instance(self):
        """app.main.app must be a FastAPI instance (the production uvicorn target)."""
        import app.main
        from fastapi import FastAPI
        assert isinstance(app.main.app, FastAPI), (
            "app.main.app must be a FastAPI instance. "
            "uvicorn serves: uvicorn app.main:app"
        )

    def test_api_middleware_request_id_imports_without_triggering_api_app(self):
        """
        from api.middleware.request_id import RequestIdMiddleware must not
        import api.app or app.main as a side-effect.

        This is the exact import in app.main line 20 that previously triggered
        the circular import chain: api/__init__.py → api.app → app.main.
        """
        # Remove api from sys.modules to simulate a fresh import context
        # Note: in practice the test process has already loaded these; we verify
        # the import chain is structurally clean by checking api.__init__ content.
        import api
        # api/__init__ must not re-export create_app at package level
        assert not hasattr(api, "create_app"), (
            "api.__init__ must NOT import create_app at package level. "
            "Doing so creates the cycle: "
            "app.main → api (package) → api.app → app.main (partial) → ImportError. "
            "All callers must use 'from api.app import create_app' explicitly."
        )

    def test_api_init_has_no_module_level_imports_from_api_app(self):
        """api/__init__.py must not have a live import statement from api.app."""
        import ast
        import pathlib
        init_path = pathlib.Path(__file__).parent.parent / "api" / "__init__.py"
        source = init_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        # Walk top-level nodes only (module body) — ignore function/class bodies
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not mod.startswith("api.app"), (
                    "api/__init__.py must not import from api.app at module level. "
                    "This creates the circular import: "
                    "app.main → api (package) → api.app → app.main. "
                    "Remove the import — all callers use 'from api.app import create_app' directly."
                )

    def test_api_init_has_no_from_app_main_import(self):
        """api/__init__.py must not import from app.main directly."""
        import pathlib
        init_path = pathlib.Path(__file__).parent.parent / "api" / "__init__.py"
        source = init_path.read_text(encoding="utf-8")
        assert "from app.main import" not in source, (
            "api/__init__.py must not import from app.main — creates circular dependency."
        )


# ── Section 2: Application factory ───────────────────────────────────────────

class TestApplicationFactory:
    """Verify create_app() builds a functional FastAPI application."""

    def _make_app(self):
        from api.app import create_app
        return create_app(skip_config_validation=True)

    def test_create_app_returns_fastapi_instance(self):
        """create_app() must return a FastAPI instance, not raise."""
        from fastapi import FastAPI
        app = self._make_app()
        assert isinstance(app, FastAPI)

    def test_app_has_routes(self):
        """The production app must have routes registered."""
        app = self._make_app()
        paths = {route.path for route in app.routes}
        assert len(paths) > 0, "No routes registered — all routers may have been dropped."

    def test_rag_routes_present(self):
        """Core RAG routes must be present."""
        app = self._make_app()
        paths = {route.path for route in app.routes}
        for expected in ("/health", "/health/live", "/health/ready", "/metrics"):
            assert expected in paths, (
                f"Route {expected!r} is missing. "
                "RAG router may not have been included in create_app()."
            )

    def test_gateway_routes_present(self):
        """Action gateway routes must be present."""
        app = self._make_app()
        paths = {route.path for route in app.routes}
        assert any("/actions" in p for p in paths), (
            "No /actions route found — gateway router may not be included."
        )

    def test_middleware_stack_includes_request_id(self):
        """RequestIdMiddleware must be registered."""
        from api.middleware.request_id import RequestIdMiddleware
        app = self._make_app()
        # user_middleware contains Middleware(cls=...) objects; m.cls is the class itself
        middleware_classes = [m.cls for m in app.user_middleware if hasattr(m, "cls")]
        assert RequestIdMiddleware in middleware_classes, (
            "RequestIdMiddleware not found in middleware stack. "
            f"Registered middleware: {middleware_classes}. "
            "app.add_middleware(RequestIdMiddleware) may have been removed."
        )


# ── Section 3: Production smoke test (HTTP-level) ────────────────────────────

class TestProductionSmoke:
    """HTTP-level smoke tests against the production create_app() factory."""

    def _make_client(self):
        from starlette.testclient import TestClient
        from api.app import create_app
        return TestClient(
            create_app(skip_config_validation=True),
            raise_server_exceptions=False,
        )

    def test_health_live_returns_200(self):
        """/health/live must return 200 (liveness probe — never touches external systems)."""
        client = self._make_client()
        response = client.get("/health/live")
        assert response.status_code == 200, (
            f"/health/live returned {response.status_code}, expected 200. "
            "Liveness probe must always return 200 while the process is running."
        )

    def test_health_returns_200(self):
        """/health must return 200 with {status: ok}."""
        client = self._make_client()
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json().get("status") == "ok"

    def test_metrics_returns_200_no_auth_required(self):
        """/metrics must return 200 without X-API-Key (Prometheus scrapes unauthenticated)."""
        import app.main as _app_main
        from unittest.mock import patch
        with patch.object(_app_main, "get_metrics_response", return_value=None):
            client = self._make_client()
            response = client.get("/metrics")
        assert response.status_code == 200, (
            f"/metrics returned {response.status_code}. Must be 200 with no auth header."
        )
        assert response.status_code != 401, "/metrics must never require X-API-Key"
        assert response.status_code != 404, "/metrics route must always be registered"

    def test_request_id_header_present_in_response(self):
        """X-Request-ID must be present in every response (RequestIdMiddleware active)."""
        client = self._make_client()
        response = client.get("/health")
        assert "x-request-id" in response.headers, (
            "X-Request-ID header missing from response. "
            "RequestIdMiddleware may not be active."
        )

    def test_rag_routes_require_api_key(self):
        """/query must require X-API-Key (auth middleware active)."""
        client = self._make_client()
        response = client.post("/query", json={"query_text": "test"})
        assert response.status_code in (401, 403), (
            f"/query returned {response.status_code} without an API key. "
            "API key auth middleware must reject unauthenticated RAG route access."
        )


# ── Section 4: Prometheus records ALL response statuses ──────────────────────

class TestMetricsRecordAllStatuses:
    """
    Regression for the production observability bug:
      POST /query without key → 401, but http_requests_total{status='401'} never appeared.

    Root cause: api_key_auth_middleware was registered as outermost @middleware
    (Starlette LIFO: registered second = outermost). It returned JSONResponse(401)
    without calling call_next. _log_requests_dispatch lived inside it and was never
    reached, so record_request() was never called for 401/429 responses.

    Fix: swap registration order so _log_requests_dispatch is outermost, wrapping auth.
    """

    def _make_app_with_prometheus(self, monkeypatch):
        import threading
        import observability.metrics as _m
        # Reset singleton so this test gets a clean metrics state
        monkeypatch.setattr(_m, "_initialized", False)
        monkeypatch.setattr(_m, "_metrics_available", False)
        monkeypatch.setattr(_m, "_init_lock", threading.Lock())
        for attr in (
            "_http_requests_total", "_http_request_duration_seconds",
            "_retrieval_latency_seconds", "_llm_latency_seconds",
            "_rate_limit_rejections_total", "_active_requests", "_retrieval_candidates",
        ):
            monkeypatch.setattr(_m, attr, None)

        # Override record_request directly to capture all calls without needing
        # prometheus_client installed in the test environment.
        import app.main as _app_main
        calls: list[dict] = []

        def _spy_record(method, path, status, duration):
            calls.append({"method": method, "path": path, "status": status})

        monkeypatch.setattr(_app_main, "record_request", _spy_record)

        # Reset the shared module-level rate-limiter singletons with fresh instances
        # that have a very high limit so these tests do not consume rate-limit budget
        # from the bucket shared across the whole test suite process. Without this,
        # the ~20 HTTP requests made by this test class would deplete the default
        # 30-req/60s bucket and cause unrelated tests that run later to see 429.
        from app.security import RateLimiter
        import app.security as _sec
        monkeypatch.setattr(_sec, "_rl_default", RateLimiter(max_requests=10_000, window_s=60.0))
        monkeypatch.setattr(_sec, "_rl_chat",    RateLimiter(max_requests=10_000, window_s=60.0))
        monkeypatch.setattr(_sec, "_rl_webhook", RateLimiter(max_requests=10_000, window_s=60.0))

        from api.app import create_app
        app = create_app(skip_config_validation=True)
        monkeypatch.setattr(_sec, "_API_KEYS", frozenset(["valid-key"]))

        from starlette.testclient import TestClient
        client = TestClient(app, raise_server_exceptions=False)
        return client, calls

    def test_401_is_recorded_in_prometheus(self, monkeypatch):
        """
        POST /query without key must produce record_request(..., status=401).
        Before the fix: api_key_auth_middleware was outer, returned 401 without
        calling call_next, so _log_requests_dispatch was never reached.
        """
        client, calls = self._make_app_with_prometheus(monkeypatch)
        response = client.post("/query", json={"query_text": "test"})
        assert response.status_code == 401
        statuses = [c["status"] for c in calls]
        assert 401 in statuses, (
            f"record_request was not called with status=401. Calls: {calls}. "
            "Auth middleware must be inner to _log_requests_dispatch so 401 "
            "short-circuit returns are still visible to the logging middleware."
        )

    def test_200_is_recorded_in_prometheus(self, monkeypatch):
        """GET /health must record status=200."""
        client, calls = self._make_app_with_prometheus(monkeypatch)
        response = client.get("/health")
        assert response.status_code == 200
        statuses = [c["status"] for c in calls]
        assert 200 in statuses, f"record_request not called with 200. Calls: {calls}"

    def test_404_is_recorded_in_prometheus(self, monkeypatch):
        """GET /nonexistent with valid key must record status=404."""
        client, calls = self._make_app_with_prometheus(monkeypatch)
        response = client.get("/nonexistent", headers={"X-API-Key": "valid-key"})
        assert response.status_code == 404
        statuses = [c["status"] for c in calls]
        assert 404 in statuses, f"record_request not called with 404. Calls: {calls}"

    def test_422_is_recorded_in_prometheus(self, monkeypatch):
        """POST /query with bad body and valid key must record status=422."""
        client, calls = self._make_app_with_prometheus(monkeypatch)
        response = client.post("/query", json={"bad": 1}, headers={"X-API-Key": "valid-key"})
        assert response.status_code == 422
        statuses = [c["status"] for c in calls]
        assert 422 in statuses, f"record_request not called with 422. Calls: {calls}"

    def test_middleware_order_logging_outer_auth_inner(self):
        """
        _log_requests_dispatch must be registered as the outermost @middleware,
        wrapping api_key_auth_middleware. Starlette LIFO: last registered = outermost.
        Verified by inspecting user_middleware list: index 1 must be _log_requests_dispatch,
        index 2 must be api_key_auth_middleware.
        """
        from api.app import create_app
        app = create_app(skip_config_validation=True)
        mw = app.user_middleware
        # Find the two BaseHTTPMiddleware entries (the @middleware("http") ones)
        http_mw = [m for m in mw if hasattr(m, "cls") and m.cls.__name__ == "BaseHTTPMiddleware"]
        assert len(http_mw) == 2, f"Expected 2 BaseHTTPMiddleware entries, got {len(http_mw)}"
        outer = http_mw[0]  # lower index = outer
        inner = http_mw[1]
        outer_name = getattr(outer.kwargs.get("dispatch"), "__name__", "")
        inner_name = getattr(inner.kwargs.get("dispatch"), "__name__", "")
        assert outer_name == "_log_requests_dispatch", (
            f"Outermost @middleware is {outer_name!r}, expected '_log_requests_dispatch'. "
            "Swap the registration order: register api_key_auth_middleware first, "
            "_log_requests_dispatch second so it is outermost and sees all responses."
        )
        assert inner_name == "api_key_auth_middleware", (
            f"Inner @middleware is {inner_name!r}, expected 'api_key_auth_middleware'."
        )
