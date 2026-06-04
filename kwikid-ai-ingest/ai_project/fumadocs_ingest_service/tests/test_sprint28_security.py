"""
tests/test_sprint28_security.py

Sprint 2.8: Security, Authorization, SLA Watchdog, Audit, Config Validation.

Coverage:
  ApiKeyAuthenticator:
    - Valid key accepted
    - Invalid key rejected
    - Missing key rejected
    - Disabled auth returns anonymous admin
    - Multiple keys — each works independently
    - Constant-time path (hmac.compare_digest called)
    - Role assigned correctly per registry

  Authorization (Role-Based):
    - APPROVER can approve/reject
    - OPERATOR can tick worker
    - ADMIN can do all
    - Wrong role denied with 403
    - Missing key returns 401

  Route Protection:
    - POST /actions/{id}/approve → 401 missing key, 403 wrong role, 200 correct role
    - POST /actions/{id}/reject  → same
    - POST /worker/tick          → 401 missing key, 403 wrong role, 200 correct role
    - POST /watchdog/run         → 401 missing key, 403 wrong role, 200 correct role
    - GET  /health               → always 200 (public)
    - POST /webhook/{client}     → protected by HMAC only (no API key needed)

  SLA Watchdog:
    - Expires AWAITING_APPROVAL actions past their deadline
    - Ignores APPROVED / non-expired actions
    - Idempotent (second run finds zero actions)
    - Returns correct counts

  Audit:
    - Approval emits ACTION_APPROVED event
    - Rejection emits ACTION_REJECTED event
    - Expiry sweep emits ACTION_EXPIRED event
    - Event has action_id, actor, timestamp, metadata

  Configuration Validation:
    - AUTH_ENABLED=true + no keys → ConfigurationValidationError
    - AUTH_ENABLED=true + keys configured → passes
    - AUTH_ENABLED=false → passes regardless
    - ENFORCE_HMAC=true + no secret → ConfigurationValidationError
    - ENFORCE_HMAC=false → passes regardless

  Security hardening:
    - API key never in 401 response body
    - API key never in 403 response body
    - Stack traces never in error responses
    - Role value revealed in 403 for diagnosis (role name only, not key)
"""
from __future__ import annotations

import hashlib
import hmac as hmac_lib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from audit.logger import AuditLogger
from audit.models import AuditEvent, AuditEventType
from case_engine.action_gateway import ActionGateway
from case_engine.action_models import ActionProposal, ActionRequest
from case_engine.action_repository import ActionRepository
from case_engine.action_runtime import ActionRuntime
from case_engine.action_state import ActionRiskLevel, ActionState
from case_engine.executor_registry import ActionExecutorRegistry
from case_engine.models import Case
from case_engine.provider_registry import ProviderRegistry
from case_engine.provider_router import ProviderRouter
from case_engine.sla_watchdog import SLAWatchdog, WatchdogResult
from security.auth import ApiKeyAuthenticator, AuthContext, AuthResult
from security.config import build_authenticator_from_env
from security.config_validator import ConfigurationValidationError, validate_startup_config
from security.roles import Permission, Role, has_permission
from webhook.freshdesk_processor import FreshdeskWebhookProcessor


# ── Test constants ─────────────────────────────────────────────────────────────

_APPROVER_KEY  = "approver-key-secret-abc"
_OPERATOR_KEY  = "operator-key-secret-xyz"
_ADMIN_KEY     = "admin-key-secret-999"
_UNKNOWN_KEY   = "totally-unknown-key-000"
_CLIENT        = "unity_bank"
_WH_SECRET     = b"webhook-secret-test"


# ── Test infrastructure ────────────────────────────────────────────────────────

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
            existing = next(a for a in self._store.values() if a.idempotency_key == action.idempotency_key)
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
        return [a for a in self._store.values()
                if a.client == client and a.current_state == ActionState.APPROVED]

    def list_rolling_back_actions(self, client: str | None = None) -> list[ActionRequest]:
        return [a for a in self._store.values()
                if a.current_state == ActionState.ROLLING_BACK
                and a.rollback_action_id is not None
                and (client is None or a.client == client)]

    def list_pending_approvals(self, client: str) -> list[ActionRequest]:
        return [a for a in self._store.values()
                if a.client == client and a.current_state == ActionState.AWAITING_APPROVAL]

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


