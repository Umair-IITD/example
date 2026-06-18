"""
tests/test_sprint2279_tenant_tool_registry.py

Sprint 2.27.9: TenantAwareToolRegistry tests.

Covers:
  - get_tools_for_client() returns correct tools per client
  - get_tools_for_client() returns empty tuple for unknown client
  - get_tools_for_context() delegates to get_tools_for_client()
  - is_tool_enabled() True/False for known/unknown tools
  - is_tool_enabled_for_context() via TenantContext
  - register_tool_for_client() adds tool to existing client
  - register_tool_for_client() adds tools to unknown client (runtime extras only)
  - all_registered_clients() returns all client_ids
  - tool_count_for_client() correct count
  - tools_diff() between two clients
  - build_tenant_tool_registry() factory
  - Unity Bank has the expected 10 tools
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
)
from case_engine.tenant.registry import TenantRegistry, build_default_tenant_registry
from case_engine.tenant.tool_registry import TenantAwareToolRegistry, build_tenant_tool_registry


# ── Expected Unity Bank tools ─────────────────────────────────────────────────

UNITY_BANK_TOOLS = frozenset({
    "GetUserDetails",
    "GetSessionDetails",
    "GetFailureReason",
    "GetCaseHistory",
    "GetOnboardingStatus",
    "SessionLogsTool",
    "SessionSummaryTool",
    "SessionVideoTool",
    "MetricsTool",
    "ServerStatusTool",
})


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_cfg(client_id: str, domains: tuple, tools: tuple) -> TenantConfig:
    return TenantConfig(
        client_id=client_id,
        client_name=client_id.replace("_", " ").title(),
        domains=domains,
        tenant_type=TenantType.BANK,
        environment=TenantEnvironment.UAT,
        tool_config=TenantToolConfig(enabled_tools=tools),
        auth_config=TenantAuthConfig(credentials_ref=f"{client_id}_ref"),
    )


@pytest.fixture
def unity_cfg() -> TenantConfig:
    return _make_cfg(
        "unity_bank", ("unitybank.co.in",),
        ("GetUserDetails", "GetSessionDetails", "GetFailureReason"),
    )


@pytest.fixture
def bob_cfg() -> TenantConfig:
    return _make_cfg(
        "bank_of_baroda", ("bobbank.in",),
        ("GetUserDetails", "VideoCropTool"),
    )


@pytest.fixture
def registry(unity_cfg, bob_cfg) -> TenantRegistry:
    return TenantRegistry({"unity_bank": unity_cfg, "bank_of_baroda": bob_cfg})


@pytest.fixture
def tool_registry(registry) -> TenantAwareToolRegistry:
    return TenantAwareToolRegistry(registry)


@pytest.fixture
def unity_context() -> TenantContext:
    return TenantContext(
        client_id="unity_bank",
        client_name="Unity Bank",
        domain="unitybank.co.in",
        tenant_type=TenantType.BANK,
        environment=TenantEnvironment.UAT,
        enabled_tools=("GetUserDetails",),
        credentials_ref="unity_bank_api_credentials",
    )


# ── get_tools_for_client() ────────────────────────────────────────────────────

class TestGetToolsForClient:
    def test_returns_tuple(self, tool_registry):
        result = tool_registry.get_tools_for_client("unity_bank")
        assert isinstance(result, tuple)

    def test_unity_bank_tools(self, tool_registry):
        tools = tool_registry.get_tools_for_client("unity_bank")
        assert "GetUserDetails" in tools
        assert "GetSessionDetails" in tools
        assert "GetFailureReason" in tools

    def test_bob_tools(self, tool_registry):
        tools = tool_registry.get_tools_for_client("bank_of_baroda")
        assert "GetUserDetails" in tools
        assert "VideoCropTool" in tools

    def test_unknown_client_returns_empty_tuple(self, tool_registry):
        tools = tool_registry.get_tools_for_client("nonexistent_bank")
        assert tools == ()

    def test_tools_sorted(self, tool_registry):
        tools = tool_registry.get_tools_for_client("unity_bank")
        assert list(tools) == sorted(tools)

    def test_clients_have_different_tools(self, tool_registry):
        unity = set(tool_registry.get_tools_for_client("unity_bank"))
        bob = set(tool_registry.get_tools_for_client("bank_of_baroda"))
        assert unity != bob


# ── get_tools_for_context() ───────────────────────────────────────────────────

class TestGetToolsForContext:
    def test_matches_get_tools_for_client(self, tool_registry, unity_context):
        by_ctx = tool_registry.get_tools_for_context(unity_context)
        by_id = tool_registry.get_tools_for_client("unity_bank")
        assert by_ctx == by_id

    def test_returns_tuple(self, tool_registry, unity_context):
        assert isinstance(tool_registry.get_tools_for_context(unity_context), tuple)


# ── is_tool_enabled() ────────────────────────────────────────────────────────

class TestIsToolEnabled:
    def test_known_tool_enabled(self, tool_registry):
        assert tool_registry.is_tool_enabled("GetUserDetails", "unity_bank") is True

    def test_unknown_tool_not_enabled(self, tool_registry):
        assert tool_registry.is_tool_enabled("FakeTool", "unity_bank") is False

    def test_other_client_tool_not_enabled_for_unity(self, tool_registry):
        assert tool_registry.is_tool_enabled("VideoCropTool", "unity_bank") is False

    def test_unknown_client_returns_false(self, tool_registry):
        assert tool_registry.is_tool_enabled("GetUserDetails", "nonexistent") is False

    def test_all_configured_tools_enabled(self, tool_registry):
        for tool in ("GetUserDetails", "GetSessionDetails", "GetFailureReason"):
            assert tool_registry.is_tool_enabled(tool, "unity_bank") is True


# ── is_tool_enabled_for_context() ────────────────────────────────────────────

class TestIsToolEnabledForContext:
    def test_known_tool(self, tool_registry, unity_context):
        assert tool_registry.is_tool_enabled_for_context("GetUserDetails", unity_context) is True

    def test_unknown_tool(self, tool_registry, unity_context):
        assert tool_registry.is_tool_enabled_for_context("FakeTool", unity_context) is False


# ── register_tool_for_client() ────────────────────────────────────────────────

class TestRegisterToolForClient:
    def test_adds_new_tool(self, tool_registry):
        tool_registry.register_tool_for_client("NewTool", "unity_bank")
        assert tool_registry.is_tool_enabled("NewTool", "unity_bank") is True

    def test_does_not_remove_existing_tools(self, tool_registry):
        tool_registry.register_tool_for_client("NewTool", "unity_bank")
        assert tool_registry.is_tool_enabled("GetUserDetails", "unity_bank") is True

    def test_register_same_tool_twice_idempotent(self, tool_registry):
        tool_registry.register_tool_for_client("ExtraTool", "unity_bank")
        tool_registry.register_tool_for_client("ExtraTool", "unity_bank")
        count = tool_registry.get_tools_for_client("unity_bank").count("ExtraTool")
        assert count == 1

    def test_runtime_extra_not_in_other_client(self, tool_registry):
        tool_registry.register_tool_for_client("SpecialTool", "unity_bank")
        assert not tool_registry.is_tool_enabled("SpecialTool", "bank_of_baroda")


# ── tool_count_for_client() ───────────────────────────────────────────────────

class TestToolCount:
    def test_count_matches_enabled_tools(self, tool_registry):
        count = tool_registry.tool_count_for_client("unity_bank")
        tools = tool_registry.get_tools_for_client("unity_bank")
        assert count == len(tools)

    def test_count_zero_for_unknown_client(self, tool_registry):
        assert tool_registry.tool_count_for_client("unknown") == 0

    def test_count_increases_after_register(self, tool_registry):
        before = tool_registry.tool_count_for_client("unity_bank")
        tool_registry.register_tool_for_client("BonusTool", "unity_bank")
        assert tool_registry.tool_count_for_client("unity_bank") == before + 1


# ── all_registered_clients() ─────────────────────────────────────────────────

class TestAllRegisteredClients:
    def test_returns_list(self, tool_registry):
        assert isinstance(tool_registry.all_registered_clients(), list)

    def test_contains_both_clients(self, tool_registry):
        clients = tool_registry.all_registered_clients()
        assert "unity_bank" in clients
        assert "bank_of_baroda" in clients


# ── tools_diff() ─────────────────────────────────────────────────────────────

class TestToolsDiff:
    def test_returns_dict_with_expected_keys(self, tool_registry):
        diff = tool_registry.tools_diff("unity_bank", "bank_of_baroda")
        assert "only_in_a" in diff
        assert "only_in_b" in diff
        assert "shared" in diff

    def test_shared_tool(self, tool_registry):
        diff = tool_registry.tools_diff("unity_bank", "bank_of_baroda")
        assert "GetUserDetails" in diff["shared"]

    def test_only_in_a_has_unity_only_tools(self, tool_registry):
        diff = tool_registry.tools_diff("unity_bank", "bank_of_baroda")
        assert "GetSessionDetails" in diff["only_in_a"]

    def test_only_in_b_has_bob_only_tools(self, tool_registry):
        diff = tool_registry.tools_diff("unity_bank", "bank_of_baroda")
        assert "VideoCropTool" in diff["only_in_b"]

    def test_diff_same_client_has_no_difference(self, tool_registry):
        diff = tool_registry.tools_diff("unity_bank", "unity_bank")
        assert diff["only_in_a"] == []
        assert diff["only_in_b"] == []
        assert len(diff["shared"]) > 0

    def test_unknown_client_results_in_all_in_b(self, tool_registry):
        diff = tool_registry.tools_diff("nonexistent", "unity_bank")
        assert diff["only_in_a"] == []
        assert len(diff["only_in_b"]) > 0


# ── Default registry Unity Bank tools ────────────────────────────────────────

class TestDefaultRegistryTools:
    def test_unity_bank_has_all_10_tools(self):
        tool_reg = build_tenant_tool_registry()
        tools = set(tool_reg.get_tools_for_client("unity_bank"))
        assert tools == UNITY_BANK_TOOLS

    def test_unity_bank_tool_count_is_10(self):
        tool_reg = build_tenant_tool_registry()
        assert tool_reg.tool_count_for_client("unity_bank") == 10

    def test_build_factory_no_args(self):
        tool_reg = build_tenant_tool_registry()
        assert isinstance(tool_reg, TenantAwareToolRegistry)

    def test_build_factory_custom_registry(self):
        reg = build_default_tenant_registry()
        tool_reg = build_tenant_tool_registry(registry=reg)
        assert isinstance(tool_reg, TenantAwareToolRegistry)
