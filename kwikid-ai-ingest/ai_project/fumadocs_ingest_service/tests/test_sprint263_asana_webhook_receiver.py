"""
tests/test_sprint263_asana_webhook_receiver.py

Sprint 2.63 — Asana Webhook Receiver: closes the L2 resolution loop
(full closure including customer reply and Freshdesk status=4 transition).

Scope:
  - asana/webhook.py: AsanaWebhookSecretStore, AsanaEventIdempotencyStore,
    verify_signature(), extract_completed_task_events()
  - asana/client.py: AsanaClient.create_webhook()
  - case_engine/engineering/service.py: get_ticket_by_external_id()
  - api/routes/webhooks/asana.py: POST /webhooks/asana/task-completed
    (full closure loop: idempotency → resolve → ClosureFieldGuard →
    send_customer_reply → update_ticket_fields(status=4))
  - freshdesk/response_service.py: get_ticket() + update_ticket_fields(status=)
  - app/main.py: asana_idempotency_store wiring

Sections:
  A — Signature verification
  B — Completed-task event extraction
  C — Secret store persistence
  D — AsanaClient.create_webhook()
  E — get_ticket_by_external_id()
  F — Webhook route: handshake + signature gate
  G — Resolution-loop background task
  H — Event idempotency store
  I — Closure step (ClosureFieldGuard, customer reply, status=4)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile

# ── Environment guardrails — BEFORE importing app code ───────────────────────
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("OPENAI_API_KEY", "test-263-key")
os.environ.setdefault("RAG_API_KEY", "test-263-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-263-key")

import httpx
import pytest

from asana.client import AsanaClient, AsanaConfig
from asana.webhook import (
    AsanaEventIdempotencyStore,
    AsanaWebhookSecretStore,
    extract_completed_task_events,
    verify_signature,
)
from case_engine.engineering.models import EngineeringStatus
from case_engine.engineering.service import EngineeringEscalationService
from freshdesk.closure_guard import ClosureFieldGuard
from freshdesk.safety_gate import ReplySafetyGate


# ---------------------------------------------------------------------------
# Section A — Signature verification
# ---------------------------------------------------------------------------

class TestA_SignatureVerification:

    def test_A1_valid_signature_accepted(self):
        secret = "test-secret-123"
        body = b'{"events":[]}'
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert verify_signature(secret, body, sig) is True

    def test_A2_wrong_signature_rejected(self):
        secret = "test-secret-123"
        body = b'{"events":[]}'
        assert verify_signature(secret, body, "0" * 64) is False

    def test_A3_missing_secret_rejected(self):
        body = b'{"events":[]}'
        sig = hmac.new(b"x", body, hashlib.sha256).hexdigest()
        assert verify_signature("", body, sig) is False

    def test_A4_missing_signature_header_rejected(self):
        assert verify_signature("secret", b"{}", None) is False

    def test_A5_tampered_body_rejected(self):
        secret = "test-secret-123"
        original_body = b'{"events":[{"a":1}]}'
        tampered_body = b'{"events":[{"a":2}]}'
        sig = hmac.new(secret.encode(), original_body, hashlib.sha256).hexdigest()
        assert verify_signature(secret, tampered_body, sig) is False


# ---------------------------------------------------------------------------
# Section B — Completed-task event extraction
# ---------------------------------------------------------------------------

class TestB_EventExtraction:

    def test_B1_extracts_single_completed_task(self):
        payload = {
            "events": [
                {
                    "action": "changed",
                    "resource": {"gid": "999", "resource_type": "task"},
                    "change": {"field": "completed", "new_value": True},
                }
            ]
        }
        events = extract_completed_task_events(payload)
        assert len(events) == 1
        assert events[0].task_gid == "999"

    def test_B2_ignores_non_completed_field_changes(self):
        payload = {
            "events": [
                {"resource": {"gid": "1", "resource_type": "task"}, "change": {"field": "name", "new_value": "x"}}
            ]
        }
        assert extract_completed_task_events(payload) == []

    def test_B3_ignores_completed_false(self):
        payload = {
            "events": [
                {
                    "resource": {"gid": "1", "resource_type": "task"},
                    "change": {"field": "completed", "new_value": False},
                }
            ]
        }
        assert extract_completed_task_events(payload) == []

    def test_B4_ignores_non_task_resources(self):
        payload = {
            "events": [
                {
                    "resource": {"gid": "1", "resource_type": "project"},
                    "change": {"field": "completed", "new_value": True},
                }
            ]
        }
        assert extract_completed_task_events(payload) == []

    def test_B5_multiple_events_extracts_all_matching(self):
        payload = {
            "events": [
                {"resource": {"gid": "1", "resource_type": "task"}, "change": {"field": "completed", "new_value": True}},
                {"resource": {"gid": "2", "resource_type": "task"}, "change": {"field": "name", "new_value": "y"}},
                {"resource": {"gid": "3", "resource_type": "task"}, "change": {"field": "completed", "new_value": True}},
            ]
        }
        events = extract_completed_task_events(payload)
        assert {e.task_gid for e in events} == {"1", "3"}

    def test_B6_malformed_payload_returns_empty_never_raises(self):
        assert extract_completed_task_events({}) == []
        assert extract_completed_task_events({"events": "not-a-list"}) == []
        assert extract_completed_task_events({"events": [{"resource": None}]}) == []


# ---------------------------------------------------------------------------
# Section C — Secret store persistence
# ---------------------------------------------------------------------------

class TestC_SecretStore:

    def test_C1_get_returns_none_when_unset(self):
        tmp = tempfile.mktemp(suffix=".json")
        store = AsanaWebhookSecretStore(path=tmp)
        assert store.get("proj-1") is None

    def test_C2_set_then_get_round_trips(self):
        tmp = tempfile.mktemp(suffix=".json")
        store = AsanaWebhookSecretStore(path=tmp)
        store.set("proj-1", "shhh-secret")
        assert store.get("proj-1") == "shhh-secret"
        os.remove(tmp)

    def test_C3_multiple_projects_independent(self):
        tmp = tempfile.mktemp(suffix=".json")
        store = AsanaWebhookSecretStore(path=tmp)
        store.set("proj-1", "secret-1")
        store.set("proj-2", "secret-2")
        assert store.get("proj-1") == "secret-1"
        assert store.get("proj-2") == "secret-2"
        os.remove(tmp)

    def test_C4_get_on_missing_file_does_not_raise(self):
        store = AsanaWebhookSecretStore(path="/nonexistent/path/that/does/not/exist.json")
        assert store.get("proj-1") is None


# ---------------------------------------------------------------------------
# Section D — AsanaClient.create_webhook()
# ---------------------------------------------------------------------------

class TestD_CreateWebhook:

    def _client(self, transport):
        config = AsanaConfig(
            api_key="test-token",
            project_gid="proj-123",
            workspace_gid="ws-456",
        )
        # Inject mock transport by monkeypatching httpx.Client construction
        # is not directly supported by AsanaClient (it builds its own Client
        # per call) — so we patch httpx.Client globally for this test via
        # a thin wrapper transport-aware Client subclass is overkill here;
        # instead we verify against a real AsanaClient using monkeypatched
        # httpx.Client(transport=...) at the httpx module level is avoided —
        # AsanaClient.create_webhook always builds `httpx.Client(timeout=...)`
        # without a transport hook (matching create_task/get_task/health,
        # which are called from production without one). We test payload
        # construction and response parsing via monkeypatching httpx.Client.
        return AsanaClient(config)

    def test_D1_create_webhook_posts_expected_payload(self, monkeypatch):
        captured = {}

        def fake_post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return httpx.Response(
                201,
                json={"data": {"gid": "wh-1", "resource": {"gid": "proj-123"}, "target": "https://x.test/hook", "active": True}},
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        client = self._client(None)
        result = client.create_webhook("https://x.test/hook")

        assert captured["url"].endswith("/webhooks")
        assert captured["json"]["data"]["resource"] == "proj-123"
        assert captured["json"]["data"]["target"] == "https://x.test/hook"
        assert captured["headers"]["Authorization"] == "Bearer test-token"
        assert result["gid"] == "wh-1"

    def test_D2_create_webhook_explicit_resource_overrides_config(self, monkeypatch):
        captured = {}

        def fake_post(self, url, json=None, headers=None):
            captured["json"] = json
            return httpx.Response(201, json={"data": {"gid": "wh-2"}}, request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        client = self._client(None)
        client.create_webhook("https://x.test/hook", resource_gid="other-project")
        assert captured["json"]["data"]["resource"] == "other-project"

    def test_D3_create_webhook_raises_on_http_error(self, monkeypatch):
        def fake_post(self, url, json=None, headers=None):
            return httpx.Response(400, json={"errors": [{"message": "bad request"}]}, request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        client = self._client(None)
        with pytest.raises(httpx.HTTPStatusError):
            client.create_webhook("https://x.test/hook")


# ---------------------------------------------------------------------------
# Section E — get_ticket_by_external_id()
# ---------------------------------------------------------------------------

class TestE_TicketLookupByExternalId:

    def _service_with_ticket(self, external_id: str) -> EngineeringEscalationService:
        from dataclasses import replace

        svc = EngineeringEscalationService()
        result = svc.create_ticket(case=None, topic="OTP_DELIVERY_FAILURE", freshdesk_ticket_id="fd-1")
        ticket_id = result.ticket.ticket_id
        # Simulate a live Asana task having been created (create_ticket without
        # an injected asana_client leaves external_id=None) — EngineeringTicket
        # is a frozen dataclass, so rebuild it with external_id set, mirroring
        # what _create_ticket_internal sets when self._asana is not None.
        stored = svc._store[ticket_id]
        svc._store[ticket_id] = replace(stored, external_id=external_id)
        return svc

    def test_E1_finds_ticket_by_matching_external_id(self):
        svc = self._service_with_ticket("asana-task-999")
        found = svc.get_ticket_by_external_id("asana-task-999")
        assert found is not None
        assert found.external_id == "asana-task-999"

    def test_E2_returns_none_when_no_match(self):
        svc = self._service_with_ticket("asana-task-999")
        assert svc.get_ticket_by_external_id("nonexistent") is None

    def test_E3_returns_none_on_empty_store(self):
        svc = EngineeringEscalationService()
        assert svc.get_ticket_by_external_id("anything") is None


# ---------------------------------------------------------------------------
# Section F — Webhook route: handshake + signature gate
# ---------------------------------------------------------------------------

class TestF_WebhookRoute:

    def _make_app(self):
        from fastapi import FastAPI
        from api.routes.webhooks.asana import router

        app = FastAPI()
        app.include_router(router)
        tmp = tempfile.mktemp(suffix=".json")
        app.state.asana_webhook_secret_store = AsanaWebhookSecretStore(path=tmp)
        app.state.asana_project_gid = "proj-123"
        app.state.engineering_escalation_service = None
        app.state.freshdesk_response_service = None
        return app

    def test_F1_handshake_echoes_secret_header(self):
        from fastapi.testclient import TestClient
        app = self._make_app()
        client = TestClient(app)
        r = client.post("/webhooks/asana/task-completed", headers={"X-Hook-Secret": "sekrit"})
        assert r.status_code == 200
        assert r.headers.get("x-hook-secret") == "sekrit"

    def test_F2_handshake_persists_secret_for_future_verification(self):
        from fastapi.testclient import TestClient
        app = self._make_app()
        client = TestClient(app)
        client.post("/webhooks/asana/task-completed", headers={"X-Hook-Secret": "persisted-secret"})
        assert app.state.asana_webhook_secret_store.get("proj-123") == "persisted-secret"

    def test_F3_valid_signature_event_accepted(self):
        from fastapi.testclient import TestClient
        app = self._make_app()
        app.state.asana_webhook_secret_store.set("proj-123", "sekrit")
        client = TestClient(app)
        body = json.dumps({"events": [
            {"resource": {"gid": "999", "resource_type": "task"}, "change": {"field": "completed", "new_value": True}}
        ]}).encode()
        sig = hmac.new(b"sekrit", body, hashlib.sha256).hexdigest()
        r = client.post(
            "/webhooks/asana/task-completed",
            content=body,
            headers={"X-Hook-Signature": sig, "Content-Type": "application/json"},
        )
        assert r.status_code == 200
        assert r.json()["acted_on"] == 1

    def test_F4_invalid_signature_rejected_401(self):
        from fastapi.testclient import TestClient
        app = self._make_app()
        app.state.asana_webhook_secret_store.set("proj-123", "sekrit")
        client = TestClient(app)
        body = json.dumps({"events": []}).encode()
        r = client.post(
            "/webhooks/asana/task-completed",
            content=body,
            headers={"X-Hook-Signature": "wrong-signature", "Content-Type": "application/json"},
        )
        assert r.status_code == 401

    def test_F5_event_with_no_completed_changes_acts_on_zero(self):
        from fastapi.testclient import TestClient
        app = self._make_app()
        app.state.asana_webhook_secret_store.set("proj-123", "sekrit")
        client = TestClient(app)
        body = json.dumps({"events": [
            {"resource": {"gid": "1", "resource_type": "task"}, "change": {"field": "name", "new_value": "x"}}
        ]}).encode()
        sig = hmac.new(b"sekrit", body, hashlib.sha256).hexdigest()
        r = client.post(
            "/webhooks/asana/task-completed",
            content=body,
            headers={"X-Hook-Signature": sig, "Content-Type": "application/json"},
        )
        assert r.status_code == 200
        assert r.json()["acted_on"] == 0

    def test_F6_unconfigured_project_ignores_gracefully(self):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from api.routes.webhooks.asana import router

        app = FastAPI()
        app.include_router(router)
        app.state.asana_webhook_secret_store = None
        app.state.asana_project_gid = ""
        client = TestClient(app)
        body = json.dumps({"events": []}).encode()
        r = client.post(
            "/webhooks/asana/task-completed",
            content=body,
            headers={"X-Hook-Signature": "anything", "Content-Type": "application/json"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ignored"


# ---------------------------------------------------------------------------
# Section G — Resolution-loop background task
# ---------------------------------------------------------------------------

def _make_fake_resp_svc_with_cf_clients(reply_log, fields_log):
    """Helper: FakeRespSvc with cf_clients set so ClosureFieldGuard passes."""
    class FakeRespSvc:
        async def get_ticket(self, ticket_id, **kw):
            return {"custom_fields": {"cf_clients": "Unity Bank"}}

        async def send_customer_reply(self, ticket_id, body, **kw):
            reply_log.append((ticket_id, body))
            return {"id": "reply-1"}

        async def update_ticket_fields(self, ticket_id, custom_fields, *, status=None, ticket_type=None, **kw):
            fields_log.append({"ticket_id": ticket_id, "status": status, "ticket_type": ticket_type, "custom_fields": custom_fields})
            return {}

        async def add_internal_note(self, ticket_id, note, **kw):
            return {}

    return FakeRespSvc()


class TestG_ResolutionLoop:

    @pytest.mark.asyncio
    async def test_G1_full_resolution_loop_resolves_and_closes_ticket(self):
        """Full happy path: resolve engineering ticket + notify customer + status=4."""
        from api.routes.webhooks.asana import _handle_task_completed
        from dataclasses import replace

        svc = EngineeringEscalationService()
        result = svc.create_ticket(case=None, topic="OTP_DELIVERY_FAILURE", freshdesk_ticket_id="fd-42")
        ticket_id = result.ticket.ticket_id
        svc._store[ticket_id] = replace(svc._store[ticket_id], external_id="999", asana_project_id="proj-123")

        reply_log, fields_log = [], []
        resp_svc = _make_fake_resp_svc_with_cf_clients(reply_log, fields_log)

        # Sprint 2.63.1: a wired, non-kill-switched gate is required for the
        # resolution reply to actually send — see TestJ for gate-specific coverage.
        await _handle_task_completed("999", svc, resp_svc, None, ReplySafetyGate())

        updated = svc.get_ticket(ticket_id)
        assert updated.status == EngineeringStatus.RESOLVED

        assert len(reply_log) == 1
        assert reply_log[0][0] == "fd-42"
        assert "resolved" in reply_log[0][1].lower()

        assert len(fields_log) == 1
        assert fields_log[0]["status"] == 4
        assert fields_log[0]["ticket_type"] == "Issues"
        assert fields_log[0]["custom_fields"]["cf_sop_status"] == "No SOP Available"
        assert fields_log[0]["custom_fields"]["cf_resolution_classification"] == "Permanent Fix Applied by Dev"

    @pytest.mark.asyncio
    async def test_G2_no_matching_ticket_does_nothing(self):
        from api.routes.webhooks.asana import _handle_task_completed

        svc = EngineeringEscalationService()
        calls = []

        class FakeRespSvc:
            async def add_internal_note(self, *a, **kw):
                calls.append(1)
            async def get_ticket(self, *a, **kw):
                calls.append(1)

        await _handle_task_completed("nonexistent-gid", svc, FakeRespSvc(), None)
        assert calls == []

    @pytest.mark.asyncio
    async def test_G3_none_engineering_service_does_not_raise(self):
        from api.routes.webhooks.asana import _handle_task_completed
        await _handle_task_completed("999", None, None, None)

    @pytest.mark.asyncio
    async def test_G4_guard_blocked_posts_internal_note_not_customer_reply(self):
        """When ClosureFieldGuard blocks (cf_clients missing), posts internal note only."""
        from api.routes.webhooks.asana import _handle_task_completed
        from dataclasses import replace

        svc = EngineeringEscalationService()
        result = svc.create_ticket(case=None, topic="X", freshdesk_ticket_id="fd-1")
        ticket_id = result.ticket.ticket_id
        svc._store[ticket_id] = replace(svc._store[ticket_id], external_id="999")

        notes, replies, updates = [], [], []

        class FakeRespSvcMissingCfClients:
            async def get_ticket(self, ticket_id, **kw):
                return {"custom_fields": {}}  # cf_clients absent → guard blocks

            async def add_internal_note(self, ticket_id, note, **kw):
                notes.append((ticket_id, note))
                return {}

            async def send_customer_reply(self, *a, **kw):
                replies.append(1)

            async def update_ticket_fields(self, *a, **kw):
                updates.append(1)

        await _handle_task_completed("999", svc, FakeRespSvcMissingCfClients(), None)

        assert len(notes) == 1
        assert "blocked" in notes[0][1].lower()
        assert replies == []
        assert updates == []


# ---------------------------------------------------------------------------
# Section H — Event idempotency store
# ---------------------------------------------------------------------------

class TestH_IdempotencyStore:

    def test_H1_has_processed_returns_false_initially(self):
        store = AsanaEventIdempotencyStore()
        assert store.has_processed("task-1") is False

    def test_H2_mark_then_has_returns_true(self):
        store = AsanaEventIdempotencyStore()
        store.mark_processed("task-1")
        assert store.has_processed("task-1") is True

    def test_H3_different_gids_are_independent(self):
        store = AsanaEventIdempotencyStore()
        store.mark_processed("task-1")
        assert store.has_processed("task-2") is False

    @pytest.mark.asyncio
    async def test_H4_duplicate_event_skips_freshdesk_writes(self):
        """Second call for same task_gid must not call any response_service method."""
        from api.routes.webhooks.asana import _handle_task_completed
        from dataclasses import replace

        svc = EngineeringEscalationService()
        result = svc.create_ticket(case=None, topic="X", freshdesk_ticket_id="fd-99")
        ticket_id = result.ticket.ticket_id
        svc._store[ticket_id] = replace(svc._store[ticket_id], external_id="dup-task")

        store = AsanaEventIdempotencyStore()
        calls = []

        class FakeRespSvc:
            async def get_ticket(self, *a, **kw):
                calls.append("get_ticket")
                return {"custom_fields": {"cf_clients": "Unity Bank"}}
            async def send_customer_reply(self, *a, **kw):
                calls.append("send_customer_reply")
                return {}
            async def update_ticket_fields(self, *a, **kw):
                calls.append("update_ticket_fields")
                return {}
            async def add_internal_note(self, *a, **kw):
                calls.append("add_internal_note")
                return {}

        resp_svc = FakeRespSvc()
        await _handle_task_completed("dup-task", svc, resp_svc, store)
        first_call_count = len(calls)
        assert first_call_count > 0  # First call executed normally

        calls.clear()
        await _handle_task_completed("dup-task", svc, resp_svc, store)
        assert calls == []  # Second call skipped entirely


# ---------------------------------------------------------------------------
# Section I — Closure step: ClosureFieldGuard + combined PUT
# ---------------------------------------------------------------------------

class TestI_ClosureStep:

    def test_I1_closure_guard_approves_our_payload_when_cf_clients_present(self):
        """Guard must accept our confirmed field mapping when cf_clients is set."""
        guard = ClosureFieldGuard()
        payload = {
            "custom_fields": {
                "cf_sop_status": "No SOP Available",
                "cf_resolution_classification": "Permanent Fix Applied by Dev",
            },
            "status": 4,
            "type": "Issues",
        }
        current_ticket = {"custom_fields": {"cf_clients": "Unity Bank"}}
        decision = guard.guard_status_transition(payload, current_ticket)
        assert decision.allowed is True

    def test_I2_closure_guard_blocks_when_cf_clients_absent(self):
        """Guard blocks when cf_clients not on current ticket (not yet assigned)."""
        guard = ClosureFieldGuard()
        payload = {
            "custom_fields": {
                "cf_sop_status": "No SOP Available",
                "cf_resolution_classification": "Permanent Fix Applied by Dev",
            },
            "status": 4,
            "type": "Issues",
        }
        decision = guard.guard_status_transition(payload, {})
        assert decision.allowed is False
        assert "cf_clients" in decision.missing_fields

    def test_I3_closure_guard_blocks_invalid_ticket_type(self):
        """Guard blocks unknown ticket_type values even when other fields are set."""
        guard = ClosureFieldGuard()
        payload = {
            "custom_fields": {
                "cf_sop_status": "No SOP Available",
                "cf_resolution_classification": "Permanent Fix Applied by Dev",
            },
            "status": 4,
            "type": "NotAValidType",
        }
        current_ticket = {"custom_fields": {"cf_clients": "Unity Bank"}}
        decision = guard.guard_status_transition(payload, current_ticket)
        assert decision.allowed is False
        assert "NotAValidType" in (decision.invalid_type_value or "")

    def test_I4_closure_guard_blocks_forbidden_cf_clients_write(self):
        """Guard blocks payloads that try to overwrite the AI-read-only cf_clients field."""
        guard = ClosureFieldGuard()
        payload = {
            "custom_fields": {
                "cf_clients": "Attacker",
                "cf_sop_status": "No SOP Available",
                "cf_resolution_classification": "Permanent Fix Applied by Dev",
            },
            "status": 4,
            "type": "Issues",
        }
        decision = guard.guard_status_transition(payload, {"custom_fields": {"cf_clients": "Unity Bank"}})
        assert decision.allowed is False
        assert "cf_clients" in decision.forbidden_writes

    @pytest.mark.asyncio
    async def test_I5_update_ticket_fields_sends_status_in_payload(self):
        """FreshdeskResponseService.update_ticket_fields passes status through to client."""
        from unittest.mock import AsyncMock, MagicMock
        from freshdesk.response_service import FreshdeskResponseService

        client = MagicMock()
        client.update_ticket = AsyncMock(return_value={"id": "42"})

        svc = FreshdeskResponseService(client)
        await svc.update_ticket_fields(42, {"cf_sop_status": "No SOP Available"}, status=4, ticket_type="Issues")

        client.update_ticket.assert_called_once()
        _, call_payload = client.update_ticket.call_args[0]
        assert call_payload["status"] == 4
        assert call_payload["type"] == "Issues"
        assert call_payload["custom_fields"]["cf_sop_status"] == "No SOP Available"

    @pytest.mark.asyncio
    async def test_I6_get_ticket_calls_client_get_ticket(self):
        """FreshdeskResponseService.get_ticket delegates to client.get_ticket."""
        from unittest.mock import AsyncMock, MagicMock
        from freshdesk.response_service import FreshdeskResponseService

        client = MagicMock()
        client.get_ticket = AsyncMock(return_value={"id": "99", "custom_fields": {"cf_clients": "Unity Bank"}})

        svc = FreshdeskResponseService(client)
        result = await svc.get_ticket(99)

        client.get_ticket.assert_called_once_with(99)
        assert result["custom_fields"]["cf_clients"] == "Unity Bank"


# ---------------------------------------------------------------------------
# Section J — ReplySafetyGate wiring on the Asana resolution-closure reply
# (Sprint 2.63.1: gate was fully implemented in Sprint 2.48 but never actually
# invoked by any send_customer_reply() call site — see also
# tests/test_sprint2631_reply_safety_gate_wiring.py for the freshdesk.py sites.)
# ---------------------------------------------------------------------------

def _make_fake_resp_svc_tracking_notes(reply_log, fields_log, notes_log):
    class FakeRespSvc:
        async def get_ticket(self, ticket_id, **kw):
            return {"custom_fields": {"cf_clients": "Unity Bank"}}

        async def send_customer_reply(self, ticket_id, body, **kw):
            reply_log.append((ticket_id, body))
            return {"id": "reply-1"}

        async def update_ticket_fields(self, ticket_id, custom_fields, *, status=None, ticket_type=None, **kw):
            fields_log.append({"ticket_id": ticket_id, "status": status, "ticket_type": ticket_type, "custom_fields": custom_fields})
            return {}

        async def add_internal_note(self, ticket_id, note, **kw):
            notes_log.append((ticket_id, note))
            return {}

    return FakeRespSvc()


class TestJ_ReplySafetyGateOnResolutionReply:

    @pytest.mark.asyncio
    async def test_J1_no_gate_wired_fails_closed_does_not_send_or_close(self):
        """
        Regression guard for the exact bug this sprint fixes: if
        reply_safety_gate is None (not wired), the resolution reply must NOT
        be auto-sent and the ticket must NOT be closed — draft a note instead.
        """
        from api.routes.webhooks.asana import _handle_task_completed
        from dataclasses import replace

        svc = EngineeringEscalationService()
        result = svc.create_ticket(case=None, topic="X", freshdesk_ticket_id="fd-j1")
        ticket_id = result.ticket.ticket_id
        svc._store[ticket_id] = replace(svc._store[ticket_id], external_id="j1-task")

        reply_log, fields_log, notes_log = [], [], []
        resp_svc = _make_fake_resp_svc_tracking_notes(reply_log, fields_log, notes_log)

        await _handle_task_completed("j1-task", svc, resp_svc, None, None)

        assert reply_log == []
        assert fields_log == []
        assert len(notes_log) == 1
        assert "not wired" in notes_log[0][1].lower()

    @pytest.mark.asyncio
    async def test_J2_kill_switch_blocks_send_and_close(self):
        """Kill switch engaged → no customer reply, no ticket closure, draft posted."""
        from api.routes.webhooks.asana import _handle_task_completed
        from dataclasses import replace

        svc = EngineeringEscalationService()
        result = svc.create_ticket(case=None, topic="X", freshdesk_ticket_id="fd-j2")
        ticket_id = result.ticket.ticket_id
        svc._store[ticket_id] = replace(svc._store[ticket_id], external_id="j2-task")

        reply_log, fields_log, notes_log = [], [], []
        resp_svc = _make_fake_resp_svc_tracking_notes(reply_log, fields_log, notes_log)
        gate = ReplySafetyGate(kill_switch=True)

        await _handle_task_completed("j2-task", svc, resp_svc, None, gate)

        assert reply_log == []
        assert fields_log == []
        assert len(notes_log) == 1
        assert "kill switch" in notes_log[0][1].lower()

    @pytest.mark.asyncio
    async def test_J3_duplicate_reply_hash_blocks_second_ticket_close(self):
        """
        Same gate instance, two DIFFERENT engineering tickets that happen to
        produce an identical rendered reply body for the same Freshdesk ticket
        id — the gate's duplicate-hash protection is ticket_id+body scoped and
        must block the second send even though idempotency_store (task_gid
        scoped) would not catch it.
        """
        from api.routes.webhooks.asana import _handle_task_completed
        from dataclasses import replace

        svc = EngineeringEscalationService()
        r1 = svc.create_ticket(case=None, topic="X", freshdesk_ticket_id="fd-j3")
        svc._store[r1.ticket.ticket_id] = replace(svc._store[r1.ticket.ticket_id], external_id="j3-task-a")

        reply_log, fields_log, notes_log = [], [], []
        resp_svc = _make_fake_resp_svc_tracking_notes(reply_log, fields_log, notes_log)
        gate = ReplySafetyGate()

        await _handle_task_completed("j3-task-a", svc, resp_svc, None, gate)
        assert len(reply_log) == 1

        # Second, distinct engineering ticket resolving to the SAME Freshdesk
        # ticket id renders the identical fixed template body — the gate must
        # block the duplicate autonomous send.
        r2 = svc.create_ticket(case=None, topic="X", freshdesk_ticket_id="fd-j3")
        svc._store[r2.ticket.ticket_id] = replace(svc._store[r2.ticket.ticket_id], external_id="j3-task-b")

        await _handle_task_completed("j3-task-b", svc, resp_svc, None, gate)
        assert len(reply_log) == 1  # still 1 — second send was blocked
        assert len(fields_log) == 1  # ticket was not closed a second time
        # BLOCK_DUPLICATE has should_draft=False by design (safety_gate.py) — the
        # first send already produced a customer-visible reply, no redraft needed.
        assert notes_log == []

    @pytest.mark.asyncio
    async def test_J4_allowed_path_sends_and_closes_exactly_as_before(self):
        """A wired, non-kill-switched gate on a fresh ticket allows the full flow through."""
        from api.routes.webhooks.asana import _handle_task_completed
        from dataclasses import replace

        svc = EngineeringEscalationService()
        result = svc.create_ticket(case=None, topic="X", freshdesk_ticket_id="fd-j4")
        ticket_id = result.ticket.ticket_id
        svc._store[ticket_id] = replace(svc._store[ticket_id], external_id="j4-task")

        reply_log, fields_log, notes_log = [], [], []
        resp_svc = _make_fake_resp_svc_tracking_notes(reply_log, fields_log, notes_log)

        await _handle_task_completed("j4-task", svc, resp_svc, None, ReplySafetyGate())

        assert len(reply_log) == 1
        assert len(fields_log) == 1
        assert notes_log == []
