"""
tests/test_sprint24_freshdesk_provider.py

Sprint 2.4 — FreshdeskProvider + FreshdeskConfig exhaustive tests.

Areas covered:
  1.  FreshdeskConfig validation
  2.  FreshdeskProvider capabilities
  3.  FreshdeskProvider metadata
  4.  health_check — success paths
  5.  health_check — failure paths (never raises)
  6.  execute add_note — success
  7.  execute update_ticket — success
  8.  execute unsupported operation
  9.  error mapping — transient (429, 5xx, timeout, connection)
  10. error mapping — permanent (401, 403, 404, 409, 422)
  11. ProviderRouter integration
  12. Freshdesk exception hierarchy contracts
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, call

import httpx
import pytest

from case_engine.provider_exceptions import (
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderError,
    ProviderExecutionError,
    ProviderPermanentError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderTransientError,
    ProviderUnavailableError,
    ProviderValidationError,
)
from case_engine.provider_interface import Provider
from case_engine.provider_models import (
    ProviderCapability,
    ProviderHealth,
    ProviderMetadata,
    ProviderRequest,
    ProviderResponse,
)
from case_engine.provider_registry import ProviderRegistry
from case_engine.provider_router import ProviderRouter
from freshdesk.freshdesk_exceptions import (
    FreshdeskApiError,
    FreshdeskAuthError,
    FreshdeskConflictError,
    FreshdeskConnectionError,
    FreshdeskError,
    FreshdeskForbiddenError,
    FreshdeskNotFoundError,
    FreshdeskRateLimitError,
    FreshdeskServerError,
    FreshdeskTimeoutException,
    FreshdeskValidationError,
)
from freshdesk.freshdesk_models import FreshdeskConfig
from freshdesk.freshdesk_provider import FreshdeskProvider


# ══════════════════════════════════════════════════════════════════════════════
# Test infrastructure
# ══════════════════════════════════════════════════════════════════════════════


def _config(
    domain: str = "acme.freshdesk.com",
    api_key: str = "test-api-key",
    timeout_seconds: float = 10.0,
) -> FreshdeskConfig:
    return FreshdeskConfig(domain=domain, api_key=api_key, timeout_seconds=timeout_seconds)


def _mock_response(status: int, body: dict[str, Any] | None = None) -> MagicMock:
    """Create a mock httpx.Response with the given status and JSON body."""
    resp = MagicMock()
    resp.status_code = status
    if body is not None:
        resp.json.return_value = body
    else:
        resp.json.side_effect = Exception("no JSON body")
    return resp


def _mock_client(get_response=None, post_response=None, put_response=None) -> MagicMock:
    """Create a mock httpx.Client with pre-configured method responses."""
    client = MagicMock(spec=httpx.Client)
    if get_response is not None:
        client.get.return_value = get_response
    if post_response is not None:
        client.post.return_value = post_response
    if put_response is not None:
        client.put.return_value = put_response
    return client


def _make_provider(
    domain: str = "acme.freshdesk.com",
    api_key: str = "test-api-key",
    http_client: MagicMock | None = None,
) -> FreshdeskProvider:
    config = _config(domain=domain, api_key=api_key)
    client = http_client if http_client is not None else MagicMock(spec=httpx.Client)
    return FreshdeskProvider(config, http_client=client)


def _make_request(
    operation: str = "add_note",
    ticket_id: str = "12345",
    payload: dict[str, Any] | None = None,
    **overrides: Any,
) -> ProviderRequest:
    defaults = dict(
        request_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        action_id="action-1",
        case_id="case-1",
        ticket_id=ticket_id,
        client="acme",
        operation=operation,
        payload=payload if payload is not None else {"body": "Test note", "private": True},
    )
    defaults.update(overrides)
    return ProviderRequest(**defaults)


# ══════════════════════════════════════════════════════════════════════════════
# 1. FreshdeskConfig validation
# ══════════════════════════════════════════════════════════════════════════════


class TestFreshdeskConfig:
    def test_valid_config_constructs(self) -> None:
        cfg = FreshdeskConfig(domain="acme.freshdesk.com", api_key="key")
        assert cfg.domain == "acme.freshdesk.com"

    def test_domain_stored_correctly(self) -> None:
        cfg = FreshdeskConfig(domain="acme.freshdesk.com", api_key="key")
        assert cfg.domain == "acme.freshdesk.com"

    def test_api_key_stored_correctly(self) -> None:
        cfg = FreshdeskConfig(domain="acme.freshdesk.com", api_key="my-api-key")
        assert cfg.api_key == "my-api-key"

    def test_default_timeout_is_ten(self) -> None:
        cfg = FreshdeskConfig(domain="acme.freshdesk.com", api_key="key")
        assert cfg.timeout_seconds == 10.0

    def test_custom_timeout_stored(self) -> None:
        cfg = FreshdeskConfig(domain="acme.freshdesk.com", api_key="key", timeout_seconds=5.0)
        assert cfg.timeout_seconds == 5.0

    def test_base_url_returns_https(self) -> None:
        cfg = FreshdeskConfig(domain="acme.freshdesk.com", api_key="key")
        assert cfg.base_url == "https://acme.freshdesk.com"

    def test_frozen(self) -> None:
        cfg = FreshdeskConfig(domain="acme.freshdesk.com", api_key="key")
        with pytest.raises((AttributeError, TypeError)):
            cfg.domain = "other.freshdesk.com"  # type: ignore[misc]

    def test_empty_domain_raises(self) -> None:
        with pytest.raises(ValueError, match="domain"):
            FreshdeskConfig(domain="", api_key="key")

    def test_https_prefix_raises(self) -> None:
        with pytest.raises(ValueError, match="protocol"):
            FreshdeskConfig(domain="https://acme.freshdesk.com", api_key="key")

    def test_http_prefix_raises(self) -> None:
        with pytest.raises(ValueError, match="protocol"):
            FreshdeskConfig(domain="http://acme.freshdesk.com", api_key="key")

    def test_empty_api_key_raises(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            FreshdeskConfig(domain="acme.freshdesk.com", api_key="")

    def test_zero_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout_seconds"):
            FreshdeskConfig(domain="acme.freshdesk.com", api_key="key", timeout_seconds=0)

    def test_negative_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout_seconds"):
            FreshdeskConfig(domain="acme.freshdesk.com", api_key="key", timeout_seconds=-1.0)


# ══════════════════════════════════════════════════════════════════════════════
# 2. FreshdeskProvider capabilities
# ══════════════════════════════════════════════════════════════════════════════


class TestFreshdeskProviderCapabilities:
    def test_capabilities_contains_execute(self) -> None:
        provider = _make_provider()
        assert ProviderCapability.EXECUTE in provider.capabilities()

    def test_capabilities_contains_health_check(self) -> None:
        provider = _make_provider()
        assert ProviderCapability.HEALTH_CHECK in provider.capabilities()

    def test_capabilities_is_frozenset(self) -> None:
        provider = _make_provider()
        assert isinstance(provider.capabilities(), frozenset)

    def test_rollback_not_in_capabilities(self) -> None:
        provider = _make_provider()
        assert ProviderCapability.ROLLBACK not in provider.capabilities()

    def test_idempotent_not_in_capabilities(self) -> None:
        provider = _make_provider()
        assert ProviderCapability.IDEMPOTENT not in provider.capabilities()

    def test_provider_name_is_freshdesk(self) -> None:
        provider = _make_provider()
        assert provider.provider_name == "freshdesk"

    def test_provider_version_is_non_empty_string(self) -> None:
        provider = _make_provider()
        assert isinstance(provider.provider_version, str)
        assert len(provider.provider_version) > 0


# ══════════════════════════════════════════════════════════════════════════════
# 3. FreshdeskProvider metadata
# ══════════════════════════════════════════════════════════════════════════════


class TestFreshdeskProviderMetadata:
    def test_metadata_returns_provider_metadata(self) -> None:
        provider = _make_provider()
        assert isinstance(provider.metadata(), ProviderMetadata)

    def test_metadata_name_matches_provider_name(self) -> None:
        provider = _make_provider()
        assert provider.metadata().provider_name == provider.provider_name

    def test_metadata_version_matches_provider_version(self) -> None:
        provider = _make_provider()
        assert provider.metadata().provider_version == provider.provider_version

    def test_metadata_capabilities_match(self) -> None:
        provider = _make_provider()
        assert provider.metadata().capabilities == provider.capabilities()

    def test_is_provider_abc(self) -> None:
        provider = _make_provider()
        assert isinstance(provider, Provider)

    def test_metadata_description_non_empty(self) -> None:
        provider = _make_provider()
        assert len(provider.metadata().description) > 0


# ══════════════════════════════════════════════════════════════════════════════
# 4. health_check — success paths
# ══════════════════════════════════════════════════════════════════════════════


class TestFreshdeskHealthCheckSuccess:
    def test_healthy_returns_is_healthy_true(self) -> None:
        client = _mock_client(get_response=_mock_response(200, {"id": 1}))
        provider = _make_provider(http_client=client)
        result = provider.health_check()
        assert result.is_healthy is True

    def test_healthy_latency_ms_non_negative(self) -> None:
        client = _mock_client(get_response=_mock_response(200, {"id": 1}))
        provider = _make_provider(http_client=client)
        result = provider.health_check()
        assert result.latency_ms >= 0

    def test_healthy_provider_name_matches(self) -> None:
        client = _mock_client(get_response=_mock_response(200, {"id": 1}))
        provider = _make_provider(http_client=client)
        result = provider.health_check()
        assert result.provider_name == provider.provider_name

    def test_healthy_returns_provider_health_instance(self) -> None:
        client = _mock_client(get_response=_mock_response(200, {"id": 1}))
        provider = _make_provider(http_client=client)
        result = provider.health_check()
        assert isinstance(result, ProviderHealth)

    def test_healthy_message_is_none(self) -> None:
        client = _mock_client(get_response=_mock_response(200, {"id": 1}))
        provider = _make_provider(http_client=client)
        result = provider.health_check()
        assert result.message is None

    def test_healthy_checked_at_is_recent(self) -> None:
        before = datetime.now(tz=timezone.utc)
        client = _mock_client(get_response=_mock_response(200, {"id": 1}))
        provider = _make_provider(http_client=client)
        result = provider.health_check()
        after = datetime.now(tz=timezone.utc)
        assert before <= result.checked_at <= after

    def test_health_check_calls_correct_endpoint(self) -> None:
        client = _mock_client(get_response=_mock_response(200, {"id": 1}))
        provider = _make_provider(http_client=client)
        provider.health_check()
        client.get.assert_called_once_with("/api/v2/agents/me")


# ══════════════════════════════════════════════════════════════════════════════
# 5. health_check — failure paths (NEVER raises)
# ══════════════════════════════════════════════════════════════════════════════


class TestFreshdeskHealthCheckFailure:
    def test_http_401_returns_unhealthy(self) -> None:
        client = _mock_client(get_response=_mock_response(401, {}))
        provider = _make_provider(http_client=client)
        result = provider.health_check()
        assert result.is_healthy is False

    def test_http_403_returns_unhealthy(self) -> None:
        client = _mock_client(get_response=_mock_response(403, {}))
        provider = _make_provider(http_client=client)
        result = provider.health_check()
        assert result.is_healthy is False

    def test_http_500_returns_unhealthy(self) -> None:
        client = _mock_client(get_response=_mock_response(500, {}))
        provider = _make_provider(http_client=client)
        result = provider.health_check()
        assert result.is_healthy is False

    def test_http_503_returns_unhealthy(self) -> None:
        client = _mock_client(get_response=_mock_response(503, {}))
        provider = _make_provider(http_client=client)
        result = provider.health_check()
        assert result.is_healthy is False

    def test_timeout_exception_returns_unhealthy(self) -> None:
        client = MagicMock(spec=httpx.Client)
        client.get.side_effect = httpx.ReadTimeout("timed out")
        provider = _make_provider(http_client=client)
        result = provider.health_check()
        assert result.is_healthy is False

    def test_connect_error_returns_unhealthy(self) -> None:
        client = MagicMock(spec=httpx.Client)
        client.get.side_effect = httpx.ConnectError("connection refused")
        provider = _make_provider(http_client=client)
        result = provider.health_check()
        assert result.is_healthy is False

    def test_arbitrary_exception_returns_unhealthy(self) -> None:
        client = MagicMock(spec=httpx.Client)
        client.get.side_effect = RuntimeError("unexpected crash")
        provider = _make_provider(http_client=client)
        result = provider.health_check()
        assert result.is_healthy is False

    def test_failure_has_message(self) -> None:
        client = MagicMock(spec=httpx.Client)
        client.get.side_effect = httpx.ReadTimeout("timed out")
        provider = _make_provider(http_client=client)
        result = provider.health_check()
        assert result.message is not None
        assert len(result.message) > 0

    def test_unhealthy_http_message_contains_status(self) -> None:
        client = _mock_client(get_response=_mock_response(401))
        provider = _make_provider(http_client=client)
        result = provider.health_check()
        assert "401" in (result.message or "")

    def test_health_check_never_raises(self) -> None:
        client = MagicMock(spec=httpx.Client)
        client.get.side_effect = Exception("catastrophic failure")
        provider = _make_provider(http_client=client)
        provider.health_check()  # must not raise


# ══════════════════════════════════════════════════════════════════════════════
# 6. execute — add_note success
# ══════════════════════════════════════════════════════════════════════════════


class TestFreshdeskExecuteAddNote:
    def test_success_returns_provider_response(self) -> None:
        client = _mock_client(post_response=_mock_response(201, {"id": 99}))
        provider = _make_provider(http_client=client)
        request = _make_request(
            operation="add_note",
            ticket_id="12345",
            payload={"body": "Hello", "private": True},
        )
        result = provider.execute(request)
        assert isinstance(result, ProviderResponse)

    def test_success_is_true(self) -> None:
        client = _mock_client(post_response=_mock_response(201, {"id": 99}))
        provider = _make_provider(http_client=client)
        result = provider.execute(_make_request(operation="add_note"))
        assert result.success is True

    def test_provider_request_id_from_response(self) -> None:
        client = _mock_client(post_response=_mock_response(201, {"id": 42}))
        provider = _make_provider(http_client=client)
        result = provider.execute(_make_request(operation="add_note", ticket_id="777"))
        assert result.provider_request_id == "42"

    def test_result_contains_note_id(self) -> None:
        client = _mock_client(post_response=_mock_response(201, {"id": 99}))
        provider = _make_provider(http_client=client)
        result = provider.execute(_make_request(operation="add_note"))
        assert result.result.get("note_id") == "99"

    def test_status_code_echoed(self) -> None:
        client = _mock_client(post_response=_mock_response(201, {"id": 1}))
        provider = _make_provider(http_client=client)
        result = provider.execute(_make_request(operation="add_note"))
        assert result.status_code == 201

    def test_post_sent_to_correct_endpoint(self) -> None:
        client = _mock_client(post_response=_mock_response(201, {"id": 1}))
        provider = _make_provider(http_client=client)
        provider.execute(_make_request(operation="add_note", ticket_id="999"))
        client.post.assert_called_once()
        path_arg = client.post.call_args[0][0]
        assert "999" in path_arg
        assert "notes" in path_arg

    def test_request_id_echoed_in_response(self) -> None:
        rid = str(uuid.uuid4())
        client = _mock_client(post_response=_mock_response(201, {"id": 1}))
        provider = _make_provider(http_client=client)
        result = provider.execute(_make_request(operation="add_note", request_id=rid))
        assert result.request_id == rid

    def test_payload_body_forwarded(self) -> None:
        client = _mock_client(post_response=_mock_response(201, {"id": 1}))
        provider = _make_provider(http_client=client)
        provider.execute(_make_request(
            operation="add_note",
            payload={"body": "Specific note body", "private": False},
        ))
        _, kwargs = client.post.call_args
        assert kwargs["json"]["body"] == "Specific note body"
        assert kwargs["json"]["private"] is False


# ══════════════════════════════════════════════════════════════════════════════
# 7. execute — update_ticket success
# ══════════════════════════════════════════════════════════════════════════════


class TestFreshdeskExecuteUpdateTicket:
    def test_success_returns_provider_response(self) -> None:
        client = _mock_client(put_response=_mock_response(200, {"id": 12345}))
        provider = _make_provider(http_client=client)
        result = provider.execute(_make_request(
            operation="update_ticket",
            ticket_id="12345",
            payload={"status": 2},
        ))
        assert isinstance(result, ProviderResponse)

    def test_success_is_true(self) -> None:
        client = _mock_client(put_response=_mock_response(200, {"id": 12345}))
        provider = _make_provider(http_client=client)
        result = provider.execute(_make_request(
            operation="update_ticket",
            payload={"status": 2},
        ))
        assert result.success is True

    def test_provider_request_id_is_ticket_id(self) -> None:
        client = _mock_client(put_response=_mock_response(200, {"id": 555}))
        provider = _make_provider(http_client=client)
        result = provider.execute(_make_request(
            operation="update_ticket",
            ticket_id="555",
            payload={"status": 2},
        ))
        assert result.provider_request_id == "555"

    def test_put_sent_to_correct_endpoint(self) -> None:
        client = _mock_client(put_response=_mock_response(200, {"id": 888}))
        provider = _make_provider(http_client=client)
        provider.execute(_make_request(
            operation="update_ticket",
            ticket_id="888",
            payload={"status": 2},
        ))
        client.put.assert_called_once()
        path_arg = client.put.call_args[0][0]
        assert "888" in path_arg

    def test_payload_forwarded_to_put(self) -> None:
        client = _mock_client(put_response=_mock_response(200, {"id": 1}))
        provider = _make_provider(http_client=client)
        provider.execute(_make_request(
            operation="update_ticket",
            payload={"status": 3, "priority": 2},
        ))
        _, kwargs = client.put.call_args
        assert kwargs["json"]["status"] == 3
        assert kwargs["json"]["priority"] == 2

    def test_status_code_echoed(self) -> None:
        client = _mock_client(put_response=_mock_response(200, {"id": 1}))
        provider = _make_provider(http_client=client)
        result = provider.execute(_make_request(
            operation="update_ticket",
            payload={"status": 2},
        ))
        assert result.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# 8. execute — unsupported operation
# ══════════════════════════════════════════════════════════════════════════════


class TestFreshdeskUnsupportedOperation:
    def test_unsupported_op_raises_provider_execution_error(self) -> None:
        provider = _make_provider()
        with pytest.raises(ProviderExecutionError):
            provider.execute(_make_request(operation="delete_ticket"))

    def test_unsupported_op_error_code(self) -> None:
        provider = _make_provider()
        try:
            provider.execute(_make_request(operation="delete_ticket"))
        except ProviderExecutionError as exc:
            assert exc.error_code == "UNSUPPORTED_OPERATION"
        else:
            pytest.fail("Expected ProviderExecutionError")

    def test_unsupported_op_is_permanent(self) -> None:
        provider = _make_provider()
        with pytest.raises(ProviderPermanentError):
            provider.execute(_make_request(operation="noop"))

    def test_unsupported_op_carries_provider_name(self) -> None:
        provider = _make_provider()
        try:
            provider.execute(_make_request(operation="unknown"))
        except ProviderExecutionError as exc:
            assert exc.provider_name == "freshdesk"
        else:
            pytest.fail("Expected ProviderExecutionError")


# ══════════════════════════════════════════════════════════════════════════════
# 9. Error mapping — transient errors
# ══════════════════════════════════════════════════════════════════════════════


class TestFreshdeskErrorMappingTransient:
    def _provider_with_post_status(self, status: int) -> FreshdeskProvider:
        client = _mock_client(post_response=_mock_response(status, {}))
        return _make_provider(http_client=client)

    def test_429_raises_rate_limit_error(self) -> None:
        provider = self._provider_with_post_status(429)
        with pytest.raises(ProviderRateLimitError):
            provider.execute(_make_request(operation="add_note"))

    def test_429_is_retryable(self) -> None:
        provider = self._provider_with_post_status(429)
        try:
            provider.execute(_make_request(operation="add_note"))
        except ProviderRateLimitError as exc:
            assert exc.retryable is True
        else:
            pytest.fail("Expected ProviderRateLimitError")

    def test_500_raises_unavailable_error(self) -> None:
        provider = self._provider_with_post_status(500)
        with pytest.raises(ProviderUnavailableError):
            provider.execute(_make_request(operation="add_note"))

    def test_503_raises_unavailable_error(self) -> None:
        provider = self._provider_with_post_status(503)
        with pytest.raises(ProviderUnavailableError):
            provider.execute(_make_request(operation="add_note"))

    def test_5xx_is_retryable(self) -> None:
        provider = self._provider_with_post_status(500)
        try:
            provider.execute(_make_request(operation="add_note"))
        except ProviderUnavailableError as exc:
            assert exc.retryable is True
        else:
            pytest.fail("Expected ProviderUnavailableError")

    def test_timeout_exception_raises_provider_timeout_error(self) -> None:
        client = MagicMock(spec=httpx.Client)
        client.post.side_effect = httpx.ReadTimeout("timed out")
        provider = _make_provider(http_client=client)
        with pytest.raises(ProviderTimeoutError):
            provider.execute(_make_request(operation="add_note"))

    def test_connect_timeout_raises_provider_timeout_error(self) -> None:
        client = MagicMock(spec=httpx.Client)
        client.post.side_effect = httpx.ConnectTimeout("connect timed out")
        provider = _make_provider(http_client=client)
        with pytest.raises(ProviderTimeoutError):
            provider.execute(_make_request(operation="add_note"))

    def test_provider_timeout_error_is_retryable(self) -> None:
        client = MagicMock(spec=httpx.Client)
        client.post.side_effect = httpx.ReadTimeout("timed out")
        provider = _make_provider(http_client=client)
        try:
            provider.execute(_make_request(operation="add_note"))
        except ProviderTimeoutError as exc:
            assert exc.retryable is True
        else:
            pytest.fail("Expected ProviderTimeoutError")

    def test_connect_error_raises_unavailable_error(self) -> None:
        client = MagicMock(spec=httpx.Client)
        client.post.side_effect = httpx.ConnectError("connection refused")
        provider = _make_provider(http_client=client)
        with pytest.raises(ProviderUnavailableError):
            provider.execute(_make_request(operation="add_note"))

    def test_connect_error_is_retryable(self) -> None:
        client = MagicMock(spec=httpx.Client)
        client.post.side_effect = httpx.ConnectError("connection refused")
        provider = _make_provider(http_client=client)
        try:
            provider.execute(_make_request(operation="add_note"))
        except ProviderUnavailableError as exc:
            assert exc.retryable is True
        else:
            pytest.fail("Expected ProviderUnavailableError")

    def test_all_transient_catchable_as_provider_transient_error(self) -> None:
        transient_cases = [
            (429, ProviderRateLimitError),
            (500, ProviderUnavailableError),
            (503, ProviderUnavailableError),
        ]
        for status, exc_class in transient_cases:
            provider = self._provider_with_post_status(status)
            with pytest.raises(ProviderTransientError):
                provider.execute(_make_request(operation="add_note"))


# ══════════════════════════════════════════════════════════════════════════════
# 10. Error mapping — permanent errors
# ══════════════════════════════════════════════════════════════════════════════


class TestFreshdeskErrorMappingPermanent:
    def _provider_with_put_status(self, status: int) -> FreshdeskProvider:
        client = _mock_client(put_response=_mock_response(status, {}))
        return _make_provider(http_client=client)

    def test_401_raises_authentication_error(self) -> None:
        provider = self._provider_with_put_status(401)
        with pytest.raises(ProviderAuthenticationError):
            provider.execute(_make_request(
                operation="update_ticket", payload={"status": 2}
            ))

    def test_401_is_not_retryable(self) -> None:
        provider = self._provider_with_put_status(401)
        try:
            provider.execute(_make_request(
                operation="update_ticket", payload={"status": 2}
            ))
        except ProviderAuthenticationError as exc:
            assert exc.retryable is False
        else:
            pytest.fail("Expected ProviderAuthenticationError")

    def test_403_raises_authorization_error(self) -> None:
        provider = self._provider_with_put_status(403)
        with pytest.raises(ProviderAuthorizationError):
            provider.execute(_make_request(
                operation="update_ticket", payload={"status": 2}
            ))

    def test_403_is_not_retryable(self) -> None:
        provider = self._provider_with_put_status(403)
        try:
            provider.execute(_make_request(
                operation="update_ticket", payload={"status": 2}
            ))
        except ProviderAuthorizationError as exc:
            assert exc.retryable is False
        else:
            pytest.fail("Expected ProviderAuthorizationError")

    def test_404_raises_execution_error(self) -> None:
        client = _mock_client(post_response=_mock_response(404, {}))
        provider = _make_provider(http_client=client)
        with pytest.raises(ProviderExecutionError):
            provider.execute(_make_request(operation="add_note"))

    def test_404_error_code_resource_not_found(self) -> None:
        client = _mock_client(post_response=_mock_response(404, {}))
        provider = _make_provider(http_client=client)
        try:
            provider.execute(_make_request(operation="add_note"))
        except ProviderExecutionError as exc:
            assert exc.error_code == "RESOURCE_NOT_FOUND"
        else:
            pytest.fail("Expected ProviderExecutionError")

    def test_404_is_not_retryable(self) -> None:
        client = _mock_client(post_response=_mock_response(404, {}))
        provider = _make_provider(http_client=client)
        try:
            provider.execute(_make_request(operation="add_note"))
        except ProviderExecutionError as exc:
            assert exc.retryable is False
        else:
            pytest.fail("Expected ProviderExecutionError")

    def test_422_raises_validation_error(self) -> None:
        client = _mock_client(post_response=_mock_response(422, {}))
        provider = _make_provider(http_client=client)
        with pytest.raises(ProviderValidationError):
            provider.execute(_make_request(operation="add_note"))

    def test_422_is_not_retryable(self) -> None:
        client = _mock_client(post_response=_mock_response(422, {}))
        provider = _make_provider(http_client=client)
        try:
            provider.execute(_make_request(operation="add_note"))
        except ProviderValidationError as exc:
            assert exc.retryable is False
        else:
            pytest.fail("Expected ProviderValidationError")

    def test_all_permanent_catchable_as_provider_permanent_error(self) -> None:
        permanent_statuses = [401, 403, 404, 409, 422]
        for status in permanent_statuses:
            client = _mock_client(post_response=_mock_response(status, {}))
            provider = _make_provider(http_client=client)
            with pytest.raises(ProviderPermanentError):
                provider.execute(_make_request(operation="add_note"))

    def test_409_raises_execution_error(self) -> None:
        client = _mock_client(put_response=_mock_response(409, {}))
        provider = _make_provider(http_client=client)
        with pytest.raises(ProviderExecutionError):
            provider.execute(_make_request(
                operation="update_ticket", payload={"status": 2}
            ))

    def test_409_error_code_is_conflict(self) -> None:
        client = _mock_client(put_response=_mock_response(409, {}))
        provider = _make_provider(http_client=client)
        try:
            provider.execute(_make_request(
                operation="update_ticket", payload={"status": 2}
            ))
        except ProviderExecutionError as exc:
            assert exc.error_code == "CONFLICT"
        else:
            pytest.fail("Expected ProviderExecutionError")


# ══════════════════════════════════════════════════════════════════════════════
# 11. ProviderRouter integration
# ══════════════════════════════════════════════════════════════════════════════


class TestFreshdeskRouterIntegration:
    def _setup(self, health_status: int = 200, execute_status: int = 201) -> tuple[
        FreshdeskProvider, ProviderRouter
    ]:
        client = MagicMock(spec=httpx.Client)
        client.get.return_value = _mock_response(health_status, {"id": 1})
        client.post.return_value = _mock_response(execute_status, {"id": 42})
        client.put.return_value = _mock_response(200, {"id": 12345})
        provider = _make_provider(http_client=client)
        registry = ProviderRegistry()
        registry.register(provider)
        router = ProviderRouter(registry)
        return provider, router

    def test_register_in_registry_succeeds(self) -> None:
        provider, router = self._setup()
        assert router.provider_is_healthy("freshdesk") is True

    def test_route_returns_provider_response(self) -> None:
        _, router = self._setup()
        request = _make_request(operation="add_note")
        result = router.route("freshdesk", request)
        assert isinstance(result, ProviderResponse)

    def test_route_success_is_true(self) -> None:
        _, router = self._setup()
        result = router.route("freshdesk", _make_request(operation="add_note"))
        assert result.success is True

    def test_provider_has_execute_capability(self) -> None:
        _, router = self._setup()
        assert router.provider_has_capability("freshdesk", ProviderCapability.EXECUTE) is True

    def test_provider_has_health_check_capability(self) -> None:
        _, router = self._setup()
        assert router.provider_has_capability("freshdesk", ProviderCapability.HEALTH_CHECK) is True

    def test_provider_lacks_rollback_capability(self) -> None:
        _, router = self._setup()
        assert router.provider_has_capability("freshdesk", ProviderCapability.ROLLBACK) is False

    def test_unhealthy_provider_raises_unavailable(self) -> None:
        _, router = self._setup(health_status=500)
        from case_engine.provider_exceptions import ProviderUnavailableError
        with pytest.raises(ProviderUnavailableError):
            router.route("freshdesk", _make_request(operation="add_note"))

    def test_provider_is_healthy_when_200(self) -> None:
        _, router = self._setup(health_status=200)
        assert router.provider_is_healthy("freshdesk") is True

    def test_provider_is_unhealthy_when_500(self) -> None:
        _, router = self._setup(health_status=500)
        assert router.provider_is_healthy("freshdesk") is False


# ══════════════════════════════════════════════════════════════════════════════
# 12. Freshdesk exception hierarchy contracts
# ══════════════════════════════════════════════════════════════════════════════


class TestFreshdeskExceptionHierarchy:
    def test_freshdesk_api_error_carries_status_code(self) -> None:
        exc = FreshdeskApiError("test", status_code=500)
        assert exc.status_code == 500

    def test_freshdesk_api_error_empty_body_default(self) -> None:
        exc = FreshdeskApiError("test", status_code=500)
        assert exc.response_body == {}

    def test_freshdesk_api_error_carries_body(self) -> None:
        exc = FreshdeskApiError("test", status_code=422, response_body={"errors": ["bad"]})
        assert exc.response_body == {"errors": ["bad"]}

    def test_all_specific_errors_are_freshdesk_api_error(self) -> None:
        specific = [
            FreshdeskAuthError("t", status_code=401),
            FreshdeskForbiddenError("t", status_code=403),
            FreshdeskNotFoundError("t", status_code=404),
            FreshdeskConflictError("t", status_code=409),
            FreshdeskValidationError("t", status_code=422),
            FreshdeskRateLimitError("t", status_code=429),
            FreshdeskServerError("t", status_code=500),
        ]
        for exc in specific:
            assert isinstance(exc, FreshdeskApiError)
            assert isinstance(exc, FreshdeskError)

    def test_connection_error_is_freshdesk_error(self) -> None:
        assert isinstance(FreshdeskConnectionError("t"), FreshdeskError)

    def test_timeout_exception_is_freshdesk_error(self) -> None:
        assert isinstance(FreshdeskTimeoutException("t"), FreshdeskError)

    def test_connection_error_not_api_error(self) -> None:
        assert not isinstance(FreshdeskConnectionError("t"), FreshdeskApiError)

    def test_timeout_not_api_error(self) -> None:
        assert not isinstance(FreshdeskTimeoutException("t"), FreshdeskApiError)

    def test_provider_timeout_carries_provider_name(self) -> None:
        client = MagicMock(spec=httpx.Client)
        client.post.side_effect = httpx.ReadTimeout("timed out")
        provider = _make_provider(http_client=client)
        try:
            provider.execute(_make_request(operation="add_note"))
        except ProviderTimeoutError as exc:
            assert exc.provider_name == "freshdesk"
        else:
            pytest.fail("Expected ProviderTimeoutError")

    def test_provider_auth_error_carries_provider_name(self) -> None:
        client = _mock_client(post_response=_mock_response(401, {}))
        provider = _make_provider(http_client=client)
        try:
            provider.execute(_make_request(operation="add_note"))
        except ProviderAuthenticationError as exc:
            assert exc.provider_name == "freshdesk"
        else:
            pytest.fail("Expected ProviderAuthenticationError")

    def test_all_provider_errors_catchable_as_provider_error(self) -> None:
        statuses = [401, 403, 404, 422, 429, 500]
        for status in statuses:
            client = _mock_client(post_response=_mock_response(status, {}))
            provider = _make_provider(http_client=client)
            with pytest.raises(ProviderError):
                provider.execute(_make_request(operation="add_note"))