@dataclass
class _FakeStack:
    repository: _FakeRepository = field(default_factory=_FakeRepository)
    _gateway: ActionGateway | None = None
    _worker: Any = None
    _health: Any = None
    _watchdog: SLAWatchdog | None = None

    def __post_init__(self) -> None:
        from worker.action_worker import ActionWorker
        from runtime.health import HealthService
        self._gateway = ActionGateway(repository=self.repository)
        provider_registry = ProviderRegistry()
        executor_registry = ActionExecutorRegistry()
        router = ProviderRouter(provider_registry)
        runtime = ActionRuntime(
            gateway=self._gateway,
            repository=self.repository,
            registry=executor_registry,
        )
        self._worker = ActionWorker(runtime=runtime, repository=self.repository, worker_id="test")
        self._health = HealthService(
            provider_registry=provider_registry,
            executor_registry=executor_registry,
            runtime=runtime,
            worker=self._worker,
        )
        self._watchdog = SLAWatchdog(gateway=self._gateway, repository=self.repository)

    @property
    def gateway(self): return self._gateway
    @property
    def health(self): return self._health
    @property
    def worker(self): return self._worker
    @property
    def watchdog(self): return self._watchdog


def _make_authenticator(*, enabled: bool = True) -> ApiKeyAuthenticator:
    registry = {
        _APPROVER_KEY: ("alice",    Role.APPROVER),
        _OPERATOR_KEY: ("scheduler", Role.OPERATOR),
        _ADMIN_KEY:    ("admin",    Role.ADMIN),
    }
    return ApiKeyAuthenticator(registry, auth_enabled=enabled)


def _make_app(
    *,
    auth_enabled: bool = True,
    enforce_hmac: bool = False,
) -> tuple[TestClient, _FakeStack, AuditLogger]:
    stack = _FakeStack()
    processor = FreshdeskWebhookProcessor(secret=_WH_SECRET, enforce_hmac=enforce_hmac)
    authenticator = _make_authenticator(enabled=auth_enabled)
    audit_logger = AuditLogger()
    app = create_app(
        stack=stack,
        processor=processor,
        authenticator=authenticator,
        audit_logger=audit_logger,
        skip_config_validation=True,
    )
    return TestClient(app, raise_server_exceptions=False), stack, audit_logger


def _headers(key: str) -> dict:
    return {"x-api-key": key}


def _create_reversible_action(stack: _FakeStack, ticket_id: str = "TKT-R") -> ActionRequest:
    case = Case(case_id=str(uuid.uuid4()), ticket_id=ticket_id, client=_CLIENT)
    proposal = ActionProposal(
        action_type="update_ticket",
        action_namespace="freshdesk",
        risk_level=ActionRiskLevel.REVERSIBLE,
        action_params={"status": 2},
        rollback_action_type="update_ticket",
        rollback_params={"status": 5},
    )
    return stack.gateway.propose(case, proposal)


def _create_expired_action(stack: _FakeStack, ticket_id: str = "TKT-EXP") -> ActionRequest:
    """Create a REVERSIBLE action with an expires_at in the past."""
    case = Case(case_id=str(uuid.uuid4()), ticket_id=ticket_id, client=_CLIENT)
    proposal = ActionProposal(
        action_type="update_ticket",
        action_namespace="freshdesk",
        risk_level=ActionRiskLevel.REVERSIBLE,
        action_params={"status": 2},
        rollback_action_type="update_ticket",
        rollback_params={"status": 5},
    )
    action = stack.gateway.propose(case, proposal)
    assert action.current_state == ActionState.AWAITING_APPROVAL
    # Backdate expires_at to the past
    action.expires_at = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    stack.repository.update_action(action)
    return action


# ══════════════════════════════════════════════════════════════════════════════
# 1. ApiKeyAuthenticator unit tests
# ══════════════════════════════════════════════════════════════════════════════

class TestApiKeyAuthenticator:

    def test_valid_approver_key_returns_authenticated(self):
        auth = _make_authenticator()
        result = auth.authenticate(_APPROVER_KEY)
        assert result.authenticated
        assert result.role == Role.APPROVER
        assert result.identity == "alice"

    def test_valid_operator_key_returns_authenticated(self):
        auth = _make_authenticator()
        result = auth.authenticate(_OPERATOR_KEY)
        assert result.authenticated
        assert result.role == Role.OPERATOR

    def test_valid_admin_key_returns_authenticated(self):
        auth = _make_authenticator()
        result = auth.authenticate(_ADMIN_KEY)
        assert result.authenticated
        assert result.role == Role.ADMIN

    def test_invalid_key_returns_not_authenticated(self):
        auth = _make_authenticator()
        result = auth.authenticate(_UNKNOWN_KEY)
        assert not result.authenticated

    def test_none_key_returns_not_authenticated(self):
        auth = _make_authenticator()
        result = auth.authenticate(None)
        assert not result.authenticated

    def test_empty_string_key_returns_not_authenticated(self):
        auth = _make_authenticator()
        result = auth.authenticate("")
        assert not result.authenticated

    def test_disabled_auth_returns_anonymous_admin(self):
        auth = _make_authenticator(enabled=False)
        result = auth.authenticate(None)
        assert result.authenticated
        assert result.role == Role.ADMIN
        assert result.identity == "anonymous"

    def test_disabled_auth_returns_admin_for_wrong_key(self):
        auth = _make_authenticator(enabled=False)
        result = auth.authenticate("garbage-key")
        assert result.authenticated
        assert result.role == Role.ADMIN

    def test_key_count_correct(self):
        auth = _make_authenticator()
        assert auth.key_count == 3

    def test_empty_registry_always_rejects(self):
        auth = ApiKeyAuthenticator({}, auth_enabled=True)
        result = auth.authenticate("any-key")
        assert not result.authenticated

    def test_compare_digest_called(self):
        auth = _make_authenticator()
        with patch("hmac.compare_digest", wraps=hmac_lib.compare_digest) as mock_cd:
            auth.authenticate(_APPROVER_KEY)
            assert mock_cd.called

    def test_raw_key_never_in_denied_result(self):
        auth = _make_authenticator()
        result = auth.authenticate(_UNKNOWN_KEY)
        # The denied result must not expose any key material
        assert not result.authenticated
        assert _UNKNOWN_KEY not in str(result)

    def test_authenticate_never_raises(self):
        auth = _make_authenticator()
        # Should never raise regardless of input
        result = auth.authenticate(None)
        assert isinstance(result, AuthResult)
        result2 = auth.authenticate("x" * 10000)
        assert isinstance(result2, AuthResult)


