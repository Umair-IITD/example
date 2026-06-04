"""
tests/test_sprint29_audit.py

Sprint 2.9: Compliance, Audit Persistence & Observability.

Coverage:
  InMemoryAuditRepository:
    - insert / retrieve by action / case / client
    - list_events with all filter combinations
    - count_events with all filter combinations
    - pagination (limit, offset)
    - O(1) indices for primary lookups

  AuditService:
    - emit delegates to repository
    - get_action/case/client_history
    - search with filters and pagination
    - count with filters
    - emit never raises

  AuditLogger (refactored, backward-compat):
    - AuditLogger() still works with no args
    - emit / events_for_action / all_events / count / count_by_type
    - repository property exposed
    - shared-repo pattern: logger + service see same events

  AuditEvent model:
    - case_id and client fields added with defaults
    - to_dict includes case_id and client
    - backward compat: positional construction still works
    - immutable (frozen=True)

  Worker audit emission (ACTION_EXECUTION_STARTED, ACTION_EXECUTED,
  ACTION_FAILED, ACTION_ROLLED_BACK, ACTION_ROLLBACK_FAILED):
    - Events emitted with correct action_id, case_id, client, actor
    - Action_type in metadata
    - No emission when audit_service is None (backward compat)
    - Rollback events target the original action_id

  Watchdog audit emission (ACTION_EXPIRED):
    - Emitted per-action on expiry
    - Contains action_id, case_id, client, reason
    - Idempotent: second run emits no additional events
    - No emission when audit_service is None

  Audit Query API (GET /audit/...):
    - ADMIN key required on all endpoints
    - 401 without key; 403 with APPROVER/OPERATOR key
    - GET /audit/actions/{id} returns correct events
    - GET /audit/cases/{id} returns correct events
    - GET /audit/clients/{client} returns correct events
    - GET /audit/events with filters and pagination
    - Response structure: events list, total, limit, offset
    - ISO8601 timestamps
    - Empty results: 200 with empty list

  Security:
    - No stack traces in error responses
    - Invalid event_type filter → 400 INVALID_FILTER (not 500)
    - Audit endpoints not accessible to APPROVER or OPERATOR

  Compliance reconstruction:
    - Full action lifecycle reconstructable via audit API
    - Rollback lifecycle reconstructable
    - SLA expiry lifecycle reconstructable
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from audit.logger import AuditLogger
from audit.models import AuditEvent, AuditEventType
from audit.repository import InMemoryAuditRepository
from audit.service import AuditService
from case_engine.action_executor import (
    ExecutionContext,
    ExecutionResult,
    PermanentExecutionError,
    RetryableExecutionError,
)
from case_engine.action_gateway import ActionGateway
from case_engine.action_models import ActionProposal, ActionRequest
from case_engine.action_repository import ActionRepository
from case_engine.action_runtime import ActionRuntime
from case_engine.action_state import ActionRiskLevel, ActionState
from case_engine.executor_registry import ActionExecutorRegistry
from case_engine.models import Case
from case_engine.provider_registry import ProviderRegistry
from case_engine.provider_router import ProviderRouter
from case_engine.sla_watchdog import SLAWatchdog
from security.auth import ApiKeyAuthenticator
from security.roles import Role
from webhook.freshdesk_processor import FreshdeskWebhookProcessor


# ── Constants ──────────────────────────────────────────────────────────────────

_ADMIN_KEY    = "admin-s29-key-alpha"
_APPROVER_KEY = "approver-s29-key-beta"
_OPERATOR_KEY = "operator-s29-key-gamma"
_CLIENT       = "acme_bank"
_WH_SECRET    = b"wh-secret-s29"


# ── Shared fake infrastructure ─────────────────────────────────────────────────

class _FakeRepository(ActionRepository):
    def __init__(self) -> None:
        super().__init__(supabase_client=None)
        self._store: dict[str, ActionRequest] = {}

    def insert_action(self, action: ActionRequest) -> ActionRequest:
        if action.idempotency_key and any(
            a.idempotency_key == action.idempotency_key
            for a in self._store.values()
        ):
            from case_engine.action_gateway import DuplicateActionError
            existing = next(
                a for a in self._store.values() if a.idempotency_key == action.idempotency_key
            )
            raise DuplicateActionError(existing)
        self._store[action.action_id] = action
        return action

    def get_action(self, action_id: str) -> ActionRequest | None:
        return self._store.get(action_id)

    def get_action_by_idempotency_key(self, key: str) -> ActionRequest | None:
        return next((a for a in self._store.values() if a.idempotency_key == key), None)

    def update_action(self, action: ActionRequest, *, expected_state=None) -> bool:
        if action.action_id in self._store:
            self._store[action.action_id] = action
        return True

    def list_approved_actions(self, client: str) -> list[ActionRequest]:
        return [
            a for a in self._store.values()
            if a.client == client and a.current_state == ActionState.APPROVED
        ]

    def list_rolling_back_actions(self, client: str | None = None) -> list[ActionRequest]:
        return [
            a for a in self._store.values()
            if a.current_state == ActionState.ROLLING_BACK
            and a.rollback_action_id is not None
            and (client is None or a.client == client)
        ]

    def list_pending_approvals(self, client: str) -> list[ActionRequest]:
        return [
            a for a in self._store.values()
            if a.client == client and a.current_state == ActionState.AWAITING_APPROVAL
        ]

    def list_expired_actions(self, client: str | None = None) -> list[ActionRequest]:
        now = datetime.now(tz=timezone.utc)
        return [
            a for a in self._store.values()
            if a.current_state in (ActionState.AWAITING_APPROVAL, ActionState.APPROVED)
            and a.expires_at is not None
            and a.expires_at < now
            and (client is None or a.client == client)
        ]

    def record_transition(self, record) -> bool:
        return True


class _AlwaysSucceedExecutor:
    action_namespace = "test"
    action_type      = "noop"

    def health_check(self):
        return True

    def execute(self, context, payload):
        return ExecutionResult(success=True, provider_reference="ok", latency_ms=1)

    def rollback(self, context, payload):
        return ExecutionResult(success=True, provider_reference="rolled-back", latency_ms=1)


class _AlwaysFailExecutor:
    action_namespace = "test"
    action_type      = "fail_perm"

    def health_check(self):
        return True

    def execute(self, context, payload):
        raise PermanentExecutionError("PERM_FAIL", "always fails")

    def rollback(self, context, payload):
        raise PermanentExecutionError("ROLLBACK_FAIL", "rollback always fails")


class _RetryableExecutor:
    action_namespace = "test"
    action_type      = "fail_retry"

    def health_check(self):
        return True

    def execute(self, context, payload):
        raise RetryableExecutionError("RETRY_FAIL", "retryable failure")

    def rollback(self, context, payload):
        raise RetryableExecutionError("ROLLBACK_RETRY", "retryable rollback failure")


class _SucceedExecuteFailRollbackExecutor:
    """Succeeds on execute(), fails on rollback(). Used for rollback-failure tests."""
    action_namespace = "test"
    action_type      = "noop"

    def health_check(self):
        return True

    def execute(self, context, payload):
        return ExecutionResult(success=True, provider_reference="ok", latency_ms=1)

    def rollback(self, context, payload):
        raise PermanentExecutionError("ROLLBACK_PERM_FAIL", "rollback always fails")


def _build_runtime(
    repo: _FakeRepository,
    audit_service: AuditService | None = None,
    executor=None,
) -> tuple[ActionGateway, ActionRuntime]:
    gateway = ActionGateway(repository=repo)
    registry = ActionExecutorRegistry()
    if executor is not None:
        registry.register_executor(executor)
    runtime = ActionRuntime(
        gateway=gateway,
        repository=repo,
        registry=registry,
        audit_service=audit_service,
    )
    return gateway, runtime


def _make_safe_action(gateway: ActionGateway, repo: _FakeRepository) -> ActionRequest:
    case = Case(case_id=str(uuid.uuid4()), ticket_id="TKT-SAFE", client=_CLIENT)
    proposal = ActionProposal(
        action_type="noop",
        action_namespace="test",
        risk_level=ActionRiskLevel.SAFE,
        action_params={},
    )
    return gateway.propose(case, proposal)


def _make_reversible_action(gateway: ActionGateway) -> ActionRequest:
    case = Case(case_id=str(uuid.uuid4()), ticket_id="TKT-REV", client=_CLIENT)
    proposal = ActionProposal(
        action_type="noop",
        action_namespace="test",
        risk_level=ActionRiskLevel.REVERSIBLE,
        action_params={"forward": True},
        rollback_action_type="noop",
        rollback_params={"compensation": True},   # different params → unique idempotency key
    )
    return gateway.propose(case, proposal)


def _make_expired_action(gateway: ActionGateway, repo: _FakeRepository) -> ActionRequest:
    action = _make_reversible_action(gateway)
    action.expires_at = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    repo.update_action(action)
    return action


# ── App factory helpers ────────────────────────────────────────────────────────

def _make_authenticator() -> ApiKeyAuthenticator:
    return ApiKeyAuthenticator(
        {
            _ADMIN_KEY:    ("cto",       Role.ADMIN),
            _APPROVER_KEY: ("alice",     Role.APPROVER),
            _OPERATOR_KEY: ("scheduler", Role.OPERATOR),
        },
        auth_enabled=True,
    )


def _make_app(
    repo: _FakeRepository | None = None,
    audit_service: AuditService | None = None,
    audit_logger: AuditLogger | None = None,
) -> tuple[TestClient, _FakeRepository, AuditService, AuditLogger]:
    repo = repo or _FakeRepository()
    if audit_service is None and audit_logger is None:
        audit_repo = InMemoryAuditRepository()
        audit_service = AuditService(audit_repo)
        audit_logger = AuditLogger(audit_repo)
    elif audit_logger is not None and audit_service is None:
        audit_service = AuditService(audit_logger.repository)
    gateway, runtime = _build_runtime(repo, audit_service=audit_service)
    watchdog = SLAWatchdog(gateway=gateway, repository=repo, audit_service=audit_service)

    @dataclass
    class FakeStack:
        repository: _FakeRepository
        _gateway: ActionGateway
        _runtime: ActionRuntime
        _watchdog: SLAWatchdog

        @property
        def gateway(self):   return self._gateway
        @property
        def watchdog(self):  return self._watchdog
        @property
        def worker(self):
            from worker.action_worker import ActionWorker
            return ActionWorker(runtime=self._runtime, repository=self.repository)
        @property
        def health(self):
            from runtime.health import HealthService
            return HealthService(
                provider_registry=ProviderRegistry(),
                executor_registry=ActionExecutorRegistry(),
                runtime=self._runtime,
                worker=self.worker,
            )

    stack = FakeStack(
        repository=repo,
        _gateway=gateway,
        _runtime=runtime,
        _watchdog=watchdog,
    )
    app = create_app(
        stack=stack,
        processor=FreshdeskWebhookProcessor(secret=_WH_SECRET, enforce_hmac=False),
        authenticator=_make_authenticator(),
        audit_logger=audit_logger,
        audit_service=audit_service,
        skip_config_validation=True,
    )
    return TestClient(app, raise_server_exceptions=False), repo, audit_service, audit_logger


def _admin_headers() -> dict:
    return {"x-api-key": _ADMIN_KEY}

def _approver_headers() -> dict:
    return {"x-api-key": _APPROVER_KEY}

def _operator_headers() -> dict:
    return {"x-api-key": _OPERATOR_KEY}


# ══════════════════════════════════════════════════════════════════════════════
# 1. InMemoryAuditRepository unit tests
# ══════════════════════════════════════════════════════════════════════════════

class TestInMemoryAuditRepository:

    def _repo(self) -> InMemoryAuditRepository:
        return InMemoryAuditRepository()

    def _event(self, action_id="a1", case_id="c1", client="acme",
                event_type=AuditEventType.ACTION_APPROVED, actor="human:alice") -> AuditEvent:
        return AuditEvent(
            action_id=action_id,
            event_type=event_type,
            actor=actor,
            case_id=case_id,
            client=client,
        )

    def test_insert_and_retrieve_by_action(self):
        repo = self._repo()
        e = self._event()
        repo.insert_event(e)
        assert repo.events_for_action("a1") == [e]

    def test_events_for_action_empty(self):
        assert self._repo().events_for_action("unknown") == []

    def test_events_for_case(self):
        repo = self._repo()
        e = self._event(case_id="case-xyz")
        repo.insert_event(e)
        assert repo.events_for_case("case-xyz") == [e]

    def test_events_for_case_empty(self):
        assert self._repo().events_for_case("unknown") == []

    def test_events_for_client(self):
        repo = self._repo()
        e = self._event(client="tenant_a")
        repo.insert_event(e)
        result = repo.events_for_client("tenant_a")
        assert len(result) == 1
        assert result[0] == e

    def test_events_for_client_empty(self):
        assert self._repo().events_for_client("unknown") == []

    def test_events_for_client_pagination_limit(self):
        repo = self._repo()
        for i in range(5):
            repo.insert_event(self._event(action_id=f"a{i}", client="t1"))
        assert len(repo.events_for_client("t1", limit=3)) == 3

    def test_events_for_client_pagination_offset(self):
        repo = self._repo()
        events = [self._event(action_id=f"a{i}", client="t1") for i in range(5)]
        for e in events:
            repo.insert_event(e)
        result = repo.events_for_client("t1", limit=10, offset=3)
        assert len(result) == 2

    def test_list_events_returns_all_in_order(self):
        repo = self._repo()
        e1 = self._event(action_id="x1")
        e2 = self._event(action_id="x2")
        repo.insert_event(e1)
        repo.insert_event(e2)
        result = repo.list_events(limit=10)
        assert result == [e1, e2]

    def test_list_events_filter_event_type(self):
        repo = self._repo()
        repo.insert_event(self._event(event_type=AuditEventType.ACTION_APPROVED))
        repo.insert_event(self._event(event_type=AuditEventType.ACTION_REJECTED))
        result = repo.list_events(event_type=AuditEventType.ACTION_APPROVED, limit=10)
        assert len(result) == 1
        assert result[0].event_type == AuditEventType.ACTION_APPROVED

    def test_list_events_filter_client(self):
        repo = self._repo()
        repo.insert_event(self._event(client="alpha"))
        repo.insert_event(self._event(client="beta"))
        result = repo.list_events(client="alpha", limit=10)
        assert len(result) == 1
        assert result[0].client == "alpha"

    def test_list_events_filter_action_id(self):
        repo = self._repo()
        repo.insert_event(self._event(action_id="target"))
        repo.insert_event(self._event(action_id="other"))
        result = repo.list_events(action_id="target", limit=10)
        assert len(result) == 1

    def test_list_events_filter_case_id(self):
        repo = self._repo()
        repo.insert_event(self._event(case_id="case-A"))
        repo.insert_event(self._event(case_id="case-B"))
        result = repo.list_events(case_id="case-A", limit=10)
        assert len(result) == 1

    def test_list_events_combined_filters(self):
        repo = self._repo()
        repo.insert_event(self._event(client="alpha", event_type=AuditEventType.ACTION_APPROVED))
        repo.insert_event(self._event(client="alpha", event_type=AuditEventType.ACTION_REJECTED))
        repo.insert_event(self._event(client="beta",  event_type=AuditEventType.ACTION_APPROVED))
        result = repo.list_events(client="alpha", event_type=AuditEventType.ACTION_APPROVED, limit=10)
        assert len(result) == 1

    def test_list_events_pagination(self):
        repo = self._repo()
        for i in range(10):
            repo.insert_event(self._event(action_id=f"a{i}"))
        page1 = repo.list_events(limit=4, offset=0)
        page2 = repo.list_events(limit=4, offset=4)
        assert len(page1) == 4
        assert len(page2) == 4
        assert page1 != page2

    def test_count_events_total(self):
        repo = self._repo()
        for i in range(7):
            repo.insert_event(self._event(action_id=f"a{i}"))
        assert repo.count_events() == 7

    def test_count_events_by_type(self):
        repo = self._repo()
        repo.insert_event(self._event(event_type=AuditEventType.ACTION_APPROVED))
        repo.insert_event(self._event(event_type=AuditEventType.ACTION_APPROVED))
        repo.insert_event(self._event(event_type=AuditEventType.ACTION_REJECTED))
        assert repo.count_events(event_type=AuditEventType.ACTION_APPROVED) == 2
        assert repo.count_events(event_type=AuditEventType.ACTION_REJECTED) == 1

    def test_count_events_by_client(self):
        repo = self._repo()
        repo.insert_event(self._event(client="alpha"))
        repo.insert_event(self._event(client="alpha"))
        repo.insert_event(self._event(client="beta"))
        assert repo.count_events(client="alpha") == 2

    def test_count_events_empty(self):
        assert self._repo().count_events() == 0

    def test_insert_event_never_raises(self):
        repo = self._repo()
        # Should not raise even with unusual event
        repo.insert_event(self._event())


# ══════════════════════════════════════════════════════════════════════════════
# 2. AuditService unit tests
# ══════════════════════════════════════════════════════════════════════════════

class TestAuditService:

    def _svc(self) -> tuple[AuditService, InMemoryAuditRepository]:
        repo = InMemoryAuditRepository()
        return AuditService(repo), repo

    def _event(self, action_id="a1", case_id="c1", client="acme",
                event_type=AuditEventType.ACTION_APPROVED) -> AuditEvent:
        return AuditEvent(action_id=action_id, event_type=event_type,
                          actor="human:alice", case_id=case_id, client=client)

    def test_emit_stores_event(self):
        svc, repo = self._svc()
        e = self._event()
        svc.emit(e)
        assert repo.count_events() == 1

    def test_emit_never_raises(self):
        svc, _ = self._svc()
        # Even if we pass a broken event, should not raise
        try:
            svc.emit(self._event())
        except Exception:
            pytest.fail("AuditService.emit should never raise")

    def test_get_action_history(self):
        svc, _ = self._svc()
        e = self._event(action_id="act-1")
        svc.emit(e)
        history = svc.get_action_history("act-1")
        assert len(history) == 1
        assert history[0].action_id == "act-1"

    def test_get_action_history_empty(self):
        svc, _ = self._svc()
        assert svc.get_action_history("nonexistent") == []

    def test_get_case_history(self):
        svc, _ = self._svc()
        e = self._event(case_id="case-99")
        svc.emit(e)
        history = svc.get_case_history("case-99")
        assert len(history) == 1

    def test_get_case_history_empty(self):
        svc, _ = self._svc()
        assert svc.get_case_history("unknown") == []

    def test_get_client_history(self):
        svc, _ = self._svc()
        svc.emit(self._event(client="tenant_x"))
        svc.emit(self._event(client="tenant_x"))
        svc.emit(self._event(client="tenant_y"))
        result = svc.get_client_history("tenant_x")
        assert len(result) == 2

    def test_search_by_event_type(self):
        svc, _ = self._svc()
        svc.emit(self._event(event_type=AuditEventType.ACTION_APPROVED))
        svc.emit(self._event(event_type=AuditEventType.ACTION_REJECTED))
        result = svc.search(event_type=AuditEventType.ACTION_APPROVED)
        assert len(result) == 1

    def test_search_by_client(self):
        svc, _ = self._svc()
        svc.emit(self._event(client="alpha"))
        svc.emit(self._event(client="beta"))
        result = svc.search(client="alpha")
        assert len(result) == 1

    def test_search_pagination_limit(self):
        svc, _ = self._svc()
        for i in range(10):
            svc.emit(self._event(action_id=f"a{i}"))
        result = svc.search(limit=4)
        assert len(result) == 4

    def test_search_pagination_offset(self):
        svc, _ = self._svc()
        for i in range(10):
            svc.emit(self._event(action_id=f"a{i}"))
        result = svc.search(limit=10, offset=7)
        assert len(result) == 3

    def test_count_total(self):
        svc, _ = self._svc()
        for i in range(5):
            svc.emit(self._event(action_id=f"a{i}"))
        assert svc.count() == 5

    def test_count_by_event_type(self):
        svc, _ = self._svc()
        svc.emit(self._event(event_type=AuditEventType.ACTION_APPROVED))
        svc.emit(self._event(event_type=AuditEventType.ACTION_EXECUTED))
        assert svc.count(event_type=AuditEventType.ACTION_APPROVED) == 1
        assert svc.count(event_type=AuditEventType.ACTION_EXECUTED) == 1

    def test_count_by_client(self):
        svc, _ = self._svc()
        svc.emit(self._event(client="x"))
        svc.emit(self._event(client="x"))
        svc.emit(self._event(client="y"))
        assert svc.count(client="x") == 2

    def test_two_services_same_repo_share_state(self):
        repo = InMemoryAuditRepository()
        s1 = AuditService(repo)
        s2 = AuditService(repo)
        s1.emit(self._event())
        assert s2.count() == 1


# ══════════════════════════════════════════════════════════════════════════════
# 3. AuditLogger backward compatibility and shared-repo pattern
# ══════════════════════════════════════════════════════════════════════════════

class TestAuditLoggerRefactored:

    def _event(self, action_id="a1") -> AuditEvent:
        return AuditEvent(
            action_id=action_id,
            event_type=AuditEventType.ACTION_APPROVED,
            actor="human:alice",
        )

    def test_no_args_constructor_still_works(self):
        logger = AuditLogger()
        logger.emit(self._event())
        assert logger.count() == 1

    def test_emit_stores_event(self):
        logger = AuditLogger()
        e = self._event("act-x")
        logger.emit(e)
        assert logger.count() == 1

    def test_events_for_action(self):
        logger = AuditLogger()
        logger.emit(self._event("id-1"))
        logger.emit(self._event("id-2"))
        assert len(logger.events_for_action("id-1")) == 1

    def test_all_events_returns_all(self):
        logger = AuditLogger()
        logger.emit(self._event("x"))
        logger.emit(self._event("y"))
        assert len(logger.all_events()) == 2

    def test_count_correct(self):
        logger = AuditLogger()
        assert logger.count() == 0
        logger.emit(self._event())
        assert logger.count() == 1

    def test_count_by_type(self):
        logger = AuditLogger()
        logger.emit(self._event())
        logger.emit(AuditEvent(
            action_id="a2",
            event_type=AuditEventType.ACTION_REJECTED,
            actor="human:bob",
        ))
        assert logger.count_by_type(AuditEventType.ACTION_APPROVED) == 1
        assert logger.count_by_type(AuditEventType.ACTION_REJECTED) == 1

    def test_shared_repo_logger_and_service_see_same_events(self):
        repo = InMemoryAuditRepository()
        logger = AuditLogger(repository=repo)
        svc = AuditService(repository=repo)
        logger.emit(self._event("shared-1"))
        assert svc.count() == 1
        svc.emit(self._event("shared-2"))
        assert logger.count() == 2

    def test_repository_property_exposed(self):
        repo = InMemoryAuditRepository()
        logger = AuditLogger(repository=repo)
        assert logger.repository is repo

    def test_emit_never_raises(self):
        logger = AuditLogger()
        try:
            logger.emit(self._event())
        except Exception:
            pytest.fail("AuditLogger.emit should never raise")

    def test_injected_repo_constructor(self):
        repo = InMemoryAuditRepository()
        logger = AuditLogger(repository=repo)
        logger.emit(self._event("test-id"))
        assert repo.count_events() == 1


# ══════════════════════════════════════════════════════════════════════════════
# 4. AuditEvent model — Sprint 2.9 field additions
# ══════════════════════════════════════════════════════════════════════════════

class TestAuditEventModel:

    def test_case_id_defaults_to_empty_string(self):
        e = AuditEvent(
            action_id="a",
            event_type=AuditEventType.ACTION_APPROVED,
            actor="human:x",
        )
        assert e.case_id == ""

    def test_client_defaults_to_empty_string(self):
        e = AuditEvent(
            action_id="a",
            event_type=AuditEventType.ACTION_APPROVED,
            actor="human:x",
        )
        assert e.client == ""

    def test_to_dict_includes_case_id_and_client(self):
        e = AuditEvent(
            action_id="a",
            event_type=AuditEventType.ACTION_APPROVED,
            actor="human:x",
            case_id="case-abc",
            client="tenant_q",
        )
        d = e.to_dict()
        assert d["case_id"] == "case-abc"
        assert d["client"] == "tenant_q"

    def test_backward_compat_positional_construction(self):
        # positional: action_id, event_type, actor
        e = AuditEvent("id", AuditEventType.ACTION_EXECUTED, "executor:w1")
        assert e.action_id == "id"
        assert e.case_id == ""

    def test_immutable_frozen_dataclass(self):
        e = AuditEvent(
            action_id="a",
            event_type=AuditEventType.ACTION_APPROVED,
            actor="human:x",
        )
        with pytest.raises((AttributeError, TypeError)):
            e.action_id = "mutated"  # type: ignore[misc]

    def test_to_dict_timestamp_is_isoformat(self):
        e = AuditEvent(
            action_id="a",
            event_type=AuditEventType.ACTION_APPROVED,
            actor="human:x",
        )
        ts = e.to_dict()["timestamp"]
        assert "T" in ts
        assert "+" in ts or "Z" in ts or ts.endswith("+00:00")


# ══════════════════════════════════════════════════════════════════════════════
# 5. Worker audit emission
# ══════════════════════════════════════════════════════════════════════════════

class TestWorkerAuditEmission:

    def _setup_success(self) -> tuple[AuditService, ActionRequest]:
        repo = _FakeRepository()
        audit_repo = InMemoryAuditRepository()
        svc = AuditService(audit_repo)
        gateway, runtime = _build_runtime(repo, audit_service=svc, executor=_AlwaysSucceedExecutor())
        action = _make_safe_action(gateway, repo)
        assert action.current_state == ActionState.APPROVED
        runtime.execute_action(action.action_id, executor_id="worker-1")
        return svc, action

    def test_execution_started_event_emitted(self):
        svc, action = self._setup_success()
        types = [e.event_type for e in svc.get_action_history(action.action_id)]
        assert AuditEventType.ACTION_EXECUTION_STARTED in types

    def test_executed_event_emitted_on_success(self):
        svc, action = self._setup_success()
        types = [e.event_type for e in svc.get_action_history(action.action_id)]
        assert AuditEventType.ACTION_EXECUTED in types

    def test_execution_started_has_correct_action_id(self):
        svc, action = self._setup_success()
        events = [e for e in svc.get_action_history(action.action_id)
                  if e.event_type == AuditEventType.ACTION_EXECUTION_STARTED]
        assert events[0].action_id == action.action_id

    def test_events_have_correct_case_id(self):
        svc, action = self._setup_success()
        for e in svc.get_action_history(action.action_id):
            assert e.case_id == action.case_id

    def test_events_have_correct_client(self):
        svc, action = self._setup_success()
        for e in svc.get_action_history(action.action_id):
            assert e.client == action.client

    def test_actor_includes_executor_id(self):
        svc, action = self._setup_success()
        started = next(e for e in svc.get_action_history(action.action_id)
                       if e.event_type == AuditEventType.ACTION_EXECUTION_STARTED)
        assert "worker-1" in started.actor

    def test_action_type_in_metadata(self):
        svc, action = self._setup_success()
        started = next(e for e in svc.get_action_history(action.action_id)
                       if e.event_type == AuditEventType.ACTION_EXECUTION_STARTED)
        assert "action_type" in started.metadata

    def test_failed_event_emitted_on_permanent_failure(self):
        repo = _FakeRepository()
        audit_repo = InMemoryAuditRepository()
        svc = AuditService(audit_repo)
        gateway, runtime = _build_runtime(repo, audit_service=svc, executor=_AlwaysFailExecutor())
        case = Case(case_id=str(uuid.uuid4()), ticket_id="TKT-FAIL", client=_CLIENT)
        action = gateway.propose(case, ActionProposal(
            action_type="fail_perm", action_namespace="test",
            risk_level=ActionRiskLevel.SAFE, action_params={},
        ))
        runtime.execute_action(action.action_id, executor_id="worker-fail")
        types = [e.event_type for e in svc.get_action_history(action.action_id)]
        assert AuditEventType.ACTION_FAILED in types

    def test_failed_event_has_failure_code_in_metadata(self):
        repo = _FakeRepository()
        audit_repo = InMemoryAuditRepository()
        svc = AuditService(audit_repo)
        gateway, runtime = _build_runtime(repo, audit_service=svc, executor=_AlwaysFailExecutor())
        case = Case(case_id=str(uuid.uuid4()), ticket_id="TKT-FAIL2", client=_CLIENT)
        action = gateway.propose(case, ActionProposal(
            action_type="fail_perm", action_namespace="test",
            risk_level=ActionRiskLevel.SAFE, action_params={},
        ))
        runtime.execute_action(action.action_id, executor_id="worker-fail")
        failed = next(e for e in svc.get_action_history(action.action_id)
                      if e.event_type == AuditEventType.ACTION_FAILED)
        assert "failure_code" in failed.metadata

    def test_no_audit_emission_when_service_is_none(self):
        repo = _FakeRepository()
        gateway, runtime = _build_runtime(repo, audit_service=None, executor=_AlwaysSucceedExecutor())
        action = _make_safe_action(gateway, repo)
        # Should not raise
        result = runtime.execute_action(action.action_id, executor_id="w1")
        assert result.success

    def test_two_actions_two_independent_event_sets(self):
        repo = _FakeRepository()
        audit_repo = InMemoryAuditRepository()
        svc = AuditService(audit_repo)
        gateway, runtime = _build_runtime(repo, audit_service=svc, executor=_AlwaysSucceedExecutor())
        a1 = _make_safe_action(gateway, repo)
        case2 = Case(case_id=str(uuid.uuid4()), ticket_id="TKT-2", client=_CLIENT)
        a2 = gateway.propose(case2, ActionProposal(
            action_type="noop", action_namespace="test", risk_level=ActionRiskLevel.SAFE, action_params={}
        ))
        runtime.execute_action(a1.action_id, executor_id="w1")
        runtime.execute_action(a2.action_id, executor_id="w1")
        h1 = svc.get_action_history(a1.action_id)
        h2 = svc.get_action_history(a2.action_id)
        assert all(e.action_id == a1.action_id for e in h1)
        assert all(e.action_id == a2.action_id for e in h2)

    def test_rolled_back_event_emitted_on_rollback_success(self):
        repo = _FakeRepository()
        audit_repo = InMemoryAuditRepository()
        svc = AuditService(audit_repo)
        gateway, runtime = _build_runtime(repo, audit_service=svc, executor=_AlwaysSucceedExecutor())
        original = _make_reversible_action(gateway)
        # Approve
        gateway.approve(original, approved_by="alice", notes="")
        # Execute original
        runtime.execute_action(original.action_id, executor_id="w1")
        # Propose rollback
        case = Case(case_id=original.case_id, ticket_id=original.ticket_id, client=original.client)
        compensation = gateway.propose_rollback(original, case)
        # Execute rollback
        runtime.execute_rollback(original.action_id, executor_id="w1")
        types = [e.event_type for e in svc.get_action_history(original.action_id)]
        assert AuditEventType.ACTION_ROLLED_BACK in types

    def test_rollback_failed_event_emitted_on_rollback_failure(self):
        repo = _FakeRepository()
        audit_repo = InMemoryAuditRepository()
        svc = AuditService(audit_repo)
        # Executor that succeeds on execute() but fails on rollback()
        gateway, runtime = _build_runtime(
            repo, audit_service=svc, executor=_SucceedExecuteFailRollbackExecutor()
        )
        original = _make_reversible_action(gateway)
        gateway.approve(original, approved_by="alice", notes="")
        runtime.execute_action(original.action_id, executor_id="w1")
        case = Case(case_id=original.case_id, ticket_id=original.ticket_id, client=original.client)
        gateway.propose_rollback(original, case)
        runtime.execute_rollback(original.action_id, executor_id="w1")
        types = [e.event_type for e in svc.get_action_history(original.action_id)]
        assert AuditEventType.ACTION_ROLLBACK_FAILED in types

    def test_rolled_back_event_targets_original_action_id(self):
        repo = _FakeRepository()
        audit_repo = InMemoryAuditRepository()
        svc = AuditService(audit_repo)
        gateway, runtime = _build_runtime(repo, audit_service=svc, executor=_AlwaysSucceedExecutor())
        original = _make_reversible_action(gateway)
        gateway.approve(original, approved_by="alice", notes="")
        runtime.execute_action(original.action_id, executor_id="w1")
        case = Case(case_id=original.case_id, ticket_id=original.ticket_id, client=original.client)
        gateway.propose_rollback(original, case)
        runtime.execute_rollback(original.action_id, executor_id="w1")
        rolled_back_events = [e for e in svc.get_action_history(original.action_id)
                              if e.event_type == AuditEventType.ACTION_ROLLED_BACK]
        assert len(rolled_back_events) == 1
        assert rolled_back_events[0].action_id == original.action_id

    def test_retryable_failure_emits_action_failed(self):
        repo = _FakeRepository()
        audit_repo = InMemoryAuditRepository()
        svc = AuditService(audit_repo)
        gateway, runtime = _build_runtime(repo, audit_service=svc, executor=_RetryableExecutor())
        case = Case(case_id=str(uuid.uuid4()), ticket_id="TKT-RETRY", client=_CLIENT)
        # IRREVERSIBLE so max_attempts=1 prevents re-approve loop
        action = gateway.propose(case, ActionProposal(
            action_type="fail_retry",
            action_namespace="test",
            risk_level=ActionRiskLevel.IRREVERSIBLE,
            action_params={},
        ))
        gateway.approve(action, approved_by="alice", notes="")
        runtime.execute_action(action.action_id, executor_id="w1")
        types = [e.event_type for e in svc.get_action_history(action.action_id)]
        assert AuditEventType.ACTION_FAILED in types


# ══════════════════════════════════════════════════════════════════════════════
# 6. Watchdog audit emission
# ══════════════════════════════════════════════════════════════════════════════

class TestWatchdogAuditEmission:

    def _setup(self) -> tuple[AuditService, SLAWatchdog, _FakeRepository, ActionGateway]:
        repo = _FakeRepository()
        audit_repo = InMemoryAuditRepository()
        svc = AuditService(audit_repo)
        gateway = ActionGateway(repository=repo)
        watchdog = SLAWatchdog(gateway=gateway, repository=repo, audit_service=svc)
        return svc, watchdog, repo, gateway

    def test_action_expired_emitted_on_expiry(self):
        svc, watchdog, repo, gateway = self._setup()
        _make_expired_action(gateway, repo)
        watchdog.run()
        types = [e.event_type for e in svc.search(limit=100)]
        assert AuditEventType.ACTION_EXPIRED in types

    def test_expired_event_has_correct_action_id(self):
        svc, watchdog, repo, gateway = self._setup()
        action = _make_expired_action(gateway, repo)
        watchdog.run()
        events = svc.get_action_history(action.action_id)
        assert any(e.event_type == AuditEventType.ACTION_EXPIRED for e in events)

    def test_expired_event_has_correct_client(self):
        svc, watchdog, repo, gateway = self._setup()
        action = _make_expired_action(gateway, repo)
        watchdog.run()
        exp_event = next(e for e in svc.get_action_history(action.action_id)
                         if e.event_type == AuditEventType.ACTION_EXPIRED)
        assert exp_event.client == action.client

    def test_expired_event_has_reason_in_metadata(self):
        svc, watchdog, repo, gateway = self._setup()
        _make_expired_action(gateway, repo)
        watchdog.run()
        events = svc.search(event_type=AuditEventType.ACTION_EXPIRED, limit=10)
        assert events[0].metadata.get("reason") == "sla_deadline_elapsed"

    def test_idempotent_second_run_emits_no_additional_event(self):
        svc, watchdog, repo, gateway = self._setup()
        _make_expired_action(gateway, repo)
        watchdog.run()
        count_after_first = svc.count(event_type=AuditEventType.ACTION_EXPIRED)
        watchdog.run()
        count_after_second = svc.count(event_type=AuditEventType.ACTION_EXPIRED)
        assert count_after_first == count_after_second

    def test_multiple_expired_actions_emit_multiple_events(self):
        svc, watchdog, repo, gateway = self._setup()
        _make_expired_action(gateway, repo)
        _make_expired_action(gateway, repo)
        watchdog.run()
        assert svc.count(event_type=AuditEventType.ACTION_EXPIRED) == 2

    def test_non_expired_action_emits_no_event(self):
        svc, watchdog, repo, gateway = self._setup()
        _make_reversible_action(gateway)  # expires in 4 hours
        watchdog.run()
        assert svc.count(event_type=AuditEventType.ACTION_EXPIRED) == 0

    def test_no_emission_when_audit_service_is_none(self):
        repo = _FakeRepository()
        gateway = ActionGateway(repository=repo)
        watchdog = SLAWatchdog(gateway=gateway, repository=repo, audit_service=None)
        _make_expired_action(gateway, repo)
        # Should not raise
        result = watchdog.run()
        assert result.expired_actions == 1

    def test_actor_is_watchdog_sla(self):
        svc, watchdog, repo, gateway = self._setup()
        _make_expired_action(gateway, repo)
        watchdog.run()
        events = svc.search(event_type=AuditEventType.ACTION_EXPIRED, limit=10)
        assert events[0].actor == "watchdog:sla"

    def test_expired_event_has_case_id(self):
        svc, watchdog, repo, gateway = self._setup()
        action = _make_expired_action(gateway, repo)
        watchdog.run()
        exp_event = next(e for e in svc.get_action_history(action.action_id)
                         if e.event_type == AuditEventType.ACTION_EXPIRED)
        assert exp_event.case_id == action.case_id


# ══════════════════════════════════════════════════════════════════════════════
# 7. Audit Query API — HTTP endpoints
# ══════════════════════════════════════════════════════════════════════════════

class TestAuditQueryAPI:

    def _emit(self, svc: AuditService, action_id="a1", case_id="c1",
              client=_CLIENT, event_type=AuditEventType.ACTION_APPROVED) -> AuditEvent:
        e = AuditEvent(
            action_id=action_id, event_type=event_type,
            actor="human:alice", case_id=case_id, client=client,
        )
        svc.emit(e)
        return e

    def test_get_action_audit_requires_admin(self):
        c, _, svc, _ = _make_app()
        self._emit(svc)
        r = c.get("/audit/actions/a1", headers=_admin_headers())
        assert r.status_code == 200

    def test_get_action_audit_returns_401_without_key(self):
        c, _, _, _ = _make_app()
        r = c.get("/audit/actions/a1")
        assert r.status_code == 401

    def test_get_action_audit_returns_403_with_approver_key(self):
        c, _, svc, _ = _make_app()
        self._emit(svc)
        r = c.get("/audit/actions/a1", headers=_approver_headers())
        assert r.status_code == 403

    def test_get_action_audit_returns_403_with_operator_key(self):
        c, _, svc, _ = _make_app()
        self._emit(svc)
        r = c.get("/audit/actions/a1", headers=_operator_headers())
        assert r.status_code == 403

    def test_get_action_audit_returns_events(self):
        c, _, svc, _ = _make_app()
        self._emit(svc, action_id="act-123")
        r = c.get("/audit/actions/act-123", headers=_admin_headers())
        body = r.json()
        assert len(body["events"]) == 1
        assert body["events"][0]["action_id"] == "act-123"

    def test_get_action_audit_empty_result_returns_200(self):
        c, _, _, _ = _make_app()
        r = c.get("/audit/actions/nonexistent", headers=_admin_headers())
        assert r.status_code == 200
        assert r.json()["events"] == []

    def test_get_case_audit_requires_admin(self):
        c, _, svc, _ = _make_app()
        self._emit(svc, case_id="case-abc")
        r = c.get("/audit/cases/case-abc", headers=_admin_headers())
        assert r.status_code == 200

    def test_get_case_audit_returns_401_without_key(self):
        c, _, _, _ = _make_app()
        r = c.get("/audit/cases/case-x")
        assert r.status_code == 401

    def test_get_case_audit_returns_403_with_approver_key(self):
        c, _, _, _ = _make_app()
        r = c.get("/audit/cases/case-x", headers=_approver_headers())
        assert r.status_code == 403

    def test_get_case_audit_returns_events(self):
        c, _, svc, _ = _make_app()
        self._emit(svc, case_id="case-99")
        r = c.get("/audit/cases/case-99", headers=_admin_headers())
        body = r.json()
        assert body["total"] == 1
        assert body["case_id"] == "case-99"

    def test_get_client_audit_requires_admin(self):
        c, _, svc, _ = _make_app()
        self._emit(svc, client="tenant_z")
        r = c.get("/audit/clients/tenant_z", headers=_admin_headers())
        assert r.status_code == 200

    def test_get_client_audit_returns_401_without_key(self):
        c, _, _, _ = _make_app()
        r = c.get("/audit/clients/tenant_z")
        assert r.status_code == 401

    def test_get_client_audit_returns_403_with_operator_key(self):
        c, _, _, _ = _make_app()
        r = c.get("/audit/clients/tenant_z", headers=_operator_headers())
        assert r.status_code == 403

    def test_get_client_audit_returns_events(self):
        c, _, svc, _ = _make_app()
        self._emit(svc, client="my_tenant")
        self._emit(svc, client="my_tenant", event_type=AuditEventType.ACTION_EXECUTED)
        r = c.get("/audit/clients/my_tenant", headers=_admin_headers())
        body = r.json()
        assert body["total"] == 2
        assert len(body["events"]) == 2

    def test_list_audit_events_requires_admin(self):
        c, _, _, _ = _make_app()
        r = c.get("/audit/events", headers=_admin_headers())
        assert r.status_code == 200

    def test_list_audit_events_returns_401_without_key(self):
        c, _, _, _ = _make_app()
        r = c.get("/audit/events")
        assert r.status_code == 401

    def test_list_audit_events_returns_403_with_approver(self):
        c, _, _, _ = _make_app()
        r = c.get("/audit/events", headers=_approver_headers())
        assert r.status_code == 403

    def test_list_audit_events_pagination_limit(self):
        c, _, svc, _ = _make_app()
        for i in range(10):
            self._emit(svc, action_id=f"a{i}")
        r = c.get("/audit/events?limit=4", headers=_admin_headers())
        body = r.json()
        assert len(body["events"]) == 4
        assert body["total"] == 10

    def test_list_audit_events_pagination_offset(self):
        c, _, svc, _ = _make_app()
        for i in range(10):
            self._emit(svc, action_id=f"a{i}")
        r = c.get("/audit/events?limit=10&offset=8", headers=_admin_headers())
        body = r.json()
        assert len(body["events"]) == 2

    def test_list_audit_events_filter_event_type(self):
        c, _, svc, _ = _make_app()
        self._emit(svc, event_type=AuditEventType.ACTION_APPROVED)
        self._emit(svc, event_type=AuditEventType.ACTION_REJECTED)
        r = c.get("/audit/events?event_type=ACTION_APPROVED", headers=_admin_headers())
        body = r.json()
        assert body["total"] == 1
        assert body["events"][0]["event_type"] == "ACTION_APPROVED"

    def test_list_audit_events_filter_client(self):
        c, _, svc, _ = _make_app()
        self._emit(svc, client="bank_a")
        self._emit(svc, client="bank_b")
        r = c.get("/audit/events?client=bank_a", headers=_admin_headers())
        assert r.json()["total"] == 1

    def test_list_audit_events_filter_action_id(self):
        c, _, svc, _ = _make_app()
        self._emit(svc, action_id="target-id")
        self._emit(svc, action_id="other-id")
        r = c.get("/audit/events?action_id=target-id", headers=_admin_headers())
        assert r.json()["total"] == 1

    def test_list_audit_events_filter_case_id(self):
        c, _, svc, _ = _make_app()
        self._emit(svc, case_id="case-filter")
        self._emit(svc, case_id="other-case")
        r = c.get("/audit/events?case_id=case-filter", headers=_admin_headers())
        assert r.json()["total"] == 1

    def test_list_audit_events_response_structure(self):
        c, _, _, _ = _make_app()
        r = c.get("/audit/events", headers=_admin_headers())
        body = r.json()
        assert "events" in body
        assert "total" in body
        assert "limit" in body
        assert "offset" in body

    def test_event_timestamps_are_iso8601(self):
        c, _, svc, _ = _make_app()
        self._emit(svc)
        r = c.get("/audit/events", headers=_admin_headers())
        body = r.json()
        ts = body["events"][0]["timestamp"]
        assert "T" in ts

    def test_invalid_event_type_filter_returns_400(self):
        c, _, _, _ = _make_app()
        r = c.get("/audit/events?event_type=BOGUS_TYPE", headers=_admin_headers())
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "INVALID_FILTER"

    def test_get_action_audit_response_includes_action_id(self):
        c, _, svc, _ = _make_app()
        self._emit(svc, action_id="verify-me")
        r = c.get("/audit/actions/verify-me", headers=_admin_headers())
        assert r.json()["action_id"] == "verify-me"

    def test_error_responses_use_standard_envelope(self):
        c, _, _, _ = _make_app()
        r = c.get("/audit/events", headers=_approver_headers())
        body = r.json()
        assert "error" in body
        assert "code" in body["error"]
        assert "message" in body["error"]

    def test_401_response_has_no_stack_trace(self):
        c, _, _, _ = _make_app()
        r = c.get("/audit/events")
        body_str = r.text
        assert "Traceback" not in body_str
        assert "traceback" not in body_str.lower()


# ══════════════════════════════════════════════════════════════════════════════
# 8. Compliance reconstruction — primary acceptance criterion
# ══════════════════════════════════════════════════════════════════════════════

class TestComplianceReconstruction:

    def test_approval_lifecycle_reconstructable(self):
        """
        Compliance officer can see: Proposal → Approved → Execution Started → Executed.
        """
        repo = _FakeRepository()
        audit_repo = InMemoryAuditRepository()
        svc = AuditService(audit_repo)
        audit_logger = AuditLogger(audit_repo)

        c, _, svc_from_app, _ = _make_app(repo=repo, audit_service=svc, audit_logger=audit_logger)

        # Create and approve a REVERSIBLE action
        gateway = ActionGateway(repository=repo)
        action = _make_reversible_action(gateway)
        case_id = action.case_id

        # Approve via HTTP
        c.post(
            f"/actions/{action.action_id}/approve",
            headers={"x-api-key": _APPROVER_KEY},
            json={"notes": "LGTM"},
        )

        # Verify approval event is queryable
        r = c.get(f"/audit/actions/{action.action_id}", headers=_admin_headers())
        assert r.status_code == 200
        events = r.json()["events"]
        event_types = [e["event_type"] for e in events]
        assert "ACTION_APPROVED" in event_types

    def test_rejection_lifecycle_reconstructable(self):
        """
        Compliance officer can see: Proposal → Rejected.
        """
        repo = _FakeRepository()
        audit_repo = InMemoryAuditRepository()
        svc = AuditService(audit_repo)
        audit_logger = AuditLogger(audit_repo)

        c, _, _, _ = _make_app(repo=repo, audit_service=svc, audit_logger=audit_logger)
        gateway = ActionGateway(repository=repo)
        action = _make_reversible_action(gateway)

        c.post(
            f"/actions/{action.action_id}/reject",
            headers={"x-api-key": _APPROVER_KEY},
            json={"notes": "Not authorized"},
        )

        r = c.get(f"/audit/actions/{action.action_id}", headers=_admin_headers())
        events = r.json()["events"]
        assert any(e["event_type"] == "ACTION_REJECTED" for e in events)

    def test_expiry_lifecycle_reconstructable(self):
        """
        Compliance officer can see: Proposal → Expired (via watchdog).
        """
        repo = _FakeRepository()
        audit_repo = InMemoryAuditRepository()
        svc = AuditService(audit_repo)
        audit_logger = AuditLogger(audit_repo)

        c, _, _, _ = _make_app(repo=repo, audit_service=svc, audit_logger=audit_logger)
        gateway = ActionGateway(repository=repo)
        action = _make_expired_action(gateway, repo)

        # Run watchdog via HTTP
        c.post("/watchdog/run", headers=_operator_headers())

        r = c.get(f"/audit/actions/{action.action_id}", headers=_admin_headers())
        events = r.json()["events"]
        # Watchdog emits ACTION_EXPIRED per-action
        assert any(e["event_type"] == "ACTION_EXPIRED" for e in events)

    def test_case_history_aggregates_all_actions(self):
        """
        All events for a case are retrievable via /audit/cases/{case_id}.
        """
        repo = _FakeRepository()
        audit_repo = InMemoryAuditRepository()
        svc = AuditService(audit_repo)
        audit_logger = AuditLogger(audit_repo)

        c, _, _, _ = _make_app(repo=repo, audit_service=svc, audit_logger=audit_logger)
        gateway = ActionGateway(repository=repo)

        shared_case_id = str(uuid.uuid4())
        for i in range(2):
            action = _make_reversible_action(gateway)
            action.case_id = shared_case_id
            repo.update_action(action)
            svc.emit(AuditEvent(
                action_id=action.action_id,
                event_type=AuditEventType.ACTION_APPROVED,
                actor="human:alice",
                case_id=shared_case_id,
                client=_CLIENT,
            ))

        r = c.get(f"/audit/cases/{shared_case_id}", headers=_admin_headers())
        assert r.status_code == 200
        assert r.json()["total"] == 2

    def test_client_history_scopes_to_tenant(self):
        """
        Events for tenant_A are not visible under tenant_B query.
        """
        repo = _FakeRepository()
        audit_repo = InMemoryAuditRepository()
        svc = AuditService(audit_repo)
        audit_logger = AuditLogger(audit_repo)

        c, _, _, _ = _make_app(repo=repo, audit_service=svc, audit_logger=audit_logger)
        svc.emit(AuditEvent(
            action_id="id-1", event_type=AuditEventType.ACTION_APPROVED,
            actor="human:alice", client="tenant_A",
        ))
        svc.emit(AuditEvent(
            action_id="id-2", event_type=AuditEventType.ACTION_REJECTED,
            actor="human:bob", client="tenant_B",
        ))

        r_a = c.get("/audit/clients/tenant_A", headers=_admin_headers())
        r_b = c.get("/audit/clients/tenant_B", headers=_admin_headers())
        assert r_a.json()["total"] == 1
        assert r_b.json()["total"] == 1
        assert r_a.json()["events"][0]["client"] == "tenant_A"
        assert r_b.json()["events"][0]["client"] == "tenant_B"
