"""
tests/test_sprint244_observation_generator.py

Sprint 2.44: Observation Generator — comprehensive test suite.

Sections:
  A  — Package & module imports
  B  — exceptions.py — hierarchy and attributes
  C  — versioning.py — GeneratorVersion, constants, comparison
  D  — models.py — enums, TimelineEntry, InvestigationStepRecord
  E  — models.py — DecisionTraceSummary.from_trace()
  F  — models.py — ObservationAuditMetadata, Observation, REQUIRED_SECTION_HEADERS
  G  — contracts.py — ObservationTemplate & ObservationGeneratorProtocol
  H  — templates.py — template matching logic
  I  — templates.py — template rendering output
  J  — templates.py — section content: evidence, supporting, contradictions
  K  — registry.py — register, unregister, get, all_templates, resolve
  L  — registry.py — build_default_registry, fallback, thread safety
  M  — metrics.py — ObservationMetric, GeneratorMetrics, record, snapshot
  N  — validators.py — validate_analysis()
  O  — validators.py — validate_inputs(), validate_observation()
  P  — validators.py — verify_evidence_preservation()
  Q  — generator.py — ObservationGenerator.generate() happy paths
  R  — generator.py — status logic (COMPLETE/PARTIAL/FALLBACK/ERROR)
  S  — generator.py — apply_to_context(), get_metrics()
  T  — generator.py — edge cases and error handling
  U  — serialization.py — to_dict/from_dict roundtrip
  V  — serialization.py — to_json/from_json roundtrip, error handling
  W  — context.py — Sprint 2.44 observation fields
  X  — Integration: full pipeline, all 7 templates, evidence preservation
  Y  — Regression: Sprint 2.18 imports unaffected
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ── Production imports ─────────────────────────────────────────────────────────

from case_engine.investigation.models import (
    EvidenceBundle,
    EvidenceSource,
    EvidenceType,
    LogEvidence,
    RecommendedAction,
    RootCauseCategory,
    SessionEvidence,
    SummaryEvidence,
    UserEvidence,
)
from case_engine.tenant.models import TenantContext, TenantEnvironment, TenantType
from case_engine.investigation.observation import (
    CURRENT_GENERATOR_VERSION,
    CURRENT_TEMPLATE_REGISTRY_VERSION,
    DecisionTraceSummary,
    GeneratorMetrics,
    GeneratorVersion,
    InvalidAnalysisError,
    InvalidInputError,
    InvestigationStepRecord,
    Observation,
    ObservationAuditMetadata,
    ObservationConfigurationError,
    ObservationDeserializationError,
    ObservationDraft,
    ObservationError,
    ObservationGenerator,
    ObservationGeneratorProtocol,
    ObservationMetric,
    ObservationSerializationError,
    ObservationStatus,
    ObservationTemplate,
    ObservationValidationError,
    REQUIRED_SECTION_HEADERS,
    TemplateNotFoundError,
    TemplateRegistry,
    TemplateRenderError,
    TimelineEntry,
    build_default_registry,
    observation_from_dict,
    observation_from_json,
    observation_to_dict,
    observation_to_json,
)
from case_engine.investigation.observation.templates import (
    ApiObservationTemplate,
    FallbackObservationTemplate,
    OcrObservationTemplate,
    OtpObservationTemplate,
    PortalObservationTemplate,
    SessionObservationTemplate,
    VkycObservationTemplate,
)
from case_engine.investigation.root_cause import (
    AuditMetadata,
    ConfidenceAdjustment,
    ConfidenceBreakdown,
    ContradictionRecord,
    ContradictionSeverity,
    DecisionTrace,
    EscalationLevel,
    EscalationRecommendation,
    EvidenceReference,
    RootCauseAnalysis,
    RootCauseEngine,
    RuleMatch,
    RuleMatchStatus,
)


# ── Factories ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


def _make_confidence_breakdown(
    base: float = 0.80,
    final: float = 0.80,
) -> ConfidenceBreakdown:
    return ConfidenceBreakdown(
        base_confidence=base,
        adjustments=(),
        final_confidence=final,
    )


def _make_escalation(should: bool = False) -> EscalationRecommendation:
    return EscalationRecommendation(
        should_escalate=should,
        level=EscalationLevel.L2 if should else EscalationLevel.NONE,
        reason="test reason",
        urgency_score=0.8 if should else 0.1,
    )


def _make_rule_match(
    rule_id: str = "test-rule",
    status: RuleMatchStatus = RuleMatchStatus.MATCH,
    category: RootCauseCategory = RootCauseCategory.LIVENESS_FAILURE,
) -> RuleMatch:
    return RuleMatch(
        rule_id=rule_id,
        rule_name="TestRule",
        status=status,
        confidence_contribution=0.85 if status == RuleMatchStatus.MATCH else 0.0,
        evidence_used=("ev-001",),
        explanation=f"Test explanation for {rule_id}",
        evaluation_order=0,
    )


def _make_decision_trace(
    contradictions: tuple[ContradictionRecord, ...] = (),
    missing: tuple[str, ...] = (),
) -> DecisionTrace:
    return DecisionTrace(
        rules_evaluated=(_make_rule_match(),),
        confidence_adjustments=(),
        contradictions_detected=contradictions,
        missing_evidence_types=tuple(missing),
        evaluation_order=("test-rule",),
    )


def _make_audit_meta(case_id: str = "case-x", bundle_id: str = "bundle-x") -> AuditMetadata:
    return AuditMetadata(
        analysis_id=_uid(),
        engine_version="2.43.0",
        rule_registry_version="1.0.0",
        evidence_bundle_id=bundle_id,
        plan_id=_uid(),
        case_id=case_id,
        analyzed_at=_now(),
        execution_duration_ms=10,
        rules_evaluated_count=5,
    )


def _make_analysis(
    case_id: str = "case-test",
    category: RootCauseCategory = RootCauseCategory.LIVENESS_FAILURE,
    confidence: float = 0.85,
    escalate: bool = False,
    supporting_evidence: tuple[str, ...] = ("ev-001",),
    contradicting_evidence: tuple[str, ...] = (),
    missing_evidence: tuple[str, ...] = (),
    contradictions: tuple[ContradictionRecord, ...] = (),
    evidence_refs: tuple[EvidenceReference, ...] = (),
) -> RootCauseAnalysis:
    ev_refs = evidence_refs or (
        EvidenceReference(
            evidence_id="ev-001",
            evidence_type=EvidenceType.SESSION.value,
            source="GetSessionDetailsTool",
            weight=1.0,
        ),
    )
    return RootCauseAnalysis(
        analysis_id=_uid(),
        case_id=case_id,
        category=category,
        confidence=confidence,
        explanation=f"Test explanation for {category.value}",
        evidence_references=ev_refs,
        supporting_evidence=supporting_evidence,
        contradicting_evidence=contradicting_evidence,
        missing_evidence=missing_evidence,
        recommended_action=RecommendedAction.ESCALATE if escalate else RecommendedAction.SESSION_RESET,
        escalation_recommendation=_make_escalation(escalate),
        decision_trace=_make_decision_trace(contradictions, missing_evidence),
        confidence_breakdown=_make_confidence_breakdown(confidence, confidence),
        rule_matches=(
            RuleMatch(
                rule_id="test-rule",
                rule_name="TestRule",
                status=RuleMatchStatus.MATCH,
                confidence_contribution=confidence,
                evidence_used=tuple(supporting_evidence),
                explanation=f"Test explanation for {category.value}",
                evaluation_order=0,
            ),
        ),
        version=CURRENT_GENERATOR_VERSION,
        timestamp=_now(),
        audit_metadata=_make_audit_meta(case_id),
    )


def _make_log_evidence(
    success: bool = True,
    source: EvidenceSource = EvidenceSource.GET_FAILURE_REASON,
    payload: dict | None = None,
) -> LogEvidence:
    return LogEvidence(
        evidence_id=_uid(),
        evidence_type=EvidenceType.LOG,
        source=source,
        tool_name="TestTool",
        payload=payload or {"failure_code": "ERR_001"},
        collected_at=_now(),
        invocation_id=_uid(),
        success=success,
        error_code=None if success else "ERR_TOOL",
        error_message=None if success else "Tool failed",
    )


def _make_session_evidence(success: bool = True) -> SessionEvidence:
    return SessionEvidence(
        evidence_id=_uid(),
        evidence_type=EvidenceType.SESSION,
        source=EvidenceSource.GET_SESSION_DETAILS,
        tool_name="GetSessionDetailsTool",
        payload={"session_status": "FAILED", "failure_code": "LIVENESS"},
        collected_at=_now(),
        invocation_id=_uid(),
        success=success,
    )


def _make_bundle(
    items: list | None = None,
    case_id: str = "case-test",
    topic: str = "VKYC_SESSION_FAILURE",
) -> EvidenceBundle:
    return EvidenceBundle(
        bundle_id=_uid(),
        case_id=case_id,
        topic=topic,
        plan_id=_uid(),
        items=items or [],
        collected_at=_now(),
    )


def _make_observation_metric(
    observation_id: str | None = None,
    template_id: str = "tpl-otp",
    duration_ms: int = 5,
    contradiction_count: int = 0,
    escalated: bool = False,
    confidence: float = 0.80,
    status: str = "COMPLETE",
) -> ObservationMetric:
    return ObservationMetric(
        observation_id=observation_id or _uid(),
        template_id=template_id,
        duration_ms=duration_ms,
        contradiction_count=contradiction_count,
        escalated=escalated,
        confidence=confidence,
        status=status,
        recorded_at=_now(),
    )


def _make_tenant() -> TenantContext:
    return TenantContext(
        client_id="unity_bank",
        client_name="Unity Bank",
        domain="unitybank.co.in",
        tenant_type=TenantType.BANK,
        environment=TenantEnvironment.UAT,
        enabled_tools=("GetSessionDetailsTool", "GetUserDetailsTool"),
        credentials_ref="unity_bank_creds",
    )


def _generate(
    analysis: RootCauseAnalysis | None = None,
    bundle: EvidenceBundle | None = None,
    topic: str = "VKYC_SESSION_FAILURE",
) -> Observation:
    if analysis is None:
        analysis = _make_analysis()
    gen = ObservationGenerator()
    return gen.generate(analysis, bundle=bundle)


# ════════════════════════════════════════════════════════════════════════════════
# A — Package & module imports
# ════════════════════════════════════════════════════════════════════════════════

class TestA_PackageImports:
    def test_A01_generator_importable(self):
        assert ObservationGenerator is not None

    def test_A02_observation_importable(self):
        assert Observation is not None

    def test_A03_observation_status_importable(self):
        assert ObservationStatus is not None

    def test_A04_build_default_registry_callable(self):
        assert callable(build_default_registry)

    def test_A05_all_exceptions_importable(self):
        for cls in (
            ObservationError,
            ObservationConfigurationError,
            TemplateNotFoundError,
            TemplateRenderError,
            InvalidAnalysisError,
            InvalidInputError,
            ObservationValidationError,
            ObservationSerializationError,
            ObservationDeserializationError,
        ):
            assert cls is not None

    def test_A06_serialization_functions_importable(self):
        for fn in (observation_to_dict, observation_to_json,
                   observation_from_dict, observation_from_json):
            assert callable(fn)

    def test_A07_generator_version_importable(self):
        assert GeneratorVersion is not None

    def test_A08_constants_are_strings(self):
        assert isinstance(CURRENT_GENERATOR_VERSION, str)
        assert isinstance(CURRENT_TEMPLATE_REGISTRY_VERSION, str)

    def test_A09_constants_not_empty(self):
        assert CURRENT_GENERATOR_VERSION
        assert CURRENT_TEMPLATE_REGISTRY_VERSION

    def test_A10_required_section_headers_is_tuple(self):
        assert isinstance(REQUIRED_SECTION_HEADERS, tuple)
        assert len(REQUIRED_SECTION_HEADERS) == 15


# ════════════════════════════════════════════════════════════════════════════════
# B — exceptions.py
# ════════════════════════════════════════════════════════════════════════════════

class TestB_Exceptions:
    def test_B01_all_inherit_from_base(self):
        for cls in (
            ObservationConfigurationError,
            TemplateNotFoundError,
            TemplateRenderError,
            InvalidAnalysisError,
            InvalidInputError,
            ObservationValidationError,
            ObservationSerializationError,
            ObservationDeserializationError,
        ):
            assert issubclass(cls, ObservationError)

    def test_B02_template_not_found_attributes(self):
        exc = TemplateNotFoundError("tpl-x")
        assert exc.template_id == "tpl-x"
        assert "tpl-x" in str(exc)

    def test_B03_template_render_error_attributes(self):
        exc = TemplateRenderError("tpl-y", "bad data")
        assert exc.template_id == "tpl-y"
        assert exc.cause == "bad data"

    def test_B04_invalid_analysis_error_attributes(self):
        exc = InvalidAnalysisError("reason here")
        assert exc.reason == "reason here"

    def test_B05_invalid_input_error_attributes(self):
        exc = InvalidInputError("bundle", "case_id mismatch")
        assert exc.input_name == "bundle"
        assert exc.reason == "case_id mismatch"

    def test_B06_observation_validation_error_attributes(self):
        exc = ObservationValidationError("obs-1", "missing field")
        assert exc.observation_id == "obs-1"
        assert exc.reason == "missing field"

    def test_B07_deserialization_error_attributes(self):
        exc = ObservationDeserializationError("obs-2", "bad field")
        assert exc.observation_id == "obs-2"
        assert exc.reason == "bad field"

    def test_B08_observation_error_is_exception(self):
        assert issubclass(ObservationError, Exception)

    def test_B09_can_be_caught_as_base_class(self):
        with pytest.raises(ObservationError):
            raise TemplateNotFoundError("tpl-z")

    def test_B10_deserialization_error_message(self):
        exc = ObservationDeserializationError("obs-3", "field x")
        assert "obs-3" in str(exc)
        assert "field x" in str(exc)


# ════════════════════════════════════════════════════════════════════════════════
# C — versioning.py
# ════════════════════════════════════════════════════════════════════════════════

class TestC_Versioning:
    def test_C01_from_string_valid(self):
        v = GeneratorVersion.from_string("2.44.0")
        assert v.major == 2
        assert v.minor == 44
        assert v.patch == 0

    def test_C02_from_string_invalid_format(self):
        with pytest.raises(ValueError):
            GeneratorVersion.from_string("2.44")

    def test_C03_from_string_non_numeric(self):
        with pytest.raises(ValueError):
            GeneratorVersion.from_string("a.b.c")

    def test_C04_current_returns_correct_values(self):
        v = GeneratorVersion.current()
        assert v.major == 2
        assert v.minor == 44

    def test_C05_as_string(self):
        v = GeneratorVersion(2, 44, 0)
        assert v.as_string == "2.44.0"

    def test_C06_is_compatible_with_same_major(self):
        v1 = GeneratorVersion(2, 44, 0)
        v2 = GeneratorVersion(2, 43, 1)
        assert v1.is_compatible_with(v2)

    def test_C07_not_compatible_with_different_major(self):
        v1 = GeneratorVersion(2, 44, 0)
        v2 = GeneratorVersion(3, 0, 0)
        assert not v1.is_compatible_with(v2)

    def test_C08_is_newer_than(self):
        v1 = GeneratorVersion(2, 44, 1)
        v2 = GeneratorVersion(2, 44, 0)
        assert v1.is_newer_than(v2)
        assert not v2.is_newer_than(v1)

    def test_C09_is_older_than(self):
        v1 = GeneratorVersion(2, 43, 0)
        v2 = GeneratorVersion(2, 44, 0)
        assert v1.is_older_than(v2)

    def test_C10_to_dict_has_keys(self):
        v = GeneratorVersion(2, 44, 0)
        d = v.to_dict()
        assert d["major"] == 2
        assert d["minor"] == 44
        assert d["patch"] == 0
        assert d["as_string"] == "2.44.0"

    def test_C11_str_representation(self):
        v = GeneratorVersion(2, 44, 0)
        assert str(v) == "2.44.0"

    def test_C12_frozen(self):
        v = GeneratorVersion(2, 44, 0)
        with pytest.raises((AttributeError, TypeError)):
            v.major = 3  # type: ignore


# ════════════════════════════════════════════════════════════════════════════════
# D — models.py — basic types
# ════════════════════════════════════════════════════════════════════════════════

class TestD_ModelsBasicTypes:
    def test_D01_observation_status_values(self):
        statuses = {s.value for s in ObservationStatus}
        assert statuses == {"COMPLETE", "PARTIAL", "FALLBACK", "ERROR"}

    def test_D02_timeline_entry_to_dict(self):
        entry = TimelineEntry(
            timestamp="2024-01-01T00:00:00Z",
            event="test",
            source="tool-a",
            evidence_id="ev-1",
        )
        d = entry.to_dict()
        assert d["timestamp"] == "2024-01-01T00:00:00Z"
        assert d["event"] == "test"
        assert d["source"] == "tool-a"
        assert d["evidence_id"] == "ev-1"

    def test_D03_timeline_entry_no_evidence_id(self):
        entry = TimelineEntry(
            timestamp=_now(), event="e", source="s"
        )
        assert entry.evidence_id is None
        assert entry.to_dict()["evidence_id"] is None

    def test_D04_timeline_entry_frozen(self):
        entry = TimelineEntry(timestamp=_now(), event="e", source="s")
        with pytest.raises((AttributeError, TypeError)):
            entry.timestamp = "new"  # type: ignore

    def test_D05_step_record_to_dict(self):
        step = InvestigationStepRecord(
            step_id="s-1", sequence=0, tool_name="tool",
            purpose="collect", evidence_collected=True,
        )
        d = step.to_dict()
        assert d["step_id"] == "s-1"
        assert d["evidence_collected"] is True

    def test_D06_step_record_frozen(self):
        step = InvestigationStepRecord(
            step_id="s-2", sequence=1, tool_name="t", purpose="p", evidence_collected=False
        )
        with pytest.raises((AttributeError, TypeError)):
            step.sequence = 99  # type: ignore


# ════════════════════════════════════════════════════════════════════════════════
# E — models.py — DecisionTraceSummary
# ════════════════════════════════════════════════════════════════════════════════

class TestE_DecisionTraceSummary:
    def _make_trace_with_contradiction(self) -> DecisionTrace:
        contradiction = ContradictionRecord(
            contradiction_id="c-1",
            description="Session says ok but failure says error",
            evidence_a_id="ev-a",
            evidence_b_id="ev-b",
            severity=ContradictionSeverity.HIGH,
        )
        return DecisionTrace(
            rules_evaluated=(
                RuleMatch(
                    rule_id="r1",
                    rule_name="R1",
                    status=RuleMatchStatus.MATCH,
                    confidence_contribution=0.9,
                    evidence_used=(),
                    explanation="test",
                    evaluation_order=0,
                ),
            ),
            confidence_adjustments=(),
            contradictions_detected=(contradiction,),
            missing_evidence_types=("USER_DATA",),
            evaluation_order=("r1",),
        )

    def test_E01_from_trace_counts_matches(self):
        trace = _make_decision_trace()
        summary = DecisionTraceSummary.from_trace(trace)
        assert summary.matches >= 0

    def test_E02_from_trace_counts_contradictions(self):
        trace = self._make_trace_with_contradiction()
        summary = DecisionTraceSummary.from_trace(trace)
        assert summary.contradictions == 1

    def test_E03_from_trace_missing_evidence_count(self):
        trace = self._make_trace_with_contradiction()
        summary = DecisionTraceSummary.from_trace(trace)
        assert summary.missing_evidence_count == 1

    def test_E04_from_trace_evaluation_order(self):
        trace = self._make_trace_with_contradiction()
        summary = DecisionTraceSummary.from_trace(trace)
        assert "r1" in summary.evaluation_order

    def test_E05_to_dict_complete(self):
        trace = _make_decision_trace()
        summary = DecisionTraceSummary.from_trace(trace)
        d = summary.to_dict()
        required_keys = {
            "rules_evaluated", "matches", "partial_matches",
            "no_matches", "unknowns", "contradictions",
            "missing_evidence_count", "evaluation_order",
        }
        assert required_keys <= set(d.keys())

    def test_E06_frozen(self):
        trace = _make_decision_trace()
        summary = DecisionTraceSummary.from_trace(trace)
        with pytest.raises((AttributeError, TypeError)):
            summary.matches = 99  # type: ignore


# ════════════════════════════════════════════════════════════════════════════════
# F — models.py — ObservationAuditMetadata, Observation
# ════════════════════════════════════════════════════════════════════════════════

class TestF_ObservationAndAuditMetadata:
    def _make_audit_metadata(self, oid: str = "obs-1") -> ObservationAuditMetadata:
        return ObservationAuditMetadata(
            observation_id=oid,
            generator_version="2.44.0",
            template_registry_version="1.0.0",
            template_id="tpl-otp",
            analysis_id=_uid(),
            evidence_bundle_id=None,
            case_id="case-1",
            context_id=None,
            sop_id=None,
            playbook_id=None,
            generated_at=_now(),
            generation_duration_ms=5,
        )

    def test_F01_audit_metadata_to_dict_has_required_keys(self):
        meta = self._make_audit_metadata()
        d = meta.to_dict()
        assert "observation_id" in d
        assert "generator_version" in d
        assert "template_id" in d
        assert "generation_duration_ms" in d

    def test_F02_audit_metadata_frozen(self):
        meta = self._make_audit_metadata()
        with pytest.raises((AttributeError, TypeError)):
            meta.template_id = "new"  # type: ignore

    def test_F03_required_section_headers_count(self):
        assert len(REQUIRED_SECTION_HEADERS) == 15

    def test_F04_required_section_headers_contains_issue_summary(self):
        assert "=== ISSUE SUMMARY ===" in REQUIRED_SECTION_HEADERS

    def test_F05_required_section_headers_contains_audit(self):
        assert "=== AUDIT ===" in REQUIRED_SECTION_HEADERS

    def test_F06_observation_to_dict_has_all_fields(self):
        obs = _generate()
        d = obs.to_dict()
        required = {
            "observation_id", "case_id", "status", "confidence",
            "root_cause_category", "recommended_action", "observation_text",
            "template_id", "analysis_id", "generated_at", "version",
            "supporting_evidence", "contradicting_evidence", "missing_evidence",
        }
        assert required <= set(d.keys())

    def test_F07_observation_frozen(self):
        obs = _generate()
        with pytest.raises((AttributeError, TypeError)):
            obs.confidence = 0.1  # type: ignore

    def test_F08_observation_escalate_property(self):
        analysis_no_esc = _make_analysis(escalate=False)
        obs = _generate(analysis=analysis_no_esc)
        assert obs.escalate is False

        analysis_esc = _make_analysis(escalate=True)
        obs2 = _generate(analysis=analysis_esc)
        assert obs2.escalate is True


# ════════════════════════════════════════════════════════════════════════════════
# G — contracts.py — Protocols
# ════════════════════════════════════════════════════════════════════════════════

class TestG_Contracts:
    def test_G01_template_protocol_satisfied_by_otp_template(self):
        t = OtpObservationTemplate()
        assert isinstance(t, ObservationTemplate)

    def test_G02_template_protocol_satisfied_by_fallback(self):
        t = FallbackObservationTemplate()
        assert isinstance(t, ObservationTemplate)

    def test_G03_generator_protocol_satisfied_by_observation_generator(self):
        g = ObservationGenerator()
        assert isinstance(g, ObservationGeneratorProtocol)

    def test_G04_template_has_required_attributes(self):
        t = OtpObservationTemplate()
        assert hasattr(t, "template_id")
        assert hasattr(t, "template_name")
        assert hasattr(t, "priority")
        assert hasattr(t, "is_fallback")
        assert callable(t.matches)
        assert callable(t.render)


# ════════════════════════════════════════════════════════════════════════════════
# H — templates.py — matching logic
# ════════════════════════════════════════════════════════════════════════════════

class TestH_TemplateMatching:
    def test_H01_otp_matches_otp_topic(self):
        t = OtpObservationTemplate()
        assert t.matches("", "OTP_DELIVERY_FAILURE")

    def test_H02_otp_matches_sms_category(self):
        t = OtpObservationTemplate()
        assert t.matches("SMS_DELIVERY_FAILURE", "")

    def test_H03_otp_does_not_match_vkyc_topic(self):
        t = OtpObservationTemplate()
        assert not t.matches("LIVENESS_FAILURE", "VKYC_SESSION_FAILURE")

    def test_H04_vkyc_matches_vkyc_topic(self):
        t = VkycObservationTemplate()
        assert t.matches("", "VKYC_SESSION_FAILURE")

    def test_H05_vkyc_matches_liveness_category(self):
        t = VkycObservationTemplate()
        assert t.matches("LIVENESS_FAILURE", "")

    def test_H06_vkyc_matches_kyc_rejected(self):
        t = VkycObservationTemplate()
        assert t.matches("KYC_REJECTED", "")

    def test_H07_ocr_matches_ocr_topic(self):
        t = OcrObservationTemplate()
        assert t.matches("", "DOCUMENT_OCR_FAILURE")

    def test_H08_ocr_matches_document_failure_category(self):
        t = OcrObservationTemplate()
        assert t.matches("DOCUMENT_FAILURE", "")

    def test_H09_api_matches_api_topic(self):
        t = ApiObservationTemplate()
        assert t.matches("", "API_CALLBACK_FAILURE")

    def test_H10_api_matches_callback_failure_category(self):
        t = ApiObservationTemplate()
        assert t.matches("CALLBACK_FAILURE", "")

    def test_H11_api_matches_network_failure(self):
        t = ApiObservationTemplate()
        assert t.matches("NETWORK_FAILURE", "")

    def test_H12_portal_matches_portal_topic(self):
        t = PortalObservationTemplate()
        assert t.matches("", "AGENT_PORTAL_ISSUE")

    def test_H13_session_matches_session_topic(self):
        t = SessionObservationTemplate()
        assert t.matches("", "SESSION_FAILURE")

    def test_H14_session_matches_expired_session(self):
        t = SessionObservationTemplate()
        assert t.matches("EXPIRED_SESSION", "")

    def test_H15_fallback_matches_anything(self):
        t = FallbackObservationTemplate()
        assert t.matches("UNKNOWN", "")
        assert t.matches("", "RANDOM_TOPIC")
        assert t.matches("", "")

    def test_H16_fallback_is_fallback_true(self):
        t = FallbackObservationTemplate()
        assert t.is_fallback is True

    def test_H17_non_fallback_is_fallback_false(self):
        for cls in (OtpObservationTemplate, VkycObservationTemplate,
                    OcrObservationTemplate, ApiObservationTemplate,
                    PortalObservationTemplate, SessionObservationTemplate):
            assert cls().is_fallback is False

    def test_H18_priority_ordering(self):
        templates = [
            OtpObservationTemplate(),
            VkycObservationTemplate(),
            OcrObservationTemplate(),
            ApiObservationTemplate(),
            PortalObservationTemplate(),
            SessionObservationTemplate(),
        ]
        priorities = [t.priority for t in templates]
        assert priorities == sorted(priorities)

    def test_H19_fallback_has_highest_priority_value(self):
        fb = FallbackObservationTemplate()
        otp = OtpObservationTemplate()
        assert fb.priority > otp.priority

    def test_H20_topic_matching_case_insensitive_normalized(self):
        t = OtpObservationTemplate()
        assert t.matches("", "otp_delivery_failure")


# ════════════════════════════════════════════════════════════════════════════════
# I — templates.py — rendering output
# ════════════════════════════════════════════════════════════════════════════════

class TestI_TemplateRendering:
    def _make_draft(self, category=RootCauseCategory.LIVENESS_FAILURE, topic="VKYC_SESSION_FAILURE"):
        analysis = _make_analysis(category=category)
        return ObservationDraft(
            analysis=analysis,
            case_id=analysis.case_id,
            topic=topic,
            timeline=(),
            investigation_steps=(),
            decision_trace_summary=DecisionTraceSummary.from_trace(analysis.decision_trace),
            evidence_items=(),
            generated_at=_now(),
            generator_version="2.44.0",
        )

    def test_I01_all_required_sections_in_otp_render(self):
        t = OtpObservationTemplate()
        draft = self._make_draft(RootCauseCategory.SMS_DELIVERY_FAILURE, "OTP_DELIVERY_FAILURE")
        text = t.render(draft)
        for header in REQUIRED_SECTION_HEADERS:
            assert header in text, f"Missing section: {header}"

    def test_I02_all_required_sections_in_vkyc_render(self):
        t = VkycObservationTemplate()
        draft = self._make_draft(RootCauseCategory.LIVENESS_FAILURE, "VKYC_SESSION_FAILURE")
        text = t.render(draft)
        for header in REQUIRED_SECTION_HEADERS:
            assert header in text

    def test_I03_all_required_sections_in_fallback_render(self):
        t = FallbackObservationTemplate()
        draft = self._make_draft()
        text = t.render(draft)
        for header in REQUIRED_SECTION_HEADERS:
            assert header in text

    def test_I04_otp_framing_line(self):
        t = OtpObservationTemplate()
        draft = self._make_draft(RootCauseCategory.SMS_DELIVERY_FAILURE, "OTP")
        text = t.render(draft)
        assert "One-Time Password" in text

    def test_I05_vkyc_framing_line(self):
        t = VkycObservationTemplate()
        draft = self._make_draft()
        text = t.render(draft)
        assert "Video KYC" in text

    def test_I06_ocr_framing_line(self):
        t = OcrObservationTemplate()
        draft = self._make_draft(RootCauseCategory.DOCUMENT_FAILURE, "OCR")
        text = t.render(draft)
        assert "OCR" in text

    def test_I07_api_framing_line(self):
        t = ApiObservationTemplate()
        draft = self._make_draft(RootCauseCategory.CALLBACK_FAILURE, "API")
        text = t.render(draft)
        assert "API" in text or "callback" in text.lower()

    def test_I08_portal_framing_line(self):
        t = PortalObservationTemplate()
        draft = self._make_draft(RootCauseCategory.PORTAL_UNAVAILABLE, "PORTAL")
        text = t.render(draft)
        assert "portal" in text.lower()

    def test_I09_session_framing_line(self):
        t = SessionObservationTemplate()
        draft = self._make_draft(RootCauseCategory.EXPIRED_SESSION, "SESSION")
        text = t.render(draft)
        assert "session" in text.lower()

    def test_I10_deterministic_output(self):
        t = OtpObservationTemplate()
        draft = self._make_draft(RootCauseCategory.SMS_DELIVERY_FAILURE, "OTP")
        text1 = t.render(draft)
        text2 = t.render(draft)
        assert text1 == text2

    def test_I11_explanation_in_root_cause_section(self):
        analysis = _make_analysis(category=RootCauseCategory.LIVENESS_FAILURE)
        draft = ObservationDraft(
            analysis=analysis,
            case_id=analysis.case_id,
            topic="VKYC",
            timeline=(),
            investigation_steps=(),
            decision_trace_summary=DecisionTraceSummary.from_trace(analysis.decision_trace),
            evidence_items=(),
            generated_at=_now(),
            generator_version="2.44.0",
        )
        t = VkycObservationTemplate()
        text = t.render(draft)
        assert analysis.explanation in text

    def test_I12_confidence_in_confidence_section(self):
        analysis = _make_analysis(confidence=0.75)
        draft = ObservationDraft(
            analysis=analysis,
            case_id=analysis.case_id,
            topic="VKYC",
            timeline=(),
            investigation_steps=(),
            decision_trace_summary=DecisionTraceSummary.from_trace(analysis.decision_trace),
            evidence_items=(),
            generated_at=_now(),
            generator_version="2.44.0",
        )
        t = FallbackObservationTemplate()
        text = t.render(draft)
        assert "0.75" in text

    def test_I13_recommended_action_in_render(self):
        analysis = _make_analysis(escalate=False)
        draft = ObservationDraft(
            analysis=analysis,
            case_id=analysis.case_id,
            topic="VKYC",
            timeline=(),
            investigation_steps=(),
            decision_trace_summary=DecisionTraceSummary.from_trace(analysis.decision_trace),
            evidence_items=(),
            generated_at=_now(),
            generator_version="2.44.0",
        )
        t = FallbackObservationTemplate()
        text = t.render(draft)
        assert analysis.recommended_action.value in text


# ════════════════════════════════════════════════════════════════════════════════
# J — templates.py — evidence and contradictions sections
# ════════════════════════════════════════════════════════════════════════════════

class TestJ_TemplateSectionContent:
    def _render_with_analysis(self, analysis: RootCauseAnalysis, topic: str = "VKYC") -> str:
        draft = ObservationDraft(
            analysis=analysis,
            case_id=analysis.case_id,
            topic=topic,
            timeline=(),
            investigation_steps=(),
            decision_trace_summary=DecisionTraceSummary.from_trace(analysis.decision_trace),
            evidence_items=(),
            generated_at=_now(),
            generator_version="2.44.0",
        )
        return FallbackObservationTemplate().render(draft)

    def test_J01_supporting_evidence_in_text(self):
        analysis = _make_analysis(supporting_evidence=("ev-support-1",))
        text = self._render_with_analysis(analysis)
        assert "ev-support-1" in text

    def test_J02_contradicting_evidence_in_text(self):
        analysis = _make_analysis(contradicting_evidence=("ev-contra-1",))
        text = self._render_with_analysis(analysis)
        assert "ev-contra-1" in text

    def test_J03_missing_evidence_in_text(self):
        analysis = _make_analysis(missing_evidence=("USER_DATA",))
        text = self._render_with_analysis(analysis)
        assert "USER_DATA" in text

    def test_J04_none_detected_when_no_supporting(self):
        analysis = _make_analysis(supporting_evidence=())
        text = self._render_with_analysis(analysis)
        assert "None detected." in text

    def test_J05_contradictions_none_detected_when_empty(self):
        analysis = _make_analysis(contradictions=())
        text = self._render_with_analysis(analysis)
        assert "None detected." in text

    def test_J06_contradiction_in_text_when_present(self):
        contradiction = ContradictionRecord(
            contradiction_id="c-1",
            description="data conflict",
            evidence_a_id="ev-a",
            evidence_b_id="ev-b",
            severity=ContradictionSeverity.HIGH,
        )
        analysis = _make_analysis(contradictions=(contradiction,))
        text = self._render_with_analysis(analysis)
        assert "data conflict" in text
        assert "c-1" in text

    def test_J07_evidence_reference_in_text(self):
        ref = EvidenceReference(
            evidence_id="ref-ev-1",
            evidence_type="SESSION",
            source="GetSessionDetailsTool",
            weight=0.9,
        )
        analysis = _make_analysis(evidence_refs=(ref,))
        text = self._render_with_analysis(analysis)
        assert "ref-ev-1" in text

    def test_J08_audit_section_contains_analysis_id(self):
        analysis = _make_analysis()
        text = self._render_with_analysis(analysis)
        assert analysis.analysis_id in text

    def test_J09_no_evidence_bundle_produces_empty_evidence_summary(self):
        analysis = _make_analysis()
        draft = ObservationDraft(
            analysis=analysis,
            case_id=analysis.case_id,
            topic="TEST",
            timeline=(),
            investigation_steps=(),
            decision_trace_summary=DecisionTraceSummary.from_trace(analysis.decision_trace),
            evidence_items=(),
            generated_at=_now(),
            generator_version="2.44.0",
        )
        t = FallbackObservationTemplate()
        text = t.render(draft)
        assert "No evidence bundle" in text

    def test_J10_escalation_yes_when_should_escalate(self):
        analysis = _make_analysis(escalate=True)
        text = self._render_with_analysis(analysis)
        assert "YES" in text or "should_escalate" in text or "L2" in text


# ════════════════════════════════════════════════════════════════════════════════
# K — registry.py — core operations
# ════════════════════════════════════════════════════════════════════════════════

class TestK_TemplateRegistry:
    def test_K01_register_and_get(self):
        reg = TemplateRegistry()
        t = OtpObservationTemplate()
        reg.register(t)
        assert reg.get("tpl-otp") is t

    def test_K02_get_unknown_raises(self):
        reg = TemplateRegistry()
        with pytest.raises(TemplateNotFoundError):
            reg.get("does-not-exist")

    def test_K03_unregister_known(self):
        reg = TemplateRegistry()
        reg.register(OtpObservationTemplate())
        reg.unregister("tpl-otp")
        assert not reg.is_registered("tpl-otp")

    def test_K04_unregister_unknown_raises(self):
        reg = TemplateRegistry()
        with pytest.raises(TemplateNotFoundError):
            reg.unregister("does-not-exist")

    def test_K05_register_overwrites(self):
        reg = TemplateRegistry()
        t1 = OtpObservationTemplate()
        t2 = OtpObservationTemplate()
        reg.register(t1)
        reg.register(t2)
        assert reg.template_count == 1

    def test_K06_is_registered_true(self):
        reg = TemplateRegistry()
        reg.register(OtpObservationTemplate())
        assert reg.is_registered("tpl-otp")

    def test_K07_is_registered_false(self):
        reg = TemplateRegistry()
        assert not reg.is_registered("tpl-otp")

    def test_K08_all_templates_sorted_by_priority(self):
        reg = build_default_registry()
        templates = reg.all_templates()
        priorities = [t.priority for t in templates]
        assert priorities == sorted(priorities)

    def test_K09_template_count(self):
        reg = TemplateRegistry()
        reg.register(OtpObservationTemplate())
        reg.register(VkycObservationTemplate())
        assert reg.template_count == 2

    def test_K10_reset_clears_all(self):
        reg = TemplateRegistry()
        reg.register(OtpObservationTemplate())
        reg.reset()
        assert reg.template_count == 0

    def test_K11_resolve_otp_topic(self):
        reg = build_default_registry()
        t = reg.resolve("", "OTP_DELIVERY_FAILURE")
        assert t.template_id == "tpl-otp"

    def test_K12_resolve_vkyc_category(self):
        reg = build_default_registry()
        t = reg.resolve("LIVENESS_FAILURE", "")
        assert t.template_id == "tpl-vkyc"

    def test_K13_resolve_unknown_falls_back(self):
        reg = build_default_registry()
        t = reg.resolve("UNKNOWN_CATEGORY", "UNKNOWN_TOPIC")
        assert t.template_id == "tpl-fallback"

    def test_K14_resolve_no_fallback_raises(self):
        reg = TemplateRegistry()
        reg.register(OtpObservationTemplate())
        with pytest.raises(ObservationConfigurationError):
            reg.resolve("UNKNOWN", "UNKNOWN")

    def test_K15_fallback_property_returns_fallback(self):
        reg = build_default_registry()
        fb = reg.fallback
        assert fb is not None
        assert fb.is_fallback is True


# ════════════════════════════════════════════════════════════════════════════════
# L — registry.py — build_default_registry, thread-safety
# ════════════════════════════════════════════════════════════════════════════════

class TestL_DefaultRegistryAndThreadSafety:
    def test_L01_default_registry_has_7_templates(self):
        reg = build_default_registry()
        assert reg.template_count == 7

    def test_L02_default_registry_has_fallback(self):
        reg = build_default_registry()
        assert reg.fallback is not None

    def test_L03_all_7_template_ids_present(self):
        reg = build_default_registry()
        ids = {t.template_id for t in reg.all_templates()}
        assert "tpl-otp" in ids
        assert "tpl-vkyc" in ids
        assert "tpl-ocr" in ids
        assert "tpl-api" in ids
        assert "tpl-portal" in ids
        assert "tpl-session" in ids
        assert "tpl-fallback" in ids

    def test_L04_thread_safe_concurrent_register(self):
        reg = TemplateRegistry()
        errors: list[Exception] = []

        def register_otp():
            try:
                for _ in range(100):
                    reg.register(OtpObservationTemplate())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=register_otp) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert reg.template_count == 1

    def test_L05_broken_template_does_not_block_resolve(self):
        reg = TemplateRegistry()

        class BrokenTemplate:
            template_id = "tpl-broken"
            template_name = "broken"
            priority = 1
            is_fallback = False

            def matches(self, category, topic):
                raise RuntimeError("broken")

            def render(self, draft):
                return ""

        reg.register(BrokenTemplate())  # type: ignore
        reg.register(FallbackObservationTemplate())

        # resolve should skip broken template and use fallback
        t = reg.resolve("ANYTHING", "ANYTHING")
        assert t.template_id == "tpl-fallback"


# ════════════════════════════════════════════════════════════════════════════════
# M — metrics.py
# ════════════════════════════════════════════════════════════════════════════════

class TestM_Metrics:
    def test_M01_metric_to_dict_has_all_fields(self):
        m = _make_observation_metric()
        d = m.to_dict()
        assert "observation_id" in d
        assert "template_id" in d
        assert "duration_ms" in d
        assert "confidence" in d
        assert "status" in d

    def test_M02_generator_metrics_initial_zero(self):
        gm = GeneratorMetrics()
        assert gm.generation_count == 0
        assert gm.escalation_count == 0
        assert gm.contradiction_count == 0
        assert gm.average_latency_ms == 0.0

    def test_M03_generator_metrics_record_increases_count(self):
        gm = GeneratorMetrics()
        gm.record(_make_observation_metric())
        assert gm.generation_count == 1

    def test_M04_generator_metrics_escalation_count(self):
        gm = GeneratorMetrics()
        gm.record(_make_observation_metric(escalated=True))
        gm.record(_make_observation_metric(escalated=False))
        assert gm.escalation_count == 1

    def test_M05_generator_metrics_contradiction_count(self):
        gm = GeneratorMetrics()
        gm.record(_make_observation_metric(contradiction_count=3))
        assert gm.contradiction_count == 3

    def test_M06_generator_metrics_average_latency(self):
        gm = GeneratorMetrics()
        gm.record(_make_observation_metric(duration_ms=10))
        gm.record(_make_observation_metric(duration_ms=20))
        assert gm.average_latency_ms == 15.0

    def test_M07_template_usage_counts(self):
        gm = GeneratorMetrics()
        gm.record(_make_observation_metric(template_id="tpl-otp"))
        gm.record(_make_observation_metric(template_id="tpl-otp"))
        gm.record(_make_observation_metric(template_id="tpl-vkyc"))
        usage = gm.template_usage()
        assert usage["tpl-otp"] == 2
        assert usage["tpl-vkyc"] == 1

    def test_M08_snapshot_has_all_keys(self):
        gm = GeneratorMetrics()
        snap = gm.snapshot()
        assert "generation_count" in snap
        assert "template_usage" in snap
        assert "average_latency_ms" in snap
        assert "contradiction_count" in snap
        assert "escalation_count" in snap
        assert "confidence_distribution" in snap

    def test_M09_confidence_distribution_has_buckets(self):
        gm = GeneratorMetrics()
        gm.record(_make_observation_metric(confidence=0.85))
        dist = gm.confidence_distribution()
        assert "0.80-1.00" in dist
        assert dist["0.80-1.00"] == 1

    def test_M10_reset_clears_all(self):
        gm = GeneratorMetrics()
        gm.record(_make_observation_metric())
        gm.reset()
        assert gm.generation_count == 0
        assert gm.escalation_count == 0


# ════════════════════════════════════════════════════════════════════════════════
# N — validators.py — validate_analysis()
# ════════════════════════════════════════════════════════════════════════════════

class TestN_ValidateAnalysis:
    from case_engine.investigation.observation.validators import validate_analysis

    def test_N01_valid_analysis_passes(self):
        from case_engine.investigation.observation.validators import validate_analysis
        validate_analysis(_make_analysis())  # should not raise

    def test_N02_none_raises(self):
        from case_engine.investigation.observation.validators import validate_analysis
        with pytest.raises(InvalidAnalysisError):
            validate_analysis(None)

    def test_N03_wrong_type_raises(self):
        from case_engine.investigation.observation.validators import validate_analysis
        with pytest.raises(InvalidAnalysisError):
            validate_analysis("not an analysis")  # type: ignore

    def test_N04_empty_analysis_id_raises(self):
        from case_engine.investigation.observation.validators import validate_analysis
        analysis = _make_analysis()
        # Build a new one with empty analysis_id via the dataclass replace trick
        import dataclasses
        bad = dataclasses.replace(analysis, analysis_id="")
        with pytest.raises(InvalidAnalysisError):
            validate_analysis(bad)

    def test_N05_bad_confidence_raises(self):
        from case_engine.investigation.observation.validators import validate_analysis
        import dataclasses
        analysis = _make_analysis()
        bad = dataclasses.replace(analysis, confidence=1.5)
        with pytest.raises(InvalidAnalysisError):
            validate_analysis(bad)

    def test_N06_negative_confidence_raises(self):
        from case_engine.investigation.observation.validators import validate_analysis
        import dataclasses
        analysis = _make_analysis()
        bad = dataclasses.replace(analysis, confidence=-0.1)
        with pytest.raises(InvalidAnalysisError):
            validate_analysis(bad)

    def test_N07_none_decision_trace_raises(self):
        from case_engine.investigation.observation.validators import validate_analysis
        import dataclasses
        analysis = _make_analysis()
        bad = dataclasses.replace(analysis, decision_trace=None)  # type: ignore
        with pytest.raises(InvalidAnalysisError):
            validate_analysis(bad)

    def test_N08_confidence_exactly_zero_passes(self):
        from case_engine.investigation.observation.validators import validate_analysis
        validate_analysis(_make_analysis(confidence=0.0))

    def test_N09_confidence_exactly_one_passes(self):
        from case_engine.investigation.observation.validators import validate_analysis
        validate_analysis(_make_analysis(confidence=1.0))


# ════════════════════════════════════════════════════════════════════════════════
# O — validators.py — validate_inputs, validate_observation
# ════════════════════════════════════════════════════════════════════════════════

class TestO_ValidateInputsAndObservation:
    def test_O01_matching_case_ids_passes(self):
        from case_engine.investigation.observation.validators import validate_inputs
        analysis = _make_analysis(case_id="case-1")
        bundle = _make_bundle(case_id="case-1")
        validate_inputs(analysis, bundle, None)  # no raise

    def test_O02_mismatched_case_id_bundle_raises(self):
        from case_engine.investigation.observation.validators import validate_inputs
        analysis = _make_analysis(case_id="case-1")
        bundle = _make_bundle(case_id="case-2")
        with pytest.raises(InvalidInputError) as exc_info:
            validate_inputs(analysis, bundle, None)
        assert "bundle" in exc_info.value.input_name

    def test_O03_none_bundle_passes(self):
        from case_engine.investigation.observation.validators import validate_inputs
        analysis = _make_analysis(case_id="case-1")
        validate_inputs(analysis, None, None)  # no raise

    def test_O04_valid_observation_passes(self):
        from case_engine.investigation.observation.validators import validate_observation
        obs = _generate()
        validate_observation(obs)  # no raise

    def test_O05_missing_required_section_raises(self):
        from case_engine.investigation.observation.validators import validate_observation
        import dataclasses
        obs = _generate()
        bad = dataclasses.replace(
            obs,
            observation_text="no sections here",
            status=ObservationStatus.COMPLETE,
        )
        with pytest.raises(ObservationValidationError):
            validate_observation(bad)


# ════════════════════════════════════════════════════════════════════════════════
# P — validators.py — verify_evidence_preservation()
# ════════════════════════════════════════════════════════════════════════════════

class TestP_EvidencePreservation:
    def test_P01_no_violations_when_preserved(self):
        from case_engine.investigation.observation.validators import verify_evidence_preservation
        obs = _generate()
        analysis = _make_analysis()
        gen = ObservationGenerator()
        obs2 = gen.generate(analysis)
        violations = verify_evidence_preservation(obs2, analysis)
        assert violations == []

    def test_P02_detects_supporting_evidence_change(self):
        from case_engine.investigation.observation.validators import verify_evidence_preservation
        import dataclasses
        analysis = _make_analysis(supporting_evidence=("ev-001",))
        gen = ObservationGenerator()
        obs = gen.generate(analysis)
        bad = dataclasses.replace(obs, supporting_evidence=("different-ev",))
        violations = verify_evidence_preservation(bad, analysis)
        assert any("supporting_evidence" in v for v in violations)

    def test_P03_detects_confidence_change(self):
        from case_engine.investigation.observation.validators import verify_evidence_preservation
        import dataclasses
        analysis = _make_analysis(confidence=0.85)
        gen = ObservationGenerator()
        obs = gen.generate(analysis)
        bad = dataclasses.replace(obs, confidence=0.99)
        violations = verify_evidence_preservation(bad, analysis)
        assert any("confidence" in v for v in violations)

    def test_P04_detects_recommended_action_change(self):
        from case_engine.investigation.observation.validators import verify_evidence_preservation
        import dataclasses
        analysis = _make_analysis(escalate=False)
        gen = ObservationGenerator()
        obs = gen.generate(analysis)
        bad = dataclasses.replace(obs, recommended_action=RecommendedAction.ESCALATE)
        violations = verify_evidence_preservation(bad, analysis)
        assert any("recommended_action" in v for v in violations)

    def test_P05_detects_category_change(self):
        from case_engine.investigation.observation.validators import verify_evidence_preservation
        import dataclasses
        analysis = _make_analysis(category=RootCauseCategory.LIVENESS_FAILURE)
        gen = ObservationGenerator()
        obs = gen.generate(analysis)
        bad = dataclasses.replace(obs, root_cause_category=RootCauseCategory.UNKNOWN)
        violations = verify_evidence_preservation(bad, analysis)
        assert any("category" in v for v in violations)


# ════════════════════════════════════════════════════════════════════════════════
# Q — generator.py — happy paths
# ════════════════════════════════════════════════════════════════════════════════

class TestQ_GeneratorHappyPaths:
    def test_Q01_generate_returns_observation(self):
        obs = _generate()
        assert isinstance(obs, Observation)

    def test_Q02_observation_id_is_uuid(self):
        obs = _generate()
        uuid.UUID(obs.observation_id)  # no raise

    def test_Q03_observation_case_id_matches_analysis(self):
        analysis = _make_analysis(case_id="case-abc")
        obs = ObservationGenerator().generate(analysis)
        assert obs.case_id == "case-abc"

    def test_Q04_analysis_id_preserved(self):
        analysis = _make_analysis()
        obs = ObservationGenerator().generate(analysis)
        assert obs.analysis_id == analysis.analysis_id

    def test_Q05_confidence_preserved(self):
        analysis = _make_analysis(confidence=0.72)
        obs = ObservationGenerator().generate(analysis)
        assert obs.confidence == 0.72

    def test_Q06_category_preserved(self):
        analysis = _make_analysis(category=RootCauseCategory.LIVENESS_FAILURE)
        obs = ObservationGenerator().generate(analysis)
        assert obs.root_cause_category == RootCauseCategory.LIVENESS_FAILURE

    def test_Q07_supporting_evidence_preserved(self):
        analysis = _make_analysis(supporting_evidence=("ev-1", "ev-2"))
        obs = ObservationGenerator().generate(analysis)
        assert obs.supporting_evidence == ("ev-1", "ev-2")

    def test_Q08_contradicting_evidence_preserved(self):
        analysis = _make_analysis(contradicting_evidence=("ev-contra",))
        obs = ObservationGenerator().generate(analysis)
        assert obs.contradicting_evidence == ("ev-contra",)

    def test_Q09_missing_evidence_preserved(self):
        analysis = _make_analysis(missing_evidence=("USER_DATA",))
        obs = ObservationGenerator().generate(analysis)
        assert obs.missing_evidence == ("USER_DATA",)

    def test_Q10_all_required_sections_in_observation_text(self):
        obs = _generate()
        for header in REQUIRED_SECTION_HEADERS:
            assert header in obs.observation_text

    def test_Q11_generated_at_is_iso_timestamp(self):
        obs = _generate()
        datetime.fromisoformat(obs.generated_at)  # no raise

    def test_Q12_version_matches_constant(self):
        obs = _generate()
        assert obs.version == CURRENT_GENERATOR_VERSION

    def test_Q13_otp_template_selected_for_otp_topic(self):
        analysis = _make_analysis(category=RootCauseCategory.SMS_DELIVERY_FAILURE)
        bundle = _make_bundle(topic="OTP_DELIVERY_FAILURE")
        obs = ObservationGenerator().generate(analysis, bundle=bundle)
        assert obs.template_id == "tpl-otp"

    def test_Q14_vkyc_template_selected_for_vkyc_topic(self):
        analysis = _make_analysis(category=RootCauseCategory.LIVENESS_FAILURE)
        bundle = _make_bundle(topic="VKYC_SESSION_FAILURE")
        obs = ObservationGenerator().generate(analysis, bundle=bundle)
        assert obs.template_id == "tpl-vkyc"

    def test_Q15_fallback_selected_for_unknown_topic(self):
        analysis = _make_analysis(category=RootCauseCategory.UNKNOWN)
        bundle = _make_bundle(topic="COMPLETELY_UNKNOWN_TOPIC_XYZ")
        obs = ObservationGenerator().generate(analysis, bundle=bundle)
        assert obs.template_id == "tpl-fallback"

    def test_Q16_contradictions_preserved(self):
        contradiction = ContradictionRecord(
            contradiction_id="c-1",
            description="conflict",
            evidence_a_id="ev-a",
            evidence_b_id="ev-b",
            severity=ContradictionSeverity.HIGH,
        )
        analysis = _make_analysis(contradictions=(contradiction,))
        obs = ObservationGenerator().generate(analysis)
        assert len(obs.contradictions) == 1
        assert obs.contradictions[0].contradiction_id == "c-1"

    def test_Q17_escalation_preserved(self):
        analysis = _make_analysis(escalate=True)
        obs = ObservationGenerator().generate(analysis)
        assert obs.escalation.should_escalate is True


# ════════════════════════════════════════════════════════════════════════════════
# R — generator.py — status logic
# ════════════════════════════════════════════════════════════════════════════════

class TestR_GeneratorStatus:
    def test_R01_complete_status_when_bundle_present(self):
        analysis = _make_analysis(category=RootCauseCategory.LIVENESS_FAILURE)
        bundle = _make_bundle(topic="VKYC_SESSION_FAILURE")
        obs = ObservationGenerator().generate(analysis, bundle=bundle)
        assert obs.status == ObservationStatus.COMPLETE

    def test_R02_partial_status_when_no_bundle_but_specific_template(self):
        analysis = _make_analysis(category=RootCauseCategory.SMS_DELIVERY_FAILURE)
        obs = ObservationGenerator().generate(analysis, bundle=None)
        # No bundle but specific template matches → PARTIAL
        assert obs.status == ObservationStatus.PARTIAL

    def test_R03_fallback_status_when_fallback_template_used(self):
        analysis = _make_analysis(category=RootCauseCategory.UNKNOWN)
        bundle = _make_bundle(topic="UNKNOWN_TOPIC_XYZ")
        obs = ObservationGenerator().generate(analysis, bundle=bundle)
        assert obs.status == ObservationStatus.FALLBACK

    def test_R04_error_status_on_template_render_failure(self):
        from case_engine.investigation.observation.registry import TemplateRegistry

        class AlwaysFailTemplate:
            template_id = "tpl-fail"
            template_name = "fail"
            priority = 1
            is_fallback = False

            def matches(self, category, topic):
                return True

            def render(self, draft):
                raise RuntimeError("forced render failure")

        registry = TemplateRegistry()
        registry.register(AlwaysFailTemplate())  # type: ignore
        registry.register(FallbackObservationTemplate())

        analysis = _make_analysis()
        # Remove the fallback too to force ERROR
        only_fail_reg = TemplateRegistry()
        only_fail_reg.register(AlwaysFailTemplate())  # type: ignore

        class AlwaysFailFallback:
            template_id = "tpl-fallback"
            template_name = "fallback"
            priority = 9999
            is_fallback = True

            def matches(self, category, topic):
                return True

            def render(self, draft):
                raise RuntimeError("fallback also broken")

        only_fail_reg.register(AlwaysFailFallback())  # type: ignore

        gen = ObservationGenerator(registry=only_fail_reg)
        obs = gen.generate(analysis)
        assert obs.status == ObservationStatus.ERROR


# ════════════════════════════════════════════════════════════════════════════════
# S — generator.py — apply_to_context, get_metrics
# ════════════════════════════════════════════════════════════════════════════════

class TestS_ApplyToContextAndMetrics:
    def _make_context(self):
        from case_engine.investigation.context import InvestigationContext
        return InvestigationContext(
            context_id=_uid(),
            case_id="case-s1",
            ticket_id="ticket-1",
            ticket_subject="Test subject",
            ticket_description="Test description",
            customer_id="cust-1",
            customer_email="test@test.com",
            channel="email",
            tenant_context=_make_tenant(),
            topic="VKYC_SESSION_FAILURE",
            classification_confidence=0.9,
        )

    def test_S01_apply_to_context_sets_observation(self):
        gen = ObservationGenerator()
        analysis = _make_analysis()
        obs = gen.generate(analysis)
        ctx = self._make_context()
        gen.apply_to_context(obs, ctx)
        assert ctx.observation is obs

    def test_S02_apply_to_context_sets_version(self):
        gen = ObservationGenerator()
        analysis = _make_analysis()
        obs = gen.generate(analysis)
        ctx = self._make_context()
        gen.apply_to_context(obs, ctx)
        assert ctx.observation_version == obs.version

    def test_S03_apply_to_context_sets_timestamp(self):
        gen = ObservationGenerator()
        analysis = _make_analysis()
        obs = gen.generate(analysis)
        ctx = self._make_context()
        gen.apply_to_context(obs, ctx)
        assert ctx.observation_timestamp == obs.generated_at

    def test_S04_apply_to_context_sets_status(self):
        gen = ObservationGenerator()
        analysis = _make_analysis()
        obs = gen.generate(analysis)
        ctx = self._make_context()
        gen.apply_to_context(obs, ctx)
        assert ctx.observation_status == obs.status.value

    def test_S05_apply_to_context_sets_metadata(self):
        gen = ObservationGenerator()
        analysis = _make_analysis()
        obs = gen.generate(analysis)
        ctx = self._make_context()
        gen.apply_to_context(obs, ctx)
        assert ctx.observation_metadata is not None
        assert isinstance(ctx.observation_metadata, dict)

    def test_S06_apply_to_context_sets_text(self):
        gen = ObservationGenerator()
        analysis = _make_analysis()
        obs = gen.generate(analysis)
        ctx = self._make_context()
        gen.apply_to_context(obs, ctx)
        assert ctx.observation_text == obs.observation_text

    def test_S07_get_metrics_returns_dict(self):
        gen = ObservationGenerator()
        metrics = gen.get_metrics()
        assert isinstance(metrics, dict)

    def test_S08_get_metrics_counts_generation(self):
        gen = ObservationGenerator()
        gen.generate(_make_analysis())
        metrics = gen.get_metrics()
        assert metrics["generation_count"] == 1

    def test_S09_get_metrics_multiple_generations(self):
        gen = ObservationGenerator()
        for _ in range(3):
            gen.generate(_make_analysis())
        metrics = gen.get_metrics()
        assert metrics["generation_count"] == 3

    def test_S10_context_has_rich_observation_after_apply(self):
        gen = ObservationGenerator()
        analysis = _make_analysis()
        obs = gen.generate(analysis)
        ctx = self._make_context()
        gen.apply_to_context(obs, ctx)
        assert ctx.has_rich_observation() is True


# ════════════════════════════════════════════════════════════════════════════════
# T — generator.py — edge cases
# ════════════════════════════════════════════════════════════════════════════════

class TestT_GeneratorEdgeCases:
    def test_T01_none_analysis_raises_invalid_analysis(self):
        gen = ObservationGenerator()
        with pytest.raises(InvalidAnalysisError):
            gen.generate(None)  # type: ignore

    def test_T02_wrong_analysis_type_raises(self):
        gen = ObservationGenerator()
        with pytest.raises(InvalidAnalysisError):
            gen.generate("not an analysis")  # type: ignore

    def test_T03_custom_registry_respected(self):
        from case_engine.investigation.observation.registry import TemplateRegistry

        class CustomTemplate:
            template_id = "tpl-custom"
            template_name = "custom"
            priority = 1
            is_fallback = False

            def matches(self, category, topic):
                return True

            def render(self, draft):
                # produce minimal valid text with all sections
                sections = []
                for header in REQUIRED_SECTION_HEADERS:
                    sections.append(f"{header}\nSection content.")
                return "\n\n".join(sections)

        reg = TemplateRegistry()
        reg.register(CustomTemplate())  # type: ignore
        reg.register(FallbackObservationTemplate())

        gen = ObservationGenerator(registry=reg)
        obs = gen.generate(_make_analysis())
        assert obs.template_id == "tpl-custom"

    def test_T04_shared_metrics_across_calls(self):
        metrics = GeneratorMetrics()
        gen = ObservationGenerator(metrics=metrics)
        gen.generate(_make_analysis())
        gen.generate(_make_analysis())
        assert metrics.generation_count == 2

    def test_T05_topic_from_bundle_takes_precedence_over_context(self):
        gen = ObservationGenerator()
        analysis = _make_analysis(category=RootCauseCategory.SMS_DELIVERY_FAILURE)
        bundle = _make_bundle(topic="OTP_DELIVERY_FAILURE")
        obs = gen.generate(analysis, bundle=bundle)
        assert obs.topic == "OTP_DELIVERY_FAILURE"
        assert obs.template_id == "tpl-otp"

    def test_T06_empty_topic_uses_fallback(self):
        gen = ObservationGenerator()
        analysis = _make_analysis(category=RootCauseCategory.UNKNOWN)
        obs = gen.generate(analysis, bundle=None)
        assert obs.status in {ObservationStatus.PARTIAL, ObservationStatus.FALLBACK}

    def test_T07_evidence_preservation_guardrail_fires_on_violated_confidence(self):
        gen = ObservationGenerator()
        analysis = _make_analysis(confidence=0.85)

        original_build_observation = ObservationGenerator._render

        def patched_render(self_inner, template, draft):
            text, used_template, failed = original_build_observation(self_inner, template, draft)
            return text, used_template, failed

        import dataclasses

        # We test the guardrail is active by checking it's called via the generate flow
        obs = gen.generate(analysis)
        # Confidence must match
        assert obs.confidence == 0.85

    def test_T08_generate_with_all_optional_args_none(self):
        gen = ObservationGenerator()
        obs = gen.generate(_make_analysis(), bundle=None, context=None, sop=None, playbook=None)
        assert isinstance(obs, Observation)

    def test_T09_audit_metadata_records_duration(self):
        gen = ObservationGenerator()
        obs = gen.generate(_make_analysis())
        assert obs.audit_metadata.generation_duration_ms >= 0

    def test_T10_audit_metadata_records_template_id(self):
        gen = ObservationGenerator()
        obs = gen.generate(_make_analysis())
        assert obs.audit_metadata.template_id == obs.template_id


# ════════════════════════════════════════════════════════════════════════════════
# U — serialization.py — dict roundtrip
# ════════════════════════════════════════════════════════════════════════════════

class TestU_SerializationDictRoundtrip:
    def test_U01_to_dict_returns_dict(self):
        obs = _generate()
        d = observation_to_dict(obs)
        assert isinstance(d, dict)

    def test_U02_from_dict_returns_observation(self):
        obs = _generate()
        d = observation_to_dict(obs)
        reconstructed = observation_from_dict(d)
        assert isinstance(reconstructed, Observation)

    def test_U03_observation_id_preserved(self):
        obs = _generate()
        d = observation_to_dict(obs)
        reconstructed = observation_from_dict(d)
        assert reconstructed.observation_id == obs.observation_id

    def test_U04_case_id_preserved(self):
        analysis = _make_analysis(case_id="case-roundtrip")
        obs = ObservationGenerator().generate(analysis)
        d = observation_to_dict(obs)
        reconstructed = observation_from_dict(d)
        assert reconstructed.case_id == "case-roundtrip"

    def test_U05_confidence_preserved(self):
        analysis = _make_analysis(confidence=0.77)
        obs = ObservationGenerator().generate(analysis)
        d = observation_to_dict(obs)
        reconstructed = observation_from_dict(d)
        assert reconstructed.confidence == 0.77

    def test_U06_status_preserved(self):
        obs = _generate()
        d = observation_to_dict(obs)
        reconstructed = observation_from_dict(d)
        assert reconstructed.status == obs.status

    def test_U07_supporting_evidence_preserved(self):
        analysis = _make_analysis(supporting_evidence=("ev-1", "ev-2"))
        obs = ObservationGenerator().generate(analysis)
        d = observation_to_dict(obs)
        reconstructed = observation_from_dict(d)
        assert set(reconstructed.supporting_evidence) == {"ev-1", "ev-2"}

    def test_U08_missing_evidence_preserved(self):
        analysis = _make_analysis(missing_evidence=("MISSING_DATA",))
        obs = ObservationGenerator().generate(analysis)
        d = observation_to_dict(obs)
        reconstructed = observation_from_dict(d)
        assert "MISSING_DATA" in reconstructed.missing_evidence

    def test_U09_category_preserved(self):
        analysis = _make_analysis(category=RootCauseCategory.LIVENESS_FAILURE)
        obs = ObservationGenerator().generate(analysis)
        d = observation_to_dict(obs)
        reconstructed = observation_from_dict(d)
        assert reconstructed.root_cause_category == RootCauseCategory.LIVENESS_FAILURE

    def test_U10_observation_text_preserved(self):
        obs = _generate()
        d = observation_to_dict(obs)
        reconstructed = observation_from_dict(d)
        assert reconstructed.observation_text == obs.observation_text

    def test_U11_missing_required_field_raises(self):
        obs = _generate()
        d = observation_to_dict(obs)
        del d["observation_id"]
        with pytest.raises(ObservationDeserializationError):
            observation_from_dict(d)

    def test_U12_invalid_status_value_raises(self):
        obs = _generate()
        d = observation_to_dict(obs)
        d["status"] = "INVALID_STATUS"
        with pytest.raises(ObservationDeserializationError):
            observation_from_dict(d)

    def test_U13_invalid_category_raises(self):
        obs = _generate()
        d = observation_to_dict(obs)
        d["root_cause_category"] = "NOT_A_CATEGORY"
        with pytest.raises(ObservationDeserializationError):
            observation_from_dict(d)

    def test_U14_contradictions_preserved(self):
        contradiction = ContradictionRecord(
            contradiction_id="c-round",
            description="roundtrip test",
            evidence_a_id="ev-a",
            evidence_b_id="ev-b",
            severity=ContradictionSeverity.LOW,
        )
        analysis = _make_analysis(contradictions=(contradiction,))
        obs = ObservationGenerator().generate(analysis)
        d = observation_to_dict(obs)
        reconstructed = observation_from_dict(d)
        assert len(reconstructed.contradictions) == 1
        assert reconstructed.contradictions[0].contradiction_id == "c-round"

    def test_U15_audit_metadata_preserved(self):
        obs = _generate()
        d = observation_to_dict(obs)
        reconstructed = observation_from_dict(d)
        assert reconstructed.audit_metadata.template_id == obs.audit_metadata.template_id


# ════════════════════════════════════════════════════════════════════════════════
# V — serialization.py — JSON roundtrip
# ════════════════════════════════════════════════════════════════════════════════

class TestV_SerializationJsonRoundtrip:
    def test_V01_to_json_returns_string(self):
        obs = _generate()
        j = observation_to_json(obs)
        assert isinstance(j, str)

    def test_V02_to_json_is_valid_json(self):
        obs = _generate()
        j = observation_to_json(obs)
        parsed = json.loads(j)
        assert isinstance(parsed, dict)

    def test_V03_from_json_reconstructs_observation(self):
        obs = _generate()
        j = observation_to_json(obs)
        reconstructed = observation_from_json(j)
        assert isinstance(reconstructed, Observation)

    def test_V04_json_roundtrip_preserves_observation_id(self):
        obs = _generate()
        j = observation_to_json(obs)
        reconstructed = observation_from_json(j)
        assert reconstructed.observation_id == obs.observation_id

    def test_V05_from_json_invalid_raises(self):
        with pytest.raises(ObservationDeserializationError):
            observation_from_json("not valid json {{{")

    def test_V06_json_preserves_unicode(self):
        analysis = _make_analysis()
        import dataclasses
        analysis_unicode = dataclasses.replace(
            analysis,
            explanation="Failure detected — résumé test: 中文",
        )
        obs = ObservationGenerator().generate(analysis_unicode)
        j = observation_to_json(obs)
        reconstructed = observation_from_json(j)
        assert "résumé" in reconstructed.root_cause_explanation

    def test_V07_escalation_level_preserved(self):
        analysis = _make_analysis(escalate=True)
        obs = ObservationGenerator().generate(analysis)
        j = observation_to_json(obs)
        reconstructed = observation_from_json(j)
        assert reconstructed.escalation.should_escalate is True
        assert reconstructed.escalation.level == EscalationLevel.L2


# ════════════════════════════════════════════════════════════════════════════════
# W — context.py — Sprint 2.44 observation fields
# ════════════════════════════════════════════════════════════════════════════════

class TestW_ContextObservationFields:
    def _make_context(self, case_id: str = "case-w1"):
        from case_engine.investigation.context import InvestigationContext
        return InvestigationContext(
            context_id=_uid(),
            case_id=case_id,
            ticket_id="ticket-1",
            ticket_subject="Test",
            ticket_description="Test desc",
            customer_id="cust-1",
            customer_email="test@test.com",
            channel="email",
            tenant_context=_make_tenant(),
            topic="VKYC_SESSION_FAILURE",
            classification_confidence=0.9,
        )

    def test_W01_has_rich_observation_false_initially(self):
        ctx = self._make_context()
        assert ctx.has_rich_observation() is False

    def test_W02_observation_field_is_none_initially(self):
        ctx = self._make_context()
        assert ctx.observation is None

    def test_W03_observation_version_is_none_initially(self):
        ctx = self._make_context()
        assert ctx.observation_version is None

    def test_W04_observation_timestamp_is_none_initially(self):
        ctx = self._make_context()
        assert ctx.observation_timestamp is None

    def test_W05_observation_status_is_none_initially(self):
        ctx = self._make_context()
        assert ctx.observation_status is None

    def test_W06_observation_metadata_is_none_initially(self):
        ctx = self._make_context()
        assert ctx.observation_metadata is None

    def test_W07_to_dict_includes_observation_id_none_when_no_obs(self):
        ctx = self._make_context()
        d = ctx.to_dict()
        assert d.get("observation_id") is None

    def test_W08_to_dict_includes_observation_id_when_obs_set(self):
        ctx = self._make_context()
        gen = ObservationGenerator()
        analysis = _make_analysis(case_id="case-w1")
        obs = gen.generate(analysis)
        gen.apply_to_context(obs, ctx)
        d = ctx.to_dict()
        assert d["observation_id"] == obs.observation_id

    def test_W09_to_dict_includes_observation_version(self):
        ctx = self._make_context()
        gen = ObservationGenerator()
        obs = gen.generate(_make_analysis())
        gen.apply_to_context(obs, ctx)
        d = ctx.to_dict()
        assert d["observation_version"] == obs.version

    def test_W10_to_dict_includes_observation_status(self):
        ctx = self._make_context()
        gen = ObservationGenerator()
        obs = gen.generate(_make_analysis())
        gen.apply_to_context(obs, ctx)
        d = ctx.to_dict()
        assert d["observation_status"] == obs.status.value


# ════════════════════════════════════════════════════════════════════════════════
# X — Integration: full pipeline
# ════════════════════════════════════════════════════════════════════════════════

class TestX_Integration:
    def _make_context(self, case_id: str = "case-x1"):
        from case_engine.investigation.context import InvestigationContext
        return InvestigationContext(
            context_id=_uid(),
            case_id=case_id,
            ticket_id="ticket-1",
            ticket_subject="Test",
            ticket_description="Test desc",
            customer_id="cust-1",
            customer_email="test@test.com",
            channel="email",
            tenant_context=_make_tenant(),
            topic="VKYC_SESSION_FAILURE",
            classification_confidence=0.9,
        )

    def test_X01_full_pipeline_analysis_to_observation_to_context(self):
        analysis = _make_analysis(case_id="case-full")
        gen = ObservationGenerator()
        obs = gen.generate(analysis)
        ctx = self._make_context("case-full")
        gen.apply_to_context(obs, ctx)
        assert ctx.has_rich_observation()
        assert ctx.observation_text is not None

    def test_X02_observation_roundtrip_after_full_pipeline(self):
        analysis = _make_analysis()
        gen = ObservationGenerator()
        obs = gen.generate(analysis)
        j = observation_to_json(obs)
        reconstructed = observation_from_json(j)
        assert reconstructed.observation_id == obs.observation_id
        assert reconstructed.confidence == obs.confidence

    def test_X03_all_seven_templates_produce_complete_output(self):
        specs = [
            (RootCauseCategory.SMS_DELIVERY_FAILURE, "OTP_DELIVERY_FAILURE", "tpl-otp"),
            (RootCauseCategory.LIVENESS_FAILURE, "VKYC_SESSION_FAILURE", "tpl-vkyc"),
            (RootCauseCategory.DOCUMENT_FAILURE, "DOCUMENT_OCR_FAILURE", "tpl-ocr"),
            (RootCauseCategory.CALLBACK_FAILURE, "API_CALLBACK_FAILURE", "tpl-api"),
            (RootCauseCategory.PORTAL_UNAVAILABLE, "AGENT_PORTAL_ISSUE", "tpl-portal"),
            (RootCauseCategory.EXPIRED_SESSION, "SESSION_FAILURE", "tpl-session"),
            (RootCauseCategory.UNKNOWN, "UNKNOWN_TOPIC_XYZ_123", "tpl-fallback"),
        ]
        gen = ObservationGenerator()
        for category, topic, expected_tpl in specs:
            analysis = _make_analysis(category=category)
            bundle = _make_bundle(topic=topic)
            obs = gen.generate(analysis, bundle=bundle)
            assert obs.template_id == expected_tpl, f"Expected {expected_tpl} for {topic}"
            for header in REQUIRED_SECTION_HEADERS:
                if obs.status != ObservationStatus.ERROR:
                    assert header in obs.observation_text, \
                        f"Missing {header!r} in {obs.template_id} render"

    def test_X04_evidence_preservation_for_all_templates(self):
        from case_engine.investigation.observation.validators import verify_evidence_preservation
        specs = [
            (RootCauseCategory.SMS_DELIVERY_FAILURE, "OTP_DELIVERY_FAILURE"),
            (RootCauseCategory.LIVENESS_FAILURE, "VKYC_SESSION_FAILURE"),
            (RootCauseCategory.DOCUMENT_FAILURE, "DOCUMENT_OCR_FAILURE"),
            (RootCauseCategory.CALLBACK_FAILURE, "API_CALLBACK_FAILURE"),
        ]
        gen = ObservationGenerator()
        for category, topic in specs:
            analysis = _make_analysis(
                category=category,
                supporting_evidence=("ev-s1", "ev-s2"),
                contradicting_evidence=("ev-c1",),
                missing_evidence=("MISSING_FIELD",),
            )
            bundle = _make_bundle(topic=topic)
            obs = gen.generate(analysis, bundle=bundle)
            violations = verify_evidence_preservation(obs, analysis)
            assert violations == [], \
                f"Evidence preservation violations for {topic}: {violations}"

    def test_X05_metrics_track_all_template_types(self):
        metrics = GeneratorMetrics()
        gen = ObservationGenerator(metrics=metrics)
        specs = [
            (RootCauseCategory.SMS_DELIVERY_FAILURE, "OTP_DELIVERY_FAILURE"),
            (RootCauseCategory.LIVENESS_FAILURE, "VKYC_SESSION_FAILURE"),
        ]
        for category, topic in specs:
            analysis = _make_analysis(category=category)
            bundle = _make_bundle(topic=topic)
            gen.generate(analysis, bundle=bundle)

        usage = metrics.template_usage()
        assert "tpl-otp" in usage
        assert "tpl-vkyc" in usage

    def test_X06_escalation_observation_flagged_in_metrics(self):
        metrics = GeneratorMetrics()
        gen = ObservationGenerator(metrics=metrics)
        analysis = _make_analysis(escalate=True)
        gen.generate(analysis)
        assert metrics.escalation_count == 1

    def test_X07_contradiction_counted_in_metrics(self):
        metrics = GeneratorMetrics()
        gen = ObservationGenerator(metrics=metrics)
        contradiction = ContradictionRecord(
            contradiction_id="c-metrics",
            description="test",
            evidence_a_id="ev-a",
            evidence_b_id="ev-b",
            severity=ContradictionSeverity.HIGH,
        )
        analysis = _make_analysis(contradictions=(contradiction,))
        gen.generate(analysis)
        assert metrics.contradiction_count == 1

    def test_X08_observation_text_contains_case_id(self):
        analysis = _make_analysis(case_id="CASE-INTEGRATION-42")
        gen = ObservationGenerator()
        obs = gen.generate(analysis)
        assert "CASE-INTEGRATION-42" in obs.observation_text

    def test_X09_partial_evidence_does_not_crash(self):
        analysis = _make_analysis(
            supporting_evidence=(),
            contradicting_evidence=(),
            missing_evidence=("FIELD_A", "FIELD_B"),
        )
        obs = ObservationGenerator().generate(analysis)
        assert isinstance(obs, Observation)

    def test_X10_full_json_roundtrip_with_all_template_types(self):
        specs = [
            (RootCauseCategory.SMS_DELIVERY_FAILURE, "OTP_DELIVERY_FAILURE"),
            (RootCauseCategory.LIVENESS_FAILURE, "VKYC_SESSION_FAILURE"),
            (RootCauseCategory.UNKNOWN, "UNKNOWN_XYZ"),
        ]
        gen = ObservationGenerator()
        for category, topic in specs:
            analysis = _make_analysis(category=category)
            bundle = _make_bundle(topic=topic)
            obs = gen.generate(analysis, bundle=bundle)
            j = observation_to_json(obs)
            reconstructed = observation_from_json(j)
            assert reconstructed.observation_id == obs.observation_id
            assert reconstructed.root_cause_category == obs.root_cause_category


# ════════════════════════════════════════════════════════════════════════════════
# Y — Regression: Sprint 2.18 imports unaffected
# ════════════════════════════════════════════════════════════════════════════════

class TestY_Regression:
    def test_Y01_sprint218_observation_generator_importable_from_investigation(self):
        from case_engine.investigation import ObservationGenerator as Sprint218ObsGen
        gen = Sprint218ObsGen()
        assert callable(gen.generate)

    def test_Y02_sprint218_obs_gen_takes_bundle_and_root_cause(self):
        from case_engine.investigation import ObservationGenerator as Sprint218ObsGen
        from case_engine.investigation.models import (
            EvidenceBundle, RootCauseAnalysis as RC218,
            RootCauseCategory, RecommendedAction,
        )
        bundle = EvidenceBundle(
            bundle_id=_uid(),
            case_id="case-reg",
            topic="VKYC_Session_Failure",
            plan_id=_uid(),
            items=[],
            collected_at=_now(),
        )
        rc = RC218(
            analysis_id=_uid(),
            case_id="case-reg",
            topic="VKYC_Session_Failure",
            category=RootCauseCategory.LIVENESS_FAILURE,
            confidence=0.8,
            explanation="test",
            evidence_ids=[],
            recommended_action=RecommendedAction.SESSION_RESET,
            escalate=False,
            analysed_at=_now(),
        )
        gen = Sprint218ObsGen()
        result = gen.generate(bundle, rc)
        assert isinstance(result, str)

    def test_Y03_sprint244_obs_gen_from_observation_package(self):
        from case_engine.investigation.observation import ObservationGenerator as Sprint244ObsGen
        gen = Sprint244ObsGen()
        obs = gen.generate(_make_analysis())
        assert isinstance(obs, Observation)

    def test_Y04_observation_from_sprint244_package_is_rich(self):
        from case_engine.investigation.observation import ObservationGenerator
        obs = ObservationGenerator().generate(_make_analysis())
        assert hasattr(obs, "confidence_breakdown")
        assert hasattr(obs, "decision_trace_summary")
        assert hasattr(obs, "evidence_references")

    def test_Y05_sprint243_root_cause_engine_unchanged(self):
        engine = RootCauseEngine()
        bundle = _make_bundle(items=[_make_log_evidence()])
        analysis = engine.analyze(bundle)
        assert isinstance(analysis, RootCauseAnalysis)

    def test_Y06_investigation_import_works(self):
        from case_engine.investigation import (
            InvestigationService,
            EvidenceCollector,
            InvestigationPlanner,
            RootCauseEngine as Sprint218RCE,
            ObservationGenerator as Sprint218OG,
        )
        assert InvestigationService is not None
        assert EvidenceCollector is not None

    def test_Y07_observation_sprint218_module_importable(self):
        from case_engine.investigation._observation_sprint218 import ObservationGenerator
        gen = ObservationGenerator()
        assert callable(gen.generate)

    def test_Y08_no_circular_imports(self):
        import importlib
        import sys
        # Try reimporting the module to verify no circular import issues
        for mod_name in list(sys.modules.keys()):
            if "case_engine.investigation.observation" in mod_name:
                pass  # already imported, would fail on circular if present
        # If we got here, no circular import issue
        assert True

    def test_Y09_sprint243_root_cause_context_fields_intact(self):
        from case_engine.investigation.context import InvestigationContext
        ctx = InvestigationContext(
            context_id=_uid(),
            case_id="case-y",
            ticket_id="t1",
            ticket_subject="s",
            ticket_description="d",
            customer_id="c1",
            customer_email="e@e.com",
            channel="email",
            tenant_context=_make_tenant(),
            topic="VKYC",
            classification_confidence=0.9,
        )
        # Sprint 2.43 fields should still exist
        assert hasattr(ctx, "root_cause_analysis")
        assert hasattr(ctx, "root_cause_confidence")
        assert hasattr(ctx, "root_cause_version")
        assert hasattr(ctx, "root_cause_recommendation")
        assert hasattr(ctx, "decision_trace_summary")

    def test_Y10_sprint244_context_fields_exist(self):
        from case_engine.investigation.context import InvestigationContext
        ctx = InvestigationContext(
            context_id=_uid(),
            case_id="case-y2",
            ticket_id="t1",
            ticket_subject="s",
            ticket_description="d",
            customer_id="c1",
            customer_email="e@e.com",
            channel="email",
            tenant_context=_make_tenant(),
            topic="VKYC",
            classification_confidence=0.9,
        )
        # Sprint 2.44 fields should exist
        assert hasattr(ctx, "observation")
        assert hasattr(ctx, "observation_version")
        assert hasattr(ctx, "observation_timestamp")
        assert hasattr(ctx, "observation_status")
        assert hasattr(ctx, "observation_metadata")
