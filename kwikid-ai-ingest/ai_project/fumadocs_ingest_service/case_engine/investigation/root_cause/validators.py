"""
case_engine/investigation/root_cause/validators.py

Sprint 2.43: Validators for the Root Cause Engine.

RootCauseValidator checks:
  - EvidenceBundle structural soundness (EB01–EB04)
  - RuleResult correctness (RR01–RR03)
  - RuleEvaluationContext completeness (RC01–RC02)

All validate_* methods return (ok: bool, reasons: list[str]).
They never raise.

Dependency direction:
  validators.py → root_cause/contracts.py (RuleResult, RuleEvaluationContext)
  validators.py → root_cause/models.py (RuleMatchStatus)
  validators.py → case_engine.investigation.models (EvidenceBundle)
  validators.py → stdlib only
"""
from __future__ import annotations

import logging

from case_engine.investigation.root_cause.contracts import RuleEvaluationContext, RuleResult
from case_engine.investigation.root_cause.models import RuleMatchStatus

LOGGER = logging.getLogger(__name__)


class RootCauseValidator:
    """
    Validates inputs and outputs of the Root Cause Engine.

    All methods return (ok, reasons). They never raise.
    """

    def validate_bundle(self, bundle: object) -> tuple[bool, list[str]]:
        """
        Check that an EvidenceBundle is structurally sound before analysis.

        Checks:
          EB01: bundle is not None.
          EB02: bundle has a non-empty bundle_id.
          EB03: bundle has a non-empty case_id.
          EB04: bundle.items is accessible (list-like).
        """
        reasons: list[str] = []

        if bundle is None:
            reasons.append("EB01: EvidenceBundle is None")
            return False, reasons

        if not getattr(bundle, "bundle_id", ""):
            reasons.append("EB02: bundle_id is empty")

        if not getattr(bundle, "case_id", ""):
            reasons.append("EB03: case_id is empty")

        try:
            _ = list(getattr(bundle, "items", []))
        except TypeError:
            reasons.append("EB04: bundle.items is not iterable")

        return len(reasons) == 0, reasons

    def validate_rule_result(self, result: RuleResult) -> tuple[bool, list[str]]:
        """
        Check that a RuleResult is well-formed.

        Checks:
          RR01: confidence is in [0.0, 1.0].
          RR02: MATCH/PARTIAL_MATCH must have a non-empty explanation.
          RR03: MATCH/PARTIAL_MATCH should have a category_hint.
        """
        reasons: list[str] = []

        if not (0.0 <= result.confidence <= 1.0):
            reasons.append(
                f"RR01: confidence {result.confidence} out of [0.0, 1.0] "
                f"for rule {result.rule_id!r}"
            )

        is_positive = result.status in (RuleMatchStatus.MATCH, RuleMatchStatus.PARTIAL_MATCH)
        if is_positive and not result.explanation:
            reasons.append(
                f"RR02: MATCH/PARTIAL_MATCH rule {result.rule_id!r} has empty explanation"
            )

        if is_positive and result.category_hint is None:
            reasons.append(
                f"RR03: MATCH/PARTIAL_MATCH rule {result.rule_id!r} has no category_hint"
            )

        return len(reasons) == 0, reasons

    def validate_context(
        self, context: RuleEvaluationContext
    ) -> tuple[bool, list[str]]:
        """
        Check that a RuleEvaluationContext has minimum required fields.

        Checks:
          RC01: case_id is non-empty.
          RC02: topic is non-empty.
        """
        reasons: list[str] = []

        if not context.case_id:
            reasons.append("RC01: RuleEvaluationContext.case_id is empty")

        if not context.topic:
            reasons.append("RC02: RuleEvaluationContext.topic is empty")

        return len(reasons) == 0, reasons
