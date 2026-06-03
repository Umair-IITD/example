"""
case_engine/audit.py

AuditLogger: write-through audit log for all case events.

Design:
- Every call attempts a synchronous DB write.
- On failure: error is logged, exception is NOT re-raised (no silent data loss
  but also no crash of the primary request path).
- If supabase_client is None, falls back to log-only mode (useful for tests
  and offline environments).
- Case events → case_audit_log (append-only, no UPDATE/DELETE).
- Security events → security_compliance_audit (RLS-protected, SOC-only access).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from case_engine.case_state import CaseState
from case_engine.models import AuditEntry, AuditEventType, Case, CaseTransition, MatchType

LOGGER = logging.getLogger(__name__)

_AUDIT_TABLE    = "case_audit_log"
_SECURITY_TABLE = "security_compliance_audit"


class AuditLogger:
    """
    Writes case events to case_audit_log.

    supabase_client: a supabase-py Client instance, or None for log-only mode.
    """

    def __init__(self, supabase_client: Any = None) -> None:
        self._sb = supabase_client

    # ── Public logging methods ─────────────────────────────────────────────────

    def log_transition(
        self,
        case: Case,
        transition: CaseTransition,
    ) -> None:
        """Log a state transition event."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            actor=transition.actor,
            action_type=AuditEventType.STATE_TRANSITION,
            action_detail={
                "from_state": transition.from_state.value,
                "to_state":   transition.to_state.value,
                "reason":     transition.reason,
            },
            outcome="SUCCESS",
        )
        self._write(entry)

    def log_classification(
        self,
        case: Case,
        topic: str,
        confidence: float,
        tier_used: int,
        meets_threshold: bool,
    ) -> None:
        """Log a topic classification result."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.CLASSIFICATION,
            action_detail={
                "topic":          topic,
                "confidence":     confidence,
                "tier_used":      tier_used,
                "meets_threshold": meets_threshold,
            },
            outcome="PASS" if meets_threshold else "BELOW_THRESHOLD",
        )
        self._write(entry)

    def log_rag_call(
        self,
        case: Case,
        match_type: str,
        confidence: str,
        chunks_count: int,
        requires_human: bool,
        cited_sop_ids: list[str] | None = None,
    ) -> None:
        """Log a RAG retrieval event."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.RAG_CALLED,
            action_detail={
                "match_type":     match_type,
                "confidence":     confidence,
                "chunks_count":   chunks_count,
                "requires_human": requires_human,
                "cited_sop_ids":  cited_sop_ids or [],
            },
            outcome="SUCCESS",
        )
        self._write(entry)

    def log_note_posted(
        self,
        case: Case,
        note_type: str,
        confidence: str,
    ) -> None:
        """Log a Freshdesk note posting event."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.NOTE_POSTED,
            action_detail={
                "note_type":  note_type,
                "confidence": confidence,
            },
            outcome="SUCCESS",
        )
        self._write(entry)

    def log_escalation(
        self,
        case: Case,
        trigger: str,
        reason: str,
        priority: str,
    ) -> None:
        """Log an escalation event."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.ESCALATION_TRIGGERED,
            action_detail={
                "trigger":  trigger,
                "reason":   reason,
                "priority": priority,
            },
            outcome="ESCALATED",
        )
        self._write(entry)

    def log_error(
        self,
        case: Case,
        error_type: str,
        error_msg: str,
        *,
        actor: str = "system",
    ) -> None:
        """Log a system error event. Never raises."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            actor=actor,
            action_type=AuditEventType.ERROR,
            action_detail={"error_type": error_type, "message": error_msg},
            outcome="FAILURE",
            error_code=error_type,
        )
        self._write(entry)

    # ── Security compliance events ─────────────────────────────────────────────

    def log_security_event(
        self,
        case: Case,
        *,
        security_event_type: str,
        event_details: dict[str, Any],
        severity: str = "HIGH",
    ) -> None:
        """Log a security event to security_compliance_audit (not case_audit_log).

        Security events (DEEPFAKE_SUSPECTED, FOREIGN_IP, AADHAAR_MISMATCH, etc.)
        are SOC-routed and must NOT appear in case_audit_log.
        """
        LOGGER.warning(
            "security_event type=%s case=%s ticket=%s severity=%s",
            security_event_type, case.case_id, case.ticket_id, severity,
        )
        if self._sb is None:
            return

        row: dict[str, Any] = {
            "audit_id":            str(uuid.uuid4()),
            "case_id":             case.case_id,
            "ticket_id":           case.ticket_id,
            "client":              case.client,
            "security_event_type": security_event_type,
            "event_details":       event_details,
            "severity":            severity,
            "detected_at":         datetime.now(tz=timezone.utc).isoformat(),
            "reported_to_soc":     False,
        }
        self._write_security(row)

    # ── Sprint 2 audit API stubs ───────────────────────────────────────────────
    # These methods record Level 2 Action Gateway events. They write to
    # case_audit_log so the event trail is complete when Level 2 is enabled.
    # They are placeholders only — not wired to any runtime logic in Sprint 1.

    def log_action_proposed(
        self,
        case: Case,
        action_name: str,
        action_detail: dict[str, Any],
        idempotency_key: str,
    ) -> None:
        """Sprint 2 stub: log an action proposal from the Action Gateway."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.ACTION_PROPOSED,
            action_detail={"action_name": action_name, **action_detail},
            outcome="PROPOSED",
            idempotency_key=idempotency_key,
        )
        self._write(entry)

    def log_action_executed(
        self,
        case: Case,
        action_name: str,
        outcome: str,
        idempotency_key: str,
        error_code: str | None = None,
    ) -> None:
        """Sprint 2 stub: log a successfully executed action."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.ACTION_EXECUTED,
            action_detail={"action_name": action_name, "outcome": outcome},
            outcome=outcome,
            error_code=error_code,
            idempotency_key=idempotency_key,
        )
        self._write(entry)

    def log_action_rejected(
        self,
        case: Case,
        action_name: str,
        reason: str,
        idempotency_key: str,
    ) -> None:
        """Sprint 2 stub: log a rejected action (human sign-off required)."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.ACTION_REJECTED,
            action_detail={"action_name": action_name, "reason": reason},
            outcome="REJECTED",
            idempotency_key=idempotency_key,
        )
        self._write(entry)

    def log_slot_filled(
        self,
        case: Case,
        slot_name: str,
        value_hash: str,
        turn_count: int,
    ) -> None:
        """Sprint 2 stub: log a slot fill from the conversational state machine."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.SLOT_FILLED,
            action_detail={
                "slot_name":  slot_name,
                "value_hash": value_hash,
                "turn_count": turn_count,
            },
            outcome="FILLED",
        )
        self._write(entry)

    # ── Internal write ─────────────────────────────────────────────────────────

    def _write(self, entry: AuditEntry) -> None:
        """Write an audit entry to case_audit_log. Never raises."""
        LOGGER.debug(
            "audit event=%s case=%s ticket=%s outcome=%s",
            entry.action_type.value, entry.case_id, entry.ticket_id, entry.outcome,
        )

        if self._sb is None:
            return  # log-only mode

        try:
            self._sb.table(_AUDIT_TABLE).insert(entry.to_db_row()).execute()
        except Exception as exc:
            # Log but do not re-raise — audit failure must not crash the request path
            LOGGER.error(
                "audit_write_failed event=%s case_id=%s error=%s",
                entry.action_type.value, entry.case_id, exc,
            )

    def _write_security(self, row: dict[str, Any]) -> None:
        """Write a row to security_compliance_audit. Never raises."""
        if self._sb is None:
            return

        try:
            self._sb.table(_SECURITY_TABLE).insert(row).execute()
        except Exception as exc:
            LOGGER.error(
                "security_audit_write_failed type=%s case_id=%s error=%s",
                row.get("security_event_type"), row.get("case_id"), exc,
            )
