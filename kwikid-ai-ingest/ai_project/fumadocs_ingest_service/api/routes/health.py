"""
api/routes/health.py

Sprint 2.11 B1: Health endpoints.

Routes:
  GET /health       — legacy runtime health (unchanged from Sprint 2.7)
  GET /health/live  — liveness probe: process is alive and running
  GET /health/ready — readiness probe: service can serve requests

Liveness vs readiness:
  /health/live returns 200 as long as the Python process is handling requests.
  It should be used by container orchestrators to detect crashes/deadlocks.

  /health/ready returns 200 only when all dependency checks pass:
    - Supabase connectivity
    - Audit service operational
    - Required env vars present

  200 = healthy/ready. 503 = unhealthy/not-ready. Body always returned.

Response format (application/json):
  /health/live:
    {"alive": true, "service": "kwikid-ai-ingest", "checked_at": "<ISO8601>"}

  /health/ready:
    {
      "ready": true|false,
      "checks": {
        "supabase": {"healthy": true, "message": "Connected", "latency_ms": 12.3},
        "audit":    {"healthy": true, "message": "Operational"},
        "config":   {"healthy": true, "message": "All required vars present"}
      },
      "checked_at": "<ISO8601>"
    }
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.health_providers import (
    AuditHealthProvider,
    ConfigHealthProvider,
    SupabaseHealthProvider,
    aggregate_results,
)

LOGGER = logging.getLogger(__name__)

router = APIRouter()

_SERVICE_NAME = "kwikid-ai-ingest"


@router.get("/health")
def get_health(request: Request) -> JSONResponse:
    """
    Legacy runtime health check (Sprint 2.7).

    Returns 200 when the Action Gateway runtime is healthy.
    """
    stack = request.app.state.stack
    if stack is None:
        return JSONResponse(
            status_code=503,
            content={
                "is_healthy": False,
                "error": "Runtime not initialised",
            },
        )
    try:
        health = stack.health.check()
        body = {
            "is_healthy": health.is_healthy,
            "provider_statuses": health.provider_statuses,
            "executor_count": health.executor_count,
            "executor_keys": [
                {"namespace": ns, "action_type": at}
                for ns, at in health.executor_keys
            ],
            "runtime_ready": health.runtime_ready,
            "worker_available": health.worker_available,
            "checked_at": health.checked_at.isoformat(),
        }
        status_code = 200 if health.is_healthy else 503
        return JSONResponse(content=body, status_code=status_code)
    except Exception as exc:
        LOGGER.error("health.get_health: error=%s", exc)
        return JSONResponse(
            status_code=503,
            content={"is_healthy": False, "error": "Health check failed"},
        )


@router.get("/health/live")
def liveness(request: Request) -> JSONResponse:
    """
    Liveness probe — returns 200 while the process is running.

    Used by Kubernetes/Docker to detect if the pod needs restarting.
    Never touches external systems.
    """
    return JSONResponse(
        status_code=200,
        content={
            "alive": True,
            "service": _SERVICE_NAME,
            "checked_at": datetime.now(tz=timezone.utc).isoformat(),
        },
    )


@router.get("/health/ready")
def readiness(request: Request) -> JSONResponse:
    """
    Readiness probe — returns 200 when the service can serve requests.

    Checks: Supabase connectivity, audit service, required config.
    Returns 503 if any check fails (body still returned for diagnosis).
    """
    state = request.app.state

    # Extract dependencies from app.state
    supabase_client = _get_supabase_client(state)
    audit_service = getattr(state, "audit_service", None)

    providers = [
        ConfigHealthProvider(),
        SupabaseHealthProvider(supabase_client=supabase_client),
        AuditHealthProvider(audit_service=audit_service),
    ]

    results = []
    for provider in providers:
        try:
            results.append(provider.check())
        except Exception as exc:
            LOGGER.error("health.readiness: provider=%s error=%s", provider.NAME, exc)
            from api.health_providers import HealthCheckResult
            results.append(HealthCheckResult(
                name=provider.NAME,
                healthy=False,
                message=f"Provider check failed: {type(exc).__name__}",
            ))

    body = aggregate_results(results)
    body["service"] = _SERVICE_NAME
    status_code = 200 if body["ready"] else 503
    return JSONResponse(content=body, status_code=status_code)


def _get_supabase_client(state) -> object | None:
    """Extract supabase client from app state, trying multiple locations."""
    # Direct injection (tests)
    client = getattr(state, "supabase_client", None)
    if client is not None:
        return client
    # Via stack (production)
    stack = getattr(state, "stack", None)
    if stack is not None:
        return getattr(stack, "_supabase_client", None) or getattr(stack, "supabase_client", None)
    return None
