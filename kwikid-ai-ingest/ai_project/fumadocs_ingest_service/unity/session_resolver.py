"""
unity/session_resolver.py

Sprint 2.51: Select the most relevant Unity session for a support ticket
from a getAllUserSession response.

Selection rules (SOT integration_notes.md §3):
  1. Sessions before ticket_created_at are preferred (temporal alignment).
  2. Among those, prefer non-terminal (active/stuck) sessions near ticket time.
  3. Otherwise, pick the most-recently-initialised session.

Never raises — returns None if the input list is empty.

Dependency direction: session_resolver.py → stdlib + unity.models
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from unity.models import SessionStatus, UnitySession


# Non-terminal statuses (SOT integration_notes §3)
_NON_TERMINAL: frozenset[SessionStatus] = frozenset({
    SessionStatus.WAITING,
    SessionStatus.KYC_RESULT_PARTIAL_UPDATE,
})


def select_most_relevant_session(
    sessions: Iterable[UnitySession],
    *,
    ticket_created_at: datetime | float | str | None = None,
) -> UnitySession | None:
    """
    Return the single most-relevant UnitySession for a support ticket, or
    None if the collection is empty.

    `ticket_created_at` may be:
      - datetime (aware or naive — naive treated as UTC)
      - float / int (unix seconds)
      - ISO 8601 string
      - None (no temporal alignment; fall through to "most recent")
    """
    sessions = tuple(sessions)
    if not sessions:
        return None

    ticket_ts = _to_unix(ticket_created_at)

    if ticket_ts is None:
        # No temporal alignment — pick the latest init_time.
        return _pick_latest_init(sessions)

    before = [s for s in sessions if s.timeline.init_time and s.timeline.init_time <= ticket_ts]
    if not before:
        # All sessions came AFTER the ticket — pick the latest overall.
        return _pick_latest_init(sessions)

    non_terminal = [s for s in before if s.session_status in _NON_TERMINAL]
    if non_terminal:
        return _pick_latest_init(non_terminal)

    return _pick_latest_init(before)


def _pick_latest_init(sessions: Iterable[UnitySession]) -> UnitySession:
    return max(sessions, key=lambda s: s.timeline.init_time or 0.0)


def _to_unix(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # ISO 8601
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            try:
                return float(s)
            except ValueError:
                return None
    return None
