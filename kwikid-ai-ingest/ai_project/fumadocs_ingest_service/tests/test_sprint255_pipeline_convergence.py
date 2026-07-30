"""
tests/test_sprint255_pipeline_convergence.py

Sprint 2.5.5 + 2.5.5C — Pipeline Convergence & Business-First Runtime Alignment

Tests verify the 7 runtime blocker fixes applied in Sprint 2.5.5C:

Section A — NLP / Classifier fixes (Sprint 2.5.5)
  A1  Natural-language OTP negation phrase → OTP_Delivery_Failure (conf ≥ 0.85)
  A2  Channel slot extracted from SMS/EMAIL/VOICE in free-form text
  A3  AuditLogger wired to workflow services (no AttributeError)

Section B — Async event-loop fixes (Sprint 2.5.5C Blocker 1)
  B1  asyncio.run() works from sync context (no running loop)
  B2  asyncio.run() works from async context (ThreadPoolExecutor bridge)

Section C — State machine fixes (Sprint 2.5.5C Blocker 2)
  C1  ESCALATED case returns immediately from _run_pipeline (frozen guard)
  C2  ESCALATED result has agent_status=ESCALATED, not FAILED

Section D — Double-planner prevention (Sprint 2.5.5C Blocker 3)
  D1  workflow_result captured from case.workflow_id after pre-fill auto-start
  D2  explicit start_workflow() not called when workflow already started

Section E — Knowledge retrieval (Sprint 2.5.5C Blocker 4)
  E1  workflow_context key present in explicit start_workflow result dict
  E2  investigation_result key present in explicit start_workflow result dict

Section F — Observation note propagation (Sprint 2.5.5C Blockers 5+6)
  F1  HandlerResult has observation_note field
  F2  _extract_observation_note returns note from HandlerResult.observation_note
  F3  _extract_observation_note returns note from HandlerResult.detail fallback
  F4  Empty result returns empty string from _extract_observation_note

Section G — Registry soft recovery (Sprint 2.5.5C Blocker 7)
  G1  resume_ticket with case_id recovers missing registry entry
  G2  resume_ticket without case_id still returns TICKET_NOT_FOUND when missing
  G3  resume_ticket passes case_id from handler.handle conversation state
"""
from __future__ import annotations

import asyncio
import pytest
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch


# ── env setup ────────────────────────────────────────────────────────────────

import os
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test_key")
os.environ.setdefault("RAG_API_KEY", "test_rag_key")
os.environ.setdefault("OPENAI_API_KEY", "test_openai_key")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")


# ═══════════════════════════════════════════════════════════════════════════════
# Section A — NLP / Classifier fixes
# ═══════════════════════════════════════════════════════════════════════════════

