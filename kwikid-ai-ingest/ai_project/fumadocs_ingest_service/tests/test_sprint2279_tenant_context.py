"""
tests/test_sprint2279_tenant_context.py

Sprint 2.27.9: TenantContext model and propagation tests.

Covers:
  - TenantContext is frozen (immutable)
  - TenantContext.to_dict() serializes all fields
  - to_dict() does NOT include actual secret values
  - credentials_ref is a reference name, not a secret
  - TenantContext carries all fields needed by downstream pipeline
  - TenantContext created from TenantConfig via ClientResolver
  - Case.tenant_context field exists (Sprint 2.27.9 addition)
  - Case.to_db_row() does NOT include tenant_context (in-process only)
  - Case.from_db_row() does not crash if tenant_context not in row
  - TenantConfig.to_dict() serializes correctly
  - TenantToolConfig.to_dict() and TenantAuthConfig.to_dict()
  - UnknownClientError attributes
"""
from __future__ import annotations

import dataclasses
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
from case_engine.tenant.resolver import ClientResolver


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def unity_context() -> TenantContext:
    return TenantContext(
        client_id="unity_bank",
        client_name="Unity Bank",
        domain="unitybank.co.in",
        tenant_type=TenantType.BANK,
        environment=TenantEnvironment.UAT,
        enabled_tools=("GetUserDetails", "GetSessionDetails", "GetFailureReason"),
        credentials_ref="unity_bank_api_credentials",
        workflow_overrides={},
        portal_base_url="",
    )


@pytest.fixture
def resolver() -> ClientResolver:
    from case_engine.tenant.registry import build_default_tenant_registry
    return ClientResolver(build_default_tenant_registry())


# ── TenantContext immutability ────────────────────────────────────────────────

class TestTenantContextImmutability:
    def test_is_frozen_dataclass(self, unity_context):
        assert dataclasses.is_dataclass(unity_context)
        assert unity_context.__dataclass_params__.frozen

    def test_cannot_set_client_id(self, unity_context):
        with pytest.raises((AttributeError, TypeError)):
            unity_context.client_id = "changed"  # type: ignore[misc]

    def test_cannot_set_domain(self, unity_context):
        with pytest.raises((AttributeError, TypeError)):
            unity_context.domain = "changed"  # type: ignore[misc]

    def test_cannot_set_enabled_tools(self, unity_context):
        with pytest.raises((AttributeError, TypeError)):
            unity_context.enabled_tools = ("NewTool",)  # type: ignore[misc]

    def test_enabled_tools_is_tuple(self, unity_context):
        assert isinstance(unity_context.enabled_tools, tuple)

    def test_equality_stable(self, unity_context):
        # frozen dataclasses support equality comparison
        from case_engine.tenant.models import TenantContext, TenantType, TenantEnvironment
        same = TenantContext(
            client_id=unity_context.client_id,
            client_name=unity_context.client_name,
            domain=unity_context.domain,
            tenant_type=unity_context.tenant_type,
            environment=unity_context.environment,
            enabled_tools=unity_context.enabled_tools,
            credentials_ref=unity_context.credentials_ref,
        )
        assert same == unity_context


# ── TenantContext.to_dict() ───────────────────────────────────────────────────

class TestTenantContextToDict:
    def test_returns_dict(self, unity_context):
        assert isinstance(unity_context.to_dict(), dict)

    def test_client_id_present(self, unity_context):
        d = unity_context.to_dict()
        assert d["client_id"] == "unity_bank"

    def test_client_name_present(self, unity_context):
        d = unity_context.to_dict()
        assert d["client_name"] == "Unity Bank"

    def test_domain_present(self, unity_context):
        d = unity_context.to_dict()
        assert d["domain"] == "unitybank.co.in"

    def test_tenant_type_is_string(self, unity_context):
        d = unity_context.to_dict()
        assert isinstance(d["tenant_type"], str)
        assert d["tenant_type"] == "bank"

    def test_environment_is_string(self, unity_context):
        d = unity_context.to_dict()
        assert d["environment"] == "uat"

    def test_enabled_tools_is_list(self, unity_context):
        d = unity_context.to_dict()
        assert isinstance(d["enabled_tools"], list)

    def test_credentials_ref_is_reference_name(self, unity_context):
        d = unity_context.to_dict()
        assert d["credentials_ref"] == "unity_bank_api_credentials"

    def test_no_actual_secret_in_dict(self, unity_context):
        d = unity_context.to_dict()
        values = str(d)
        # credentials_ref should be a short reference name, not a long secret
        assert len(d["credentials_ref"]) < 100

    def test_workflow_overrides_is_dict(self, unity_context):
        d = unity_context.to_dict()
        assert isinstance(d["workflow_overrides"], dict)

    def test_all_required_keys_present(self, unity_context):
        d = unity_context.to_dict()
        required = {
            "client_id", "client_name", "domain", "tenant_type",
            "environment", "enabled_tools", "credentials_ref",
            "workflow_overrides", "portal_base_url",
        }
        assert required.issubset(d.keys())


