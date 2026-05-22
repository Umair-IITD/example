"""
app/security.py

Phase B2.5 Security: API key authentication, per-IP sliding-window rate limiting,
and structured audit logging for the KwikID RAG service.

Auth design:
  - All routes except /health, /ready, /freshdesk/webhook require X-API-Key header.
  - /freshdesk/webhook is exempt because it uses HMAC-SHA256 (app/freshdesk_webhook.py).
  - Constant-time multi-key comparison — no timing oracle on which key matched.
  - Empty key set → always reject (fail closed, not fail open).

Rate-limit design:
  - Sliding-window deque per client IP — no external dependency.
  - /rag/chat:          configurable via RAG_CHAT_RATE_LIMIT (default 20 req/60s)
  - /freshdesk/webhook: 60 req/60s
  - all other routes:   30 req/60s
  - Limits apply to ALL paths including /health (prevents DoS on health endpoints).

Audit logging:
  - All auth events (ok / missing / invalid) go to the "audit" logger.
  - Keys are never logged in full — only an 8-char prefix.
"""
from __future__ import annotations

import hmac
import logging
import os
import threading
from collections import deque
from time import monotonic
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

LOGGER = logging.getLogger(__name__)
AUDIT_LOGGER = logging.getLogger("audit")

# Paths that do not require an API key.
# /freshdesk/webhook is protected by HMAC-SHA256 instead.
_UNPROTECTED_PATHS: frozenset[str] = frozenset({
    "/health",
    "/ready",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/freshdesk/webhook",
})

# Populated at startup via initialize(). Empty until then → fail closed.
_API_KEYS: frozenset[str] = frozenset()


# ── Key management ─────────────────────────────────────────────────────────────

def initialize(api_keys: frozenset[str]) -> None:
    """Cache API keys once at startup so middleware never re-reads env per request."""
    global _API_KEYS
    _API_KEYS = api_keys


def load_api_keys() -> frozenset[str]:
    """Read API keys from RAG_API_KEY (single) and/or RAG_API_KEYS (CSV list).

    Both env vars are read simultaneously and merged. Blank values are discarded.
    """
    keys: set[str] = set()
    single = os.getenv("RAG_API_KEY", "").strip()
    if single:
        keys.add(single)
    for k in os.getenv("RAG_API_KEYS", "").split(","):
        k = k.strip()
        if k:
            keys.add(k)
    return frozenset(keys)


def _constant_time_key_check(provided: str, keys: frozenset[str]) -> bool:
    """Return True iff provided matches any key in keys, without timing oracle.

    Uses bitwise-OR across all comparisons (no short-circuit) so runtime is
    O(len(keys)) regardless of which key, if any, matched. An empty keys set
    always returns False (fail closed — do not allow unauthenticated access).
    """
    if not keys:
        return False
    provided_bytes = provided.encode("utf-8")
    result = 0
    for key in keys:
        result |= int(hmac.compare_digest(provided_bytes, key.encode("utf-8")))
    return bool(result)


def _key_prefix(key: str) -> str:
    """Return a log-safe truncated prefix of an API key (never the full key)."""
    return (key[:8] + "...") if len(key) > 8 else "***"


# ── Client IP extraction ───────────────────────────────────────────────────────

def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ── Sliding-window rate limiter ────────────────────────────────────────────────

class RateLimiter:
    """Thread-safe per-key sliding-window rate limiter using deque + threading.Lock.

    No external dependency. Timestamps older than window_s are evicted on access,
    so memory is proportional to max_requests * number_of_active_clients.
    """

    def __init__(self, max_requests: int, window_s: float) -> None:
        self._max = max_requests
        self._window = window_s
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def is_allowed(self, key: str) -> bool:
        """Return True and record request, or return False if limit exceeded."""
        now = monotonic()
        cutoff = now - self._window
        with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._max:
                return False
            bucket.append(now)
            return True

    @property
    def config(self) -> dict[str, Any]:
        return {"max_requests": self._max, "window_s": self._window}


# Module-level limiter instances — constructed at import time.
# RAG_CHAT_RATE_LIMIT is read once here; restart the process to change it.
_rl_chat = RateLimiter(
    max_requests=int(os.getenv("RAG_CHAT_RATE_LIMIT", "20")),
    window_s=60.0,
)
_rl_webhook = RateLimiter(max_requests=60, window_s=60.0)
_rl_default = RateLimiter(max_requests=30, window_s=60.0)


def _pick_limiter(path: str) -> RateLimiter:
    if path == "/rag/chat":
        return _rl_chat
    if path == "/freshdesk/webhook":
        return _rl_webhook
    return _rl_default


# ── HTTP middleware ────────────────────────────────────────────────────────────

async def api_key_auth_middleware(request: Request, call_next):
    """FastAPI HTTP middleware: rate-limit then authenticate every request.

    Registered via @app.middleware("http"). The last registered middleware is
    outermost in Starlette's LIFO stack, so register this AFTER log_requests
    to make it outermost (runs first on inbound requests).
    """
    path = request.url.path
    ip = _get_client_ip(request)

    # ── Rate limiting (applies to ALL paths, including /health) ───────────────
    if not _pick_limiter(path).is_allowed(ip):
        AUDIT_LOGGER.warning("rate_limit_exceeded ip=%s path=%s", ip, path)
        return JSONResponse(
            status_code=429,
            content={
                "error": "rate_limit_exceeded",
                "message": "Too many requests. Please retry later.",
            },
            headers={"Retry-After": "60"},
        )

    # ── API key authentication (skip exempt paths) ────────────────────────────
    if path not in _UNPROTECTED_PATHS:
        provided = request.headers.get("X-API-Key", "")

        if not provided:
            AUDIT_LOGGER.warning("auth_missing_key ip=%s path=%s", ip, path)
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "message": "X-API-Key header is required.",
                },
            )

        if not _constant_time_key_check(provided, _API_KEYS):
            AUDIT_LOGGER.warning(
                "auth_invalid_key ip=%s path=%s prefix=%s",
                ip, path, _key_prefix(provided),
            )
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "message": "Invalid API key.",
                },
            )

        AUDIT_LOGGER.debug(
            "auth_ok ip=%s path=%s prefix=%s", ip, path, _key_prefix(provided)
        )

    return await call_next(request)


# ── Startup validation ─────────────────────────────────────────────────────────

def validate_startup_security(api_keys: frozenset[str], openai_key: str) -> list[str]:
    """Return a list of fatal security configuration errors found at startup.

    The service MUST NOT start if this returns any errors — missing API keys
    means all protected endpoints are inaccessible, and missing OpenAI keys
    means the generation pipeline will fail on every request.
    """
    errors: list[str] = []
    if not api_keys:
        errors.append(
            "No API keys configured. Set RAG_API_KEY or RAG_API_KEYS env var. "
            "Without keys the middleware rejects all protected endpoints (fail closed), "
            "making the service permanently inaccessible."
        )
    if not openai_key:
        errors.append(
            "OPENAI_API_KEY is not set. /rag/chat and /freshdesk/webhook will fail "
            "on every generation request."
        )
    return errors
