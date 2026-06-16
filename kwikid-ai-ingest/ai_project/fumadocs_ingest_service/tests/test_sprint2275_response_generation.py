"""
tests/test_sprint2275_response_generation.py

Sprint 2.27.5 Phase 5: ResponseGenerationService test suite.

Tests: deterministic templates, LLM injection, audit events, all ResponseTypes.
"""
import pytest
from unittest.mock import MagicMock

from case_engine.models import Case
from case_engine.response_generation.models import (
    ResponseContext,
    ResponseDraft,
    ResponseMetadata,
    ResponseType,
)
from case_engine.response_generation.service import (
    ResponseGenerationService,
    build_response_generation_service,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_context(
    response_type: ResponseType = ResponseType.RESOLUTION,
    case_id: str = "case-001",
    topic: str = "VKYC_Session_Failure",
    action_summary: str = "OTP was resent successfully.",
    escalation_reason: str = "",
    clarification_question: str = "",
) -> ResponseContext:
    return ResponseContext(
        case_id=case_id,
        topic=topic,
        response_type=response_type,
        action_summary=action_summary,
        escalation_reason=escalation_reason,
        clarification_question=clarification_question,
    )


def _make_case(case_id: str = "case-001") -> Case:
    return Case(case_id=case_id, ticket_id="fd-001", client="unity_bank")


# ── ResponseType enum ─────────────────────────────────────────────────────────

class TestResponseTypeEnum:
    def test_all_values(self):
        values = {rt.value for rt in ResponseType}
        assert "resolution" in values
        assert "escalation" in values
        assert "clarification" in values
        assert "status_update" in values
        assert "approval_needed" in values

    def test_string_enum(self):
        assert ResponseType.RESOLUTION == "resolution"
        assert ResponseType.ESCALATION == "escalation"


# ── ResponseContext ───────────────────────────────────────────────────────────

class TestResponseContext:
    def test_to_dict(self):
        ctx = _make_context()
        d = ctx.to_dict()
        assert d["case_id"] == "case-001"
        assert d["topic"] == "VKYC_Session_Failure"
        assert d["response_type"] == "resolution"
        assert isinstance(d["sop_steps"], list)
        assert isinstance(d["citations"], list)

    def test_immutable(self):
        ctx = _make_context()
        with pytest.raises((AttributeError, TypeError)):
            ctx.case_id = "hacked"  # type: ignore[misc]

    def test_with_sop_steps(self):
        ctx = ResponseContext(
            case_id="c1", topic="VKYC_Session_Failure",
            response_type=ResponseType.RESOLUTION,
            sop_steps=("Step 1", "Step 2"),
            citations=("SOP:sop-1",),
        )
        assert len(ctx.sop_steps) == 2
        assert ctx.citations[0] == "SOP:sop-1"


# ── ResponseMetadata ──────────────────────────────────────────────────────────

class TestResponseMetadata:
    def test_to_dict(self):
        meta = ResponseMetadata(
            generator_type="deterministic_template",
            template_used="resolution_success",
            generation_ms=5,
        )
        d = meta.to_dict()
        assert d["generator_type"] == "deterministic_template"
        assert d["template_used"] == "resolution_success"
        assert d["generation_ms"] == 5
        assert d["llm_model"] is None


# ── ResponseDraft ─────────────────────────────────────────────────────────────

class TestResponseDraft:
    def test_to_dict(self):
        svc = build_response_generation_service()
        ctx = _make_context(ResponseType.RESOLUTION)
        draft = svc.generate(ctx)
        d = draft.to_dict()
        assert "draft_id" in d
        assert d["response_type"] == "resolution"
        assert isinstance(d["body_text"], str)
        assert isinstance(d["body_html"], str)
        assert d["escalation_required"] is False

    def test_failure_classmethod(self):
        draft = ResponseDraft.failure("c1", "VKYC_Session_Failure", "something broke")
        assert draft.response_type == ResponseType.ESCALATION
        assert draft.escalation_required is True
        assert draft.confidence == 0.0

    def test_immutable(self):
        svc = build_response_generation_service()
        draft = svc.generate(_make_context())
        with pytest.raises((AttributeError, TypeError)):
            draft.body_text = "hacked"  # type: ignore[misc]


# ── ResponseGenerationService — deterministic templates ───────────────────────

class TestResponseGenerationServiceTemplates:
    def test_resolution_template(self):
        svc = build_response_generation_service()
        ctx = _make_context(ResponseType.RESOLUTION, action_summary="Issue resolved.")
        draft = svc.generate(ctx)
        assert "resolved" in draft.body_text.lower()
        assert draft.escalation_required is False
        assert draft.confidence > 0.5

    def test_escalation_template(self):
        svc = build_response_generation_service()
        ctx = _make_context(
            ResponseType.ESCALATION,
            escalation_reason="Backend API is down.",
        )
        draft = svc.generate(ctx)
        assert "escalat" in draft.body_text.lower() or "engineer" in draft.body_text.lower()
        assert draft.escalation_required is True

    def test_clarification_template(self):
        svc = build_response_generation_service()
        ctx = _make_context(
            ResponseType.CLARIFICATION,
            clarification_question="Can you provide the session ID?",
        )
        draft = svc.generate(ctx)
        assert "session ID" in draft.body_text or "information" in draft.body_text.lower()
        assert draft.escalation_required is False

    def test_status_update_template(self):
        svc = build_response_generation_service()
        ctx = _make_context(ResponseType.STATUS_UPDATE, action_summary="Team is working on it.")
        draft = svc.generate(ctx)
        assert draft.confidence > 0
        assert draft.escalation_required is False

    def test_approval_needed_template(self):
        svc = build_response_generation_service()
        ctx = _make_context(ResponseType.APPROVAL_NEEDED, action_summary="Awaiting sign-off.")
        draft = svc.generate(ctx)
        assert draft.confidence > 0
        assert draft.escalation_required is False

    def test_all_templates_produce_html(self):
        svc = build_response_generation_service()
        for rt in ResponseType:
            ctx = _make_context(rt)
            draft = svc.generate(ctx)
            assert "<p>" in draft.body_html

    def test_generator_type_is_deterministic(self):
        svc = build_response_generation_service()
        draft = svc.generate(_make_context())
        assert draft.metadata.generator_type == "deterministic_template"

    def test_draft_is_llm_ready(self):
        svc = build_response_generation_service()
        draft = svc.generate(_make_context())
        assert draft.llm_ready is True

    def test_citations_passed_through(self):
        svc = build_response_generation_service()
        ctx = ResponseContext(
            case_id="c1", topic="OTP_Delivery_Failure",
            response_type=ResponseType.RESOLUTION,
            citations=("SOP:otp-1", "SOP:otp-2"),
        )
        draft = svc.generate(ctx)
        assert "SOP:otp-1" in draft.citations
        assert "SOP:otp-2" in draft.citations

    def test_action_summary_from_resolution_outcome(self):
        svc = build_response_generation_service()
        ctx = ResponseContext(
            case_id="c1", topic="OTP_Delivery_Failure",
            response_type=ResponseType.RESOLUTION,
            resolution_outcome={"summary": "OTP was resent to the registered mobile number."},
        )
        draft = svc.generate(ctx)
        assert "OTP" in draft.body_text or "resolved" in draft.body_text.lower()


# ── LLM injection ─────────────────────────────────────────────────────────────

class TestResponseGenerationServiceLLM:
    def test_llm_generator_replaces_body_text(self):
        def llm_gen(ctx):
            return "LLM-generated response for " + ctx.topic

        svc = build_response_generation_service(llm_generator=llm_gen)
        ctx = _make_context(ResponseType.RESOLUTION)
        draft = svc.generate(ctx)
        assert "LLM-generated" in draft.body_text
        assert draft.metadata.generator_type == "llm"

    def test_llm_failure_falls_back_to_template(self):
        def broken_llm(ctx):
            raise RuntimeError("LLM unavailable")

        svc = build_response_generation_service(llm_generator=broken_llm)
        ctx = _make_context(ResponseType.RESOLUTION)
        draft = svc.generate(ctx)
        assert draft.metadata.generator_type == "deterministic_template"
        assert draft.confidence > 0

    def test_llm_empty_string_falls_back_to_template(self):
        svc = build_response_generation_service(llm_generator=lambda ctx: "")
        draft = svc.generate(_make_context())
        assert draft.metadata.generator_type == "deterministic_template"


# ── Never-raises contract ─────────────────────────────────────────────────────

class TestResponseGenerationNeverRaises:
    def test_generate_never_raises(self):
        from case_engine.response_generation.service import ResponseGenerationService
        svc = ResponseGenerationService(audit_logger=None)
        ctx = _make_context()
        draft = svc.generate(ctx)
        assert draft is not None

    def test_generate_with_all_none_context_fields(self):
        svc = build_response_generation_service()
        ctx = ResponseContext(
            case_id="c1", topic="", response_type=ResponseType.STATUS_UPDATE,
        )
        draft = svc.generate(ctx)
        assert isinstance(draft, ResponseDraft)


# ── Audit events ──────────────────────────────────────────────────────────────

class TestResponseGenerationAudit:
    def test_audit_emitted(self):
        mock_audit = MagicMock()
        svc = build_response_generation_service(audit_logger=mock_audit)
        ctx = _make_context()
        case = _make_case()
        draft = svc.generate(ctx, case=case)
        mock_audit.log_response_generated.assert_called_once()
        call_kwargs = mock_audit.log_response_generated.call_args
        assert call_kwargs[0][0] is case

    def test_audit_failure_does_not_crash(self):
        mock_audit = MagicMock()
        mock_audit.log_response_generated.side_effect = RuntimeError("audit down")
        svc = build_response_generation_service(audit_logger=mock_audit)
        ctx = _make_context()
        draft = svc.generate(ctx, case=_make_case())
        assert draft is not None

    def test_no_audit_without_case(self):
        mock_audit = MagicMock()
        svc = build_response_generation_service(audit_logger=mock_audit)
        draft = svc.generate(_make_context(), case=None)
        mock_audit.log_response_generated.assert_not_called()
        assert draft is not None


# ── Factory ───────────────────────────────────────────────────────────────────

class TestResponseGenerationFactory:
    def test_build_with_no_args(self):
        svc = build_response_generation_service()
        assert svc is not None

    def test_build_with_audit(self):
        mock_audit = MagicMock()
        svc = build_response_generation_service(audit_logger=mock_audit)
        assert svc._audit is mock_audit

    def test_build_with_llm_generator(self):
        svc = build_response_generation_service(llm_generator=lambda ctx: "hi")
        assert svc._llm_generator is not None