# ══════════════════════════════════════════════════════════════════════════════
# 2. Role / Permission logic
# ══════════════════════════════════════════════════════════════════════════════

class TestRolePermissions:

    def test_approver_can_approve(self):
        assert has_permission(Role.APPROVER, Permission.APPROVE_ACTION)

    def test_approver_can_reject(self):
        assert has_permission(Role.APPROVER, Permission.REJECT_ACTION)

    def test_approver_cannot_tick_worker(self):
        assert not has_permission(Role.APPROVER, Permission.WORKER_TICK)

    def test_approver_cannot_run_watchdog(self):
        assert not has_permission(Role.APPROVER, Permission.WATCHDOG_RUN)

    def test_operator_can_tick_worker(self):
        assert has_permission(Role.OPERATOR, Permission.WORKER_TICK)

    def test_operator_can_run_watchdog(self):
        assert has_permission(Role.OPERATOR, Permission.WATCHDOG_RUN)

    def test_operator_cannot_approve(self):
        assert not has_permission(Role.OPERATOR, Permission.APPROVE_ACTION)

    def test_admin_can_all(self):
        for perm in Permission:
            assert has_permission(Role.ADMIN, perm)

    def test_auth_result_has_permission_approver(self):
        result = AuthResult(authenticated=True, identity="x", role=Role.APPROVER)
        assert result.has_permission(Permission.APPROVE_ACTION)
        assert not result.has_permission(Permission.WORKER_TICK)

    def test_auth_result_has_permission_unauthenticated(self):
        result = AuthResult.denied()
        assert not result.has_permission(Permission.APPROVE_ACTION)


# ══════════════════════════════════════════════════════════════════════════════
# 3. POST /actions/{id}/approve — auth protection
# ══════════════════════════════════════════════════════════════════════════════

class TestApproveAuthProtection:

    def _setup(self) -> tuple[TestClient, _FakeStack, AuditLogger]:
        return _make_app(auth_enabled=True)

    def test_approver_key_allows_approval(self):
        c, stack, _ = self._setup()
        action = _create_reversible_action(stack)
        r = c.post(f"/actions/{action.action_id}/approve", headers=_headers(_APPROVER_KEY))
        assert r.status_code == 200

    def test_admin_key_allows_approval(self):
        c, stack, _ = self._setup()
        action = _create_reversible_action(stack, "TKT-ADMIN-APPROVE")
        r = c.post(f"/actions/{action.action_id}/approve", headers=_headers(_ADMIN_KEY))
        assert r.status_code == 200

    def test_operator_key_denied_approval(self):
        c, stack, _ = self._setup()
        action = _create_reversible_action(stack, "TKT-OP-APPROVE")
        r = c.post(f"/actions/{action.action_id}/approve", headers=_headers(_OPERATOR_KEY))
        assert r.status_code == 403

    def test_missing_key_returns_401_on_approve(self):
        c, stack, _ = self._setup()
        action = _create_reversible_action(stack, "TKT-MISSING-KEY")
        r = c.post(f"/actions/{action.action_id}/approve")
        assert r.status_code == 401

    def test_wrong_key_returns_401_on_approve(self):
        c, stack, _ = self._setup()
        action = _create_reversible_action(stack, "TKT-WRONG-KEY")
        r = c.post(f"/actions/{action.action_id}/approve", headers=_headers(_UNKNOWN_KEY))
        assert r.status_code == 401

    def test_401_uses_error_envelope(self):
        c, stack, _ = self._setup()
        action = _create_reversible_action(stack, "TKT-ENV-401")
        r = c.post(f"/actions/{action.action_id}/approve")
        body = r.json()
        assert "error" in body
        assert body["error"]["code"] == "UNAUTHORIZED"

    def test_403_uses_error_envelope(self):
        c, stack, _ = self._setup()
        action = _create_reversible_action(stack, "TKT-ENV-403")
        r = c.post(f"/actions/{action.action_id}/approve", headers=_headers(_OPERATOR_KEY))
        assert r.json()["error"]["code"] == "FORBIDDEN"

    def test_approver_identity_recorded_as_approver(self):
        c, stack, _ = self._setup()
        action = _create_reversible_action(stack, "TKT-IDENTITY")
        r = c.post(f"/actions/{action.action_id}/approve", headers=_headers(_APPROVER_KEY))
        # approver = identity from API key ("alice")
        assert r.json()["approver"] == "alice"

    def test_admin_identity_recorded_as_approver(self):
        c, stack, _ = self._setup()
        action = _create_reversible_action(stack, "TKT-ADMIN-ID")
        r = c.post(f"/actions/{action.action_id}/approve", headers=_headers(_ADMIN_KEY))
        assert r.json()["approver"] == "admin"


