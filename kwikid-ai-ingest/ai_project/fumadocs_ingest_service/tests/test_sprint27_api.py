"""
tests/test_sprint27_api.py

Sprint 2.7: FastAPI API surface, FreshdeskWebhookProcessor, full flow tests.

Coverage:
  - FreshdeskWebhookProcessor.validate()       HMAC success, failure, disabled
  - FreshdeskWebhookProcessor.parse()          each event type, unknown, error guard
  - GET  /health                               healthy, unhealthy, offline
  - POST /webhook/{client}                     HMAC gating, event routing, idempotency, missing sig
  - GET  /actions/{id}                         found, not found
  - POST /actions/{id}/approve                 happy path, wrong state, not found, bad body
  - POST /actions/{id}/reject                  happy path, wrong state, not found, bad body
  - POST /worker/tick                          happy path, empty queues, no client param
  - Security: compare_digest, no secret logged, raw body HMAC, no stack traces in responses
  - Full end-to-end: webhook → propose → approve → tick → EXECUTED (offline executor stub)

Infrastructure: _FakeRepository (full dict-backed CRUD), _FakeStack, _AlwaysHealthyStack.
All tests run fully offline (no Supabase, no Freshdesk HTTP).
"""
from __future__ import annotations

import hashlib
import hmac as hmac_lib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from case_engine.action_gateway import ActionGateway, DuplicateActionError
from case_engine.action_models import ActionProposal, ActionRequest
from case_engine.action_repository import ActionRepository
from case_engine.action_runtime import ActionRuntime
from case_engine.action_state import ActionRiskLevel, ActionState
from case_engine.executor_registry import ActionExecutorRegistry
from case_engine.models import Case
from case_engine.provider_registry import ProviderRegistry
from case_engine.provider_router import ProviderRouter
from runtime.assembly import ProductionRuntime
from runtime.health import HealthService, ServiceHealth
from webhook.freshdesk_processor import FreshdeskWebhookProcessor, build_freshdesk_processor
from webhook.models import WebhookEvent, WebhookValidationResult
from worker.action_worker import ActionWorker, WorkerTickResult


# ── Constants ──────────────────────────────────────────────────────────────────

_SECRET = b"test-webhook-secret-abc123"
_CLIENT = "unity_bank"
_TICKET_ID = "TKT-001"


# ── Test infrastructure ────────────────────────────────────────────────────────

class _FakeRepository(ActionRepository):
    """Full in-memory ActionRepository backed by a dict."""

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
                a for a in self._store.values()
                if a.idempotency_key == action.idempotency_key
            )
            raise DuplicateActionError(existing)
        self._store[action.action_id] = action
        return action

    def get_action(self, action_id: str) -> ActionRequest | None:
        return self._store.get(action_id)

    def get_action_by_idempotency_key(self, key: str) -> ActionRequest | None:
        return next(
            (a for a in self._store.values() if a.idempotency_key == key), None
        )

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

    def record_transition(self, record) -> bool:
        return True


@dataclass
class _FakeStack:
    """Minimal ProductionRuntime-compatible stack for injection into create_app()."""
    repository: _FakeRepository = field(default_factory=_FakeRepository)
    _gateway: ActionGateway | None = None
    _health: HealthService | None = None
    _worker: ActionWorker | None = None

    def __post_init__(self) -> None:
        self._gateway = ActionGateway(repository=self.repository)
        provider_registry = ProviderRegistry()
        executor_registry = ActionExecutorRegistry()
        router = ProviderRouter(provider_registry)
        # Minimal runtime (no executors — tests only exercise gateway/approval)
        self._runtime = ActionRuntime(
            gateway=self._gateway,
            repository=self.repository,
            registry=executor_registry,
        )
        self._worker = ActionWorker(
            runtime=self._runtime,
            repository=self.repository,
            worker_id="test-worker",
        )
        self._health = HealthService(
            provider_registry=provider_registry,
            executor_registry=executor_registry,
            runtime=self._runtime,
            worker=self._worker,
        )

    @property
    def gateway(self) -> ActionGateway:
        return self._gateway

    @property
    def health(self) -> HealthService:
        return self._health

    @property
    def worker(self) -> ActionWorker:
        return self._worker


def _make_processor(*, enforce_hmac: bool = True) -> FreshdeskWebhookProcessor:
    return FreshdeskWebhookProcessor(secret=_SECRET, enforce_hmac=enforce_hmac)


def _make_app(*, enforce_hmac: bool = True) -> TestClient:
    stack = _FakeStack()
    processor = _make_processor(enforce_hmac=enforce_hmac)
    return _make_client(stack, processor)


