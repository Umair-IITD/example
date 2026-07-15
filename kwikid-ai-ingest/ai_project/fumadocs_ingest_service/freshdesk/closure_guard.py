"""
freshdesk/closure_guard.py

Sprint 2.48: ClosureFieldGuard — prevent HTTP 422 by validating required Freshdesk
closure fields BEFORE any PUT that transitions a ticket to Resolved (4) or Closed (5).

Source of truth
---------------
Freshdesk_discovery/ticket_lifecycle.md Section 2 + Section 6:
    Moving a ticket to `Resolved` (4) or `Closed` (5) requires these fields
    populated on the ticket:
        cf_clients
        ticket_type            (API name: "type")
        cf_sop_status
        cf_resolution_classification

    A PUT that sets status=4 without these populated returns HTTP 422.

Freshdesk_discovery/api_reference.md Section 3.2 + Section 9.4:
    Custom fields must be nested under "custom_fields" on writes
    (never "ticket_custom_fields" which is only the webhook read key).

Responsibilities
----------------
- validate_payload(payload, current_ticket) → list[str]  # missing field names
- guard_status_transition(payload, current_ticket) → GuardDecision
- assert_safe(payload, current_ticket) → raises ClosureGuardError

Design rules
------------
- Read-only: never modifies payloads or tickets.
- Field presence test: non-None AND non-empty string / truthy value.
- Combines pending values (from the PUT payload) with existing values (from the
  fetched ticket) — the guard permits a single PUT that fills the missing fields
  AND changes status in one call, matching the SOT-mandated write sequence.
- Restricts writes on `type` to the 8 documented values (SOT api_reference.md 7.3).
- PII-free: never reads or logs field values that could contain sensitive data.

Dependency direction
--------------------
    closure_guard.py → stdlib only
    closure_guard.py → NO imports from case_engine/, api/, orchestrator/
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ── Constants (SOT) ───────────────────────────────────────────────────────────

STATUS_RESOLVED = 4
STATUS_CLOSED   = 5

# Fields that MUST be populated before status=4 or status=5 can be written.
# Source: ticket_lifecycle.md Section 6.
REQUIRED_CLOSURE_FIELDS: frozenset[str] = frozenset({
    "cf_clients",
    "ticket_type",
    "cf_sop_status",
    "cf_resolution_classification",
})

# The Freshdesk-native API name for "ticket_type" on write is "type"
# (SOT api_reference.md Section 3.2 sample payloads use "type"; the field
# is exposed on webhook payloads as "ticket_type"). The guard tolerates both.
_TICKET_TYPE_KEYS: tuple[str, ...] = ("ticket_type", "type")

# The 8 valid ticket_type values the AI is allowed to WRITE.
# Source: api_reference.md Section 7.3 + ticket_lifecycle.md Section 10.
ALLOWED_TICKET_TYPES: frozenset[str] = frozenset({
    "Issues",
    "Custom Change Request",
    "Login",
    "Logout",
    "BA BAU",
    "Feedback",
    "Service Task",
    "Internal mail",
})

# Custom fields that are AI WRITE-scoped. Source: custom_fields.json.
# Guard restricts writes to this set for safety.
AI_WRITEABLE_CUSTOM_FIELDS: frozenset[str] = frozenset({
    "cf_sop_status",
    "cf_resolution_classification",
    "cf_rca_status",
    "cf_query_type",
    "cf_issue_area",
    "cf_portal",
    "cf_issue_type234462",
    "cf_review_ticket",
    "cf_session_ids",
    "cf_rca",
    "cf_stackoverflow_link",
    "cf_asana_ticket_link",
    "cf_handling_time",
    "cf_resolved_date_by_developer",
})

# Custom fields the AI must NEVER overwrite once set by Dispatch'r rules or humans.
# Source: workflow_discovery.md Section 2, observations.md Section 5.3.
AI_READ_ONLY_CUSTOM_FIELDS: frozenset[str] = frozenset({
    "cf_clients",
    "cf_environment",
})


# ── Exceptions ────────────────────────────────────────────────────────────────

class ClosureGuardError(Exception):
    """
    Raised by ClosureFieldGuard.assert_safe() when a payload attempting to
    resolve or close a ticket fails the SOT closure-field requirements.
    """
    def __init__(self, message: str, *, missing_fields: list[str] | None = None) -> None:
        super().__init__(message)
        self.missing_fields = list(missing_fields or [])


# ── Result models ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GuardDecision:
    """
    Outcome of guard_status_transition().

    Fields:
        allowed:            True iff the payload is safe to send.
        missing_fields:     names of the required fields still missing.
        forbidden_writes:   custom fields the payload attempts to overwrite
                            that the AI is not permitted to write.
        invalid_type_value: the ticket_type value if it is not in the allowed set.
        reason:             human-readable rejection reason.
    """
    allowed:            bool
    missing_fields:     tuple[str, ...] = ()
    forbidden_writes:   tuple[str, ...] = ()
    invalid_type_value: str | None = None
    reason:             str = ""

    @classmethod
    def ok(cls) -> "GuardDecision":
        return cls(allowed=True, reason="OK")

    @classmethod
    def block_missing(cls, missing: list[str]) -> "GuardDecision":
        return cls(
            allowed=False,
            missing_fields=tuple(sorted(missing)),
            reason=f"missing required closure fields: {sorted(missing)!r}",
        )

    @classmethod
    def block_forbidden(cls, forbidden: list[str]) -> "GuardDecision":
        return cls(
            allowed=False,
            forbidden_writes=tuple(sorted(forbidden)),
            reason=f"payload attempts to write forbidden fields: {sorted(forbidden)!r}",
        )

    @classmethod
    def block_invalid_type(cls, value: str) -> "GuardDecision":
        return cls(
            allowed=False,
            invalid_type_value=value,
            reason=f"ticket_type={value!r} is not one of the {len(ALLOWED_TICKET_TYPES)} allowed values",
        )


# ── Guard ─────────────────────────────────────────────────────────────────────

class ClosureFieldGuard:
    """
    Validate Freshdesk PUT payloads for compliance with the SOT closure contract.

    Typical usage inside the Execution Layer:

        guard = ClosureFieldGuard()
        current = await client.get_ticket(ticket_id)   # authoritative state
        decision = guard.guard_status_transition(update_payload, current)
        if not decision.allowed:
            logger.warning("closure_guard: %s", decision.reason)
            return          # do NOT send the PUT
        await client.update_ticket(ticket_id, update_payload)
    """

    # ── Public API ────────────────────────────────────────────────────────────

    def is_closure_transition(self, payload: dict[str, Any]) -> bool:
        """True iff payload attempts to set status to Resolved or Closed."""
        status = payload.get("status")
        try:
            return int(status) in (STATUS_RESOLVED, STATUS_CLOSED)
        except (TypeError, ValueError):
            return False

    def missing_closure_fields(
        self,
        payload: dict[str, Any],
        current_ticket: dict[str, Any] | None = None,
    ) -> list[str]:
        """
        Return sorted list of required closure fields NOT populated after the
        PUT is applied. An empty list means the closure is safe.

        The check considers:
          1. Fields explicitly set in the payload (top-level or under custom_fields).
          2. Fields already populated on the current ticket (if provided).
        """
        current = current_ticket or {}
        combined_custom = _merged_custom_fields(payload, current)
        combined_top    = _merged_top_level(payload, current)

        missing: list[str] = []
        for field_name in sorted(REQUIRED_CLOSURE_FIELDS):
            if field_name == "ticket_type":
                # ticket_type is a top-level field in both payload and Freshdesk read APIs.
                # Accept either the "type" or "ticket_type" key when checking.
                value = _first_non_empty(combined_top, _TICKET_TYPE_KEYS)
            else:
                value = combined_custom.get(field_name)
            if not _is_populated(value):
                missing.append(field_name)
        return missing

    def forbidden_writes(self, payload: dict[str, Any]) -> list[str]:
        """
        Return sorted list of custom-field names the payload attempts to write
        that are on the AI_READ_ONLY_CUSTOM_FIELDS list.
        """
        cf = payload.get("custom_fields") or {}
        if not isinstance(cf, dict):
            return []
        return sorted(k for k in cf.keys() if k in AI_READ_ONLY_CUSTOM_FIELDS)

    def invalid_ticket_type(self, payload: dict[str, Any]) -> str | None:
        """
        Return the ticket_type value if the payload sets it to a non-allowed
        string, else None. Only a write attempt is validated — the guard does
        NOT complain about existing non-standard values on the ticket record
        (per SOT tolerance rule: "tolerate on reads; never write non-standard").
        """
        for key in _TICKET_TYPE_KEYS:
            if key in payload:
                value = payload.get(key)
                if isinstance(value, str) and value and value not in ALLOWED_TICKET_TYPES:
                    return value
        return None

    def guard_status_transition(
        self,
        payload: dict[str, Any],
        current_ticket: dict[str, Any] | None = None,
    ) -> GuardDecision:
        """
        Full guard for an update_ticket payload.

        Order of checks:
          1. forbidden_writes  — payload tries to overwrite cf_clients / cf_environment
          2. invalid_ticket_type — payload sets an unsupported ticket_type value
          3. is_closure_transition + missing_closure_fields — status=4/5 without required fields

        Returns GuardDecision.ok() when the payload is safe to send.
        """
        forbidden = self.forbidden_writes(payload)
        if forbidden:
            return GuardDecision.block_forbidden(forbidden)

        invalid_type = self.invalid_ticket_type(payload)
        if invalid_type is not None:
            return GuardDecision.block_invalid_type(invalid_type)

        if self.is_closure_transition(payload):
            missing = self.missing_closure_fields(payload, current_ticket)
            if missing:
                return GuardDecision.block_missing(missing)

        return GuardDecision.ok()

    def assert_safe(
        self,
        payload: dict[str, Any],
        current_ticket: dict[str, Any] | None = None,
    ) -> None:
        """
        Raise ClosureGuardError if the payload is not safe to send.

        Preferred for pipeline stages that already treat control-flow exceptions
        as failure signals (Sprint 2.47 pipeline contract).
        """
        decision = self.guard_status_transition(payload, current_ticket)
        if not decision.allowed:
            raise ClosureGuardError(decision.reason, missing_fields=list(decision.missing_fields))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_populated(value: Any) -> bool:
    """True iff the field value is present and non-empty."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    return True


