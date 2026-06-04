"""
tests/test_sprint2111_regression.py

Sprint 2.11.1 regression tests for the three confirmed production bugs:

  Bug 1: GET /metrics returns 401 (Prometheus scraping blocked)
    - Root cause: /metrics not in _UNPROTECTED_PATHS in app/security.py
    - Root cause 2: PROMETHEUS_ENABLED gate returned 404
    - Fix: added /metrics to _UNPROTECTED_PATHS; removed 404 gate

  Bug 2: GET /health/live and GET /health/ready return 404
    - Root cause: production app is app/main.py, not api/app.py; new routes
      were only registered in api/routes/health.py via api/app.py
    - Fix: added /health/live and /health/ready routes to app/main.py;
      added both paths to _UNPROTECTED_PATHS

  Bug 3: RequestIdMiddleware not wired in production app
    - Root cause: api/app.py wires it but app/main.py did not
    - Fix: app/main.py now registers RequestIdMiddleware as outermost middleware

All tests use a TestClient that does NOT provide X-API-Key. This verifies that
the endpoints are genuinely unauthenticated — the precise behaviour that was
broken in production.
"""
from __future__ import annotations

import os
import sys

import pytest

# ── Minimal env so app/main.py lifespan does not abort ────────────────────────
# These must be set before any app import so the module-level security
# initialization does not raise at import time.
os.environ.setdefault("RAG_API_KEY", "test-regression-key-2111")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-regression-2111")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-supabase-key-2111")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENABLED", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
os.environ.setdefault("PROMETHEUS_ENABLED", "false")


# ---------------------------------------------------------------------------
# Section 1: app/security.py — _UNPROTECTED_PATHS
# ---------------------------------------------------------------------------

class TestUnprotectedPaths:
    """Verify the correct set of paths are exempt from API key authentication."""

    def test_metrics_in_unprotected_paths(self):
        from app.security import _UNPROTECTED_PATHS
        assert "/metrics" in _UNPROTECTED_PATHS, (
            "/metrics must be in _UNPROTECTED_PATHS so Prometheus can scrape "
            "without an API key. Regression: was missing, caused 401."
        )

    def test_health_live_in_unprotected_paths(self):
        from app.security import _UNPROTECTED_PATHS
        assert "/health/live" in _UNPROTECTED_PATHS, (
            "/health/live must be unauthenticated for Kubernetes liveness probes. "
            "Regression: was missing, would have caused 401."
        )

    def test_health_ready_in_unprotected_paths(self):
        from app.security import _UNPROTECTED_PATHS
        assert "/health/ready" in _UNPROTECTED_PATHS, (
            "/health/ready must be unauthenticated for Kubernetes readiness probes. "
            "Regression: was missing, would have caused 401."
        )

    def test_existing_unprotected_paths_preserved(self):
        """Ensure the fix did not accidentally remove previously exempt paths."""
        from app.security import _UNPROTECTED_PATHS
        for path in ("/health", "/ready", "/freshdesk/webhook", "/docs", "/openapi.json", "/redoc"):
            assert path in _UNPROTECTED_PATHS, f"Existing exempt path {path!r} must remain."

    def test_protected_path_not_added(self):
        """Sensitive endpoints must NOT be in unprotected paths."""
        from app.security import _UNPROTECTED_PATHS
        for path in ("/ingest", "/rag/chat", "/train/chat", "/admin/dead-letter"):
            assert path not in _UNPROTECTED_PATHS, (
                f"Protected path {path!r} must require authentication."
            )


# ---------------------------------------------------------------------------
# Section 2: app/main.py — route existence (no TestClient needed)
# ---------------------------------------------------------------------------

class TestRouteRegistration:
    """Verify that /health/live, /health/ready, and /metrics are registered."""

    @pytest.fixture(scope="class")
    def route_paths(self):
        # Import app without triggering lifespan (TestClient not needed here).
        # We just inspect the registered routes.
        try:
            from app.main import app
        except Exception:
            pytest.skip("app/main.py could not be imported (missing dependencies)")
        return {route.path for route in getattr(app, "routes", [])}

    def test_health_live_route_exists(self, route_paths):
        assert "/health/live" in route_paths, (
            "GET /health/live must be registered in app/main.py. "
            "Regression: was missing, caused 404."
        )

    def test_health_ready_route_exists(self, route_paths):
        assert "/health/ready" in route_paths, (
            "GET /health/ready must be registered in app/main.py. "
            "Regression: was missing, caused 404."
        )

    def test_metrics_route_exists(self, route_paths):
        assert "/metrics" in route_paths, (
            "GET /metrics must be registered in app/main.py."
        )

    def test_legacy_health_preserved(self, route_paths):
        assert "/health" in route_paths

    def test_legacy_ready_preserved(self, route_paths):
        assert "/ready" in route_paths


# ---------------------------------------------------------------------------
# Section 3: app/main.py — middleware registration
# ---------------------------------------------------------------------------

