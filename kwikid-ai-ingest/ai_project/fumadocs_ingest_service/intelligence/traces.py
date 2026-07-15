"""
intelligence/traces.py

Wave 3: 22 canonical `TRACE_NN_<STAGE>` traces that make the entire
Freshdesk → EvidenceBundle → LLM → Reply pipeline visible from the log
stream alone.

These trace tags are the SINGLE grep-target an operator uses to verify a
production run. They complement (not replace) the per-integration trace
layers:
  - Sprint 2.49 `TRACE_FD_01..10` — Freshdesk-boundary traces
  - Sprint 2.50 `ENTER_METRICS_TOOL` etc. — Metrics adapter traces
  - Sprint 2.51 `ENTER_UNITY_TOKEN` etc. — Unity adapter traces
  - Sprint 2.52 `ENTER_WEBHOOK / TOOL_EXECUTOR / ...` — pipeline-boundary traces

Emission contract
-----------------
- WARNING level via the `intelligence.traces` logger.
- Fixed 6-KV layout: `ticket_id / tenant / case_id / stage / duration_ms / status`.
- PII sanitizer redacts values containing `@`, `password`, `authorization`,
  `bearer `, `api_key`, `token=`, `pan`, `aadhaar`, JWT prefix `eyJ`, or
  values >128 chars.
- Never raises — telemetry cannot break the pipeline.
- Duration always emitted as integer milliseconds; use `-` when unknown.

Dependency direction: traces.py → stdlib only.
"""
from __future__ import annotations

import logging
from typing import Final

LOGGER = logging.getLogger("intelligence.traces")


# ── The 22 canonical tags (Wave 3) ──────────────────────────────────────────

TRACE_01_WEBHOOK_RECEIVED:            Final[str] = "TRACE_01_WEBHOOK_RECEIVED"
TRACE_02_PAYLOAD_NORMALIZED:          Final[str] = "TRACE_02_PAYLOAD_NORMALIZED"
TRACE_03_TENANT_RESOLVED:             Final[str] = "TRACE_03_TENANT_RESOLVED"
TRACE_04_CASE_CREATED:                Final[str] = "TRACE_04_CASE_CREATED"
TRACE_05_CLASSIFICATION_STARTED:      Final[str] = "TRACE_05_CLASSIFICATION_STARTED"
TRACE_06_CLASSIFICATION_COMPLETED:    Final[str] = "TRACE_06_CLASSIFICATION_COMPLETED"
TRACE_07_WORKFLOW_SELECTED:           Final[str] = "TRACE_07_WORKFLOW_SELECTED"
TRACE_08_INVESTIGATION_STARTED:       Final[str] = "TRACE_08_INVESTIGATION_STARTED"
TRACE_09_EVIDENCE_COLLECTION_STARTED: Final[str] = "TRACE_09_EVIDENCE_COLLECTION_STARTED"
TRACE_10_UNITY_TOOL_EXECUTED:         Final[str] = "TRACE_10_UNITY_TOOL_EXECUTED"
TRACE_11_METRIC_TOOL_EXECUTED:        Final[str] = "TRACE_11_METRIC_TOOL_EXECUTED"
TRACE_12_EVIDENCE_BUNDLE_READY:       Final[str] = "TRACE_12_EVIDENCE_BUNDLE_READY"
TRACE_13_CONTEXT_BUILDER:             Final[str] = "TRACE_13_CONTEXT_BUILDER"
TRACE_14_PROMPT_BUILDER:              Final[str] = "TRACE_14_PROMPT_BUILDER"
TRACE_15_HYBRID_RAG:                  Final[str] = "TRACE_15_HYBRID_RAG"
TRACE_16_LLM_REQUEST:                 Final[str] = "TRACE_16_LLM_REQUEST"
TRACE_17_LLM_RESPONSE:                Final[str] = "TRACE_17_LLM_RESPONSE"
TRACE_18_REASONING_COMPLETE:          Final[str] = "TRACE_18_REASONING_COMPLETE"
TRACE_19_OBSERVATION_GENERATED:       Final[str] = "TRACE_19_OBSERVATION_GENERATED"
TRACE_20_CUSTOMER_REPLY_GENERATED:    Final[str] = "TRACE_20_CUSTOMER_REPLY_GENERATED"
TRACE_21_ACTION_PROPOSAL:             Final[str] = "TRACE_21_ACTION_PROPOSAL"
TRACE_22_PIPELINE_COMPLETE:           Final[str] = "TRACE_22_PIPELINE_COMPLETE"


ALL_WAVE3_TRACES: Final[frozenset[str]] = frozenset({
    TRACE_01_WEBHOOK_RECEIVED,
    TRACE_02_PAYLOAD_NORMALIZED,
    TRACE_03_TENANT_RESOLVED,
    TRACE_04_CASE_CREATED,
    TRACE_05_CLASSIFICATION_STARTED,
    TRACE_06_CLASSIFICATION_COMPLETED,
    TRACE_07_WORKFLOW_SELECTED,
    TRACE_08_INVESTIGATION_STARTED,
    TRACE_09_EVIDENCE_COLLECTION_STARTED,
    TRACE_10_UNITY_TOOL_EXECUTED,
    TRACE_11_METRIC_TOOL_EXECUTED,
    TRACE_12_EVIDENCE_BUNDLE_READY,
    TRACE_13_CONTEXT_BUILDER,
    TRACE_14_PROMPT_BUILDER,
    TRACE_15_HYBRID_RAG,
    TRACE_16_LLM_REQUEST,
    TRACE_17_LLM_RESPONSE,
    TRACE_18_REASONING_COMPLETE,
    TRACE_19_OBSERVATION_GENERATED,
    TRACE_20_CUSTOMER_REPLY_GENERATED,
    TRACE_21_ACTION_PROPOSAL,
    TRACE_22_PIPELINE_COMPLETE,
})


_PII_MARKERS: Final[tuple[str, ...]] = (
    "@",             # email
    "password",
    "authorization",
    "bearer ",
    "api_key",
    "token=",
    "pan",           # PII per Unity SOT
    "aadhaar",       # PII per Unity SOT
    "eyj",           # JWT prefix
)

_REDACTED: Final[str] = "REDACTED"
_MISSING:  Final[str] = "-"


def emit_wave3_trace(
    tag: str,
    *,
    ticket_id:   str = "",
    tenant:      str = "",
    case_id:     str = "",
    stage:       str = "",
    duration_ms: int | None = None,
    status:      str = "",
) -> None:
    """Emit one of the 22 Wave 3 traces at WARNING level."""
    try:
        if tag not in ALL_WAVE3_TRACES:
            LOGGER.warning("intelligence.traces.unknown_tag tag=%s", tag)
            return
        dur = str(int(duration_ms)) if isinstance(duration_ms, (int, float)) else _MISSING
        fields = {
            "ticket_id":   _sanitize(ticket_id),
            "tenant":      _sanitize(tenant),
            "case_id":     _sanitize(case_id),
            "stage":       _sanitize(stage),
            "duration_ms": dur,
            "status":      _sanitize(status),
        }
        LOGGER.warning(
            "%s ticket_id=%s tenant=%s case_id=%s stage=%s duration_ms=%s status=%s",
            tag,
            fields["ticket_id"], fields["tenant"], fields["case_id"],
            fields["stage"], fields["duration_ms"], fields["status"],
        )
    except Exception as exc:
        LOGGER.warning("intelligence.traces.emit_error tag=%s error=%s", tag, exc)


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
