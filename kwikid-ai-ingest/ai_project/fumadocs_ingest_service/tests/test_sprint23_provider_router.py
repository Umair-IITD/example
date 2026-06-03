"""
tests/test_sprint23_provider_router.py

Sprint 2.3 — ProviderRouter + Provider interface contract tests.

Covers:
  ProviderRouter.route():
    - Returns ProviderResponse on successful execution
    - Raises UnknownProviderError if provider not registered
    - Raises ProviderCapabilityError if capability not supported
    - Raises ProviderUnavailableError if health check fails
    - Health check exception treated as unhealthy
    - Passes ProviderError from execute() through unchanged
    - Wraps unexpected non-ProviderError as ProviderError
    - Default required_capability is EXECUTE
    - Forwards request to correct provider
    - rollback capability validation works

  ProviderRouter.provider_is_healthy():
    - Returns True for healthy registered provider
    - Returns False for unhealthy provider
    - Returns False for unregistered provider
    - Returns False when health_check raises

  ProviderRouter.provider_has_capability():
    - Returns True when capability is declared
    - Returns False when capability is not declared
    - Returns False for unregistered provider

  Provider interface contracts:
    - Cannot instantiate Provider directly (ABC)
    - Concrete implementation satisfies the contract
    - metadata() returns ProviderMetadata
    - capabilities() returns frozenset
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from case_engine.provider_exceptions import (
    ProviderCapabilityError,
    ProviderError,
    ProviderExecutionError,
    ProviderPermanentError,
    ProviderTimeoutError,
    ProviderTransientError,
    ProviderUnavailableError,
)
from case_engine.provider_interface import Provider
from case_engine.provider_models import (
    ProviderCapability,
    ProviderHealth,
    ProviderMetadata,
    ProviderRequest,
    ProviderResponse,
)
from case_engine.provider_registry import ProviderRegistry, UnknownProviderError
from case_engine.provider_router import ProviderRouter


# ══════════════════════════════════════════════════════════════════════════════
# Test infrastructure
# ══════════════════════════════════════════════════════════════════════════════


def _make_provider(
    name: str = "freshdesk",
    version: str = "1.0.0",
    capabilities: frozenset[ProviderCapability] | None = None,
    healthy: bool = True,
    health_raises: bool = False,
    execute_mode: str = "success",
) -> Provider:
    _caps = capabilities if capabilities is not None else frozenset({ProviderCapability.EXECUTE})

    class _Stub(Provider):
        @property
        def provider_name(self) -> str:
            return name

        @property
        def provider_version(self) -> str:
            return version

        def capabilities(self) -> frozenset[ProviderCapability]:
            return _caps

        def health_check(self) -> ProviderHealth:
            if health_raises:
                raise RuntimeError("network error")
            return ProviderHealth(
                provider_name=name,
                provider_version=version,
                is_healthy=healthy,
                latency_ms=5,
                checked_at=datetime.now(tz=timezone.utc),
                message=None if healthy else "provider unavailable",
            )

        def execute(self, request: ProviderRequest) -> ProviderResponse:
            if execute_mode == "transient":
                raise ProviderTransientError("timeout", error_code="TIMEOUT")
            if execute_mode == "permanent":
                raise ProviderExecutionError("not found", error_code="NOT_FOUND")
            if execute_mode == "exception":
                raise RuntimeError("unexpected crash")
            return ProviderResponse(
                request_id=request.request_id,
                provider_request_id="FD-12345",
                success=True,
                result={"ticket_id": "TKT-1"},
            )

    return _Stub()


def _make_request(**overrides: Any) -> ProviderRequest:
    defaults = dict(
        request_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        action_id="action-1",
        case_id="case-1",
        ticket_id="ticket-1",
        client="acme",
        operation="reset_otp",
        payload={"account_id": "ACC-123"},
    )
    defaults.update(overrides)
    return ProviderRequest(**defaults)


def _make_router(*providers: Provider) -> ProviderRouter:
    reg = ProviderRegistry()
    for p in providers:
        reg.register(p)
    return ProviderRouter(reg)


# ══════════════════════════════════════════════════════════════════════════════
# ProviderRouter.route() — Success
# ══════════════════════════════════════════════════════════════════════════════


class TestRouteSuccess:
    def test_returns_provider_response(self) -> None:
        router = _make_router(_make_provider("freshdesk"))
        result = router.route("freshdesk", _make_request())
        assert isinstance(result, ProviderResponse)

    def test_result_success_true(self) -> None:
        router = _make_router(_make_provider("freshdesk"))
        result = router.route("freshdesk", _make_request())
        assert result.success is True

    def test_result_has_provider_reference(self) -> None:
        router = _make_router(_make_provider("freshdesk"))
        result = router.route("freshdesk", _make_request())
        assert result.provider_request_id == "FD-12345"

    def test_request_id_echoed_in_response(self) -> None:
        rid = str(uuid.uuid4())
        router = _make_router(_make_provider("freshdesk"))
        result = router.route("freshdesk", _make_request(request_id=rid))
        assert result.request_id == rid

    def test_routes_to_correct_provider(self) -> None:
        router = _make_router(
            _make_provider("freshdesk"),
            _make_provider("zendesk"),
        )
        result = router.route("freshdesk", _make_request())
        assert isinstance(result, ProviderResponse)

    def test_default_capability_is_execute(self) -> None:
        router = _make_router(_make_provider(
            "freshdesk",
            capabilities=frozenset({ProviderCapability.EXECUTE}),
        ))
        result = router.route("freshdesk", _make_request())
        assert result.success is True


# ══════════════════════════════════════════════════════════════════════════════
# ProviderRouter.route() — Guard Rails
# ══════════════════════════════════════════════════════════════════════════════


class TestRouteGuardRails:
    def test_unknown_provider_raises(self) -> None:
        router = _make_router()
        with pytest.raises(UnknownProviderError):
            router.route("freshdesk", _make_request())

    def test_unknown_provider_error_carries_name(self) -> None:
        router = _make_router()
        try:
            router.route("freshdesk", _make_request())
        except UnknownProviderError as exc:
            assert exc.provider_name == "freshdesk"
        else:
            pytest.fail("Expected UnknownProviderError")

    def test_missing_capability_raises_capability_error(self) -> None:
        provider = _make_provider(
            "freshdesk",
            capabilities=frozenset({ProviderCapability.ROLLBACK}),
        )
        router = _make_router(provider)
        with pytest.raises(ProviderCapabilityError):
            router.route("freshdesk", _make_request(), required_capability=ProviderCapability.EXECUTE)

    def test_capability_error_carries_provider_name(self) -> None:
        provider = _make_provider(
            "freshdesk",
            capabilities=frozenset({ProviderCapability.ROLLBACK}),
        )
        router = _make_router(provider)
        try:
            router.route("freshdesk", _make_request(), required_capability=ProviderCapability.EXECUTE)
        except ProviderCapabilityError as exc:
            assert exc.provider_name == "freshdesk"
        else:
            pytest.fail("Expected ProviderCapabilityError")

    def test_capability_error_is_permanent(self) -> None:
        provider = _make_provider(
            "freshdesk",
            capabilities=frozenset({ProviderCapability.ROLLBACK}),
        )
        router = _make_router(provider)
        with pytest.raises(ProviderPermanentError):
            router.route("freshdesk", _make_request(), required_capability=ProviderCapability.EXECUTE)

    def test_unhealthy_provider_raises_unavailable(self) -> None:
        provider = _make_provider("freshdesk", healthy=False)
        router = _make_router(provider)
        with pytest.raises(ProviderUnavailableError):
            router.route("freshdesk", _make_request())

    def test_unavailable_is_transient(self) -> None:
        provider = _make_provider("freshdesk", healthy=False)
        router = _make_router(provider)
        try:
            router.route("freshdesk", _make_request())
        except ProviderUnavailableError as exc:
            assert exc.retryable is True
        else:
            pytest.fail("Expected ProviderUnavailableError")

    def test_health_check_exception_treated_as_unavailable(self) -> None:
        provider = _make_provider("freshdesk", health_raises=True)
        router = _make_router(provider)
        with pytest.raises(ProviderUnavailableError):
            router.route("freshdesk", _make_request())

    def test_rollback_capability_validation(self) -> None:
        provider = _make_provider(
            "freshdesk",
            capabilities=frozenset({ProviderCapability.EXECUTE, ProviderCapability.ROLLBACK}),
        )
        router = _make_router(provider)
        result = router.route(
            "freshdesk", _make_request(),
            required_capability=ProviderCapability.ROLLBACK,
        )
        assert result.success is True

    def test_empty_capabilities_raises_capability_error(self) -> None:
        provider = _make_provider("freshdesk", capabilities=frozenset())
        router = _make_router(provider)
        with pytest.raises(ProviderCapabilityError):
            router.route("freshdesk", _make_request())


# ══════════════════════════════════════════════════════════════════════════════
# ProviderRouter.route() — Exception Propagation
# ══════════════════════════════════════════════════════════════════════════════


class TestRouteExceptionPropagation:
    def test_provider_transient_error_propagates(self) -> None:
        router = _make_router(_make_provider("freshdesk", execute_mode="transient"))
        with pytest.raises(ProviderTransientError):
            router.route("freshdesk", _make_request())

    def test_provider_permanent_error_propagates(self) -> None:
        router = _make_router(_make_provider("freshdesk", execute_mode="permanent"))
        with pytest.raises(ProviderExecutionError):
            router.route("freshdesk", _make_request())

    def test_unexpected_exception_wrapped_as_provider_error(self) -> None:
        router = _make_router(_make_provider("freshdesk", execute_mode="exception"))
        with pytest.raises(ProviderError) as exc_info:
            router.route("freshdesk", _make_request())
        assert exc_info.value.error_code == "PROVIDER_UNEXPECTED_ERROR"

    def test_unexpected_exception_carries_provider_name(self) -> None:
        router = _make_router(_make_provider("freshdesk", execute_mode="exception"))
        with pytest.raises(ProviderError) as exc_info:
            router.route("freshdesk", _make_request())
        assert exc_info.value.provider_name == "freshdesk"

    def test_transient_error_is_retryable(self) -> None:
        router = _make_router(_make_provider("freshdesk", execute_mode="transient"))
        try:
            router.route("freshdesk", _make_request())
        except ProviderTransientError as exc:
            assert exc.retryable is True
        else:
            pytest.fail("Expected ProviderTransientError")

    def test_permanent_error_is_not_retryable(self) -> None:
        router = _make_router(_make_provider("freshdesk", execute_mode="permanent"))
        try:
            router.route("freshdesk", _make_request())
        except ProviderPermanentError as exc:
            assert exc.retryable is False
        else:
            pytest.fail("Expected ProviderPermanentError")


# ══════════════════════════════════════════════════════════════════════════════
# ProviderRouter.provider_is_healthy()
# ══════════════════════════════════════════════════════════════════════════════


class TestProviderIsHealthy:
    def test_returns_true_for_healthy(self) -> None:
        router = _make_router(_make_provider("freshdesk", healthy=True))
        assert router.provider_is_healthy("freshdesk") is True

    def test_returns_false_for_unhealthy(self) -> None:
        router = _make_router(_make_provider("freshdesk", healthy=False))
        assert router.provider_is_healthy("freshdesk") is False

    def test_returns_false_for_unregistered(self) -> None:
        router = _make_router()
        assert router.provider_is_healthy("freshdesk") is False

    def test_returns_false_when_health_check_raises(self) -> None:
        router = _make_router(_make_provider("freshdesk", health_raises=True))
        assert router.provider_is_healthy("freshdesk") is False

    def test_never_raises(self) -> None:
        router = _make_router()
        router.provider_is_healthy("nonexistent")  # must not raise


# ══════════════════════════════════════════════════════════════════════════════
# ProviderRouter.provider_has_capability()
# ══════════════════════════════════════════════════════════════════════════════


class TestProviderHasCapability:
    def test_returns_true_when_declared(self) -> None:
        router = _make_router(_make_provider(
            "freshdesk",
            capabilities=frozenset({ProviderCapability.EXECUTE}),
        ))
        assert router.provider_has_capability("freshdesk", ProviderCapability.EXECUTE) is True

    def test_returns_false_when_not_declared(self) -> None:
        router = _make_router(_make_provider(
            "freshdesk",
            capabilities=frozenset({ProviderCapability.EXECUTE}),
        ))
        assert router.provider_has_capability("freshdesk", ProviderCapability.ROLLBACK) is False

    def test_returns_false_for_unregistered(self) -> None:
        router = _make_router()
        assert router.provider_has_capability("freshdesk", ProviderCapability.EXECUTE) is False

    def test_multiple_capabilities(self) -> None:
        router = _make_router(_make_provider(
            "freshdesk",
            capabilities=frozenset({ProviderCapability.EXECUTE, ProviderCapability.ROLLBACK}),
        ))
        assert router.provider_has_capability("freshdesk", ProviderCapability.EXECUTE) is True
        assert router.provider_has_capability("freshdesk", ProviderCapability.ROLLBACK) is True
        assert router.provider_has_capability("freshdesk", ProviderCapability.IDEMPOTENT) is False

    def test_never_raises(self) -> None:
        router = _make_router()
        router.provider_has_capability("nonexistent", ProviderCapability.EXECUTE)  # must not raise


# ══════════════════════════════════════════════════════════════════════════════
# Provider Interface Contracts
# ══════════════════════════════════════════════════════════════════════════════


class TestProviderInterfaceContracts:
    def test_cannot_instantiate_abstract_provider(self) -> None:
        with pytest.raises(TypeError):
            Provider()  # type: ignore[abstract]

    def test_concrete_provider_satisfies_contract(self) -> None:
        provider = _make_provider("freshdesk")
        assert isinstance(provider, Provider)

    def test_provider_name_is_string(self) -> None:
        provider = _make_provider("freshdesk")
        assert isinstance(provider.provider_name, str)

    def test_provider_version_is_string(self) -> None:
        provider = _make_provider("freshdesk")
        assert isinstance(provider.provider_version, str)

    def test_capabilities_returns_frozenset(self) -> None:
        provider = _make_provider("freshdesk")
        assert isinstance(provider.capabilities(), frozenset)

    def test_health_check_returns_provider_health(self) -> None:
        provider = _make_provider("freshdesk")
        result = provider.health_check()
        assert isinstance(result, ProviderHealth)

    def test_execute_returns_provider_response(self) -> None:
        provider = _make_provider("freshdesk")
        request = _make_request()
        result = provider.execute(request)
        assert isinstance(result, ProviderResponse)

    def test_metadata_returns_provider_metadata(self) -> None:
        provider = _make_provider("freshdesk")
        meta = provider.metadata()
        assert isinstance(meta, ProviderMetadata)

    def test_metadata_name_matches_provider_name(self) -> None:
        provider = _make_provider("freshdesk")
        assert provider.metadata().provider_name == provider.provider_name

    def test_metadata_version_matches_provider_version(self) -> None:
        provider = _make_provider("freshdesk")
        assert provider.metadata().provider_version == provider.provider_version

    def test_metadata_capabilities_match(self) -> None:
        caps = frozenset({ProviderCapability.EXECUTE, ProviderCapability.ROLLBACK})
        provider = _make_provider("freshdesk", capabilities=caps)
        assert provider.metadata().capabilities == caps
