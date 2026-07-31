# Metrics Root Cause Analysis — Sprint 2.11.2 Phase 1

**Date:** 2026-06-04
**Bug:** `PROMETHEUS_ENABLED=true` in `.env` had no effect — metrics were always disabled

---

## Symptom

Setting `PROMETHEUS_ENABLED=true` in `.env` did not enable Prometheus metrics. The `/metrics` endpoint always returned the disabled placeholder:

```
# KwikID AI Ingest Service
# Metrics collection disabled. Set PROMETHEUS_ENABLED=true to enable.
```

No counters or histograms were registered regardless of configuration.

---

## Root Cause: Import-Time Evaluation Before `load_dotenv()`

### Original code in `observability/metrics.py`

```python
# Module-level evaluation (OLD — BROKEN)
_ENABLED = os.getenv("PROMETHEUS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
_metrics_available = _init_metrics()  # called at import time using _ENABLED
```

### Import chain that triggered the bug

```
uvicorn loads app/main.py
  ↓
Line 20: from api.middleware.request_id import RequestIdMiddleware
  ↓
api/middleware/request_id.py imports observability.structured_logger
  ↓
observability/structured_logger.py imports observability.__init__
  ↓
observability/__init__.py: from . import metrics    ← MODULE-LEVEL CODE RUNS HERE
  ↓
os.getenv("PROMETHEUS_ENABLED") == None or "false"  ← .env NOT YET LOADED
  ↓
_ENABLED = False, _metrics_available = False
```

### Why `.env` was not yet loaded

`app/main.py` calls `load_dotenv()` (via `app.config`) at line ~40. The `observability.metrics` module is imported at line 20 via the `RequestIdMiddleware` chain. Python module-level code runs exactly once, on first import. By the time `load_dotenv()` runs, `_ENABLED` is already frozen to `False`.

---

## Fix: Lazy Initialization with Double-Checked Locking

### New pattern in `observability/metrics.py`

```python
import threading

_init_lock = threading.Lock()
_initialized = False
_metrics_available = False

# All metric objects initialized to None
_http_requests_total: Any = None
# ... etc ...

def _do_init_metrics() -> bool:
    """Read PROMETHEUS_ENABLED at call time (not import time)."""
    enabled = os.getenv("PROMETHEUS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return False
    try:
        from prometheus_client import Counter, Gauge, Histogram
        _http_requests_total = Counter("http_requests_total", ...)
        # ... register all metrics ...
        return True
    except ImportError:
        LOGGER.warning("prometheus_client not installed")
        return False

def _ensure_initialized() -> None:
    """Double-checked locking — called at the top of every record_* function."""
    global _initialized, _metrics_available
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        _metrics_available = _do_init_metrics()
        _initialized = True

def record_request(method, path, status, duration_s):
    _ensure_initialized()       # ← reads os.environ AFTER load_dotenv() has run
    if not _metrics_available:
        return
    ...
```

### Why this works

- `_ensure_initialized()` is called on the **first actual metrics operation**, which happens during request processing — long after `load_dotenv()` has populated `os.environ`.
- Double-checked locking ensures thread safety: the lock is only acquired once (on first call), then the `_initialized` flag fast-paths all subsequent calls.
- The module can be imported freely at any point in the import chain without side effects.

---

## Validation

After the fix, setting `PROMETHEUS_ENABLED=true` in `.env` correctly enables all 7 metric instruments:

- `http_requests_total` (Counter)
- `http_request_duration_seconds` (Histogram)
- `retrieval_latency_seconds` (Histogram)
- `llm_latency_seconds` (Histogram)
- `rate_limit_rejections_total` (Counter)
- `active_requests` (Gauge)
- `retrieval_candidates_count` (Histogram)

The `/metrics` endpoint returns real Prometheus text exposition format with counters populated after requests.
