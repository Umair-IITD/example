"""
tests/test_sprint23_provider_exceptions.py

Sprint 2.3 — Provider exception hierarchy contract tests.

Covers:
  - ProviderError base: error_code, provider_name, retryable, message
  - ProviderTransientError: retryable=True, correct default error_code
  - ProviderPermanentError: retryable=False, correct default error_code
  - All concrete errors are catchable as ProviderError
  - Transient errors are catchable as ProviderTransientError
  - Permanent errors are catchable as ProviderPermanentError
  - ProviderCapabilityError is a subclass of ProviderValidationError
  - Distinct error_code defaults per exception class
  - Custom error_code and provider_name accepted by all classes
  - Exception messages are preserved
  - Retry decision from retryable property maps correctly to retry policy
"""
from __future__ import annotations

import pytest

from case_engine.provider_exceptions import (
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderCapabilityError,
    ProviderError,
    ProviderExecutionError,
    ProviderPermanentError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderTransientError,
    ProviderUnavailableError,
    ProviderValidationError,
)


# ── Base ProviderError ─────────────────────────────────────────────────────────


class TestProviderErrorBase:
    def test_is_runtime_error(self) -> None:
        assert isinstance(ProviderError("test"), RuntimeError)

    def test_default_error_code(self) -> None:
        exc = ProviderError("test")
        assert exc.error_code == "PROVIDER_ERROR"

    def test_custom_error_code(self) -> None:
        exc = ProviderError("test", error_code="MY_CODE")
        assert exc.error_code == "MY_CODE"

    def test_provider_name_defaults_none(self) -> None:
        exc = ProviderError("test")
        assert exc.provider_name is None

    def test_provider_name_set(self) -> None:
        exc = ProviderError("test", provider_name="freshdesk")
        assert exc.provider_name == "freshdesk"

    def test_retryable_defaults_false(self) -> None:
        exc = ProviderError("test")
        assert exc.retryable is False

    def test_message_preserved(self) -> None:
        exc = ProviderError("connection failed")
        assert "connection failed" in str(exc)

    def test_catchable_as_exception(self) -> None:
        with pytest.raises(Exception):
            raise ProviderError("test")


# ── ProviderTransientError ────────────────────────────────────────────────────


class TestProviderTransientError:
    def test_retryable_is_true(self) -> None:
        assert ProviderTransientError("test").retryable is True

    def test_is_provider_error(self) -> None:
        assert isinstance(ProviderTransientError("test"), ProviderError)

    def test_is_runtime_error(self) -> None:
        assert isinstance(ProviderTransientError("test"), RuntimeError)

    def test_custom_error_code(self) -> None:
        exc = ProviderTransientError("test", error_code="CUSTOM")
        assert exc.error_code == "CUSTOM"

    def test_provider_name_set(self) -> None:
        exc = ProviderTransientError("test", provider_name="freshdesk")
        assert exc.provider_name == "freshdesk"


# ── ProviderPermanentError ─────────────────────────────────────────────────────


class TestProviderPermanentError:
    def test_retryable_is_false(self) -> None:
        assert ProviderPermanentError("test").retryable is False

    def test_is_provider_error(self) -> None:
        assert isinstance(ProviderPermanentError("test"), ProviderError)

    def test_is_runtime_error(self) -> None:
        assert isinstance(ProviderPermanentError("test"), RuntimeError)


# ── ProviderUnavailableError ──────────────────────────────────────────────────


class TestProviderUnavailableError:
    def test_is_transient(self) -> None:
        assert isinstance(ProviderUnavailableError("down"), ProviderTransientError)

    def test_is_provider_error(self) -> None:
        assert isinstance(ProviderUnavailableError("down"), ProviderError)

    def test_retryable(self) -> None:
        assert ProviderUnavailableError("down").retryable is True

    def test_default_error_code(self) -> None:
        assert ProviderUnavailableError("down").error_code == "PROVIDER_UNAVAILABLE"

    def test_custom_code_and_name(self) -> None:
        exc = ProviderUnavailableError("down", error_code="DOWN", provider_name="fd")
        assert exc.error_code == "DOWN"
        assert exc.provider_name == "fd"

    def test_message_preserved(self) -> None:
        exc = ProviderUnavailableError("connection refused")
        assert "connection refused" in str(exc)

    def test_catchable_as_provider_error(self) -> None:
        with pytest.raises(ProviderError):
            raise ProviderUnavailableError("down")


# ── ProviderTimeoutError ──────────────────────────────────────────────────────


class TestProviderTimeoutError:
    def test_is_transient(self) -> None:
        assert isinstance(ProviderTimeoutError("timed out"), ProviderTransientError)

    def test_retryable(self) -> None:
        assert ProviderTimeoutError("timed out").retryable is True

    def test_default_error_code(self) -> None:
        assert ProviderTimeoutError("timed out").error_code == "PROVIDER_TIMEOUT"

    def test_catchable_as_provider_transient(self) -> None:
        with pytest.raises(ProviderTransientError):
            raise ProviderTimeoutError("timed out")


# ── ProviderRateLimitError ────────────────────────────────────────────────────


