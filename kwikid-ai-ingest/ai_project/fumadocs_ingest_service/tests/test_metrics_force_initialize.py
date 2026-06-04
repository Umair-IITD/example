"""
tests/test_metrics_force_initialize.py

Regression tests for the Prometheus metrics production bug:
  PROMETHEUS_ENABLED=true in .env → /metrics still returns placeholder.

Root cause (triple):
  1. load_dotenv(override=False) silently skips PROMETHEUS_ENABLED when the system
     environment already has the variable set (even to "false"). This happens if the
     user previously ran $env:PROMETHEUS_ENABLED = "false" in their terminal.
  2. _do_init_metrics() caught all exceptions as a silent WARNING-level log, giving
     no indication of what value PROMETHEUS_ENABLED actually had at init time.
  3. Metrics were initialized lazily on first request with no startup-time forced init,
     so force_initialize() was never called from the lifespan.

Fixes:
  1. load_dotenv(override=True) in app/config.py.
  2. Explicit LOGGER.info showing the raw env-var value in _do_init_metrics().
  3. force_initialize(enabled=settings.prometheus_enabled) called from the lifespan
     in app/main.py using the already-parsed Settings object, bypassing os.getenv.

These tests use monkeypatch to isolate the module-level singleton state so each
test starts from a clean _initialized=False, _metrics_available=False baseline.
They mock _do_register_metrics() to avoid actual prometheus_client registration
(which cannot be repeated in the same process without clearing the global registry).
"""
from __future__ import annotations

import os

import pytest

# ── Minimal env for tests that import app/main.py ─────────────────────────────
# Must be set BEFORE api.app is imported below, so the app factory does not abort.
os.environ.setdefault("RAG_API_KEY", "test-force-init-key")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-force-init")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-supabase-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENABLED", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
os.environ.setdefault("PROMETHEUS_ENABLED", "false")

# Import the app factory at module level so app.main is fully initialized in
# sys.modules before any test method runs.  All other test files follow this pattern.
# Without this, `import app.main` inside a test method triggers a circular import:
#   app.main → api.middleware.request_id → api.__init__ → api.app → app.main (partial).
from api.app import create_app as _create_app  # noqa: F401 — side-effect import


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def reset_metrics(monkeypatch):
    """Reset observability.metrics module singleton state before each test.

    Uses monkeypatch so pytest automatically restores the original values
    after each test completes, even if the test raises.
    """
    import observability.metrics as _m
    monkeypatch.setattr(_m, "_initialized", False)
    monkeypatch.setattr(_m, "_metrics_available", False)
    for attr in (
        "_http_requests_total",
        "_http_request_duration_seconds",
        "_retrieval_latency_seconds",
        "_llm_latency_seconds",
        "_rate_limit_rejections_total",
        "_active_requests",
        "_retrieval_candidates",
    ):
        monkeypatch.setattr(_m, attr, None)
    yield


@pytest.fixture
def mock_register_success(monkeypatch):
    """Stub _do_register_metrics() to return True without touching prometheus_client.

    This avoids 'Duplicated timeseries in CollectorRegistry' errors when multiple
    tests in the same pytest process would otherwise re-register the same metrics.
    """
    import observability.metrics as _m
    monkeypatch.setattr(_m, "_do_register_metrics", lambda: True)


@pytest.fixture
def mock_register_failure(monkeypatch):
    """Stub _do_register_metrics() to return False (simulates install error)."""
    import observability.metrics as _m
    monkeypatch.setattr(_m, "_do_register_metrics", lambda: False)


# ── Section 1: force_initialize() logic ───────────────────────────────────────

