"""
case_engine/tools/tool_models.py

Sprint 2.17: Tool domain models.
Sprint 2.38: Added ToolProvider and ToolCapability for architecture reconciliation
             (Part H — Tool architecture reconciliation per flow_diagram.mermaid).

Design:
- ToolDefinition: static schema for a tool (registered once at startup).
- ToolInput: validated inputs for a single invocation.
- ToolResult: result of a single tool invocation (success or failure).
- ToolProvider: which external system the tool contacts.
- ToolCapability: what the tool does in that system.

No LLM coupling. No KwikID-specific fields.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Provider and capability declarations ───────────────────────────────────────

class ToolProvider(str, Enum):
    """
    External system a tool contacts when invoked.

    Per flow_diagram.mermaid Tools subgraph: Freshdesk, Unity, Admin Portal,
    S3, Database, Flagsmith, MinIO, Vision, MCP.
    """
    FRESHDESK        = "FRESHDESK"        # Freshdesk CRM / ticketing
    UNITY            = "UNITY"            # Unity ID verification platform
    ADMIN_PORTAL     = "ADMIN_PORTAL"     # KwikID admin portal API
    S3               = "S3"               # AWS S3 object storage
    DATABASE         = "DATABASE"         # Direct database access (Supabase/Postgres)
    FLAGSMITH        = "FLAGSMITH"        # Flagsmith feature flags
    MINIO            = "MINIO"            # MinIO object storage
    VISION           = "VISION"           # Computer vision analysis provider
    MCP              = "MCP"              # MCP tool server
    METRICS_PLATFORM = "METRICS_PLATFORM" # Sprint 2.50 — Uptime Kuma monitoring dashboard


class ToolCapability(str, Enum):
    """What a tool does in its target provider system."""
    READ     = "READ"     # Fetch / retrieve data
    WRITE    = "WRITE"    # Create or update data
    EXECUTE  = "EXECUTE"  # Trigger an action (reset, resend, etc.)
    QUERY    = "QUERY"    # Search or filter data
    NOTIFY   = "NOTIFY"   # Send a notification
    ANALYZE  = "ANALYZE"  # Analyse data (vision, metrics)


@dataclass(frozen=True)
class ToolDefinition:
    """
    Static description of an investigation tool.

    Registered in ToolRegistry at startup. Immutable after construction.

    tool_name       : unique identifier (e.g., "GetSessionDetailsTool")
    description     : human-readable explanation of what the tool does
    required_inputs : names of input keys that must be present to invoke
    output_schema   : dict mapping output key names to their expected type description
    version         : semver string (e.g., "1.0")
    tags            : optional grouping labels (e.g., ["vkyc", "session"])
    """
    tool_name:       str
    description:     str
    required_inputs: tuple[str, ...]
    output_schema:   dict[str, str]       # key → type description
    version:         str                  = "1.0"
    tags:            tuple[str, ...]      = field(default_factory=tuple)
    provider:        ToolProvider | None  = None
    capability:      ToolCapability | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name":       self.tool_name,
            "description":     self.description,
            "required_inputs": list(self.required_inputs),
            "output_schema":   self.output_schema,
            "version":         self.version,
            "tags":            list(self.tags),
            "provider":        self.provider.value if self.provider else None,
            "capability":      self.capability.value if self.capability else None,
        }


@dataclass
class ToolInput:
    """
    Validated input for a single tool invocation.

    invocation_id : unique ID for this invocation (used for deduplication / audit)
    tool_name     : which tool to invoke
    inputs        : key-value inputs matched against ToolDefinition.required_inputs
    requested_by  : identifier of the caller (e.g., workflow step ID, reasoning engine)
    """
    tool_name:    str
    inputs:       dict[str, Any] = field(default_factory=dict)
    invocation_id: str = field(default_factory=_new_id)
    requested_by:  str = "system"
    requested_at:  str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "invocation_id": self.invocation_id,
            "tool_name":     self.tool_name,
            "inputs":        self.inputs,
            "requested_by":  self.requested_by,
            "requested_at":  self.requested_at,
        }


@dataclass
class ToolResult:
    """
    Result of a single tool invocation.

    success       : True if the tool ran without error
    payload       : dict of output key-value pairs (matches output_schema)
    error_code    : short error identifier (if success=False)
    error_message : human-readable error explanation (if success=False)
    tool_name     : which tool produced this result
    invocation_id : pairs with ToolInput.invocation_id for tracing
    executed_at   : ISO timestamp of when the tool completed
    duration_ms   : wall-clock duration of tool execution
    """
    success:       bool
    tool_name:     str = ""
    invocation_id: str = field(default_factory=_new_id)
    payload:       dict[str, Any] = field(default_factory=dict)
    error_code:    str | None = None
    error_message: str | None = None
    executed_at:   str = field(default_factory=_now_iso)
    duration_ms:   int = 0

    @classmethod
    def ok(
        cls,
        tool_name: str,
        payload: dict[str, Any],
        invocation_id: str = "",
        duration_ms: int = 0,
    ) -> "ToolResult":
        return cls(
            success=True,
            tool_name=tool_name,
            payload=payload,
            invocation_id=invocation_id or _new_id(),
            duration_ms=duration_ms,
        )

    @classmethod
    def fail(
        cls,
        tool_name: str,
        error_code: str,
        error_message: str,
        invocation_id: str = "",
    ) -> "ToolResult":
        return cls(
            success=False,
            tool_name=tool_name,
            error_code=error_code,
            error_message=error_message,
            invocation_id=invocation_id or _new_id(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "success":       self.success,
            "tool_name":     self.tool_name,
            "invocation_id": self.invocation_id,
            "payload":       self.payload,
            "error_code":    self.error_code,
            "error_message": self.error_message,
            "executed_at":   self.executed_at,
            "duration_ms":   self.duration_ms,
        }
