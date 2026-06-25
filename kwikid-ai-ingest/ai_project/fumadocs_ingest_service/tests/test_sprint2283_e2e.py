"""
Sprint 2.28.3 — End-to-end Freshdesk webhook integration tests.

12 scenarios covering the full Freshdesk → Backend → Freshdesk pipeline.
All external dependencies (RAG, Freshdesk API, CaseService, DB) are mocked
so these tests run without network or DB access.

Auth is configured via env vars to use FRESHDESK_WEBHOOK_MODE=static with a
known FRESHDESK_WEBHOOK_SECRET, matching the dev setup used in Sprint 2.28.
"""
import json
import os
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from fastapi.testclient import TestClient

_WEBHOOK_SECRET = "test-sprint2283-static-secret"
_WEBHOOK_URL = "/freshdesk/webhook"

# ── Environment setup ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def webhook_env(monkeypatch):
    """Enable the webhook with a known static secret for all tests in this module."""
    monkeypatch.setenv("FRESHDESK_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("FRESHDESK_WEBHOOK_SECRET", _WEBHOOK_SECRET)
    monkeypatch.setenv("FRESHDESK_WEBHOOK_MODE", "static")
    monkeypatch.setenv("FRESHDESK_WEBHOOK_MIN_CONFIDENCE", "low")
    monkeypatch.setenv("FRESHDESK_WEBHOOK_REPLY_AS_NOTE", "true")
    monkeypatch.setenv("FRESHDESK_WEBHOOK_DEFAULT_CLIENT", "")
    monkeypatch.setenv("FRESHDESK_DOMAIN", "kwikid.freshdesk.com")
    monkeypatch.setenv("FRESHDESK_API_KEY", "test-fd-api-key")
    monkeypatch.setenv("ACTION_GATEWAY_ENABLED", "false")


@pytest.fixture()
def client():
    from app.main import app
    return TestClient(app)


# ── Mock builders ──────────────────────────────────────────────────────────────

def _make_gen_result(
    *,
    answer: str = "Please reset your MPIN via Settings > Security.",
    confidence: str = "high",
    confidence_score: float = 0.85,
    requires_human: bool = False,
    citations: list | None = None,
    chunks: list | None = None,
    session_id: str = "fd-197416",
    message_id: str = "msg-abc123",
):
    result = MagicMock()
    result.answer = answer
    result.confidence = confidence
    result.confidence_score = confidence_score
    result.requires_human = requires_human
    result.citations = citations or []
    result.chunks = chunks or [{"content": "chunk1", "similarity": 0.9}]
    result.diagnostics = {"match_type": "exact_match", "duration_ms": 120.0}
    result.session_id = session_id
    result.message_id = message_id
    result.insufficient_context = False
    result.clarification = None
    return result


def _auth_headers() -> dict:
    return {"X-Webhook-Token": _WEBHOOK_SECRET}


def _fd_payload(
    ticket_id: str = "197416",
    subject: str = "MPIN reset issue",
    description: str = "Customer cannot reset MPIN via app.",
    cf_clients: str = "Unity",
    email: str = "agent@unitybank.co.in",
    tags: list | None = None,
) -> dict:
    """Build a realistic Freshdesk webhook payload."""
    return {
        "freshdesk_webhook": {
            "ticket_id": ticket_id,
            "ticket_subject": subject,
            "ticket_description": description,
            "ticket_description_text": description,
            "requester_email": email,
            "ticket_tags": ",".join(tags or []),
            "ticket_custom_fields": {"cf_clients": cf_clients},
            "ticket_status": "open",
            "ticket_priority": "medium",
        }
    }


# ── Scenario 1: Ticket Created (happy path) ───────────────────────────────────

class TestScenario1TicketCreated:
    def test_ticket_created_returns_200(self, client):
        gen_result = _make_gen_result()
        with (
            patch("app.main._get_generator") as mock_gen_factory,
            patch("app.main.FreshdeskReplyClient") as mock_reply_cls,
            patch("app.main._get_case_service") as mock_cs_factory,
        ):
            mock_generator = MagicMock()
            mock_generator.generate.return_value = gen_result
            mock_gen_factory.return_value = mock_generator
            mock_reply_cls.return_value.post_note.return_value = {"id": 9001}
            mock_case = MagicMock()
            mock_case.case_id = "case-001"
            mock_case.current_state.value = "ACTIVE"
            mock_case.topic = "MPIN_RESET"
            from case_engine.models import CaseState
            mock_case.current_state = CaseState.TRIAGE_COMPLETE
            mock_cs_factory.return_value.open_case.return_value = mock_case
            mock_cs_factory.return_value.classify_case.return_value = None

            resp = client.post(
                _WEBHOOK_URL,
                json=_fd_payload(),
                headers=_auth_headers(),
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["ticket_id"] == "197416"


# ── Scenario 2: Tenant Resolved via cf_clients ────────────────────────────────

class TestScenario2TenantResolved:
    def test_unity_resolved_from_cf_clients(self, client):
        gen_result = _make_gen_result()
        with (
            patch("app.main._get_generator") as mock_gen_factory,
            patch("app.main.FreshdeskReplyClient") as mock_reply_cls,
            patch("app.main._get_case_service") as mock_cs_factory,
        ):
            mock_generator = MagicMock()
            mock_generator.generate.return_value = gen_result
            mock_gen_factory.return_value = mock_generator
            mock_reply_cls.return_value.post_note.return_value = {}
            mock_case = MagicMock()
            from case_engine.models import CaseState
            mock_case.current_state = CaseState.TRIAGE_COMPLETE
            mock_cs_factory.return_value.open_case.return_value = mock_case

            resp = client.post(
                _WEBHOOK_URL,
                json=_fd_payload(cf_clients="Unity"),
                headers=_auth_headers(),
            )

        assert resp.status_code == 200
        assert resp.json()["client"] == "Unity"

    def test_bob_resolved_from_cf_clients(self, client):
        gen_result = _make_gen_result()
        with (
            patch("app.main._get_generator") as mock_gen_factory,
            patch("app.main.FreshdeskReplyClient") as mock_reply_cls,
            patch("app.main._get_case_service") as mock_cs_factory,
        ):
            mock_generator = MagicMock()
            mock_generator.generate.return_value = gen_result
            mock_gen_factory.return_value = mock_generator
            mock_reply_cls.return_value.post_note.return_value = {}
            mock_case = MagicMock()
            from case_engine.models import CaseState
            mock_case.current_state = CaseState.TRIAGE_COMPLETE
            mock_cs_factory.return_value.open_case.return_value = mock_case

            resp = client.post(
                _WEBHOOK_URL,
                json=_fd_payload(cf_clients="BOB", email="agent@bankofbaroda.com"),
                headers=_auth_headers(),
            )

        assert resp.status_code == 200
        assert resp.json()["client"] == "BOB"


# ── Scenario 3: Case Created ───────────────────────────────────────────────────

class TestScenario3CaseCreated:
    def test_case_service_called_with_ticket_id_and_tenant(self, client):
        gen_result = _make_gen_result()
        with (
            patch("app.main._get_generator") as mock_gen_factory,
            patch("app.main.FreshdeskReplyClient") as mock_reply_cls,
            patch("app.main._get_case_service") as mock_cs_factory,
        ):
            mock_generator = MagicMock()
            mock_generator.generate.return_value = gen_result
            mock_gen_factory.return_value = mock_generator
            mock_reply_cls.return_value.post_note.return_value = {}
            mock_cs = MagicMock()
            mock_case = MagicMock()
            from case_engine.models import CaseState
            mock_case.current_state = CaseState.TRIAGE_COMPLETE
            mock_case.case_id = "case-xyz"
            mock_cs.open_case.return_value = mock_case
            mock_cs_factory.return_value = mock_cs

            resp = client.post(
                _WEBHOOK_URL,
                json=_fd_payload(ticket_id="197416", cf_clients="CBI"),
                headers=_auth_headers(),
            )

        assert resp.status_code == 200
        mock_cs.open_case.assert_called_once_with("197416", "CBI")


# ── Scenario 4: Internal Note Generated ───────────────────────────────────────

class TestScenario4InternalNote:
    def test_private_note_posted_when_reply_as_note_true(self, client):
        gen_result = _make_gen_result(confidence="high", requires_human=False)
        with (
            patch("app.main._ACTION_GATEWAY_ENABLED", False),
            patch("app.main._get_generator") as mock_gen_factory,
            patch("app.main.FreshdeskReplyClient") as mock_reply_cls,
            patch("app.main._get_case_service") as mock_cs_factory,
        ):
            mock_generator = MagicMock()
            mock_generator.generate.return_value = gen_result
            mock_gen_factory.return_value = mock_generator
            mock_fd_client = MagicMock()
            mock_fd_client.post_note.return_value = {"id": 99}
            mock_reply_cls.return_value = mock_fd_client
            mock_case = MagicMock()
            from case_engine.models import CaseState
            mock_case.current_state = CaseState.TRIAGE_COMPLETE
            mock_cs_factory.return_value.open_case.return_value = mock_case

            resp = client.post(
                _WEBHOOK_URL,
                json=_fd_payload(),
                headers=_auth_headers(),
            )

        assert resp.status_code == 200
        assert resp.json()["action"] == "private_note_posted"
        mock_fd_client.post_note.assert_called_once()
        call_args = mock_fd_client.post_note.call_args
        assert call_args[0][0] == "197416"  # ticket_id
        assert call_args[1].get("private") is True


# ── Scenario 5: Customer Reply Generated ──────────────────────────────────────

class TestScenario5CustomerReply:
    def test_public_reply_posted_when_reply_as_note_false(self, client, monkeypatch):
        monkeypatch.setenv("FRESHDESK_WEBHOOK_REPLY_AS_NOTE", "false")
        gen_result = _make_gen_result(confidence="high", requires_human=False)
        with (
            patch("app.main._ACTION_GATEWAY_ENABLED", False),
            patch("app.main._get_generator") as mock_gen_factory,
            patch("app.main.FreshdeskReplyClient") as mock_reply_cls,
            patch("app.main._get_case_service") as mock_cs_factory,
        ):
            mock_generator = MagicMock()
            mock_generator.generate.return_value = gen_result
            mock_gen_factory.return_value = mock_generator
            mock_fd_client = MagicMock()
            mock_fd_client.post_reply.return_value = {"id": 88}
            mock_reply_cls.return_value = mock_fd_client
            mock_case = MagicMock()
            from case_engine.models import CaseState
            mock_case.current_state = CaseState.TRIAGE_COMPLETE
            mock_cs_factory.return_value.open_case.return_value = mock_case

            resp = client.post(
                _WEBHOOK_URL,
                json=_fd_payload(),
                headers=_auth_headers(),
            )

        assert resp.status_code == 200
        assert resp.json()["action"] == "public_reply_posted"
        mock_fd_client.post_reply.assert_called_once()


# ── Scenario 6: Clarification Loop ────────────────────────────────────────────

class TestScenario6ClarificationLoop:
    def test_ticket_updated_webhook_accepted(self, client):
        """
        The /webhooks/freshdesk/ticket-updated endpoint must return 200 when a
        customer reply arrives.

        The Sprint 2.28.x verifier always uses HMAC (not static mode), so we mock
        _get_verifier to return None (no-auth mode), matching a test environment
        where FRESHDESK_WEBHOOK_ENFORCE_HMAC=false and no verifier is configured.
        Full RAG loop requires Sprint 2.29 wiring of freshdesk_rag_processor.
        """
        payload = {
            "freshdesk_webhook": {
                "id": "197416",
                "ticket_subject": "RE: MPIN reset issue",
                "description": "I tried that but it still doesn't work.",
                "description_text": "I tried that but it still doesn't work.",
                "requester_email": "customer@unitybank.co.in",
                "tags": [],
                "custom_fields": {"cf_clients": "Unity"},
                "status": "open",
                "priority": "medium",
                "updated_at": "2026-06-24T12:00:00Z",
                "latest_comment": {
                    "body": "I tried that but it still doesn't work.",
                    "body_text": "I tried that but it still doesn't work.",
                    "from_requester": True,
                },
            }
        }
        with patch("api.routes.webhooks.freshdesk._get_verifier", return_value=None):
            resp = client.post(
                "/webhooks/freshdesk/ticket-updated",
                json=payload,
            )
        assert resp.status_code == 200


# ── Scenario 7: Unknown Tenant ────────────────────────────────────────────────

class TestScenario7UnknownTenant:
    def test_returns_skipped_no_tenant(self, client):
        payload = _fd_payload(
            cf_clients="",        # empty — falls through
            email="user@unknownbank.xyz",  # not in domain map
            tags=[],              # no tag prefix
        )
        resp = client.post(_WEBHOOK_URL, json=payload, headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "skipped"
        assert body["reason"] == "no_tenant"
        assert body["ticket_id"] == "197416"

    def test_others_passthrough_with_no_email_returns_no_tenant(self, client):
        """'Others' in cf_clients with no usable email → no_tenant."""
        payload = _fd_payload(cf_clients="Others", email="user@unknownbank.xyz")
        resp = client.post(_WEBHOOK_URL, json=payload, headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "skipped"
        assert body["reason"] == "no_tenant"

    def test_others_passthrough_with_known_email_resolves(self, client):
        """'Others' in cf_clients but known email domain → tenant resolved via email."""
        gen_result = _make_gen_result()
        payload = _fd_payload(cf_clients="Others", email="user@unitybank.co.in")
        with (
            patch("app.main._get_generator") as mock_gen_factory,
            patch("app.main.FreshdeskReplyClient") as mock_reply_cls,
            patch("app.main._get_case_service") as mock_cs_factory,
        ):
            mock_generator = MagicMock()
            mock_generator.generate.return_value = gen_result
            mock_gen_factory.return_value = mock_generator
            mock_reply_cls.return_value.post_note.return_value = {}
            mock_case = MagicMock()
            from case_engine.models import CaseState
            mock_case.current_state = CaseState.TRIAGE_COMPLETE
            mock_cs_factory.return_value.open_case.return_value = mock_case

            resp = client.post(_WEBHOOK_URL, json=payload, headers=_auth_headers())

        assert resp.status_code == 200
        assert resp.json()["client"] == "Unity"


# ── Scenario 8: Missing Custom Fields ────────────────────────────────────────

class TestScenario8MissingCustomFields:
    def test_no_custom_fields_falls_back_to_email_domain(self, client):
        """When custom_fields is absent, email domain must resolve the tenant."""
        gen_result = _make_gen_result()
        # Build flat payload without 'freshdesk_webhook' wrapper and without custom_fields
        payload = {
            "ticket_id": "197417",
            "ticket_subject": "Login failure",
            "ticket_description": "Cannot log in to the app.",
            "ticket_description_text": "Cannot log in to the app.",
            "requester_email": "agent@rblbank.com",
            "ticket_tags": [],
            "ticket_status": "open",
            "ticket_priority": "high",
            # no 'ticket_custom_fields' key at all
        }
        with (
            patch("app.main._get_generator") as mock_gen_factory,
            patch("app.main.FreshdeskReplyClient") as mock_reply_cls,
            patch("app.main._get_case_service") as mock_cs_factory,
        ):
            mock_generator = MagicMock()
            mock_generator.generate.return_value = gen_result
            mock_gen_factory.return_value = mock_generator
            mock_reply_cls.return_value.post_note.return_value = {}
            mock_case = MagicMock()
            from case_engine.models import CaseState
            mock_case.current_state = CaseState.TRIAGE_COMPLETE
            mock_cs_factory.return_value.open_case.return_value = mock_case

            resp = client.post(_WEBHOOK_URL, json=payload, headers=_auth_headers())

        assert resp.status_code == 200
        assert resp.json()["client"] == "RBL"

    def test_no_custom_fields_no_email_uses_default_client(self, client, monkeypatch):
        monkeypatch.setenv("FRESHDESK_WEBHOOK_DEFAULT_CLIENT", "DefaultBank")
        gen_result = _make_gen_result()
        payload = {
            "ticket_id": "197418",
            "ticket_subject": "App crash",
            "ticket_description_text": "App crashes on startup.",
            "requester_email": "",
            "ticket_tags": [],
            "ticket_status": "open",
        }
        with (
            patch("app.main._get_generator") as mock_gen_factory,
            patch("app.main.FreshdeskReplyClient") as mock_reply_cls,
            patch("app.main._get_case_service") as mock_cs_factory,
        ):
            mock_generator = MagicMock()
            mock_generator.generate.return_value = gen_result
            mock_gen_factory.return_value = mock_generator
            mock_reply_cls.return_value.post_note.return_value = {}
            mock_case = MagicMock()
            from case_engine.models import CaseState
            mock_case.current_state = CaseState.TRIAGE_COMPLETE
            mock_cs_factory.return_value.open_case.return_value = mock_case

            resp = client.post(_WEBHOOK_URL, json=payload, headers=_auth_headers())

        assert resp.status_code == 200
        assert resp.json()["client"] == "DefaultBank"


# ── Scenario 9: Invalid Payload ───────────────────────────────────────────────

class TestScenario9InvalidPayload:
    def test_non_json_body_returns_400(self, client):
        resp = client.post(
            _WEBHOOK_URL,
            content=b"not-json-at-all",
            headers={**_auth_headers(), "Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_json"

    def test_array_payload_returns_400(self, client):
        resp = client.post(
            _WEBHOOK_URL,
            json=["not", "a", "dict"],
            headers=_auth_headers(),
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "payload_must_be_object"

    def test_missing_ticket_id_returns_skipped(self, client):
        payload = {
            "ticket_subject": "No ID here",
            "ticket_description_text": "Missing ticket_id field.",
            "requester_email": "agent@unitybank.co.in",
        }
        resp = client.post(_WEBHOOK_URL, json=payload, headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "skipped"
        assert body["reason"] == "missing_ticket_id"

    def test_missing_auth_header_returns_401(self, client):
        resp = client.post(_WEBHOOK_URL, json=_fd_payload())
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"] == "invalid_webhook_token"

    def test_wrong_token_returns_401(self, client):
        resp = client.post(
            _WEBHOOK_URL,
            json=_fd_payload(),
            headers={"X-Webhook-Token": "wrong-token"},
        )
        assert resp.status_code == 401


# ── Scenario 10: Duplicate Webhook (idempotency) ──────────────────────────────

class TestScenario10DuplicateWebhook:
    def test_same_ticket_id_processed_each_time_via_old_path(self, client):
        """
        The old /freshdesk/webhook path has no idempotency store — each call
        runs through the full pipeline. This test confirms the second call
        also returns 200 (not rejected as duplicate).
        """
        gen_result = _make_gen_result()
        with (
            patch("app.main._get_generator") as mock_gen_factory,
            patch("app.main.FreshdeskReplyClient") as mock_reply_cls,
            patch("app.main._get_case_service") as mock_cs_factory,
        ):
            mock_generator = MagicMock()
            mock_generator.generate.return_value = gen_result
            mock_gen_factory.return_value = mock_generator
            mock_reply_cls.return_value.post_note.return_value = {}
            mock_case = MagicMock()
            from case_engine.models import CaseState
            mock_case.current_state = CaseState.TRIAGE_COMPLETE
            mock_cs_factory.return_value.open_case.return_value = mock_case

            resp1 = client.post(_WEBHOOK_URL, json=_fd_payload(), headers=_auth_headers())
            resp2 = client.post(_WEBHOOK_URL, json=_fd_payload(), headers=_auth_headers())

        assert resp1.status_code == 200
        assert resp2.status_code == 200


# ── Scenario 11: Webhook Retry (Freshdesk API transient failure) ──────────────

class TestScenario11WebhookRetry:
    def test_freshdesk_api_transient_error_returns_502(self, client):
        """If FreshdeskReplyClient raises after all retries, webhook returns 502."""
        import httpx
        gen_result = _make_gen_result()
        with (
            patch("app.main._ACTION_GATEWAY_ENABLED", False),
            patch("app.main._get_generator") as mock_gen_factory,
            patch("app.main.FreshdeskReplyClient") as mock_reply_cls,
            patch("app.main._get_case_service") as mock_cs_factory,
        ):
            mock_generator = MagicMock()
            mock_generator.generate.return_value = gen_result
            mock_gen_factory.return_value = mock_generator
            mock_fd_client = MagicMock()
            mock_fd_client.post_note.side_effect = RuntimeError("Connection timeout")
            mock_reply_cls.return_value = mock_fd_client
            mock_case = MagicMock()
            from case_engine.models import CaseState
            mock_case.current_state = CaseState.TRIAGE_COMPLETE
            mock_cs_factory.return_value.open_case.return_value = mock_case

            resp = client.post(_WEBHOOK_URL, json=_fd_payload(), headers=_auth_headers())

        assert resp.status_code == 502
        body = resp.json()
        assert body["detail"]["status"] == "freshdesk_api_error"
        assert body["detail"]["ticket_id"] == "197416"


# ── Scenario 12: Freshdesk API Failure (4xx) ──────────────────────────────────

class TestScenario12FreshdeskAPIFailure:
    def test_freshdesk_401_propagates_as_502(self, client):
        """Freshdesk API 401 (bad API key) should propagate as 502 to caller."""
        import httpx
        gen_result = _make_gen_result()
        with (
            patch("app.main._ACTION_GATEWAY_ENABLED", False),
            patch("app.main._get_generator") as mock_gen_factory,
            patch("app.main.FreshdeskReplyClient") as mock_reply_cls,
            patch("app.main._get_case_service") as mock_cs_factory,
        ):
            mock_generator = MagicMock()
            mock_generator.generate.return_value = gen_result
            mock_gen_factory.return_value = mock_generator
            mock_fd_client = MagicMock()
            mock_req = MagicMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 401
            mock_fd_client.post_note.side_effect = httpx.HTTPStatusError(
                "401 Unauthorized", request=mock_req, response=mock_resp
            )
            mock_reply_cls.return_value = mock_fd_client
            mock_case = MagicMock()
            from case_engine.models import CaseState
            mock_case.current_state = CaseState.TRIAGE_COMPLETE
            mock_cs_factory.return_value.open_case.return_value = mock_case

            resp = client.post(_WEBHOOK_URL, json=_fd_payload(), headers=_auth_headers())

        assert resp.status_code == 502
        assert resp.json()["detail"]["status"] == "freshdesk_api_error"

    def test_rag_pipeline_error_propagates_as_502(self, client):
        """RuntimeError in RAG pipeline returns 502 with rag_error status."""
        with (
            patch("app.main._get_generator") as mock_gen_factory,
            patch("app.main._get_case_service") as mock_cs_factory,
        ):
            mock_generator = MagicMock()
            mock_generator.generate.side_effect = RuntimeError("Supabase unavailable")
            mock_gen_factory.return_value = mock_generator
            mock_case = MagicMock()
            from case_engine.models import CaseState
            mock_case.current_state = CaseState.TRIAGE_COMPLETE
            mock_cs_factory.return_value.open_case.return_value = mock_case

            resp = client.post(_WEBHOOK_URL, json=_fd_payload(), headers=_auth_headers())

        assert resp.status_code == 502
        body = resp.json()
        assert body["detail"]["status"] == "rag_error"
        assert body["detail"]["ticket_id"] == "197416"
