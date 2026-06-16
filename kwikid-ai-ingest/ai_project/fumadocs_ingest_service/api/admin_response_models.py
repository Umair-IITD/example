"""
api/admin_response_models.py

Sprint 2.13: Pydantic response models for admin/operational endpoints.

All models are frozen and use strict typing. Datetimes are represented as
ISO 8601 strings so they survive JSON serialisation.

No business logic lives here — these are pure serialisation contracts.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class StateCountItem(BaseModel):
    """Count of actions in a single state."""
    model_config = ConfigDict(frozen=True)

    state: str
    count: int


class GatewaySummaryResponse(BaseModel):
    """Response for GET /admin/action-gateway/summary"""
    model_config = ConfigDict(frozen=True)

    client: str | None
    state_counts: list[StateCountItem]
    total_live: int
    metrics_totals: dict[str, int]
    checked_at: str


class DeadLetterActionItem(BaseModel):
    """A single dead-lettered action."""
    model_config = ConfigDict(frozen=True)

    action_id: str
    action_type: str
    action_namespace: str
    client: str
    proposed_at: str | None
    dead_lettered_at: str | None
    failure_code: str | None
    failure_reason: str | None
    execution_attempt: int
    max_attempts: int


class DeadLetterResponse(BaseModel):
    """Response for GET /admin/action-gateway/dead-letter"""
    model_config = ConfigDict(frozen=True)

    client: str | None
    actions: list[DeadLetterActionItem]
    total: int
    limit: int
    offset: int
    checked_at: str


class RetriedActionItem(BaseModel):
    """An action that has been retried at least once."""
    model_config = ConfigDict(frozen=True)

    action_id: str
    action_type: str
    action_namespace: str
    client: str
    current_state: str
    execution_attempt: int
    max_attempts: int
    failure_code: str | None
    failure_reason: str | None
    proposed_at: str | None


class RetryResponse(BaseModel):
    """Response for GET /admin/action-gateway/retries"""
    model_config = ConfigDict(frozen=True)

    client: str | None
    actions: list[RetriedActionItem]
    total: int
    checked_at: str


class StuckExecutingItem(BaseModel):
    """An action stuck in EXECUTING beyond the threshold."""
    model_config = ConfigDict(frozen=True)

    action_id: str
    action_type: str
    action_namespace: str
    client: str
    executor_id: str | None
    execution_started_at: str | None
    execution_attempt: int
    stuck_for_seconds: float


class StaleApprovalItem(BaseModel):
    """An action stuck in AWAITING_APPROVAL beyond the threshold."""
    model_config = ConfigDict(frozen=True)

    action_id: str
    action_type: str
    action_namespace: str
    client: str
    risk_level: str
    proposed_at: str | None
    expires_at: str | None
    waiting_for_seconds: float


class StuckActionsResponse(BaseModel):
    """Response for GET /admin/action-gateway/stuck"""
    model_config = ConfigDict(frozen=True)

    client: str | None
    stuck_executing: list[StuckExecutingItem]
    stale_approval: list[StaleApprovalItem]
    total_stuck: int
    executing_threshold_seconds: int
    approval_threshold_seconds: int
    checked_at: str


class WorkerStatsItem(BaseModel):
    """Aggregated stats for a single worker derived from recent actions."""
    model_config = ConfigDict(frozen=True)

    worker_id: str
    successful: int
    failed: int
    dead_lettered: int
    total_processed: int
    last_execution_at: str | None


class WorkerResponse(BaseModel):
    """Response for GET /admin/action-gateway/workers"""
    model_config = ConfigDict(frozen=True)

    client: str | None
    workers: list[WorkerStatsItem]
    total_workers: int
    window_hours: int
    checked_at: str
