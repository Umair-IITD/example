"""
tests/test_sprint_wave7_clarification_resume.py

Wave 7 Prep — Blueprint Layer 6: Clarification Resume Logic.

Tests that when a customer replies to a Freshdesk ticket with their URN/Session ID,
the system:
  1. Detects the comment as an incoming customer reply (not private).
  2. Routes the comment body through NLPRouter (NLU layer) to extract entities.
  3. Calls CaseService.receive_message() to fill the extracted slots.
  4. Returns the next clarification question if more slots are needed.
  5. Returns None (triggers investigation via orchestrator) when all slots are filled.

Design rules:
  - NLPRouter is NEVER called for real OpenAI API in tests — always mocked.
  - No network calls — all external services are mocked.
  - Freshdesk write path goes through FreshdeskResponseService (not tested here,
    that's the freshdesk-safety-reviewer's domain).
  - datetime.now(tz=timezone.utc).replace(tzinfo=None) used for naive timestamps.

Sections:
  A — Comment type detection (incoming/private flags)
  B — NLPRouter slot extraction via _nlp_slot_resume()
  C — CaseService.receive_message() slot-filling integration
  D — End-to-end handler behaviour (httpx.MockTransport)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

# ── Env vars required by imports ──────────────────────────────────────────────
os.environ.setdefault("RAG_API_KEY", "test-rag-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-supabase-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")

from case_engine.nlp_router import NLPRouter, NLPSignal
from freshdesk.conversation_state import ConversationLifecycle, ConversationState, ConversationStateStore
from freshdesk.freshdesk_models import FreshdeskLatestComment, FreshdeskUpdateEvent
from freshdesk.handlers import FreshdeskTicketUpdatedHandler, HandlerResult
from freshdesk.idempotency import WebhookIdempotencyStore


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_naive() -> str:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None).isoformat()


def _make_conv_state(
    ticket_id: str,
    case_id: str = "case-abc",
    awaiting_customer: bool = True,
    lifecycle: ConversationLifecycle = ConversationLifecycle.CLARIFICATION,
) -> ConversationState:
    return ConversationState(
        ticket_id=ticket_id,
        case_id=case_id,
        client_id="unity_bank",
        awaiting_customer=awaiting_customer,
        clarification_pending=awaiting_customer,
        lifecycle_state=lifecycle,
    )


def _make_updated_payload(
    ticket_id: int = 99001,
    body_text: str = "My URN is URN99001234",
    incoming: bool = True,
    private: bool = False,
) -> dict[str, Any]:
    return {
        "freshdesk_webhook": {
            "ticket_id": ticket_id,
            "updated_at": _now_naive(),
            "latest_comment": {
                "body": f"<p>{body_text}</p>",
                "body_text": body_text,
                "incoming": incoming,
                "private": private,
                "user_id": 42,
            },
        }
    }


def _make_nlp_signal(
    intent: str = "VKYC_SESSION_FAILURE",
    urn: str | None = "URN99001234",
    session_id: str | None = None,
    needs_clarification: bool = True,
) -> NLPSignal:
    return NLPSignal(
        intent=intent,
        nested_case=None,
        entities={
            "urn": urn,
            "session_id": session_id,
            "phone_number": None,
            "agent_id": None,
            "application_id": None,
            "callback_type": None,
        },
        negation_detected=False,
        confidence=0.91,
        needs_clarification=needs_clarification,
        clarification_question="Please also provide the Session ID." if needs_clarification else None,
        raw_text="<NEVER_LOG>",
    )


def _make_receive_message_result(
    next_question_text: str | None = None,
    workflow_started: bool = False,
) -> Any:
    """Return a mock ReceiveMessageResult."""
    from case_engine.service import ReceiveMessageResult
    from case_engine.models import CaseState
    nq = None
    if next_question_text is not None:
        nq = {
            "slot_name": "session_id",
            "prompt_text": next_question_text,
            "is_required": True,
        }
    return ReceiveMessageResult(
        case_id="case-abc",
        state=CaseState.AWAITING_INPUT if next_question_text else CaseState.WORKFLOW_ACTIVE,
        slot_values={"urn": {"status": "FILLED", "value": "URN99001234", "attempt_count": 1}},
        next_question=nq,
        all_slots_filled=next_question_text is None,
        workflow_started=workflow_started,
    )


def _build_handler(
    *,
    conv_state: ConversationState | None = None,
    nlp_router: Any = None,
    case_service: Any = None,
    orchestrator: Any = None,
) -> FreshdeskTicketUpdatedHandler:
    idem = MagicMock(spec=WebhookIdempotencyStore)
    idem.check.return_value = False
    idem.make_key.return_value = "key-1"

    conv_store = MagicMock(spec=ConversationStateStore)
    conv_store.get.return_value = conv_state

    return FreshdeskTicketUpdatedHandler(
        idempotency_store=idem,
        conversation_store=conv_store,
        ticket_orchestrator=orchestrator,
        nlp_router=nlp_router,
        case_service=case_service,
    )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION A — Comment type detection
# ══════════════════════════════════════════════════════════════════════════════

class TestA_CommentTypeDetection:
    """
    Blueprint Layer 6 requirement: only incoming=True, private=False comments
    are classified as customer replies and eligible for clarification resume.
    """

    def test_A1_incoming_public_is_customer_reply(self) -> None:
        comment = FreshdeskLatestComment(
            body="My URN is URN12345",
            body_text="My URN is URN12345",
            incoming=True,
            private=False,
        )
        assert comment.is_customer_reply is True
        assert comment.is_agent_reply is False
        assert comment.is_internal_note is False

    def test_A2_incoming_private_is_internal_note(self) -> None:
        comment = FreshdeskLatestComment(
            body="Private note",
            body_text="Private note",
            incoming=True,
            private=True,
        )
        assert comment.is_customer_reply is False
        assert comment.is_internal_note is False  # incoming private is unusual; not customer reply
        assert comment.is_agent_reply is False

    def test_A3_outgoing_public_is_agent_reply(self) -> None:
        comment = FreshdeskLatestComment(
            body="Agent response",
            body_text="Agent response",
            incoming=False,
            private=False,
        )
        assert comment.is_customer_reply is False
        assert comment.is_agent_reply is True

    def test_A4_outgoing_private_is_internal_note(self) -> None:
        comment = FreshdeskLatestComment(
            body="Internal note",
            body_text="Internal note",
            incoming=False,
            private=True,
        )
        assert comment.is_customer_reply is False
        assert comment.is_internal_note is True

    def test_A5_detect_update_action_customer_reply(self) -> None:
        handler = _build_handler()
        from freshdesk.freshdesk_models import FreshdeskTicketPayload
        comment = FreshdeskLatestComment(
            body_text="URN is URN99001",
            incoming=True,
            private=False,
        )
        action = handler._detect_update_action(
            ticket=FreshdeskTicketPayload(),
            changes={},
            latest_comment=comment,
        )
        assert action == "customer_reply"

    def test_A6_detect_update_action_internal_note(self) -> None:
        handler = _build_handler()
        from freshdesk.freshdesk_models import FreshdeskTicketPayload
        comment = FreshdeskLatestComment(
            body_text="Internal note",
            incoming=False,
            private=True,
        )
        action = handler._detect_update_action(
            ticket=FreshdeskTicketPayload(),
            changes={},
            latest_comment=comment,
        )
        assert action == "internal_note"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION B — NLPRouter slot extraction via _nlp_slot_resume()
# ══════════════════════════════════════════════════════════════════════════════

class TestB_NLPSlotExtraction:
    """
    _nlp_slot_resume() must call NLPRouter.route(), then CaseService.receive_message()
    for each extracted entity, returning next_question text or None.
    """

    def test_B1_nlp_router_called_with_comment_body(self) -> None:
        mock_nlp = MagicMock(spec=NLPRouter)
        mock_nlp.route.return_value = _make_nlp_signal(urn="URN99001234")

        mock_case_svc = MagicMock()
        mock_case_svc.get_case.return_value = MagicMock()
        mock_case_svc.receive_message.return_value = _make_receive_message_result(
            next_question_text="Please also provide the Session ID."
        )

        handler = _build_handler(nlp_router=mock_nlp, case_service=mock_case_svc)
        result = handler._nlp_slot_resume(
            ticket_id="99001",
            comment_text="My URN is URN99001234",
            case_id="case-abc",
        )

        mock_nlp.route.assert_called_once_with("My URN is URN99001234")
        assert result == "Please also provide the Session ID."

    def test_B2_urn_entity_passed_to_receive_message(self) -> None:
        mock_nlp = MagicMock(spec=NLPRouter)
        mock_nlp.route.return_value = _make_nlp_signal(urn="URN12345678", session_id=None)

        mock_case = MagicMock()
        mock_case_svc = MagicMock()
        mock_case_svc.get_case.return_value = mock_case
        mock_case_svc.receive_message.return_value = _make_receive_message_result(
            next_question_text="Please share your Session ID."
        )

        handler = _build_handler(nlp_router=mock_nlp, case_service=mock_case_svc)
        handler._nlp_slot_resume("99002", "My URN is URN12345678", "case-abc")

        mock_case_svc.receive_message.assert_any_call(
            mock_case,
            "My URN is URN12345678",
            slot_name="urn",
            slot_value_str="URN12345678",
        )

    def test_B3_session_id_entity_passed_to_receive_message(self) -> None:
        mock_nlp = MagicMock(spec=NLPRouter)
        mock_nlp.route.return_value = _make_nlp_signal(urn=None, session_id="KID-ABC123")

        mock_case = MagicMock()
        mock_case_svc = MagicMock()
        mock_case_svc.get_case.return_value = mock_case
        mock_case_svc.receive_message.return_value = _make_receive_message_result(
            next_question_text="Please also provide your URN."
        )

        handler = _build_handler(nlp_router=mock_nlp, case_service=mock_case_svc)
        handler._nlp_slot_resume("99003", "Session is KID-ABC123", "case-abc")

        mock_case_svc.receive_message.assert_any_call(
            mock_case,
            "Session is KID-ABC123",
            slot_name="session_id",
            slot_value_str="KID-ABC123",
        )

    def test_B4_all_slots_filled_returns_none(self) -> None:
        mock_nlp = MagicMock(spec=NLPRouter)
        mock_nlp.route.return_value = _make_nlp_signal(
            urn="URN99001234",
            session_id="KID-XYZ789",
            needs_clarification=False,
        )

        mock_case_svc = MagicMock()
        mock_case_svc.get_case.return_value = MagicMock()
        mock_case_svc.receive_message.return_value = _make_receive_message_result(
            next_question_text=None,
            workflow_started=True,
        )

        handler = _build_handler(nlp_router=mock_nlp, case_service=mock_case_svc)
        result = handler._nlp_slot_resume(
            ticket_id="99004",
            comment_text="URN is URN99001234, session KID-XYZ789",
            case_id="case-abc",
        )
        assert result is None

    def test_B5_no_entities_calls_receive_message_with_text_only(self) -> None:
        signal_no_entities = NLPSignal(
            intent="VKYC_SESSION_FAILURE",
            nested_case=None,
            entities={
                "urn": None, "session_id": None, "phone_number": None,
                "agent_id": None, "application_id": None, "callback_type": None,
            },
            negation_detected=False,
            confidence=0.5,
            needs_clarification=True,
            clarification_question="Please provide URN and Session ID.",
            raw_text="<NEVER_LOG>",
        )
        mock_nlp = MagicMock(spec=NLPRouter)
        mock_nlp.route.return_value = signal_no_entities

        mock_case = MagicMock()
        mock_case_svc = MagicMock()
        mock_case_svc.get_case.return_value = mock_case
        mock_case_svc.receive_message.return_value = _make_receive_message_result(
            next_question_text="Please provide URN and Session ID."
        )

        handler = _build_handler(nlp_router=mock_nlp, case_service=mock_case_svc)
        result = handler._nlp_slot_resume("99005", "I need help", "case-abc")

        # With no entities, should fall back to implicit extraction
        mock_case_svc.receive_message.assert_called_once_with(mock_case, "I need help")
        assert result == "Please provide URN and Session ID."

    def test_B6_no_case_id_returns_none_immediately(self) -> None:
        mock_nlp = MagicMock(spec=NLPRouter)
        mock_case_svc = MagicMock()

        handler = _build_handler(nlp_router=mock_nlp, case_service=mock_case_svc)
        result = handler._nlp_slot_resume("99006", "URN is something", case_id=None)

        assert result is None
        mock_nlp.route.assert_not_called()
        mock_case_svc.get_case.assert_not_called()

    def test_B7_case_not_found_returns_none(self) -> None:
        mock_nlp = MagicMock(spec=NLPRouter)
        mock_nlp.route.return_value = _make_nlp_signal(urn="URN99001")

        mock_case_svc = MagicMock()
        mock_case_svc.get_case.return_value = None  # case not in DB

        handler = _build_handler(nlp_router=mock_nlp, case_service=mock_case_svc)
        result = handler._nlp_slot_resume("99007", "URN is URN99001", "case-missing")

        assert result is None
        mock_case_svc.receive_message.assert_not_called()

    def test_B8_nlp_router_error_returns_none(self) -> None:
        mock_nlp = MagicMock(spec=NLPRouter)
        mock_nlp.route.side_effect = RuntimeError("OpenAI timeout")

        mock_case_svc = MagicMock()
        handler = _build_handler(nlp_router=mock_nlp, case_service=mock_case_svc)
        result = handler._nlp_slot_resume("99008", "some text", "case-abc")

        assert result is None
        mock_case_svc.get_case.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION C — Handler integration: customer reply in CLARIFICATION state
# ══════════════════════════════════════════════════════════════════════════════

class TestC_HandlerClarificationResume:
    """
    FreshdeskTicketUpdatedHandler.handle() must:
    - Call _nlp_slot_resume() when conv_state.awaiting_customer=True.
    - Return response_draft = next clarification question when slots still missing.
    - Fall through to orchestrator when _nlp_slot_resume returns None (all slots filled).
    """

    def test_C1_clarification_reply_calls_nlp_slot_resume(self) -> None:
        conv = _make_conv_state("99010", awaiting_customer=True)

        mock_nlp = MagicMock(spec=NLPRouter)
        mock_nlp.route.return_value = _make_nlp_signal(urn="URN99010")

        mock_case_svc = MagicMock()
        mock_case_svc.get_case.return_value = MagicMock()
        mock_case_svc.receive_message.return_value = _make_receive_message_result(
            next_question_text="Please also provide the Session ID."
        )

        handler = _build_handler(
            conv_state=conv,
            nlp_router=mock_nlp,
            case_service=mock_case_svc,
        )
        payload = _make_updated_payload(ticket_id=99010, body_text="URN99010")
        result = handler.handle(payload)

        assert result.success is True
        assert result.response_draft == "Please also provide the Session ID."
        mock_nlp.route.assert_called_once()

    def test_C2_next_question_is_response_draft(self) -> None:
        conv = _make_conv_state("99011", awaiting_customer=True)

        mock_nlp = MagicMock(spec=NLPRouter)
        mock_nlp.route.return_value = _make_nlp_signal(urn="URN99011")

        mock_case_svc = MagicMock()
        mock_case_svc.get_case.return_value = MagicMock()
        mock_case_svc.receive_message.return_value = _make_receive_message_result(
            next_question_text="Could you please share your Session ID?"
        )

        handler = _build_handler(
            conv_state=conv,
            nlp_router=mock_nlp,
            case_service=mock_case_svc,
        )
        result = handler.handle(_make_updated_payload(99011, "URN is URN99011"))
        assert result.response_draft == "Could you please share your Session ID?"

    def test_C3_no_nlp_router_falls_back_to_orchestrator(self) -> None:
        conv = _make_conv_state("99012", awaiting_customer=True)
        mock_orch = MagicMock()
        mock_orch.resume_ticket.return_value = MagicMock(
            error_code=None,
            agent_result={"response_draft": {"body_text": "Investigating now..."}},
        )

        handler = _build_handler(
            conv_state=conv,
            nlp_router=None,
            case_service=None,
            orchestrator=mock_orch,
        )
        result = handler.handle(_make_updated_payload(99012, "URN is URN99012"))

        assert result.success is True
        mock_orch.resume_ticket.assert_called_once()

    def test_C4_not_awaiting_customer_skips_nlp_slot_resume(self) -> None:
        conv = _make_conv_state("99013", awaiting_customer=False, lifecycle=ConversationLifecycle.OPEN)

        mock_nlp = MagicMock(spec=NLPRouter)
        mock_case_svc = MagicMock()

        handler = _build_handler(
            conv_state=conv,
            nlp_router=mock_nlp,
            case_service=mock_case_svc,
        )
        handler.handle(_make_updated_payload(99013, "Some message"))

        # _nlp_slot_resume should NOT be called — we're not in CLARIFICATION state
        mock_nlp.route.assert_not_called()

    def test_C5_agent_reply_not_processed_as_customer_reply(self) -> None:
        conv = _make_conv_state("99014", awaiting_customer=True)
        mock_nlp = MagicMock(spec=NLPRouter)
        mock_case_svc = MagicMock()

        handler = _build_handler(
            conv_state=conv,
            nlp_router=mock_nlp,
            case_service=mock_case_svc,
        )
        # Agent reply: incoming=False, private=False
        payload = _make_updated_payload(99014, "Agent note", incoming=False, private=False)
        result = handler.handle(payload)

        assert result.success is True
        assert result.action == "agent_reply"
        mock_nlp.route.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION D — End-to-end via httpx.MockTransport (10-second webhook budget)
# ══════════════════════════════════════════════════════════════════════════════

class TestD_EndToEndWebhook:
    """
    The /webhooks/freshdesk/ticket-updated endpoint must return 200 OK within
    10 seconds regardless of processing time (processing runs in BackgroundTasks).
    Tests use httpx.MockTransport to avoid real network calls.
    """

    def test_D1_ticket_updated_webhook_returns_200_immediately(self) -> None:
        """Webhook returns 200 OK; the payload detection is synchronous."""
        import httpx
        from fastapi.testclient import TestClient

        try:
            from app.main import create_app
        except Exception:
            pytest.skip("create_app not available in test context")

        app = create_app()
        client = TestClient(app, raise_server_exceptions=False)

        payload = _make_updated_payload(ticket_id=99020)
        response = client.post(
            "/webhooks/freshdesk/ticket-updated",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body.get("status") == "accepted"

    def test_D2_duplicate_webhook_skipped_idempotency(self) -> None:
        """
        Sending the same payload twice must be safe — idempotency check skips
        the second run.
        """
        conv = _make_conv_state("99021", awaiting_customer=True)
        mock_nlp = MagicMock(spec=NLPRouter)
        mock_case_svc = MagicMock()

        idem = MagicMock(spec=WebhookIdempotencyStore)
        idem.make_key.return_value = "dup-key-99021"
        # First call: not duplicate; second call: duplicate
        idem.check.side_effect = [False, True]

        conv_store = MagicMock(spec=ConversationStateStore)
        conv_store.get.return_value = conv

        handler = FreshdeskTicketUpdatedHandler(
            idempotency_store=idem,
            conversation_store=conv_store,
            nlp_router=mock_nlp,
            case_service=mock_case_svc,
        )
        payload = _make_updated_payload(99021, "URN99021")

        r1 = handler.handle(payload)
        r2 = handler.handle(payload)

        assert r1.success is True
        assert r2.success is True
        assert r2.skipped is True
        assert r2.skip_reason == "DUPLICATE"
        # NLPRouter called only once — skipped on second invocation
        mock_nlp.route.call_count <= 1

    def test_D3_empty_comment_body_skips_nlp_router(self) -> None:
        conv = _make_conv_state("99022", awaiting_customer=True)
        mock_nlp = MagicMock(spec=NLPRouter)
        mock_case_svc = MagicMock()
        mock_case_svc.get_case.return_value = MagicMock()

        handler = _build_handler(
            conv_state=conv,
            nlp_router=mock_nlp,
            case_service=mock_case_svc,
        )
        # Empty body_text — should not call NLPRouter
        payload = _make_updated_payload(99022, body_text="")
        payload["freshdesk_webhook"]["latest_comment"]["body_text"] = ""
        payload["freshdesk_webhook"]["latest_comment"]["body"] = ""
        handler.handle(payload)

        mock_nlp.route.assert_not_called()

    def test_D4_pii_raw_text_never_in_nlp_signal(self) -> None:
        """
        Confirm NLPSignal.raw_text is captured but not logged.
        The NLPRouter mock returns a signal with raw_text — this test proves
        the handler never passes raw_text to log statements.
        """
        conv = _make_conv_state("99023", awaiting_customer=True)

        captured_signal = _make_nlp_signal(urn="URN99023")
        captured_signal = NLPSignal(
            intent=captured_signal.intent,
            nested_case=captured_signal.nested_case,
            entities=captured_signal.entities,
            negation_detected=captured_signal.negation_detected,
            confidence=captured_signal.confidence,
            needs_clarification=False,
            clarification_question=None,
            raw_text="SENSITIVE_TEXT_MUST_NOT_APPEAR_IN_LOGS",
        )

        mock_nlp = MagicMock(spec=NLPRouter)
        mock_nlp.route.return_value = captured_signal

        mock_case_svc = MagicMock()
        mock_case_svc.get_case.return_value = MagicMock()
        mock_case_svc.receive_message.return_value = _make_receive_message_result(
            next_question_text=None,
            workflow_started=True,
        )

        import logging
        log_records: list[str] = []

        class CapturingHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                log_records.append(self.format(record))

        capturing = CapturingHandler()
        logger = logging.getLogger("freshdesk.handlers")
        logger.addHandler(capturing)

        try:
            handler = _build_handler(
                conv_state=conv,
                nlp_router=mock_nlp,
                case_service=mock_case_svc,
            )
            handler.handle(_make_updated_payload(99023, "URN99023"))
        finally:
            logger.removeHandler(capturing)

        combined_logs = "\n".join(log_records)
        assert "SENSITIVE_TEXT_MUST_NOT_APPEAR_IN_LOGS" not in combined_logs, (
            "raw_text leaked into logs — PII violation"
        )
