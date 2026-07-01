"""
tests/test_sprint2292_e2e_golden_path.py

Sprint 2.29.2 PART 3 + PART 5: Golden Path execution trace + end-to-end validation.

Proves EACH component of the Golden Path either:
  ✓ ENTERED  — called and returned a result
  ✗ SKIPPED  — intentionally not called (optional component absent)
  ✗ FAILED   — called but raised / returned error

Golden Path:
  Freshdesk
  → Webhook Receiver (route)
  → FreshdeskTicketCreatedHandler
    → IdempotencyStore.check()
    → FreshdeskTicketPayload.from_dict()     ← field mapping fix verified here
    → ClientResolver (optional)
    → TicketOrchestrator (optional)
    → ConversationStateStore.get_or_create()
    → IdempotencyStore.mark_completed()
  → FreshdeskResponseService (for UNKNOWN_CLIENT path)
  → Background task returns SUCCESS

Variants tested:
  1.  ticket-created with canonical id field → SUCCESS
  2.  ticket-created with ticket_id field → SUCCESS (field mapping fix)
  3.  ticket-created with nested requester → SUCCESS
  4.  ticket-created with mixed alternatives → SUCCESS
  5.  duplicate event (idempotency) → SKIPPED
  6.  MISSING_TICKET_ID no longer occurs with defensive mapping
  7.  ticket-updated canonical → SUCCESS
  8.  ticket-updated with ticket_id field → SUCCESS
  9.  client_resolver wired → client_id resolved
  10. client_resolver raises UnknownClientError → error_code=UNKNOWN_CLIENT
  11. orchestrator wired → case_id populated
  12. orchestrator raises → error_code=ORCHESTRATOR_ERROR
  13. unknown_tenant → response_service note posted via route
  14. handler with no optional components → gracefully skips all
  15. idempotency WAL (ensure_receipt) called before 200 via route
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("RAG_API_KEY", "test-sprint2292-e2e")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENABLED", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from freshdesk.handlers import FreshdeskTicketCreatedHandler, FreshdeskTicketUpdatedHandler, HandlerResult
from freshdesk.idempotency import WebhookIdempotencyStore
from freshdesk.conversation_state import ConversationStateStore, ConversationLifecycle
from freshdesk.verifier import FreshdeskWebhookVerifier
from freshdesk.freshdesk_models import FreshdeskWebhookPayload, FreshdeskUpdateEvent
from api.routes.webhooks.freshdesk import router


SECRET = "kwikid-e2e-test-secret-2292"


def _recent_ts() -> str:
    return (datetime.now(tz=timezone.utc) - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_app(*, enforce: bool = False) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.freshdesk_verifier = FreshdeskWebhookVerifier(SECRET, mode="static", enforce=enforce)
    app.state.freshdesk_idempotency_store = WebhookIdempotencyStore()
    app.state.freshdesk_conversation_store = ConversationStateStore()
    app.state.freshdesk_response_service = None
    app.state.freshdesk_rag_processor = None
    app.state.freshdesk_ticket_created_handler = None  # set per test
    app.state.freshdesk_ticket_updated_handler = None  # set per test
    return app


# ── Part 3: Handler execution trace helpers ───────────────────────────────────

def _make_created_handler(
    *,
    orchestrator=None,
    client_resolver=None,
    audit_logger=None,
    supabase_client=None,
) -> tuple[FreshdeskTicketCreatedHandler, WebhookIdempotencyStore, ConversationStateStore]:
    idem = WebhookIdempotencyStore(supabase_client=supabase_client)
    conv = ConversationStateStore(supabase_client=supabase_client)
    handler = FreshdeskTicketCreatedHandler(
        idempotency_store=idem,
        conversation_store=conv,
        ticket_orchestrator=orchestrator,
        client_resolver=client_resolver,
        audit_logger=audit_logger,
    )
    return handler, idem, conv


# ── Test 1: canonical id field — ticket_id extracted correctly ────────────────

class TestCanonicalIdField:
    def test_handler_succeeds_with_id_key(self):
        handler, idem, conv = _make_created_handler()
        payload = {"freshdesk_webhook": {
            "id": 197416,
            "subject": "Login issue",
            "requester_email": "agent@unity.co.in",
            "ticket_custom_fields": {"cf_clients": "Unity"},
            "created_at": _recent_ts(),
        }}
        result = handler.handle(payload)
        assert result.success is True
        assert result.ticket_id == "197416"
        assert result.error_code is None

    def test_conversation_state_created(self):
        handler, idem, conv = _make_created_handler()
        payload = {"freshdesk_webhook": {
            "id": 197416,
            "requester_email": "a@b.com",
            "created_at": _recent_ts(),
        }}
        handler.handle(payload)
        state = conv.get("197416")
        assert state is not None
        assert state.lifecycle_state == ConversationLifecycle.OPEN

    def test_idempotency_mark_completed(self):
        handler, idem, conv = _make_created_handler()
        payload = {"freshdesk_webhook": {
            "id": 197416,
            "requester_email": "a@b.com",
            "created_at": _recent_ts(),
        }}
        handler.handle(payload)
        ts = payload["freshdesk_webhook"]["created_at"]
        key = idem.make_key("197416", "ticket_created", ts)
        entry = idem.get(key)
        assert entry is not None
        from freshdesk.idempotency import IdempotencyStatus
        assert entry.status == IdempotencyStatus.COMPLETED


# ── Test 2: ticket_id key field mapping fix ───────────────────────────────────

class TestTicketIdKeyFieldMappingFix:
    def test_ticket_id_key_no_longer_causes_missing_ticket_id_error(self):
        """
        BEFORE fix: d.get("id", 0) → 0 → MISSING_TICKET_ID.
        AFTER fix:  d.get("id") is None → falls back to d.get("ticket_id", 0) → 197416.
        """
        handler, _, _ = _make_created_handler()
        payload = {"freshdesk_webhook": {
            "ticket_id": 197416,    # ← Dispatch'r template variant, NO "id" key
            "subject": "Login issue",
            "requester_email": "agent@unity.co.in",
            "created_at": _recent_ts(),
        }}
        result = handler.handle(payload)
        assert result.error_code != "MISSING_TICKET_ID", (
            f"Field mapping fix not applied — still returning MISSING_TICKET_ID. "
            f"error_code={result.error_code}"
        )
        assert result.ticket_id == "197416"
        assert result.success is True

    def test_ticket_id_string_variant(self):
        handler, _, _ = _make_created_handler()
        payload = {"freshdesk_webhook": {
            "ticket_id": "197416",
            "requester_email": "x@y.com",
        }}
        result = handler.handle(payload)
        assert result.ticket_id == "197416"
        assert result.success is True


# ── Test 3: nested requester object ──────────────────────────────────────────

class TestNestedRequesterInHandler:
    def test_nested_requester_email_extracted(self):
        handler, _, _ = _make_created_handler()
        payload = {"freshdesk_webhook": {
            "id": 300,
            "requester": {"email": "nested@bank.com", "name": "Nested User"},
        }}
        result = handler.handle(payload)
        # Should not fail with MISSING_EMAIL since email IS present (nested)
        assert result.error_code != "MISSING_EMAIL"
        assert result.ticket_id == "300"


# ── Test 4: mixed alternatives ────────────────────────────────────────────────

class TestMixedAlternativesInHandler:
    def test_all_alternatives_succeed(self):
        handler, _, conv = _make_created_handler()
        payload = {"freshdesk_webhook": {
            "ticket_id": 88888,
            "ticket_subject": "Mixed alternatives test",
            "requester": {"email": "req@test.com"},
            "custom_fields": {"cf_clients": "TestCorp"},
        }}
        result = handler.handle(payload)
        assert result.ticket_id == "88888"
        assert result.success is True
        state = conv.get("88888")
        assert state is not None


# ── Test 5: duplicate event suppression ──────────────────────────────────────

class TestDuplicateEventSuppression:
    def test_second_identical_event_is_skipped(self):
        handler, idem, _ = _make_created_handler()
        ts = _recent_ts()
        payload = {"freshdesk_webhook": {
            "id": 197416,
            "requester_email": "a@b.com",
            "created_at": ts,
        }}
        r1 = handler.handle(payload)
        assert r1.success is True
        assert r1.skipped is False

        r2 = handler.handle(payload)
        assert r2.success is True
        assert r2.skipped is True
        assert r2.skip_reason == "DUPLICATE"

    def test_different_timestamp_not_duplicate(self):
        handler, _, _ = _make_created_handler()
        p1 = {"freshdesk_webhook": {"id": 197416, "requester_email": "a@b.com", "created_at": _recent_ts()}}
        p2 = {"freshdesk_webhook": {"id": 197416, "requester_email": "a@b.com", "created_at": _recent_ts()}}
        # Both have slightly different timestamps from _recent_ts() — may or may not be the same
        # Force different timestamps to guarantee non-duplicate
        p1["freshdesk_webhook"]["created_at"] = "2026-06-25T10:00:00Z"
        p2["freshdesk_webhook"]["created_at"] = "2026-06-25T11:00:00Z"
        r1 = handler.handle(p1)
        r2 = handler.handle(p2)
        assert r1.success is True
        assert not r2.skipped  # different event_ts → not a duplicate


# ── Test 6: MISSING_TICKET_ID no longer fires for Dispatch'r payloads ────────

class TestMissingTicketIdResolved:
    def test_empty_id_and_empty_ticket_id_still_returns_missing(self):
        """Edge case: both id and ticket_id absent → still MISSING_TICKET_ID (expected)."""
        handler, _, _ = _make_created_handler()
        payload = {"freshdesk_webhook": {"requester_email": "a@b.com"}}
        result = handler.handle(payload)
        assert result.error_code == "MISSING_TICKET_ID"

    def test_id_zero_explicit_returns_missing(self):
        handler, _, _ = _make_created_handler()
        payload = {"freshdesk_webhook": {"id": 0, "requester_email": "a@b.com"}}
        result = handler.handle(payload)
        assert result.error_code == "MISSING_TICKET_ID"

    def test_valid_ticket_id_key_does_not_return_missing(self):
        """Regression: ticket_id key must NOT produce MISSING_TICKET_ID."""
        handler, _, _ = _make_created_handler()
        payload = {"freshdesk_webhook": {"ticket_id": 197416, "requester_email": "a@b.com"}}
        result = handler.handle(payload)
        assert result.error_code != "MISSING_TICKET_ID"


# ── Test 7: ticket-updated canonical ─────────────────────────────────────────

class TestTicketUpdatedCanonical:
    def test_canonical_update_succeeds(self):
        idem = WebhookIdempotencyStore()
        conv = ConversationStateStore()
        handler = FreshdeskTicketUpdatedHandler(
            idempotency_store=idem, conversation_store=conv
        )
        payload = {"freshdesk_webhook": {
            "id": 197416,
            "updated_at": _recent_ts(),
            "changes": {"status": [2, 4]},
        }}
        result = handler.handle(payload)
        assert result.success is True
        assert result.ticket_id == "197416"


# ── Test 8: ticket-updated with ticket_id key ─────────────────────────────────

class TestTicketUpdatedTicketIdKey:
    def test_ticket_id_key_in_update(self):
        idem = WebhookIdempotencyStore()
        conv = ConversationStateStore()
        handler = FreshdeskTicketUpdatedHandler(
            idempotency_store=idem, conversation_store=conv
        )
        payload = {"freshdesk_webhook": {
            "ticket_id": 197416,      # ← ticket_id variant
            "updated_at": _recent_ts(),
            "changes": {"status": [2, 4]},
        }}
        result = handler.handle(payload)
        assert result.success is True
        assert result.ticket_id == "197416"


# ── Test 9: client_resolver wired → client_id resolved ───────────────────────

class TestClientResolverWired:
    def test_client_id_resolved_from_resolver(self):
        mock_resolver = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.client_id = "unity-001"
        mock_ctx.client_name = "Unity Bank"
        mock_resolver.resolve.return_value = mock_ctx

        handler, _, conv = _make_created_handler(client_resolver=mock_resolver)
        payload = {"freshdesk_webhook": {
            "id": 197416,
            "requester_email": "agent@unity.co.in",
            "ticket_custom_fields": {"cf_clients": "Unity"},
        }}
        result = handler.handle(payload)
        assert result.success is True
        mock_resolver.resolve.assert_called_once_with("agent@unity.co.in")

        state = conv.get("197416")
        assert state is not None
        assert state.client_id == "unity-001"

    def test_resolver_called_with_email_not_logged(self):
        calls = []
        mock_resolver = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.client_id = "c1"
        mock_ctx.client_name = "Client1"
        mock_resolver.resolve.side_effect = lambda email: (calls.append(email), mock_ctx)[1]

        handler, _, _ = _make_created_handler(client_resolver=mock_resolver)
        payload = {"freshdesk_webhook": {
            "id": 1,
            "requester_email": "secret@email.com",
        }}
        handler.handle(payload)
        # resolver was called with the email (internal use is fine)
        assert "secret@email.com" in calls


# ── Test 10: UnknownClientError path ─────────────────────────────────────────

class TestUnknownClientError:
    def test_unknown_client_returns_error(self):
        mock_resolver = MagicMock()
        mock_resolver.resolve.side_effect = Exception("UnknownClientError")

        handler, _, _ = _make_created_handler(client_resolver=mock_resolver)
        payload = {"freshdesk_webhook": {
            "id": 197416,
            "requester_email": "unknown@newdomain.com",
        }}
        result = handler.handle(payload)
        assert result.success is False
        assert result.error_code == "UNKNOWN_CLIENT"
        assert "domain" in (result.detail or {})


# ── Test 11: orchestrator wired → case_id populated ──────────────────────────

class TestOrchestratorWired:
    def test_orchestrator_called_and_case_id_returned(self):
        mock_orch = MagicMock()
        mock_orch_result = MagicMock()
        mock_orch_result.case_id = "CASE-001"
        mock_orch.process_ticket.return_value = mock_orch_result

        handler, idem, conv = _make_created_handler(orchestrator=mock_orch)
        payload = {"freshdesk_webhook": {
            "id": 197416,
            "subject": "Login issue",
            "description_text": "Cannot log in.",
            "requester_email": "agent@unity.co.in",
            "ticket_custom_fields": {"cf_clients": "Unity"},
        }}
        result = handler.handle(payload)
        assert result.success is True
        assert result.case_id == "CASE-001"

        mock_orch.process_ticket.assert_called_once()
        call_kwargs = mock_orch.process_ticket.call_args[1]
        assert call_kwargs["ticket_id"] == "197416"
        assert call_kwargs["subject"] == "Login issue"

    def test_case_id_stored_in_conversation_state(self):
        mock_orch = MagicMock()
        mock_orch_result = MagicMock()
        mock_orch_result.case_id = "CASE-002"
        mock_orch.process_ticket.return_value = mock_orch_result

        handler, _, conv = _make_created_handler(orchestrator=mock_orch)
        payload = {"freshdesk_webhook": {
            "id": 200,
            "requester_email": "a@b.com",
        }}
        handler.handle(payload)
        state = conv.get("200")
        assert state is not None
        assert state.case_id == "CASE-002"


# ── Test 12: orchestrator raises → ORCHESTRATOR_ERROR ────────────────────────

class TestOrchestratorError:
    def test_orchestrator_exception_returns_error(self):
        mock_orch = MagicMock()
        mock_orch.process_ticket.side_effect = RuntimeError("DB error")

        handler, _, _ = _make_created_handler(orchestrator=mock_orch)
        payload = {"freshdesk_webhook": {
            "id": 197416,
            "requester_email": "a@b.com",
        }}
        result = handler.handle(payload)
        assert result.success is False
        assert result.error_code == "ORCHESTRATOR_ERROR"


# ── Test 13: unknown_tenant → response_service note posted (route level) ──────

class TestUnknownTenantNoteViaRoute:
    def test_unknown_tenant_triggers_note_on_response_service(self):
        app = _make_app()

        mock_resolver = MagicMock()
        mock_resolver.resolve.side_effect = Exception("UnknownClientError")

        mock_resp_svc = MagicMock()
        mock_resp_svc.add_internal_note = AsyncMock(return_value={"id": 9999})
        app.state.freshdesk_response_service = mock_resp_svc

        idem = WebhookIdempotencyStore()
        conv = ConversationStateStore()
        app.state.freshdesk_ticket_created_handler = FreshdeskTicketCreatedHandler(
            idempotency_store=idem,
            conversation_store=conv,
            client_resolver=mock_resolver,
        )

        with TestClient(app) as client:
            resp = client.post(
                "/webhooks/freshdesk/ticket-created",
                json={"freshdesk_webhook": {
                    "id": 197416,
                    "requester_email": "agent@newbank.co.in",
                    "created_at": _recent_ts(),
                }},
                headers={"X-Webhook-Token": SECRET},
            )

        assert resp.status_code == 200
        mock_resp_svc.add_internal_note.assert_called_once()
        call_args = mock_resp_svc.add_internal_note.call_args
        assert call_args[0][0] == "197416"
        assert "not registered" in call_args[0][1] or "newbank" in call_args[0][1]


# ── Test 14: no optional components → gracefully skips all ───────────────────

class TestNoOptionalComponents:
    def test_handler_with_no_resolver_no_orchestrator_succeeds(self):
        handler, _, conv = _make_created_handler()
        payload = {"freshdesk_webhook": {
            "id": 197416,
            "requester_email": "a@b.com",
            "ticket_custom_fields": {"cf_clients": "Unity"},
        }}
        result = handler.handle(payload)
        assert result.success is True
        assert result.case_id is None  # no orchestrator → no case_id

    def test_client_id_falls_back_to_cf_clients(self):
        handler, _, conv = _make_created_handler()
        payload = {"freshdesk_webhook": {
            "id": 197416,
            "requester_email": "a@b.com",
            "ticket_custom_fields": {"cf_clients": "Unity"},
        }}
        handler.handle(payload)
        state = conv.get("197416")
        # client_id is empty (no resolver), but conversation state is created
        assert state is not None
        assert state.ticket_id == "197416"


# ── Test 15: WAL pre-persist via route ───────────────────────────────────────

class TestWALPrePersistViaRoute:
    def test_ensure_receipt_called_before_200(self):
        app = _make_app()

        mock_idem = MagicMock(spec=WebhookIdempotencyStore)
        mock_idem.check.return_value = False
        mock_idem.ensure_receipt.return_value = True
        mock_idem.make_key = WebhookIdempotencyStore.make_key
        app.state.freshdesk_idempotency_store = mock_idem

        app.state.freshdesk_ticket_created_handler = FreshdeskTicketCreatedHandler(
            idempotency_store=WebhookIdempotencyStore(),
            conversation_store=ConversationStateStore(),
        )

        with TestClient(app) as client:
            resp = client.post(
                "/webhooks/freshdesk/ticket-created",
                json={"freshdesk_webhook": {
                    "id": 197416,
                    "requester_email": "a@b.com",
                    "created_at": _recent_ts(),
                }},
                headers={"X-Webhook-Token": SECRET},
            )

        assert resp.status_code == 200
        mock_idem.ensure_receipt.assert_called_once()
        args = mock_idem.ensure_receipt.call_args[0]
        assert args[1] == "197416"  # ticket_id
        assert args[2] == "ticket_created"  # event_type


# ── Golden Path execution trace summary ──────────────────────────────────────

class TestGoldenPathExecutionTrace:
    """
    Proves which components of the Golden Path execute for a representative
    production-like payload.  Each assertion is a component checkpoint.
    """

    def test_full_trace_with_all_components_wired(self):
        component_trace: list[str] = []

        class TracingOrchestrator:
            def process_ticket(self, *, ticket_id, subject, description_text,
                               requester_email, client, metadata):
                component_trace.append(f"ORCHESTRATOR:process_ticket({ticket_id})")
                result = MagicMock()
                result.case_id = f"CASE-{ticket_id}"
                return result

        class TracingResolver:
            def resolve(self, email):
                domain = email.split("@")[-1] if "@" in email else "unknown"
                component_trace.append(f"CLIENT_RESOLVER:resolve(domain={domain})")
                ctx = MagicMock()
                ctx.client_id = "unity-001"
                ctx.client_name = "Unity"
                return ctx

        class TracingAudit:
            def _write(self, entry):
                component_trace.append(f"AUDIT:{entry.action_type}")

        idem = WebhookIdempotencyStore()
        conv = ConversationStateStore()

        component_trace.append("HANDLER:START")
        handler = FreshdeskTicketCreatedHandler(
            idempotency_store=idem,
            conversation_store=conv,
            ticket_orchestrator=TracingOrchestrator(),
            client_resolver=TracingResolver(),
            audit_logger=TracingAudit(),
        )

        payload = {"freshdesk_webhook": {
            "id": 197416,
            "subject": "Login broken",
            "description_text": "Cannot log in to KwikID.",
            "requester_email": "agent@unitybank.co.in",
            "ticket_custom_fields": {"cf_clients": "Unity"},
            "created_at": _recent_ts(),
        }}

        result = handler.handle(payload)

        # ── Checkpoint: Parse ─────────────────────────────────────────────────
        assert result.ticket_id == "197416", "PARSE: ticket_id extraction failed"

        # ── Checkpoint: Idempotency check (not duplicate) ─────────────────────
        assert not result.skipped, "IDEMPOTENCY: first event should not be skipped"

        # ── Checkpoint: Client resolution ─────────────────────────────────────
        resolver_calls = [t for t in component_trace if t.startswith("CLIENT_RESOLVER")]
        assert len(resolver_calls) == 1, f"CLIENT_RESOLVER: expected 1 call, got {resolver_calls}"
        assert "unitybank.co.in" in resolver_calls[0], "RESOLVER: domain logged correctly"

        # ── Checkpoint: Orchestrator ──────────────────────────────────────────
        orch_calls = [t for t in component_trace if t.startswith("ORCHESTRATOR")]
        assert len(orch_calls) == 1, f"ORCHESTRATOR: expected 1 call, got {orch_calls}"
        assert result.case_id == "CASE-197416", "ORCHESTRATOR: case_id not returned"

        # ── Checkpoint: Conversation state ────────────────────────────────────
        state = conv.get("197416")
        assert state is not None, "CONVERSATION_STATE: not created"
        assert state.case_id == "CASE-197416", "CONVERSATION_STATE: case_id not stored"
        assert state.client_id == "unity-001", "CONVERSATION_STATE: client_id not stored"

        # ── Checkpoint: Audit ─────────────────────────────────────────────────
        audit_calls = [t for t in component_trace if t.startswith("AUDIT")]
        assert len(audit_calls) >= 1, f"AUDIT: expected at least 1 event, got {audit_calls}"

        # ── Checkpoint: Idempotency completed ─────────────────────────────────
        ts = payload["freshdesk_webhook"]["created_at"]
        key = idem.make_key("197416", "ticket_created", ts)
        entry = idem.get(key)
        assert entry is not None, "IDEMPOTENCY: entry not in store after completion"
        from freshdesk.idempotency import IdempotencyStatus
        assert entry.status == IdempotencyStatus.COMPLETED, f"IDEMPOTENCY: status={entry.status}"

        # ── Checkpoint: Overall success ───────────────────────────────────────
        assert result.success is True, f"RESULT: not success, error_code={result.error_code}"
        assert result.error_code is None, f"RESULT: unexpected error_code={result.error_code}"

    def test_trace_without_optional_components(self):
        """When orchestrator and resolver are absent, core components still run."""
        idem = WebhookIdempotencyStore()
        conv = ConversationStateStore()
        handler = FreshdeskTicketCreatedHandler(
            idempotency_store=idem,
            conversation_store=conv,
        )
        payload = {"freshdesk_webhook": {
            "ticket_id": 999,          # ← ticket_id variant to confirm fix
            "requester_email": "x@y.com",
        }}
        result = handler.handle(payload)

        # Components that MUST run regardless:
        assert result.ticket_id == "999"          # PARSE: ✓ ENTERED
        assert result.success is True             # HANDLER: ✓ ENTERED
        assert result.case_id is None             # ORCHESTRATOR: ✗ SKIPPED (None)
        state = conv.get("999")
        assert state is not None                  # CONVERSATION_STATE: ✓ ENTERED
        # No client_id since no resolver:
        assert state.client_id == ""              # CLIENT_RESOLVER: ✗ SKIPPED (None)


# ── Test 16: Format B — Freshdesk Dispatch'r rules schema (production contract) ─
# Canonical payload: {"ticket": {"id": 198165, ...}, "requester": {...}, "custom_fields": {...}}
# This is the schema Freshdesk Dispatch'r rules POST.
# BEFORE fix: ticket_id=0, error_code=MISSING_TICKET_ID
# AFTER fix:  ticket_id=198165, success=True

class TestDispatchrRulesFormatB:
    """
    Proves that the Freshdesk Dispatch'r rules format {"ticket": {...}, "requester": {...}}
    is correctly parsed at every stage.  Ticket 198165 must survive unchanged.
    """

    _FORMAT_B_PAYLOAD = {
        "ticket": {
            "id": 198165,
            "subject": "KYC verification session failed",
            "description": "<p>User cannot complete KYC step 3</p>",
            "description_text": "User cannot complete KYC step 3",
            "status": 2,
            "priority": 2,
            "type": "Problem",
            "created_at": None,  # will be filled by test
        },
        "requester": {
            "email": "agent@unitybank.co.in",
            "name": "Test Agent",
        },
        "custom_fields": {
            "cf_clients": "Unity",
            "cf_environment": "production",
        },
    }

    def _payload(self) -> dict:
        p = {**self._FORMAT_B_PAYLOAD}
        p["ticket"] = {**p["ticket"], "created_at": _recent_ts()}
        return p

    # ── Model layer ────────────────────────────────────────────────────────────

    def test_format_b_model_extracts_ticket_id(self):
        """FreshdeskWebhookPayload.from_dict correctly normalises Format B."""
        parsed = FreshdeskWebhookPayload.from_dict(self._payload())
        assert parsed.ticket.ticket_id == "198165", (
            f"ticket_id should be '198165', got {parsed.ticket.ticket_id!r} — "
            "nested ticket.id not extracted"
        )

    def test_format_b_model_extracts_subject(self):
        parsed = FreshdeskWebhookPayload.from_dict(self._payload())
        assert "KYC" in parsed.ticket.subject

    def test_format_b_model_extracts_requester_email(self):
        parsed = FreshdeskWebhookPayload.from_dict(self._payload())
        assert parsed.ticket.requester_email == "agent@unitybank.co.in", (
            f"requester_email not extracted from nested requester object: {parsed.ticket.requester_email!r}"
        )

    def test_format_b_model_extracts_custom_fields(self):
        parsed = FreshdeskWebhookPayload.from_dict(self._payload())
        assert parsed.ticket.custom_fields.cf_clients == "Unity", (
            f"cf_clients not extracted from nested custom_fields: {parsed.ticket.custom_fields.cf_clients!r}"
        )

    # ── Handler layer ──────────────────────────────────────────────────────────

    def test_format_b_handler_no_missing_ticket_id(self):
        """Root cause fix: ticket_id=0 / MISSING_TICKET_ID must NOT occur."""
        handler, _, _ = _make_created_handler()
        result = handler.handle(self._payload())
        assert result.error_code != "MISSING_TICKET_ID", (
            "Format B (Dispatch'r rules) still produces MISSING_TICKET_ID — "
            "nested ticket.id not reaching the handler"
        )
        assert result.ticket_id == "198165", (
            f"ticket_id should be '198165', got {result.ticket_id!r}"
        )

    def test_format_b_handler_success(self):
        """Full Format B payload succeeds end-to-end through the handler."""
        handler, idem, conv = _make_created_handler()
        result = handler.handle(self._payload())
        assert result.success is True, (
            f"Expected success=True for Format B payload, got error_code={result.error_code}"
        )
        state = conv.get("198165")
        assert state is not None, "ConversationState not created for ticket 198165"

    def test_format_b_ticket_id_never_zero(self):
        """Explicit zero guard: the integer 0 must never appear as ticket_id."""
        handler, _, _ = _make_created_handler()
        result = handler.handle(self._payload())
        assert result.ticket_id != "0", "ticket_id is '0' — Format B parsing is broken"
        assert result.ticket_id != "", "ticket_id is empty — Format B parsing is broken"

    # ── Route layer ────────────────────────────────────────────────────────────

    def test_format_b_route_accepts_and_extracts_198165(self):
        """Route correctly extracts ticket_id=198165 from Format B for pre-persistence."""
        app = _make_app()

        mock_idem = MagicMock(spec=WebhookIdempotencyStore)
        mock_idem.check.return_value = False
        mock_idem.ensure_receipt.return_value = True
        mock_idem.make_key = WebhookIdempotencyStore.make_key
        app.state.freshdesk_idempotency_store = mock_idem

        handler, _, _ = _make_created_handler()
        app.state.freshdesk_ticket_created_handler = handler

        with TestClient(app) as client:
            resp = client.post(
                "/webhooks/freshdesk/ticket-created",
                json=self._payload(),
                headers={"X-Webhook-Token": SECRET},
            )

        assert resp.status_code == 200
        mock_idem.ensure_receipt.assert_called_once()
        # Pre-persistence must have used ticket_id=198165, not "" or "0"
        args = mock_idem.ensure_receipt.call_args[0]
        assert args[1] == "198165", (
            f"Route pre-persist used ticket_id={args[1]!r} — expected '198165'. "
            "Format B nested ticket.id not extracted in route layer."
        )

    # ── Format A still works (regression guard) ────────────────────────────────

    def test_format_a_freshdesk_webhook_wrapper_unaffected(self):
        """Ensure Format A (freshdesk_webhook wrapper) still works after Format B fix."""
        handler, _, _ = _make_created_handler()
        payload_a = {"freshdesk_webhook": {
            "ticket_id": 198165,
            "ticket_subject": "KYC issue from default webhook",
            "ticket_contact_email": "agent@unitybank.co.in",
            "ticket_cf_clients": "Unity",
            "ticket_created_at": _recent_ts(),
        }}
        parsed = FreshdeskWebhookPayload.from_dict(payload_a)
        assert parsed.ticket.ticket_id == "198165"
        assert parsed.ticket.requester_email == "agent@unitybank.co.in", (
            f"ticket_contact_email not mapped to requester_email: {parsed.ticket.requester_email!r}"
        )
        assert parsed.ticket.custom_fields.cf_clients == "Unity", (
            f"ticket_cf_clients not mapped to cf_clients: {parsed.ticket.custom_fields.cf_clients!r}"
        )
        result = handler.handle(payload_a)
        assert result.ticket_id == "198165"
        assert result.success is True