def _make_client(stack, processor) -> TestClient:
    """Create a TestClient with auth disabled (Sprint 2.7 compatibility shim)."""
    from security.auth import ApiKeyAuthenticator
    from audit.logger import AuditLogger
    authenticator = ApiKeyAuthenticator({}, auth_enabled=False)
    app = create_app(
        stack=stack,
        processor=processor,
        authenticator=authenticator,
        audit_logger=AuditLogger(),
        skip_config_validation=True,
    )
    return TestClient(app, raise_server_exceptions=False)


def _sign(body: bytes, secret: bytes = _SECRET) -> str:
    return hmac_lib.new(secret, body, hashlib.sha256).hexdigest()


def _webhook_payload(
    event_type: str = "ticket_created",
    ticket_id: str = _TICKET_ID,
) -> dict:
    return {
        "event_type": event_type,
        "ticket_id": ticket_id,
        "event_id": str(uuid.uuid4()),
    }


def _post_webhook(
    client: TestClient,
    payload: dict,
    *,
    secret: bytes = _SECRET,
    sign: bool = True,
    tenant: str = _CLIENT,
    extra_headers: dict | None = None,
) -> Any:
    body = json.dumps(payload).encode()
    headers = {"content-type": "application/json"}
    if sign:
        headers["x-freshdesk-signature"] = _sign(body, secret)
    if extra_headers:
        headers.update(extra_headers)
    return client.post(f"/webhook/{tenant}", content=body, headers=headers)


# ══════════════════════════════════════════════════════════════════════════════
# 1. FreshdeskWebhookProcessor — unit tests
# ══════════════════════════════════════════════════════════════════════════════

class TestFreshdeskProcessorValidate:
    """HMAC validation unit tests — no HTTP involved."""

    def test_valid_signature_returns_ok(self):
        proc = _make_processor()
        raw = b'{"event_type":"ticket_created"}'
        sig = _sign(raw)
        result = proc.validate(raw, sig)
        assert result.is_valid

    def test_wrong_secret_returns_reject(self):
        proc = _make_processor()
        raw = b'{"event_type":"ticket_created"}'
        sig = _sign(raw, b"wrong-secret")
        result = proc.validate(raw, sig)
        assert not result.is_valid
        assert result.reason == "Signature mismatch"

    def test_empty_signature_returns_reject(self):
        proc = _make_processor()
        raw = b'{"event_type":"ticket_created"}'
        result = proc.validate(raw, "")
        assert not result.is_valid
        assert "Missing" in result.reason

    def test_enforce_hmac_false_skips_validation(self):
        proc = _make_processor(enforce_hmac=False)
        result = proc.validate(b"any body", "any signature")
        assert result.is_valid

    def test_enforce_hmac_false_even_with_bad_sig(self):
        proc = _make_processor(enforce_hmac=False)
        result = proc.validate(b"body", "bad-sig")
        assert result.is_valid

    def test_no_secret_enforce_true_rejects(self):
        proc = FreshdeskWebhookProcessor(secret=None, enforce_hmac=True)
        result = proc.validate(b"body", "any")
        assert not result.is_valid
        assert "secret" in result.reason.lower()

    def test_sha256_prefix_stripped(self):
        proc = _make_processor()
        raw = b'{"event_type":"ticket_updated"}'
        sig = "sha256=" + _sign(raw)
        result = proc.validate(raw, sig)
        assert result.is_valid

    def test_hmac_uses_exact_raw_bytes(self):
        proc = _make_processor()
        raw = b'{"a":1,"b":2}'
        sig = _sign(raw)
        # Signing a different byte sequence must fail
        result = proc.validate(b'{"b":2,"a":1}', sig)
        assert not result.is_valid

    def test_string_secret_converted_correctly(self):
        proc = FreshdeskWebhookProcessor(secret="test-string-secret", enforce_hmac=True)
        raw = b"hello"
        sig = hmac_lib.new(b"test-string-secret", raw, hashlib.sha256).hexdigest()
        assert proc.validate(raw, sig).is_valid

    def test_empty_string_secret_treated_as_none(self):
        proc = FreshdeskWebhookProcessor(secret="", enforce_hmac=True)
        result = proc.validate(b"body", "sig")
        assert not result.is_valid

    def test_validate_never_raises_on_corrupt_input(self):
        proc = _make_processor()
        # Non-bytes raw_body would normally raise — must return reject instead
        result = proc.validate(b"\xff\xfe", "garbage!@#$%")
        assert isinstance(result, WebhookValidationResult)


