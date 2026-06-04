"""
api/health_providers.py

Sprint 2.11 B2: Health check providers for /health/ready endpoint.

Design:
  Each provider implements check() → HealthCheckResult.
  Results are aggregated by the readiness endpoint.
  Failures are caught — a bad provider should not 500 the health endpoint.

Providers:
  SupabaseHealthProvider  — tests Supabase connectivity with a lightweight ping.
  AuditHealthProvider     — verifies the audit repository is operational.
  ConfigHealthProvider    — verifies required env vars are set.

Thread safety:
  All providers are stateless and safe for concurrent use.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

LOGGER = logging.getLogger(__name__)

_REQUIRED_ENV_VARS = ("SUPABASE_URL", "SUPABASE_KEY", "RAG_API_KEY")


@dataclass(frozen=True)
class HealthCheckResult:
    """
    Result of a single health check.

    name:    Provider name (shown in /health/ready response).
    healthy: True if the check passed.
    message: Human-readable status or error description.
    latency_ms: Time taken for the check (0 if not measured).
    """
    name: str
    healthy: bool
    message: str
    latency_ms: float = 0.0


class SupabaseHealthProvider:
    """
    Checks connectivity to Supabase by running a lightweight query.

    Uses the supabase_client stored in app.state. If no client is configured,
    the check is skipped (healthy=True) — avoids false alarms in dev mode.
    """

    NAME = "supabase"

    def __init__(self, supabase_client: Any = None) -> None:
        self._client = supabase_client

    def check(self) -> HealthCheckResult:
        if self._client is None:
            return HealthCheckResult(
                name=self.NAME,
                healthy=True,
                message="Supabase client not configured (offline mode)",
            )
        try:
            import time
            t0 = time.perf_counter()
            # Lightweight ping: select 1 row from a known table (audit_events).
            # Falls back to a raw RPC if the table doesn't exist.
            try:
                self._client.table("audit_events").select("event_id").limit(1).execute()
            except Exception:
                # Try a simpler RPC ping
                self._client.rpc("version").execute()
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return HealthCheckResult(
                name=self.NAME,
                healthy=True,
                message="Connected",
                latency_ms=round(elapsed_ms, 2),
            )
        except Exception as exc:
            LOGGER.warning("health.supabase: connectivity check failed: %s", exc)
            return HealthCheckResult(
                name=self.NAME,
                healthy=False,
                message=f"Connection failed: {type(exc).__name__}",
            )


class AuditHealthProvider:
    """
    Checks the audit repository by counting events (non-destructive read).

    Returns healthy=True even if the repository returns 0 events.
    Returns healthy=False only if the repository raises an exception.
    """

    NAME = "audit"

    def __init__(self, audit_service: Any = None) -> None:
        self._svc = audit_service

    def check(self) -> HealthCheckResult:
        if self._svc is None:
            return HealthCheckResult(
                name=self.NAME,
                healthy=True,
                message="Audit service not configured",
            )
        try:
            import time
            t0 = time.perf_counter()
            self._svc.count()
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return HealthCheckResult(
                name=self.NAME,
                healthy=True,
                message="Operational",
                latency_ms=round(elapsed_ms, 2),
            )
        except Exception as exc:
            LOGGER.warning("health.audit: check failed: %s", exc)
            return HealthCheckResult(
                name=self.NAME,
                healthy=False,
                message=f"Audit check failed: {type(exc).__name__}",
            )


class ConfigHealthProvider:
    """
    Verifies required environment variables are set.

    Checks the presence (not validity) of REQUIRED_ENV_VARS in os.environ.
    This is a fast, no-I/O check suitable for liveness probes too.
    """

    NAME = "config"

    def __init__(self, required_vars: tuple[str, ...] = _REQUIRED_ENV_VARS) -> None:
        self._required = required_vars

    def check(self) -> HealthCheckResult:
        missing = [v for v in self._required if not os.environ.get(v, "").strip()]
        if missing:
            return HealthCheckResult(
                name=self.NAME,
                healthy=False,
                message=f"Missing required env vars: {', '.join(missing)}",
            )
        return HealthCheckResult(
            name=self.NAME,
            healthy=True,
            message="All required vars present",
        )


def aggregate_results(results: list[HealthCheckResult]) -> dict:
    """
    Aggregate a list of HealthCheckResult into a structured dict for the API response.
    """
    checks = {
        r.name: {
            "healthy": r.healthy,
            "message": r.message,
            **({"latency_ms": r.latency_ms} if r.latency_ms > 0 else {}),
        }
        for r in results
    }
    is_ready = all(r.healthy for r in results)
    return {
        "ready": is_ready,
        "checks": checks,
        "checked_at": datetime.now(tz=timezone.utc).isoformat(),
    }
