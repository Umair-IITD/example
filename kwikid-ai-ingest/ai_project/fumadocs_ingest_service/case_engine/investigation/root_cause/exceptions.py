"""
case_engine/investigation/root_cause/exceptions.py

Sprint 2.43: Exception hierarchy for the Root Cause Engine.

All exceptions inherit from RootCauseError.

Dependency direction:
  exceptions.py → stdlib only (no case_engine imports)
"""
from __future__ import annotations


class RootCauseError(Exception):
    """Base exception for the Root Cause Engine."""


class RootCauseConfigurationError(RootCauseError):
    """Registry or engine is misconfigured (e.g. no fallback rule)."""


class RuleNotFoundError(RootCauseError):
    """No rule found for the requested rule_id."""

    def __init__(self, rule_id: str) -> None:
        super().__init__(f"No rule registered with rule_id={rule_id!r}")
        self.rule_id = rule_id


class RuleExecutionError(RootCauseError):
    """A rule raised an unexpected exception during evaluate()."""

    def __init__(self, rule_id: str, cause: Exception) -> None:
        super().__init__(
            f"Rule {rule_id!r} raised an unexpected exception: {cause}"
        )
        self.rule_id = rule_id
        self.cause = cause


class InvalidEvidenceError(RootCauseError):
    """EvidenceBundle failed structural validation before analysis."""

    def __init__(self, bundle_id: str, reason: str) -> None:
        super().__init__(
            f"EvidenceBundle {bundle_id!r} is invalid: {reason}"
        )
        self.bundle_id = bundle_id
        self.reason = reason


class EngineTimeoutError(RootCauseError):
    """Analysis exceeded the configured time budget."""

    def __init__(self, analysis_id: str, timeout_seconds: float) -> None:
        super().__init__(
            f"Analysis {analysis_id!r} exceeded timeout of {timeout_seconds}s"
        )
        self.analysis_id = analysis_id
        self.timeout_seconds = timeout_seconds
