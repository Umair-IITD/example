"""
audit/retry_policy.py

Sprint 2.10 Hardening: Production-grade retry policy for audit writes.

Design:
  AuditRetryPolicy encapsulates the decision of whether to retry a failed
  Supabase write, and how long to wait between attempts.

  Retry categories (transient — safe to retry):
    - Connection errors, timeouts, network resets
    - HTTP 5xx responses from Supabase/PostgREST

  Do NOT retry (permanent failures):
    - HTTP 409: duplicate primary key (event_id already exists)
    - HTTP 422: malformed payload (validation error)
    - HTTP 4xx other than rate-limit-class errors

  Backoff schedule (configurable, exponential with cap):
    attempt 1 → immediate (0 ms delay)
    attempt 2 → 100 ms
    attempt 3 → 250 ms
    attempt 4 → 500 ms

  Environment variables:
    AUDIT_RETRY_ENABLED=true   (default: true)
    AUDIT_MAX_RETRIES=4        (default: 4)

  Metrics emitted (via MetricsService if injected):
    audit_retry_total          — each non-first attempt
    audit_retry_success_total  — retried and eventually succeeded
    audit_retry_failure_total  — exhausted all retries

Thread safety:
  AuditRetryPolicy is stateless; safe for concurrent use.

AuditWriteResult:
  Returned by execute_with_retry().  Carries success flag, attempt count,
  and the last exception (if any) for observability.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)

# Delay schedule in seconds: attempt[i] → BACKOFF_DELAYS[i] seconds before retry.
# Index 0 = before first retry (after attempt 1 fails).
_DEFAULT_DELAYS_S: tuple[float, ...] = (0.0, 0.1, 0.25, 0.5)

# PostgreSQL / PostgREST error codes that should NOT be retried.
_PERMANENT_PG_CODES = frozenset({
    "23505",  # unique_violation (duplicate event_id)
    "23503",  # foreign_key_violation
    "23502",  # not_null_violation
    "22P02",  # invalid_text_representation
})


@dataclass(frozen=True)
class AuditWriteResult:
    """
    Result of an audit write attempt (possibly with retries).

    success:     True if the write eventually succeeded.
    attempts:    Number of attempts made (1 = no retry needed).
    last_error:  The last exception caught, if success=False.
    retried:     True if more than one attempt was made.
    """
    success: bool
    attempts: int = 1
    last_error: Exception | None = None
    retried: bool = False


def _is_retryable(exc: Exception) -> bool:
    """
    Return True if the exception is a transient error safe to retry.

    Conservative heuristic: only retry connection/timeout/5xx.
    Any structured PostgreSQL error with a permanent code is NOT retried.
    Any 4xx HTTP error (except rate-limit) is NOT retried.
    """
    exc_str = str(exc).lower()

    # Structured Supabase/PostgREST errors
    exc_type = type(exc).__name__.lower()
    if "apiresponse" in exc_type or "postgrest" in exc_type or "storageerror" in exc_type:
        # Check for 4xx status codes — permanent
        if any(f'"code": {c}' in exc_str or f"status={c}" in exc_str
               for c in ("400", "401", "403", "404", "409", "422")):
            return False
        for pg_code in _PERMANENT_PG_CODES:
            if pg_code in exc_str:
                return False

    # Network-level transient failures → always retry
    if any(kw in exc_str for kw in (
        "timeout", "connection", "reset", "refused", "eof", "broken pipe",
        "service unavailable", "internal server error", "bad gateway",
        "gateway timeout", "503", "500", "502", "504",
    )):
        return True

    # Duplicate primary key → permanent
    if "unique" in exc_str or "duplicate" in exc_str or "23505" in exc_str:
        return False

    # Default: do not retry unknown exceptions (conservative)
    return False


class AuditRetryPolicy:
    """
    Retry policy for audit writes.

    Call execute_with_retry(fn) to run fn() with automatic retries
    on transient failures.

    Args:
        max_retries:   Maximum number of attempts total (1 = no retry).
        delays_s:      Per-retry delay schedule in seconds.
        enabled:       Master switch; when False, execute_with_retry runs fn once.
        metrics:       Optional MetricsService for audit retry counters.
    """

    def __init__(
        self,
        *,
        max_retries: int | None = None,
        delays_s: tuple[float, ...] | None = None,
        enabled: bool | None = None,
        metrics: Any = None,
    ) -> None:
        env_enabled = os.environ.get("AUDIT_RETRY_ENABLED", "true").strip().lower()
        self._enabled = enabled if enabled is not None else env_enabled not in ("false", "0", "no")

        env_max = os.environ.get("AUDIT_MAX_RETRIES", "4")
        try:
            self._max_retries = max_retries if max_retries is not None else max(1, int(env_max))
        except (ValueError, TypeError):
            self._max_retries = 4

        self._delays = delays_s if delays_s is not None else _DEFAULT_DELAYS_S
        self._metrics = metrics

    def execute_with_retry(self, fn: Callable[[], None]) -> AuditWriteResult:
        """
        Call fn() up to max_retries times, retrying on transient errors.

        Returns AuditWriteResult describing outcome.
        Never raises — all exceptions are caught and returned.
        """
        if not self._enabled:
            try:
                fn()
                return AuditWriteResult(success=True, attempts=1)
            except Exception as exc:
                LOGGER.error("audit.retry: write failed (retries disabled): %s", exc)
                return AuditWriteResult(success=False, attempts=1, last_error=exc)

        last_exc: Exception | None = None
        retried = False

        for attempt in range(1, self._max_retries + 1):
            try:
                fn()
                if retried:
                    self._emit("audit_retry_success_total")
                return AuditWriteResult(success=True, attempts=attempt, retried=retried)

            except Exception as exc:
                last_exc = exc

                if not _is_retryable(exc):
                    LOGGER.error(
                        "audit.retry: permanent failure on attempt %d (not retrying): %s",
                        attempt, exc,
                    )
                    self._emit("audit_retry_failure_total")
                    return AuditWriteResult(
                        success=False, attempts=attempt, last_error=exc, retried=retried,
                    )

                if attempt >= self._max_retries:
                    break

                delay = self._delays[attempt - 1] if attempt - 1 < len(self._delays) else self._delays[-1]
                LOGGER.warning(
                    "audit.retry: transient failure attempt %d/%d, retrying in %.3fs: %s",
                    attempt, self._max_retries, delay, exc,
                )
                self._emit("audit_retry_total")
                retried = True
                if delay > 0:
                    time.sleep(delay)

        # Exhausted all retries
        LOGGER.error(
            "audit.retry: exhausted %d attempts. Last error: %s", self._max_retries, last_exc,
        )
        self._emit("audit_retry_failure_total")
        return AuditWriteResult(
            success=False, attempts=self._max_retries, last_error=last_exc, retried=True,
        )

    def _emit(self, counter_name: str) -> None:
        """Fire-and-forget metric emission. Never raises."""
        if self._metrics is None:
            return
        try:
            self._metrics._col.increment(counter_name)
        except Exception:
            pass
