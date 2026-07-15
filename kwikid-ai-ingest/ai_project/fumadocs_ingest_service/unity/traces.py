"""
unity/traces.py

Sprint 2.51: Deterministic runtime traces for Unity Admin Portal boundary.

Ten canonical tags (exactly as specified in the sprint prompt):

    ENTER_UNITY_TOKEN
    EXIT_UNITY_TOKEN
    ENTER_UNITY_LOOKUP
    EXIT_UNITY_LOOKUP
    ENTER_UNITY_DETAILS
    EXIT_UNITY_DETAILS
    ENTER_EVIDENCE_MAPPING
    EXIT_EVIDENCE_MAPPING
    UNITY_API_FAILURE
    UNITY_AUTH_FAILURE

Every trace emits at WARNING level via the `unity.traces` logger with a
fixed 6-KV layout:

    TAG tool=<t> tenant=<T> case_id=<c> trace_id=<t> endpoint=<e> status=<s>

Fields containing PII markers (`@`, `password`, `authorization`, `bearer `,
`Token`, `Aadhaar`, `PAN`) or values >128 chars are replaced with the
literal `REDACTED`. Long JWTs never reach the trace layer.

Dependency direction: traces.py → stdlib only.
"""
from __future__ import annotations

import logging
from typing import Final

LOGGER = logging.getLogger("unity.traces")


TRACE_ENTER_UNITY_TOKEN:      Final[str] = "ENTER_UNITY_TOKEN"
TRACE_EXIT_UNITY_TOKEN:       Final[str] = "EXIT_UNITY_TOKEN"
TRACE_ENTER_UNITY_LOOKUP:     Final[str] = "ENTER_UNITY_LOOKUP"
TRACE_EXIT_UNITY_LOOKUP:      Final[str] = "EXIT_UNITY_LOOKUP"
TRACE_ENTER_UNITY_DETAILS:    Final[str] = "ENTER_UNITY_DETAILS"
TRACE_EXIT_UNITY_DETAILS:     Final[str] = "EXIT_UNITY_DETAILS"
TRACE_ENTER_EVIDENCE_MAPPING: Final[str] = "ENTER_EVIDENCE_MAPPING"
TRACE_EXIT_EVIDENCE_MAPPING:  Final[str] = "EXIT_EVIDENCE_MAPPING"
TRACE_UNITY_API_FAILURE:      Final[str] = "UNITY_API_FAILURE"
TRACE_UNITY_AUTH_FAILURE:     Final[str] = "UNITY_AUTH_FAILURE"

ALL_UNITY_TRACES: Final[frozenset[str]] = frozenset({
    TRACE_ENTER_UNITY_TOKEN,
    TRACE_EXIT_UNITY_TOKEN,
    TRACE_ENTER_UNITY_LOOKUP,
    TRACE_EXIT_UNITY_LOOKUP,
    TRACE_ENTER_UNITY_DETAILS,
    TRACE_EXIT_UNITY_DETAILS,
    TRACE_ENTER_EVIDENCE_MAPPING,
    TRACE_EXIT_EVIDENCE_MAPPING,
    TRACE_UNITY_API_FAILURE,
    TRACE_UNITY_AUTH_FAILURE,
})

_PII_MARKERS: Final[tuple[str, ...]] = (
    "@",
    "password",
    "authorization",
    "bearer ",
    "aadhaar",
    "pan",
    "eyj",           # JWT header prefix
)

_REDACTED: Final[str] = "REDACTED"
_MISSING:  Final[str] = "-"


def emit_unity_trace(
    tag: str,
    *,
    tool:     str = "",
    tenant:   str = "",
    case_id:  str = "",
    trace_id: str = "",
    endpoint: str = "",
    status:   str = "",
) -> None:
    """Emit one of the 10 canonical Sprint 2.51 traces at WARNING level."""
    try:
        if tag not in ALL_UNITY_TRACES:
            LOGGER.warning("unity.traces.unknown_tag tag=%s", tag)
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
        LOGGER.warning("unity.traces.emit_error tag=%s error=%s", tag, exc)


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
