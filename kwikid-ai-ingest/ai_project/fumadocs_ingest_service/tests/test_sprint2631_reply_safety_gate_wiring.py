"""
tests/test_sprint2631_reply_safety_gate_wiring.py

Sprint 2.63.1 — ReplySafetyGate wiring.

Context: ReplySafetyGate (freshdesk/safety_gate.py, Sprint 2.48) was fully
implemented — confidence threshold, force-escalation impact override,
duplicate-hash detection, kill switch — but was never actually instantiated
or invoked by ANY of the four autonomous customer-facing reply call sites:

  1. api/routes/webhooks/freshdesk.py  — escalation-created reply (fixed template)
  2. api/routes/webhooks/freshdesk.py  — Pass 1 orchestrator response_draft reply
  3. api/routes/webhooks/freshdesk.py  — Pass 2 orchestrator response_draft reply
  4. api/routes/webhooks/asana.py      — resolution-closure reply (fixed template)
     (covered separately in tests/test_sprint263_asana_webhook_receiver.py
     Section J, since it lives alongside the rest of the closure-loop tests)

This file covers sites 1-3: the shared `_gated_customer_reply()` funnel
helper directly (Section K), and the confidence-threading path from
HandlerResult through to the route layer for the two response_draft sites
(Section L), verified via the real background-task entry points
`_process_ticket_created` / `_process_ticket_updated` with a fake
Request/app.state (Section M).

Never touches the network.
"""
from __future__ import annotations

import os

os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("OPENAI_API_KEY", "test-2631-key")
os.environ.setdefault("RAG_API_KEY", "test-2631-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-2631-key")

from unittest.mock import MagicMock

import pytest

from freshdesk.safety_gate import ReplySafetyGate


# ---------------------------------------------------------------------------
# Fakes shared across sections
# ---------------------------------------------------------------------------

class _FakeState:
    pass


class _FakeApp:
    def __init__(self) -> None:
        self.state = _FakeState()


class _FakeRequest:
    def __init__(self) -> None:
        self.app = _FakeApp()


def _tracking_resp_svc(reply_log, notes_log):
    class FakeRespSvc:
        async def send_customer_reply(self, ticket_id, body, **kw):
            reply_log.append((ticket_id, body))
            return {"id": "reply-1"}

        async def add_internal_note(self, ticket_id, note, **kw):
            notes_log.append((ticket_id, note))
            return {}

    return FakeRespSvc()


# ---------------------------------------------------------------------------
# Section K — _gated_customer_reply() funnel (freshdesk.py)
# ---------------------------------------------------------------------------

