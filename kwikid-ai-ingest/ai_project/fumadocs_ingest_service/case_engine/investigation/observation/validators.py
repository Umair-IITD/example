"""
case_engine/investigation/observation/validators.py

Sprint 2.44: Validators for the Observation Generator.

validate_analysis()             — input gate: RootCauseAnalysis must be sound.
validate_inputs()               — cross-input consistency (case_id agreement).
validate_observation()          — output gate: generated Observation is well-formed.
verify_evidence_preservation()  — blueprint Section 26 guardrail: every
                                  analytical field on the Observation must
                                  equal the RootCauseAnalysis field EXACTLY.
                                  Never invent. Never rewrite. Never hide.

Dependency direction:
  validators.py → observation/models.py, observation/exceptions.py
  validators.py → case_engine.investigation.root_cause.models
  validators.py → case_engine.investigation.models (enums)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from case_engine.investigation.models import RecommendedAction, RootCauseCategory
from case_engine.investigation.observation.exceptions import (
    InvalidAnalysisError,
    InvalidInputError,
    ObservationValidationError,
)
from case_engine.investigation.observation.models import (
    Observation,
    ObservationStatus,
    REQUIRED_SECTION_HEADERS,
)
from case_engine.investigation.root_cause.models import RootCauseAnalysis

if TYPE_CHECKING:
    from case_engine.investigation.models import EvidenceBundle


def validate_analysis(analysis: object) -> None:
    """
    Validate the input RootCauseAnalysis before generation.

    Raises InvalidAnalysisError on any structural problem.
    """
    if analysis is None:
        raise InvalidAnalysisError("analysis is None")

    if not isinstance(analysis, RootCauseAnalysis):
        raise InvalidAnalysisError(
            f"expected RootCauseAnalysis, got {type(analysis).__name__}"
        )

    if not analysis.analysis_id:
        raise InvalidAnalysisError("analysis_id is empty")

    if not isinstance(analysis.category, RootCauseCategory):
        raise InvalidAnalysisError(
            f"category is not a RootCauseCategory: {analysis.category!r}"
        )

    if not isinstance(analysis.recommended_action, RecommendedAction):
        raise InvalidAnalysisError(
            f"recommended_action is not a RecommendedAction: "
            f"{analysis.recommended_action!r}"
        )

    if not (0.0 <= analysis.confidence <= 1.0):
        raise InvalidAnalysisError(
            f"confidence {analysis.confidence} out of range [0.0, 1.0]"
        )

    if analysis.decision_trace is None:
        raise InvalidAnalysisError("decision_trace is None")

    if analysis.confidence_breakdown is None:
        raise InvalidAnalysisError("confidence_breakdown is None")

    if analysis.escalation_recommendation is None:
        raise InvalidAnalysisError("escalation_recommendation is None")

    if analysis.audit_metadata is None:
        raise InvalidAnalysisError("audit_metadata is None")


def validate_inputs(
    analysis: RootCauseAnalysis,
    bundle: "EvidenceBundle | None",
    context: object | None,
) -> None:
    """
    Validate cross-input consistency.

    Raises InvalidInputError when the bundle or context disagree with the
    analysis on case_id (only when both sides carry a non-empty case_id).
    """
    if bundle is not None:
        bundle_case_id = getattr(bundle, "case_id", "")
        if bundle_case_id and analysis.case_id and bundle_case_id != analysis.case_id:
            raise InvalidInputError(
                "bundle",
                f"case_id mismatch: bundle={bundle_case_id!r} "
                f"analysis={analysis.case_id!r}",
            )

    if context is not None:
        context_case_id = getattr(context, "case_id", "")
        if context_case_id and analysis.case_id and context_case_id != analysis.case_id:
            raise InvalidInputError(
                "context",
                f"case_id mismatch: context={context_case_id!r} "
                f"analysis={analysis.case_id!r}",
            )


def validate_observation(observation: Observation) -> None:
    """
    Validate a generated Observation before it is returned to callers.

    Raises ObservationValidationError on any structural problem.
    """
    oid = getattr(observation, "observation_id", "")

    if not observation.observation_id:
        raise ObservationValidationError(oid, "observation_id is empty")

    if not observation.analysis_id:
        raise ObservationValidationError(oid, "analysis_id is empty")

    if not observation.observation_text:
        raise ObservationValidationError(oid, "observation_text is empty")

    if not observation.version:
        raise ObservationValidationError(oid, "version is empty")

    if not observation.generated_at:
        raise ObservationValidationError(oid, "generated_at is empty")

    if not observation.template_id:
        raise ObservationValidationError(oid, "template_id is empty")

    if not isinstance(observation.status, ObservationStatus):
        raise ObservationValidationError(
            oid, f"status is not an ObservationStatus: {observation.status!r}"
        )

    if not (0.0 <= observation.confidence <= 1.0):
        raise ObservationValidationError(
            oid, f"confidence {observation.confidence} out of range [0.0, 1.0]"
        )

    if observation.status != ObservationStatus.ERROR:
        missing = [
            header for header in REQUIRED_SECTION_HEADERS
            if header not in observation.observation_text
        ]
        if missing:
            raise ObservationValidationError(
                oid, f"observation_text missing required sections: {missing}"
            )


def verify_evidence_preservation(
    observation: Observation,
    analysis: RootCauseAnalysis,
) -> list[str]:
    """
    Blueprint Section 26 guardrail: verify the Observation preserved every
    analytical field of the RootCauseAnalysis EXACTLY.

    Returns a list of violation descriptions (empty when fully preserved).
    """
    violations: list[str] = []

    if observation.supporting_evidence != tuple(analysis.supporting_evidence):
        violations.append("supporting_evidence differs from analysis")

    if observation.contradicting_evidence != tuple(analysis.contradicting_evidence):
        violations.append("contradicting_evidence differs from analysis")

    if observation.missing_evidence != tuple(analysis.missing_evidence):
        violations.append("missing_evidence differs from analysis")

    if observation.evidence_references != tuple(analysis.evidence_references):
        violations.append("evidence_references differ from analysis")

    if observation.contradictions != tuple(analysis.decision_trace.contradictions_detected):
        violations.append("contradictions differ from decision_trace")

    if observation.confidence != analysis.confidence:
        violations.append("confidence was modified")

    if observation.confidence_breakdown != analysis.confidence_breakdown:
        violations.append("confidence_breakdown was modified")

    if observation.root_cause_category != analysis.category:
        violations.append("root_cause_category was modified")

    if observation.root_cause_explanation != analysis.explanation:
        violations.append("root_cause_explanation was modified")

    if observation.recommended_action != analysis.recommended_action:
        violations.append("recommended_action was modified")

    if observation.escalation != analysis.escalation_recommendation:
        violations.append("escalation_recommendation was modified")

    if observation.analysis_id != analysis.analysis_id:
        violations.append("analysis_id does not match")

    return violations
