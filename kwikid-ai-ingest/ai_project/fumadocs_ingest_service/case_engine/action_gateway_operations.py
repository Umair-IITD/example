"""
case_engine/action_gateway_operations.py

Sprint 2.13: ActionGatewayOperationsService — read-only operational visibility.

Answers the questions a production operator needs to answer without touching
the database directly:

  - What actions are waiting?          → get_state_summary()
  - What actions failed?               → get_dead_letter_summary()
  - What actions are dead-lettered?    → get_dead_letter_summary()
  - What actions are executing?        → get_stuck_actions()
  - Which worker processed an action?  → get_worker_summary()
  - Why did an action fail?            → get_dead_letter_summary()
  - What retries occurred?             → get_retry_summary()
  - How many actions succeeded today?  → get_state_summary() / MetricsService
  - How many actions failed today?     → get_state_summary() / MetricsService
  - How many actions are stuck?        → get_stuck_actions()

Design:
  - Read-only: zero state mutations.
  - Never raises: all repository and computation exceptions are caught and
    represented as empty results with a logged warning.
  - Offline-safe: returns empty/zero summaries when repository has no DB client.
  - Returns frozen dataclasses for immutability. The API layer converts to Pydantic.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from case_engine.action_state import ActionState

if TYPE_CHECKING:
    from case_engine.action_repository import ActionRepository
    from metrics.service import MetricsService

LOGGER = logging.getLogger(__name__)


# ── Result dataclasses ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StateCount:
    state: str
    count: int


@dataclass(frozen=True)
class StateSummary:
    """Per-state action counts plus in-process metrics totals."""
    client: str | None
    state_counts: list[StateCount]
    total_live: int
    metrics_totals: dict[str, int]
    checked_at: datetime


@dataclass(frozen=True)
class DeadLetterItem:
    action_id: str
    action_type: str
    action_namespace: str
    client: str
    proposed_at: datetime | None
    dead_lettered_at: datetime | None
    failure_code: str | None
    failure_reason: str | None
    execution_attempt: int
    max_attempts: int


@dataclass(frozen=True)
class DeadLetterSummary:
    client: str | None
    items: list[DeadLetterItem]
    total: int
    checked_at: datetime


@dataclass(frozen=True)
class RetriedActionItem:
    action_id: str
    action_type: str
    action_namespace: str
    client: str
    current_state: str
    execution_attempt: int
    max_attempts: int
    failure_code: str | None
    failure_reason: str | None
    proposed_at: datetime | None


@dataclass(frozen=True)
class RetrySummary:
    client: str | None
    items: list[RetriedActionItem]
    total: int
    checked_at: datetime


@dataclass(frozen=True)
class StuckExecutingItem:
    action_id: str
    action_type: str
    action_namespace: str
    client: str
    executor_id: str | None
    execution_started_at: datetime | None
    execution_attempt: int
    stuck_for_seconds: float


@dataclass(frozen=True)
class StaleApprovalItem:
    action_id: str
    action_type: str
    action_namespace: str
    client: str
    risk_level: str
    proposed_at: datetime | None
    expires_at: datetime | None
    waiting_for_seconds: float


@dataclass(frozen=True)
class StuckActionsSummary:
    stuck_executing: list[StuckExecutingItem]
    stale_approval: list[StaleApprovalItem]
    executing_threshold_seconds: int
    approval_threshold_seconds: int
    client: str | None
    checked_at: datetime


@dataclass(frozen=True)
class WorkerStats:
    worker_id: str
    successful: int
    failed: int
    dead_lettered: int
    total_processed: int
    last_execution_at: datetime | None


@dataclass(frozen=True)
class WorkerSummary:
    client: str | None
    workers: list[WorkerStats]
    total_workers: int
    window_hours: int
    checked_at: datetime


# ── Operational service ────────────────────────────────────────────────────────

class ActionGatewayOperationsService:
    """
    Read-only operational queries for the Action Gateway.

    All methods are safe to call in production without any risk of state
    mutation. Never raises — exceptions from the repository are caught and
    returned as empty/zero results with a logged warning.
    """

    def __init__(
        self,
        repository: "ActionRepository",
        metrics_service: "MetricsService | None" = None,
    ) -> None:
        self._repo = repository
        self._metrics = metrics_service

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_state_summary(self, client: str | None = None) -> StateSummary:
        """
        Return a count of actions per state and in-process metrics totals.

        The state_counts are DB-backed (current truth). The metrics_totals
        are in-process counters (reset on restart). Both together give the
        operator a complete picture of the gateway's health.
        """
        state_counts: list[StateCount] = []
        try:
            raw = self._repo.count_by_state(client=client)
            for state_name, count in sorted(raw.items()):
                state_counts.append(StateCount(state=state_name, count=count))
        except Exception as exc:
            LOGGER.warning(
                "operations.get_state_summary: count_by_state failed client=%s error=%s",
                client, exc,
            )

        live_states = {
            ActionState.PROPOSED.value,
            ActionState.AWAITING_APPROVAL.value,
            ActionState.APPROVED.value,
            ActionState.EXECUTING.value,
            ActionState.ROLLING_BACK.value,
        }
        total_live = sum(
            sc.count for sc in state_counts
            if sc.state in live_states and sc.count > 0
        )

        metrics_totals: dict[str, int] = {}
        if self._metrics is not None:
            try:
                metrics_totals = self._metrics.get_gateway_totals()
            except Exception as exc:
                LOGGER.warning(
                    "operations.get_state_summary: get_gateway_totals failed error=%s", exc,
                )

        return StateSummary(
            client=client,
            state_counts=state_counts,
            total_live=total_live,
            metrics_totals=metrics_totals,
            checked_at=datetime.now(tz=timezone.utc),
        )

    def get_dead_letter_summary(
        self,
        client: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> DeadLetterSummary:
        """
        Return dead-lettered actions in a typed summary.

        limit/offset apply in-process (the repository returns all dead-letter
        rows; filtering happens here to avoid adding pagination complexity to
        the repository).
        """
        try:
            actions = self._repo.list_dead_letter_actions(client=client)
        except Exception as exc:
            LOGGER.warning(
                "operations.get_dead_letter_summary: failed client=%s error=%s", client, exc,
            )
            actions = []

        total = len(actions)
        page = actions[offset: offset + limit]

        items = [
            DeadLetterItem(
                action_id=a.action_id,
                action_type=a.action_type,
                action_namespace=a.action_namespace,
                client=a.client,
                proposed_at=a.proposed_at,
                dead_lettered_at=getattr(a, "dead_lettered_at", None),
                failure_code=a.failure_code,
                failure_reason=a.failure_reason,
                execution_attempt=a.execution_attempt,
                max_attempts=a.max_attempts,
            )
            for a in page
        ]
        return DeadLetterSummary(
            client=client,
            items=items,
            total=total,
            checked_at=datetime.now(tz=timezone.utc),
        )

    def get_retry_summary(self, client: str | None = None) -> RetrySummary:
        """
        Return actions that have been retried at least once (execution_attempt >= 2).

        Covers all states — the action may have ultimately succeeded, failed,
        or been dead-lettered after retries.
        """
        try:
            actions = self._repo.list_retried_actions(client=client)
        except Exception as exc:
            LOGGER.warning(
                "operations.get_retry_summary: failed client=%s error=%s", client, exc,
            )
            actions = []

        items = [
            RetriedActionItem(
                action_id=a.action_id,
                action_type=a.action_type,
                action_namespace=a.action_namespace,
                client=a.client,
                current_state=a.current_state.value,
                execution_attempt=a.execution_attempt,
                max_attempts=a.max_attempts,
                failure_code=a.failure_code,
                failure_reason=a.failure_reason,
                proposed_at=a.proposed_at,
            )
            for a in actions
        ]
        return RetrySummary(
            client=client,
            items=items,
            total=len(items),
            checked_at=datetime.now(tz=timezone.utc),
        )

    def get_stuck_actions(
        self,
        executing_threshold_seconds: int = 900,
        approval_threshold_seconds: int = 86400,
        client: str | None = None,
    ) -> StuckActionsSummary:
        """
        Detect actions stuck in EXECUTING or AWAITING_APPROVAL beyond thresholds.

        EXECUTING: compares now - execution_started_at against executing_threshold_seconds.
        AWAITING_APPROVAL: compares now - proposed_at against approval_threshold_seconds.

        Returns read-only findings. Does NOT modify any action state.

        Defaults:
          executing_threshold_seconds = 900  (15 minutes)
          approval_threshold_seconds  = 86400 (24 hours)
        """
        now = datetime.now(tz=timezone.utc)
        stuck_executing: list[StuckExecutingItem] = []
        stale_approval: list[StaleApprovalItem] = []

        try:
            executing = self._repo.list_executing_actions(older_than_seconds=executing_threshold_seconds)
            for a in executing:
                started = a.execution_started_at
                stuck_for = (now - started).total_seconds() if started else 0.0
                stuck_executing.append(StuckExecutingItem(
                    action_id=a.action_id,
                    action_type=a.action_type,
                    action_namespace=a.action_namespace,
                    client=a.client,
                    executor_id=a.executor_id,
                    execution_started_at=started,
                    execution_attempt=a.execution_attempt,
                    stuck_for_seconds=round(stuck_for, 1),
                ))
        except Exception as exc:
            LOGGER.warning(
                "operations.get_stuck_actions: executing query failed error=%s", exc,
            )

        try:
            stale = self._repo.list_stale_approval_actions(
                older_than_seconds=approval_threshold_seconds,
                client=client,
            )
            for a in stale:
                proposed = a.proposed_at
                waiting_for = (now - proposed).total_seconds() if proposed else 0.0
                stale_approval.append(StaleApprovalItem(
                    action_id=a.action_id,
                    action_type=a.action_type,
                    action_namespace=a.action_namespace,
                    client=a.client,
                    risk_level=a.risk_level.value,
                    proposed_at=proposed,
                    expires_at=a.expires_at,
                    waiting_for_seconds=round(waiting_for, 1),
                ))
        except Exception as exc:
            LOGGER.warning(
                "operations.get_stuck_actions: stale_approval query failed error=%s", exc,
            )

        return StuckActionsSummary(
            stuck_executing=stuck_executing,
            stale_approval=stale_approval,
            executing_threshold_seconds=executing_threshold_seconds,
            approval_threshold_seconds=approval_threshold_seconds,
            client=client,
            checked_at=now,
        )

    def get_worker_summary(
        self,
        client: str | None = None,
        window_hours: int = 24,
    ) -> WorkerSummary:
        """
        Derive per-worker execution statistics from recently terminal actions.

        Groups EXECUTED, FAILED, and DEAD_LETTER actions by executor_id to
        produce per-worker success/failure/dead-letter counts and the timestamp
        of the most recent execution.

        Bounded by window_hours to avoid full-table scans. Default: 24 hours.
        """
        try:
            actions = self._repo.list_recently_terminal_actions(
                hours=window_hours,
                client=client,
            )
        except Exception as exc:
            LOGGER.warning(
                "operations.get_worker_summary: failed client=%s error=%s", client, exc,
            )
            actions = []

        # Group by executor_id
        stats: dict[str, dict[str, Any]] = {}
        for a in actions:
            wid = a.executor_id or "unknown"
            if wid not in stats:
                stats[wid] = {
                    "successful": 0,
                    "failed": 0,
                    "dead_lettered": 0,
                    "last_execution_at": None,
                }
            s = stats[wid]
            if a.current_state == ActionState.EXECUTED:
                s["successful"] += 1
            elif a.current_state == ActionState.DEAD_LETTER:
                s["dead_lettered"] += 1
            else:
                s["failed"] += 1

            ec = getattr(a, "execution_completed_at", None)
            if ec is not None:
                if s["last_execution_at"] is None or ec > s["last_execution_at"]:
                    s["last_execution_at"] = ec

        workers = [
            WorkerStats(
                worker_id=wid,
                successful=d["successful"],
                failed=d["failed"],
                dead_lettered=d["dead_lettered"],
                total_processed=d["successful"] + d["failed"] + d["dead_lettered"],
                last_execution_at=d["last_execution_at"],
            )
            for wid, d in sorted(stats.items())
        ]
        return WorkerSummary(
            client=client,
            workers=workers,
            total_workers=len(workers),
            window_hours=window_hours,
            checked_at=datetime.now(tz=timezone.utc),
        )