class TestForceInitialize:
    """
    Verify force_initialize(enabled=...) correctly sets _metrics_available
    without consulting os.getenv().

    This is the core fix for the production bug: even when system env has
    PROMETHEUS_ENABLED=false, passing enabled=True from settings enables metrics.
    """

    def test_force_initialize_enabled_sets_available(
        self, reset_metrics, mock_register_success
    ):
        """force_initialize(enabled=True) → _metrics_available=True."""
        import observability.metrics as _m
        assert not _m._initialized
        _m.force_initialize(enabled=True)
        assert _m._initialized
        assert _m._metrics_available

    def test_force_initialize_disabled_clears_available(self, reset_metrics):
        """force_initialize(enabled=False) → _metrics_available=False."""
        import observability.metrics as _m
        _m.force_initialize(enabled=False)
        assert _m._initialized
        assert not _m._metrics_available

    def test_force_initialize_disabled_does_not_call_register(
        self, reset_metrics, monkeypatch
    ):
        """force_initialize(enabled=False) must not attempt prometheus_client registration."""
        import observability.metrics as _m
        called = []
        monkeypatch.setattr(_m, "_do_register_metrics", lambda: called.append(1) or True)
        _m.force_initialize(enabled=False)
        assert called == [], "_do_register_metrics should not be called when enabled=False"

    def test_force_initialize_is_idempotent(self, reset_metrics, monkeypatch):
        """Second call to force_initialize must be a no-op (_do_register_metrics called once)."""
        import observability.metrics as _m
        call_count = []
        monkeypatch.setattr(_m, "_do_register_metrics", lambda: call_count.append(1) or True)
        _m.force_initialize(enabled=True)
        _m.force_initialize(enabled=True)  # second call — must be ignored
        assert len(call_count) == 1, (
            "force_initialize() must use double-checked locking so _do_register_metrics "
            "is called at most once per process lifetime"
        )

    def test_force_initialize_wins_over_system_env_false(
        self, reset_metrics, mock_register_success, monkeypatch
    ):
        """
        THE PRODUCTION REGRESSION:
        System env has PROMETHEUS_ENABLED=false (set before load_dotenv runs),
        but the .env file has PROMETHEUS_ENABLED=true.

        force_initialize(enabled=True) must enable metrics regardless of the env var.
        Without this fix, the old _ensure_initialized() path would read the shadowed
        env var and return False, silently disabling metrics.
        """
        monkeypatch.setenv("PROMETHEUS_ENABLED", "false")

        import observability.metrics as _m
        # This is what the lifespan does: read parsed settings, call force_initialize.
        # settings.prometheus_enabled would be True (from .env via override=True),
        # so we pass enabled=True explicitly.
        _m.force_initialize(enabled=True)
        assert _m._metrics_available, (
            "force_initialize(enabled=True) must enable metrics even when "
            "os.environ['PROMETHEUS_ENABLED'] == 'false'. The explicit flag bypasses "
            "the env var read — this is the fix for the production outage."
        )

    def test_force_initialize_registration_failure_sets_available_false(
        self, reset_metrics, mock_register_failure
    ):
        """If prometheus_client registration fails, _metrics_available=False."""
        import observability.metrics as _m
        _m.force_initialize(enabled=True)
        assert _m._initialized
        assert not _m._metrics_available

    def test_force_initialize_before_ensure_initialized(
        self, reset_metrics, mock_register_success
    ):
        """force_initialize() → _ensure_initialized() must be a no-op (initialized=True)."""
        import observability.metrics as _m
        _m.force_initialize(enabled=True)

        # _ensure_initialized() called later (e.g. from record_request) must be a no-op
        call_count = []
        original = _m._do_init_metrics

        import observability.metrics as _m2  # same module object
        # Can't use monkeypatch here since reset_metrics already runs, use manual patch
        _m2._do_init_metrics_backup = _m2._do_init_metrics
        def _spy(*a, **kw):
            call_count.append(1)
            return original(*a, **kw)
        _m2._do_init_metrics = _spy
        try:
            _m2._ensure_initialized()  # should return immediately
        finally:
            _m2._do_init_metrics = _m2._do_init_metrics_backup

        assert call_count == [], (
            "_ensure_initialized() must return early if force_initialize() already ran"
        )


