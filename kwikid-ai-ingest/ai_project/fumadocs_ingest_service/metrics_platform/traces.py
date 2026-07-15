"""
metrics_platform/traces.py

Sprint 2.50: Deterministic runtime traces for the Metrics Dashboard boundary.

Six canonical tags emitted at fixed points across the tool run so an
operator can verify the METRICTOOL / SERVERTOOL path from the log stream
alone.

Trace catalogue (Sprint 2.50)
-----------------------------
    ENTER_METRICS_TOOL          — Tool.run() entered
    METRICS_QUERY               — outbound HTTP query dispatched
    METRICS_RESPONSE            — outbound HTTP query returned (or failed)
    METRICS_NORMALIZED          — canonical MetricsEvidence / ServerHealth prepared
    METRICS_EVIDENCE_CREATED    — evidence object serialised into ToolResult
    EXIT_METRICS_TOOL           — Tool.run() returning

Fields: tool, tenant, case_id, trace_id, endpoint, status. All strings.
Missing values render as "-". Never raises. PII-safe.

Dependency direction
--------------------
    traces.py → stdlib only.
"""
from __future__ import annotations

import logging
from typing import Final

LOGGER = logging.getLogger("metrics_platform.traces")


TRACE_ENTER_METRICS_TOOL:        Final[str] = "ENTER_METRICS_TOOL"
TRACE_METRICS_QUERY:             Final[str] = "METRICS_QUERY"
TRACE_METRICS_RESPONSE:          Final[str] = "METRICS_RESPONSE"
TRACE_METRICS_NORMALIZED:        Final[str] = "METRICS_NORMALIZED"
TRACE_METRICS_EVIDENCE_CREATED:  Final[str] = "METRICS_EVIDENCE_CREATED"
TRACE_EXIT_METRICS_TOOL:         Final[str] = "EXIT_METRICS_TOOL"

ALL_METRICS_TRACES: Final[frozenset[str]] = frozenset({
    TRACE_ENTER_METRICS_TOOL,
    TRACE_METRICS_QUERY,
    TRACE_METRICS_RESPONSE,
    TRACE_METRICS_NORMALIZED,
    TRACE_METRICS_EVIDENCE_CREATED,
    TRACE_EXIT_METRICS_TOOL,
})

_PII_MARKERS: Final[tuple[str, ...]] = (
    "@",             # email
    "password",
    "authorization",
    "bearer ",
    "api_key",
)

_REDACTED: Final[str] = "REDACTED"
_MISSING:  Final[str] = "-"


def emit_metrics_trace(
    tag: str,
    *,
    tool:     str = "",
    tenant:   str = "",
    case_id:  str = "",
    trace_id: str = "",
    endpoint: str = "",
    status:   str = "",
) -> None:
    """
    Emit one of the 6 canonical Sprint 2.50 traces at WARNING level.

    Fields are string-only, redacted for PII markers, truncated over 128 chars,
    rendered `-` when missing. Never raises.
    """
    try:
        if tag not in ALL_METRICS_TRACES:
            LOGGER.warning("metrics_platform.traces.unknown_tag tag=%s", tag)
            return
        fields = {
            "tool":     _sanitize(tool),
            "tenant":   _sanitize(tenant),
            "case_id":  _sanitize(case_id),
            "trace_id": _sanitize(trace_id),
            "endpoint": _sanitize(endpoint),
            "status":   _sanitize(status),
        }
        LOGGER.warning(
            "%s tool=%s tenant=%s case_id=%s trace_id=%s endpoint=%s status=%s",
            tag,
            fields["tool"], fields["tenant"], fields["case_id"],
            fields["trace_id"], fields["endpoint"], fields["status"],
        )
    except Exception as exc:
        LOGGER.warning("metrics_platform.traces.emit_error tag=%s error=%s", tag, exc)


def _sanitize(value: object) -> str:
    if value is None:
        return _MISSING
    s = str(value).strip()
    if not s:
        return _MISSING
    low = s.lower()
    for marker in _PII_MARKERS:
        if marker in low:
            return _REDACTED
    if len(s) > 128:
        return _REDACTED
    return s
