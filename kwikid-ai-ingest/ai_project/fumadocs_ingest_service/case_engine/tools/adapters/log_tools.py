"""
case_engine/tools/adapters/log_tools.py — GetSessionLogsTool adapter.

Sprint 2.60: Multi-Tenant Log Platform (Grafana Loki) BaseTool adapter.

Retrieves and curates backend log evidence for a VKYC session. The flow is:
  1. GetSessionDetailsTool (already built) → returns start_time/end_time epoch floats
  2. GetSessionLogsTool → uses those epochs to query Loki, applies PII redaction
     + BM25 relevance extraction, returns ONLY the curated log excerpt

Security constraints (Blueprint §27/§34/§35):
  - PII FIRST: _redact_and_truncate() runs on every line before scoring
  - NO RAW LOGS TO LLM: raw text from fetch_session_logs NEVER leaves this module
  - NEVER RAISE: run() catches all exceptions, returns canonical UNAVAILABLE dict
  - DISABLED DEGRADATION: if credentials missing, log_availability="DISABLED"
  - PARTIAL != UNAVAILABLE: empty Loki result = PARTIAL (retention aging)
  - asyncio.to_thread(): sync httpx wrapped via _run_async() bridge pattern

Dependency direction:
    log_tools.py → case_engine.tools.tool_executor.BaseTool
               → case_engine.tools.tool_models
               → case_engine.integrations.loki.*
               → stdlib
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from case_engine.tools.tool_executor import BaseTool
from case_engine.tools.tool_models import (
    ToolCapability,
    ToolDefinition,
    ToolProvider,
)
from case_engine.integrations.loki.config import LokiConfig, LokiLogAvailability
from case_engine.integrations.loki.client import LokiClient, fetch_session_logs
from case_engine.integrations.loki.relevance import extract_relevant_lines

LOGGER = logging.getLogger(__name__)

_FETCH_BUFFER = timedelta(minutes=5)


# ── Shared async runner (copied exactly from unity_tools.py) ─────────────────

def _run_async(coro):
    """
    Bridge sync BaseTool.run() to async work.
    Works from both sync contexts and async contexts (FastAPI async background tasks).
    When a running event loop is detected, the coroutine is executed in a separate
    OS thread via ThreadPoolExecutor so asyncio.run() can create its own loop.
    """
    try:
        asyncio.get_running_loop()
        # Already inside a running event loop — run in a separate thread to avoid
        # "asyncio.run() cannot be called from a running event loop".
        import concurrent.futures  # noqa: PLC0415
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        # No running loop present — asyncio.run() is safe.
        return asyncio.run(coro)


# ── Async fetch helper ────────────────────────────────────────────────────────

async def _async_fetch(
    config: LokiConfig,
    tenant_key: str,
    session_id: str,
    start_dt: datetime,
    end_dt: datetime,
    ticket_query: str | None,
) -> dict[str, Any]:
    """
    Async wrapper around the synchronous Loki HTTP calls.
    fetch_session_logs is sync (httpx.Client) — wrapped via asyncio.to_thread.
    Raw text NEVER leaves this function; only curated_log_excerpt is returned.
    """
    availability = LokiLogAvailability.AVAILABLE
    curated_excerpt = ""
    selected_lines: list = []
    stats: dict = {}
    raw_text: str | None = None

    try:
        with LokiClient(config, tenant_key) as client:
            # fetch_session_logs uses sync httpx — wrap in to_thread
            raw_text = await asyncio.to_thread(
                fetch_session_logs,
                client,
                session_id,
                start_dt - _FETCH_BUFFER,
                end_dt + _FETCH_BUFFER,
            )

        # Detect PARTIAL: HTTP succeeded but Loki returned no data
        raw_lines = [
            line for line in raw_text.splitlines()
            if line.startswith("[20")
        ] if raw_text else []

        if not raw_lines:
            availability = LokiLogAvailability.PARTIAL
        else:
            selected_lines, stats = extract_relevant_lines(
                raw_lines,
                ticket_query or session_id,
            )
            curated_excerpt = "\n".join(p.content for p in selected_lines)
            # raw_text is intentionally not returned or logged — PII risk

    except Exception as exc:  # noqa: BLE001
        availability = LokiLogAvailability.UNAVAILABLE
        return {
            "tool_name": "GetSessionLogsTool",
            "session_id": session_id,
            "tenant": tenant_key,
            "log_availability": LokiLogAvailability.UNAVAILABLE.value,
            "window_start_utc": start_dt.isoformat(),
            "window_end_utc": end_dt.isoformat(),
            "log_line_count": 0,
            "log_char_count": 0,
            "curated_log_excerpt": "",
            "reduction_stats": {},
            "collected_at": datetime.now(tz=timezone.utc).isoformat(),
            "ambiguity_note": "",
            "error": str(exc),
        }
    finally:
        # Explicit scrub of raw_text — defence in depth against accidental leakage
        raw_text = None  # noqa: F841

    ambiguity_note = ""
    if availability == LokiLogAvailability.PARTIAL:
        ambiguity_note = (
            "No log lines found — data may have aged out of Loki retention window. "
            "Empty result does not confirm absence of a technical issue."
        )

    return {
        "tool_name": "GetSessionLogsTool",
        "session_id": session_id,
        "tenant": tenant_key,
        "log_availability": availability.value,
        "window_start_utc": start_dt.isoformat(),
        "window_end_utc": end_dt.isoformat(),
        "log_line_count": len(selected_lines),
        "log_char_count": stats.get("output_chars", 0),
        "curated_log_excerpt": curated_excerpt,
        "reduction_stats": stats,
        "collected_at": datetime.now(tz=timezone.utc).isoformat(),
        "ambiguity_note": ambiguity_note,
        "error": "",
    }


# ── GetSessionLogsTool ────────────────────────────────────────────────────────

class GetSessionLogsTool(BaseTool):
    """
    Retrieves and curates backend log evidence for a VKYC session from Grafana Loki.

    Takes session_id (+ optional start_epoch / end_epoch from GetSessionDetailsTool)
    and returns a PII-redacted, BM25-ranked log excerpt in curated_log_excerpt.

    Never raises — all failures return a canonical dict with log_availability=UNAVAILABLE.
    """

    TOOL_NAME = "GetSessionLogsTool"

    def __init__(self, config: LokiConfig | None = None) -> None:
        self._config = config or LokiConfig.from_env()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_name=self.TOOL_NAME,
            description=(
                "Retrieve and curate backend log evidence for a VKYC session "
                "from the Multi-Tenant Grafana Loki log platform. Uses session "
                "timeline from GetSessionDetailsTool. Log excerpt is PII-redacted "
                "and BM25-ranked before delivery. Never returns raw logs."
            ),
            required_inputs=("session_id",),
            output_schema={
                "tool_name":           "str",
                "session_id":          "str",
                "tenant":              "str",
                "log_availability":    "enum[AVAILABLE|PARTIAL|UNAVAILABLE|DISABLED]",
                "window_start_utc":    "iso8601",
                "window_end_utc":      "iso8601",
                "log_line_count":      "int",
                "log_char_count":      "int",
                "curated_log_excerpt": "str",
                "reduction_stats":     "dict",
                "collected_at":        "iso8601",
                "ambiguity_note":      "str",
                "error":               "str",
            },
            version="1.0.0",
            tags=("loki", "logs", "backend", "read_only"),
            provider=ToolProvider.LOG_PLATFORM,
            capability=ToolCapability.READ,
        )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """
        Execute the log retrieval. NEVER raises.
        """
        session_id: str = inputs.get("session_id", "")
        start_epoch: float | None = inputs.get("start_epoch")
        end_epoch: float | None = inputs.get("end_epoch")
        ticket_query: str | None = inputs.get("ticket_query")
        tenant: str = inputs.get("tenant", "saas")

        # DISABLED check: if credentials not configured, return immediately
        if not self._config.enabled:
            return {
                "tool_name": self.TOOL_NAME,
                "session_id": session_id,
                "tenant": tenant,
                "log_availability": LokiLogAvailability.DISABLED.value,
                "window_start_utc": "",
                "window_end_utc": "",
                "log_line_count": 0,
                "log_char_count": 0,
                "curated_log_excerpt": "",
                "reduction_stats": {},
                "collected_at": datetime.now(tz=timezone.utc).isoformat(),
                "ambiguity_note": "",
                "error": "",
            }

        # Build start/end datetimes
        if start_epoch is not None:
            start_dt = datetime.fromtimestamp(float(start_epoch), tz=timezone.utc)
        else:
            start_dt = datetime.now(tz=timezone.utc) - timedelta(hours=2)

        if end_epoch is not None:
            end_dt = datetime.fromtimestamp(float(end_epoch), tz=timezone.utc)
        else:
            end_dt = datetime.now(tz=timezone.utc)

        try:
            return _run_async(
                _async_fetch(
                    self._config, tenant, session_id, start_dt, end_dt, ticket_query
                )
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "tool_name": self.TOOL_NAME,
                "session_id": session_id,
                "tenant": tenant,
                "log_availability": LokiLogAvailability.UNAVAILABLE.value,
                "window_start_utc": "",
                "window_end_utc": "",
                "log_line_count": 0,
                "log_char_count": 0,
                "curated_log_excerpt": "",
                "reduction_stats": {},
                "collected_at": datetime.now(tz=timezone.utc).isoformat(),
                "ambiguity_note": "",
                "error": str(exc),
            }


# ── Registration helper ───────────────────────────────────────────────────────

def register_loki_tools(
    registry,
    *,
    config: LokiConfig | None = None,
) -> dict[str, str]:
    """
    Register GetSessionLogsTool into the supplied registry.

    Works with either:
      - Sprint 2.17 ToolRegistry (has .register(tool))
      - Sprint 2.45 ProductionToolRegistry (has .register_capability(...))

    Returns a dict {tool_name: "registered" | "error:<msg>"}.
    """
    outcomes: dict[str, str] = {}
    tool = GetSessionLogsTool(config=config)

    try:
        registry.register(tool)
        outcomes[tool.definition.tool_name] = "registered"
    except Exception as exc:  # noqa: BLE001
        outcomes[tool.definition.tool_name] = f"error:{exc}"

    # Capability routing (Sprint 2.45 registry only)
    if hasattr(registry, "register_capability"):
        try:
            registry.register_capability("LOG", tool.definition.tool_name)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("log_tools.register_capability warn=%s", exc)

    return outcomes