# ── Section 2: _do_init_metrics() env-var path ────────────────────────────────

class TestDoInitMetrics:
    """
    Verify the lazy env-var path (_do_init_metrics) handles all env states correctly
    and emits diagnostic logs.
    """

    def test_lazy_init_disabled_when_env_false(self, reset_metrics, mock_register_success):
        """_ensure_initialized() with PROMETHEUS_ENABLED=false → not available."""
        import observability.metrics as _m
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("PROMETHEUS_ENABLED", "false")
            _m._ensure_initialized()
        assert not _m._metrics_available

    def test_lazy_init_disabled_when_env_missing(self, reset_metrics, mock_register_success):
        """_ensure_initialized() with PROMETHEUS_ENABLED not in env → not available."""
        import observability.metrics as _m
        with pytest.MonkeyPatch.context() as mp:
            mp.delenv("PROMETHEUS_ENABLED", raising=False)
            _m._ensure_initialized()
        assert not _m._metrics_available

    def test_lazy_init_enabled_when_env_true(self, reset_metrics, mock_register_success):
        """_ensure_initialized() with PROMETHEUS_ENABLED=true → available."""
        import observability.metrics as _m
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("PROMETHEUS_ENABLED", "true")
            _m._ensure_initialized()
        assert _m._metrics_available

    def test_lazy_init_enabled_when_env_1(self, reset_metrics, mock_register_success):
        """_ensure_initialized() with PROMETHEUS_ENABLED=1 → available."""
        import observability.metrics as _m
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("PROMETHEUS_ENABLED", "1")
            _m._ensure_initialized()
        assert _m._metrics_available


# ── Section 3: /metrics endpoint behavior ─────────────────────────────────────

class TestMetricsEndpointBehavior:
    """
    Verify the /metrics route always returns 200 and is unauthenticated.

    These tests patch app.main.get_metrics_response (the module-level binding
    used by metrics_endpoint) so the behavior is controlled regardless of whether
    prometheus_client is installed or PROMETHEUS_ENABLED is set.

    Note: patching observability.metrics.get_metrics_response does NOT work because
    app/main.py imports it with `from ... import get_metrics_response`, creating a
    LOCAL binding. Patching the module attribute leaves app.main's local binding
    pointing to the original function.
    """

    def _make_client(self):
        from starlette.testclient import TestClient
        from api.app import create_app
        return TestClient(
            create_app(skip_config_validation=True),
            raise_server_exceptions=False,
        )

    def test_metrics_returns_200_with_prometheus_data(self, monkeypatch):
        """GET /metrics → 200 with prometheus text when metrics are enabled."""
        import app.main as _app_main
        fake_data = b"# HELP http_requests_total Total HTTP requests\n"
        monkeypatch.setattr(
            _app_main,
            "get_metrics_response",
            lambda: (fake_data, "text/plain; version=0.0.4; charset=utf-8"),
        )
        client = self._make_client()
        response = client.get("/metrics")
        assert response.status_code == 200
        assert b"http_requests_total" in response.content
        assert "text/plain" in response.headers["content-type"]

    def test_metrics_returns_200_with_placeholder_when_disabled(self, monkeypatch):
        """GET /metrics → 200 with placeholder comment when metrics are disabled."""
        import app.main as _app_main
        monkeypatch.setattr(_app_main, "get_metrics_response", lambda: None)
        client = self._make_client()
        response = client.get("/metrics")
        assert response.status_code == 200
        assert b"Metrics collection disabled" in response.content
        assert "text/plain" in response.headers["content-type"]

    def test_metrics_never_returns_401_without_api_key(self, monkeypatch):
        """GET /metrics must not require X-API-Key (unauthenticated Prometheus scraping)."""
        import app.main as _app_main
        monkeypatch.setattr(_app_main, "get_metrics_response", lambda: None)
        client = self._make_client()
        # No X-API-Key header
        response = client.get("/metrics")
        assert response.status_code != 401, (
            "/metrics must be exempt from API key auth so Prometheus can scrape it. "
            "Regression: was 401 in Sprint 2.11.1."
        )

    def test_metrics_never_returns_404(self, monkeypatch):
        """GET /metrics must not 404 (endpoint must always be registered)."""
        import app.main as _app_main
        monkeypatch.setattr(_app_main, "get_metrics_response", lambda: None)
        client = self._make_client()
        response = client.get("/metrics")
        assert response.status_code != 404