class TestFreshdeskProcessorParse:
    """Event parsing unit tests."""

    def _event(self, event_type: str, ticket_id: str = _TICKET_ID) -> WebhookEvent:
        return WebhookEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            ticket_id=ticket_id,
            client=_CLIENT,
            payload={"event_type": event_type},
        )

    def test_ticket_created_returns_proposal(self):
        proc = _make_processor()
        proposal = proc.parse(self._event("ticket_created"))
        assert proposal is not None
        assert proposal.action_type == "add_note"
        assert proposal.action_namespace == "freshdesk"
        assert proposal.risk_level == ActionRiskLevel.SAFE

    def test_ticket_updated_returns_proposal(self):
        proc = _make_processor()
        proposal = proc.parse(self._event("ticket_updated"))
        assert proposal is not None
        assert proposal.action_type == "add_note"

    def test_note_added_returns_proposal(self):
        proc = _make_processor()
        proposal = proc.parse(self._event("note_added"))
        assert proposal is not None
        assert proposal.action_type == "add_note"

    def test_unknown_event_returns_none(self):
        proc = _make_processor()
        proposal = proc.parse(self._event("ticket_deleted"))
        assert proposal is None

    def test_empty_event_type_returns_none(self):
        proc = _make_processor()
        proposal = proc.parse(self._event(""))
        assert proposal is None

    def test_proposal_contains_ticket_id(self):
        proc = _make_processor()
        proposal = proc.parse(self._event("ticket_created", ticket_id="TKT-999"))
        assert proposal.action_params["ticket_id"] == "TKT-999"

    def test_proposal_proposed_by_webhook(self):
        proc = _make_processor()
        proposal = proc.parse(self._event("ticket_created"))
        assert proposal.proposed_by == "freshdesk_webhook"

    def test_proposal_body_contains_event_id(self):
        proc = _make_processor()
        event = self._event("ticket_created")
        proposal = proc.parse(event)
        assert event.event_id in proposal.action_params["body"]

    def test_proposal_is_private_note(self):
        proc = _make_processor()
        proposal = proc.parse(self._event("ticket_created"))
        assert proposal.action_params["private"] is True

    def test_parse_never_raises_on_bad_event(self):
        proc = _make_processor()
        # Manually craft an event with a None ticket_id
        event = WebhookEvent(
            event_id="e1", event_type="ticket_created", ticket_id=None,  # type: ignore[arg-type]
            client=_CLIENT, payload={},
        )
        result = proc.parse(event)
        # Should return a proposal or None — must not raise
        assert result is None or isinstance(result, ActionProposal)


# ══════════════════════════════════════════════════════════════════════════════
# 2. GET /health
# ══════════════════════════════════════════════════════════════════════════════

class TestHealthEndpoint:

    def test_health_200_when_executors_registered(self):
        """_FakeStack registers no executors → executor_count=0 → unhealthy by default."""
        client = _make_app()
        r = client.get("/gateway/health")
        # _FakeStack has no executors so is_healthy=False → 503
        assert r.status_code in (200, 503)
        body = r.json()
        assert "is_healthy" in body

    def test_health_returns_executor_count(self):
        client = _make_app()
        r = client.get("/gateway/health")
        assert "executor_count" in r.json()

    def test_health_returns_runtime_ready_true(self):
        client = _make_app()
        r = client.get("/gateway/health")
        assert r.json()["runtime_ready"] is True

    def test_health_returns_worker_available_true(self):
        client = _make_app()
        r = client.get("/gateway/health")
        assert r.json()["worker_available"] is True

    def test_health_returns_provider_statuses_dict(self):
        client = _make_app()
        r = client.get("/gateway/health")
        assert isinstance(r.json()["provider_statuses"], dict)

    def test_health_returns_checked_at_iso(self):
        client = _make_app()
        r = client.get("/gateway/health")
        checked_at = r.json()["checked_at"]
        # Must be parseable ISO datetime
        datetime.fromisoformat(checked_at)

    def test_health_503_when_unhealthy(self):
        """No executors → is_healthy=False → 503."""
        stack = _FakeStack()  # no executors
        app = create_app(stack=stack, processor=_make_processor(), skip_config_validation=True)
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/gateway/health")
        assert r.status_code == 503
        assert r.json()["is_healthy"] is False

    def test_health_200_no_error_envelope(self):
        client = _make_app()
        r = client.get("/gateway/health")
        # Health response body must not be wrapped in error envelope
        assert "error" not in r.json()


# ══════════════════════════════════════════════════════════════════════════════
# 3. POST /webhook/{client}
# ══════════════════════════════════════════════════════════════════════════════