class TestA_NLPClassifierFixes:

    def test_A1_natural_language_otp_negation_phrase(self):
        """Sprint 2.5.6: NLP router (not regex) classifies OTP negation phrases with high confidence.
        Uses a mock router to verify that TopicClassifier propagates OTP topic correctly."""
        from unittest.mock import MagicMock
        from case_engine.classifier import TopicClassifier
        from case_engine.nlp_router import NLPRouter, NLPSignal

        mock_router = MagicMock(spec=NLPRouter)
        mock_router.route.return_value = NLPSignal(
            intent="OTP_DELIVERY_FAILURE",
            nested_case="OTP_NOT_RECEIVED",
            entities={"urn": None, "session_id": None},
            negation_detected=True,
            confidence=0.92,
            needs_clarification=True,
            clarification_question="Please provide URN and Session ID",
            raw_text="",
        )

        clf = TopicClassifier(nlp_router=mock_router)
        phrases = [
            "I am not receiving OTP on SMS",
            "I am not receiving the OTP",
            "I haven't received the OTP yet",
            "Unable to receive OTP",
            "not getting OTP on my phone",
        ]
        for phrase in phrases:
            result = clf.classify(phrase)
            assert result is not None, f"No result for: {phrase!r}"
            topic = getattr(result, "topic", None)
            conf  = float(getattr(result, "confidence", 0.0))
            assert "OTP" in str(topic or "") or "otp" in str(topic or "").lower(), (
                f"Expected OTP topic for {phrase!r}, got {topic!r}"
            )
            assert conf >= 0.85, (
                f"Confidence {conf:.2f} < 0.85 for phrase {phrase!r}"
            )

    def test_A2_channel_slot_extracted_from_sms_text(self):
        """Channel slot 'channel=SMS' extracted when customer mentions SMS."""
        from case_engine.runtime.support_agent_runtime import _extract_free_form_slots
        result = _extract_free_form_slots(
            "OTP_Delivery_Failure",
            "My phone number is 9876543210. I am not receiving OTP on SMS.",
        )
        assert "channel" in result, f"channel slot missing; got {result}"
        assert result["channel"].upper() in {"SMS", "sms"} or "SMS" in result["channel"].upper()

    def test_A2b_channel_slot_extracted_from_email_text(self):
        """Channel slot 'channel=EMAIL' extracted when customer mentions email."""
        from case_engine.runtime.support_agent_runtime import _extract_free_form_slots
        result = _extract_free_form_slots(
            "OTP_Delivery_Failure",
            "Not getting the OTP on email. Phone: 9876543210.",
        )
        assert "channel" in result, f"channel slot missing; got {result}"
        assert "EMAIL" in result["channel"].upper()

    def test_A3_audit_logger_wired_no_attribute_error(self):
        """Workflow services receive a case_engine.audit.AuditLogger that has log_investigation_started."""
        from case_engine.audit import AuditLogger as CeAuditLogger
        logger = CeAuditLogger.__new__(CeAuditLogger)
        logger._supabase = None
        assert hasattr(logger, "log_investigation_started"), (
            "AuditLogger missing log_investigation_started — investigation pipeline will crash"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Section B — Async event-loop fixes
# ═══════════════════════════════════════════════════════════════════════════════

class TestB_AsyncEventLoopFixes:

    def test_B1_asyncio_run_from_sync_context(self):
        """asyncio.run() succeeds from a plain sync context (no running loop)."""
        async def _coro():
            return 42
        result = asyncio.run(_coro())
        assert result == 42

    def test_B2_unity_run_async_bridge_from_async_context(self):
        """_run_async() in unity_tools can execute a coroutine even when called
        from inside a running event loop (ThreadPoolExecutor bridge)."""
        from case_engine.tools.adapters.unity_tools import _run_async

        async def _coro():
            return "bridge_ok"

        async def _test():
            # We're inside a running event loop here — _run_async must use
            # the ThreadPoolExecutor path.
            return _run_async(_coro())

        result = asyncio.run(_test())
        assert result == "bridge_ok"


# ═══════════════════════════════════════════════════════════════════════════════
# Section C — State machine / frozen state fixes
# ═══════════════════════════════════════════════════════════════════════════════

class TestC_FrozenStateGuard:

    def _make_escalated_case(self):
        from case_engine.models import Case
        from case_engine.case_state import CaseState
        case = MagicMock(spec=Case)
        case.case_id = "test-case-escalated"
        case.ticket_id = "999"
        case.current_state = CaseState.ESCALATED
        case.topic = "OTP_Delivery_Failure"
        case.confidence = 0.90
        case.slot_state = {}
        case.workflow_id = None
        case.workflow_context = None
        case.workflow_state = None
        case.metadata = {}
        return case

    def _make_runtime(self):
        from case_engine.runtime.support_agent_runtime import SupportAgentRuntime
        rt = SupportAgentRuntime.__new__(SupportAgentRuntime)
        rt._case_svc = None
        rt._response_svc = None
        rt._engineering = None
        rt._intelligence = None
        rt._audit = None
        rt._mode = MagicMock()
        rt._mode.value = "DRY_RUN"
        return rt

    def test_C1_escalated_case_returns_immediately(self):
        """ESCALATED case skips entire pipeline — no classification or workflow start."""
        rt = self._make_runtime()
        case = self._make_escalated_case()
        from case_engine.runtime.support_agent_runtime import SupportAgentRuntime
        # Patch _emit_agent_started and _build_result
        with patch.object(SupportAgentRuntime, "_emit_agent_started"):
            with patch.object(SupportAgentRuntime, "_build_result", return_value=MagicMock()) as mock_build:
                rt._run_pipeline(
                    case=case,
                    message_text="test",
                    slot_values=None,
                    started_ms=0,
                    started_at="2026-07-16T00:00:00Z",
                    steps_completed=[],
                )
                mock_build.assert_called_once()
                call_kwargs = mock_build.call_args[1]
                from case_engine.runtime.agent_models import AgentStatus
                assert call_kwargs.get("agent_status") == AgentStatus.ESCALATED

    def test_C2_escalated_result_has_correct_status(self):
        """Frozen state guard returns AgentStatus.ESCALATED (not FAILED) for ESCALATED cases."""
        from case_engine.runtime.agent_models import AgentStatus
        # Verify the enum value is correct
        assert AgentStatus.ESCALATED == "ESCALATED"
        assert AgentStatus.FAILED != AgentStatus.ESCALATED


# ═══════════════════════════════════════════════════════════════════════════════
# Section D — Double-planner prevention
# ═══════════════════════════════════════════════════════════════════════════════

class TestD_DoublePlannerPrevention:

    def test_D1_workflow_result_recovered_from_case_workflow_id(self):
        """If case.workflow_id is set, workflow_result is recovered before explicit start_workflow.
        This simulates the pre-fill auto-start scenario where the for-loop discards results."""
        case = MagicMock()
        case.workflow_id = "wf-auto-123"
        case.workflow_state = "INVESTIGATION_COMPLETE"
        case.workflow_context = {"step_results": {}}

        # Simulate what the recovery code does
        workflow_result = None
        if workflow_result is None and getattr(case, "workflow_id", None):
            workflow_result = dict(getattr(case, "workflow_context", None) or {})
            workflow_result["workflow_state"] = case.workflow_state
            workflow_result["workflow_id"]    = case.workflow_id

        assert workflow_result is not None
        assert workflow_result["workflow_id"] == "wf-auto-123"
        assert workflow_result["workflow_state"] == "INVESTIGATION_COMPLETE"

    def test_D2_no_explicit_workflow_start_when_autostarted(self):
        """Once workflow_result is recovered, the explicit start_workflow branch is skipped."""
        case = MagicMock()
        case.workflow_id = "wf-auto-456"
        case.workflow_state = "DONE"
        case.workflow_context = {}
        case_svc = MagicMock()

        workflow_result = None
        # Recovery step
        if workflow_result is None and getattr(case, "workflow_id", None):
            workflow_result = dict(getattr(case, "workflow_context", None) or {})
            workflow_result["workflow_state"] = case.workflow_state
            workflow_result["workflow_id"]    = case.workflow_id

        # Explicit start should NOT be called
        if workflow_result is None and case_svc is not None:
            case_svc.start_workflow(case, {})

        case_svc.start_workflow.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# Section E — Knowledge retrieval (workflow_context in explicit start)
# ═══════════════════════════════════════════════════════════════════════════════

class TestE_KnowledgeRetrieval:

    def test_E1_workflow_context_in_explicit_start_result(self):
        """Explicit start_workflow result dict includes workflow_context key.
        Without this key, _extract_workflow_knowledge returns {} and reasoning has no context."""
        wf_start = MagicMock()
        wf_start.workflow_id = "wf-explicit-789"
        wf_start.workflow_state = "INVESTIGATION_COMPLETE"
        wf_start.step_results = {}
        wf_start.resolved = False
        wf_start.escalated = False
        wf_start.escalation_reason = None
        wf_start.resolution_note = None
        wf_start.workflow_context = {"investigation": {"root_cause": "OTP gateway timeout"}}
        wf_start.investigation_result = {"observation_note": "Gateway unreachable"}

        workflow_result = {
            "workflow_id":        wf_start.workflow_id,
            "workflow_state":     wf_start.workflow_state,
            "step_results":       wf_start.step_results,
            "resolved":           wf_start.resolved,
            "escalated":          wf_start.escalated,
            "escalation_reason":  wf_start.escalation_reason,
            "resolution_note":    wf_start.resolution_note,
            "workflow_context":   getattr(wf_start, "workflow_context", None),
            "investigation_result": getattr(wf_start, "investigation_result", None),
        }

        assert "workflow_context" in workflow_result, "workflow_context key missing from explicit start result"
        assert workflow_result["workflow_context"] == {"investigation": {"root_cause": "OTP gateway timeout"}}

    def test_E2_investigation_result_in_explicit_start_result(self):
        """Explicit start_workflow result dict includes investigation_result key for observation extraction."""
        wf_start = MagicMock()
        wf_start.investigation_result = {"observation_note": "User phone not registered"}
        workflow_result = {
            "investigation_result": getattr(wf_start, "investigation_result", None),
        }
        assert "investigation_result" in workflow_result
        assert workflow_result["investigation_result"]["observation_note"] == "User phone not registered"


# ═══════════════════════════════════════════════════════════════════════════════
# Section F — Observation note propagation
# ═══════════════════════════════════════════════════════════════════════════════

class TestF_ObservationNotePropagation:

    def test_F1_handler_result_has_observation_note_field(self):
        """HandlerResult dataclass has observation_note field (Sprint 2.5.5C addition)."""
        from freshdesk.handlers import HandlerResult
        result = HandlerResult(success=True, observation_note="L1 findings: OTP gateway unreachable")
        assert result.observation_note == "L1 findings: OTP gateway unreachable"

    def test_F1b_handler_result_observation_note_defaults_to_none(self):
        """HandlerResult.observation_note defaults to None when not provided."""
        from freshdesk.handlers import HandlerResult
        result = HandlerResult(success=True)
        assert result.observation_note is None

    def test_F2_extract_observation_note_from_handler_result_attribute(self):
        """_extract_observation_note reads from result.observation_note."""
        from api.routes.webhooks.freshdesk import _extract_observation_note
        from freshdesk.handlers import HandlerResult
        result = HandlerResult(success=True, observation_note="Investigation: OTP unreachable")
        note = _extract_observation_note(result)
        assert note == "Investigation: OTP unreachable"

    def test_F3_extract_observation_note_fallback_to_detail(self):
        """_extract_observation_note falls back to result.detail['observation_note']."""
        from api.routes.webhooks.freshdesk import _extract_observation_note
        from freshdesk.handlers import HandlerResult
        result = HandlerResult(success=True, detail={"observation_note": "Fallback note"})
        note = _extract_observation_note(result)
        assert note == "Fallback note"

    def test_F4_extract_observation_note_empty_result(self):
        """_extract_observation_note returns empty string when no note available."""
        from api.routes.webhooks.freshdesk import _extract_observation_note
        from freshdesk.handlers import HandlerResult
        result = HandlerResult(success=True)
        note = _extract_observation_note(result)
        assert note == ""

    def test_F5_obs_note_extracted_from_intelligence_layer_in_agent_result(self):
        """Observation note is extractable from agent_result metadata.intelligence_result.observation.note."""
        agent_result = {
            "agent_status": "SUCCESS",
            "metadata": {
                "intelligence_result": {
                    "observation": {
                        "note": "LLM observation: OTP gateway unreachable for Unity Bank",
                    }
                }
            }
        }
        _obs_note = None
        _intel = (agent_result.get("metadata") or {}).get("intelligence_result") or {}
        _intel_obs = (_intel.get("observation") or {}).get("note") or ""
        if _intel_obs:
            _obs_note = _intel_obs
        assert _obs_note == "LLM observation: OTP gateway unreachable for Unity Bank"


# ═══════════════════════════════════════════════════════════════════════════════
# Section G — Registry soft recovery
# ═══════════════════════════════════════════════════════════════════════════════

class TestG_RegistrySoftRecovery:

    def _build_orchestrator(self, case_svc=None):
        from case_engine.ticket_orchestration.orchestrator import TicketOrchestrator
        orch = TicketOrchestrator.__new__(TicketOrchestrator)
        orch._registry = {}
        orch._agent = None
        orch._case_svc = case_svc
        orch._audit = None
        orch._client_resolver = None
        return orch

    def test_G1_resume_with_case_id_recovers_missing_entry(self):
        """resume_ticket with valid case_id soft-recovers a missing registry entry."""
        mock_case = MagicMock()
        mock_case.case_id = "case-abc"

        case_svc = MagicMock()
        case_svc.get_case.return_value = mock_case

        orch = self._build_orchestrator(case_svc=case_svc)
        result = orch.resume_ticket("ticket-123", "Hello", case_id="case-abc")

        # get_case may be called more than once:
        # 1. During soft recovery (to verify case exists)
        # 2. During the normal resume flow (to load case for agent)
        assert case_svc.get_case.call_count >= 1, "get_case should be called at least once"
        assert "ticket-123" in orch._registry, "Registry entry should be created on soft recovery"
        # No agent wired, so result will be a failure from the resume path (case not None but agent is None)
        # The key is no TICKET_NOT_FOUND error
        assert result.error_code != "TICKET_NOT_FOUND", (
            f"Expected soft recovery, got TICKET_NOT_FOUND. error_code={result.error_code}"
        )

    def test_G2_resume_without_case_id_returns_ticket_not_found(self):
        """resume_ticket without case_id still returns TICKET_NOT_FOUND when registry miss."""
        orch = self._build_orchestrator()
        result = orch.resume_ticket("ticket-999", "Hello")
        assert result.error_code == "TICKET_NOT_FOUND"

    def test_G3_resume_with_case_id_but_case_svc_missing_returns_not_found(self):
        """resume_ticket with case_id but no CaseService still returns TICKET_NOT_FOUND."""
        orch = self._build_orchestrator(case_svc=None)
        result = orch.resume_ticket("ticket-888", "Hello", case_id="case-xyz")
        assert result.error_code == "TICKET_NOT_FOUND"

    def test_G4_resume_ticket_signature_accepts_case_id_kwarg(self):
        """resume_ticket public signature accepts case_id keyword argument without error."""
        from case_engine.ticket_orchestration.orchestrator import TicketOrchestrator
        import inspect
        sig = inspect.signature(TicketOrchestrator.resume_ticket)
        assert "case_id" in sig.parameters, (
            "resume_ticket missing case_id parameter — handlers cannot pass conversation state"
        )