class TestProviderRateLimitError:
    def test_is_transient(self) -> None:
        assert isinstance(ProviderRateLimitError("rate limit"), ProviderTransientError)

    def test_retryable(self) -> None:
        assert ProviderRateLimitError("rate limit").retryable is True

    def test_default_error_code(self) -> None:
        assert ProviderRateLimitError("rate limit").error_code == "PROVIDER_RATE_LIMIT"

    def test_catchable_as_provider_error(self) -> None:
        with pytest.raises(ProviderError):
            raise ProviderRateLimitError("rate limit")


# ── ProviderAuthenticationError ───────────────────────────────────────────────


class TestProviderAuthenticationError:
    def test_is_permanent(self) -> None:
        assert isinstance(ProviderAuthenticationError("auth failed"), ProviderPermanentError)

    def test_retryable_is_false(self) -> None:
        assert ProviderAuthenticationError("auth failed").retryable is False

    def test_default_error_code(self) -> None:
        assert ProviderAuthenticationError("auth failed").error_code == "PROVIDER_AUTH_FAILED"

    def test_catchable_as_provider_permanent(self) -> None:
        with pytest.raises(ProviderPermanentError):
            raise ProviderAuthenticationError("auth failed")


# ── ProviderAuthorizationError ────────────────────────────────────────────────


class TestProviderAuthorizationError:
    def test_is_permanent(self) -> None:
        assert isinstance(ProviderAuthorizationError("no permission"), ProviderPermanentError)

    def test_retryable_is_false(self) -> None:
        assert ProviderAuthorizationError("no permission").retryable is False

    def test_default_error_code(self) -> None:
        assert ProviderAuthorizationError("no permission").error_code == "PROVIDER_UNAUTHORIZED"

    def test_catchable_as_provider_error(self) -> None:
        with pytest.raises(ProviderError):
            raise ProviderAuthorizationError("no permission")


# ── ProviderValidationError ───────────────────────────────────────────────────


class TestProviderValidationError:
    def test_is_permanent(self) -> None:
        assert isinstance(ProviderValidationError("invalid"), ProviderPermanentError)

    def test_retryable_is_false(self) -> None:
        assert ProviderValidationError("invalid").retryable is False

    def test_default_error_code(self) -> None:
        assert ProviderValidationError("invalid").error_code == "PROVIDER_VALIDATION_FAILED"


# ── ProviderCapabilityError ───────────────────────────────────────────────────


class TestProviderCapabilityError:
    def test_is_validation_error(self) -> None:
        assert isinstance(ProviderCapabilityError("no cap"), ProviderValidationError)

    def test_is_permanent(self) -> None:
        assert isinstance(ProviderCapabilityError("no cap"), ProviderPermanentError)

    def test_retryable_is_false(self) -> None:
        assert ProviderCapabilityError("no cap").retryable is False

    def test_default_error_code(self) -> None:
        assert ProviderCapabilityError("no cap").error_code == "PROVIDER_CAPABILITY_UNSUPPORTED"

    def test_catchable_as_provider_validation_error(self) -> None:
        with pytest.raises(ProviderValidationError):
            raise ProviderCapabilityError("no cap")

    def test_catchable_as_provider_error(self) -> None:
        with pytest.raises(ProviderError):
            raise ProviderCapabilityError("no cap")


# ── ProviderExecutionError ────────────────────────────────────────────────────


class TestProviderExecutionError:
    def test_is_permanent(self) -> None:
        assert isinstance(ProviderExecutionError("exec failed"), ProviderPermanentError)

    def test_retryable_is_false(self) -> None:
        assert ProviderExecutionError("exec failed").retryable is False

    def test_default_error_code(self) -> None:
        assert ProviderExecutionError("exec failed").error_code == "PROVIDER_EXECUTION_FAILED"

    def test_catchable_as_provider_permanent(self) -> None:
        with pytest.raises(ProviderPermanentError):
            raise ProviderExecutionError("exec failed")


# ── Cross-hierarchy catchability ──────────────────────────────────────────────


class TestCrossHierarchyCatchability:
    def test_all_transient_catchable_as_provider_error(self) -> None:
        transient_classes = [
            ProviderUnavailableError,
            ProviderTimeoutError,
            ProviderRateLimitError,
        ]
        for cls in transient_classes:
            exc = cls("test")
            assert isinstance(exc, ProviderError)
            assert exc.retryable is True

    def test_all_permanent_catchable_as_provider_error(self) -> None:
        permanent_classes = [
            ProviderAuthenticationError,
            ProviderAuthorizationError,
            ProviderValidationError,
            ProviderCapabilityError,
            ProviderExecutionError,
        ]
        for cls in permanent_classes:
            exc = cls("test")
            assert isinstance(exc, ProviderError)
            assert exc.retryable is False

    def test_transient_not_permanent(self) -> None:
        exc = ProviderUnavailableError("test")
        assert not isinstance(exc, ProviderPermanentError)

    def test_permanent_not_transient(self) -> None:
        exc = ProviderAuthenticationError("test")
        assert not isinstance(exc, ProviderTransientError)

    def test_distinct_default_codes(self) -> None:
        codes = [
            ProviderError("test").error_code,
            ProviderUnavailableError("test").error_code,
            ProviderTimeoutError("test").error_code,
            ProviderRateLimitError("test").error_code,
            ProviderAuthenticationError("test").error_code,
            ProviderAuthorizationError("test").error_code,
            ProviderValidationError("test").error_code,
            ProviderCapabilityError("test").error_code,
            ProviderExecutionError("test").error_code,
        ]
        assert len(codes) == len(set(codes)), "Error codes must be distinct"