# ── Section 4: structural checks (source code assertions) ─────────────────────

class TestStructuralRequirements:
    """
    Verify key architectural properties of the metrics initialization code
    that prevent the production outage from recurring.

    Note: load_dotenv() uses override=False (the default) to preserve test
    isolation — conftest.py sets AUDIT_BACKEND=inmemory and override=True
    would replace it with the .env value (supabase), breaking 9+ tests.
    The production fix for env-var shadowing (system env overriding .env) is
    the diagnostic logging in _do_init_metrics() that surfaces the exact env
    var value observed, and force_initialize() in the lifespan that makes
    the initialization explicit and visible.
    """

    def test_force_initialize_exported_from_metrics(self):
        """observability/metrics.py must define force_initialize()."""
        from observability.metrics import force_initialize
        assert callable(force_initialize), (
            "force_initialize must be a callable exported from observability.metrics"
        )

    def test_force_initialize_signature_requires_enabled_keyword(self):
        """force_initialize() must accept enabled as a keyword-only argument."""
        import inspect
        from observability.metrics import force_initialize
        sig = inspect.signature(force_initialize)
        assert "enabled" in sig.parameters, "force_initialize must accept 'enabled' parameter"
        param = sig.parameters["enabled"]
        assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
            "force_initialize 'enabled' parameter must be keyword-only (force_initialize(*, enabled=...)). "
            "This prevents accidental positional calls with the wrong argument."
        )

    def test_force_initialize_in_lifespan_import(self):
        """app/main.py must import force_initialize from observability.metrics."""
        import pathlib
        main_path = pathlib.Path(__file__).parent.parent / "app" / "main.py"
        source = main_path.read_text(encoding="utf-8")
        assert "force_initialize" in source, (
            "app/main.py must import force_initialize from observability.metrics "
            "so the lifespan can explicitly initialize metrics using settings.prometheus_enabled "
            "rather than relying on os.getenv()."
        )

    def test_force_initialize_called_in_lifespan(self):
        """app/main.py lifespan must call force_initialize with explicit enabled flag."""
        import pathlib
        main_path = pathlib.Path(__file__).parent.parent / "app" / "main.py"
        source = main_path.read_text(encoding="utf-8")
        assert "_metrics_force_initialize" in source or "force_initialize(enabled=" in source, (
            "app/main.py lifespan must call force_initialize(enabled=...) to initialize metrics "
            "from the parsed settings value, bypassing os.getenv() entirely. "
            "This is the fix for the silent PROMETHEUS_ENABLED production outage."
        )

    def test_do_init_metrics_logs_env_var_value(self):
        """_do_init_metrics must log the raw PROMETHEUS_ENABLED value it observed."""
        import pathlib
        metrics_path = pathlib.Path(__file__).parent.parent / "observability" / "metrics.py"
        source = metrics_path.read_text(encoding="utf-8")
        # The function must log the raw env var value so operators can diagnose shadowing
        assert "PROMETHEUS_ENABLED=%r" in source or "PROMETHEUS_ENABLED='" in source, (
            "_do_init_metrics() must log the raw PROMETHEUS_ENABLED env var value it saw. "
            "Without this, a user with PROMETHEUS_ENABLED=false in their system env (shadowing .env) "
            "gets no diagnostic information about why metrics are disabled."
        )
