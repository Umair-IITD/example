"""
app/rate_limiter.py

Distributed sliding-window rate limiter with Redis backend and graceful
in-process fallback.

Algorithm: Sliding Window with Redis Sorted Set (ZADD + ZRANGEBYSCORE)
  - Redis key: "rl:{endpoint_prefix}:{client_id}"
  - Sorted set score = request timestamp (ms)
  - Atomic Lua script: remove expired, count, add if under limit
  - PEXPIRE on each ZADD ensures automatic cleanup

Configuration:
  REDIS_RATE_LIMIT_ENABLED=false    — master switch (default: off for local dev)
  REDIS_URL=redis://localhost:6379  — connection URL
  REDIS_SOCKET_TIMEOUT_S=1.0       — fast-fail timeout (keep requests fast)
  REDIS_SOCKET_CONNECT_TIMEOUT_S=0.5
  RAG_CHAT_RATE_LIMIT=20           — requests per 60s for /rag/chat

Fallback behavior:
  - If Redis disabled or unreachable: transparently uses in-process deque limiter
  - In-process limiter is per-worker (not distributed) but always safe
  - Automatic Redis reconnection is NOT attempted (restart required to reconnect)
    — avoids latency spikes from repeated failed connection attempts

Security:
  - Rate limit keys never contain user data — only endpoint prefix + hashed IP
  - Redis commands are atomic (Lua script) — no TOCTOU race conditions
  - Fast-fail timeout prevents Redis slowness from blocking requests
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections import deque
from typing import Any

LOGGER = logging.getLogger(__name__)


# ── In-process fallback ───────────────────────────────────────────────────────

class InProcessSlidingWindow:
    """
    Thread-safe per-key sliding-window rate limiter using a deque + lock.
    Per-worker only — not correct across multiple uvicorn workers.
    Used as fallback when Redis is unavailable or disabled.
    """

    def __init__(self, max_requests: int, window_s: float) -> None:
        self._max = max_requests
        self._window = window_s
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            # Evict expired timestamps
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._max:
                return False
            bucket.append(now)
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)

    @property
    def backend_type(self) -> str:
        return "in_process"

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": "in_process",
            "max_requests": self._max,
            "window_s": self._window,
        }


# ── Redis sliding-window ──────────────────────────────────────────────────────

# Lua script: atomic sliding-window check-and-increment.
# Returns 1 (allowed) or 0 (rejected).
# Uses millisecond timestamps for sub-second accuracy.
_LUA_SCRIPT = """
local key        = KEYS[1]
local now_ms     = tonumber(ARGV[1])
local window_ms  = tonumber(ARGV[2])
local limit      = tonumber(ARGV[3])
local window_start = now_ms - window_ms

-- 1. Remove entries older than the window
redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start - 1)

-- 2. Count remaining entries in window
local count = redis.call('ZCARD', key)

if count < limit then
    -- 3a. Add current request (score = timestamp, member = timestamp+jitter for uniqueness)
    local jitter = math.random(0, 9999)
    local member = tostring(now_ms) .. ':' .. tostring(jitter)
    redis.call('ZADD', key, now_ms, member)
    -- 4. Refresh TTL slightly longer than window
    redis.call('PEXPIRE', key, window_ms + 5000)
    return 1
else
    -- 3b. Rejected — don't add to key
    return 0
