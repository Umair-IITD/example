"""
case_engine/repository.py

CaseRepository — Supabase persistence for case records.

Design:
- All methods are synchronous (called via asyncio.to_thread in async handlers).
- Graceful failure: on any DB error, log + return None or False. Never raise.
  The Phase 1 RAG pipeline must continue even if case persistence fails.
- Upsert on case creation: avoids duplicate cases for the same ticket on retry.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from case_engine.case_state import CaseState
from case_engine.models import Case, CaseTransition, AuditEntry

LOGGER = logging.getLogger(__name__)

_CASES_TABLE       = "cases"
_TRANSITIONS_TABLE = "case_transitions"
_AUDIT_TABLE       = "case_audit_log"


class CaseRepository:
    """
    Persistence layer for cases, transitions, and audit entries.

    supabase_client: supabase-py Client, or None for in-memory/offline use.
    """

    def __init__(self, supabase_client: Any = None) -> None:
        self._sb = supabase_client

    # ── Case CRUD ──────────────────────────────────────────────────────────────

    def create_case(self, ticket_id: str, client: str) -> Case | None:
        """
        Create a new case record in NEW state.

        Uses upsert on (ticket_id, client) to handle webhook retries gracefully.
        Returns the Case, or None if persistence is unavailable.
        """
        case = Case(ticket_id=ticket_id, client=client)

        if self._sb is None:
            LOGGER.debug("repository: offline — case created in-memory only %s", case.case_id)
            return case

        try:
            row = case.to_db_row()
            result = (
                self._sb
                .table(_CASES_TABLE)
                .upsert(row, on_conflict="ticket_id,client")
                .execute()
            )
            if result.data:
                return Case.from_db_row(result.data[0])
            return case
        except Exception as exc:
            LOGGER.error("repository.create_case failed ticket=%s error=%s", ticket_id, exc)
            return case  # Return in-memory case so Phase 1 can continue

    def get_case(self, case_id: str) -> Case | None:
        if self._sb is None:
            return None
        try:
            result = (
                self._sb
                .table(_CASES_TABLE)
                .select("*")
                .eq("case_id", case_id)
                .limit(1)
                .execute()
            )
            if result.data:
                return Case.from_db_row(result.data[0])
            return None
        except Exception as exc:
            LOGGER.error("repository.get_case failed case_id=%s error=%s", case_id, exc)
            return None

    def get_case_by_ticket(self, ticket_id: str, client: str) -> Case | None:
        if self._sb is None:
            return None
        try:
            result = (
                self._sb
                .table(_CASES_TABLE)
                .select("*")
                .eq("ticket_id", ticket_id)
                .eq("client", client)
                .limit(1)
                .execute()
            )
            if result.data:
                return Case.from_db_row(result.data[0])
            return None
        except Exception as exc:
            LOGGER.error(
                "repository.get_case_by_ticket failed ticket=%s client=%s error=%s",
                ticket_id, client, exc,
            )
            return None

    def update_case_state(
        self,
        case: Case,
        new_state: CaseState,
    ) -> bool:
        """Persist the current in-memory case state to the DB."""
        if self._sb is None:
            return True  # offline — in-memory already updated

        now = datetime.now(tz=timezone.utc).isoformat()
        patch: dict[str, Any] = {
            "current_state": new_state.value,
            "updated_at":    now,
        }
        if case.topic:
            patch["topic"] = case.topic
        if case.confidence is not None:
            patch["confidence"] = case.confidence
        if new_state == CaseState.CLOSED and case.closed_at:
            patch["closed_at"] = case.closed_at.isoformat()
        if case.sla_breach_at is not None:
            patch["sla_breach_at"] = case.sla_breach_at.isoformat()
        if case.slot_state:
            patch["slot_state"] = case.slot_state
        if case.workflow_id is not None:
            patch["workflow_id"] = case.workflow_id
        if case.workflow_state is not None:
            patch["workflow_state"] = case.workflow_state
        if case.workflow_step_index is not None:
            patch["workflow_step_index"] = case.workflow_step_index
        if case.workflow_context:
            patch["workflow_context"] = case.workflow_context

        try:
            self._sb.table(_CASES_TABLE).update(patch).eq("case_id", case.case_id).execute()
            return True
        except Exception as exc:
            LOGGER.error(
                "repository.update_case_state failed case_id=%s state=%s error=%s",
                case.case_id, new_state.value, exc,
            )
            return False

    # ── Transition log ─────────────────────────────────────────────────────────

    def record_transition(self, transition: CaseTransition) -> bool:
        """Append a transition record (insert only, never update)."""
        if self._sb is None:
            return True

        try:
            self._sb.table(_TRANSITIONS_TABLE).insert(transition.to_db_row()).execute()
            return True
        except Exception as exc:
            LOGGER.error(
                "repository.record_transition failed case_id=%s %s→%s error=%s",
                transition.case_id,
                transition.from_state.value,
                transition.to_state.value,
                exc,
            )
            return False

    # ── Workflow listing (Sprint 2.16) ────────────────────────────────────────

    def list_cases_by_workflow_state(self, states: list[str] | None = None) -> list[Case]:
        """
        Return cases that have a non-null workflow_state, optionally filtered by state.

        Returns an empty list if offline or on any DB error.
        """
        if self._sb is None:
            return []

        try:
            query = self._sb.table(_CASES_TABLE).select("*").not_.is_("workflow_state", "null")
            if states:
                query = query.in_("workflow_state", states)
            result = query.order("updated_at", desc=True).limit(200).execute()
            if result.data:
                return [Case.from_db_row(row) for row in result.data]
            return []
        except Exception as exc:
            LOGGER.error("repository.list_cases_by_workflow_state failed error=%s", exc)
            return []

    # ── Audit log ─────────────────────────────────────────────────────────────

    def write_audit_entry(self, entry: AuditEntry) -> bool:
        """Append an audit entry (insert only, never update)."""
        if self._sb is None:
            return True

        try:
            self._sb.table(_AUDIT_TABLE).insert(entry.to_db_row()).execute()
            return True
        except Exception as exc:
            LOGGER.error(
                "repository.write_audit_entry failed case_id=%s event=%s error=%s",
                entry.case_id, entry.action_type.value, exc,
            )
            return False
