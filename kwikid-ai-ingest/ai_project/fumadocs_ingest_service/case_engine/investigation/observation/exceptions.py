"""
case_engine/investigation/observation/exceptions.py

Sprint 2.44: Exception hierarchy for the Observation Generator.

All exceptions derive from ObservationError so callers can catch the
entire family with one except clause.

Dependency direction:
  exceptions.py → stdlib only
"""
from __future__ import annotations


class ObservationError(Exception):
    """Base class for all Observation Generator errors."""


class ObservationConfigurationError(ObservationError):
    """The generator or registry is misconfigured (e.g. no fallback template)."""


class TemplateNotFoundError(ObservationError):
    """Raised when a template_id is not registered in the TemplateRegistry."""

    def __init__(self, template_id: str) -> None:
        self.template_id = template_id
        super().__init__(
            f"Observation template {template_id!r} is not registered."
        )


class TemplateRenderError(ObservationError):
    """Raised when a template fails to render an observation draft."""

    def __init__(self, template_id: str, cause: str) -> None:
        self.template_id = template_id
        self.cause = cause
        super().__init__(
            f"Template {template_id!r} failed to render: {cause}"
        )


class InvalidAnalysisError(ObservationError):
    """Raised when the input RootCauseAnalysis is structurally invalid."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Invalid RootCauseAnalysis: {reason}")


class InvalidInputError(ObservationError):
    """Raised when an auxiliary input (bundle/context) is inconsistent."""

    def __init__(self, input_name: str, reason: str) -> None:
        self.input_name = input_name
        self.reason = reason
        super().__init__(f"Invalid input {input_name!r}: {reason}")


class ObservationValidationError(ObservationError):
    """Raised when a generated Observation fails post-generation validation."""

    def __init__(self, observation_id: str, reason: str) -> None:
        self.observation_id = observation_id
        self.reason = reason
        super().__init__(
            f"Observation {observation_id!r} failed validation: {reason}"
        )


class ObservationSerializationError(ObservationError):
    """Raised when an Observation cannot be serialized to JSON/dict."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Observation serialization error: {reason}")


class ObservationDeserializationError(ObservationError):
    """Raised when an Observation cannot be reconstructed from JSON/dict."""

    def __init__(self, observation_id: str, reason: str) -> None:
        self.observation_id = observation_id
        self.reason = reason
        super().__init__(
            f"Observation {observation_id!r} deserialization error: {reason}"
        )
