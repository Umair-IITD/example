"""
intelligence/exceptions.py

Wave 3: Typed exception hierarchy for the Enterprise Intelligence Layer.

Every failure mode has a distinct type so callers can decide whether to
fall back to a draft note, escalate to a human, or short-circuit. All
inherit from IntelligenceError.

Dependency direction: exceptions.py → stdlib only.
"""
from __future__ import annotations

from typing import Any


class IntelligenceError(RuntimeError):
    """Base class for all Enterprise Intelligence failures."""


class IntelligenceDisabled(IntelligenceError):
    """`INTELLIGENCE_ENABLED=false` — layer is turned off by config."""


class IntelligenceConfigError(IntelligenceError):
    """Missing / invalid configuration (LLM API key, provider, etc.)."""


class LLMRequestError(IntelligenceError):
    """Transport-level failure to the LLM provider."""

    def __init__(self, message: str, *, status_code: int | None = None,
                 response_body: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class LLMTimeoutError(LLMRequestError):
    """Timeout during LLM inference. Retriable up to `llm_max_retries`."""


class LLMAuthError(LLMRequestError):
    """401/403 from the LLM provider — never retry."""


class LLMRateLimitError(LLMRequestError):
    """429 from the LLM provider — retriable with backoff."""


class LLMServerError(LLMRequestError):
    """5xx from the LLM provider — retriable with backoff."""


class ReasoningParseError(IntelligenceError):
    """
    LLM returned malformed JSON or a JSON payload that fails schema
    validation. Callers should treat this as a low-confidence draft that
    requires human review, not as a hard error.
    """

    def __init__(self, message: str, *, raw_response: str = "",
                 missing_fields: list[str] | None = None) -> None:
        super().__init__(message)
        self.raw_response = raw_response[:500]
        self.missing_fields = list(missing_fields or [])


class ContextBuildError(IntelligenceError):
    """Failed to assemble LLMContext from inputs (usually a programming bug)."""
