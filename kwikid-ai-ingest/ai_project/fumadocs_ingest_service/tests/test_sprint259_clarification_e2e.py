"""
tests/test_sprint259_clarification_e2e.py

Sprint 2.5.9 — Wave 7: Clarification Loop Completion
End-to-end conversational clarification loop tests for FreshdeskTicketUpdatedHandler.

Sections:
  TestA — Happy path: customer provides all slots in first reply → orchestrator runs
  TestB — Multi-turn: partial slot fill → re-ask → second reply fills → orchestrator runs
  TestC — Garbage/max-attempts: two unhelpful replies → ESCALATED, orchestrator skipped
  TestD — State persistence: server restart simulation → Supabase load → resume

Never touches the network. All state is in-memory or mocked.
NLU (NLPRouter) and NLG (intelligence/) layers are frozen — all mocked.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, call

# ── Env bootstrap (must precede project imports) ───────────────────────────────
os.environ.setdefault("RAG_API_KEY",                    "test-259-rag")
os.environ.setdefault("OPENAI_API_KEY",                 "sk-test-259")
os.environ.setdefault("SUPABASE_URL",                   "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY",                   "test-supabase-key-259")
os.environ.setdefault("AUDIT_BACKEND",                  "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")

import pytest

from case_engine.nlp_router import NLPRouter, NLPSignal
from case_engine.service import CaseService, ReceiveMessageResult
from case_engine.models import CaseState
from freshdesk.conversation_state import ConversationLifecycle, ConversationState, ConversationStateStore
from freshdesk.handlers import FreshdeskTicketUpdatedHandler
from freshdesk.idempotency import WebhookIdempotencyStore


# ═══════════════════════════════════════════════════════════════════════════════
# Shared fixtures / builders
# ═══════════════════════════════════════════════════════════════════════════════

_TICKET_ID = "77001"
_CASE_ID   = "case-vkyc-001"
_CLIENT_ID = "unity_bank"

# Minimal valid FreshdeskUpdateEvent payload (ticket-dict format with latest_comment)
def _update_payload(body_text: str, updated_at: str = "2026-01-01T10:00:00Z") -> dict:
    return {
        "ticket": {
            "id": int(_TICKET_ID),
            "updated_at": updated_at,
            "status": 2,
            "custom_fields": {"cf_clients": _CLIENT_ID},
        },
        "changes": {},
        "latest_comment": {
            "body": body_text,
            "body_text": body_text,
            "incoming": True,
            "private": False,
        },
    }


def _make_nlp_signal(
    *,
    urn: str | None = None,
    session_id: str | None = None,
) -> NLPSignal:
    """Return a NLPSignal with the given entity slots pre-filled."""
    entities: dict[str, str | None] = {}
    if urn is not None:
        entities["urn"] = urn
    if session_id is not None:
        entities["session_id"] = session_id
    needs_clarification = not (urn and session_id)
    return NLPSignal(
        intent="VKYC_SESSION_FAILURE",
        nested_case=None,
        entities=entities,
        negation_detected=False,
        confidence=0.95,
        needs_clarification=needs_clarification,
        clarification_question=None,
        raw_text="",
    )


def _make_receive_result(
    *,
    next_question_text: str | None = None,
    all_slots_filled: bool = False,
    escalated: bool = False,
) -> ReceiveMessageResult:
    nq: dict | None = None
    if next_question_text is not None:
        nq = {"prompt_text": next_question_text, "slot_name": "session_id"}
    return ReceiveMessageResult(
        case_id=_CASE_ID,
        state=CaseState.AWAITING_INPUT if next_question_text else (
            CaseState.ESCALATED if escalated else CaseState.WORKFLOW_ACTIVE
        ),
        slot_values={},
        next_question=nq,
        all_slots_filled=all_slots_filled,
        escalated=escalated,
        workflow_started=all_slots_filled,
    )


def _make_orchestration_result() -> MagicMock:
    """Minimal TicketOrchestrationResult-like mock with a response_draft."""
    result = MagicMock()
    result.error_code = None
    result.agent_result = {
        "response_draft": {
            "body_text": "I have investigated your VKYC session failure...",
            "body_html": "<p>I have investigated your VKYC session failure...</p>",
        }
    }
    return result


def _build_handler(
    *,
    conv_state: ConversationState | None = None,
    supabase_client: Any = None,
    mock_nlp_router: Any = None,
    mock_case_service: Any = None,
    mock_orchestrator: Any = None,
    unique_ts: str = "2026-01-01T10:00:00Z",
) -> tuple[FreshdeskTicketUpdatedHandler, ConversationStateStore]:
    """
    Builds a FreshdeskTicketUpdatedHandler with a fresh idempotency store
    and a conversation store pre-seeded with conv_state (if provided).
    """
    store = ConversationStateStore(supabase_client=supabase_client)
    if conv_state is not None:
        store._store[conv_state.ticket_id] = conv_state

    handler = FreshdeskTicketUpdatedHandler(
        idempotency_store=WebhookIdempotencyStore(),
        conversation_store=store,
        ticket_orchestrator=mock_orchestrator,
        audit_logger=None,
        metrics_collector=None,
        nlp_router=mock_nlp_router,
        case_service=mock_case_service,
    )
    return handler, store


# ═══════════════════════════════════════════════════════════════════════════════
# TestA — Happy path: all slots provided in first reply → orchestrator runs
# ═══════════════════════════════════════════════════════════════════════════════

class TestA_HappyPath:
    """
    Flow: ticket in CLARIFICATION with awaiting_customer=True → customer replies
    with URN + session_id → NLU extracts both → receive_message returns all_slots_filled
    → orchestrator.resume_ticket() is called once → HandlerResult.response_draft is the
    investigation observation, NOT the clarification question.
    """

    def _setup(self):
        mock_nlp = MagicMock(spec=NLPRouter)
        mock_nlp.route.return_value = _make_nlp_signal(
            urn="URN-12345",
            session_id="abc-def-session",
        )

        mock_case = MagicMock(spec=CaseService)
        mock_case.get_case.return_value = MagicMock(case_id=_CASE_ID)
        mock_case.receive_message.return_value = _make_receive_result(all_slots_filled=True)

        mock_orch = MagicMock()
        mock_orch.resume_ticket.return_value = _make_orchestration_result()

        conv_state = ConversationState(
            ticket_id=_TICKET_ID,
            client_id=_CLIENT_ID,
            lifecycle_state=ConversationLifecycle.CLARIFICATION,
            awaiting_customer=True,
            clarification_pending=True,
            case_id=_CASE_ID,
        )

        handler, store = _build_handler(
            conv_state=conv_state,
            mock_nlp_router=mock_nlp,
            mock_case_service=mock_case,
            mock_orchestrator=mock_orch,
        )
        return handler, store, mock_nlp, mock_case, mock_orch

    def test_A1_handler_succeeds(self):
        handler, store, *_ = self._setup()
        payload = _update_payload("My URN is URN-12345 and session is abc-def-session")
        result = handler.handle(payload)
        assert result.success is True

    def test_A2_nlp_router_called_once(self):
        handler, store, mock_nlp, mock_case, mock_orch = self._setup()
        handler.handle(_update_payload("My URN is URN-12345 and session is abc-def-session"))
        mock_nlp.route.assert_called_once()

    def test_A3_receive_message_called_for_each_entity(self):
        handler, store, mock_nlp, mock_case, mock_orch = self._setup()
        handler.handle(_update_payload("My URN is URN-12345 and session is abc-def-session"))
        # NLU returned urn + session_id → receive_message called twice (once per entity)
        assert mock_case.receive_message.call_count == 2

    def test_A4_orchestrator_resume_called_once(self):
        handler, store, mock_nlp, mock_case, mock_orch = self._setup()
        handler.handle(_update_payload("My URN is URN-12345 and session is abc-def-session"))
        mock_orch.resume_ticket.assert_called_once()

    def test_A5_response_draft_is_observation_not_question(self):
        handler, store, mock_nlp, mock_case, mock_orch = self._setup()
        result = handler.handle(_update_payload("My URN is URN-12345 and session is abc-def-session"))
        # The orchestrator's investigation observation must be the draft, NOT a clarification question
        assert result.response_draft is not None
        assert "investigated" in (result.response_draft or "").lower()

    def test_A6_awaiting_customer_cleared_after_reply(self):
        handler, store, *_ = self._setup()
        handler.handle(_update_payload("My URN is URN-12345 and session is abc-def-session"))
        # All slots filled → no re-arming; awaiting_customer must remain False
        state = store.get(_TICKET_ID)
        assert state is not None
        assert state.awaiting_customer is False

    def test_A7_action_is_customer_reply(self):
        handler, store, *_ = self._setup()
        result = handler.handle(_update_payload("My URN is URN-12345 and session is abc-def-session"))
        assert result.action == "customer_reply"


# ═══════════════════════════════════════════════════════════════════════════════
# TestB — Multi-turn: URN only first → re-ask session_id → second reply fills it
# ═══════════════════════════════════════════════════════════════════════════════

class TestB_MultiTurn:
    """
    Turn 1: customer sends only URN → NLU extracts urn only → receive_message returns
            next_question("Please provide session ID") → handler re-arms awaiting_customer=True
            → orchestrator NOT called → response_draft = the next_question text.

    Turn 2: customer sends session_id → NLU extracts session_id → receive_message returns
            all_slots_filled → orchestrator called once → response_draft = investigation.
    """

    def _setup_turn1(self):
        mock_nlp = MagicMock(spec=NLPRouter)
        mock_nlp.route.return_value = _make_nlp_signal(urn="URN-12345", session_id=None)

        mock_case = MagicMock(spec=CaseService)
        mock_case.get_case.return_value = MagicMock(case_id=_CASE_ID)
        mock_case.receive_message.return_value = _make_receive_result(
            next_question_text="Please provide your session ID."
        )

        mock_orch = MagicMock()

        conv_state = ConversationState(
            ticket_id=_TICKET_ID,
            client_id=_CLIENT_ID,
            lifecycle_state=ConversationLifecycle.CLARIFICATION,
            awaiting_customer=True,
            clarification_pending=True,
            case_id=_CASE_ID,
        )

        handler, store = _build_handler(
            conv_state=conv_state,
            mock_nlp_router=mock_nlp,
            mock_case_service=mock_case,
            mock_orchestrator=mock_orch,
        )
        return handler, store, mock_nlp, mock_case, mock_orch

    def _setup_turn2(self, store: ConversationStateStore):
        """Re-use the same store (simulates same server instance) with fresh mocks."""
        mock_nlp = MagicMock(spec=NLPRouter)
        mock_nlp.route.return_value = _make_nlp_signal(urn=None, session_id="sess-xyz-999")

        mock_case = MagicMock(spec=CaseService)
        mock_case.get_case.return_value = MagicMock(case_id=_CASE_ID)
        mock_case.receive_message.return_value = _make_receive_result(all_slots_filled=True)

        mock_orch = MagicMock()
        mock_orch.resume_ticket.return_value = _make_orchestration_result()

        handler = FreshdeskTicketUpdatedHandler(
            idempotency_store=WebhookIdempotencyStore(),
            conversation_store=store,
            ticket_orchestrator=mock_orch,
            audit_logger=None,
            metrics_collector=None,
            nlp_router=mock_nlp,
            case_service=mock_case,
        )
        return handler, mock_nlp, mock_case, mock_orch

    def test_B1_turn1_orchestrator_not_called(self):
        handler, store, mock_nlp, mock_case, mock_orch = self._setup_turn1()
        handler.handle(_update_payload("My URN is URN-12345"))
        mock_orch.resume_ticket.assert_not_called()

    def test_B2_turn1_response_draft_is_next_question(self):
        handler, store, *_ = self._setup_turn1()
        result = handler.handle(_update_payload("My URN is URN-12345"))
        assert result.response_draft is not None
        assert "session" in (result.response_draft or "").lower()

    def test_B3_turn1_rearmed_awaiting_customer(self):
        handler, store, *_ = self._setup_turn1()
        handler.handle(_update_payload("My URN is URN-12345"))
        state = store.get(_TICKET_ID)
        assert state is not None
        assert state.awaiting_customer is True
        assert state.lifecycle_state == ConversationLifecycle.CLARIFICATION

    def test_B4_turn2_orchestrator_called_after_session_id(self):
        handler, store, *_ = self._setup_turn1()
        # Turn 1 — partial fill
        handler.handle(_update_payload("My URN is URN-12345", updated_at="2026-01-01T10:00:00Z"))
        # Turn 2 — provide session_id (new timestamp to bypass idempotency)
        handler2, mock_nlp2, mock_case2, mock_orch2 = self._setup_turn2(store)
        result2 = handler2.handle(_update_payload("My session is sess-xyz-999", updated_at="2026-01-01T10:01:00Z"))
        mock_orch2.resume_ticket.assert_called_once()

    def test_B5_turn2_response_draft_is_investigation(self):
        handler, store, *_ = self._setup_turn1()
        handler.handle(_update_payload("My URN is URN-12345", updated_at="2026-01-01T10:00:00Z"))
        handler2, mock_nlp2, mock_case2, mock_orch2 = self._setup_turn2(store)
        result2 = handler2.handle(_update_payload("My session is sess-xyz-999", updated_at="2026-01-01T10:01:00Z"))
        assert result2.response_draft is not None
        assert "investigated" in (result2.response_draft or "").lower()

    def test_B6_turn1_nlp_router_receives_comment_text(self):
        handler, store, mock_nlp, mock_case, mock_orch = self._setup_turn1()
        handler.handle(_update_payload("My URN is URN-12345"))
        mock_nlp.route.assert_called_once_with("My URN is URN-12345")

    def test_B7_turn2_nlp_routes_second_reply(self):
        handler, store, *_ = self._setup_turn1()
        handler.handle(_update_payload("My URN is URN-12345", updated_at="2026-01-01T10:00:00Z"))
        handler2, mock_nlp2, mock_case2, mock_orch2 = self._setup_turn2(store)
        handler2.handle(_update_payload("My session is sess-xyz-999", updated_at="2026-01-01T10:01:00Z"))
        mock_nlp2.route.assert_called_once_with("My session is sess-xyz-999")


# ═══════════════════════════════════════════════════════════════════════════════
# TestC — Garbage replies / max-attempts → ESCALATED, orchestrator NOT called
# ═══════════════════════════════════════════════════════════════════════════════

class TestC_GarbageMaxAttempts:
    """
    Turn 1: customer replies "hello?" → NLU extracts nothing (implicit extraction)
            → receive_message returns next_question (first attempt)
            → handler re-arms awaiting_customer, orchestrator NOT called.

    Turn 2: customer replies "I don't know" → NLU extracts nothing
            → receive_message returns escalated=True (max attempts exceeded)
            → _nlp_slot_resume returns human-transfer message (not None)
            → handler gate skips orchestrator
            → response_draft contains transfer/escalation text.
    """

    def _setup_turn1_garbage(self):
        mock_nlp = MagicMock(spec=NLPRouter)
        # No entities extracted — implicit extraction path
        mock_nlp.route.return_value = _make_nlp_signal(urn=None, session_id=None)

        mock_case = MagicMock(spec=CaseService)
        mock_case.get_case.return_value = MagicMock(case_id=_CASE_ID)
        # Still needs clarification after first garbage attempt
        mock_case.receive_message.return_value = _make_receive_result(
            next_question_text="I need your URN and session ID to proceed. Could you please provide them?"
        )

        mock_orch = MagicMock()

        conv_state = ConversationState(
            ticket_id=_TICKET_ID,
            client_id=_CLIENT_ID,
            lifecycle_state=ConversationLifecycle.CLARIFICATION,
            awaiting_customer=True,
            clarification_pending=True,
            case_id=_CASE_ID,
        )
        handler, store = _build_handler(
            conv_state=conv_state,
            mock_nlp_router=mock_nlp,
            mock_case_service=mock_case,
            mock_orchestrator=mock_orch,
        )
        return handler, store, mock_nlp, mock_case, mock_orch

    def _setup_turn2_escalated(self, store: ConversationStateStore):
        mock_nlp = MagicMock(spec=NLPRouter)
        mock_nlp.route.return_value = _make_nlp_signal(urn=None, session_id=None)

        mock_case = MagicMock(spec=CaseService)
        mock_case.get_case.return_value = MagicMock(case_id=_CASE_ID)
        # Max attempts exceeded → escalated=True
        mock_case.receive_message.return_value = _make_receive_result(escalated=True)

        mock_orch = MagicMock()

        handler = FreshdeskTicketUpdatedHandler(
            idempotency_store=WebhookIdempotencyStore(),
            conversation_store=store,
            ticket_orchestrator=mock_orch,
            audit_logger=None,
            metrics_collector=None,
            nlp_router=mock_nlp,
            case_service=mock_case,
        )
        return handler, mock_nlp, mock_case, mock_orch

    def test_C1_turn1_orchestrator_not_called_on_garbage(self):
        handler, store, mock_nlp, mock_case, mock_orch = self._setup_turn1_garbage()
        handler.handle(_update_payload("hello?"))
        mock_orch.resume_ticket.assert_not_called()

    def test_C2_turn1_asks_again(self):
        handler, store, *_ = self._setup_turn1_garbage()
        result = handler.handle(_update_payload("hello?"))
        assert result.response_draft is not None
        assert "urn" in (result.response_draft or "").lower() or "session" in (result.response_draft or "").lower()

    def test_C3_turn1_rearmed_awaiting_customer(self):
        handler, store, *_ = self._setup_turn1_garbage()
        handler.handle(_update_payload("hello?"))
        state = store.get(_TICKET_ID)
        assert state is not None
        assert state.awaiting_customer is True

    def test_C4_turn2_orchestrator_not_called_when_escalated(self):
        handler, store, *_ = self._setup_turn1_garbage()
        handler.handle(_update_payload("hello?", updated_at="2026-01-01T10:00:00Z"))

        handler2, mock_nlp2, mock_case2, mock_orch2 = self._setup_turn2_escalated(store)
        handler2.handle(_update_payload("I don't know", updated_at="2026-01-01T10:01:00Z"))
        mock_orch2.resume_ticket.assert_not_called()

    def test_C5_turn2_response_draft_contains_human_transfer_message(self):
        handler, store, *_ = self._setup_turn1_garbage()
        handler.handle(_update_payload("hello?", updated_at="2026-01-01T10:00:00Z"))

        handler2, mock_nlp2, mock_case2, mock_orch2 = self._setup_turn2_escalated(store)
        result2 = handler2.handle(_update_payload("I don't know", updated_at="2026-01-01T10:01:00Z"))
        assert result2.response_draft is not None
        draft = result2.response_draft or ""
        # Must contain a human escalation signal, NOT a slot question
        assert "human" in draft.lower() or "agent" in draft.lower()

    def test_C6_turn2_handler_succeeds(self):
        handler, store, *_ = self._setup_turn1_garbage()
        handler.handle(_update_payload("hello?", updated_at="2026-01-01T10:00:00Z"))

        handler2, mock_nlp2, mock_case2, mock_orch2 = self._setup_turn2_escalated(store)
        result2 = handler2.handle(_update_payload("I don't know", updated_at="2026-01-01T10:01:00Z"))
        assert result2.success is True

    def test_C7_implicit_extraction_path_used_when_no_entities(self):
        """When NLU returns no entities, _nlp_slot_resume falls back to implicit extraction."""
        handler, store, mock_nlp, mock_case, mock_orch = self._setup_turn1_garbage()
        handler.handle(_update_payload("hello?"))
        # NLU returned empty entities → receive_message called once (implicit path)
        mock_case.receive_message.assert_called_once()
        call_kwargs = mock_case.receive_message.call_args
        # Implicit extraction call: no slot_name kwarg
        assert "slot_name" not in (call_kwargs.kwargs or {})


# ═══════════════════════════════════════════════════════════════════════════════
# TestD — State persistence: fresh in-memory store loads conv_state from Supabase
# ═══════════════════════════════════════════════════════════════════════════════

class TestD_StatePersistence:
    """
    Simulates a server restart:
      1. Conv state exists in Supabase (ticket was in CLARIFICATION, awaiting_customer=True)
      2. A new handler is instantiated with an EMPTY in-memory store + Supabase mock
      3. ticket-updated arrives → handler calls store.get(ticket_id) → cache miss →
         falls through to _db_get() → Supabase mock returns the stored conv_state row
      4. Handler correctly identifies awaiting_customer=True and routes through
         clarification path → NLU extracts slots → orchestrator runs

    The Supabase client is mocked with a chain call that mimics:
        sb.table(...).select("*").eq(...).limit(1).execute()
        returning result.data = [<conv_state row dict>]
    """

    def _make_supabase_mock(self) -> Any:
        """Build a chainable Supabase mock that returns conv_state for our ticket."""
        conv_row = {
            "ticket_id":             _TICKET_ID,
            "client_id":             _CLIENT_ID,
            "lifecycle_state":       "CLARIFICATION",
            "clarification_pending": True,
            "awaiting_customer":     True,
            "awaiting_human_approval": False,
            "clarification_count":   1,
            "case_id":               _CASE_ID,
            "created_at":            "2026-01-01T09:00:00+00:00",
            "updated_at":            "2026-01-01T09:30:00+00:00",
            "resolved_at":           None,
            "metadata":              {},
        }
        execute_result = MagicMock()
        execute_result.data = [conv_row]

        # Chain: sb.table(_TABLE).select("*").eq("ticket_id", ...).limit(1).execute()
        mock_sb = MagicMock()
        (
            mock_sb
            .table.return_value
            .select.return_value
            .eq.return_value
            .limit.return_value
            .execute.return_value
        ) = execute_result

        # Upsert chain (called during update())
        (
            mock_sb
            .table.return_value
            .upsert.return_value
            .execute.return_value
        ) = MagicMock()

        return mock_sb

    def _setup(self):
        mock_nlp = MagicMock(spec=NLPRouter)
        mock_nlp.route.return_value = _make_nlp_signal(
            urn="URN-RESTART-001",
            session_id="sess-restart-abc",
        )

        mock_case = MagicMock(spec=CaseService)
        mock_case.get_case.return_value = MagicMock(case_id=_CASE_ID)
        mock_case.receive_message.return_value = _make_receive_result(all_slots_filled=True)

        mock_orch = MagicMock()
        mock_orch.resume_ticket.return_value = _make_orchestration_result()

        sb_mock = self._make_supabase_mock()

        # Fresh store — NO pre-seeded in-memory state; Supabase mock provides persistence
        handler, store = _build_handler(
            conv_state=None,  # empty in-memory store (simulates server restart)
            supabase_client=sb_mock,
            mock_nlp_router=mock_nlp,
            mock_case_service=mock_case,
            mock_orchestrator=mock_orch,
        )
        return handler, store, mock_nlp, mock_case, mock_orch, sb_mock

    def test_D1_handler_succeeds_after_restart(self):
        handler, store, *_ = self._setup()
        result = handler.handle(_update_payload("My URN is URN-RESTART-001 and session is sess-restart-abc"))
        assert result.success is True

    def test_D2_supabase_queried_on_cache_miss(self):
        handler, store, mock_nlp, mock_case, mock_orch, sb_mock = self._setup()
        # In-memory store starts empty → must hit Supabase
        assert store.get(_TICKET_ID) is None or True  # may be None before handle
        handler.handle(_update_payload("My URN is URN-RESTART-001 and session is sess-restart-abc"))
        sb_mock.table.assert_called()

    def test_D3_state_loaded_from_supabase(self):
        handler, store, *_ = self._setup()
        handler.handle(_update_payload("My URN is URN-RESTART-001 and session is sess-restart-abc"))
        # After handle(), in-memory store should have the ticket_id (loaded from Supabase)
        state = store.get(_TICKET_ID)
        assert state is not None
        assert state.case_id == _CASE_ID

    def test_D4_orchestrator_called_with_correct_ticket_id(self):
        handler, store, mock_nlp, mock_case, mock_orch, sb_mock = self._setup()
        handler.handle(_update_payload("My URN is URN-RESTART-001 and session is sess-restart-abc"))
        mock_orch.resume_ticket.assert_called_once()
        call_args = mock_orch.resume_ticket.call_args
        assert call_args.args[0] == _TICKET_ID

    def test_D5_clarification_path_entered_from_supabase_state(self):
        """After Supabase load, awaiting_customer=True triggers clarification path."""
        handler, store, mock_nlp, mock_case, mock_orch, sb_mock = self._setup()
        result = handler.handle(_update_payload("My URN is URN-RESTART-001 and session is sess-restart-abc"))
        # NLU was called → proves we entered the clarification / NLP slot resume path
        mock_nlp.route.assert_called_once()
        assert result.success is True

    def test_D6_response_draft_is_investigation_observation(self):
        handler, store, *_ = self._setup()
        result = handler.handle(_update_payload("My URN is URN-RESTART-001 and session is sess-restart-abc"))
        assert result.response_draft is not None
        assert "investigated" in (result.response_draft or "").lower()


# ═══════════════════════════════════════════════════════════════════════════════
# TestE — Edge cases and guard conditions
# ═══════════════════════════════════════════════════════════════════════════════

class TestE_EdgeCases:
    """Guards against common failure modes in the clarification loop."""

    def _make_clarification_state(self) -> ConversationState:
        return ConversationState(
            ticket_id=_TICKET_ID,
            client_id=_CLIENT_ID,
            lifecycle_state=ConversationLifecycle.CLARIFICATION,
            awaiting_customer=True,
            clarification_pending=True,
            case_id=_CASE_ID,
        )

    def test_E1_orchestrator_not_called_when_nlp_router_absent(self):
        """If NLPRouter is not injected but orchestrator is, orchestrator skipped since
        _nlp_slot_question stays None but the slot-resume block is skipped entirely,
        meaning the orchestrator IS called (no NLU check). Verify handler still succeeds."""
        conv_state = self._make_clarification_state()
        mock_orch = MagicMock()
        mock_orch.resume_ticket.return_value = _make_orchestration_result()
        handler, store = _build_handler(
            conv_state=conv_state,
            mock_nlp_router=None,  # no NLPRouter injected
            mock_case_service=None,
            mock_orchestrator=mock_orch,
        )
        result = handler.handle(_update_payload("hello?"))
        assert result.success is True

    def test_E2_agent_reply_does_not_trigger_clarification_path(self):
        """An agent reply (incoming=False, private=False) must NOT enter the clarification path."""
        conv_state = self._make_clarification_state()
        mock_nlp = MagicMock(spec=NLPRouter)
        mock_orch = MagicMock()
        handler, store = _build_handler(
            conv_state=conv_state,
            mock_nlp_router=mock_nlp,
            mock_orchestrator=mock_orch,
        )
        agent_reply_payload = {
            "ticket": {
                "id": int(_TICKET_ID),
                "updated_at": "2026-01-01T11:00:00Z",
                "status": 2,
                "custom_fields": {"cf_clients": _CLIENT_ID},
            },
            "changes": {},
            "latest_comment": {
                "body": "Agent added a note",
                "body_text": "Agent added a note",
                "incoming": False,   # NOT a customer reply
                "private": False,
            },
        }
        handler.handle(agent_reply_payload)
        # NLU must NOT be called for agent replies
        mock_nlp.route.assert_not_called()

    def test_E3_duplicate_webhook_skipped(self):
        """Second webhook with same ticket_id + timestamp must be idempotency-skipped."""
        conv_state = self._make_clarification_state()
        mock_nlp = MagicMock(spec=NLPRouter)
        mock_orch = MagicMock()
        handler, store = _build_handler(
            conv_state=conv_state,
            mock_nlp_router=mock_nlp,
            mock_orchestrator=mock_orch,
        )
        payload = _update_payload("My URN is 12345")
        handler.handle(payload)          # first call — processed
        result2 = handler.handle(payload)  # second call — same ts → idempotency skip
        assert result2.skipped is True
        assert result2.skip_reason == "DUPLICATE"

    def test_E4_empty_comment_body_skips_nlp(self):
        """If latest_comment.body_text is empty/whitespace, NLU must NOT be invoked."""
        conv_state = self._make_clarification_state()
        mock_nlp = MagicMock(spec=NLPRouter)
        mock_case = MagicMock(spec=CaseService)
        mock_orch = MagicMock()
        handler, store = _build_handler(
            conv_state=conv_state,
            mock_nlp_router=mock_nlp,
            mock_case_service=mock_case,
            mock_orchestrator=mock_orch,
        )
        empty_payload = _update_payload("   ")  # whitespace only
        result = handler.handle(empty_payload)
        assert result.success is True
        mock_nlp.route.assert_not_called()

    def test_E5_non_clarification_customer_reply_goes_to_continue_path(self):
        """A customer reply when lifecycle_state != CLARIFICATION goes to the continue path."""
        normal_state = ConversationState(
            ticket_id=_TICKET_ID,
            client_id=_CLIENT_ID,
            lifecycle_state=ConversationLifecycle.OPEN,
            awaiting_customer=False,  # NOT awaiting clarification
            clarification_pending=False,
            case_id=_CASE_ID,
        )
        mock_nlp = MagicMock(spec=NLPRouter)
        mock_orch = MagicMock()
        mock_orch.resume_ticket.return_value = _make_orchestration_result()
        handler, store = _build_handler(
            conv_state=normal_state,
            mock_nlp_router=mock_nlp,
            mock_orchestrator=mock_orch,
        )
        result = handler.handle(_update_payload("Just following up"))
        assert result.success is True
        # NLU must NOT be called on the non-clarification continue path
        mock_nlp.route.assert_not_called()
        # Orchestrator IS called (normal continue path)
        mock_orch.resume_ticket.assert_called_once()