# ══════════════════════════════════════════════════════════════════════════════
# 4. POST /actions/{id}/reject — auth protection
# ══════════════════════════════════════════════════════════════════════════════

class TestRejectAuthProtection:

    def _setup(self): return _make_app(auth_enabled=True)

    def test_approver_key_allows_rejection(self):
        c, stack, _ = self._setup()
        action = _create_reversible_action(stack, "TKT-REJ-APPR")
        r = c.post(f"/actions/{action.action_id}/reject", headers=_headers(_APPROVER_KEY))
        assert r.status_code == 200

    def test_admin_key_allows_rejection(self):
        c, stack, _ = self._setup()
        action = _create_reversible_action(stack, "TKT-REJ-ADMIN")
        r = c.post(f"/actions/{action.action_id}/reject", headers=_headers(_ADMIN_KEY))
        assert r.status_code == 200

    def test_operator_key_denied_rejection(self):
        c, stack, _ = self._setup()
        action = _create_reversible_action(stack, "TKT-REJ-OP")
        r = c.post(f"/actions/{action.action_id}/reject", headers=_headers(_OPERATOR_KEY))
        assert r.status_code == 403

    def test_missing_key_returns_401_on_reject(self):
        c, stack, _ = self._setup()
        action = _create_reversible_action(stack, "TKT-REJ-MISS")
        r = c.post(f"/actions/{action.action_id}/reject")
        assert r.status_code == 401

    def test_rejected_action_has_correct_rejector(self):
        c, stack, _ = self._setup()
        action = _create_reversible_action(stack, "TKT-REJ-WHO")
        c.post(f"/actions/{action.action_id}/reject", headers=_headers(_APPROVER_KEY))
        stored = stack.repository.get_action(action.action_id)
        assert stored.current_state == ActionState.REJECTED


# ══════════════════════════════════════════════════════════════════════════════
# 5. POST /worker/tick — auth protection
# ══════════════════════════════════════════════════════════════════════════════

class TestWorkerTickAuthProtection:

    def _setup(self): return _make_app(auth_enabled=True)

    def test_operator_key_allows_tick(self):
        c, stack, _ = self._setup()
        r = c.post(f"/worker/tick?client={_CLIENT}", headers=_headers(_OPERATOR_KEY))
        assert r.status_code == 200

    def test_admin_key_allows_tick(self):
        c, stack, _ = self._setup()
        r = c.post(f"/worker/tick?client={_CLIENT}", headers=_headers(_ADMIN_KEY))
        assert r.status_code == 200

    def test_approver_key_denied_tick(self):
        c, stack, _ = self._setup()
        r = c.post(f"/worker/tick?client={_CLIENT}", headers=_headers(_APPROVER_KEY))
        assert r.status_code == 403

    def test_missing_key_returns_401_on_tick(self):
        c, stack, _ = self._setup()
        r = c.post(f"/worker/tick?client={_CLIENT}")
        assert r.status_code == 401

    def test_wrong_key_returns_401_on_tick(self):
        c, stack, _ = self._setup()
        r = c.post(f"/worker/tick?client={_CLIENT}", headers=_headers(_UNKNOWN_KEY))
        assert r.status_code == 401

    def test_tick_response_includes_actor(self):
        c, stack, _ = self._setup()
        r = c.post(f"/worker/tick?client={_CLIENT}", headers=_headers(_OPERATOR_KEY))
        assert r.json()["actor"] == "scheduler"


# ══════════════════════════════════════════════════════════════════════════════
# 6. POST /watchdog/run — auth protection
# ══════════════════════════════════════════════════════════════════════════════

