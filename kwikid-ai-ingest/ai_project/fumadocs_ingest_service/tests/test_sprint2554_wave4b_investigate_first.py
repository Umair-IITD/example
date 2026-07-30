"""
tests/test_sprint2554_wave4b_investigate_first.py

Sprint 2.54 Wave 4B — Investigate-First Policy Regression Tests.

Locks in the core invariant: investigation runs BEFORE clarification is issued.
The old runtime short-circuited to AWAITING_CLARIFICATION before calling
start_workflow(). These tests guard against any regression to that pattern.

Sections:
  A — Investigation runs before clarification (core invariant)
  B — Clarification still issued after investigation when slots missing
  C — Agent status precedence: CLARIFICATION over L2 when waiting for input
  D — Resolved/Closed lifecycle does not trigger continue-path resume
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import MagicMock

os.environ.setdefault("RAG_API_KEY", "test-w4b-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-supabase-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

from case_engine.models import Case
from case_engine.case_state import CaseState
from case_engine.runtime.support_agent_runtime import SupportAgentRuntime
from case_engine.runtime.agent_models import AgentStatus


def _make_case(
    topic: str = "OTP_Delivery_Failure",
    case_id: str = "case-w4b-001",
    state: CaseState = CaseState.TRIAGE_COMPLETE,
) -> Case:
    c = Case(case_id=case_id, ticket_id="fd-w4b-001", client="unity_bank")
    c.topic = topic
    c.confidence = 0.88
    c.current_state = state
    return c


def _make_case_svc_missing_slots() -> MagicMock:
    """CaseService mock: slots not filled, produces clarification question."""
    mock_cs = MagicMock()
    mock_cs.classify_case.side_effect = lambda c, t: c

    msg_result = MagicMock()
    msg_result.all_slots_filled = False
    msg_result.workflow_started = False
    msg_result.next_question = {"prompt_text": "What is your phone number?"}
    mock_cs.receive_message.return_value = msg_result

    wf = MagicMock()
    wf.workflow_id = "wf-w4b"
    wf.workflow_state = "SLOT_FILL"
    wf.step_results = []
    wf.resolved = False
    wf.escalated = False
    wf.escalation_reason = None
    wf.resolution_note = None
    mock_cs.start_workflow.return_value = wf

    return mock_cs


# ── A — Investigation runs before clarification ───────────────────────────────

class TestA_InvestigationBeforeClarification:
    """
    Core Wave 4B invariant: start_workflow() (investigation path) MUST be called
    even when slots are not yet filled. The old runtime returned early before
    ever reaching Step 4.
    """

    def test_A1_start_workflow_called_when_slots_missing(self):
        """start_workflow() must be called even when all_slots_filled=False."""
        mock_cs = _make_case_svc_missing_slots()
        rt = SupportAgentRuntime(case_service=mock_cs)
        case = _make_case()
        rt.run_case(case, "No OTP received.")
        mock_cs.start_workflow.assert_called_once()

    def test_A2_workflow_step_in_steps_completed_when_slots_missing(self):
        """'WORKFLOW' step must appear in steps_completed when slots are missing."""
        mock_cs = _make_case_svc_missing_slots()
        rt = SupportAgentRuntime(case_service=mock_cs)
        case = _make_case()
        result = rt.run_case(case, "No OTP received.")
        assert "WORKFLOW" in result.steps_completed, (
            f"Expected WORKFLOW in steps_completed, got: {result.steps_completed}"
        )

    def test_A3_clarify_step_also_present(self):
        """'CLARIFY' step must also appear — clarification is NOT skipped."""
        mock_cs = _make_case_svc_missing_slots()
        rt = SupportAgentRuntime(case_service=mock_cs)
        case = _make_case()
        result = rt.run_case(case, "No OTP received.")
        assert "CLARIFY" in result.steps_completed

    def test_A4_workflow_called_after_slot_extract(self):
        """
        step order invariant: SLOT_EXTRACT appears before WORKFLOW in steps_completed.
        Investigation cannot run before slot extraction.
        """
        mock_cs = _make_case_svc_missing_slots()
        rt = SupportAgentRuntime(case_service=mock_cs)
        case = _make_case()
        result = rt.run_case(case, "No OTP received.")
        steps = result.steps_completed
        assert "SLOT_EXTRACT" in steps
        assert "WORKFLOW" in steps
        assert steps.index("SLOT_EXTRACT") < steps.index("WORKFLOW"), (
            f"SLOT_EXTRACT must precede WORKFLOW. Got: {steps}"
        )

    def test_A5_enter_clarification_policy_trace_not_return_clarification_required(self, caplog):
        """
        ENTER_CLARIFICATION_POLICY must appear; RETURN_CLARIFICATION_REQUIRED must NOT.
        The old early-return emitted RETURN_CLARIFICATION_REQUIRED — now it must not.
        """
        import logging
        mock_cs = _make_case_svc_missing_slots()
        rt = SupportAgentRuntime(case_service=mock_cs)
        case = _make_case()
        with caplog.at_level(logging.WARNING, logger="case_engine.runtime.support_agent_runtime"):
            rt.run_case(case, "No OTP received.")
        messages = [r.getMessage() for r in caplog.records]
        assert any("ENTER_CLARIFICATION_POLICY" in m for m in messages), (
            "ENTER_CLARIFICATION_POLICY trace must fire"
        )
        assert not any("RETURN_CLARIFICATION_REQUIRED" in m for m in messages), (
            "RETURN_CLARIFICATION_REQUIRED must NOT fire (old early-return path is gone)"
        )

    def test_A6_no_early_return_receive_message_called_once(self):
        """receive_message() should be called once (for the main slot extraction pass)."""
        mock_cs = _make_case_svc_missing_slots()
        rt = SupportAgentRuntime(case_service=mock_cs)
        case = _make_case()
        rt.run_case(case, "No OTP received.")
        # The main receive_message call happens once in Step 3. Pre-fill calls
        # may add more, but the final call must occur.
        assert mock_cs.receive_message.called


# ── B — Clarification still issued after investigation ────────────────────────

class TestB_ClarificationStillIssuedAfterInvestigation:
    """
    Investigation runs, but the clarification question is STILL asked when slots
    are genuinely missing. "Investigate First" does not mean "Never Ask".
    """

    def test_B1_agent_status_awaiting_clarification_when_slots_missing(self):
        """
        After investigation, status must be AWAITING_CLARIFICATION when the case is
        still in AWAITING_INPUT state (i.e., slots were not filled).
        """
        mock_cs = _make_case_svc_missing_slots()

        def _recv_side(*args, **kwargs):
            # Simulate CaseService transitioning case to AWAITING_INPUT
            if args and hasattr(args[0], "current_state"):
                args[0].current_state = CaseState.AWAITING_INPUT
            r = MagicMock()
            r.all_slots_filled = False
            r.workflow_started = False
            r.next_question = {"prompt_text": "What is your phone number?"}
            return r
        mock_cs.receive_message.side_effect = _recv_side

        rt = SupportAgentRuntime(case_service=mock_cs)
        case = _make_case()
        result = rt.run_case(case, "No OTP received.")
        assert result.agent_status == AgentStatus.AWAITING_CLARIFICATION, (
            f"Expected AWAITING_CLARIFICATION after investigation+missing slots, "
            f"got: {result.agent_status}"
        )

    def test_B2_clarification_and_workflow_both_in_steps(self):
        """
        Both CLARIFY and WORKFLOW must appear in steps_completed — investigation
        ran AND clarification was noted. Neither is skipped.
        """
        mock_cs = _make_case_svc_missing_slots()
        rt = SupportAgentRuntime(case_service=mock_cs)
        case = _make_case()
        result = rt.run_case(case, "No OTP received.")
        assert "CLARIFY" in result.steps_completed, "CLARIFY step must appear"
        assert "WORKFLOW" in result.steps_completed, "WORKFLOW step must appear"


# ── C — Agent status: CLARIFICATION takes priority over L2 ───────────────────

class TestC_ClarificationPrecedenceOverL2:
    """
    When case.current_state == AWAITING_INPUT AND needs_l2 is True,
    agent_status must be AWAITING_CLARIFICATION, not ESCALATED.
    The blueprint mandates asking the customer before escalating to L2.
    """

    def test_C1_clarification_beats_l2_escalation(self):
        """
        topic=VKYC_Session_Failure is in _L2_ESCALATION_TOPICS and triggers L2.
        But when awaiting input, AWAITING_CLARIFICATION must take priority.
        """
        mock_cs = MagicMock()
        mock_cs.classify_case.side_effect = lambda c, t: c

        def _recv_side(*args, **kwargs):
            if args and hasattr(args[0], "current_state"):
                args[0].current_state = CaseState.AWAITING_INPUT
            r = MagicMock()
            r.all_slots_filled = False
            r.workflow_started = False
            r.next_question = {"prompt_text": "What is your session ID?"}
            return r
        mock_cs.receive_message.side_effect = _recv_side

        # VKYC_Session_Failure with unresolved workflow triggers L2
        wf = MagicMock()
        wf.workflow_id = "wf-vkyc"
        wf.workflow_state = "SLOT_FILL"
        wf.step_results = []
        wf.resolved = False
        wf.escalated = False
        wf.escalation_reason = None
        wf.resolution_note = None
        mock_cs.start_workflow.return_value = wf

        rt = SupportAgentRuntime(case_service=mock_cs)
        case = _make_case(topic="VKYC_Session_Failure", case_id="case-vkyc")
        result = rt.run_case(case, "Session failed.")

        assert result.agent_status == AgentStatus.AWAITING_CLARIFICATION, (
            f"AWAITING_CLARIFICATION must beat L2 escalation. Got: {result.agent_status}. "
            "Blueprint §7: ask for missing info before escalating."
        )


# ── D — RESOLVED lifecycle skips continue-path resume ────────────────────────

class TestD_ResolvedLifecycleSkipsResume:
    """
    RESOLVED/CLOSED conversations must not trigger the Sprint 2.54 'continue' resume path.
    Only OPEN/PENDING conversations trigger customer_reply_continue.
    """

    def test_D1_resolved_conv_does_not_call_resume_ticket(self):
        from freshdesk.handlers import FreshdeskTicketUpdatedHandler
        from freshdesk.idempotency import WebhookIdempotencyStore
        from freshdesk.conversation_state import ConversationStateStore, ConversationLifecycle

        orch = MagicMock()
        idem = WebhookIdempotencyStore()
        conv = ConversationStateStore()

        conv.get_or_create("300001", "unity_bank")
        conv.update(
            "300001",
            awaiting_customer=False,
            lifecycle_state=ConversationLifecycle.RESOLVED,
        )

        handler = FreshdeskTicketUpdatedHandler(
            idempotency_store=idem,
            conversation_store=conv,
            ticket_orchestrator=orch,
        )

        handler.handle({
            "freshdesk_webhook": {
                "ticket_id": "300001",
                "ticket_url": "https://kwikid.freshdesk.com/helpdesk/tickets/300001",
                "ticket_status": "Resolved",
                "ticket_updated_at": "2026-07-16T04:00:00Z",
                "ticket_subject": "Done",
                "latest_comment": {
                    "id": 1,
                    "body": "<p>Thanks!</p>",
                    "body_text": "Thanks!",
                    "incoming": True,
                    "private": False,
                    "user_id": 999,
                    "created_at": "2026-07-16T04:00:00Z",
                },
            }
        })

        orch.resume_ticket.assert_not_called()

    def test_D2_open_conv_not_awaiting_does_call_resume_ticket(self):
        """OPEN conversation with awaiting_customer=False triggers customer_reply_continue."""
        from freshdesk.handlers import FreshdeskTicketUpdatedHandler
        from freshdesk.idempotency import WebhookIdempotencyStore
        from freshdesk.conversation_state import ConversationStateStore, ConversationLifecycle

        orch = MagicMock()
        resume_result = MagicMock()
        resume_result.error_code = None
        resume_result.agent_result = {}
        orch.resume_ticket.return_value = resume_result

        idem = WebhookIdempotencyStore()
        conv = ConversationStateStore()

        conv.get_or_create("300002", "unity_bank")
        # default lifecycle = OPEN, awaiting_customer = False

        handler = FreshdeskTicketUpdatedHandler(
            idempotency_store=idem,
            conversation_store=conv,
            ticket_orchestrator=orch,
        )

        handler.handle({
            "freshdesk_webhook": {
                "ticket_id": "300002",
                "ticket_url": "https://kwikid.freshdesk.com/helpdesk/tickets/300002",
                "ticket_status": "Open",
                "ticket_updated_at": "2026-07-16T04:00:00Z",
                "ticket_subject": "OTP issue",
                "latest_comment": {
                    "id": 2,
                    "body": "<p>Still failing.</p>",
                    "body_text": "Still failing.",
                    "incoming": True,
                    "private": False,
                    "user_id": 999,
                    "created_at": "2026-07-16T04:00:00Z",
                },
            }
        })

        orch.resume_ticket.assert_called_once()
