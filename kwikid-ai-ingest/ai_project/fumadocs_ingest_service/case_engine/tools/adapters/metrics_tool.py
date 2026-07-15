"""
case_engine/tools/adapters/metrics_tool.py

Sprint 2.50: Production METRICTOOL and SERVERTOOL adapters.

These are the FIRST real (non-mock) BaseTool implementations in the
`case_engine/tools/adapters/` package. Both wrap the READ-ONLY Uptime Kuma
integration in `metrics_platform/` and expose it through the Sprint 2.17
BaseTool / ToolDefinition contract.

Architecture position (per flow_diagram.mermaid):

    EvidenceCollector → CapabilityRouter → BaseTool (Metric/Server) → UptimeKumaClient
                                       ↓
                                 MetricsEvidence / ServerHealthEvidence
                                       ↓
                                 EvidenceBundle → RootCauseEngine

Design rules
------------
- Adapters are synchronous (`BaseTool.run()` is sync per Sprint 2.17); we
  bridge to the async `UptimeKumaClient` via `asyncio.run` inside `run()`
  when no running loop is present, and `asyncio.new_event_loop()` under
  a nested-loop guard when one is.
- Adapters NEVER raise — errors are folded into a canonical
  `MetricsEvidence(data_available=UNAVAILABLE, error=...)` shape and
  returned as a successful `BaseTool.run()` payload dict. The upstream
  ToolExecutor never sees an exception, and the Investigation continues.
- Adapters honour the DataAvailability=DISABLED short-circuit when the
  config flag is off — no HTTP is issued, and the evidence carries the
  disabled marker.
- Traces (Sprint 2.50): ENTER_METRICS_TOOL, METRICS_QUERY, METRICS_RESPONSE,
  METRICS_NORMALIZED, METRICS_EVIDENCE_CREATED, EXIT_METRICS_TOOL.

Dependency direction
--------------------
    metrics_tool.py → case_engine.tools.tool_executor.BaseTool  (contract)
    metrics_tool.py → case_engine.tools.tool_models             (dataclasses)
    metrics_tool.py → metrics_platform.*                        (real integration)
    metrics_tool.py → stdlib
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from case_engine.tools.tool_executor import BaseTool
from case_engine.tools.tool_models import (
    ToolCapability,
    ToolDefinition,
    ToolProvider,
)
from metrics_platform import (
    DataAvailability,
    MetricsEvidence,
    MetricsPlatformConfig,
    MetricsPlatformError,
    ServerHealthEvidence,
    UptimeKumaClient,
    build_metrics_evidence,
    build_server_health_evidence,
    emit_metrics_trace,
    TRACE_ENTER_METRICS_TOOL,
    TRACE_EXIT_METRICS_TOOL,
    TRACE_METRICS_EVIDENCE_CREATED,
    TRACE_METRICS_NORMALIZED,
    TRACE_METRICS_QUERY,
    TRACE_METRICS_RESPONSE,
)

LOGGER = logging.getLogger(__name__)


# ── MetricTool ────────────────────────────────────────────────────────────────

class MetricTool(BaseTool):
    """
    Adapter implementing the METRICTOOL contract (SOT ai_investigation_mapping §3).

    Inputs (all optional, per SOT §3.2):
        ticket_created_at    ISO 8601 UTC — used for outage correlation
        tenant_id            e.g. "UNITY", "RBL"
        symptom_keywords     list[str]    used to filter monitors by name
        slug                 str          status page slug (defaults to config.default_slug)
        case_id              str          passthrough for correlation
        trace_id             str          passthrough

    Output payload keys (dict form of MetricsEvidence.to_dict()) — always
    present, always same shape, even on failure.
    """

    TOOL_NAME = "MetricTool"

    def __init__(
        self,
        config: MetricsPlatformConfig | None = None,
        *,
        client_factory=None,
    ) -> None:
        self._config = config or MetricsPlatformConfig.from_env()
        # client_factory: () -> UptimeKumaClient  — injected in tests to bypass network.
        self._client_factory = client_factory or (
            lambda: UptimeKumaClient(self._config)
        )

    # ── BaseTool contract ────────────────────────────────────────────────────

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_name=self.TOOL_NAME,
            description=(
                "Read-only Uptime Kuma metrics adapter. Returns monitor status, "
                "outage correlation, active incidents, maintenance windows, and "
                "uptime percentages for the investigation timeframe."
            ),
            required_inputs=(),           # every input has a safe default
            output_schema={
                "tool_name":              "str",
                "collected_at":           "iso8601",
                "source":                 "str (uptime_kuma)",
                "data_available":         "enum (AVAILABLE|PARTIAL|UNAVAILABLE|DISABLED)",
                "monitors":               "list[MonitorStatus]",
                "monitors_currently_down": "list[str]",
                "monitors_down_at_ticket_time": "list[OutageEvent]",
                "active_incident":        "IncidentInfo | null",
                "maintenance_windows":    "list[MaintenanceInfo]",
                "uptime_24h":             "list[UptimePercentage]",
            },
            version="1.0.0",
            tags=("metrics", "uptime_kuma", "monitoring", "read_only"),
            provider=ToolProvider.METRICS_PLATFORM,
            capability=ToolCapability.READ,
        )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """
        Execute METRICTOOL. Never raises — always returns a payload dict.
        """
        slug        = str(inputs.get("slug") or self._config.default_slug)
        tenant      = str(inputs.get("tenant_id") or "")
        case_id     = str(inputs.get("case_id") or "")
        trace_id    = str(inputs.get("trace_id") or "")
        ticket_ts   = inputs.get("ticket_created_at") or None
        keywords    = inputs.get("symptom_keywords") or ()

        emit_metrics_trace(
            TRACE_ENTER_METRICS_TOOL,
            tool=self.TOOL_NAME, tenant=tenant, case_id=case_id,
            trace_id=trace_id, endpoint=slug, status="STARTED",
        )

        if not self._config.enabled:
            evidence = build_metrics_evidence(
                tool_name=self.TOOL_NAME,
                prometheus_text=None, status_page=None, heartbeats=None,
                availability=DataAvailability.DISABLED,
                error="metrics platform disabled by configuration",
                tenant_id=tenant, trace_id=trace_id, case_id=case_id, slug=slug,
            )
            payload = self._emit_and_return(
                evidence, tenant=tenant, case_id=case_id, trace_id=trace_id,
                endpoint=slug, status="DISABLED",
            )
            return payload

        promo, status_page, heartbeats, availability, err = _fetch_all(
            client_factory=self._client_factory,
            slug=slug,
            fetch_prometheus=True,
            tenant=tenant, case_id=case_id, trace_id=trace_id,
        )

        emit_metrics_trace(
            TRACE_METRICS_NORMALIZED,
            tool=self.TOOL_NAME, tenant=tenant, case_id=case_id,
            trace_id=trace_id, endpoint=slug, status=availability.value,
        )

        evidence = build_metrics_evidence(
            tool_name=self.TOOL_NAME,
            prometheus_text=promo,
            status_page=status_page,
            heartbeats=heartbeats,
            symptom_keywords=keywords,
            ticket_created_at_iso=ticket_ts,
            tenant_id=tenant, trace_id=trace_id, case_id=case_id, slug=slug,
            availability=availability, error=err,
        )
        return self._emit_and_return(
            evidence, tenant=tenant, case_id=case_id, trace_id=trace_id,
            endpoint=slug, status=availability.value,
        )

    # ── Internal ─────────────────────────────────────────────────────────────

    def _emit_and_return(
        self,
        evidence: MetricsEvidence,
        *,
        tenant: str, case_id: str, trace_id: str, endpoint: str, status: str,
    ) -> dict[str, Any]:
        emit_metrics_trace(
            TRACE_METRICS_EVIDENCE_CREATED,
            tool=self.TOOL_NAME, tenant=tenant, case_id=case_id,
            trace_id=trace_id, endpoint=endpoint,
            status=f"{status}:{len(evidence.monitors)}mon",
        )
        emit_metrics_trace(
            TRACE_EXIT_METRICS_TOOL,
            tool=self.TOOL_NAME, tenant=tenant, case_id=case_id,
            trace_id=trace_id, endpoint=endpoint, status=status,
        )
        return evidence.to_dict()


# ── ServerTool ────────────────────────────────────────────────────────────────

class ServerTool(BaseTool):
    """
    Adapter implementing the SERVERTOOL contract (SOT ai_investigation_mapping §4).

    Distils the same Uptime Kuma status-page + heartbeat data into
    component-level infrastructure health. Uses only the two PUBLIC endpoints
    (no `/metrics`), so it works without an API key.
    """

    TOOL_NAME = "ServerTool"

    def __init__(
        self,
        config: MetricsPlatformConfig | None = None,
        *,
        client_factory=None,
    ) -> None:
        self._config = config or MetricsPlatformConfig.from_env()
        self._client_factory = client_factory or (
            lambda: UptimeKumaClient(self._config)
        )

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_name=self.TOOL_NAME,
            description=(
                "Read-only Uptime Kuma server-health adapter. Returns "
                "component-level up/down status for infrastructure "
                "(database, API, queue, worker)."
            ),
            required_inputs=(),
            output_schema={
                "tool_name":                "str",
                "collected_at":             "iso8601",
                "data_available":           "enum",
                "infrastructure_status":    "list[ComponentStatus]",
                "any_infrastructure_down":  "bool",
                "down_components":          "list[str]",
            },
            version="1.0.0",
            tags=("metrics", "server_health", "uptime_kuma", "read_only"),
            provider=ToolProvider.METRICS_PLATFORM,
            capability=ToolCapability.READ,
        )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        slug     = str(inputs.get("slug") or self._config.default_slug)
        tenant   = str(inputs.get("tenant_id") or "")
        case_id  = str(inputs.get("case_id") or "")
        trace_id = str(inputs.get("trace_id") or "")
        keywords = inputs.get("component_keywords") or ("db", "database", "api", "queue", "worker")

        emit_metrics_trace(
            TRACE_ENTER_METRICS_TOOL,
            tool=self.TOOL_NAME, tenant=tenant, case_id=case_id,
            trace_id=trace_id, endpoint=slug, status="STARTED",
        )

        if not self._config.enabled:
            evidence = build_server_health_evidence(
                tool_name=self.TOOL_NAME,
                status_page=None, heartbeats=None,
                availability=DataAvailability.DISABLED,
                error="metrics platform disabled by configuration",
                tenant_id=tenant, trace_id=trace_id, case_id=case_id, slug=slug,
            )
            return self._emit_and_return(
                evidence, tenant=tenant, case_id=case_id, trace_id=trace_id,
                endpoint=slug, status="DISABLED",
            )

        _promo, status_page, heartbeats, availability, err = _fetch_all(
            client_factory=self._client_factory,
            slug=slug,
            fetch_prometheus=False,          # SERVERTOOL does not need /metrics
            tenant=tenant, case_id=case_id, trace_id=trace_id,
        )

        emit_metrics_trace(
            TRACE_METRICS_NORMALIZED,
            tool=self.TOOL_NAME, tenant=tenant, case_id=case_id,
            trace_id=trace_id, endpoint=slug, status=availability.value,
        )

        evidence = build_server_health_evidence(
            tool_name=self.TOOL_NAME,
            status_page=status_page,
            heartbeats=heartbeats,
            component_keywords=keywords,
            tenant_id=tenant, trace_id=trace_id, case_id=case_id, slug=slug,
            availability=availability, error=err,
        )
        return self._emit_and_return(
            evidence, tenant=tenant, case_id=case_id, trace_id=trace_id,
            endpoint=slug, status=availability.value,
        )

    def _emit_and_return(
        self,
        evidence: ServerHealthEvidence,
        *,
        tenant: str, case_id: str, trace_id: str, endpoint: str, status: str,
    ) -> dict[str, Any]:
        emit_metrics_trace(
            TRACE_METRICS_EVIDENCE_CREATED,
            tool=self.TOOL_NAME, tenant=tenant, case_id=case_id,
            trace_id=trace_id, endpoint=endpoint,
            status=f"{status}:{len(evidence.infrastructure_status)}comp",
        )
        emit_metrics_trace(
            TRACE_EXIT_METRICS_TOOL,
            tool=self.TOOL_NAME, tenant=tenant, case_id=case_id,
            trace_id=trace_id, endpoint=endpoint, status=status,
        )
        return evidence.to_dict()


# ── Sync-over-async bridge ────────────────────────────────────────────────────

def _fetch_all(
    *,
    client_factory,
    slug: str,
    fetch_prometheus: bool,
    tenant: str,
    case_id: str,
    trace_id: str,
) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None, DataAvailability, str]:
    """
    Fetch (optionally) /metrics + /api/status-page/<slug> + heartbeat/<slug>
    concurrently. Never raises. Returns the tuple:

        (prometheus_text_or_None,
         status_page_dict_or_None,
         heartbeats_dict_or_None,
         availability,
         error_string)
    """
    try:
        return asyncio.run(_fetch_all_async(
            client_factory, slug, fetch_prometheus,
            tenant=tenant, case_id=case_id, trace_id=trace_id,
        ))
    except RuntimeError as exc:
        # Running inside an existing event loop (pytest-asyncio etc.).
        # Spin up a dedicated loop to run the fetch — safe because our
        # httpx client isn't shared across loops.
        if "already running" in str(exc).lower() or "cannot be called from a running event loop" in str(exc).lower():
            new_loop = asyncio.new_event_loop()
            try:
                return new_loop.run_until_complete(_fetch_all_async(
                    client_factory, slug, fetch_prometheus,
                    tenant=tenant, case_id=case_id, trace_id=trace_id,
                ))
            finally:
                new_loop.close()
        return (None, None, None, DataAvailability.UNAVAILABLE, f"event_loop_error: {exc}")


async def _fetch_all_async(
    client_factory,
    slug: str,
    fetch_prometheus: bool,
    *,
    tenant: str,
    case_id: str,
    trace_id: str,
) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None, DataAvailability, str]:
    client = client_factory()
    successes = 0
    attempts  = 0
    errors: list[str] = []
    prometheus_text: str | None = None
    status_page: dict[str, Any] | None = None
    heartbeats: dict[str, Any] | None = None

    async def _get_prometheus() -> None:
        nonlocal prometheus_text
        emit_metrics_trace(
            TRACE_METRICS_QUERY,
            tool="metric", tenant=tenant, case_id=case_id, trace_id=trace_id,
            endpoint="/metrics", status="DISPATCHED",
        )
        try:
            prometheus_text = await client.get_prometheus_metrics()
            emit_metrics_trace(
                TRACE_METRICS_RESPONSE,
                tool="metric", tenant=tenant, case_id=case_id, trace_id=trace_id,
                endpoint="/metrics", status="200",
            )
        except MetricsPlatformError as exc:
            errors.append(f"/metrics: {exc}")
            emit_metrics_trace(
                TRACE_METRICS_RESPONSE,
                tool="metric", tenant=tenant, case_id=case_id, trace_id=trace_id,
                endpoint="/metrics", status="ERROR",
            )
            raise

    async def _get_status_page() -> None:
        nonlocal status_page
        emit_metrics_trace(
            TRACE_METRICS_QUERY,
            tool="metric", tenant=tenant, case_id=case_id, trace_id=trace_id,
            endpoint=f"/api/status-page/{slug}", status="DISPATCHED",
        )
        try:
            status_page = await client.get_status_page(slug)
            emit_metrics_trace(
                TRACE_METRICS_RESPONSE,
                tool="metric", tenant=tenant, case_id=case_id, trace_id=trace_id,
                endpoint=f"/api/status-page/{slug}", status="200",
            )
        except MetricsPlatformError as exc:
            errors.append(f"/status-page/{slug}: {exc}")
            emit_metrics_trace(
                TRACE_METRICS_RESPONSE,
                tool="metric", tenant=tenant, case_id=case_id, trace_id=trace_id,
                endpoint=f"/api/status-page/{slug}", status="ERROR",
            )
            raise

    async def _get_heartbeats() -> None:
        nonlocal heartbeats
        emit_metrics_trace(
            TRACE_METRICS_QUERY,
            tool="metric", tenant=tenant, case_id=case_id, trace_id=trace_id,
            endpoint=f"/api/status-page/heartbeat/{slug}", status="DISPATCHED",
        )
        try:
            heartbeats = await client.get_heartbeats(slug)
            emit_metrics_trace(
                TRACE_METRICS_RESPONSE,
                tool="metric", tenant=tenant, case_id=case_id, trace_id=trace_id,
                endpoint=f"/api/status-page/heartbeat/{slug}", status="200",
            )
        except MetricsPlatformError as exc:
            errors.append(f"/heartbeat/{slug}: {exc}")
            emit_metrics_trace(
                TRACE_METRICS_RESPONSE,
                tool="metric", tenant=tenant, case_id=case_id, trace_id=trace_id,
                endpoint=f"/api/status-page/heartbeat/{slug}", status="ERROR",
            )
            raise

    tasks = []
    if fetch_prometheus:
        tasks.append(_get_prometheus()); attempts += 1
    tasks.append(_get_status_page());     attempts += 1
    tasks.append(_get_heartbeats());      attempts += 1

    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if not isinstance(r, Exception):
                successes += 1
    finally:
        await client.close()

    if successes == 0:
        availability = DataAvailability.UNAVAILABLE
    elif successes < attempts:
        availability = DataAvailability.PARTIAL
    else:
        availability = DataAvailability.AVAILABLE
    err = "; ".join(errors) if errors else ""
    return prometheus_text, status_page, heartbeats, availability, err


# ── Factories + registrar ────────────────────────────────────────────────────

def build_metric_tool(
    *,
    config: MetricsPlatformConfig | None = None,
    client_factory=None,
) -> MetricTool:
    return MetricTool(config=config, client_factory=client_factory)


def build_server_tool(
    *,
    config: MetricsPlatformConfig | None = None,
    client_factory=None,
) -> ServerTool:
    return ServerTool(config=config, client_factory=client_factory)


def register_metrics_tools(
    registry,
    *,
    config: MetricsPlatformConfig | None = None,
    client_factory=None,
) -> dict[str, str]:
    """
    Register both the MetricTool and ServerTool into the supplied registry.

    Works with either:
      - Sprint 2.17 `ToolRegistry` (has `.register(tool)`)
      - Sprint 2.45 `ProductionToolRegistry` (has `.register(tool, metadata=...)`
        and `.register_capability(evidence_kind, tool_name)`)

    Returns a dict `{tool_name: "registered" | "replaced" | "unavailable"}`
    so callers can log / audit the registration outcome.
    """
    outcomes: dict[str, str] = {}
    metric = build_metric_tool(config=config, client_factory=client_factory)
    server = build_server_tool(config=config, client_factory=client_factory)

    for tool in (metric, server):
        try:
            registry.register(tool)
            outcomes[tool.definition.tool_name] = "registered"
        except Exception as exc:
            outcomes[tool.definition.tool_name] = f"error:{exc}"

    # Capability routing (Sprint 2.45 registry only).
    if hasattr(registry, "register_capability"):
        try:
            # Both tools produce METRIC-kind evidence; the collector picks
            # via priority ordering, so we register MetricTool primary.
            registry.register_capability("METRIC", metric.definition.tool_name)
        except Exception as exc:
            LOGGER.debug("metrics_tool.register_capability warn=%s", exc)
    return outcomes