# ── TenantConfig.to_dict() ────────────────────────────────────────────────────

class TestTenantConfigToDict:
    def test_returns_dict(self):
        cfg = TenantConfig(
            client_id="unity_bank",
            client_name="Unity Bank",
            domains=("unitybank.co.in",),
            tenant_type=TenantType.BANK,
            environment=TenantEnvironment.UAT,
            tool_config=TenantToolConfig(enabled_tools=("GetUserDetails",)),
            auth_config=TenantAuthConfig(credentials_ref="ref"),
        )
        d = cfg.to_dict()
        assert isinstance(d, dict)
        assert d["client_id"] == "unity_bank"
        assert isinstance(d["domains"], list)
        assert isinstance(d["tool_config"], dict)
        assert isinstance(d["auth_config"], dict)

    def test_enabled_is_present(self):
        cfg = TenantConfig(
            client_id="x",
            client_name="X Bank",
            domains=("x.co.in",),
            tenant_type=TenantType.BANK,
            environment=TenantEnvironment.UAT,
            tool_config=TenantToolConfig(enabled_tools=()),
            auth_config=TenantAuthConfig(credentials_ref="ref"),
        )
        d = cfg.to_dict()
        assert "enabled" in d


# ── TenantToolConfig and TenantAuthConfig ─────────────────────────────────────

class TestSubConfigs:
    def test_tool_config_to_dict(self):
        tc = TenantToolConfig(enabled_tools=("ToolA", "ToolB"), max_tool_calls=5)
        d = tc.to_dict()
        assert d["enabled_tools"] == ["ToolA", "ToolB"]
        assert d["max_tool_calls"] == 5

    def test_auth_config_to_dict(self):
        ac = TenantAuthConfig(credentials_ref="my_ref", auth_type="api_key")
        d = ac.to_dict()
        assert d["credentials_ref"] == "my_ref"
        assert d["auth_type"] == "api_key"

    def test_auth_config_default_type(self):
        ac = TenantAuthConfig(credentials_ref="ref")
        assert ac.auth_type == "api_key"

    def test_tool_config_default_max_calls(self):
        tc = TenantToolConfig(enabled_tools=("ToolA",))
        assert tc.max_tool_calls == 10


# ── UnknownClientError ────────────────────────────────────────────────────────

class TestUnknownClientError:
    def test_is_exception(self):
        e = UnknownClientError(domain="xyz.com")
        assert isinstance(e, Exception)

    def test_domain_attribute(self):
        e = UnknownClientError(domain="xyz.com")
        assert e.domain == "xyz.com"

    def test_email_attribute(self):
        e = UnknownClientError(domain="xyz.com", email="user@xyz.com")
        assert e.email == "user@xyz.com"

    def test_empty_email_default(self):
        e = UnknownClientError(domain="xyz.com")
        assert e.email == ""

    def test_str_contains_domain(self):
        e = UnknownClientError(domain="xyz.com")
        assert "xyz.com" in str(e)

    def test_can_be_raised_and_caught(self):
        with pytest.raises(UnknownClientError) as exc_info:
            raise UnknownClientError(domain="bad.com", email="user@bad.com")
        assert exc_info.value.domain == "bad.com"


# ── TenantContext propagation through Case ────────────────────────────────────

class TestCaseTenantContext:
    def test_case_has_tenant_context_field(self):
        from case_engine.models import Case
        c = Case()
        assert hasattr(c, "tenant_context")

    def test_case_tenant_context_default_none(self):
        from case_engine.models import Case
        c = Case()
        assert c.tenant_context is None

    def test_case_tenant_context_can_be_set(self, unity_context):
        from case_engine.models import Case
        c = Case()
        c.tenant_context = unity_context
        assert c.tenant_context is unity_context

    def test_case_to_db_row_excludes_tenant_context(self, unity_context):
        from case_engine.models import Case
        c = Case(ticket_id="T-001", client="unity_bank")
        c.tenant_context = unity_context
        row = c.to_db_row()
        assert "tenant_context" not in row

    def test_case_from_db_row_has_none_tenant_context(self):
        from case_engine.models import Case
        row = {
            "case_id": "c-001",
            "ticket_id": "T-001",
            "client": "unity_bank",
            "topic": None,
            "confidence": None,
            "current_state": "NEW",
            "created_at": "2026-06-01T00:00:00+00:00",
            "updated_at": "2026-06-01T00:00:00+00:00",
            "closed_at": None,
            "sla_breach_at": None,
        }
        c = Case.from_db_row(row)
        assert c.tenant_context is None

    def test_case_tenant_context_accepts_resolved_context(self, resolver):
        from case_engine.models import Case
        ctx = resolver.resolve("agent@unitybank.co.in")
        c = Case(ticket_id="T-002")
        c.tenant_context = ctx
        assert c.tenant_context.client_id == "unity_bank"
