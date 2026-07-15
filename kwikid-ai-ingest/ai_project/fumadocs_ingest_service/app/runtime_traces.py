"""
app/runtime_traces.py

Sprint 2.52: High-level ENTER_ / EXIT_ / RETURN_ / EXCEPTION_ runtime traces
for the top-level components (Webhook, Tool Executor, Unity, Metrics,
Investigation, Evidence Bundle).

These are DISTINCT from and COMPLEMENTARY to the per-integration trace
layers:
  - `freshdesk/traces.py`     — 10 TRACE_FD_* tags (Sprint 2.49)
  - `metrics_platform/traces.py` — 6 METRICS_* tags (Sprint 2.50)
  - `unity/traces.py`         — 10 UNITY_* tags (Sprint 2.51)

The Sprint 2.52 tags emit at the top-level pipeline stages so an operator
can visually confirm the pipeline reached each boundary without needing
to grep for per-integration tags.

Emission contract (identical to prior trace layers):
  - WARNING level via the `app.runtime_traces` logger.
  - Fixed field layout: component / stage / case_id / trace_id / status.
  - Missing values render as "-".
  - PII sanitizer redacts values containing `@`, `password`, `bearer `,
    `authorization`, `api_key`, `token`, and any value >128 chars.
  - Never raises — telemetry cannot break the pipeline.

Dependency direction
--------------------
    runtime_traces.py → stdlib only.
"""
from __future__ import annotations

import logging
from typing import Final

LOGGER = logging.getLogger("app.runtime_traces")


# ── Canonical trace tags ─────────────────────────────────────────────────────

# Webhook boundary (top of the pipeline)
TRACE_ENTER_WEBHOOK:            Final[str] = "ENTER_WEBHOOK"
TRACE_EXIT_WEBHOOK:             Final[str] = "EXIT_WEBHOOK"

# Tool Executor (Sprint 2.17 dispatcher)
TRACE_ENTER_TOOL_EXECUTOR:      Final[str] = "ENTER_TOOL_EXECUTOR"
TRACE_EXIT_TOOL_EXECUTOR:       Final[str] = "EXIT_TOOL_EXECUTOR"

# Unity integration (top-level boundary; per-request Unity traces still fire)
TRACE_ENTER_UNITY:              Final[str] = "ENTER_UNITY"
TRACE_EXIT_UNITY:               Final[str] = "EXIT_UNITY"

# Metrics platform (top-level boundary)
TRACE_ENTER_METRICS:            Final[str] = "ENTER_METRICS"
TRACE_EXIT_METRICS:             Final[str] = "EXIT_METRICS"

# Investigation layer
TRACE_ENTER_INVESTIGATION:      Final[str] = "ENTER_INVESTIGATION"
TRACE_EXIT_INVESTIGATION:       Final[str] = "EXIT_INVESTIGATION"

# Evidence Bundle — final canonical stop point before the LLM.
TRACE_ENTER_EVIDENCE_BUNDLE:    Final[str] = "ENTER_EVIDENCE_BUNDLE"
TRACE_EXIT_EVIDENCE_BUNDLE:     Final[str] = "EXIT_EVIDENCE_BUNDLE"

# Sprint 2.52 marker — the runtime deterministically stops here.
# Wave 3 (Action System / LLM) begins immediately after this trace.
TRACE_RUNTIME_STOP_LLM_BOUNDARY: Final[str] = "RUNTIME_STOP_LLM_BOUNDARY"

ALL_RUNTIME_TRACES: Final[frozenset[str]] = frozenset({
    TRACE_ENTER_WEBHOOK,
    TRACE_EXIT_WEBHOOK,
    TRACE_ENTER_TOOL_EXECUTOR,
    TRACE_EXIT_TOOL_EXECUTOR,
    TRACE_ENTER_UNITY,
    TRACE_EXIT_UNITY,
    TRACE_ENTER_METRICS,
    TRACE_EXIT_METRICS,
    TRACE_ENTER_INVESTIGATION,
    TRACE_EXIT_INVESTIGATION,
    TRACE_ENTER_EVIDENCE_BUNDLE,
    TRACE_EXIT_EVIDENCE_BUNDLE,
    TRACE_RUNTIME_STOP_LLM_BOUNDARY,
})


_PII_MARKERS: Final[tuple[str, ...]] = (
    "@",
    "password",
    "authorization",
    "bearer ",
    "api_key",
    "token=",
)

_REDACTED: Final[str] = "REDACTED"
_MISSING:  Final[str] = "-"


def emit_runtime_trace(
    tag: str,
    *,
    component: str = "",
    stage:     str = "",
    case_id:   str = "",
    trace_id:  str = "",
    status:    str = "",
) -> None:
    """
    Emit one of the 13 canonical Sprint 2.52 runtime traces at WARNING level.
    """
    try:
        if tag not in ALL_RUNTIME_TRACES:
            LOGGER.warning("app.runtime_traces.unknown_tag tag=%s", tag)
            return
        fields = {
            "component": _sanitize(component),
            "stage":     _sanitize(stage),
            "case_id":   _sanitize(case_id),
            "trace_id":  _sanitize(trace_id),
            "status":    _sanitize(status),
        }
        LOGGER.warning(
            "%s component=%s stage=%s case_id=%s trace_id=%s status=%s",
            tag,
            fields["component"], fields["stage"], fields["case_id"],
            fields["trace_id"], fields["status"],
        )
    except Exception as exc:
        LOGGER.warning("app.runtime_traces.emit_error tag=%s error=%s", tag, exc)


def emit_exception_trace(component: str, *, case_id: str = "", trace_id: str = "",
                         reason: str = "") -> None:
    """
    Emit an EXCEPTION_<component> line. `component` is embedded in the tag
    itself. `reason` is the exception message (auto-truncated to 128 chars
    and PII-sanitized by the trace helper).
    """
    tag = f"EXCEPTION_{component.upper()}"
    try:
        LOGGER.warning(
            "%s component=%s case_id=%s trace_id=%s status=%s",
            tag,
            _sanitize(component),
            _sanitize(case_id),
            _sanitize(trace_id),
            _sanitize(reason),
        )
    except Exception:
        # never raise
        pass


def emit_return_trace(reason: str, *, component: str = "",
                      case_id: str = "", trace_id: str = "") -> None:
    """
    Emit a RETURN_<reason> line marking why the pipeline short-circuited
    (e.g. DUPLICATE, UNKNOWN_CLIENT, ALL_UP, NO_EVIDENCE).
    """
    tag = f"RETURN_{reason.upper()}"
    try:
        LOGGER.warning(
            "%s component=%s case_id=%s trace_id=%s",
            tag,
            _sanitize(component), _sanitize(case_id), _sanitize(trace_id),
        )
    except Exception:
        pass


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