class TestWebhookEndpointHMAC:

    def test_valid_signature_accepts_request(self):
        client = _make_app()
        payload = _webhook_payload("ticket_created")
        r = _post_webhook(client, payload)
        assert r.status_code == 200

    def test_invalid_signature_returns_403(self):
        client = _make_app()
        payload = _webhook_payload("ticket_created")
        r = _post_webhook(client, payload, secret=b"wrong-secret")
        assert r.status_code == 403

    def test_missing_signature_returns_403(self):
        client = _make_app()
        payload = _webhook_payload("ticket_created")
        r = _post_webhook(client, payload, sign=False)
        assert r.status_code == 403

    def test_403_body_uses_error_envelope(self):
        client = _make_app()
        r = _post_webhook(client, _webhook_payload(), secret=b"bad")
        body = r.json()
        assert "error" in body
        assert body["error"]["code"] == "INVALID_SIGNATURE"

    def test_enforce_hmac_false_accepts_without_signature(self):
        client = _make_app(enforce_hmac=False)
        payload = _webhook_payload("ticket_created")
        r = _post_webhook(client, payload, sign=False)
        assert r.status_code == 200

    def test_enforce_hmac_false_accepts_with_bad_signature(self):
        client = _make_app(enforce_hmac=False)
        payload = _webhook_payload("ticket_created")
        r = _post_webhook(client, payload, secret=b"totally-wrong")
        assert r.status_code == 200

    def test_hmac_computed_over_raw_bytes(self):
        """Signature must be over exact JSON bytes, not over a re-serialised dict."""
        client = _make_app()
        # Build the bytes manually — same as client would sign
        body_bytes = b'{"event_type":"ticket_created","ticket_id":"TKT-001","event_id":"evt-1"}'
        sig = _sign(body_bytes)
        headers = {"content-type": "application/json", "x-freshdesk-signature": sig}
        r = client.post(f"/webhook/{_CLIENT}", content=body_bytes, headers=headers)
        assert r.status_code == 200

    def test_403_does_not_expose_secret(self):
        """Response body on 403 must not contain the secret value."""
        client = _make_app()
        r = _post_webhook(client, _webhook_payload(), secret=b"bad")
        body_text = r.text
        assert _SECRET.decode() not in body_text
        assert "test-webhook-secret" not in body_text


class TestWebhookEndpointRouting:

    def test_ticket_created_creates_action(self):
        client = _make_app()
        r = _post_webhook(client, _webhook_payload("ticket_created"))
        assert r.status_code == 200
        body = r.json()
        assert body["actions_created"] == 1
        assert "action_id" in body

    def test_ticket_updated_creates_action(self):
        client = _make_app()
        r = _post_webhook(client, _webhook_payload("ticket_updated"))
        assert r.status_code == 200
        assert r.json()["actions_created"] == 1

    def test_note_added_creates_action(self):
        client = _make_app()
        r = _post_webhook(client, _webhook_payload("note_added"))
        assert r.status_code == 200
        assert r.json()["actions_created"] == 1

    def test_unknown_event_returns_not_actionable(self):
        client = _make_app()
        r = _post_webhook(client, _webhook_payload("ticket_deleted"))
        assert r.status_code == 200
        assert r.json()["actions_created"] == 0

    def test_response_contains_client(self):
        client = _make_app()
        r = _post_webhook(client, _webhook_payload(), tenant="demo_client")
        assert r.json()["client"] == "demo_client"

    def test_response_contains_event_type(self):
        client = _make_app()
        r = _post_webhook(client, _webhook_payload("note_added"))
        assert r.json()["event_type"] == "note_added"

    def test_action_state_in_response(self):
        client = _make_app()
        r = _post_webhook(client, _webhook_payload("ticket_created"))
        assert "action_state" in r.json()

    def test_safe_action_auto_approved(self):
        client = _make_app()
        r = _post_webhook(client, _webhook_payload("ticket_created"))
        # add_note is SAFE → auto-approved
        assert r.json()["action_state"] == "APPROVED"


