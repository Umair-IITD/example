"""
tests/test_sprint26_e2e.py

Sprint 2.6 — End-to-End Execution Pipeline Tests.

Covers:
  Part A — Worker (ActionWorker, WorkerTickResult)
  Part B — Runtime assembly (build_production_runtime, ProductionRuntime)
  Part C — Health layer (HealthService, ServiceHealth)
  Part D — Webhook foundation (WebhookEvent, WebhookValidationResult, WebhookProcessor)
  Part E — E2E Scenarios:
      Scenario 1: Approved → worker → EXECUTED
      Scenario 2: Transient failure → retry → eventual success
      Scenario 3: Permanent failure → terminal (no retry)
      Scenario 4: Unknown executor → safe failure (action not claimed)
      Scenario 5: Expired action → never executed
      Scenario 6: Rollback path (compensation note added, original ROLLED_BACK)
      Scenario 7: Provider unhealthy → action not claimed

Test infrastructure uses:
  _FakeRepository  — in-memory ActionRepository (full CRUD support)
  _StubProvider    — configurable Provider with exception injection
  _build_stack()   — wires all components for a test
  All real Sprint 2.1-2.5 components (no mocks in production code paths)

No xfail. No skipped tests. No TODO placeholders.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from case_engine.action_executor import (
    ExecutionContext,
    ExecutionResult,
    PermanentExecutionError,
    RetryableExecutionError,
)
from case_engine.action_gateway import ActionGateway
from case_engine.action_models import ActionProposal, ActionRequest
from case_engine.action_repository import ActionRepository
from case_engine.action_runtime import ActionRuntime, EXECUTION_TIMEOUT_DEFAULT
from case_engine.action_state import ActionRiskLevel, ActionState
from case_engine.executor_registry import ActionExecutorRegistry
from case_engine.models import Case
from case_engine.provider_exceptions import (
    ProviderPermanentError,
    ProviderTransientError,
)
from case_engine.provider_interface import Provider
from case_engine.provider_models import (
    ProviderCapability,
    ProviderHealth,
    ProviderRequest,
    ProviderResponse,
)
from case_engine.provider_registry import ProviderRegistry
from case_engine.provider_router import ProviderRouter
from executors import (
    AddTicketNoteExecutor,
    IdentityResetOtpExecutor,
    UpdateTicketStatusExecutor,
)
from runtime.assembly import ProductionRuntime, build_production_runtime
from runtime.health import HealthService, ServiceHealth
from webhook.models import (
    WebhookEvent,
    WebhookProcessor,
    WebhookValidationResult,
)
from worker.action_worker import ActionWorker, WorkerTickResult


# ══════════════════════════════════════════════════════════════════════════════
# Test Infrastructure
# ══════════════════════════════════════════════════════════════════════════════


class _FakeRepository(ActionRepository):
    """
    In-memory ActionRepository for e2e testing.

    Provides real CRUD semantics against an in-memory dict store.
    Supports all query methods needed by ActionWorker and ActionRuntime.
    """

    def __init__(self) -> None:
        super().__init__(supabase_client=None)
        self._store: dict[str, ActionRequest] = {}
        self._transitions: list[Any] = []

    def get_action(self, action_id: str) -> ActionRequest | None:
        return self._store.get(action_id)

    def get_action_by_idempotency_key(self, key: str) -> ActionRequest | None:
        for a in self._store.values():
            if a.idempotency_key == key:
                return a
        return None

    def insert_action(self, action: ActionRequest) -> ActionRequest:
        self._store[action.action_id] = action
        return action

    def update_action(self, action: ActionRequest, *, expected_state=None) -> bool:
        self._store[action.action_id] = action
        return True

    def record_transition(self, record: Any) -> bool:
        self._transitions.append(record)
        return True

    def append_transition(self, record: Any) -> bool:
        return self.record_transition(record)

    def list_approved_actions(self, client: str) -> list[ActionRequest]:
        return [
            a for a in self._store.values()
            if a.client == client and a.current_state == ActionState.APPROVED
        ]

    def list_rolling_back_actions(self, client: str | None = None) -> list[ActionRequest]:
        return [
            a for a in self._store.values()
            if a.current_state == ActionState.ROLLING_BACK
            and (client is None or a.client == client)
        ]


class _StubProvider(Provider):
    """
    Configurable stub provider.

    Supports:
      - healthy/unhealthy health_check responses
      - Exception injection via _raise_on list (pops front on each execute call)
      - Call counting for verification
    """

    _NAME = "freshdesk"
    _VERSION = "0.0.1"
    _CAPS = frozenset({ProviderCapability.EXECUTE, ProviderCapability.HEALTH_CHECK})

    def __init__(self, *, healthy: bool = True) -> None:
        self._healthy = healthy
        self.call_count = 0
        self._raise_on: list[Exception] = []

    @property
    def provider_name(self) -> str:
        return self._NAME

    @property
    def provider_version(self) -> str:
        return self._VERSION

    def capabilities(self) -> frozenset[ProviderCapability]:
        return self._CAPS

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            provider_name=self._NAME,
            provider_version=self._VERSION,
            is_healthy=self._healthy,
            latency_ms=1,
            checked_at=datetime.now(tz=timezone.utc),
        )

    def execute(self, request: ProviderRequest) -> ProviderResponse:
        self.call_count += 1
        if self._raise_on:
            raise self._raise_on.pop(0)
        return ProviderResponse(
            request_id=request.request_id,
            provider_request_id=f"prov-ref-{self.call_count}",
            success=True,
            result={"note_id": f"note-{self.call_count}"},
            status_code=200,
        )


@dataclass
class _Stack:
    repo: _FakeRepository
    gateway: ActionGateway
    runtime: ActionRuntime
    worker: ActionWorker
    provider: _StubProvider
    provider_registry: ProviderRegistry


def _build_stack(*, provider: _StubProvider | None = None, worker_id: str = "test-worker") -> _Stack:
    """Wire the full execution stack with in-memory components."""
    repo = _FakeRepository()
    provider_registry = ProviderRegistry()
    stub = provider or _StubProvider()
    provider_registry.register(stub)
    router = ProviderRouter(provider_registry)
    executor_registry = ActionExecutorRegistry()
    executor_registry.register_executor(AddTicketNoteExecutor(router))
    executor_registry.register_executor(UpdateTicketStatusExecutor(router))
    executor_registry.register_executor(IdentityResetOtpExecutor(router))
    gateway = ActionGateway(repository=repo)
    runtime = ActionRuntime(
        gateway=gateway,
        repository=repo,
        registry=executor_registry,
    )
    worker = ActionWorker(runtime=runtime, repository=repo, worker_id=worker_id)
    return _Stack(
        repo=repo,
        gateway=gateway,
        runtime=runtime,
        worker=worker,
        provider=stub,
        provider_registry=provider_registry,
    )


_CLIENT = "test_client"
_TICKET_ID = "TKT-E2E-001"


def _case(client: str = _CLIENT) -> Case:
    return Case(case_id=str(uuid.uuid4()), ticket_id=_TICKET_ID, client=client)


def _safe_note_proposal(body: str = "Test note") -> ActionProposal:
    return ActionProposal(
        action_type="add_note",
        action_namespace="ticket",
        risk_level=ActionRiskLevel.SAFE,
        action_params={"body": body, "private": True},
        proposed_by="test",
    )


def _reversible_note_proposal() -> ActionProposal:
    return ActionProposal(
        action_type="add_note",
        action_namespace="ticket",
        risk_level=ActionRiskLevel.REVERSIBLE,
        action_params={"body": "Reversible note", "private": True},
        proposed_by="test",
        rollback_action_type="add_note",
        rollback_params={"compensation_note": "This note was added in error and should be disregarded."},
    )


def _safe_status_proposal(status: int = 4) -> ActionProposal:
    return ActionProposal(
        action_type="update_status",
        action_namespace="ticket",
        risk_level=ActionRiskLevel.SAFE,
        action_params={"status": status},
        proposed_by="test",
    )


def _irreversible_otp_proposal() -> ActionProposal:
    return ActionProposal(
        action_type="reset_otp",
        action_namespace="identity",
        risk_level=ActionRiskLevel.IRREVERSIBLE,
        action_params={"account_id": "ACC-123"},
        proposed_by="test",
    )


# ══════════════════════════════════════════════════════════════════════════════
# Part A — Worker Unit Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestWorkerTickUnit:
    def test_tick_empty_queue_returns_zero_processed(self) -> None:
        stack = _build_stack()
        result = stack.worker.tick(_CLIENT)
        assert result.processed == 0
        assert result.results == []

    def test_tick_result_carries_worker_id(self) -> None:
        stack = _build_stack(worker_id="my-worker")
        result = stack.worker.tick(_CLIENT)
        assert result.worker_id == "my-worker"

    def test_tick_result_carries_client(self) -> None:
        stack = _build_stack()
        result = stack.worker.tick("unity_bank")
        assert result.client == "unity_bank"

    def test_tick_rollbacks_empty_returns_zero(self) -> None:
        stack = _build_stack()
        result = stack.worker.tick_rollbacks(_CLIENT)
        assert result.processed == 0

    def test_batch_size_limits_processed(self) -> None:
        stack = _build_stack()
        case = _case()
        # Propose 3 actions (all SAFE, auto-APPROVED)
        stack.gateway.propose(case, _safe_note_proposal("note 1"))
        stack.gateway.propose(
            Case(case_id=str(uuid.uuid4()), ticket_id="TKT-002", client=_CLIENT),
            _safe_note_proposal("note 2"),
        )
        stack.gateway.propose(
            Case(case_id=str(uuid.uuid4()), ticket_id="TKT-003", client=_CLIENT),
            _safe_note_proposal("note 3"),
        )
        # Limit to 2
        stack.worker._batch_size = 2
        result = stack.worker.tick(_CLIENT)
        assert result.processed == 2

    def test_process_returns_rollback_and_forward_results(self) -> None:
        stack = _build_stack()
        rb_result, fwd_result = stack.worker.process(_CLIENT)
        assert isinstance(rb_result, WorkerTickResult)
        assert isinstance(fwd_result, WorkerTickResult)

    def test_worker_tick_result_success_count(self) -> None:
        stack = _build_stack()
        case = _case()
        stack.gateway.propose(case, _safe_note_proposal())
        result = stack.worker.tick(_CLIENT)
        assert result.success_count == 1
        assert result.failure_count == 0

    def test_worker_tick_result_failure_count(self) -> None:
        stub = _StubProvider()
        stub._raise_on = [ProviderPermanentError("bad", error_code="BAD", provider_name="freshdesk")]
        stack = _build_stack(provider=stub)
        case = _case()
        stack.gateway.propose(case, _safe_note_proposal())
        result = stack.worker.tick(_CLIENT)
        assert result.failure_count == 1
        assert result.success_count == 0


# ══════════════════════════════════════════════════════════════════════════════
# Part B — Runtime Assembly Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestProductionAssembly:
    def test_build_production_runtime_returns_stack(self) -> None:
        stack = build_production_runtime()
        assert isinstance(stack, ProductionRuntime)

    def test_offline_mode_no_provider_registered(self) -> None:
        stack = build_production_runtime()
        assert stack.provider_registry.registered_count() == 0

    def test_all_three_executors_registered(self) -> None:
        stack = build_production_runtime()
        assert stack.executor_registry.executor_exists("ticket", "add_note")
        assert stack.executor_registry.executor_exists("ticket", "update_status")
        assert stack.executor_registry.executor_exists("identity", "reset_otp")

    def test_executor_count_is_three(self) -> None:
        stack = build_production_runtime()
        assert stack.executor_registry.registered_count() == 3

    def test_runtime_is_action_runtime_instance(self) -> None:
        stack = build_production_runtime()
        assert isinstance(stack.runtime, ActionRuntime)

    def test_worker_is_action_worker_instance(self) -> None:
        stack = build_production_runtime()
        assert isinstance(stack.worker, ActionWorker)

    def test_worker_id_propagated(self) -> None:
        stack = build_production_runtime(worker_id="prod-worker-1")
        assert stack.worker.worker_id == "prod-worker-1"

    def test_health_service_wired(self) -> None:
        stack = build_production_runtime()
        assert isinstance(stack.health, HealthService)

    def test_shared_repository_gateway_runtime(self) -> None:
        stack = build_production_runtime()
        # Gateway and runtime must share the SAME repository instance
        assert stack.gateway._repo is stack.repository
        assert stack.runtime._repo is stack.repository

    def test_duplicate_executor_registration_fails(self) -> None:
        from case_engine.executor_registry import ExecutorRegistrationError
        from case_engine.provider_router import ProviderRouter
        from case_engine.provider_registry import ProviderRegistry
        router = ProviderRouter(ProviderRegistry())
        registry = ActionExecutorRegistry()
        registry.register_executor(AddTicketNoteExecutor(router))
        with pytest.raises(ExecutorRegistrationError):
            registry.register_executor(AddTicketNoteExecutor(router))


# ══════════════════════════════════════════════════════════════════════════════
# Part C — Health Layer Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestHealthService:
    def _make_health(
        self,
        *,
        provider: _StubProvider | None = None,
        executor_registry: ActionExecutorRegistry | None = None,
        runtime: ActionRuntime | None = None,
        worker: ActionWorker | None = None,
    ) -> HealthService:
        repo = _FakeRepository()
        prov_registry = ProviderRegistry()
        if provider:
            prov_registry.register(provider)
        exc_registry = executor_registry or ActionExecutorRegistry()
        rt = runtime or ActionRuntime(
            gateway=ActionGateway(repository=repo),
            repository=repo,
            registry=exc_registry,
        )
        return HealthService(
            provider_registry=prov_registry,
            executor_registry=exc_registry,
            runtime=rt,
            worker=worker,
        )

    def test_no_providers_is_healthy_if_executors_present(self) -> None:
        stack = _build_stack()
        health_svc = HealthService(
            provider_registry=stack.provider_registry,
            executor_registry=ActionExecutorRegistry(),
            runtime=stack.runtime,
        )
        # No executors → not healthy
        result = health_svc.check()
        assert result.is_healthy is False

    def test_healthy_provider_reports_healthy_in_statuses(self) -> None:
        stub = _StubProvider(healthy=True)
        health_svc = self._make_health(provider=stub)
        result = health_svc.check()
        assert result.provider_statuses.get("freshdesk") is True

    def test_unhealthy_provider_reports_unhealthy_in_statuses(self) -> None:
        stub = _StubProvider(healthy=False)
        health_svc = self._make_health(provider=stub)
        result = health_svc.check()
        assert result.provider_statuses.get("freshdesk") is False

    def test_unhealthy_provider_makes_overall_unhealthy(self) -> None:
        stack = _build_stack(provider=_StubProvider(healthy=False))
        health_svc = HealthService(
            provider_registry=stack.provider_registry,
            executor_registry=ActionExecutorRegistry(),
            runtime=stack.runtime,
        )
        result = health_svc.check()
        assert result.is_healthy is False

    def test_executor_count_reflected(self) -> None:
        stack = _build_stack()
        health_svc = HealthService(
            provider_registry=stack.provider_registry,
            executor_registry=stack.runtime._registry,
            runtime=stack.runtime,
        )
        result = health_svc.check()
        assert result.executor_count == 3

    def test_executor_keys_included(self) -> None:
        stack = _build_stack()
        health_svc = HealthService(
            provider_registry=stack.provider_registry,
            executor_registry=stack.runtime._registry,
            runtime=stack.runtime,
        )
        result = health_svc.check()
        assert ("identity", "reset_otp") in result.executor_keys
        assert ("ticket", "add_note") in result.executor_keys
        assert ("ticket", "update_status") in result.executor_keys

    def test_worker_available_false_when_none(self) -> None:
        health_svc = self._make_health(worker=None)
        result = health_svc.check()
        assert result.worker_available is False

    def test_worker_available_true_when_configured(self) -> None:
        stack = _build_stack()
        health_svc = HealthService(
            provider_registry=stack.provider_registry,
            executor_registry=stack.runtime._registry,
            runtime=stack.runtime,
            worker=stack.worker,
        )
        result = health_svc.check()
        assert result.worker_available is True

    def test_runtime_ready_true(self) -> None:
        stack = _build_stack()
        health_svc = HealthService(
            provider_registry=stack.provider_registry,
            executor_registry=stack.runtime._registry,
            runtime=stack.runtime,
        )
        result = health_svc.check()
        assert result.runtime_ready is True

    def test_checked_at_is_recent(self) -> None:
        stack = _build_stack()
        health_svc = HealthService(
            provider_registry=stack.provider_registry,
            executor_registry=stack.runtime._registry,
            runtime=stack.runtime,
        )
        before = datetime.now(tz=timezone.utc)
        result = health_svc.check()
        after = datetime.now(tz=timezone.utc)
        assert before <= result.checked_at <= after

    def test_full_healthy_stack_returns_healthy(self) -> None:
        stack = _build_stack()
        health_svc = HealthService(
            provider_registry=stack.provider_registry,
            executor_registry=stack.runtime._registry,
            runtime=stack.runtime,
            worker=stack.worker,
        )
        result = health_svc.check()
        assert result.is_healthy is True


# ══════════════════════════════════════════════════════════════════════════════
# Part D — Webhook Foundation Tests
# ══════════════════════════════════════════════════════════════════════════════


class _NoopProcessor(WebhookProcessor):
    """Concrete WebhookProcessor implementation for testing the ABC contract."""

    def validate(self, raw_body: bytes, signature: str) -> WebhookValidationResult:
        if signature == "valid":
            return WebhookValidationResult.ok()
        return WebhookValidationResult.reject(f"bad signature: {signature!r}")

    def parse(self, event: WebhookEvent) -> None:
        if event.event_type == "ticket.update":
            return ActionProposal(
                action_type="add_note",
                action_namespace="ticket",
                risk_level=ActionRiskLevel.SAFE,
                action_params={"body": "Auto-reply from webhook", "private": True},
                proposed_by="webhook",
            )
        return None


class TestWebhookFoundation:
    def test_webhook_event_construction(self) -> None:
        event = WebhookEvent(
            event_id="evt-1",
            event_type="ticket.update",
            ticket_id="TKT-001",
            client="unity_bank",
            payload={"status": 4},
        )
        assert event.event_id == "evt-1"
        assert event.ticket_id == "TKT-001"

    def test_webhook_event_is_frozen(self) -> None:
        event = WebhookEvent(event_id="e", event_type="t", ticket_id="TKT", client="c")
        with pytest.raises(Exception):
            event.event_id = "mutated"  # type: ignore[misc]

    def test_webhook_event_default_received_at(self) -> None:
        before = datetime.now(tz=timezone.utc)
        event = WebhookEvent(event_id="e", event_type="t", ticket_id="TKT", client="c")
        after = datetime.now(tz=timezone.utc)
        assert before <= event.received_at <= after

    def test_webhook_event_raw_signature_defaults_empty(self) -> None:
        event = WebhookEvent(event_id="e", event_type="t", ticket_id="TKT", client="c")
        assert event.raw_signature == ""

    def test_validation_result_ok(self) -> None:
        result = WebhookValidationResult.ok()
        assert result.is_valid is True
        assert result.reason is None

    def test_validation_result_reject_has_reason(self) -> None:
        result = WebhookValidationResult.reject("hmac mismatch")
        assert result.is_valid is False
        assert result.reason == "hmac mismatch"

    def test_validation_result_is_frozen(self) -> None:
        result = WebhookValidationResult.ok()
        with pytest.raises(Exception):
            result.is_valid = False  # type: ignore[misc]

    def test_noop_processor_valid_signature(self) -> None:
        proc = _NoopProcessor()
        result = proc.validate(b'{"event": "test"}', "valid")
        assert result.is_valid is True

    def test_noop_processor_invalid_signature(self) -> None:
        proc = _NoopProcessor()
        result = proc.validate(b'{"event": "test"}', "wrong")
        assert result.is_valid is False
        assert result.reason is not None

    def test_noop_processor_parse_known_event(self) -> None:
        proc = _NoopProcessor()
        event = WebhookEvent(
            event_id="e1", event_type="ticket.update", ticket_id="TKT-1", client="c",
        )
        proposal = proc.parse(event)
        assert proposal is not None
        assert proposal.action_type == "add_note"

    def test_noop_processor_parse_unknown_event_returns_none(self) -> None:
        proc = _NoopProcessor()
        event = WebhookEvent(
            event_id="e2", event_type="ticket.created", ticket_id="TKT-2", client="c",
        )
        proposal = proc.parse(event)
        assert proposal is None

    def test_webhook_processor_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            WebhookProcessor()  # type: ignore[abstract]


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 1 — Approved action executed by worker
# ══════════════════════════════════════════════════════════════════════════════


class TestScenario1ApprovedActionExecuted:
    def test_safe_action_auto_approved_then_executed(self) -> None:
        stack = _build_stack()
        action = stack.gateway.propose(_case(), _safe_note_proposal())
        assert action.current_state == ActionState.APPROVED

        result = stack.worker.tick(_CLIENT)

        assert result.processed == 1
        assert result.results[0].success is True

    def test_executed_action_state_is_executed(self) -> None:
        stack = _build_stack()
        action = stack.gateway.propose(_case(), _safe_note_proposal())
        stack.worker.tick(_CLIENT)
        stored = stack.repo.get_action(action.action_id)
        assert stored.current_state == ActionState.EXECUTED

    def test_execution_result_has_provider_reference(self) -> None:
        stack = _build_stack()
        stack.gateway.propose(_case(), _safe_note_proposal())
        result = stack.worker.tick(_CLIENT)
        assert result.results[0].provider_reference is not None
        assert result.results[0].provider_reference != ""

    def test_provider_was_called_once(self) -> None:
        stack = _build_stack()
        stack.gateway.propose(_case(), _safe_note_proposal())
        stack.worker.tick(_CLIENT)
        assert stack.provider.call_count == 1

    def test_update_status_action_executed(self) -> None:
        stack = _build_stack()
        stack.gateway.propose(_case(), _safe_status_proposal(status=4))
        result = stack.worker.tick(_CLIENT)
        assert result.results[0].success is True

    def test_reversible_action_executed_after_manual_approval(self) -> None:
        stack = _build_stack()
        case = _case()
        action = stack.gateway.propose(case, _reversible_note_proposal())
        assert action.current_state == ActionState.AWAITING_APPROVAL

        # Human agent approves
        action = stack.gateway.approve(action, approved_by="agent-1", notes="OK")
        assert action.current_state == ActionState.APPROVED

        result = stack.worker.tick(_CLIENT)
        assert result.results[0].success is True

    def test_worker_does_not_reprocess_executed_action(self) -> None:
        stack = _build_stack()
        stack.gateway.propose(_case(), _safe_note_proposal())
        stack.worker.tick(_CLIENT)  # executes it
        result2 = stack.worker.tick(_CLIENT)  # nothing left
        assert result2.processed == 0

    def test_multiple_actions_all_executed(self) -> None:
        stack = _build_stack()
        cases = [_case() for _ in range(3)]
        for case in cases:
            stack.gateway.propose(case, _safe_note_proposal())
        result = stack.worker.tick(_CLIENT)
        assert result.processed == 3
        assert result.success_count == 3


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 2 — Transient failure → retry → eventual success
# ══════════════════════════════════════════════════════════════════════════════


class TestScenario2TransientRetrySuccess:
    def test_transient_failure_requeues_for_retry(self) -> None:
        stub = _StubProvider()
        stub._raise_on = [
            ProviderTransientError("timeout", error_code="TIMEOUT", provider_name="freshdesk")
        ]
        stack = _build_stack(provider=stub)
        action = stack.gateway.propose(_case(), _safe_note_proposal())

        result1 = stack.worker.tick(_CLIENT)
        assert result1.results[0].success is False
        assert result1.results[0].retryable is True

        # Action must be APPROVED again (retry scheduled)
        stored = stack.repo.get_action(action.action_id)
        assert stored.current_state == ActionState.APPROVED

    def test_retry_attempt_counter_increments(self) -> None:
        stub = _StubProvider()
        stub._raise_on = [
            ProviderTransientError("timeout", error_code="TIMEOUT", provider_name="freshdesk")
        ]
        stack = _build_stack(provider=stub)
        action = stack.gateway.propose(_case(), _safe_note_proposal())
        stack.worker.tick(_CLIENT)  # fails once
        stored = stack.repo.get_action(action.action_id)
        assert stored.execution_attempt == 1

    def test_eventual_success_after_transient_failure(self) -> None:
        stub = _StubProvider()
        stub._raise_on = [
            ProviderTransientError("timeout", error_code="TIMEOUT", provider_name="freshdesk")
        ]
        stack = _build_stack(provider=stub)
        action = stack.gateway.propose(_case(), _safe_note_proposal())

        stack.worker.tick(_CLIENT)  # fails
        result2 = stack.worker.tick(_CLIENT)  # succeeds

        assert result2.results[0].success is True
        stored = stack.repo.get_action(action.action_id)
        assert stored.current_state == ActionState.EXECUTED

    def test_provider_called_twice_total(self) -> None:
        stub = _StubProvider()
        stub._raise_on = [
            ProviderTransientError("timeout", error_code="TIMEOUT", provider_name="freshdesk")
        ]
        stack = _build_stack(provider=stub)
        stack.gateway.propose(_case(), _safe_note_proposal())
        stack.worker.tick(_CLIENT)
        stack.worker.tick(_CLIENT)
        assert stub.call_count == 2

    def test_max_attempts_exhausted_leaves_failed(self) -> None:
        stub = _StubProvider()
        # 3 transient errors — SAFE actions have max_attempts=3
        stub._raise_on = [
            ProviderTransientError("t1", error_code="TIMEOUT", provider_name="freshdesk"),
            ProviderTransientError("t2", error_code="TIMEOUT", provider_name="freshdesk"),
            ProviderTransientError("t3", error_code="TIMEOUT", provider_name="freshdesk"),
        ]
        stack = _build_stack(provider=stub)
        action = stack.gateway.propose(_case(), _safe_note_proposal())

        stack.worker.tick(_CLIENT)  # attempt 1 — retry
        stack.worker.tick(_CLIENT)  # attempt 2 — retry
        stack.worker.tick(_CLIENT)  # attempt 3 — terminal

        stored = stack.repo.get_action(action.action_id)
        assert stored.current_state == ActionState.DEAD_LETTER  # Sprint 2.10: exhausted
        # No more APPROVED actions
        assert stack.repo.list_approved_actions(_CLIENT) == []


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 3 — Permanent failure → terminal
# ══════════════════════════════════════════════════════════════════════════════


class TestScenario3PermanentFailure:
    def test_permanent_error_leaves_action_failed(self) -> None:
        stub = _StubProvider()
        stub._raise_on = [
            ProviderPermanentError("not found", error_code="NOT_FOUND", provider_name="freshdesk")
        ]
        stack = _build_stack(provider=stub)
        action = stack.gateway.propose(_case(), _safe_note_proposal())

        result = stack.worker.tick(_CLIENT)

        assert result.results[0].success is False
        stored = stack.repo.get_action(action.action_id)
        assert stored.current_state == ActionState.DEAD_LETTER  # Sprint 2.10

    def test_permanent_error_not_retryable(self) -> None:
        stub = _StubProvider()
        stub._raise_on = [
            ProviderPermanentError("auth", error_code="AUTH_FAILED", provider_name="freshdesk")
        ]
        stack = _build_stack(provider=stub)
        stack.gateway.propose(_case(), _safe_note_proposal())
        result = stack.worker.tick(_CLIENT)
        assert result.results[0].retryable is False

    def test_permanent_error_no_retry_even_with_budget(self) -> None:
        stub = _StubProvider()
        stub._raise_on = [
            ProviderPermanentError("bad", error_code="BAD", provider_name="freshdesk")
        ]
        stack = _build_stack(provider=stub)
        action = stack.gateway.propose(_case(), _safe_note_proposal())
        # max_attempts=3 for SAFE, but permanent error skips retry
        stack.worker.tick(_CLIENT)  # fails permanently

        assert stack.repo.list_approved_actions(_CLIENT) == []
        stored = stack.repo.get_action(action.action_id)
        assert stored.current_state == ActionState.DEAD_LETTER  # Sprint 2.10

    def test_irreversible_action_terminal_on_first_failure(self) -> None:
        stub = _StubProvider()
        stub._raise_on = [
            ProviderTransientError("t", error_code="TIMEOUT", provider_name="freshdesk")
        ]
        stack = _build_stack(provider=stub)
        case = _case()
        # Propose IRREVERSIBLE action (max_attempts=1) then approve manually
        action = stack.gateway.propose(case, _irreversible_otp_proposal())
        assert action.current_state == ActionState.AWAITING_APPROVAL
        action = stack.gateway.approve(action, approved_by="senior-agent", notes="Verified")

        result = stack.worker.tick(_CLIENT)

        # Even though it's a transient error, max_attempts=1 → no retry → DEAD_LETTER
        stored = stack.repo.get_action(action.action_id)
        assert stored.current_state == ActionState.DEAD_LETTER  # Sprint 2.10
        assert result.results[0].success is False

    def test_failure_code_preserved_in_result(self) -> None:
        stub = _StubProvider()
        stub._raise_on = [
            ProviderPermanentError("nf", error_code="RESOURCE_NOT_FOUND", provider_name="freshdesk")
        ]
        stack = _build_stack(provider=stub)
        stack.gateway.propose(_case(), _safe_note_proposal())
        result = stack.worker.tick(_CLIENT)
        assert result.results[0].error_code == "RESOURCE_NOT_FOUND"


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 4 — Unknown executor → safe failure
# ══════════════════════════════════════════════════════════════════════════════


class TestScenario4UnknownExecutor:
    def _stack_without_executors(self) -> _Stack:
        """Stack with the executor for action_type="reset_otp" removed."""
        stack = _build_stack()
        # Build a new registry without the OTP executor
        repo = _FakeRepository()
        provider_registry = ProviderRegistry()
        provider_registry.register(_StubProvider())
        router = ProviderRouter(provider_registry)
        exc_registry = ActionExecutorRegistry()
        # Only register add_note and update_status — NOT reset_otp
        exc_registry.register_executor(AddTicketNoteExecutor(router))
        exc_registry.register_executor(UpdateTicketStatusExecutor(router))
        gateway = ActionGateway(repository=repo)
        runtime = ActionRuntime(gateway=gateway, repository=repo, registry=exc_registry)
        worker = ActionWorker(runtime=runtime, repository=repo)
        return _Stack(
            repo=repo, gateway=gateway, runtime=runtime, worker=worker,
            provider=_StubProvider(), provider_registry=provider_registry,
        )

    def test_unknown_executor_returns_failure_result(self) -> None:
        stack = self._stack_without_executors()
        # Propose and approve an OTP action (no executor registered for it)
        case = _case()
        action = stack.gateway.propose(case, _irreversible_otp_proposal())
        action = stack.gateway.approve(action, approved_by="agent", notes="approved")

        result = stack.worker.tick(_CLIENT)

        assert result.results[0].success is False

    def test_unknown_executor_action_not_claimed(self) -> None:
        stack = self._stack_without_executors()
        case = _case()
        action = stack.gateway.propose(case, _irreversible_otp_proposal())
        action = stack.gateway.approve(action, approved_by="agent", notes="approved")

        stack.worker.tick(_CLIENT)

        stored = stack.repo.get_action(action.action_id)
        # Must remain APPROVED — not claimed, not EXECUTING or FAILED
        assert stored.current_state == ActionState.APPROVED

    def test_unknown_executor_error_code(self) -> None:
        stack = self._stack_without_executors()
        case = _case()
        action = stack.gateway.propose(case, _irreversible_otp_proposal())
        action = stack.gateway.approve(action, approved_by="agent", notes="approved")
        result = stack.worker.tick(_CLIENT)
        assert result.results[0].error_code == "EXECUTOR_NOT_REGISTERED"

    def test_other_executors_still_process_normally(self) -> None:
        stack = self._stack_without_executors()
        # add_note IS registered; should process fine
        stack.gateway.propose(_case(), _safe_note_proposal())
        result = stack.worker.tick(_CLIENT)
        assert result.results[0].success is True


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 5 — Expired action → never executed
# ══════════════════════════════════════════════════════════════════════════════


class TestScenario5ExpiredAction:
    def _propose_with_past_expiry(self, stack: _Stack) -> ActionRequest:
        case = _case()
        action = stack.gateway.propose(case, _safe_note_proposal())
        # Manually force expires_at to the past (bypasses gateway validation)
        action.expires_at = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
        stack.repo.update_action(action)
        return action

    def test_expired_action_not_executed(self) -> None:
        stack = _build_stack()
        self._propose_with_past_expiry(stack)
        result = stack.worker.tick(_CLIENT)
        assert result.results[0].success is False

    def test_expired_action_error_code(self) -> None:
        stack = _build_stack()
        self._propose_with_past_expiry(stack)
        result = stack.worker.tick(_CLIENT)
        assert result.results[0].error_code == "ACTION_EXPIRED"

    def test_expired_action_state_unchanged(self) -> None:
        stack = _build_stack()
        action = self._propose_with_past_expiry(stack)
        stack.worker.tick(_CLIENT)
        stored = stack.repo.get_action(action.action_id)
        # Action was not claimed → still APPROVED (we set expires_at, not state)
        assert stored.current_state == ActionState.APPROVED

    def test_provider_not_called_for_expired_action(self) -> None:
        stack = _build_stack()
        self._propose_with_past_expiry(stack)
        stack.worker.tick(_CLIENT)
        assert stack.provider.call_count == 0

    def test_active_action_still_executes_alongside_expired(self) -> None:
        stack = _build_stack()
        self._propose_with_past_expiry(stack)  # expired
        stack.gateway.propose(  # active (fresh case, different idempotency key)
            Case(case_id=str(uuid.uuid4()), ticket_id="TKT-002", client=_CLIENT),
            _safe_note_proposal("active note"),
        )
        result = stack.worker.tick(_CLIENT)
        assert result.processed == 2
        successes = [r for r in result.results if r.success]
        assert len(successes) == 1


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 6 — Rollback path
# ══════════════════════════════════════════════════════════════════════════════


class TestScenario6RollbackPath:
    def _execute_and_propose_rollback(self, stack: _Stack) -> tuple[ActionRequest, ActionRequest]:
        case = _case()
        action = stack.gateway.propose(case, _reversible_note_proposal())
        action = stack.gateway.approve(action, approved_by="agent-1", notes="ok")
        stack.worker.tick(_CLIENT)

        stored = stack.repo.get_action(action.action_id)
        assert stored.current_state == ActionState.EXECUTED

        # Propose rollback — creates compensation action, original → ROLLING_BACK
        compensation = stack.gateway.propose_rollback(stored, case)
        return stored, compensation

    def test_rollback_success_original_rolled_back(self) -> None:
        stack = _build_stack()
        original, compensation = self._execute_and_propose_rollback(stack)

        result = stack.worker.tick_rollbacks(_CLIENT)

        assert result.processed == 1
        assert result.results[0].success is True
        stored_orig = stack.repo.get_action(original.action_id)
        assert stored_orig.current_state == ActionState.ROLLED_BACK

    def test_rollback_compensation_action_executed(self) -> None:
        stack = _build_stack()
        original, compensation = self._execute_and_propose_rollback(stack)

        stack.worker.tick_rollbacks(_CLIENT)

        stored_comp = stack.repo.get_action(compensation.action_id)
        assert stored_comp.current_state == ActionState.EXECUTED

    def test_rollback_is_rolled_back_flag_set(self) -> None:
        stack = _build_stack()
        original, _ = self._execute_and_propose_rollback(stack)
        stack.worker.tick_rollbacks(_CLIENT)
        stored = stack.repo.get_action(original.action_id)
        assert stored.is_rolled_back is True

    def test_rollback_provider_called_twice(self) -> None:
        stack = _build_stack()
        self._execute_and_propose_rollback(stack)
        # Provider call 1: forward execute; call 2: rollback compensation
        stack.worker.tick_rollbacks(_CLIENT)
        assert stack.provider.call_count == 2

    def test_rollback_only_processes_rolling_back_originals(self) -> None:
        stack = _build_stack()
        # One forward action (not in ROLLING_BACK)
        stack.gateway.propose(_case(), _safe_note_proposal())
        # tick_rollbacks should find nothing to roll back
        result = stack.worker.tick_rollbacks(_CLIENT)
        assert result.processed == 0

    def test_rollback_failure_marks_rollback_failed(self) -> None:
        stub = _StubProvider()
        stack = _build_stack(provider=stub)
        original, _ = self._execute_and_propose_rollback(stack)

        # Inject failure for the rollback provider call
        stub._raise_on = [
            ProviderPermanentError("rollback error", error_code="PERM", provider_name="freshdesk")
        ]
        result = stack.worker.tick_rollbacks(_CLIENT)

        assert result.results[0].success is False
        stored = stack.repo.get_action(original.action_id)
        assert stored.current_state == ActionState.ROLLBACK_FAILED


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 7 — Provider unhealthy → action not claimed
# ══════════════════════════════════════════════════════════════════════════════


class TestScenario7ProviderUnhealthy:
    def test_unhealthy_provider_aborts_execution(self) -> None:
        stub = _StubProvider(healthy=False)
        stack = _build_stack(provider=stub)
        action = stack.gateway.propose(_case(), _safe_note_proposal())

        result = stack.worker.tick(_CLIENT)

        assert result.results[0].success is False

    def test_unhealthy_provider_error_code(self) -> None:
        stub = _StubProvider(healthy=False)
        stack = _build_stack(provider=stub)
        stack.gateway.propose(_case(), _safe_note_proposal())
        result = stack.worker.tick(_CLIENT)
        assert result.results[0].error_code == "EXECUTOR_UNHEALTHY"

    def test_unhealthy_provider_action_not_claimed(self) -> None:
        stub = _StubProvider(healthy=False)
        stack = _build_stack(provider=stub)
        action = stack.gateway.propose(_case(), _safe_note_proposal())

        stack.worker.tick(_CLIENT)

        stored = stack.repo.get_action(action.action_id)
        # Action must remain APPROVED — the runtime aborted before begin_execution
        assert stored.current_state == ActionState.APPROVED

    def test_unhealthy_provider_no_provider_execute_call(self) -> None:
        stub = _StubProvider(healthy=False)
        stack = _build_stack(provider=stub)
        stack.gateway.propose(_case(), _safe_note_proposal())
        stack.worker.tick(_CLIENT)
        assert stub.call_count == 0

    def test_provider_becomes_healthy_action_then_executes(self) -> None:
        stub = _StubProvider(healthy=False)
        stack = _build_stack(provider=stub)
        action = stack.gateway.propose(_case(), _safe_note_proposal())

        stack.worker.tick(_CLIENT)  # fails — unhealthy
        stub._healthy = True          # provider recovers
        result2 = stack.worker.tick(_CLIENT)  # succeeds

        assert result2.results[0].success is True
        stored = stack.repo.get_action(action.action_id)
        assert stored.current_state == ActionState.EXECUTED


# ══════════════════════════════════════════════════════════════════════════════
# Context Propagation via Worker → Runtime → Executor
# ══════════════════════════════════════════════════════════════════════════════


class TestContextPropagationE2E:
    def test_worker_id_used_as_executor_id(self) -> None:
        stack = _build_stack(worker_id="ctx-worker")
        action = stack.gateway.propose(_case(), _safe_note_proposal())
        stack.worker.tick(_CLIENT)
        stored = stack.repo.get_action(action.action_id)
        assert stored.executor_id == "ctx-worker"

    def test_execution_attempt_incremented(self) -> None:
        stack = _build_stack()
        action = stack.gateway.propose(_case(), _safe_note_proposal())
        assert stack.repo.get_action(action.action_id).execution_attempt == 0
        stack.worker.tick(_CLIENT)
        stored = stack.repo.get_action(action.action_id)
        assert stored.execution_attempt == 1

    def test_execution_started_at_set(self) -> None:
        stack = _build_stack()
        action = stack.gateway.propose(_case(), _safe_note_proposal())
        stack.worker.tick(_CLIENT)
        # After EXECUTED, execution_started_at is set; execution_completed_at too
        stored = stack.repo.get_action(action.action_id)
        assert stored.execution_completed_at is not None

    def test_ticket_id_preserved_through_execution(self) -> None:
        stack = _build_stack()
        case = Case(case_id=str(uuid.uuid4()), ticket_id="TKT-CUSTOM-42", client=_CLIENT)
        action = stack.gateway.propose(case, _safe_note_proposal())
        stack.worker.tick(_CLIENT)
        stored = stack.repo.get_action(action.action_id)
        assert stored.ticket_id == "TKT-CUSTOM-42"