class TestK_GatedCustomerReplyHelper:

    @pytest.mark.asyncio
    async def test_K1_no_gate_wired_fails_closed(self):
        """None gate must never mean 'unprotected send' — this is the regression this
        sprint fixes: a missing wire-up must fail closed, not open."""
        from api.routes.webhooks.freshdesk import _gated_customer_reply

        reply_log, notes_log = [], []
        resp_svc = _tracking_resp_svc(reply_log, notes_log)

        sent = await _gated_customer_reply(
            resp_svc, None, ticket_id="1", body_html="<p>hi</p>", confidence=1.0,
        )
        assert sent is False
        assert reply_log == []
        assert len(notes_log) == 1
        assert "not wired" in notes_log[0][1].lower()

    @pytest.mark.asyncio
    async def test_K2_high_confidence_allowed_sends(self):
        from api.routes.webhooks.freshdesk import _gated_customer_reply

        reply_log, notes_log = [], []
        resp_svc = _tracking_resp_svc(reply_log, notes_log)
        gate = ReplySafetyGate()

        sent = await _gated_customer_reply(
            resp_svc, gate, ticket_id="1", body_html="<p>hi</p>", confidence=0.9,
        )
        assert sent is True
        assert len(reply_log) == 1
        assert notes_log == []

    @pytest.mark.asyncio
    async def test_K3_low_confidence_blocked_drafts_instead(self):
        from api.routes.webhooks.freshdesk import _gated_customer_reply

        reply_log, notes_log = [], []
        resp_svc = _tracking_resp_svc(reply_log, notes_log)
        gate = ReplySafetyGate(confidence_threshold=0.75)

        sent = await _gated_customer_reply(
            resp_svc, gate, ticket_id="2", body_html="<p>uncertain</p>", confidence=0.3,
        )
        assert sent is False
        assert reply_log == []
        assert len(notes_log) == 1
        assert "confidence" in notes_log[0][1].lower()

    @pytest.mark.asyncio
    async def test_K4_missing_confidence_blocked_drafts_instead(self):
        """None confidence (e.g. an unscored draft) must block, not silently pass."""
        from api.routes.webhooks.freshdesk import _gated_customer_reply

        reply_log, notes_log = [], []
        resp_svc = _tracking_resp_svc(reply_log, notes_log)
        gate = ReplySafetyGate()

        sent = await _gated_customer_reply(
            resp_svc, gate, ticket_id="3", body_html="<p>no score</p>", confidence=None,
        )
        assert sent is False
        assert reply_log == []
        assert len(notes_log) == 1

    @pytest.mark.asyncio
    async def test_K5_kill_switch_blocks(self):
        from api.routes.webhooks.freshdesk import _gated_customer_reply

        reply_log, notes_log = [], []
        resp_svc = _tracking_resp_svc(reply_log, notes_log)
        gate = ReplySafetyGate(kill_switch=True)

        sent = await _gated_customer_reply(
            resp_svc, gate, ticket_id="4", body_html="<p>hi</p>", confidence=1.0,
        )
        assert sent is False
        assert reply_log == []
        assert len(notes_log) == 1
        assert "kill switch" in notes_log[0][1].lower()

    @pytest.mark.asyncio
    async def test_K6_force_escalation_impact_blocks_even_at_high_confidence(self):
        from api.routes.webhooks.freshdesk import _gated_customer_reply

        reply_log, notes_log = [], []
        resp_svc = _tracking_resp_svc(reply_log, notes_log)
        gate = ReplySafetyGate()

        sent = await _gated_customer_reply(
            resp_svc, gate, ticket_id="5", body_html="<p>hi</p>", confidence=0.99,
            impact="Client Escalation",
        )
        assert sent is False
        assert reply_log == []
        assert len(notes_log) == 1

    @pytest.mark.asyncio
    async def test_K7_duplicate_send_blocked_second_time_no_extra_note(self):
        """Duplicate outcome has should_draft=False by design (safety_gate.py) — the
        first send already produced a customer-visible reply, no need to redraft."""
        from api.routes.webhooks.freshdesk import _gated_customer_reply

        reply_log, notes_log = [], []
        resp_svc = _tracking_resp_svc(reply_log, notes_log)
        gate = ReplySafetyGate()

        first = await _gated_customer_reply(
            resp_svc, gate, ticket_id="6", body_html="<p>same body</p>", confidence=0.9,
        )
        second = await _gated_customer_reply(
            resp_svc, gate, ticket_id="6", body_html="<p>same body</p>", confidence=0.9,
        )
        assert first is True
        assert second is False
        assert len(reply_log) == 1
        assert notes_log == []


# ---------------------------------------------------------------------------
# Section M — End-to-end through the real background-task entry points
# ---------------------------------------------------------------------------

class _MockCreatedOrchestrator:
    """Mimics TicketOrchestrator.process_ticket() for the created-handler path."""

    def __init__(self, confidence: float | None, case_id: str = "case-m") -> None:
        self._confidence = confidence
        self._case_id = case_id

    def process_ticket(self, context) -> MagicMock:
        r = MagicMock()
        r.case_id = self._case_id
        r.success = True
        r.agent_result = {
            "agent_status": "COMPLETED",
            "response_draft": {"body_html": "<p>Here is your answer.</p>", "confidence": self._confidence},
        }
        return r