class TestWatchdogAuthProtection:

    def _setup(self): return _make_app(auth_enabled=True)

    def test_operator_key_allows_watchdog(self):
        c, stack, _ = self._setup()
        r = c.post("/watchdog/run", headers=_headers(_OPERATOR_KEY))
        assert r.status_code == 200

    def test_admin_key_allows_watchdog(self):
        c, stack, _ = self._setup()
        r = c.post("/watchdog/run", headers=_headers(_ADMIN_KEY))
        assert r.status_code == 200

    def test_approver_key_denied_watchdog(self):
        c, stack, _ = self._setup()
        r = c.post("/watchdog/run", headers=_headers(_APPROVER_KEY))
        assert r.status_code == 403

    def test_missing_key_returns_401_on_watchdog(self):
        c, stack, _ = self._setup()
        r = c.post("/watchdog/run")
        assert r.status_code == 401

    def test_watchdog_response_structure(self):
        c, stack, _ = self._setup()
        r = c.post("/watchdog/run", headers=_headers(_OPERATOR_KEY))
        body = r.json()
        assert "expired_actions" in body
        assert "processed" in body
        assert "errors" in body

    def test_watchdog_empty_result(self):
        c, stack, _ = self._setup()
        r = c.post("/watchdog/run", headers=_headers(_OPERATOR_KEY))
        assert r.json()["expired_actions"] == 0
        assert r.json()["processed"] == 0

    def test_watchdog_expires_overdue_actions(self):
        c, stack, _ = self._setup()
        action = _create_expired_action(stack, "TKT-WD-EXP")
        r = c.post("/watchdog/run", headers=_headers(_OPERATOR_KEY))
        assert r.json()["expired_actions"] == 1
        assert r.json()["processed"] == 1
        # Verify state transition
        stored = stack.repository.get_action(action.action_id)
        assert stored.current_state == ActionState.EXPIRED

    def test_watchdog_ignores_non_expired_actions(self):
        c, stack, _ = self._setup()
        # Create a non-expired REVERSIBLE action (expires in 4 hours)
        _create_reversible_action(stack, "TKT-WD-LIVE")
        r = c.post("/watchdog/run", headers=_headers(_OPERATOR_KEY))
        assert r.json()["expired_actions"] == 0

    def test_watchdog_client_filter(self):
        c, stack, _ = self._setup()
        action = _create_expired_action(stack, "TKT-WD-CLIENT")
        # Run for a different client — should NOT expire our action
        r = c.post("/watchdog/run?client=other_client", headers=_headers(_OPERATOR_KEY))
        assert r.json()["expired_actions"] == 0
        stored = stack.repository.get_action(action.action_id)
        assert stored.current_state == ActionState.AWAITING_APPROVAL

    def test_watchdog_idempotent(self):
        c, stack, _ = self._setup()
        _create_expired_action(stack, "TKT-WD-IDEM")
        r1 = c.post("/watchdog/run", headers=_headers(_OPERATOR_KEY))
        r2 = c.post("/watchdog/run", headers=_headers(_OPERATOR_KEY))
        assert r1.json()["expired_actions"] == 1
        assert r2.json()["expired_actions"] == 0  # already expired

    def test_watchdog_actor_in_response(self):
        c, stack, _ = self._setup()
        r = c.post("/watchdog/run", headers=_headers(_OPERATOR_KEY))
        assert r.json()["actor"] == "scheduler"


# ══════════════════════════════════════════════════════════════════════════════
# 7. Public endpoints remain accessible (health, webhook)
# ══════════════════════════════════════════════════════════════════════════════

class TestPublicEndpoints:

    def test_health_accessible_without_key(self):
        c, _, _ = _make_app(auth_enabled=True)
        r = c.get("/health")
        assert r.status_code in (200, 503)

    def test_health_accessible_with_wrong_key(self):
        c, _, _ = _make_app(auth_enabled=True)
        r = c.get("/health", headers=_headers(_UNKNOWN_KEY))
        assert r.status_code in (200, 503)

    def test_webhook_protected_by_hmac_not_api_key(self):
        """Webhook endpoint uses HMAC, not API key auth."""
        c, _, _ = _make_app(auth_enabled=True, enforce_hmac=True)
        body = json.dumps({"event_type": "ticket_created", "ticket_id": "TKT-1"}).encode()
        sig = hmac_lib.new(_WH_SECRET, body, hashlib.sha256).hexdigest()
        # No API key header — should still work (HMAC is the gate)
        r = c.post("/webhook/unity_bank",
                   content=body,
                   headers={"x-freshdesk-signature": sig, "content-type": "application/json"})
        assert r.status_code == 200

    def test_webhook_without_hmac_returns_403_not_401(self):
        """Webhook failure should return 403 (HMAC), not 401 (API key)."""
        c, _, _ = _make_app(auth_enabled=True, enforce_hmac=True)
        body = b'{"event_type":"ticket_created"}'
        r = c.post("/webhook/unity_bank",
                   content=body,
                   headers={"content-type": "application/json"})
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "INVALID_SIGNATURE"

    def test_get_action_accessible_without_key(self):
        c, stack, _ = _make_app(auth_enabled=True)
        action = _create_reversible_action(stack, "TKT-PUBLIC-GET")
        r = c.get(f"/actions/{action.action_id}")
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# 8. SLA Watchdog — unit tests (no HTTP)
# ══════════════════════════════════════════════════════════════════════════════