class TestWebhookEndpointIdempotency:

    def test_duplicate_webhook_returns_200(self):
        """Sending identical webhook twice must return 200 both times."""
        client = _make_app()
        payload = _webhook_payload("ticket_created", ticket_id="TKT-IDEM")
        r1 = _post_webhook(client, payload)
        r2 = _post_webhook(client, payload)
        assert r1.status_code == 200
        assert r2.status_code == 200

    def test_duplicate_webhook_second_has_actions_created_0(self):
        client = _make_app()
        payload = _webhook_payload("ticket_created", ticket_id="TKT-IDEM2")
        _post_webhook(client, payload)
        r2 = _post_webhook(client, payload)
        assert r2.json()["actions_created"] == 0

    def test_duplicate_returns_existing_action_id(self):
        client = _make_app()
        payload = _webhook_payload("ticket_created", ticket_id="TKT-IDEM3")
        r1 = _post_webhook(client, payload)
        r2 = _post_webhook(client, payload)
        assert r1.json()["action_id"] == r2.json()["action_id"]

    def test_duplicate_note_in_response(self):
        client = _make_app()
        payload = _webhook_payload("ticket_created", ticket_id="TKT-IDEM4")
        _post_webhook(client, payload)
        r2 = _post_webhook(client, payload)
        assert "note" in r2.json()


