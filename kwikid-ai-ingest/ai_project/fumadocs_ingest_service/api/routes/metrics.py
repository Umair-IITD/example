"""
api/routes/metrics.py

Sprint 2.10: Prometheus metrics endpoint.

GET /metrics — returns all action gateway metrics in Prometheus text format.

Design:
  - No authentication required (Prometheus scrapers don't carry API keys by default).
  - Read-only endpoint: no state mutation.
  - Returns text/plain; version=0.0.4 content type.
  - If metrics_service is not wired (offline/test mode), returns empty metrics.
  - Failures in prometheus_text() return a safe empty body (never 500).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

LOGGER = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    include_in_schema=False,
)
async def get_metrics(request: Request) -> PlainTextResponse:
    """
    Expose action gateway metrics in Prometheus text exposition format.

    No authentication required. Safe to expose to internal Prometheus scrapers.
    Content-Type: text/plain; version=0.0.4
    """
    try:
        metrics_service = getattr(request.app.state, "metrics_service", None)
        if metrics_service is None:
            return PlainTextResponse("# metrics_service not configured\n")
        text = metrics_service.prometheus_text()
        return PlainTextResponse(text)
    except Exception as exc:
        LOGGER.error("metrics.get_metrics: failed to generate output error=%s", exc)
        return PlainTextResponse("# error generating metrics\n")
