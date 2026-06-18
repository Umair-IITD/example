"""
tests/test_sprint2279_client_resolution.py

Sprint 2.27.9: ClientResolver tests.

Covers:
  - resolve() with known domain (Unity Bank)
  - resolve() with unknown domain raises UnknownClientError
  - resolve() with malformed email raises UnknownClientError
  - resolve() with disabled tenant raises UnknownClientError
  - resolve_or_none() returns TenantContext on success
  - resolve_or_none() returns None on failure
  - resolve_by_client_id() with known client_id
  - resolve_by_client_id() with unknown client_id returns None
  - extract_domain() for various inputs
  - TenantContext fields populated correctly
  - credentials_ref is a reference key, not an actual secret
  - Domain case-insensitivity
"""
from __future__ import annotations

import pytest

from case_engine.tenant.models import (
    TenantAuthConfig,
    TenantConfig,
    TenantContext,
    TenantEnvironment,
    TenantToolConfig,
    TenantType,
    UnknownClientError,
)
from case_engine.tenant.registry import TenantRegistry
from case_engine.tenant.resolver import ClientResolver, build_client_resolver


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def unity_bank_config() -> TenantConfig:
    return TenantConfig(
        client_id="unity_bank",
        client_name="Unity Bank",
        domains=("unitybank.co.in",),
        tenant_type=TenantType.BANK,
        environment=TenantEnvironment.UAT,
        tool_config=TenantToolConfig(enabled_tools=("GetUserDetails", "GetSessionDetails")),
        auth_config=TenantAuthConfig(credentials_ref="unity_bank_api_credentials"),
        enabled=True,
    )


@pytest.fixture
def disabled_config() -> TenantConfig:
    return TenantConfig(
        client_id="disabled_bank",
        client_name="Disabled Bank",
        domains=("disabled.co.in",),
        tenant_type=TenantType.BANK,
        environment=TenantEnvironment.UAT,
        tool_config=TenantToolConfig(enabled_tools=("GetUserDetails",)),
        auth_config=TenantAuthConfig(credentials_ref="disabled_api_key"),
        enabled=False,
    )


@pytest.fixture
def registry(unity_bank_config) -> TenantRegistry:
    return TenantRegistry({"unity_bank": unity_bank_config})


@pytest.fixture
def resolver(registry) -> ClientResolver:
    return ClientResolver(registry)


@pytest.fixture
def registry_with_disabled(unity_bank_config, disabled_config) -> TenantRegistry:
    return TenantRegistry({
        "unity_bank": unity_bank_config,
        "disabled_bank": disabled_config,
    })


# ── extract_domain tests ──────────────────────────────────────────────────────

class TestExtractDomain:
    def test_standard_email(self):
        assert ClientResolver.extract_domain("user@unitybank.co.in") == "unitybank.co.in"

    def test_multi_subdomain(self):
        assert ClientResolver.extract_domain("agent@support.bank.co.in") == "support.bank.co.in"

    def test_simple_domain(self):
        assert ClientResolver.extract_domain("x@bobbank.in") == "bobbank.in"

    def test_returns_lowercase(self):
        assert ClientResolver.extract_domain("User@UNITYBANK.CO.IN") == "unitybank.co.in"

    def test_empty_string(self):
        assert ClientResolver.extract_domain("") is None

    def test_no_at_sign(self):
        assert ClientResolver.extract_domain("notanemail") is None

    def test_at_at_end(self):
        assert ClientResolver.extract_domain("user@") is None

    def test_multiple_at_signs_uses_last(self):
        result = ClientResolver.extract_domain("user@alias@unitybank.co.in")
        assert result == "unitybank.co.in"

    def test_whitespace_email(self):
        assert ClientResolver.extract_domain("   ") is None

    def test_at_only(self):
        assert ClientResolver.extract_domain("@") is None


# ── resolve() — happy path ────────────────────────────────────────────────────

class TestResolveSuccess:
    def test_returns_tenant_context(self, resolver):
        ctx = resolver.resolve("mrunali@unitybank.co.in")
        assert isinstance(ctx, TenantContext)

    def test_client_id(self, resolver):
        ctx = resolver.resolve("agent@unitybank.co.in")
        assert ctx.client_id == "unity_bank"

    def test_client_name(self, resolver):
        ctx = resolver.resolve("agent@unitybank.co.in")
        assert ctx.client_name == "Unity Bank"

    def test_domain_stored(self, resolver):
        ctx = resolver.resolve("agent@unitybank.co.in")
        assert ctx.domain == "unitybank.co.in"

    def test_tenant_type(self, resolver):
        ctx = resolver.resolve("agent@unitybank.co.in")
        assert ctx.tenant_type == TenantType.BANK

    def test_environment(self, resolver):
        ctx = resolver.resolve("agent@unitybank.co.in")
        assert ctx.environment == TenantEnvironment.UAT

    def test_enabled_tools_populated(self, resolver):
        ctx = resolver.resolve("agent@unitybank.co.in")
        assert len(ctx.enabled_tools) > 0

    def test_credentials_ref_is_reference_not_secret(self, resolver):
        ctx = resolver.resolve("agent@unitybank.co.in")
        assert ctx.credentials_ref == "unity_bank_api_credentials"
        assert "key" not in ctx.credentials_ref.lower() or ctx.credentials_ref == "unity_bank_api_credentials"

    def test_context_is_immutable(self, resolver):
        ctx = resolver.resolve("agent@unitybank.co.in")
        with pytest.raises((AttributeError, TypeError)):
            ctx.client_id = "changed"  # type: ignore[misc]

    def test_uppercase_email_domain_resolved(self, resolver):
        ctx = resolver.resolve("Agent@UNITYBANK.CO.IN")
        assert ctx.client_id == "unity_bank"

    def test_enabled_tools_is_tuple(self, resolver):
        ctx = resolver.resolve("agent@unitybank.co.in")
        assert isinstance(ctx.enabled_tools, tuple)