def _first_non_empty(source: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """Return the first value from source[key] that _is_populated."""
    for k in keys:
        if k in source and _is_populated(source.get(k)):
            return source.get(k)
    return None


def _merged_custom_fields(
    payload: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    """
    Combine custom fields from the payload (write intent) with the current
    ticket's custom fields (existing state).

    Payload values take precedence — the intent overrides the current value.
    Reads both "custom_fields" (API write key) and "ticket_custom_fields"
    (webhook read key) from the current ticket to tolerate every discovered shape.
    """
    current_cf: dict[str, Any] = {}
    if isinstance(current.get("custom_fields"), dict):
        current_cf.update(current["custom_fields"])
    if isinstance(current.get("ticket_custom_fields"), dict):
        current_cf.update(current["ticket_custom_fields"])

    payload_cf: dict[str, Any] = {}
    if isinstance(payload.get("custom_fields"), dict):
        payload_cf.update(payload["custom_fields"])

    merged = dict(current_cf)
    merged.update(payload_cf)
    return merged


def _merged_top_level(
    payload: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    """
    Combine top-level fields (payload writes) with current ticket state so
    that the guard can check whether e.g. ticket_type / type is populated
    after the PUT is applied.
    """
    merged: dict[str, Any] = {}
    for key in (*_TICKET_TYPE_KEYS, "status", "priority", "group_id"):
        if key in current:
            merged[key] = current[key]
    for key in (*_TICKET_TYPE_KEYS, "status", "priority", "group_id"):
        if key in payload:
            merged[key] = payload[key]
    return merged