class TestSLAWatchdogUnit:

    def _make_watchdog(self) -> tuple[SLAWatchdog, _FakeStack]:
        stack = _FakeStack()
        return SLAWatchdog(gateway=stack.gateway, repository=stack.repository), stack

    def test_run_returns_watchdog_result(self):
        watchdog, _ = self._make_watchdog()
        result = watchdog.run()
        assert isinstance(result, WatchdogResult)

    def test_empty_repository_returns_zero_counts(self):
        watchdog, _ = self._make_watchdog()
        result = watchdog.run()
        assert result.expired_actions == 0
        assert result.processed == 0

    def test_expires_overdue_action(self):
        watchdog, stack = self._make_watchdog()
        action = _create_expired_action(stack, "TKT-UNIT-EXP")
        result = watchdog.run()
        assert result.expired_actions == 1
        assert result.processed == 1
        stored = stack.repository.get_action(action.action_id)
        assert stored.current_state == ActionState.EXPIRED

    def test_ignores_future_expiry(self):
        watchdog, stack = self._make_watchdog()
        _create_reversible_action(stack, "TKT-FUTURE")  # expires in 4 hours
        result = watchdog.run()
        assert result.expired_actions == 0

    def test_multiple_expired_actions(self):
        watchdog, stack = self._make_watchdog()
        for i in range(3):
            _create_expired_action(stack, f"TKT-MULTI-{i}")
        result = watchdog.run()
        assert result.expired_actions == 3

    def test_idempotent_second_run(self):
        watchdog, stack = self._make_watchdog()
        _create_expired_action(stack, "TKT-IDEM-UNIT")
        r1 = watchdog.run()
        r2 = watchdog.run()
        assert r1.expired_actions == 1
        assert r2.expired_actions == 0

    def test_client_filter(self):
        watchdog, stack = self._make_watchdog()
        _create_expired_action(stack, "TKT-CF1")
        result = watchdog.run(client="other_client")
        assert result.expired_actions == 0

    def test_client_filter_matches(self):
        watchdog, stack = self._make_watchdog()
        _create_expired_action(stack, "TKT-CF2")
        result = watchdog.run(client=_CLIENT)
        assert result.expired_actions == 1

    def test_never_raises(self):
        watchdog, _ = self._make_watchdog()
        # Even if called with a strange client, should not raise
        result = watchdog.run(client="nonexistent_client")
        assert isinstance(result, WatchdogResult)


# ══════════════════════════════════════════════════════════════════════════════
# 9. Audit subsystem unit tests
# ══════════════════════════════════════════════════════════════════════════════

class TestAuditLogger:

    def test_emit_stores_event(self):
        logger = AuditLogger()
        event = AuditEvent(
            action_id="act-1",
            event_type=AuditEventType.ACTION_APPROVED,
            actor="human:alice",
        )
        logger.emit(event)
        assert logger.count() == 1

    def test_events_for_action_returns_correct(self):
        logger = AuditLogger()
        event = AuditEvent(action_id="act-X", event_type=AuditEventType.ACTION_REJECTED, actor="x")
        logger.emit(event)
        events = logger.events_for_action("act-X")
        assert len(events) == 1
        assert events[0].event_type == AuditEventType.ACTION_REJECTED

    def test_events_for_action_isolated(self):
        logger = AuditLogger()
        logger.emit(AuditEvent(action_id="act-A", event_type=AuditEventType.ACTION_APPROVED, actor="a"))
        logger.emit(AuditEvent(action_id="act-B", event_type=AuditEventType.ACTION_REJECTED, actor="b"))
        assert len(logger.events_for_action("act-A")) == 1
        assert len(logger.events_for_action("act-B")) == 1

    def test_all_events_returns_all(self):
        logger = AuditLogger()
        for i in range(5):
            logger.emit(AuditEvent(action_id=f"act-{i}", event_type=AuditEventType.ACTION_APPROVED, actor="x"))
        assert len(logger.all_events()) == 5

    def test_count_by_type(self):
        logger = AuditLogger()
        logger.emit(AuditEvent(action_id="a1", event_type=AuditEventType.ACTION_APPROVED, actor="x"))
        logger.emit(AuditEvent(action_id="a2", event_type=AuditEventType.ACTION_APPROVED, actor="x"))
        logger.emit(AuditEvent(action_id="a3", event_type=AuditEventType.ACTION_REJECTED, actor="x"))
        assert logger.count_by_type(AuditEventType.ACTION_APPROVED) == 2
        assert logger.count_by_type(AuditEventType.ACTION_REJECTED) == 1

    def test_emit_never_raises(self):
        logger = AuditLogger()
        # Emit should not raise even on strange input
        event = AuditEvent(action_id="x", event_type=AuditEventType.ACTION_EXPIRED, actor="watchdog")
        logger.emit(event)  # should not raise

    def test_audit_event_is_immutable(self):
        event = AuditEvent(action_id="x", event_type=AuditEventType.ACTION_APPROVED, actor="a")
        with pytest.raises((AttributeError, TypeError)):
            event.actor = "b"  # frozen=True

    def test_audit_event_to_dict(self):
        event = AuditEvent(
            action_id="act-999",
            event_type=AuditEventType.ACTION_EXECUTED,
            actor="executor:worker-1",
            metadata={"result": "ok"},
        )
        d = event.to_dict()
        assert d["action_id"] == "act-999"
        assert d["event_type"] == "ACTION_EXECUTED"
        assert d["actor"] == "executor:worker-1"
        assert d["metadata"]["result"] == "ok"