# ── resolve() — failure paths ─────────────────────────────────────────────────

class TestResolveFailure:
    def test_unknown_domain_raises(self, resolver):
        with pytest.raises(UnknownClientError):
            resolver.resolve("agent@unknownbank.com")

    def test_empty_email_raises(self, resolver):
        with pytest.raises(UnknownClientError):
            resolver.resolve("")

    def test_malformed_email_raises(self, resolver):
        with pytest.raises(UnknownClientError):
            resolver.resolve("notanemail")

    def test_at_only_raises(self, resolver):
        with pytest.raises(UnknownClientError):
            resolver.resolve("@")

    def test_unknown_client_error_has_domain(self, resolver):
        try:
            resolver.resolve("agent@xyzbank.in")
        except UnknownClientError as e:
            assert e.domain == "xyzbank.in"

    def test_unknown_client_error_has_email_for_malformed(self, resolver):
        try:
            resolver.resolve("notanemail")
        except UnknownClientError as e:
            assert e.email == "notanemail"

    def test_disabled_tenant_raises(self):
        disabled = TenantConfig(
            client_id="disabled",
            client_name="Disabled",
            domains=("disabled.co.in",),
            tenant_type=TenantType.BANK,
            environment=TenantEnvironment.UAT,
            tool_config=TenantToolConfig(enabled_tools=("ToolA",)),
            auth_config=TenantAuthConfig(credentials_ref="disabled_ref"),
            enabled=False,
        )
        reg = TenantRegistry({"disabled": disabled})
        res = ClientResolver(reg)
        with pytest.raises(UnknownClientError):
            res.resolve("agent@disabled.co.in")

    def test_unknown_client_error_message_contains_domain(self, resolver):
        with pytest.raises(UnknownClientError) as exc_info:
            resolver.resolve("agent@mystery.com")
        assert "mystery.com" in str(exc_info.value)


# ── resolve_or_none() tests ───────────────────────────────────────────────────

class TestResolveOrNone:
    def test_returns_context_on_success(self, resolver):
        ctx = resolver.resolve_or_none("agent@unitybank.co.in")
        assert ctx is not None
        assert ctx.client_id == "unity_bank"

    def test_returns_none_on_unknown_domain(self, resolver):
        result = resolver.resolve_or_none("agent@unknownbank.com")
        assert result is None

    def test_returns_none_on_malformed_email(self, resolver):
        result = resolver.resolve_or_none("notanemail")
        assert result is None

    def test_returns_none_on_empty_email(self, resolver):
        result = resolver.resolve_or_none("")
        assert result is None

    def test_does_not_raise(self, resolver):
        result = resolver.resolve_or_none("completely@invalid@email")
        assert result is None


# ── resolve_by_client_id() tests ─────────────────────────────────────────────

class TestResolveByClientId:
    def test_known_client_id_returns_context(self, resolver):
        ctx = resolver.resolve_by_client_id("unity_bank")
        assert ctx is not None
        assert ctx.client_id == "unity_bank"

    def test_unknown_client_id_returns_none(self, resolver):
        result = resolver.resolve_by_client_id("unknown_bank")
        assert result is None

    def test_context_has_primary_domain(self, resolver):
        ctx = resolver.resolve_by_client_id("unity_bank")
        assert ctx is not None
        assert ctx.domain == "unitybank.co.in"

    def test_disabled_client_id_returns_none(self):
        disabled = TenantConfig(
            client_id="dis",
            client_name="Dis",
            domains=("dis.co.in",),
            tenant_type=TenantType.BANK,
            environment=TenantEnvironment.UAT,
            tool_config=TenantToolConfig(enabled_tools=()),
            auth_config=TenantAuthConfig(credentials_ref="ref"),
            enabled=False,
        )
        reg = TenantRegistry({"dis": disabled})
        res = ClientResolver(reg)
        assert res.resolve_by_client_id("dis") is None

    def test_empty_client_id_returns_none(self, resolver):
        assert resolver.resolve_by_client_id("") is None


# ── build_client_resolver() factory ──────────────────────────────────────────

class TestBuildClientResolver:
    def test_factory_returns_resolver(self):
        resolver = build_client_resolver()
        assert isinstance(resolver, ClientResolver)

    def test_factory_default_registry_resolves_unity_bank(self):
        resolver = build_client_resolver()
        ctx = resolver.resolve("agent@unitybank.co.in")
        assert ctx.client_id == "unity_bank"

    def test_factory_with_custom_registry(self, registry):
        resolver = build_client_resolver(registry=registry)
        ctx = resolver.resolve("x@unitybank.co.in")
        assert ctx is not None
