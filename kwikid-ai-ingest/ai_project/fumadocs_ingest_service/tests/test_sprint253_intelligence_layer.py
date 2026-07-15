"""
tests/test_sprint253_intelligence_layer.py

Wave 3 (Sprint 2.53) — Enterprise Intelligence Layer.

Sections:
    A — IntelligenceConfig
    B — Exceptions
    C — Domain models
    D — 22 Wave-3 traces
    E — Context Builder
    F — Prompt Builder templates
    G — Reasoning parser (schema-strict)
    H — LLM client (MockLLMClient + OpenAILLMClient with MockTransport)
    I — Orchestrator end-to-end
    J — Metrics password-only auth (Part J fix)
    K — Public export surface

No network. Every httpx interaction uses MockTransport.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any
from unittest.mock import MagicMock

os.environ.setdefault("RAG_API_KEY", "test-253-key")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-253")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-supabase-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
os.environ.setdefault("INTELLIGENCE_LLM_API_KEY", "sk-test-intelligence")

import httpx
import pytest

from intelligence import (
    ALL_WAVE3_TRACES,
    ActionKind,
    ActionProposal,
    ActionProposalPromptTemplate,
    ClarificationDecision,
    ClarificationPromptTemplate,
    ConfidenceLevel,
    ContextBuildError,
    CustomerReplyDraft,
    CustomerReplyPromptTemplate,
    EvidenceHint,
    IntelligenceConfig,
    IntelligenceConfigError,
    IntelligenceDisabled,
    IntelligenceError,
    IntelligenceOrchestrator,
    IntelligenceResult,
    LLMAuthError,
    LLMClient,
    LLMContext,
    LLMRateLimitError,
    LLMRequestError,
    LLMServerError,
    LLMTimeoutError,
    MockLLMClient,
    ObservationDraft,
    ObservationPromptTemplate,
    OpenAILLMClient,
    PromptBuilder,
    PromptPair,
    ReasoningOutcome,
    ReasoningParseError,
    ReasoningPromptTemplate,
    ReasoningResult,
    RetrievedChunk,
    RiskLevel,
    TRACE_01_WEBHOOK_RECEIVED,
    TRACE_16_LLM_REQUEST,
    TRACE_17_LLM_RESPONSE,
    TRACE_22_PIPELINE_COMPLETE,
    build_llm_client,
    build_llm_context,
    emit_wave3_trace,
    parse_action_proposals,
    parse_clarification_decision,
    parse_customer_reply,
    parse_observation_draft,
    parse_reasoning_result,
)
from metrics_platform.config import MetricsPlatformConfig as _MPC


# ── Helpers ───────────────────────────────────────────────────────────────

@pytest.fixture
def caplog_wave3(caplog):
    caplog.set_level(logging.WARNING, logger="intelligence.traces")
    return caplog


def _ctx(**overrides) -> LLMContext:
    kwargs = dict(
        case_id="c-1", ticket_id="t-1", tenant_id="UNITY",
        topic="VKYC_SESSION_FAILURE",
        ticket_subject="Test subject",
        ticket_description="Test description",
    )
    kwargs.update(overrides)
    return LLMContext(**kwargs)


def _cfg(**overrides) -> IntelligenceConfig:
    kwargs = dict(
        enabled=True,
        llm_provider="mock",
        llm_model="mock-llm-v1",
        llm_base_url="https://api.openai.com/v1",
        llm_api_key="sk-test",
        llm_timeout_s=5,
        llm_max_retries=1,
        llm_temperature=0.2,
        llm_max_output_tokens=800,
        llm_json_mode=True,
        confidence_threshold=0.75,
        user_agent="test/2.53",
    )
    kwargs.update(overrides)
    return IntelligenceConfig(**kwargs)


# ══════════════════════════════════════════════════════════════════════════════
# Section A — IntelligenceConfig
# ══════════════════════════════════════════════════════════════════════════════

class TestA_Config:
    def test_A1_default_from_env(self, monkeypatch):
        for k in ("INTELLIGENCE_LLM_PROVIDER", "INTELLIGENCE_LLM_MODEL",
                  "INTELLIGENCE_ENABLED", "INTELLIGENCE_CONFIDENCE_THRESHOLD"):
            monkeypatch.delenv(k, raising=False)
        cfg = IntelligenceConfig.from_env()
        assert cfg.llm_provider == "openai"
        assert cfg.llm_model == "gpt-4o-mini"
        assert cfg.enabled is True

    def test_A2_env_overrides(self, monkeypatch):
        monkeypatch.setenv("INTELLIGENCE_LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("INTELLIGENCE_LLM_MODEL", "claude-3-haiku")
        monkeypatch.setenv("INTELLIGENCE_CONFIDENCE_THRESHOLD", "0.65")
        cfg = IntelligenceConfig.from_env()
        assert cfg.llm_provider == "anthropic"
        assert cfg.llm_model == "claude-3-haiku"
        assert cfg.confidence_threshold == 0.65

    def test_A3_invalid_provider_rejected(self):
        with pytest.raises(ValueError):
            _cfg(llm_provider="unknown_vendor")

    def test_A4_temperature_range_enforced(self):
        with pytest.raises(ValueError):
            _cfg(llm_temperature=-0.1)
        with pytest.raises(ValueError):
            _cfg(llm_temperature=2.5)

    def test_A5_confidence_threshold_range(self):
        with pytest.raises(ValueError):
            _cfg(confidence_threshold=1.5)

    def test_A6_zero_timeout_rejected(self):
        with pytest.raises(ValueError):
            _cfg(llm_timeout_s=0)

    def test_A7_masked_api_key(self):
        cfg = _cfg(llm_api_key="sk-abcdefghijkl")
        assert cfg.masked_api_key == "sk-abcde****"
        assert "abcdefghijkl" not in cfg.masked_api_key

    def test_A8_api_key_fallback_order(self, monkeypatch):
        monkeypatch.delenv("INTELLIGENCE_LLM_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_CHAT_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-fallback")
        cfg = IntelligenceConfig.from_env()
        assert cfg.llm_api_key == "sk-openai-fallback"

    def test_A9_is_mock_property(self):
        assert _cfg(llm_provider="mock").is_mock is True
        assert _cfg(llm_provider="openai").is_mock is False

    def test_A10_missing_model_rejected(self):
        with pytest.raises(ValueError):
            _cfg(llm_model="")


# ══════════════════════════════════════════════════════════════════════════════
# Section B — Exceptions
# ══════════════════════════════════════════════════════════════════════════════

class TestB_Exceptions:
    def test_B1_hierarchy(self):
        for cls in (IntelligenceDisabled, IntelligenceConfigError,
                    LLMRequestError, LLMTimeoutError, LLMAuthError,
                    LLMRateLimitError, LLMServerError,
                    ReasoningParseError, ContextBuildError):
            assert issubclass(cls, IntelligenceError)

    def test_B2_llm_error_carries_status(self):
        e = LLMRequestError("boom", status_code=502, response_body={"e": "x"})
        assert e.status_code == 502

    def test_B3_reasoning_parse_error_truncates_raw(self):
        e = ReasoningParseError("bad", raw_response="x" * 1000)
        assert len(e.raw_response) == 500

    def test_B4_reasoning_parse_error_missing_fields(self):
        e = ReasoningParseError("bad", missing_fields=["foo", "bar"])
        assert e.missing_fields == ["foo", "bar"]


# ══════════════════════════════════════════════════════════════════════════════
# Section C — Domain models
# ══════════════════════════════════════════════════════════════════════════════

class TestC_Models:
    def test_C1_confidence_level_from_float(self):
        assert ConfidenceLevel.from_float(0.9) == ConfidenceLevel.HIGH
        assert ConfidenceLevel.from_float(0.5) == ConfidenceLevel.MEDIUM
        assert ConfidenceLevel.from_float(0.2) == ConfidenceLevel.LOW
        assert ConfidenceLevel.from_float(None) == ConfidenceLevel.LOW
        assert ConfidenceLevel.from_float("bad") == ConfidenceLevel.LOW

    def test_C2_reasoning_outcome_values(self):
        assert ReasoningOutcome.RESOLVED.value == "RESOLVED"
        assert ReasoningOutcome.NEEDS_CLARIFICATION.value == "NEEDS_CLARIFICATION"

    def test_C3_action_kind_from_str_unknown(self):
        assert ActionKind.from_str("weird") == ActionKind.NO_ACTION
        assert ActionKind.from_str("send_reply") == ActionKind.SEND_REPLY

    def test_C4_risk_level_values(self):
        assert {r.value for r in RiskLevel} == {"SAFE", "MEDIUM", "HIGH", "CRITICAL"}

    def test_C5_llm_context_to_dict_json_safe(self):
        ctx = _ctx()
        json.dumps(ctx.to_dict())

    def test_C6_reasoning_result_to_dict_json_safe(self):
        rr = ReasoningResult(
            outcome=ReasoningOutcome.RESOLVED, summary="ok", root_cause="fixed",
            confidence=0.9, confidence_level=ConfidenceLevel.HIGH,
            evidence_used=(), missing_information=(), clarification_required=False,
        )
        json.dumps(rr.to_dict())

    def test_C7_intelligence_result_to_dict_json_safe(self):
        rr = ReasoningResult(
            outcome=ReasoningOutcome.RESOLVED, summary="ok", root_cause="fixed",
            confidence=0.9, confidence_level=ConfidenceLevel.HIGH,
            evidence_used=(), missing_information=(), clarification_required=False,
        )
        cd = ClarificationDecision(should_clarify=False)
        od = ObservationDraft(
            issue_summary="x", evidence="- e",
            root_cause="y", recommended_action="z",
            escalation="None", confidence_level=ConfidenceLevel.HIGH,
            body_html="<p>ok</p>",
        )
        res = IntelligenceResult(
            case_id="c", ticket_id="t",
            reasoning=rr, clarification=cd, observation=od,
            customer_reply=None,
        )
        json.dumps(res.to_dict())

    def test_C8_retrieved_chunk_truncates_content(self):
        c = RetrievedChunk(chunk_id="x", source="s", title="t", content="a" * 5000, score=0.5)
        assert len(c.to_dict()["content"]) <= 2000

    def test_C9_evidence_hint_shape(self):
        h = EvidenceHint(source="MetricTool", kind="METRIC", summary="all up")
        d = h.to_dict()
        assert d["source"] == "MetricTool"
        assert d["is_available"] is True


# ══════════════════════════════════════════════════════════════════════════════
# Section D — 22 Wave-3 traces
# ══════════════════════════════════════════════════════════════════════════════

class TestD_Traces:
    def test_D1_twenty_two_canonical_tags(self):
        assert len(ALL_WAVE3_TRACES) == 22

    def test_D2_all_expected_tags_present(self):
        # Sample check of first + last + a middle one
        assert TRACE_01_WEBHOOK_RECEIVED in ALL_WAVE3_TRACES
        assert TRACE_16_LLM_REQUEST in ALL_WAVE3_TRACES
        assert TRACE_22_PIPELINE_COMPLETE in ALL_WAVE3_TRACES

    def test_D3_emit_warning_level(self, caplog_wave3):
        emit_wave3_trace(TRACE_01_WEBHOOK_RECEIVED, ticket_id="t-1", tenant="UNITY")
        assert any(r.levelno == logging.WARNING for r in caplog_wave3.records)

    def test_D4_six_kv_fields(self, caplog_wave3):
        emit_wave3_trace(TRACE_16_LLM_REQUEST,
                         ticket_id="t-1", tenant="UNITY", case_id="c-1",
                         stage="reasoning", duration_ms=42, status="OK")
        msg = caplog_wave3.records[-1].getMessage()
        for f in ("ticket_id=t-1", "tenant=UNITY", "case_id=c-1",
                  "stage=reasoning", "duration_ms=42", "status=OK"):
            assert f in msg

    def test_D5_pii_email_redacted(self, caplog_wave3):
        emit_wave3_trace(TRACE_01_WEBHOOK_RECEIVED, tenant="user@bank.co")
        assert "REDACTED" in caplog_wave3.records[-1].getMessage()

    def test_D6_pii_bearer_redacted(self, caplog_wave3):
        emit_wave3_trace(TRACE_01_WEBHOOK_RECEIVED, status="Bearer secret")
        assert "REDACTED" in caplog_wave3.records[-1].getMessage()

    def test_D7_jwt_prefix_redacted(self, caplog_wave3):
        emit_wave3_trace(TRACE_16_LLM_REQUEST, stage="eyJ0eXAiOiJKV1Qi")
        assert "REDACTED" in caplog_wave3.records[-1].getMessage()

    def test_D8_pan_marker_redacted(self, caplog_wave3):
        emit_wave3_trace(TRACE_16_LLM_REQUEST, stage="pancapture")
        assert "REDACTED" in caplog_wave3.records[-1].getMessage()

    def test_D9_long_value_redacted(self, caplog_wave3):
        emit_wave3_trace(TRACE_16_LLM_REQUEST, stage="x" * 200)
        assert "REDACTED" in caplog_wave3.records[-1].getMessage()

    def test_D10_missing_values_render_dash(self, caplog_wave3):
        emit_wave3_trace(TRACE_22_PIPELINE_COMPLETE)
        msg = caplog_wave3.records[-1].getMessage()
        assert "ticket_id=-" in msg

    def test_D11_none_duration_renders_dash(self, caplog_wave3):
        emit_wave3_trace(TRACE_22_PIPELINE_COMPLETE, ticket_id="t-1")
        msg = caplog_wave3.records[-1].getMessage()
        assert "duration_ms=-" in msg

    def test_D12_unknown_tag_no_normal_line(self, caplog_wave3):
        emit_wave3_trace("NOT_A_TAG", ticket_id="t-1")
        assert any("unknown_tag" in r.getMessage() for r in caplog_wave3.records)

    def test_D13_never_raises_on_bad_input(self, caplog_wave3):
        emit_wave3_trace(TRACE_16_LLM_REQUEST, ticket_id=None)  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════════════════
# Section E — Context Builder
# ══════════════════════════════════════════════════════════════════════════════

class TestE_ContextBuilder:
    def test_E1_minimal_build(self):
        ctx = build_llm_context(
            case_id="c-1", ticket_id="t-1", tenant_id="UNITY", topic="X",
        )
        assert ctx.case_id == "c-1"
        assert ctx.evidence_hints == ()

    def test_E2_phone_masking(self):
        ctx = build_llm_context(
            case_id="c", ticket_id="t", tenant_id="UNITY", topic="X",
            customer_phone="7045722923",
        )
        assert ctx.customer_display == "******2923"
        # raw phone must never appear
        assert "7045722923" not in ctx.customer_display

    def test_E3_email_masking(self):
        ctx = build_llm_context(
            case_id="c", ticket_id="t", tenant_id="UNITY", topic="X",
            customer_email="alice@example.com",
        )
        assert ctx.customer_display == "***@example.com"

    def test_E4_both_phone_and_email(self):
        ctx = build_llm_context(
            case_id="c", ticket_id="t", tenant_id="UNITY", topic="X",
            customer_phone="9999900000", customer_email="a@b.com",
        )
        assert "9999900000" not in ctx.customer_display
        assert "@b.com" in ctx.customer_display

    def test_E5_evidence_bundle_reduced_to_hints(self):
        # Mimic a Sprint 2.18 EvidenceBundle: expose `successful_items`
        bundle = MagicMock()
        item = MagicMock()
        item.source = "MetricTool"
        item.evidence_type = "METRIC"
        item.success = True
        item.evidence_id = "ev-1"
        item.payload = {"data_available": "AVAILABLE"}
        bundle.successful_items = [item]
        ctx = build_llm_context(
            case_id="c", ticket_id="t", tenant_id="UNITY", topic="X",
            evidence_bundle=bundle,
        )
        assert len(ctx.evidence_hints) == 1
        assert ctx.evidence_hints[0].source == "MetricTool"
        assert ctx.evidence_hints[0].kind == "METRIC"

    def test_E6_max_chunks_enforced(self):
        chunks = [RetrievedChunk(chunk_id=str(i), source="s", title=f"t{i}",
                                  content="c", score=0.5) for i in range(20)]
        ctx = build_llm_context(
            case_id="c", ticket_id="t", tenant_id="UNITY", topic="X",
            retrieved_chunks=chunks, max_chunks=5,
        )
        assert len(ctx.retrieved_chunks) == 5

    def test_E7_max_conversation_enforced(self):
        convo = [{"role": "user", "content": f"m{i}"} for i in range(30)]
        ctx = build_llm_context(
            case_id="c", ticket_id="t", tenant_id="UNITY", topic="X",
            conversation=convo, max_conversation=10,
        )
        assert len(ctx.conversation) == 10
        # Should be the LAST 10 (most recent)
        assert ctx.conversation[-1]["content"] == "m29"

    def test_E8_invalid_conversation_entries_dropped(self):
        convo = [
            {"role": "user", "content": "ok"},
            {"role": "", "content": "empty role"},
            {"role": "user", "content": ""},
            "not a dict",
        ]
        ctx = build_llm_context(
            case_id="c", ticket_id="t", tenant_id="UNITY", topic="X",
            conversation=convo,
        )
        assert len(ctx.conversation) == 1

    def test_E9_none_bundle_no_hints(self):
        ctx = build_llm_context(
            case_id="c", ticket_id="t", tenant_id="UNITY", topic="X",
            evidence_bundle=None,
        )
        assert ctx.evidence_hints == ()

    def test_E10_deterministic(self):
        kwargs = dict(case_id="c", ticket_id="t", tenant_id="UNITY", topic="X",
                      customer_phone="1234567890")
        a = build_llm_context(**kwargs)
        b = build_llm_context(**kwargs)
        assert a.to_dict() == b.to_dict()


# ══════════════════════════════════════════════════════════════════════════════
# Section F — Prompt Builder templates
# ══════════════════════════════════════════════════════════════════════════════

class TestF_PromptBuilder:
    def test_F1_five_templates(self):
        pb = PromptBuilder()
        v = pb.versions()
        assert set(v.keys()) == {
            "reasoning", "clarification", "observation",
            "customer_reply", "action_proposal",
        }

    def test_F2_reasoning_pair_has_version(self):
        pair = ReasoningPromptTemplate.build(_ctx())
        assert pair.template_name == "reasoning"
        assert pair.prompt_version == "1.0.0"
        assert "JSON" in pair.system.upper()

    def test_F3_clarification_pair(self):
        pair = ClarificationPromptTemplate.build(_ctx())
        assert pair.template_name == "clarification"
        assert "should_clarify" in pair.system

    def test_F4_observation_pair(self):
        pair = ObservationPromptTemplate.build(_ctx())
        assert pair.template_name == "observation"
        assert "body_html" in pair.system

    def test_F5_customer_reply_pair(self):
        pair = CustomerReplyPromptTemplate.build(_ctx())
        assert pair.template_name == "customer_reply"
        assert "KwikID Support Team" in pair.system

    def test_F6_action_proposal_pair(self):
        pair = ActionProposalPromptTemplate.build(_ctx())
        assert pair.template_name == "action_proposal"
        assert "proposals" in pair.system

    def test_F7_deterministic_output(self):
        # Same context → identical prompt pair
        ctx = _ctx()
        a = ReasoningPromptTemplate.build(ctx)
        b = ReasoningPromptTemplate.build(ctx)
        assert a.system == b.system
        assert a.user == b.user

    def test_F8_evidence_rendered_in_prompt(self):
        ctx = _ctx(evidence_hints=(
            EvidenceHint(source="UnityTool", kind="SESSION", summary="approved"),
        ))
        pair = ReasoningPromptTemplate.build(ctx)
        assert "UnityTool" in pair.user
        assert "approved" in pair.user

    def test_F9_chunks_rendered_in_prompt(self):
        ctx = _ctx(retrieved_chunks=(
            RetrievedChunk(chunk_id="s1", source="stack", title="How to fix",
                           content="Do X then Y.", score=0.9),
        ))
        pair = ObservationPromptTemplate.build(ctx)
        assert "How to fix" in pair.user
        assert "Do X" in pair.user

    def test_F10_no_string_concat_in_runtime(self):
        """The builder returns a PromptPair; no manual assembly elsewhere."""
        pb = PromptBuilder()
        pair = pb.reasoning(_ctx())
        assert isinstance(pair, PromptPair)


# ══════════════════════════════════════════════════════════════════════════════
# Section G — Reasoning parser (schema-strict)
# ══════════════════════════════════════════════════════════════════════════════

class TestG_ReasoningParser:
    def test_G1_valid_reasoning(self):
        raw = json.dumps({
            "outcome": "RESOLVED", "summary": "ok", "root_cause": "y",
            "confidence": 0.9, "evidence_used": ["ev-1"],
            "missing_information": [], "clarification_required": False,
        })
        rr = parse_reasoning_result(raw, prompt_version="1.0.0", llm_model="m")
        assert rr.outcome == ReasoningOutcome.RESOLVED
        assert rr.confidence == 0.9
        assert rr.confidence_level == ConfidenceLevel.HIGH

    def test_G2_missing_field_raises(self):
        raw = json.dumps({"outcome": "RESOLVED", "summary": "x"})  # missing lots
        with pytest.raises(ReasoningParseError) as exc:
            parse_reasoning_result(raw)
        assert "root_cause" in exc.value.missing_fields

    def test_G3_confidence_clamped(self):
        raw = json.dumps({
            "outcome": "RESOLVED", "summary": "x", "root_cause": "y",
            "confidence": 2.5, "evidence_used": [], "missing_information": [],
            "clarification_required": False,
        })
        rr = parse_reasoning_result(raw)
        assert rr.confidence == 1.0

    def test_G4_unknown_outcome_falls_back(self):
        raw = json.dumps({
            "outcome": "WEIRD_OUTCOME", "summary": "x", "root_cause": "y",
            "confidence": 0.5, "evidence_used": [], "missing_information": [],
            "clarification_required": False,
        })
        rr = parse_reasoning_result(raw)
        assert rr.outcome == ReasoningOutcome.INSUFFICIENT_EVIDENCE

    def test_G5_code_fenced_json_accepted(self):
        raw = "```json\n" + json.dumps({
            "outcome": "RESOLVED", "summary": "x", "root_cause": "y",
            "confidence": 0.8, "evidence_used": [], "missing_information": [],
            "clarification_required": False,
        }) + "\n```"
        rr = parse_reasoning_result(raw)
        assert rr.outcome == ReasoningOutcome.RESOLVED

    def test_G6_json_with_surrounding_prose_extracted(self):
        raw = "Here is the answer:\n{" \
              '"outcome":"RESOLVED","summary":"x","root_cause":"y",' \
              '"confidence":0.8,"evidence_used":[],"missing_information":[],' \
              '"clarification_required":false}\nEnd.'
        rr = parse_reasoning_result(raw)
        assert rr.outcome == ReasoningOutcome.RESOLVED

    def test_G7_empty_raw_raises(self):
        with pytest.raises(ReasoningParseError):
            parse_reasoning_result("")

    def test_G8_non_json_raises(self):
        with pytest.raises(ReasoningParseError):
            parse_reasoning_result("this is definitely not JSON")

    def test_G9_clarification_parse(self):
        raw = json.dumps({
            "should_clarify": True,
            "questions": ["q1", "q2"],
            "reason": "need info",
            "required_slots": ["session_id"],
        })
        d = parse_clarification_decision(raw)
        assert d.should_clarify is True
        assert d.questions == ("q1", "q2")

    def test_G10_clarification_caps_at_3_questions(self):
        raw = json.dumps({
            "should_clarify": True,
            "questions": ["q1", "q2", "q3", "q4", "q5"],
        })
        d = parse_clarification_decision(raw)
        assert len(d.questions) == 3

    def test_G11_observation_parse(self):
        raw = json.dumps({
            "issue_summary": "x", "evidence": "- e1", "root_cause": "y",
            "recommended_action": "z", "escalation": "None",
            "confidence_level": "HIGH", "body_html": "<p>ok</p>",
        })
        od = parse_observation_draft(raw)
        assert od.confidence_level == ConfidenceLevel.HIGH
        assert od.body_html == "<p>ok</p>"

    def test_G12_customer_reply_parse(self):
        raw = json.dumps({
            "reply_kind": "resolution",
            "body_html": "<p>done</p>",
            "confidence_level": "HIGH",
            "confidence": 0.9,
            "citations": ["https://s.example/1"],
        })
        rd = parse_customer_reply(raw)
        assert rd.reply_kind == "resolution"
        assert rd.citations == ("https://s.example/1",)

    def test_G13_customer_reply_bogus_kind_defaults_to_clarification(self):
        raw = json.dumps({
            "reply_kind": "bogus", "body_html": "<p>x</p>",
            "confidence_level": "LOW", "confidence": 0.3,
        })
        rd = parse_customer_reply(raw)
        assert rd.reply_kind == "clarification"

    def test_G14_action_proposals_parse(self):
        raw = json.dumps({
            "proposals": [
                {"action_kind": "SEND_REPLY", "parameters": {},
                 "confidence": 0.9, "risk": "MEDIUM",
                 "approval_required": False, "rationale": "ok"},
                {"action_kind": "RESOLVE_TICKET", "parameters": {},
                 "confidence": 0.85, "risk": "MEDIUM",
                 "approval_required": False, "rationale": "ok"},
            ]
        })
        ps = parse_action_proposals(raw)
        assert len(ps) == 2
        assert ps[0].action_kind == ActionKind.SEND_REPLY

    def test_G15_action_proposal_critical_forces_approval(self):
        raw = json.dumps({
            "proposals": [{
                "action_kind": "UPDATE_TICKET_FIELDS", "parameters": {},
                "confidence": 0.7, "risk": "CRITICAL",
                "approval_required": False,   # LLM said false, but risk=CRITICAL
                "rationale": "test",
            }]
        })
        ps = parse_action_proposals(raw)
        assert ps[0].approval_required is True

    def test_G16_action_proposals_empty_raises(self):
        raw = json.dumps({"proposals": []})
        with pytest.raises(ReasoningParseError):
            parse_action_proposals(raw)


# ══════════════════════════════════════════════════════════════════════════════
# Section H — LLM client
# ══════════════════════════════════════════════════════════════════════════════

class TestH_MockLLMClient:
    def test_H1_default_reasoning_response_is_valid_json(self):
        client = MockLLMClient()
        # Trigger the reasoning branch synchronously via asyncio
        raw = asyncio.run(client.complete_json(
            system="You are the KwikID support-automation Reasoning Engine.",
            user="test",
        ))
        obj = json.loads(raw)
        assert "outcome" in obj

    def test_H2_calls_history_recorded(self):
        client = MockLLMClient()
        asyncio.run(client.complete_json(system="s", user="u"))
        assert len(client.calls) == 1
        assert client.calls[0]["system"] == "s"

    def test_H3_custom_responses_override(self):
        custom = json.dumps({"custom": "yes"})
        client = MockLLMClient(responses={"marker_string": custom})
        raw = asyncio.run(client.complete_json(
            system="marker_string here", user="u",
        ))
        assert raw == custom

    def test_H4_model_property(self):
        client = MockLLMClient(model_name="unit-test-model")
        assert client.model == "unit-test-model"


@pytest.mark.asyncio
class TestH_OpenAILLMClient:
    async def test_H5_missing_api_key_raises_config_error(self):
        cfg = _cfg(llm_provider="openai", llm_api_key="")
        with pytest.raises(IntelligenceConfigError):
            OpenAILLMClient(cfg)

    async def test_H6_success_response(self):
        cfg = _cfg(llm_provider="openai", llm_api_key="sk-test")
        def h(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=json.dumps({
                    "choices": [{"message": {"content": '{"ok":true}'}}]
                }).encode(),
                headers={"content-type": "application/json"},
            )
        client = OpenAILLMClient(cfg, http_client=httpx.AsyncClient(
            base_url=cfg.normalized_base_url, transport=httpx.MockTransport(h),
        ))
        try:
            raw = await client.complete_json(system="s", user="u")
        finally:
            await client.close()
        assert json.loads(raw) == {"ok": True}

    async def test_H7_401_raises_auth(self):
        cfg = _cfg(llm_provider="openai", llm_api_key="sk-test", llm_max_retries=0)
        def h(req: httpx.Request) -> httpx.Response:
            return httpx.Response(401, content=b'{"error":"unauthorized"}')
        client = OpenAILLMClient(cfg, http_client=httpx.AsyncClient(
            base_url=cfg.normalized_base_url, transport=httpx.MockTransport(h),
        ))
        try:
            with pytest.raises(LLMAuthError):
                await client.complete_json(system="s", user="u")
        finally:
            await client.close()

    async def test_H8_429_retries_then_raises(self):
        cfg = _cfg(llm_provider="openai", llm_api_key="sk-test", llm_max_retries=1)
        def h(req: httpx.Request) -> httpx.Response:
            return httpx.Response(429, content=b'{"error":"rate"}')
        # Speed up retry by patching backoff wait
        client = OpenAILLMClient(cfg, http_client=httpx.AsyncClient(
            base_url=cfg.normalized_base_url, transport=httpx.MockTransport(h),
        ))
        client._backoff = lambda _a: 0.0  # type: ignore[method-assign]
        try:
            with pytest.raises(LLMRateLimitError):
                await client.complete_json(system="s", user="u")
        finally:
            await client.close()

    async def test_H9_500_retries_then_raises(self):
        cfg = _cfg(llm_provider="openai", llm_api_key="sk-test", llm_max_retries=1)
        def h(req: httpx.Request) -> httpx.Response:
            return httpx.Response(503, content=b'{"error":"unavail"}')
        client = OpenAILLMClient(cfg, http_client=httpx.AsyncClient(
            base_url=cfg.normalized_base_url, transport=httpx.MockTransport(h),
        ))
        client._backoff = lambda _a: 0.0  # type: ignore[method-assign]
        try:
            with pytest.raises(LLMServerError):
                await client.complete_json(system="s", user="u")
        finally:
            await client.close()

    async def test_H10_malformed_body_raises(self):
        cfg = _cfg(llm_provider="openai", llm_api_key="sk-test", llm_max_retries=0)
        def h(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not json",
                                  headers={"content-type": "text/plain"})
        client = OpenAILLMClient(cfg, http_client=httpx.AsyncClient(
            base_url=cfg.normalized_base_url, transport=httpx.MockTransport(h),
        ))
        try:
            with pytest.raises(LLMRequestError):
                await client.complete_json(system="s", user="u")
        finally:
            await client.close()

    async def test_H11_no_choices_raises(self):
        cfg = _cfg(llm_provider="openai", llm_api_key="sk-test", llm_max_retries=0)
        def h(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b'{"choices":[]}',
                                  headers={"content-type": "application/json"})
        client = OpenAILLMClient(cfg, http_client=httpx.AsyncClient(
            base_url=cfg.normalized_base_url, transport=httpx.MockTransport(h),
        ))
        try:
            with pytest.raises(LLMRequestError):
                await client.complete_json(system="s", user="u")
        finally:
            await client.close()


class TestH_Factory:
    def test_H12_build_llm_client_mock(self):
        c = build_llm_client(_cfg(llm_provider="mock"))
        assert isinstance(c, MockLLMClient)

    def test_H13_build_llm_client_openai(self):
        c = build_llm_client(_cfg(llm_provider="openai", llm_api_key="sk-x"))
        assert isinstance(c, OpenAILLMClient)


# ══════════════════════════════════════════════════════════════════════════════
# Section I — Orchestrator end-to-end
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestI_Orchestrator:
    async def test_I1_full_run_returns_result(self, caplog_wave3):
        cfg = _cfg()
        client = MockLLMClient()
        ctx = _ctx()
        async with IntelligenceOrchestrator(cfg, llm_client=client) as orch:
            result = await orch.orchestrate(ctx)
        assert isinstance(result, IntelligenceResult)
        assert result.case_id == "c-1"

    async def test_I2_all_traces_emitted(self, caplog_wave3):
        cfg = _cfg()
        client = MockLLMClient()
        ctx = _ctx()
        async with IntelligenceOrchestrator(cfg, llm_client=client) as orch:
            await orch.orchestrate(ctx)
        seen = {r.getMessage().split()[0] for r in caplog_wave3.records}
        # All 10 orchestrator-level traces (13,14,15,16,17,18,19,20,21,22) fire
        expected = {
            "TRACE_13_CONTEXT_BUILDER", "TRACE_14_PROMPT_BUILDER",
            "TRACE_15_HYBRID_RAG", "TRACE_16_LLM_REQUEST", "TRACE_17_LLM_RESPONSE",
            "TRACE_18_REASONING_COMPLETE", "TRACE_19_OBSERVATION_GENERATED",
            "TRACE_20_CUSTOMER_REPLY_GENERATED", "TRACE_21_ACTION_PROPOSAL",
            "TRACE_22_PIPELINE_COMPLETE",
        }
        for tag in expected:
            assert tag in seen, f"missing trace: {tag}"

    async def test_I3_five_llm_calls_default_path(self):
        cfg = _cfg()
        client = MockLLMClient()
        ctx = _ctx()
        async with IntelligenceOrchestrator(cfg, llm_client=client) as orch:
            await orch.orchestrate(ctx)
        # reasoning + clarification + observation + reply + proposals = 5
        assert len(client.calls) == 5

    async def test_I4_disabled_raises(self):
        cfg = _cfg(enabled=False)
        client = MockLLMClient()
        ctx = _ctx()
        async with IntelligenceOrchestrator(cfg, llm_client=client) as orch:
            with pytest.raises(IntelligenceDisabled):
                await orch.orchestrate(ctx)

    async def test_I5_high_confidence_skips_clarification_call(self):
        """When reasoning is confident, orchestrator MUST NOT call the LLM again for clarification."""
        cfg = _cfg(confidence_threshold=0.75)
        # Return a HIGH-confidence resolved verdict
        high_conf = json.dumps({
            "outcome": "RESOLVED", "summary": "ok", "root_cause": "y",
            "confidence": 0.9, "evidence_used": [], "missing_information": [],
            "clarification_required": False,
        })
        client = MockLLMClient(responses={
            "Reasoning Engine": high_conf,
        })
        ctx = _ctx()
        async with IntelligenceOrchestrator(cfg, llm_client=client) as orch:
            result = await orch.orchestrate(ctx)
        assert result.reasoning.outcome == ReasoningOutcome.RESOLVED
        assert result.clarification.should_clarify is False
        # reasoning + observation + reply + proposals = 4 (clarification skipped)
        assert len(client.calls) == 4

    async def test_I6_result_has_llm_model(self):
        cfg = _cfg()
        client = MockLLMClient(model_name="test-model-v2")
        ctx = _ctx()
        async with IntelligenceOrchestrator(cfg, llm_client=client) as orch:
            result = await orch.orchestrate(ctx)
        assert result.llm_model == "test-model-v2"

    async def test_I7_result_has_duration(self):
        cfg = _cfg()
        client = MockLLMClient()
        ctx = _ctx()
        async with IntelligenceOrchestrator(cfg, llm_client=client) as orch:
            result = await orch.orchestrate(ctx)
        assert result.duration_ms >= 0

    async def test_I8_bad_llm_reasoning_response_produces_fallback(self):
        cfg = _cfg()
        client = MockLLMClient(responses={
            "Reasoning Engine": "not json at all",
        })
        ctx = _ctx()
        async with IntelligenceOrchestrator(cfg, llm_client=client) as orch:
            result = await orch.orchestrate(ctx)
        # Fallback still produces a valid ReasoningResult
        assert result.reasoning.outcome == ReasoningOutcome.INSUFFICIENT_EVIDENCE
        assert result.reasoning.confidence == 0.0

    async def test_I9_customer_reply_none_when_insufficient_evidence_no_clarify(self):
        cfg = _cfg()
        insuf = json.dumps({
            "outcome": "INSUFFICIENT_EVIDENCE", "summary": "no evidence",
            "root_cause": "unknown", "confidence": 0.2,
            "evidence_used": [], "missing_information": [],
            "clarification_required": False,
        })
        clar = json.dumps({
            "should_clarify": False, "questions": [], "reason": "no info to gather",
        })
        client = MockLLMClient(responses={
            "Reasoning Engine": insuf,
            "clarification request": clar,
        })
        ctx = _ctx()
        async with IntelligenceOrchestrator(cfg, llm_client=client) as orch:
            result = await orch.orchestrate(ctx)
        assert result.customer_reply is None

    async def test_I10_result_json_serializable(self):
        cfg = _cfg()
        client = MockLLMClient()
        ctx = _ctx()
        async with IntelligenceOrchestrator(cfg, llm_client=client) as orch:
            result = await orch.orchestrate(ctx)
        json.dumps(result.to_dict())   # must not raise


# ══════════════════════════════════════════════════════════════════════════════
# Section J — Metrics password-only auth (Part J fix)
# ══════════════════════════════════════════════════════════════════════════════

class TestJ_MetricsPasswordOnlyAuth:
    def _mk(self, **overrides):
        kwargs = dict(
            base_url="https://kuma.test:3001", api_key="",
            timeout_s=5, max_retries=0,
            default_slug="kwikid", auth_mode="bearer",
            user_agent="test/2.53",
            enabled=True,
        )
        kwargs.update(overrides)
        return _MPC(**kwargs)

    def test_J1_password_only_is_valid(self):
        cfg = self._mk(prometheus_username="", prometheus_password="pw")
        assert cfg.has_prometheus_credentials is True

    def test_J2_username_only_is_invalid(self):
        cfg = self._mk(prometheus_username="scraper", prometheus_password="")
        assert cfg.has_prometheus_credentials is False

    def test_J3_effective_auth_mode_password_only(self):
        cfg = self._mk(prometheus_username="", prometheus_password="pw")
        assert cfg.effective_auth_mode == "basic"

    def test_J4_both_set_still_works(self):
        cfg = self._mk(prometheus_username="scraper", prometheus_password="pw")
        assert cfg.has_prometheus_credentials is True

    def test_J5_neither_set_falls_back(self):
        cfg = self._mk()
        assert cfg.has_prometheus_credentials is False
        assert cfg.effective_auth_mode == "bearer"


# ══════════════════════════════════════════════════════════════════════════════
# Section K — Public export surface
# ══════════════════════════════════════════════════════════════════════════════

class TestK_Exports:
    def test_K1_intelligence_public_names(self):
        import intelligence
        for name in (
            "IntelligenceConfig", "IntelligenceOrchestrator",
            "LLMClient", "OpenAILLMClient", "MockLLMClient", "build_llm_client",
            "PromptBuilder", "PromptPair",
            "ReasoningPromptTemplate", "ClarificationPromptTemplate",
            "ObservationPromptTemplate", "CustomerReplyPromptTemplate",
            "ActionProposalPromptTemplate",
            "build_llm_context",
            "parse_reasoning_result", "parse_clarification_decision",
            "parse_observation_draft", "parse_customer_reply",
            "parse_action_proposals",
            "LLMContext", "RetrievedChunk", "EvidenceHint",
            "ReasoningResult", "ReasoningOutcome", "ClarificationDecision",
            "ObservationDraft", "CustomerReplyDraft",
            "ActionProposal", "ActionKind", "RiskLevel", "ConfidenceLevel",
            "IntelligenceResult",
            "emit_wave3_trace", "ALL_WAVE3_TRACES",
            "IntelligenceError", "IntelligenceDisabled",
            "IntelligenceConfigError",
            "LLMRequestError", "LLMTimeoutError", "LLMAuthError",
            "LLMRateLimitError", "LLMServerError",
            "ReasoningParseError", "ContextBuildError",
        ):
            assert hasattr(intelligence, name), f"missing export: {name}"

    def test_K2_all_list_is_consistent(self):
        import intelligence
        for name in intelligence.__all__:
            assert hasattr(intelligence, name), f"__all__ lists missing: {name}"