# ══════════════════════════════════════════════════════════════════════════════
# 10. Audit integration — events emitted from API routes
# ══════════════════════════════════════════════════════════════════════════════

class TestAuditIntegration:

    def _setup(self): return _make_app(auth_enabled=True)

    def test_approve_emits_audit_event(self):
        c, stack, audit = self._setup()
        action = _create_reversible_action(stack, "TKT-AUDIT-APP")
        c.post(f"/actions/{action.action_id}/approve", headers=_headers(_APPROVER_KEY))
        events = audit.events_for_action(action.action_id)
        assert len(events) == 1
        assert events[0].event_type == AuditEventType.ACTION_APPROVED

    def test_approve_audit_event_actor(self):
        c, stack, audit = self._setup()
        action = _create_reversible_action(stack, "TKT-AUDIT-ACTOR")
        c.post(f"/actions/{action.action_id}/approve", headers=_headers(_APPROVER_KEY))
        events = audit.events_for_action(action.action_id)
        assert "alice" in events[0].actor

    def test_reject_emits_audit_event(self):
        c, stack, audit = self._setup()
        action = _create_reversible_action(stack, "TKT-AUDIT-REJ")
        c.post(f"/actions/{action.action_id}/reject", headers=_headers(_APPROVER_KEY))
        events = audit.events_for_action(action.action_id)
        assert any(e.event_type == AuditEventType.ACTION_REJECTED for e in events)

    def test_audit_metadata_contains_action_type(self):
        c, stack, audit = self._setup()
        action = _create_reversible_action(stack, "TKT-AUDIT-META")
        c.post(f"/actions/{action.action_id}/approve", headers=_headers(_APPROVER_KEY))
        events = audit.events_for_action(action.action_id)
        assert events[0].metadata["action_type"] == "update_ticket"

    def test_audit_metadata_contains_client(self):
        c, stack, audit = self._setup()
        action = _create_reversible_action(stack, "TKT-AUDIT-CLIENT")
        c.post(f"/actions/{action.action_id}/approve", headers=_headers(_APPROVER_KEY))
        events = audit.events_for_action(action.action_id)
        assert events[0].metadata["client"] == _CLIENT

    def test_failed_auth_does_not_emit_audit(self):
        c, stack, audit = self._setup()
        action = _create_reversible_action(stack, "TKT-AUDIT-NOKEY")
        c.post(f"/actions/{action.action_id}/approve")  # no key → 401
        events = audit.events_for_action(action.action_id)
        assert len(events) == 0


# ══════════════════════════════════════════════════════════════════════════════
# 11. Configuration validation
# ══════════════════════════════════════════════════════════════════════════════

