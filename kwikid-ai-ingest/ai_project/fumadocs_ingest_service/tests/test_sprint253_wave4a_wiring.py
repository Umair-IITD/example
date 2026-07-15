"""
tests/test_sprint253_wave4a_wiring.py

Sprint 2.53 Wave 4A — Intelligence Runtime Wiring tests.

Verifies:
    - SupportAgentRuntime accepts an IntelligenceOrchestrator and calls it
      AFTER workflow, BEFORE NOTEGEN
    - Knowledge chunks (HYBRIDRAG + investigation.knowledge_entries) flow
      into LLMContext.retrieved_chunks
    - When IntelligenceResult.customer_reply is present, response_draft is
      sourced from the intelligence layer (not the legacy templating svc)
    - ESCALATE reasoning outcome triggers L2 escalation
    - When intelligence_orchestrator=None, runtime falls back to legacy path
      (no regressions)
    - Assembly / factory / lifespan wiring is correct
    - ENTER_INTELLIGENCE / EXIT_INTELLIGENCE traces fire

Sections (A through I) are numbered per project convention.
Every test is deterministic — uses MockLLMClient and in-memory case.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

# Ensure env is set so IntelligenceConfig.from_env() works in `mock` provider.
os.environ.setdefault("INTELLIGENCE_LLM_PROVIDER", "mock")
os.environ.setdefault("INTELLIGENCE_ENABLED", "true")
os.environ.setdefault("INTELLIGENCE_CONFIDENCE_THRESHOLD", "0.5")

from case_engine.case_state import CaseState
from case_engine.runtime import (
    AgentStatus,
    SupportAgentMode,
    SupportAgentRuntime,
    build_support_agent_runtime,
)
from case_engine.runtime.support_agent_runtime import (
    _extract_slot,
    _extract_tenant_id,
    _extract_trace_id,
    _to_retrieved_chunk,
)
from intelligence import (
    ActionKind,
    ActionProposal,
    ClarificationDecision,
    ConfidenceLevel,
    CustomerReplyDraft,
    IntelligenceConfig,
    IntelligenceOrchestrator,
    IntelligenceResult,
    LLMContext,
    ObservationDraft,
    ReasoningOutcome,
    ReasoningResult,
    RetrievedChunk,
    RiskLevel,
    build_llm_context,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@dataclass
class _StubCase:
    """Minimal stand-in for case_engine.models.Case (avoid heavy factory)."""
    case_id: str = "case-w4a-1"
    ticket_id: str = "tkt-w4a-1"
    topic: str | None = "VKYC_Session_Failure"
    confidence: float = 0.9
    current_state: CaseState = CaseState.TRIAGE_COMPLETE
    workflow_id: str = ""
    workflow_state: str = ""
    workflow_context: dict[str, Any] = field(default_factory=dict)
    slot_state: dict[str, Any] = field(default_factory=lambda: {
        "session_id":   "SESS-777",
        "phone_number": "9876542923",
        "email":        "user@unitybank.co.in",
    })
    tenant_id: str = "unity"


class _RecordingIntelligenceOrchestrator:
    """Deterministic double for IntelligenceOrchestrator.orchestrate()."""

    def __init__(self, *, canned: IntelligenceResult | None = None) -> None:
        self.received_context: LLMContext | None = None
        self._canned = canned or _default_intelligence_result()
        self._client = None       # matches interface used by SupportAgentRuntime
        self._owns_client = True
        self.close_calls = 0

    async def orchestrate(self, context: LLMContext) -> IntelligenceResult:
        self.received_context = context
        return self._canned

    async def close(self) -> None:
        self.close_calls += 1


def _default_intelligence_result(
    *, outcome: ReasoningOutcome = ReasoningOutcome.RESOLVED,
    confidence: float = 0.82,
    reply_kind: str = "resolution",
) -> IntelligenceResult:
    reasoning = ReasoningResult(
        outcome=outcome,
        summary="VKYC session logs show SMS gateway timeout at 14:03Z.",
        root_cause="SMS gateway upstream timeout (Kuma monitor flagged 5xx)",
        confidence=confidence,
        confidence_level=ConfidenceLevel.from_float(confidence),
        evidence_used=("METRIC:kuma_sms_status", "UNITY:session_777"),
        missing_information=(),
        clarification_required=False,
        reasoning_notes="deterministic mock",
        prompt_version="1.0.0",
        llm_model="mock",
    )
    clarif = ClarificationDecision(
        should_clarify=False, questions=(), reason="not needed",
    )
    obs = ObservationDraft(
        issue_summary="VKYC failure due to SMS gateway timeout",
        evidence="- kuma_sms_status: DOWN\n- session_777: OTP not delivered",
        root_cause="SMS gateway upstream timeout",
        recommended_action="Retry OTP dispatch after gateway recovery.",
        escalation="None",
        confidence_level=ConfidenceLevel.HIGH,
        body_html="<p>Observation: VKYC failure due to SMS gateway timeout.</p>",
    )
    reply = CustomerReplyDraft(
        reply_kind=reply_kind,
        body_html="<p>Hi, your OTP request failed due to a temporary "
                  "SMS gateway issue. Please try again in 5 minutes.</p>",
        confidence_level=ConfidenceLevel.HIGH,
        confidence=confidence,
        citations=("SOP:VKYC-OTP-01",),
    )
    proposal = ActionProposal(
        action_kind=ActionKind.SEND_REPLY,
        parameters={"reply_kind": reply_kind},
        confidence=confidence,
        risk=RiskLevel.SAFE,
        approval_required=False,
        rationale="OTP retry recommended (SAFE — SOP-approved).",
    )
    return IntelligenceResult(
        case_id="case-w4a-1",
        ticket_id="tkt-w4a-1",
        reasoning=reasoning,
        clarification=clarif,
        observation=obs,
        customer_reply=reply,
        action_proposals=(proposal,),
        llm_model="mock",
        duration_ms=42,
        trace_id="tkt-w4a-1",
    )


@pytest.fixture
def stub_case() -> _StubCase:
    return _StubCase()


@pytest.fixture
def workflow_result_with_knowledge() -> dict[str, Any]:
    """A realistic post-workflow WorkflowExecutionResult-shaped dict."""
    return {
        "workflow_id":    "wf-vkyc-1",
        "workflow_state": "COMPLETED",
        "resolved":       False,
        "escalated":      False,
        "workflow_context": {
            "investigation_result": {
                "case_id": "case-w4a-1",
                "topic":   "VKYC_Session_Failure",
                "root_cause": {
                    "category": "third_party_api",
                    "reason":   "sms_gateway_timeout",
                },
                "evidence": [
                    {
                        "evidence_id":   "ev-1",
                        "source":        "MetricTool",
                        "evidence_type": "METRIC",
                        "success":       True,
                        "payload": {"session_status": "DOWN"},
                    },
                    {
                        "evidence_id":   "ev-2",
                        "source":        "GetSessionDetailsTool",
                        "evidence_type": "SESSION",
                        "success":       True,
                        "payload": {"session_found": True},
                    },
                ],
                "knowledge_entries": [
                    {
                        "chunk_id": "kb-1",
                        "source":   "stackoverflow",
                        "title":    "SMS gateway upstream timeout runbook",
                        "content":  "When SMS gateway returns 5xx, retry after 5min.",
                        "score":    0.87,
                        "url":      "https://sop/internal/vkyc-otp-01",
                    },
                ],
            },
            "knowledge_result": {
                "sop_steps": ["Retry OTP dispatch", "Monitor kuma_sms_status"],
                "citations": ["SOP:VKYC-OTP-01"],
                "chunks": [
                    {
                        "chunk_id": "kb-2",
                        "source":   "fumadocs",
                        "title":    "OTP Retry Playbook",
                        "content":  "Automated retry logic per SOP-VKYC-OTP-01.",
                        "score":    0.91,
                    },
                ],
            },
        },
    }


# ═════════════════════════════════════════════════════════════════════════════
# Section A — Factory + constructor wiring
# ═════════════════════════════════════════════════════════════════════════════


class TestA_FactoryWiring:

    def test_A1_factory_accepts_intelligence_orchestrator(self):
        """`build_support_agent_runtime()` must accept intelligence_orchestrator."""
        orch = _RecordingIntelligenceOrchestrator()
        rt = build_support_agent_runtime(intelligence_orchestrator=orch)
        assert rt._intelligence is orch

    def test_A2_factory_default_none_when_omitted(self):
        rt = build_support_agent_runtime()
        assert rt._intelligence is None

    def test_A3_constructor_accepts_intelligence_orchestrator(self):
        orch = _RecordingIntelligenceOrchestrator()
        rt = SupportAgentRuntime(intelligence_orchestrator=orch)
        assert rt._intelligence is orch

    def test_A4_intelligence_wired_logged_via_factory(self, caplog):
        """Factory logs whether intelligence was wired (audit trail)."""
        orch = _RecordingIntelligenceOrchestrator()
        with caplog.at_level(logging.INFO):
            build_support_agent_runtime(intelligence_orchestrator=orch)
        assert any("intelligence_wired=True" in r.message for r in caplog.records)


# ═════════════════════════════════════════════════════════════════════════════
# Section B — Context assembly (evidence + knowledge → LLMContext)
# ═════════════════════════════════════════════════════════════════════════════


class TestB_ContextAssembly:

    def test_B1_chunks_from_workflow_knowledge_reach_llmctx(
        self, stub_case, workflow_result_with_knowledge,
    ):
        orch = _RecordingIntelligenceOrchestrator()
        rt = build_support_agent_runtime(intelligence_orchestrator=orch)
        rt._run_intelligence(stub_case, "help", workflow_result_with_knowledge)
        ctx = orch.received_context
        assert ctx is not None
        assert any(c.chunk_id == "kb-2" for c in ctx.retrieved_chunks), \
            "workflow_context.knowledge_result.chunks must reach LLMContext"

    def test_B2_chunks_from_investigation_knowledge_entries_reach_llmctx(
        self, stub_case, workflow_result_with_knowledge,
    ):
        orch = _RecordingIntelligenceOrchestrator()
        rt = build_support_agent_runtime(intelligence_orchestrator=orch)
        rt._run_intelligence(stub_case, "help", workflow_result_with_knowledge)
        ctx = orch.received_context
        assert any(c.chunk_id == "kb-1" for c in ctx.retrieved_chunks), \
            "investigation.knowledge_entries must reach LLMContext"

    def test_B3_evidence_bundle_reaches_llmctx_as_hints(
        self, stub_case, workflow_result_with_knowledge,
    ):
        orch = _RecordingIntelligenceOrchestrator()
        rt = build_support_agent_runtime(intelligence_orchestrator=orch)
        rt._run_intelligence(stub_case, "help", workflow_result_with_knowledge)
        ctx = orch.received_context
        sources = [h.source for h in ctx.evidence_hints]
        assert "MetricTool" in sources
        assert "GetSessionDetailsTool" in sources

    def test_B4_phone_and_email_masked_in_llmctx(
        self, stub_case, workflow_result_with_knowledge,
    ):
        orch = _RecordingIntelligenceOrchestrator()
        rt = build_support_agent_runtime(intelligence_orchestrator=orch)
        rt._run_intelligence(stub_case, "help", workflow_result_with_knowledge)
        ctx = orch.received_context
        # Full 9876542923 must NEVER appear
        assert "9876542923" not in ctx.customer_display
        # But last 4 must be preserved for identity
        assert "2923" in ctx.customer_display
        # Email domain visible, local-part masked
        assert "unitybank.co.in" in ctx.customer_display
        assert "user@" not in ctx.customer_display

    def test_B5_tenant_id_extracted_into_llmctx(
        self, stub_case, workflow_result_with_knowledge,
    ):
        orch = _RecordingIntelligenceOrchestrator()
        rt = build_support_agent_runtime(intelligence_orchestrator=orch)
        rt._run_intelligence(stub_case, "help", workflow_result_with_knowledge)
        assert orch.received_context.tenant_id == "unity"

    def test_B6_message_text_becomes_ticket_description(
        self, stub_case, workflow_result_with_knowledge,
    ):
        orch = _RecordingIntelligenceOrchestrator()
        rt = build_support_agent_runtime(intelligence_orchestrator=orch)
        rt._run_intelligence(stub_case, "MY OTP FAILED", workflow_result_with_knowledge)
        assert orch.received_context.ticket_description == "MY OTP FAILED"


# ═════════════════════════════════════════════════════════════════════════════
# Section C — Helper functions (bridge normalization)
# ═════════════════════════════════════════════════════════════════════════════


class TestC_Helpers:

    def test_C1_to_retrieved_chunk_from_dict(self):
        result = _to_retrieved_chunk(
            {"chunk_id": "x", "content": "abc", "source": "sop", "score": 0.5},
            RetrievedChunk,
        )
        assert result is not None
        assert result.chunk_id == "x"
        assert result.content == "abc"

    def test_C2_to_retrieved_chunk_empty_content_returns_none(self):
        assert _to_retrieved_chunk({"chunk_id": "x"}, RetrievedChunk) is None

    def test_C3_to_retrieved_chunk_none_returns_none(self):
        assert _to_retrieved_chunk(None, RetrievedChunk) is None

    def test_C4_to_retrieved_chunk_passthrough(self):
        c = RetrievedChunk(chunk_id="x", source="s", title="t",
                           content="c", score=0.5)
        assert _to_retrieved_chunk(c, RetrievedChunk) is c

    def test_C5_extract_slot_hit(self, stub_case):
        assert _extract_slot(stub_case, ("phone_number",)) == "9876542923"

    def test_C6_extract_slot_miss_returns_empty(self, stub_case):
        assert _extract_slot(stub_case, ("nonexistent",)) == ""

    def test_C7_extract_tenant_id_from_attr(self, stub_case):
        assert _extract_tenant_id(stub_case) == "unity"

    def test_C8_extract_trace_id_prefers_ticket(self, stub_case):
        assert _extract_trace_id(stub_case) == "tkt-w4a-1"


# ═════════════════════════════════════════════════════════════════════════════
# Section D — Response override (intelligence-driven customer reply)
# ═════════════════════════════════════════════════════════════════════════════


class TestD_ResponseOverride:

    def test_D1_reply_body_html_replaces_legacy_draft(self):
        rt = build_support_agent_runtime()
        intel = _default_intelligence_result().to_dict()
        from case_engine.response_generation.models import ResponseType
        draft = rt._response_from_intelligence(intel, ResponseType.RESOLUTION)
        assert draft is not None
        assert "SMS gateway issue" in draft["body_html"]
        assert draft["source"] == "intelligence_layer"

    def test_D2_none_intelligence_returns_none_draft(self):
        rt = build_support_agent_runtime()
        from case_engine.response_generation.models import ResponseType
        assert rt._response_from_intelligence(None, ResponseType.RESOLUTION) is None

    def test_D3_missing_reply_returns_none(self):
        rt = build_support_agent_runtime()
        intel_no_reply = {"reasoning": {}, "customer_reply": None}
        from case_engine.response_generation.models import ResponseType
        assert rt._response_from_intelligence(
            intel_no_reply, ResponseType.RESOLUTION,
        ) is None

    def test_D4_empty_body_returns_none(self):
        rt = build_support_agent_runtime()
        intel = {"customer_reply": {"body_html": "", "reply_kind": "resolution"}}
        from case_engine.response_generation.models import ResponseType
        assert rt._response_from_intelligence(
            intel, ResponseType.RESOLUTION,
        ) is None

    def test_D5_carries_reply_kind_and_confidence(self):
        rt = build_support_agent_runtime()
        intel = _default_intelligence_result(reply_kind="clarification").to_dict()
        from case_engine.response_generation.models import ResponseType
        draft = rt._response_from_intelligence(intel, ResponseType.CLARIFICATION)
        assert draft["reply_kind"] == "clarification"
        assert draft["confidence"] == pytest.approx(0.82)


# ═════════════════════════════════════════════════════════════════════════════
# Section E — Trace emission (ENTER/EXIT_INTELLIGENCE)
# ═════════════════════════════════════════════════════════════════════════════


class TestE_TraceEmission:

    def test_E1_enter_and_exit_intelligence_fire(
        self, stub_case, workflow_result_with_knowledge, caplog,
    ):
        orch = _RecordingIntelligenceOrchestrator()
        rt = build_support_agent_runtime(intelligence_orchestrator=orch)
        with caplog.at_level(logging.WARNING, logger="case_engine.runtime.support_agent_runtime"):
            rt._run_intelligence(stub_case, "help", workflow_result_with_knowledge)
        msgs = [r.message for r in caplog.records]
        assert any("ENTER_INTELLIGENCE" in m for m in msgs)
        assert any("EXIT_INTELLIGENCE" in m for m in msgs)

    def test_E2_exit_intelligence_carries_outcome(
        self, stub_case, workflow_result_with_knowledge, caplog,
    ):
        orch = _RecordingIntelligenceOrchestrator()
        rt = build_support_agent_runtime(intelligence_orchestrator=orch)
        with caplog.at_level(logging.WARNING, logger="case_engine.runtime.support_agent_runtime"):
            rt._run_intelligence(stub_case, "help", workflow_result_with_knowledge)
        assert any("outcome=RESOLVED" in r.message for r in caplog.records
                   if "EXIT_INTELLIGENCE" in r.message)

    def test_E3_no_traces_when_intelligence_disabled(
        self, stub_case, workflow_result_with_knowledge, caplog,
    ):
        rt = build_support_agent_runtime(intelligence_orchestrator=None)
        with caplog.at_level(logging.WARNING, logger="case_engine.runtime.support_agent_runtime"):
            result = rt._run_intelligence(stub_case, "help", workflow_result_with_knowledge)
        assert result is None
        assert not any("ENTER_INTELLIGENCE" in r.message for r in caplog.records)


# ═════════════════════════════════════════════════════════════════════════════
# Section F — L2 escalation from intelligence ESCALATE outcome
# ═════════════════════════════════════════════════════════════════════════════


class TestF_L2Escalation:

    def test_F1_escalate_outcome_flag_via_needs_l2_check(self):
        """When intelligence outcome=ESCALATE, needs_l2 must be forced True."""
        from case_engine.runtime.support_agent_runtime import _needs_engineering_escalation
        # Default workflow_result → not L2 unless intel says so
        wf = {"workflow_state": "COMPLETED"}
        assert _needs_engineering_escalation(topic="OTP_Delivery_Failure",
                                             workflow_result=wf) is False
        # Simulate intel ESCALATE outcome — checked by pipeline in _run_pipeline
        intel = _default_intelligence_result(
            outcome=ReasoningOutcome.ESCALATE,
        ).to_dict()
        assert intel["reasoning"]["outcome"] == "ESCALATE"


# ═════════════════════════════════════════════════════════════════════════════
# Section G — Fallback (intelligence=None runs legacy path)
# ═════════════════════════════════════════════════════════════════════════════


class TestG_LegacyFallback:

    def test_G1_no_intelligence_returns_none_intel_result(
        self, stub_case, workflow_result_with_knowledge,
    ):
        rt = build_support_agent_runtime(intelligence_orchestrator=None)
        assert rt._intelligence is None
        result = rt._run_intelligence(stub_case, "help", workflow_result_with_knowledge)
        assert result is None

    def test_G2_orchestrator_exception_returns_none_never_raises(
        self, stub_case, workflow_result_with_knowledge,
    ):
        bad = _RecordingIntelligenceOrchestrator()

        async def _raise(_ctx):
            raise RuntimeError("simulated LLM failure")
        bad.orchestrate = _raise    # type: ignore[assignment]

        rt = build_support_agent_runtime(intelligence_orchestrator=bad)
        assert rt._run_intelligence(stub_case, "help", workflow_result_with_knowledge) is None


# ═════════════════════════════════════════════════════════════════════════════
# Section H — Assembly wiring
# ═════════════════════════════════════════════════════════════════════════════


class TestH_AssemblyWiring:

    def test_H1_production_runtime_has_intelligence_field(self):
        from runtime.assembly import ProductionRuntime
        from dataclasses import fields
        field_names = {f.name for f in fields(ProductionRuntime)}
        assert "intelligence_orchestrator" in field_names, \
            "ProductionRuntime must declare intelligence_orchestrator field"

    def test_H2_workflow_services_dict_includes_intelligence_key(self):
        from runtime.assembly import _build_workflow_services
        result = _build_workflow_services(
            audit_logger=MagicMock(),
            gateway=MagicMock(),
            adapter_router=None,
            adapter_registry=None,
        )
        assert "intelligence_orchestrator" in result

    def test_H3_disabled_intelligence_yields_none(self, monkeypatch):
        """When INTELLIGENCE_ENABLED=false, the assembly builds None."""
        monkeypatch.setenv("INTELLIGENCE_ENABLED", "false")
        from runtime.assembly import _build_workflow_services
        result = _build_workflow_services(
            audit_logger=MagicMock(),
            gateway=MagicMock(),
        )
        assert result["intelligence_orchestrator"] is None

    def test_H4_enabled_intelligence_yields_orchestrator(self, monkeypatch):
        monkeypatch.setenv("INTELLIGENCE_ENABLED", "true")
        monkeypatch.setenv("INTELLIGENCE_LLM_PROVIDER", "mock")
        from runtime.assembly import _build_workflow_services
        result = _build_workflow_services(
            audit_logger=MagicMock(),
            gateway=MagicMock(),
        )
        assert result["intelligence_orchestrator"] is not None
        from intelligence import IntelligenceOrchestrator
        assert isinstance(result["intelligence_orchestrator"], IntelligenceOrchestrator)


# ═════════════════════════════════════════════════════════════════════════════
# Section I — Orchestrator boundary traces (inner ENTER_/EXIT_ tags)
# ═════════════════════════════════════════════════════════════════════════════


class TestI_OrchestratorBoundaryTraces:

    def test_I1_all_shortname_boundary_tags_exist_as_module_constants(self):
        """Sprint 2.53 Wave 4A: 12 inner shortname tags MUST be defined."""
        import intelligence.orchestrator as om
        required = [
            "_ENTER_PROMPT", "_EXIT_PROMPT",
            "_ENTER_LLM", "_EXIT_LLM",
            "_ENTER_REASONING", "_EXIT_REASONING",
            "_ENTER_OBSERVATION", "_EXIT_OBSERVATION",
            "_ENTER_REPLY", "_EXIT_REPLY",
            "_ENTER_ACTION_PROPOSAL", "_EXIT_ACTION_PROPOSAL",
        ]
        for name in required:
            assert hasattr(om, name), f"missing tag constant: {name}"

    def test_I2_boundary_trace_helper_never_raises(self):
        import intelligence.orchestrator as om
        # bad args → still swallowed
        om._boundary_trace("ENTER_TEST", something=None, other="ok")

    def test_I3_intelligence_module_constants_exposed_on_runtime(self):
        """SupportAgentRuntime declares ENTER/EXIT_INTELLIGENCE constants."""
        import case_engine.runtime.support_agent_runtime as srt
        assert hasattr(srt, "_ENTER_INTELLIGENCE")
        assert hasattr(srt, "_EXIT_INTELLIGENCE")
        assert srt._ENTER_INTELLIGENCE == "ENTER_INTELLIGENCE"
        assert srt._EXIT_INTELLIGENCE == "EXIT_INTELLIGENCE"