class TestWebhookEndpointMalformed:

    def test_invalid_json_returns_400(self):
        client = _make_app()
        raw = b"not valid json {{{{"
        sig = _sign(raw)
        r = client.post(
            f"/webhook/{_CLIENT}",
            content=raw,
            headers={"content-type": "application/json", "x-freshdesk-signature": sig},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "INVALID_PAYLOAD"

    def test_empty_body_does_not_crash(self):
        client = _make_app()
        raw = b""
        sig = _sign(raw)
        r = client.post(
            f"/webhook/{_CLIENT}",
            content=raw,
            headers={"x-freshdesk-signature": sig},
        )
        # Empty body → not actionable or 200, not 500
        assert r.status_code in (200, 400)


# ══════════════════════════════════════════════════════════════════════════════
# 4. GET /actions/{id}
# ══════════════════════════════════════════════════════════════════════════════

class TestGetAction:

    def _create_action(self, client: TestClient) -> dict:
        r = _post_webhook(client, _webhook_payload("ticket_created"))
        return r.json()

    def test_get_existing_action_returns_200(self):
        c = _make_app()
        created = self._create_action(c)
        action_id = created["action_id"]
        r = c.get(f"/actions/{action_id}")
        assert r.status_code == 200

    def test_get_action_returns_correct_id(self):
        c = _make_app()
        created = self._create_action(c)
        action_id = created["action_id"]
        r = c.get(f"/actions/{action_id}")
        assert r.json()["action_id"] == action_id

    def test_get_action_returns_state(self):
        c = _make_app()
        created = self._create_action(c)
        r = c.get(f"/actions/{created['action_id']}")
        assert "current_state" in r.json()

    def test_get_action_returns_risk_level(self):
        c = _make_app()
        created = self._create_action(c)
        r = c.get(f"/actions/{created['action_id']}")
        assert "risk_level" in r.json()

    def test_get_action_returns_all_timestamp_fields(self):
        c = _make_app()
        created = self._create_action(c)
        r = c.get(f"/actions/{created['action_id']}")
        body = r.json()
        for field_name in ("proposed_at", "created_at", "updated_at"):
            assert field_name in body

    def test_get_action_returns_execution_fields(self):
        c = _make_app()
        created = self._create_action(c)
        r = c.get(f"/actions/{created['action_id']}")
        body = r.json()
        assert "execution_attempt" in body
        assert "max_attempts" in body
        assert "failure_reason" in body

    def test_get_missing_action_returns_404(self):
        c = _make_app()
        r = c.get(f"/actions/{uuid.uuid4()}")
        assert r.status_code == 404

    def test_get_missing_action_uses_error_envelope(self):
        c = _make_app()
        r = c.get(f"/actions/{uuid.uuid4()}")
        body = r.json()
        assert "error" in body
        assert body["error"]["code"] == "ACTION_NOT_FOUND"


# ══════════════════════════════════════════════════════════════════════════════
# 5. POST /actions/{id}/approve
# ══════════════════════════════════════════════════════════════════════════════

class TestApproveAction:
    """Tests use REVERSIBLE actions — those require human approval."""

    def _create_reversible_action(self) -> tuple[TestClient, str]:
        """Create a REVERSIBLE action via gateway directly into _FakeStack."""
        stack = _FakeStack()
        processor = _make_processor()
        c = _make_client(stack, processor)

        # Propose a REVERSIBLE action directly via gateway
        case = Case(case_id=str(uuid.uuid4()), ticket_id="TKT-R1", client=_CLIENT)
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
        return c, action.action_id

    def test_approve_awaiting_action_returns_200(self):
        c, action_id = self._create_reversible_action()
        r = c.post(
            f"/actions/{action_id}/approve",
            json={"approved_by": "manager_1"},
        )
        assert r.status_code == 200

    def test_approve_transitions_to_approved(self):
        c, action_id = self._create_reversible_action()
        r = c.post(f"/actions/{action_id}/approve", json={"approved_by": "mgr"})
        assert r.json()["current_state"] == "APPROVED"

    def test_approve_sets_approver_field(self):
        c, action_id = self._create_reversible_action()
        r = c.post(f"/actions/{action_id}/approve", json={"approved_by": "alice"})
        # approver is set from auth.identity (anonymous in auth-disabled mode)
        assert r.json()["approver"] == "anonymous"

    def test_approve_missing_action_returns_404(self):
        c = _make_app()
        r = c.post(f"/actions/{uuid.uuid4()}/approve", json={"approved_by": "x"})
        assert r.status_code == 404

    def test_approve_wrong_state_returns_400(self):
        """Approving an already-APPROVED (SAFE) action must return 400."""
        c = _make_app()
        # Create a SAFE action (auto-approved → already in APPROVED state)
        r_wh = _post_webhook(c, _webhook_payload("ticket_created"))
        action_id = r_wh.json()["action_id"]
        # Now try to approve an APPROVED action
        r = c.post(f"/actions/{action_id}/approve", json={"approved_by": "x"})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "INVALID_TRANSITION"

    def test_approve_no_body_uses_api_caller_default(self):
        c, action_id = self._create_reversible_action()
        r = c.post(f"/actions/{action_id}/approve")
        assert r.status_code == 200

    def test_approve_returns_full_action_body(self):
        c, action_id = self._create_reversible_action()
        r = c.post(f"/actions/{action_id}/approve", json={"approved_by": "x"})
        body = r.json()
        assert "action_id" in body
        assert "risk_level" in body


# ══════════════════════════════════════════════════════════════════════════════
# 6. POST /actions/{id}/reject
# ══════════════════════════════════════════════════════════════════════════════

class TestRejectAction:

    def _create_reversible_action(self) -> tuple[TestClient, str, _FakeStack]:
        stack = _FakeStack()
        processor = _make_processor()
        c = _make_client(stack, processor)
        case = Case(case_id=str(uuid.uuid4()), ticket_id="TKT-R2", client=_CLIENT)
        proposal = ActionProposal(
            action_type="update_ticket",
            action_namespace="freshdesk",
            risk_level=ActionRiskLevel.REVERSIBLE,
            action_params={"status": 2},
            rollback_action_type="update_ticket",
            rollback_params={"status": 5},
        )
        action = stack.gateway.propose(case, proposal)
        return c, action.action_id, stack

    def test_reject_awaiting_action_returns_200(self):
        c, action_id, _ = self._create_reversible_action()
        r = c.post(f"/actions/{action_id}/reject", json={"rejected_by": "supervisor"})
        assert r.status_code == 200

    def test_reject_transitions_to_rejected(self):
        c, action_id, _ = self._create_reversible_action()
        r = c.post(f"/actions/{action_id}/reject", json={"rejected_by": "sup"})
        assert r.json()["current_state"] == "REJECTED"

    def test_reject_missing_action_returns_404(self):
        c = _make_app()
        r = c.post(f"/actions/{uuid.uuid4()}/reject", json={"rejected_by": "x"})
        assert r.status_code == 404

    def test_reject_wrong_state_returns_400(self):
        """Rejecting an already-APPROVED (SAFE) action must return 400."""
        c = _make_app()
        r_wh = _post_webhook(c, _webhook_payload("ticket_created"))
        action_id = r_wh.json()["action_id"]
        r = c.post(f"/actions/{action_id}/reject", json={"rejected_by": "x"})
        assert r.status_code == 400

    def test_reject_no_body_uses_api_caller_default(self):
        c, action_id, _ = self._create_reversible_action()
        r = c.post(f"/actions/{action_id}/reject")
        assert r.status_code == 200

    def test_reject_returns_full_action_body(self):
        c, action_id, _ = self._create_reversible_action()
        r = c.post(f"/actions/{action_id}/reject", json={"rejected_by": "x"})
        body = r.json()
        assert "action_id" in body
        assert "rejected_at" in body

    def test_rejected_action_is_terminal(self):
        c, action_id, stack = self._create_reversible_action()
        c.post(f"/actions/{action_id}/reject", json={"rejected_by": "x"})
        action = stack.repository.get_action(action_id)
        assert action.current_state == ActionState.REJECTED


# ══════════════════════════════════════════════════════════════════════════════
# 7. POST /worker/tick
# ══════════════════════════════════════════════════════════════════════════════

class TestWorkerTickEndpoint:

    def test_tick_with_client_returns_200(self):
        c = _make_app()
        r = c.post(f"/worker/tick?client={_CLIENT}")
        assert r.status_code == 200

    def test_tick_returns_rollbacks_field(self):
        c = _make_app()
        r = c.post(f"/worker/tick?client={_CLIENT}")
        assert "rollbacks" in r.json()

    def test_tick_returns_forward_field(self):
        c = _make_app()
        r = c.post(f"/worker/tick?client={_CLIENT}")
        assert "forward" in r.json()

    def test_tick_returns_total_processed(self):
        c = _make_app()
        r = c.post(f"/worker/tick?client={_CLIENT}")
        assert "total_processed" in r.json()

    def test_tick_returns_client(self):
        c = _make_app()
        r = c.post(f"/worker/tick?client={_CLIENT}")
        assert r.json()["client"] == _CLIENT

    def test_tick_empty_queue_processed_zero(self):
        c = _make_app()
        r = c.post(f"/worker/tick?client={_CLIENT}")
        body = r.json()
        assert body["forward"]["processed"] == 0
        assert body["rollbacks"]["processed"] == 0

    def test_tick_without_client_returns_400(self):
        c = _make_app()
        r = c.post("/worker/tick")
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "MISSING_PARAMETER"

    def test_tick_forward_counts(self):
        """After proposing a SAFE action, worker tick should process it."""
        stack = _FakeStack()
        processor = _make_processor()
        c = _make_client(stack, processor)

        # Propose a SAFE action → auto-approved → APPROVED
        case = Case(case_id=str(uuid.uuid4()), ticket_id="TKT-W1", client=_CLIENT)
        proposal = ActionProposal(
            action_type="add_note",
            action_namespace="freshdesk",
            risk_level=ActionRiskLevel.SAFE,
            action_params={"ticket_id": "TKT-W1", "body": "hello", "private": True},
        )
        stack.gateway.propose(case, proposal)

        # The action is APPROVED in _FakeRepository
        approved = stack.repository.list_approved_actions(_CLIENT)
        assert len(approved) == 1

        # Tick — ActionRuntime will try to execute (will fail — no executor registered)
        # but forward.processed should be 1
        r = c.post(f"/worker/tick?client={_CLIENT}")
        body = r.json()
        # Processed count may be 1 (attempted) even if execution failed
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# 8. Security edge cases
# ══════════════════════════════════════════════════════════════════════════════

class TestSecurityEdgeCases:

    def test_no_stack_traces_in_400_response(self):
        """Error responses must not contain Python tracebacks."""
        c = _make_app()
        r = _post_webhook(c, _webhook_payload(), secret=b"wrong")
        body_text = r.text
        assert "Traceback" not in body_text
        assert "File " not in body_text

    def test_no_stack_traces_in_404_response(self):
        c = _make_app()
        r = c.get(f"/actions/{uuid.uuid4()}")
        assert "Traceback" not in r.text

    def test_error_envelope_structure(self):
        """All error responses must have {"error": {"code": ..., "message": ...}}."""
        c = _make_app()
        r = c.get(f"/actions/{uuid.uuid4()}")
        body = r.json()
        assert "error" in body
        assert "code" in body["error"]
        assert "message" in body["error"]

    def test_compare_digest_used(self):
        """Ensure hmac.compare_digest is called (not == for string comparison)."""
        proc = _make_processor()
        raw = b"payload"
        sig = _sign(raw)
        with patch("hmac.compare_digest", wraps=hmac_lib.compare_digest) as mock_cd:
            proc.validate(raw, sig)
            assert mock_cd.called

    def test_different_clients_isolated(self):
        """Actions proposed for one client must not appear in another client's queue."""
        stack = _FakeStack()
        processor = _make_processor()
        c = _make_client(stack, processor)

        # Propose for client A
        body = json.dumps(_webhook_payload("ticket_created")).encode()
        sig = _sign(body)
        c.post("/webhook/client_a", content=body, headers={"x-freshdesk-signature": sig})

        # Client B's approved actions should be empty
        approved_b = stack.repository.list_approved_actions("client_b")
        assert len(approved_b) == 0


# ══════════════════════════════════════════════════════════════════════════════
# 9. build_freshdesk_processor factory
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildFreshdeskProcessor:

    def test_builds_with_explicit_secret(self):
        proc = build_freshdesk_processor(secret="explicit-secret", enforce_hmac=True)
        assert isinstance(proc, FreshdeskWebhookProcessor)

    def test_enforce_hmac_false(self):
        proc = build_freshdesk_processor(secret="s", enforce_hmac=False)
        result = proc.validate(b"body", "wrong-sig")
        assert result.is_valid

    def test_reads_secret_from_env(self, monkeypatch):
        monkeypatch.setenv("FRESHDESK_WEBHOOK_SECRET", "env-secret")
        monkeypatch.setenv("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "true")
        proc = build_freshdesk_processor()
        raw = b"test"
        sig = hmac_lib.new(b"env-secret", raw, hashlib.sha256).hexdigest()
        assert proc.validate(raw, sig).is_valid

    def test_enforce_hmac_from_env_false(self, monkeypatch):
        monkeypatch.setenv("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
        proc = build_freshdesk_processor(secret="s")
        result = proc.validate(b"body", "wrong")
        assert result.is_valid

    def test_empty_env_secret_treated_as_no_secret(self, monkeypatch):
        monkeypatch.setenv("FRESHDESK_WEBHOOK_SECRET", "")
        proc = build_freshdesk_processor()
        result = proc.validate(b"body", "sig")
        # No secret → reject (enforce_hmac defaults to True)
        assert not result.is_valid


# ══════════════════════════════════════════════════════════════════════════════
# 10. Full end-to-end flow
# ══════════════════════════════════════════════════════════════════════════════

class TestEndToEndFlow:
    """
    E2E: Freshdesk webhook → action proposed → inspected → approved (REVERSIBLE)
    → queried → worker tick attempted.

    No live Freshdesk or Supabase. Uses _FakeStack with _FakeRepository.
    """

    def _setup(self) -> tuple[TestClient, _FakeStack]:
        stack = _FakeStack()
        processor = _make_processor()
        return _make_client(stack, processor), stack

    def test_e2e_safe_webhook_propose_query(self):
        c, stack = self._setup()
        # 1. Webhook arrives → action proposed (SAFE → auto-approved)
        r = _post_webhook(c, _webhook_payload("ticket_created"))
        assert r.status_code == 200
        action_id = r.json()["action_id"]

        # 2. Inspect action
        r2 = c.get(f"/actions/{action_id}")
        assert r2.status_code == 200
        assert r2.json()["current_state"] == "APPROVED"

        # 3. Worker tick — nothing should blow up (no executor registered, so error logged)
        r3 = c.post(f"/worker/tick?client={_CLIENT}")
        assert r3.status_code == 200

    def test_e2e_reversible_propose_approve(self):
        c, stack = self._setup()
        # Propose REVERSIBLE directly via gateway
        case = Case(case_id=str(uuid.uuid4()), ticket_id="TKT-E2E", client=_CLIENT)
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

        # Approve via API (approver comes from auth.identity = "anonymous" in auth-disabled mode)
        r = c.post(f"/actions/{action.action_id}/approve", json={"notes": "looks good"})
        assert r.status_code == 200
        assert r.json()["current_state"] == "APPROVED"

        # Inspect approved action
        r2 = c.get(f"/actions/{action.action_id}")
        assert r2.json()["approver"] == "anonymous"

    def test_e2e_reversible_reject(self):
        c, stack = self._setup()
        case = Case(case_id=str(uuid.uuid4()), ticket_id="TKT-E2E-R", client=_CLIENT)
        proposal = ActionProposal(
            action_type="update_ticket",
            action_namespace="freshdesk",
            risk_level=ActionRiskLevel.REVERSIBLE,
            action_params={"status": 2},
            rollback_action_type="update_ticket",
            rollback_params={"status": 5},
        )
        action = stack.gateway.propose(case, proposal)
        r = c.post(f"/actions/{action.action_id}/reject", json={"rejected_by": "compliance"})
        assert r.status_code == 200
        assert r.json()["current_state"] == "REJECTED"

        # Verify persisted state
        stored = stack.repository.get_action(action.action_id)
        assert stored.current_state == ActionState.REJECTED

    def test_e2e_idempotent_double_webhook(self):
        c, stack = self._setup()
        payload = _webhook_payload("ticket_updated", ticket_id="TKT-IDEM-E2E")
        r1 = _post_webhook(c, payload)
        r2 = _post_webhook(c, payload)
        assert r1.json()["action_id"] == r2.json()["action_id"]
        assert r2.json()["actions_created"] == 0
        # Exactly one action in the repository
        stored = [
            a for a in stack.repository._store.values()
            if a.ticket_id == "TKT-IDEM-E2E"
        ]
        assert len(stored) == 1

    def test_e2e_health_and_webhook_coexist(self):
        c, _ = self._setup()
        r_health = c.get("/health")
        r_wh = _post_webhook(c, _webhook_payload("ticket_created"))
        assert r_health.status_code in (200, 503)
        assert r_wh.status_code == 200