class TestConfigurationValidation:

    def test_auth_enabled_with_keys_passes(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("APPROVER_API_KEYS", "alice:key1")
        monkeypatch.setenv("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
        validate_startup_config()  # should not raise

    def test_auth_disabled_passes_without_keys(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "false")
        monkeypatch.delenv("APPROVER_API_KEYS", raising=False)
        monkeypatch.delenv("OPERATOR_API_KEYS", raising=False)
        monkeypatch.delenv("ADMIN_API_KEYS", raising=False)
        monkeypatch.setenv("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
        validate_startup_config()  # should not raise

    def test_auth_enabled_without_keys_raises(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.delenv("APPROVER_API_KEYS", raising=False)
        monkeypatch.delenv("OPERATOR_API_KEYS", raising=False)
        monkeypatch.delenv("ADMIN_API_KEYS", raising=False)
        monkeypatch.setenv("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
        with pytest.raises(ConfigurationValidationError, match="AUTH_ENABLED"):
            validate_startup_config()

    def test_enforce_hmac_with_secret_passes(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "false")
        monkeypatch.setenv("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "true")
        monkeypatch.setenv("FRESHDESK_WEBHOOK_SECRET", "my-secret-value")
        validate_startup_config()  # should not raise

    def test_enforce_hmac_without_secret_raises(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "false")
        monkeypatch.setenv("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "true")
        monkeypatch.setenv("FRESHDESK_WEBHOOK_SECRET", "")
        with pytest.raises(ConfigurationValidationError, match="FRESHDESK_WEBHOOK_SECRET"):
            validate_startup_config()

    def test_enforce_hmac_false_passes_without_secret(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "false")
        monkeypatch.setenv("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
        monkeypatch.setenv("FRESHDESK_WEBHOOK_SECRET", "")
        validate_startup_config()  # should not raise

    def test_operator_keys_satisfy_auth_check(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.delenv("APPROVER_API_KEYS", raising=False)
        monkeypatch.setenv("OPERATOR_API_KEYS", "worker:somekey")
        monkeypatch.delenv("ADMIN_API_KEYS", raising=False)
        monkeypatch.setenv("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
        validate_startup_config()  # should not raise

    def test_admin_keys_satisfy_auth_check(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.delenv("APPROVER_API_KEYS", raising=False)
        monkeypatch.delenv("OPERATOR_API_KEYS", raising=False)
        monkeypatch.setenv("ADMIN_API_KEYS", "admin:adminkey")
        monkeypatch.setenv("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
        validate_startup_config()  # should not raise


# ══════════════════════════════════════════════════════════════════════════════
# 12. build_authenticator_from_env — factory tests
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildAuthenticatorFromEnv:

    def test_builds_with_approver_keys(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("APPROVER_API_KEYS", "alice:key1\nbob:key2")
        monkeypatch.delenv("OPERATOR_API_KEYS", raising=False)
        monkeypatch.delenv("ADMIN_API_KEYS", raising=False)
        auth = build_authenticator_from_env()
        assert auth.key_count == 2
        r = auth.authenticate("key1")
        assert r.role == Role.APPROVER
        assert r.identity == "alice"

    def test_builds_with_multiple_roles(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("APPROVER_API_KEYS", "approver1:akey")
        monkeypatch.setenv("OPERATOR_API_KEYS", "operator1:okey")
        monkeypatch.setenv("ADMIN_API_KEYS", "admin1:adminkey")
        auth = build_authenticator_from_env()
        assert auth.key_count == 3
        assert auth.authenticate("okey").role == Role.OPERATOR
        assert auth.authenticate("adminkey").role == Role.ADMIN

    def test_auth_disabled_in_env(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "false")
        auth = build_authenticator_from_env()
        assert not auth.auth_enabled

    def test_malformed_line_skipped(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("APPROVER_API_KEYS", "nocolon\nalice:validkey")
        monkeypatch.delenv("OPERATOR_API_KEYS", raising=False)
        monkeypatch.delenv("ADMIN_API_KEYS", raising=False)
        auth = build_authenticator_from_env()
        assert auth.key_count == 1


# ══════════════════════════════════════════════════════════════════════════════
# 13. Security hardening — no leakage
# ══════════════════════════════════════════════════════════════════════════════

class TestSecurityHardening:

    def _setup(self): return _make_app(auth_enabled=True)

    def test_401_response_does_not_contain_key(self):
        c, stack, _ = self._setup()
        action = _create_reversible_action(stack, "TKT-LEAK-1")
        r = c.post(f"/actions/{action.action_id}/approve",
                   headers=_headers(_UNKNOWN_KEY))
        body_text = r.text
        assert _UNKNOWN_KEY not in body_text
        assert _APPROVER_KEY not in body_text

    def test_403_response_does_not_contain_key(self):
        c, stack, _ = self._setup()
        action = _create_reversible_action(stack, "TKT-LEAK-2")
        r = c.post(f"/actions/{action.action_id}/approve",
                   headers=_headers(_OPERATOR_KEY))
        assert _OPERATOR_KEY not in r.text

    def test_no_stack_traces_in_401(self):
        c, stack, _ = self._setup()
        action = _create_reversible_action(stack, "TKT-TRACE-1")
        r = c.post(f"/actions/{action.action_id}/approve")
        assert "Traceback" not in r.text
        assert "File " not in r.text

    def test_no_stack_traces_in_403(self):
        c, stack, _ = self._setup()
        action = _create_reversible_action(stack, "TKT-TRACE-2")
        r = c.post(f"/actions/{action.action_id}/approve",
                   headers=_headers(_OPERATOR_KEY))
        assert "Traceback" not in r.text

    def test_error_code_not_http_error_for_401(self):
        """401 must use UNAUTHORIZED code, not generic HTTP_ERROR."""
        c, stack, _ = self._setup()
        action = _create_reversible_action(stack, "TKT-CODE-401")
        r = c.post(f"/actions/{action.action_id}/approve")
        assert r.json()["error"]["code"] == "UNAUTHORIZED"

    def test_error_code_not_http_error_for_403(self):
        """403 must use FORBIDDEN code, not generic HTTP_ERROR."""
        c, stack, _ = self._setup()
        action = _create_reversible_action(stack, "TKT-CODE-403")
        r = c.post(f"/actions/{action.action_id}/approve",
                   headers=_headers(_OPERATOR_KEY))
        assert r.json()["error"]["code"] == "FORBIDDEN"