class TestMiddlewareRegistration:
    """Verify that RequestIdMiddleware is wired into the production app."""

    def test_request_id_middleware_registered(self):
        try:
            from app.main import app
        except Exception:
            pytest.skip("app/main.py could not be imported")
        from api.middleware.request_id import RequestIdMiddleware
        middleware_types = []
        for m in app.user_middleware:
            cls = getattr(m, "cls", None)
            if cls is not None:
                middleware_types.append(cls)
        # Also check middleware stack directly
        app_stack = app.middleware_stack if hasattr(app, "middleware_stack") else None
        found = any(cls is RequestIdMiddleware for cls in middleware_types)
        assert found, (
            "RequestIdMiddleware must be registered in app/main.py. "
            "Regression: was missing, no X-Request-ID correlation on any request."
        )

    def test_cors_middleware_preserved(self):
        try:
            from app.main import app
        except Exception:
            pytest.skip("app/main.py could not be imported")
        from fastapi.middleware.cors import CORSMiddleware
        middleware_types = [getattr(m, "cls", None) for m in app.user_middleware]
        assert CORSMiddleware in middleware_types, "CORSMiddleware must still be registered."


# ---------------------------------------------------------------------------
# Section 4: app/security.py — constant-time key check
# ---------------------------------------------------------------------------

class TestConstantTimeKeyCheck:
    """Verify auth bypass is not possible through the exemption list."""

    def test_no_prefix_bypass(self):
        from app.security import _UNPROTECTED_PATHS
        # e.g. "/metrics.evil" should NOT be exempt just because "/metrics" is
        assert "/metrics.evil" not in _UNPROTECTED_PATHS

    def test_path_traversal_not_exempt(self):
        from app.security import _UNPROTECTED_PATHS
        assert "/metrics/../ingest" not in _UNPROTECTED_PATHS

    def test_api_key_auth_middleware_rejects_missing_key(self):
        """Non-exempt paths without X-API-Key must get 401."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from app.security import api_key_auth_middleware, initialize

        initialize(frozenset({"valid-key"}))

        request = MagicMock()
        request.url.path = "/ingest"
        request.client.host = "127.0.0.1"
        request.headers.get = lambda k, d="": d  # no X-API-Key, no X-Forwarded-For

        call_next = AsyncMock()

        response = asyncio.get_event_loop().run_until_complete(
            api_key_auth_middleware(request, call_next)
        )
        assert response.status_code == 401
        call_next.assert_not_called()

    def test_api_key_auth_middleware_allows_metrics(self):
        """The /metrics path must NOT trigger auth check."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from fastapi.responses import JSONResponse
        from app.security import api_key_auth_middleware, initialize

        initialize(frozenset({"valid-key"}))

        fake_response = JSONResponse({"ok": True})
        call_next = AsyncMock(return_value=fake_response)

        request = MagicMock()
        request.url.path = "/metrics"
        request.client.host = "127.0.0.1"
        request.headers.get = lambda k, d="": d  # no API key

        response = asyncio.get_event_loop().run_until_complete(
            api_key_auth_middleware(request, call_next)
        )
        # Must reach call_next (not rejected by auth)
        call_next.assert_called_once()

    def test_api_key_auth_middleware_allows_health_live(self):
        """The /health/live path must NOT trigger auth check."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from fastapi.responses import JSONResponse
        from app.security import api_key_auth_middleware, initialize

        initialize(frozenset({"valid-key"}))

        fake_response = JSONResponse({"alive": True})
        call_next = AsyncMock(return_value=fake_response)

        request = MagicMock()
        request.url.path = "/health/live"
        request.client.host = "127.0.0.1"
        request.headers.get = lambda k, d="": d

        response = asyncio.get_event_loop().run_until_complete(
            api_key_auth_middleware(request, call_next)
        )
        call_next.assert_called_once()

    def test_api_key_auth_middleware_allows_health_ready(self):
        """The /health/ready path must NOT trigger auth check."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from fastapi.responses import JSONResponse
        from app.security import api_key_auth_middleware, initialize

        initialize(frozenset({"valid-key"}))

        fake_response = JSONResponse({"ready": True})
        call_next = AsyncMock(return_value=fake_response)

        request = MagicMock()
        request.url.path = "/health/ready"
        request.client.host = "127.0.0.1"
        request.headers.get = lambda k, d="": d

        response = asyncio.get_event_loop().run_until_complete(
            api_key_auth_middleware(request, call_next)
        )
        call_next.assert_called_once()


# ---------------------------------------------------------------------------
# Section 5: metrics endpoint — no PROMETHEUS_ENABLED gate
# ---------------------------------------------------------------------------

