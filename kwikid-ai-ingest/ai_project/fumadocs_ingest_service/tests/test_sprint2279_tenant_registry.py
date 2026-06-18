"""
tests/test_sprint2279_tenant_registry.py

Sprint 2.27.9: TenantRegistry tests.

Covers:
  - lookup_by_domain() for known/unknown domains
  - lookup_by_client_id() for known/unknown ids
  - register() with new tenant succeeds
  - register() with duplicate client_id raises ValueError
  - register() with duplicate domain raises ValueError
  - validate() returns empty list for valid registry
  - validate() reports issues for empty/broken registry
  - count() returns correct tenant count
  - domain_count() returns correct domain count
  - all_tenants() returns all configs
  - all_client_ids() returns all ids
  - all_domains() returns all registered domains
  - Default registry has Unity Bank pre-configured
  - build_default_tenant_registry() factory
"""
from __future__ import annotations

import pytest

from case_engine.tenant.models import (
    TenantAuthConfig,
    TenantConfig,
    TenantEnvironment,
    TenantToolConfig,
    TenantType,
)
from case_engine.tenant.registry import (
    TenantRegistry,
    build_default_tenant_registry,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_config(
    client_id: str,
    client_name: str,
    domains: tuple[str, ...],
    enabled: bool = True,
    tools: tuple[str, ...] = ("ToolA",),
) -> TenantConfig:
    return TenantConfig(
        client_id=client_id,
        client_name=client_name,
        domains=domains,
        tenant_type=TenantType.BANK,
        environment=TenantEnvironment.UAT,
        tool_config=TenantToolConfig(enabled_tools=tools),
        auth_config=TenantAuthConfig(credentials_ref=f"{client_id}_credentials"),
        enabled=enabled,
    )


@pytest.fixture
def unity_cfg() -> TenantConfig:
    return _make_config(
        "unity_bank", "Unity Bank", ("unitybank.co.in",),
        tools=("GetUserDetails", "GetSessionDetails"),
    )


@pytest.fixture
def bob_cfg() -> TenantConfig:
    return _make_config("bank_of_baroda", "Bank of Baroda", ("bobbank.in", "baroda.co.in"))


@pytest.fixture
def single_registry(unity_cfg) -> TenantRegistry:
    return TenantRegistry({"unity_bank": unity_cfg})


@pytest.fixture
def multi_registry(unity_cfg, bob_cfg) -> TenantRegistry:
    return TenantRegistry({"unity_bank": unity_cfg, "bank_of_baroda": bob_cfg})


# ── lookup_by_domain() ────────────────────────────────────────────────────────

class TestLookupByDomain:
    def test_known_domain_returns_config(self, single_registry, unity_cfg):
        result = single_registry.lookup_by_domain("unitybank.co.in")
        assert result is not None
        assert result.client_id == "unity_bank"

    def test_unknown_domain_returns_none(self, single_registry):
        assert single_registry.lookup_by_domain("xyzbank.com") is None

    def test_case_insensitive_lookup(self, single_registry):
        assert single_registry.lookup_by_domain("UNITYBANK.CO.IN") is not None

    def test_mixed_case_lookup(self, single_registry):
        assert single_registry.lookup_by_domain("UnityBank.Co.In") is not None

    def test_empty_domain_returns_none(self, single_registry):
        assert single_registry.lookup_by_domain("") is None

    def test_multi_domain_tenant_lookup_first_domain(self, multi_registry):
        result = multi_registry.lookup_by_domain("bobbank.in")
        assert result is not None
        assert result.client_id == "bank_of_baroda"

    def test_multi_domain_tenant_lookup_second_domain(self, multi_registry):
        result = multi_registry.lookup_by_domain("baroda.co.in")
        assert result is not None
        assert result.client_id == "bank_of_baroda"

    def test_two_tenants_lookup_each(self, multi_registry):
        ub = multi_registry.lookup_by_domain("unitybank.co.in")
        bb = multi_registry.lookup_by_domain("bobbank.in")
        assert ub.client_id == "unity_bank"
        assert bb.client_id == "bank_of_baroda"


# ── lookup_by_client_id() ─────────────────────────────────────────────────────

class TestLookupByClientId:
    def test_known_client_id_returns_config(self, single_registry):
        result = single_registry.lookup_by_client_id("unity_bank")
        assert result is not None
        assert result.client_name == "Unity Bank"

    def test_unknown_client_id_returns_none(self, single_registry):
        assert single_registry.lookup_by_client_id("unknown") is None

    def test_empty_client_id_returns_none(self, single_registry):
        assert single_registry.lookup_by_client_id("") is None

    def test_multi_registry_lookup_both(self, multi_registry):
        assert multi_registry.lookup_by_client_id("unity_bank") is not None
        assert multi_registry.lookup_by_client_id("bank_of_baroda") is not None


# ── register() — mutation ─────────────────────────────────────────────────────

class TestRegister:
    def test_register_new_tenant_succeeds(self, single_registry):
        new_cfg = _make_config("cbi", "Central Bank of India", ("centralbank.co.in",))
        single_registry.register(new_cfg)
        assert single_registry.lookup_by_client_id("cbi") is not None

    def test_register_makes_domain_resolvable(self, single_registry):
        new_cfg = _make_config("cbi", "CBI", ("centralbank.co.in",))
        single_registry.register(new_cfg)
        assert single_registry.lookup_by_domain("centralbank.co.in") is not None

    def test_register_duplicate_client_id_raises(self, single_registry, unity_cfg):
        duplicate = _make_config("unity_bank", "Another Unity", ("another.co.in",))
        with pytest.raises(ValueError, match="unity_bank"):
            single_registry.register(duplicate)

    def test_register_duplicate_domain_raises(self, single_registry):
        duplicate = _make_config("other_bank", "Other Bank", ("unitybank.co.in",))
        with pytest.raises(ValueError, match="unitybank.co.in"):
            single_registry.register(duplicate)

    def test_register_increments_count(self, single_registry):
        before = single_registry.count()
        new_cfg = _make_config("new_bank", "New Bank", ("newbank.co.in",))
        single_registry.register(new_cfg)
        assert single_registry.count() == before + 1

    def test_register_multi_domain_tenant(self, single_registry):
        new_cfg = _make_config("multi", "Multi Bank", ("a.co.in", "b.co.in"))
        single_registry.register(new_cfg)
        assert single_registry.lookup_by_domain("a.co.in") is not None
        assert single_registry.lookup_by_domain("b.co.in") is not None

    def test_register_preserves_existing(self, single_registry):
        new_cfg = _make_config("new", "New", ("new.co.in",))
        single_registry.register(new_cfg)
        assert single_registry.lookup_by_client_id("unity_bank") is not None


# ── count() and domain_count() ───────────────────────────────────────────────

class TestCounts:
    def test_count_single_tenant(self, single_registry):
        assert single_registry.count() == 1

    def test_count_multi_tenants(self, multi_registry):
        assert multi_registry.count() == 2

    def test_domain_count_single_domain(self, single_registry):
        assert single_registry.domain_count() == 1

    def test_domain_count_multi_domain_tenant(self, multi_registry):
        # unity_bank: 1 domain, bank_of_baroda: 2 domains
        assert multi_registry.domain_count() == 3

    def test_empty_registry_count_zero(self):
        reg = TenantRegistry({})
        assert reg.count() == 0
        assert reg.domain_count() == 0


# ── all_tenants() / all_client_ids() / all_domains() ─────────────────────────

class TestIntrospection:
    def test_all_tenants_returns_list(self, single_registry):
        tenants = single_registry.all_tenants()
        assert isinstance(tenants, list)
        assert len(tenants) == 1

    def test_all_tenants_multi(self, multi_registry):
        assert len(multi_registry.all_tenants()) == 2

    def test_all_client_ids(self, multi_registry):
        ids = multi_registry.all_client_ids()
        assert "unity_bank" in ids
        assert "bank_of_baroda" in ids

    def test_all_domains(self, multi_registry):
        domains = multi_registry.all_domains()
        assert "unitybank.co.in" in domains
        assert "bobbank.in" in domains
        assert "baroda.co.in" in domains


# ── validate() ───────────────────────────────────────────────────────────────

class TestValidate:
    def test_valid_registry_returns_empty_list(self, single_registry):
        errors = single_registry.validate()
        assert errors == []

    def test_empty_registry_returns_error(self):
        reg = TenantRegistry({})
        errors = reg.validate()
        assert len(errors) > 0

    def test_enabled_tenant_no_tools_reported(self):
        bad_cfg = _make_config("bad", "Bad Bank", ("bad.co.in",), tools=())
        reg = TenantRegistry({"bad": bad_cfg})
        errors = reg.validate()
        assert any("bad" in e for e in errors)

    def test_no_domains_tenant_reported(self):
        cfg = TenantConfig(
            client_id="no_domain",
            client_name="No Domain",
            domains=(),
            tenant_type=TenantType.BANK,
            environment=TenantEnvironment.UAT,
            tool_config=TenantToolConfig(enabled_tools=("ToolA",)),
            auth_config=TenantAuthConfig(credentials_ref="ref"),
        )
        reg = TenantRegistry({"no_domain": cfg})
        errors = reg.validate()
        assert any("no_domain" in e for e in errors)

    def test_disabled_tenant_with_no_tools_not_reported(self):
        disabled = _make_config("dis", "Disabled", ("dis.co.in",), enabled=False, tools=())
        reg = TenantRegistry({"dis": disabled})
        errors = reg.validate()
        assert not any("dis" in e for e in errors)


# ── Default registry (build_default_tenant_registry) ─────────────────────────

class TestDefaultRegistry:
    def test_default_registry_has_unity_bank(self):
        reg = build_default_tenant_registry()
        assert reg.lookup_by_client_id("unity_bank") is not None

    def test_default_registry_resolves_unitybank_domain(self):
        reg = build_default_tenant_registry()
        cfg = reg.lookup_by_domain("unitybank.co.in")
        assert cfg is not None
        assert cfg.client_id == "unity_bank"

    def test_default_registry_count_positive(self):
        reg = build_default_tenant_registry()
        assert reg.count() >= 1

    def test_default_registry_validates_clean(self):
        reg = build_default_tenant_registry()
        errors = reg.validate()
        assert errors == []

    def test_default_registry_unity_bank_has_tools(self):
        reg = build_default_tenant_registry()
        cfg = reg.lookup_by_client_id("unity_bank")
        assert len(cfg.tool_config.enabled_tools) > 0

    def test_default_registry_unity_bank_enabled(self):
        reg = build_default_tenant_registry()
        cfg = reg.lookup_by_client_id("unity_bank")
        assert cfg.enabled is True

    def test_default_registry_credentials_ref_is_reference(self):
        reg = build_default_tenant_registry()
        cfg = reg.lookup_by_client_id("unity_bank")
        assert cfg.auth_config.credentials_ref == "unity_bank_api_credentials"
        assert len(cfg.auth_config.credentials_ref) < 100
