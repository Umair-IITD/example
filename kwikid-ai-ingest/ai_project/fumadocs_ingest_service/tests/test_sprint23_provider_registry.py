"""
tests/test_sprint23_provider_registry.py

Sprint 2.3 — ProviderRegistry contract tests.

Covers:
  - Registration of a valid provider succeeds
  - Duplicate provider_name raises ProviderRegistrationError
  - get_provider returns the correct instance
  - get_provider raises UnknownProviderError on miss
  - UnknownProviderError carries provider_name attribute
  - UnknownProviderError is a KeyError
  - unregister removes a provider
  - unregister raises UnknownProviderError for unknown name
  - list_providers returns sorted names
  - provider_exists works correctly
  - registered_count is consistent
  - health_check_all returns ProviderHealth for all providers
  - health_check_all catches exceptions from health_check()
  - providers_with_capability filters by capability
  - Independent registries do not share state
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from case_engine.provider_exceptions import ProviderError
from case_engine.provider_interface import Provider
from case_engine.provider_models import (
    ProviderCapability,
    ProviderHealth,
    ProviderMetadata,
    ProviderRequest,
    ProviderResponse,
)
from case_engine.provider_registry import (
    ProviderRegistrationError,
    ProviderRegistry,
    UnknownProviderError,
)


# ── Test infrastructure ────────────────────────────────────────────────────────


def _make_provider(
    name: str = "freshdesk",
    version: str = "1.0.0",
    capabilities: frozenset[ProviderCapability] = frozenset({ProviderCapability.EXECUTE}),
    healthy: bool = True,
    health_raises: bool = False,
    execute_mode: str = "success",
) -> Provider:
    class _Stub(Provider):
        @property
        def provider_name(self) -> str:
            return name

        @property
        def provider_version(self) -> str:
            return version

        def capabilities(self) -> frozenset[ProviderCapability]:
            return capabilities

        def health_check(self) -> ProviderHealth:
            if health_raises:
                raise RuntimeError("provider down")
            return ProviderHealth(
                provider_name=name,
                provider_version=version,
                is_healthy=healthy,
                latency_ms=5,
                checked_at=datetime.now(tz=timezone.utc),
                message=None if healthy else "unavailable",
            )

        def execute(self, request: ProviderRequest) -> ProviderResponse:
            if execute_mode == "transient":
                from case_engine.provider_exceptions import ProviderTransientError
                raise ProviderTransientError("timeout")
            return ProviderResponse(
                request_id=request.request_id,
                provider_request_id="FD-1",
                success=True,
            )

    return _Stub()


# ── Registration ───────────────────────────────────────────────────────────────


class TestRegistration:
    def test_register_single_provider_succeeds(self) -> None:
        reg = ProviderRegistry()
        reg.register(_make_provider("freshdesk"))
        assert reg.registered_count() == 1

    def test_register_two_different_providers(self) -> None:
        reg = ProviderRegistry()
        reg.register(_make_provider("freshdesk"))
        reg.register(_make_provider("zendesk"))
        assert reg.registered_count() == 2

    def test_registered_count_zero_initially(self) -> None:
        assert ProviderRegistry().registered_count() == 0

    def test_provider_exists_after_registration(self) -> None:
        reg = ProviderRegistry()
        reg.register(_make_provider("freshdesk"))
        assert reg.provider_exists("freshdesk") is True

    def test_provider_exists_false_before_registration(self) -> None:
        assert ProviderRegistry().provider_exists("freshdesk") is False


# ── Duplicate registration ─────────────────────────────────────────────────────


class TestDuplicateRegistration:
    def test_duplicate_raises_registration_error(self) -> None:
        reg = ProviderRegistry()
        reg.register(_make_provider("freshdesk"))
        with pytest.raises(ProviderRegistrationError):
            reg.register(_make_provider("freshdesk"))

    def test_error_message_contains_provider_name(self) -> None:
        reg = ProviderRegistry()
        reg.register(_make_provider("freshdesk"))
        with pytest.raises(ProviderRegistrationError, match="freshdesk"):
            reg.register(_make_provider("freshdesk"))

    def test_duplicate_does_not_overwrite_original(self) -> None:
        reg = ProviderRegistry()
        first = _make_provider("freshdesk")
        reg.register(first)
        try:
            reg.register(_make_provider("freshdesk"))
        except ProviderRegistrationError:
            pass
        assert reg.get_provider("freshdesk") is first

    def test_count_unchanged_after_duplicate(self) -> None:
        reg = ProviderRegistry()
        reg.register(_make_provider("freshdesk"))
        try:
            reg.register(_make_provider("freshdesk"))
        except ProviderRegistrationError:
            pass
        assert reg.registered_count() == 1


# ── Lookup ─────────────────────────────────────────────────────────────────────


class TestLookup:
    def test_get_provider_returns_correct_instance(self) -> None:
        reg = ProviderRegistry()
        fd = _make_provider("freshdesk")
        zd = _make_provider("zendesk")
        reg.register(fd)
        reg.register(zd)
        assert reg.get_provider("freshdesk") is fd
        assert reg.get_provider("zendesk") is zd

    def test_get_provider_raises_unknown_on_miss(self) -> None:
        with pytest.raises(UnknownProviderError):
            ProviderRegistry().get_provider("freshdesk")

    def test_get_provider_raises_for_wrong_name(self) -> None:
        reg = ProviderRegistry()
        reg.register(_make_provider("freshdesk"))
        with pytest.raises(UnknownProviderError):
            reg.get_provider("zendesk")


# ── UnknownProviderError ───────────────────────────────────────────────────────


class TestUnknownProviderError:
    def test_carries_provider_name(self) -> None:
        try:
            ProviderRegistry().get_provider("freshdesk")
        except UnknownProviderError as exc:
            assert exc.provider_name == "freshdesk"
        else:
            pytest.fail("Expected UnknownProviderError")

    def test_is_key_error(self) -> None:
        with pytest.raises(KeyError):
            ProviderRegistry().get_provider("freshdesk")

    def test_message_contains_provider_name(self) -> None:
        with pytest.raises(UnknownProviderError, match="freshdesk"):
            ProviderRegistry().get_provider("freshdesk")


# ── Unregister ─────────────────────────────────────────────────────────────────


class TestUnregister:
    def test_unregister_removes_provider(self) -> None:
        reg = ProviderRegistry()
        reg.register(_make_provider("freshdesk"))
        reg.unregister("freshdesk")
        assert reg.provider_exists("freshdesk") is False

    def test_unregister_decrements_count(self) -> None:
        reg = ProviderRegistry()
        reg.register(_make_provider("freshdesk"))
        reg.register(_make_provider("zendesk"))
        reg.unregister("freshdesk")
        assert reg.registered_count() == 1

    def test_unregister_unknown_raises(self) -> None:
        with pytest.raises(UnknownProviderError):
            ProviderRegistry().unregister("freshdesk")

    def test_can_re_register_after_unregister(self) -> None:
        reg = ProviderRegistry()
        reg.register(_make_provider("freshdesk"))
        reg.unregister("freshdesk")
        reg.register(_make_provider("freshdesk"))  # should not raise
        assert reg.registered_count() == 1

    def test_unregister_error_carries_provider_name(self) -> None:
        try:
            ProviderRegistry().unregister("freshdesk")
        except UnknownProviderError as exc:
            assert exc.provider_name == "freshdesk"
        else:
            pytest.fail("Expected UnknownProviderError")


# ── list_providers ─────────────────────────────────────────────────────────────


class TestListProviders:
    def test_empty_registry_returns_empty_list(self) -> None:
        assert ProviderRegistry().list_providers() == []

    def test_returns_sorted_names(self) -> None:
        reg = ProviderRegistry()
        reg.register(_make_provider("zendesk"))
        reg.register(_make_provider("freshdesk"))
        reg.register(_make_provider("salesforce"))
        names = reg.list_providers()
        assert names == sorted(names)

    def test_contains_all_registered_names(self) -> None:
        reg = ProviderRegistry()
        reg.register(_make_provider("freshdesk"))
        reg.register(_make_provider("zendesk"))
        assert "freshdesk" in reg.list_providers()
        assert "zendesk" in reg.list_providers()

    def test_count_matches_list_length(self) -> None:
        reg = ProviderRegistry()
        reg.register(_make_provider("freshdesk"))
        reg.register(_make_provider("zendesk"))
        assert reg.registered_count() == len(reg.list_providers())


# ── health_check_all ───────────────────────────────────────────────────────────


class TestHealthCheckAll:
    def test_empty_registry_returns_empty_dict(self) -> None:
        assert ProviderRegistry().health_check_all() == {}

    def test_returns_health_for_each_provider(self) -> None:
        reg = ProviderRegistry()
        reg.register(_make_provider("freshdesk"))
        reg.register(_make_provider("zendesk"))
        health = reg.health_check_all()
        assert "freshdesk" in health
        assert "zendesk" in health

    def test_healthy_provider_is_healthy(self) -> None:
        reg = ProviderRegistry()
        reg.register(_make_provider("freshdesk", healthy=True))
        health = reg.health_check_all()
        assert health["freshdesk"].is_healthy is True

    def test_unhealthy_provider_reported(self) -> None:
        reg = ProviderRegistry()
        reg.register(_make_provider("freshdesk", healthy=False))
        health = reg.health_check_all()
        assert health["freshdesk"].is_healthy is False

    def test_health_check_exception_treated_as_unhealthy(self) -> None:
        reg = ProviderRegistry()
        reg.register(_make_provider("freshdesk", health_raises=True))
        health = reg.health_check_all()
        assert health["freshdesk"].is_healthy is False
        assert health["freshdesk"].message is not None
        assert "health_check raised" in health["freshdesk"].message

    def test_health_check_never_raises(self) -> None:
        reg = ProviderRegistry()
        reg.register(_make_provider("freshdesk", health_raises=True))
        reg.health_check_all()  # must not raise

    def test_health_result_is_provider_health_instance(self) -> None:
        reg = ProviderRegistry()
        reg.register(_make_provider("freshdesk"))
        health = reg.health_check_all()
        assert isinstance(health["freshdesk"], ProviderHealth)


# ── providers_with_capability ──────────────────────────────────────────────────


class TestProvidersWithCapability:
    def test_returns_providers_with_execute(self) -> None:
        reg = ProviderRegistry()
        reg.register(_make_provider("freshdesk", capabilities=frozenset({ProviderCapability.EXECUTE})))
        reg.register(_make_provider("zendesk", capabilities=frozenset({ProviderCapability.ROLLBACK})))
        result = reg.providers_with_capability(ProviderCapability.EXECUTE)
        assert "freshdesk" in result
        assert "zendesk" not in result

    def test_returns_empty_when_no_match(self) -> None:
        reg = ProviderRegistry()
        reg.register(_make_provider("freshdesk", capabilities=frozenset({ProviderCapability.EXECUTE})))
        result = reg.providers_with_capability(ProviderCapability.ROLLBACK)
        assert result == []

    def test_returns_sorted_names(self) -> None:
        reg = ProviderRegistry()
        caps = frozenset({ProviderCapability.EXECUTE})
        reg.register(_make_provider("zendesk", capabilities=caps))
        reg.register(_make_provider("freshdesk", capabilities=caps))
        result = reg.providers_with_capability(ProviderCapability.EXECUTE)
        assert result == sorted(result)

    def test_provider_with_multiple_capabilities(self) -> None:
        reg = ProviderRegistry()
        caps = frozenset({ProviderCapability.EXECUTE, ProviderCapability.ROLLBACK})
        reg.register(_make_provider("freshdesk", capabilities=caps))
        assert "freshdesk" in reg.providers_with_capability(ProviderCapability.EXECUTE)
        assert "freshdesk" in reg.providers_with_capability(ProviderCapability.ROLLBACK)


# ── Isolation ──────────────────────────────────────────────────────────────────


class TestIsolation:
    def test_independent_registries_do_not_share_state(self) -> None:
        r1 = ProviderRegistry()
        r2 = ProviderRegistry()
        r1.register(_make_provider("freshdesk"))
        assert r2.registered_count() == 0
        assert not r2.provider_exists("freshdesk")