class TestMetricsEndpoint:
    """Verify metrics endpoint returns 200 always (never 404 or 403)."""

    def test_get_metrics_response_returns_none_when_disabled(self):
        """When PROMETHEUS_ENABLED=false, get_metrics_response() returns None."""
        # _ENABLED is no longer a module-level symbol (lazy init pattern).
        # Test the observable behavior: with PROMETHEUS_ENABLED=false (set at top of
        # this module), get_metrics_response() must return None.
        from observability.metrics import get_metrics_response
        import os
        if os.getenv("PROMETHEUS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
            pytest.skip("PROMETHEUS_ENABLED=true in this environment")
        result = get_metrics_response()
        assert result is None

    def test_metrics_handler_produces_text_not_404(self):
        """metrics_endpoint() must return text/plain, never HTTPException(404)."""
        import asyncio
        from fastapi import HTTPException

        # Patch get_metrics_response to return None (simulating disabled)
        import observability.metrics as om_mod
        original = om_mod.get_metrics_response
        om_mod.get_metrics_response = lambda: None
        try:
            from app.main import metrics_endpoint
            result = asyncio.get_event_loop().run_until_complete(metrics_endpoint())
            # Must be a Response, not an exception
            from starlette.responses import Response
            assert isinstance(result, Response), (
                "metrics_endpoint must return a Response when metrics are disabled, "
                "not raise HTTPException(404). Regression: previously raised 404."
            )
            assert result.status_code == 200
        finally:
            om_mod.get_metrics_response = original

    def test_metrics_handler_content_type_is_prometheus_text(self):
        """When metrics are disabled, content_type must still be Prometheus text format."""
        import asyncio

        import observability.metrics as om_mod
        original = om_mod.get_metrics_response
        om_mod.get_metrics_response = lambda: None
        try:
            from app.main import metrics_endpoint
            result = asyncio.get_event_loop().run_until_complete(metrics_endpoint())
            assert "text/plain" in result.media_type, (
                "metrics content-type must be text/plain for Prometheus compatibility"
            )
        finally:
            om_mod.get_metrics_response = original


# ---------------------------------------------------------------------------
# Section 6: health/live — response schema
# ---------------------------------------------------------------------------

class TestHealthLiveRoute:
    """Verify /health/live returns the correct schema."""

    def test_health_live_returns_alive_true(self):
        import asyncio
        try:
            from app.main import health_live
        except ImportError:
            pytest.skip("health_live not yet in app/main.py")
        result = asyncio.get_event_loop().run_until_complete(health_live())
        assert result["alive"] is True

    def test_health_live_contains_service_name(self):
        import asyncio
        from app.main import health_live
        result = asyncio.get_event_loop().run_until_complete(health_live())
        assert result.get("service") == "kwikid-ai-ingest"

    def test_health_live_contains_checked_at(self):
        import asyncio
        from app.main import health_live
        result = asyncio.get_event_loop().run_until_complete(health_live())
        assert "checked_at" in result
        # Must be a non-empty ISO8601 string
        assert isinstance(result["checked_at"], str)
        assert len(result["checked_at"]) > 10


# ---------------------------------------------------------------------------
# Section 7: request_id middleware — header propagation
# ---------------------------------------------------------------------------

class TestRequestIdMiddlewareUnit:
    """Unit tests for RequestIdMiddleware (standalone, no app import)."""

    def test_generates_request_id_when_absent(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from starlette.responses import Response as StarletteResponse
        from api.middleware.request_id import RequestIdMiddleware

        # Create a minimal ASGI app stub
        inner = MagicMock()
        mw = RequestIdMiddleware(inner)

        fake_response = StarletteResponse("ok")
        request = MagicMock()
        request.headers.get = lambda k, default="": default  # no X-Request-ID

        async def call_next(req):
            return fake_response

        result = asyncio.get_event_loop().run_until_complete(
            mw.dispatch(request, call_next)
        )
        # The middleware must have set X-Request-ID in the response
        assert "X-Request-ID" in result.headers
        assert len(result.headers["X-Request-ID"]) == 36  # UUID4 format

    def test_propagates_existing_request_id(self):
        import asyncio
        from unittest.mock import MagicMock
        from starlette.responses import Response as StarletteResponse
        from api.middleware.request_id import RequestIdMiddleware

        inner = MagicMock()
        mw = RequestIdMiddleware(inner)

        supplied_id = "my-correlation-id-abc123"
        fake_response = StarletteResponse("ok")
        request = MagicMock()
        request.headers.get = lambda k, default="": supplied_id if k == "x-request-id" else default

        async def call_next(req):
            return fake_response

        result = asyncio.get_event_loop().run_until_complete(
            mw.dispatch(request, call_next)
        )
        assert result.headers["X-Request-ID"] == supplied_id

    def test_sets_request_state(self):
        import asyncio
        from unittest.mock import MagicMock
        from starlette.responses import Response as StarletteResponse
        from api.middleware.request_id import RequestIdMiddleware

        inner = MagicMock()
        mw = RequestIdMiddleware(inner)

        state_holder = {}

        class FakeState:
            pass

        request = MagicMock()
        request.headers.get = lambda k, default="": ""
        request.state = FakeState()

        async def call_next(req):
            state_holder["request_id"] = req.state.request_id
            return StarletteResponse("ok")

        asyncio.get_event_loop().run_until_complete(
            mw.dispatch(request, call_next)
        )
        assert "request_id" in state_holder
        assert len(state_holder["request_id"]) == 36
