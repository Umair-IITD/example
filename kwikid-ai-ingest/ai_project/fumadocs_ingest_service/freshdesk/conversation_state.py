"""
freshdesk/conversation_state.py

Sprint 2.28.1: ConversationStateStore — per-ticket conversation lifecycle tracking.

State machine:
  OPEN → PENDING → CLARIFICATION → RESOLVED → CLOSED
  Any state → ESCALATED (hard stop)

Design:
  - ConversationState is a mutable dataclass (updated in place).
  - ConversationStateStore is the single source of truth per ticket.
  - In-memory primary store for speed; Supabase persistence for durability.
  - Survives service restarts: on startup, states are loaded from Supabase on first access.
  - update() applies a partial update dict to the stored state.
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

LOGGER = logging.getLogger(__name__)

_TABLE = "support_conversation_state"


class ConversationLifecycle(str, Enum):
    OPEN        = "OPEN"
    PENDING     = "PENDING"
    CLARIFICATION = "CLARIFICATION"
    RESOLVED    = "RESOLVED"
    CLOSED      = "CLOSED"
    ESCALATED   = "ESCALATED"


@dataclass
class ConversationState:
    ticket_id: str
    client_id: str
    lifecycle_state: ConversationLifecycle = ConversationLifecycle.OPEN
    clarification_pending: bool = False
    awaiting_customer: bool = False
    awaiting_human_approval: bool = False
    clarification_count: int = 0
    case_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    resolved_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_db_row(self) -> dict[str, Any]:
        from datetime import datetime, timezone
        def _ts(t: float | None) -> str | None:
            if t is None:
                return None
            return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()

        return {
            "ticket_id":               self.ticket_id,
            "client_id":               self.client_id,
            "lifecycle_state":         self.lifecycle_state.value,
            "clarification_pending":   self.clarification_pending,
            "awaiting_customer":       self.awaiting_customer,
            "awaiting_human_approval": self.awaiting_human_approval,
            "clarification_count":     self.clarification_count,
            "case_id":                 self.case_id,
            "created_at":              _ts(self.created_at),
            "updated_at":              _ts(self.updated_at),
            "resolved_at":             _ts(self.resolved_at),
            "metadata":                self.metadata,
        }

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> "ConversationState":
        import datetime as _dt
        def _parse_ts(v: str | None) -> float | None:
            if not v:
                return None
            try:
                return _dt.datetime.fromisoformat(v).timestamp()
            except Exception:
                return None

        return cls(
            ticket_id=row["ticket_id"],
            client_id=row.get("client_id", ""),
            lifecycle_state=ConversationLifecycle(row.get("lifecycle_state", "OPEN")),
            clarification_pending=bool(row.get("clarification_pending", False)),
            awaiting_customer=bool(row.get("awaiting_customer", False)),
            awaiting_human_approval=bool(row.get("awaiting_human_approval", False)),
            clarification_count=int(row.get("clarification_count", 0)),
            case_id=row.get("case_id"),
            created_at=_parse_ts(row.get("created_at")) or time.time(),
            updated_at=_parse_ts(row.get("updated_at")) or time.time(),
            resolved_at=_parse_ts(row.get("resolved_at")),
            metadata=row.get("metadata") or {},
        )


class ConversationStateStore:
    """
    Tracks conversation lifecycle per ticket.

    Usage:
        store = ConversationStateStore(supabase_client)
        state = store.get_or_create(ticket_id, client_id)
        store.update(ticket_id, clarification_pending=True, lifecycle_state=ConversationLifecycle.CLARIFICATION)
    """

    def __init__(self, supabase_client: Any = None) -> None:
        self._sb = supabase_client
        self._store: dict[str, ConversationState] = {}

    # ── Read ──────────────────────────────────────────────────────────────────

    def get(self, ticket_id: str) -> ConversationState | None:
        if ticket_id in self._store:
            return self._store[ticket_id]
        if self._sb is not None:
            return self._db_get(ticket_id)
        return None

    def get_or_create(self, ticket_id: str, client_id: str) -> ConversationState:
        existing = self.get(ticket_id)
        if existing is not None:
            return existing
        state = ConversationState(ticket_id=ticket_id, client_id=client_id)
        self._store[ticket_id] = state
        if self._sb is not None:
            self._db_upsert(state)
        return state

    def exists(self, ticket_id: str) -> bool:
        return self.get(ticket_id) is not None

    # ── Write ─────────────────────────────────────────────────────────────────

    def update(self, ticket_id: str, **kwargs: Any) -> ConversationState | None:
        state = self.get(ticket_id)
        if state is None:
            LOGGER.warning("conversation_state.update: unknown ticket_id=%s", ticket_id)
            return None
        state.updated_at = time.time()
        for key, value in kwargs.items():
            if key == "lifecycle_state" and isinstance(value, str):
                value = ConversationLifecycle(value)
            if key == "resolved_at" and value is True:
                value = time.time()
            if hasattr(state, key):
                setattr(state, key, value)
            else:
                LOGGER.warning("conversation_state.update: unknown field=%s", key)
        self._store[ticket_id] = state
        if self._sb is not None:
            self._db_upsert(state)
        return state

    def set_resolved(self, ticket_id: str) -> ConversationState | None:
        return self.update(
            ticket_id,
            lifecycle_state=ConversationLifecycle.RESOLVED,
            resolved_at=time.time(),
            clarification_pending=False,
            awaiting_customer=False,
        )

    def set_escalated(self, ticket_id: str) -> ConversationState | None:
        return self.update(
            ticket_id,
            lifecycle_state=ConversationLifecycle.ESCALATED,
            clarification_pending=False,
        )

    def increment_clarification(self, ticket_id: str) -> ConversationState | None:
        state = self.get(ticket_id)
        if state is None:
            return None
        return self.update(
            ticket_id,
            clarification_count=state.clarification_count + 1,
            clarification_pending=True,
            awaiting_customer=True,
            lifecycle_state=ConversationLifecycle.CLARIFICATION,
        )

    # ── Introspection ─────────────────────────────────────────────────────────

    def count(self) -> int:
        return len(self._store)

    def all_ticket_ids(self) -> list[str]:
        return list(self._store.keys())

    # ── Supabase persistence ──────────────────────────────────────────────────

    def _db_get(self, ticket_id: str) -> ConversationState | None:
        try:
            result = (
                self._sb.table(_TABLE)
                .select("*")
                .eq("ticket_id", ticket_id)
                .limit(1)
                .execute()
            )
            if result.data:
                state = ConversationState.from_db_row(result.data[0])
                self._store[ticket_id] = state
                return state
            return None
        except Exception as exc:
            LOGGER.warning("conversation_state.db_get: error=%s ticket_id=%s", exc, ticket_id)
            return None

    def _db_upsert(self, state: ConversationState) -> None:
        try:
            self._sb.table(_TABLE).upsert(state.to_db_row()).execute()
        except Exception as exc:
            LOGGER.warning(
                "conversation_state.db_upsert: error=%s ticket_id=%s",
                exc, state.ticket_id,
            )