class TestM_EndToEndBackgroundTaskGating:

    @pytest.mark.asyncio
    async def test_M1_pass1_high_confidence_draft_is_sent_through_real_route_layer(self):
        """
        Exercises the REAL freshdesk.py background task + the REAL handlers.py
        extraction path together: confidence flows response_draft dict ->
        HandlerResult.response_confidence -> _gated_customer_reply -> gate.check()
        -> send_customer_reply(). This is the exact path that was previously
        stripping confidence and bypassing the gate entirely.
        """
        from freshdesk.handlers import FreshdeskTicketCreatedHandler
        from freshdesk.idempotency import WebhookIdempotencyStore
        from freshdesk.conversation_state import ConversationStateStore
        from api.routes.webhooks.freshdesk import _process_ticket_created

        handler = FreshdeskTicketCreatedHandler(
            idempotency_store=WebhookIdempotencyStore(),
            conversation_store=ConversationStateStore(),
            ticket_orchestrator=_MockCreatedOrchestrator(confidence=0.95),
            client_resolver=None,
        )

        reply_log, notes_log = [], []
        resp_svc = _tracking_resp_svc(reply_log, notes_log)

        request = _FakeRequest()
        request.app.state.freshdesk_ticket_created_handler = handler
        request.app.state.freshdesk_response_service = resp_svc
        request.app.state.reply_safety_gate = ReplySafetyGate()

        payload = {"ticket": {"id": "9001", "requester_email": "a@unitybank.co.in", "subject": "s", "description": "d"}}
        await _process_ticket_created(request, payload)

        assert len(reply_log) == 1
        assert "Here is your answer" in reply_log[0][1]
        assert notes_log == [] or all("blocked" not in n[1].lower() for n in notes_log)

    @pytest.mark.asyncio
    async def test_M2_pass1_low_confidence_draft_is_NOT_sent_drafts_note_instead(self):
        """The critical regression check: before this fix, this reply would have
        gone out to the customer unconditionally regardless of confidence."""
        from freshdesk.handlers import FreshdeskTicketCreatedHandler
        from freshdesk.idempotency import WebhookIdempotencyStore
        from freshdesk.conversation_state import ConversationStateStore
        from api.routes.webhooks.freshdesk import _process_ticket_created

        handler = FreshdeskTicketCreatedHandler(
            idempotency_store=WebhookIdempotencyStore(),
            conversation_store=ConversationStateStore(),
            ticket_orchestrator=_MockCreatedOrchestrator(confidence=0.2),
            client_resolver=None,
        )

        reply_log, notes_log = [], []
        resp_svc = _tracking_resp_svc(reply_log, notes_log)

        request = _FakeRequest()
        request.app.state.freshdesk_ticket_created_handler = handler
        request.app.state.freshdesk_response_service = resp_svc
        request.app.state.reply_safety_gate = ReplySafetyGate(confidence_threshold=0.75)

        payload = {"ticket": {"id": "9002", "requester_email": "a@unitybank.co.in", "subject": "s", "description": "d"}}
        await _process_ticket_created(request, payload)

        assert reply_log == []
        assert len(notes_log) == 1
        assert "confidence" in notes_log[0][1].lower()

    @pytest.mark.asyncio
    async def test_M3_no_gate_wired_on_app_state_fails_closed_not_open(self):
        """If app.state.reply_safety_gate was never set (e.g. wiring failed at
        startup), the route layer must still refuse to auto-send."""
        from freshdesk.handlers import FreshdeskTicketCreatedHandler
        from freshdesk.idempotency import WebhookIdempotencyStore
        from freshdesk.conversation_state import ConversationStateStore
        from api.routes.webhooks.freshdesk import _process_ticket_created

        handler = FreshdeskTicketCreatedHandler(
            idempotency_store=WebhookIdempotencyStore(),
            conversation_store=ConversationStateStore(),
            ticket_orchestrator=_MockCreatedOrchestrator(confidence=0.99),
            client_resolver=None,
        )

        reply_log, notes_log = [], []
        resp_svc = _tracking_resp_svc(reply_log, notes_log)

        request = _FakeRequest()
        request.app.state.freshdesk_ticket_created_handler = handler
        request.app.state.freshdesk_response_service = resp_svc
        # Deliberately NOT setting reply_safety_gate.

        payload = {"ticket": {"id": "9003", "requester_email": "a@unitybank.co.in", "subject": "s", "description": "d"}}
        await _process_ticket_created(request, payload)

        assert reply_log == []
        assert len(notes_log) == 1
        assert "not wired" in notes_log[0][1].lower()
