"""
case_engine/action_repository.py

ActionRepository — Supabase persistence for action_gateway and
action_gateway_transitions (S2_001_action_gateway.sql).

Design mirrors CaseRepository (repository.py):
- Synchronous (called via asyncio.to_thread in async handlers).
- Graceful failure: log + return None/False; never raise to caller.
- Offline mode: supabase_client=None returns in-memory objects.

Exception: insert_action() raises DuplicateActionError on DB
idempotency-key violation (23505) — callers must handle this one.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from case_engine.action_models import ActionRequest, ActionTransitionRecord
from case_engine.action_state import ActionState

LOGGER = logging.getLogger(__name__)

_ACTIONS_TABLE     = "action_gateway"
_TRANSITIONS_TABLE = "action_gateway_transitions"


def _is_unique_violation(exc: Exception) -> bool:
    """Return True if exc is a PostgreSQL unique-constraint violation (23505)."""
    msg = str(exc).lower()
    return "23505" in msg or "duplicate key" in msg or "unique constraint" in msg


class ActionRepository:
    """
    Persistence layer for action_gateway and action_gateway_transitions.

    supabase_client: supabase-py Client, or None for in-memory/offline use.
    """

    def __init__(self, supabase_client: Any = None) -> None:
        self._sb = supabase_client
        # Used by update_action(expected_state=...) in offline mode for atomic claiming
        self._claim_lock = threading.Lock()

    # ── action_gateway ─────────────────────────────────────────────────────────

    def insert_action(self, action: ActionRequest) -> ActionRequest:
        """
        Insert a new action_gateway row.

        Returns the inserted ActionRequest (with any DB-generated defaults),
        or the in-memory ActionRequest if offline.

        Raises DuplicateActionError if the idempotency_key already exists.
        All other DB errors are logged and the in-memory action is returned.
        """
        if self._sb is None:
            LOGGER.debug("action_repo: offline — action in-memory only %s", action.action_id)
            return action

        try:
            result = (
                self._sb
                .table(_ACTIONS_TABLE)
                .insert(action.to_db_row())
                .execute()
            )
            if result.data:
                return ActionRequest.from_db_row(result.data[0])
            return action
        except Exception as exc:
            if _is_unique_violation(exc):
                # Lazy import avoids circular: action_gateway imports action_repository
                from case_engine.action_gateway import DuplicateActionError  # noqa: PLC0415
                # Fetch the existing row so callers have the conflicting ActionRequest
                existing = self.get_action_by_idempotency_key(action.idempotency_key)
                if existing is None:
                    existing = action  # fallback for extreme race condition (row deleted between fail and lookup)
                raise DuplicateActionError(existing) from exc
            LOGGER.error(
                "action_repo.insert_action failed action_id=%s type=%s error=%s",
                action.action_id, action.action_type, exc,
            )
            return action

    def get_action(self, action_id: str) -> ActionRequest | None:
        if self._sb is None:
            return None
        try:
            result = (
                self._sb
                .table(_ACTIONS_TABLE)
                .select("*")
                .eq("action_id", action_id)
                .limit(1)
                .execute()
            )
            if result.data:
                return ActionRequest.from_db_row(result.data[0])
            return None
        except Exception as exc:
            LOGGER.error("action_repo.get_action failed action_id=%s error=%s", action_id, exc)
            return None

    def get_action_by_idempotency_key(self, idempotency_key: str) -> ActionRequest | None:
        if self._sb is None:
            return None
        try:
            result = (
                self._sb
                .table(_ACTIONS_TABLE)
                .select("*")
                .eq("idempotency_key", idempotency_key)
                .limit(1)
                .execute()
            )
            if result.data:
                return ActionRequest.from_db_row(result.data[0])
            return None
        except Exception as exc:
            LOGGER.error(
                "action_repo.get_by_idempotency_key failed key=%s error=%s",
                idempotency_key[:16] + "...", exc,
            )
            return None

    def update_action(
        self,
        action: ActionRequest,
        *,
        expected_state: ActionState | None = None,
    ) -> bool:
        """
        Persist all mutable fields of an ActionRequest to the DB.

        Args:
            expected_state: When provided, adds a WHERE current_state = expected_state
                clause. Returns False (without raising) if 0 rows matched, which means
                another worker already changed the state — the optimistic locking signal
                for distributed execution safety.

        Returns:
            True if the row was updated, False if no rows matched (only when
            expected_state is provided and the state has already changed).
        """
        if self._sb is None:
            # In-memory mode: use lock for atomic check-and-set when expected_state given
            if expected_state is not None:
                with self._claim_lock:
                    if action.current_state != expected_state:
                        # Simulated concurrent claim failure (shouldn't happen in tests)
                        return False
            return True

        row = action.to_db_row()
        # Remove immutable fields from the patch
        for immutable in ("action_id", "case_id", "ticket_id", "client",
                          "action_type", "action_namespace", "risk_level",
                          "proposed_by", "proposed_at", "created_at",
                          "idempotency_key"):
            row.pop(immutable, None)

        try:
            query = self._sb.table(_ACTIONS_TABLE).update(row).eq("action_id", action.action_id)
            if expected_state is not None:
                query = query.eq("current_state", expected_state.value)
            result = query.execute()
            if expected_state is not None and not result.data:
                LOGGER.warning(
                    "action_repo.update_action: optimistic lock failed action_id=%s "
                    "expected_state=%s — claimed by another worker",
                    action.action_id, expected_state.value,
                )
                return False
            return True
        except Exception as exc:
            LOGGER.error(
                "action_repo.update_action failed action_id=%s state=%s error=%s",
                action.action_id, action.current_state.value, exc,
            )
            return False

    def list_actions_for_case(self, case_id: str) -> list[ActionRequest]:
        if self._sb is None:
            return []
        try:
            result = (
                self._sb
                .table(_ACTIONS_TABLE)
                .select("*")
                .eq("case_id", case_id)
                .order("created_at", desc=False)
                .execute()
            )
            return [ActionRequest.from_db_row(r) for r in (result.data or [])]
        except Exception as exc:
            LOGGER.error(
                "action_repo.list_actions_for_case failed case_id=%s error=%s", case_id, exc,
            )
            return []

    def list_approved_actions(self, client: str) -> list[ActionRequest]:
        """Return APPROVED actions for a client, ordered by proposed_at ASC (FIFO)."""
        if self._sb is None:
            return []
        try:
            result = (
                self._sb
                .table(_ACTIONS_TABLE)
                .select("*")
                .eq("client", client)
                .eq("current_state", ActionState.APPROVED.value)
                .order("proposed_at", desc=False)
                .execute()
            )
            return [ActionRequest.from_db_row(r) for r in (result.data or [])]
        except Exception as exc:
            LOGGER.error(
                "action_repo.list_approved_actions failed client=%s error=%s", client, exc,
            )
            return []

    def list_pending_approvals(self, client: str) -> list[ActionRequest]:
        """Return AWAITING_APPROVAL actions for a client, ordered by expires_at ASC (urgency-first)."""
        if self._sb is None:
            return []
        try:
            result = (
                self._sb
                .table(_ACTIONS_TABLE)
                .select("*")
                .eq("client", client)
                .eq("current_state", ActionState.AWAITING_APPROVAL.value)
                .order("expires_at", desc=False)
                .execute()
            )
            return [ActionRequest.from_db_row(r) for r in (result.data or [])]
        except Exception as exc:
            LOGGER.error(
                "action_repo.list_pending_approvals failed client=%s error=%s", client, exc,
            )
            return []

    def list_rolling_back_actions(self, client: str | None = None) -> list[ActionRequest]:
        """
        Return ROLLING_BACK actions that have a linked compensation (rollback_action_id set).

        Used by ActionWorker.tick_rollbacks() to find originals whose compensation
        actions are ready to execute. Optionally filtered by client.
        """
        if self._sb is None:
            return []
        try:
            query = (
                self._sb
                .table(_ACTIONS_TABLE)
                .select("*")
                .eq("current_state", ActionState.ROLLING_BACK.value)
                .not_.is_("rollback_action_id", "null")
            )
            if client is not None:
                query = query.eq("client", client)
            result = query.order("proposed_at", desc=False).execute()
            return [ActionRequest.from_db_row(r) for r in (result.data or [])]
        except Exception as exc:
            LOGGER.error(
                "action_repo.list_rolling_back_actions failed client=%s error=%s", client, exc,
            )
            return []

    def list_dead_letter_actions(self, client: str | None = None) -> list[ActionRequest]:
        """
        Return DEAD_LETTER actions (all retries exhausted), optionally filtered by client.

        Used by compliance tooling and manual intervention workflows.
        """
        if self._sb is None:
            return []
        try:
            query = (
                self._sb
                .table(_ACTIONS_TABLE)
                .select("*")
                .eq("current_state", ActionState.DEAD_LETTER.value)
            )
            if client is not None:
                query = query.eq("client", client)
            result = query.order("dead_lettered_at", desc=False).execute()
            return [ActionRequest.from_db_row(r) for r in (result.data or [])]
        except Exception as exc:
            LOGGER.error(
                "action_repo.list_dead_letter_actions failed client=%s error=%s", client, exc,
            )
            return []

    def list_expired_actions(self, client: str | None = None) -> list[ActionRequest]:
        """
        Return AWAITING_APPROVAL or APPROVED actions whose expires_at is in the past.

        Used by the expiry sweep job to transition stale actions to EXPIRED.
        Optionally filtered by client; if None, returns across all clients.
        """
        if self._sb is None:
            return []
        try:
            now_iso = datetime.now(tz=timezone.utc).isoformat()
            query = (
                self._sb
                .table(_ACTIONS_TABLE)
                .select("*")
                .in_("current_state", [
                    ActionState.AWAITING_APPROVAL.value,
                    ActionState.APPROVED.value,
                ])
                .lt("expires_at", now_iso)
            )
            if client is not None:
                query = query.eq("client", client)
            result = query.order("expires_at", desc=False).execute()
            return [ActionRequest.from_db_row(r) for r in (result.data or [])]
        except Exception as exc:
            LOGGER.error(
                "action_repo.list_expired_actions failed client=%s error=%s", client, exc,
            )
            return []

    # ── action_gateway_transitions ─────────────────────────────────────────────

    def record_transition(self, record: ActionTransitionRecord) -> bool:
        """Append a transition record (INSERT only — table is append-only via RULES)."""
        if self._sb is None:
            return True
        try:
            self._sb.table(_TRANSITIONS_TABLE).insert(record.to_db_row()).execute()
            return True
        except Exception as exc:
            LOGGER.error(
                "action_repo.record_transition failed action_id=%s %s→%s error=%s",
                record.action_id,
                record.from_state.value,
                record.to_state.value,
                exc,
            )
            return False

    def append_transition(self, record: ActionTransitionRecord) -> bool:
        """Canonical alias for record_transition()."""
        return self.record_transition(record)

    def get_transitions(self, action_id: str) -> list[ActionTransitionRecord]:
        """Return full transition history for an action, ordered by created_at ASC."""
        if self._sb is None:
            return []
        try:
            result = (
                self._sb
                .table(_TRANSITIONS_TABLE)
                .select("*")
                .eq("action_id", action_id)
                .order("created_at", desc=False)
                .execute()
            )
            rows = result.data or []
            return [
                ActionTransitionRecord(
                    transition_id=r["transition_id"],
                    action_id=r["action_id"],
                    case_id=r["case_id"],
                    ticket_id=r["ticket_id"],
                    client=r["client"],
                    from_state=ActionState(r["from_state"]),
                    to_state=ActionState(r["to_state"]),
                    actor=r.get("actor", "system"),
                    reason=r.get("reason"),
                    detail=r.get("detail") or {},
                )
                for r in rows
            ]
        except Exception as exc:
            LOGGER.error(
                "action_repo.get_transitions failed action_id=%s error=%s", action_id, exc,
            )
            return []