end
"""


class RedisRateLimiter:
    """
    Distributed rate limiter using Redis sorted-set sliding window.
    Falls back to in-process InProcessSlidingWindow if Redis unavailable.
    Thread-safe and multi-worker-safe.
    """

    def __init__(
        self,
        max_requests: int,
        window_s: float,
        fallback: InProcessSlidingWindow,
        redis_client: Any = None,
        key_prefix: str = "rl",
    ) -> None:
        self._max = max_requests
        self._window_ms = int(window_s * 1000)
        self._fallback = fallback
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._script: Any = None
        self._using_redis = False

        if redis_client is not None:
            try:
                self._script = redis_client.register_script(_LUA_SCRIPT)
                self._using_redis = True
                LOGGER.info(
                    "RedisRateLimiter[%s]: Lua script registered max=%d window_s=%.0f",
                    key_prefix, max_requests, window_s,
                )
            except Exception as exc:
                LOGGER.warning(
                    "RedisRateLimiter[%s]: Script registration failed (%s) — using in-process fallback",
                    key_prefix, exc,
                )

    def is_allowed(self, client_id: str) -> bool:
        """
        Check if the client is within rate limit. Records the request if allowed.
        client_id should be an IP address or hashed identifier.
        """
        # Hash the key to avoid storing PII in Redis even if IP is passed
        safe_key = f"{self._key_prefix}:{_hash_client_id(client_id)}"

        if self._using_redis and self._script is not None:
            try:
                now_ms = int(time.time() * 1000)
                result = self._script(
                    keys=[safe_key],
                    args=[now_ms, self._window_ms, self._max],
                )
                return bool(result)
            except Exception as exc:
                LOGGER.warning(
                    "RedisRateLimiter[%s]: Redis call failed (%s) — falling back to in-process",
                    self._key_prefix, type(exc).__name__,
                )
                self._using_redis = False
                # Fall through to in-process

        return self._fallback.is_allowed(safe_key)

    @property
    def backend_type(self) -> str:
        return "redis" if self._using_redis else "in_process"

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend_type,
            "max_requests": self._max,
            "window_ms": self._window_ms,
            "prefix": self._key_prefix,
        }


def _hash_client_id(client_id: str) -> str:
    """Hash a client IP/ID to a short hex string for use as Redis key component."""
    return hashlib.sha1(client_id.encode(), usedforsecurity=False).hexdigest()[:16]


# ── Factory ───────────────────────────────────────────────────────────────────

def _create_redis_client() -> Any | None:
    """
    Attempt to create a Redis client from environment configuration.
    Returns None if Redis is disabled or unavailable.
    """
    enabled = os.getenv("REDIS_RATE_LIMIT_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if not enabled:
        LOGGER.info("Redis rate limiting disabled (REDIS_RATE_LIMIT_ENABLED=false)")
        return None

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    socket_timeout = float(os.getenv("REDIS_SOCKET_TIMEOUT_S", "1.0"))
    connect_timeout = float(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT_S", "0.5"))

    try:
        import redis as redis_lib  # noqa: PLC0415
    except ImportError:
        LOGGER.warning(
            "REDIS_RATE_LIMIT_ENABLED=true but 'redis' package is not installed. "
            "Add it to requirements.txt: redis>=5.0. Falling back to in-process limiting."
        )
        return None

    # Redact credentials from log
    log_url = redis_url.split("@")[-1] if "@" in redis_url else redis_url
    try:
        client = redis_lib.from_url(
            redis_url,
            socket_timeout=socket_timeout,
            socket_connect_timeout=connect_timeout,
            decode_responses=True,
            health_check_interval=30,
        )
        client.ping()
        LOGGER.info("Redis rate limiter connected to %s", log_url)
        return client
    except Exception as exc:
        LOGGER.warning(
            "REDIS_RATE_LIMIT_ENABLED=true but cannot connect to Redis at %s: %s. "
            "Falling back to in-process rate limiting.",
            log_url, exc,
        )
        return None


def build_rate_limiters() -> dict[str, RedisRateLimiter]:
    """
    Build rate limiter instances for each endpoint class.
    Called once at module import time — expensive operations happen here,
    not per-request.

    Returns:
        Dict with keys: "chat", "webhook", "default"
    """
    redis_client = _create_redis_client()
    chat_limit = int(os.getenv("RAG_CHAT_RATE_LIMIT", "20"))

    limiters = {
        "chat": _build_limiter(chat_limit, 60.0, "chat", redis_client),
        "webhook": _build_limiter(60, 60.0, "webhook", redis_client),
        "default": _build_limiter(30, 60.0, "default", redis_client),
    }

    backend = "redis" if redis_client else "in_process"
    LOGGER.info(
        "rate_limiters_built backend=%s chat=%d/60s webhook=60/60s default=30/60s",
        backend, chat_limit,
    )
    return limiters


def _build_limiter(
    max_requests: int,
    window_s: float,
    prefix: str,
    redis_client: Any,
) -> RedisRateLimiter:
    fallback = InProcessSlidingWindow(max_requests=max_requests, window_s=window_s)
    return RedisRateLimiter(
        max_requests=max_requests,
        window_s=window_s,
        fallback=fallback,
        redis_client=redis_client,
        key_prefix=prefix,
    )


def pick_limiter(
    limiters: dict[str, RedisRateLimiter],
    path: str,
) -> RedisRateLimiter:
    """Select the appropriate rate limiter for a request path."""
    if path.startswith("/rag/chat"):
        return limiters["chat"]
    if path.startswith("/freshdesk/webhook"):
        return limiters["webhook"]
    return limiters["default"]


# ── Module-level default instances ───────────────────────────────────────────
# These are built once at import time and reused for the process lifetime.
# Import this dict rather than calling build_rate_limiters() multiple times.
_DEFAULT_LIMITERS: dict[str, RedisRateLimiter] | None = None
_LIMITERS_LOCK = threading.Lock()


def get_limiters() -> dict[str, RedisRateLimiter]:
    """
    Return the module-level rate limiters, building them once on first call.
    Thread-safe via double-checked locking.
    """
    global _DEFAULT_LIMITERS
    if _DEFAULT_LIMITERS is not None:
        return _DEFAULT_LIMITERS
    with _LIMITERS_LOCK:
        if _DEFAULT_LIMITERS is None:
            _DEFAULT_LIMITERS = build_rate_limiters()
    return _DEFAULT_LIMITERS
