"""
tests/test_sprint2279_tenant_adapters.py

Sprint 2.27.9: Tenant Adapter Architecture tests.

Covers:
  - AuthResult, HealthResult, ToolExecutionResult frozen dataclasses
  - TenantAdapter is abstract (cannot instantiate directly)
  - UnityBankAdapter client_id == "unity_bank"
  - UnityBankAdapter.authenticate() returns AuthResult(success=True) [stub]
  - UnityBankAdapter.health_check() returns HealthResult(healthy=True) [stub]
  - UnityBankAdapter.supported_tools() returns the 10 Unity Bank tools
  - UnityBankAdapter.execute() with supported tool returns NOT_IMPLEMENTED error
  - UnityBankAdapter.execute() with unsupported tool returns TOOL_NOT_SUPPORTED
  - UnityBankAdapter never raises (returns result objects)
  - build_adapter_for_context() factory for Unity Bank
  - build_adapter_for_context() raises UnknownClientError for unknown client_id
  - build_adapter_for_client() factory
  - registered_adapter_client_ids() includes "unity_bank"
  - Result to_dict() serialization
"""
from __future__ import annotations

import inspect
import pytest

from case_engine.tenant.adapters import (
    AuthResult,
    HealthResult,
    TenantAdapter,
    ToolExecutionResult,
    UnityBankAdapter,
    build_adapter_for_client,
    build_adapter_for_context,
    registered_adapter_client_ids,
)
from case_engine.tenant.models import (
    TenantContext,
    TenantEnvironment,
    TenantType,
    UnknownClientError,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

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


@pytest.fixture
def unknown_context() -> TenantContext:
    return TenantContext(
        client_id="bank_of_baroda",
        client_name="Bank of Baroda",
        domain="bobbank.in",
        tenant_type=TenantType.BANK,
        environment=TenantEnvironment.UAT,
        enabled_tools=("GetUserDetails",),
        credentials_ref="bob_api_credentials",
    )


@pytest.fixture
def adapter() -> UnityBankAdapter:
    return UnityBankAdapter()


UNITY_TOOLS = (
    "GetUserDetails", "GetSessionDetails", "GetFailureReason", "GetCaseHistory",
    "GetOnboardingStatus", "SessionLogsTool", "SessionSummaryTool",
    "SessionVideoTool", "MetricsTool", "ServerStatusTool",
)


# ── Result dataclass tests ────────────────────────────────────────────────────

class TestResultDataclasses:
    def test_auth_result_frozen(self):
        r = AuthResult(success=True)
        with pytest.raises((AttributeError, TypeError)):
            r.success = False  # type: ignore[misc]

    def test_health_result_frozen(self):
        r = HealthResult(healthy=True)
        with pytest.raises((AttributeError, TypeError)):
            r.healthy = False  # type: ignore[misc]

    def test_tool_result_frozen(self):
        r = ToolExecutionResult(tool_name="GetUserDetails", success=True)
        with pytest.raises((AttributeError, TypeError)):
            r.success = False  # type: ignore[misc]

    def test_auth_result_to_dict(self):
        r = AuthResult(success=True, error_msg="")
        d = r.to_dict()
        assert d["success"] is True
        assert "error_msg" in d

    def test_health_result_to_dict(self):
        r = HealthResult(healthy=True, latency_ms=42)
        d = r.to_dict()
        assert d["healthy"] is True
        assert d["latency_ms"] == 42

    def test_tool_result_to_dict(self):
        r = ToolExecutionResult(tool_name="GetUserDetails", success=False, error_code="NOT_IMPLEMENTED")
        d = r.to_dict()
        assert d["tool_name"] == "GetUserDetails"
        assert d["success"] is False
        assert d["error_code"] == "NOT_IMPLEMENTED"

    def test_auth_result_defaults(self):
        r = AuthResult(success=True)
        assert r.error_msg == ""
        assert r.details == {}

    def test_health_result_defaults(self):
        r = HealthResult(healthy=True)
        assert r.latency_ms == 0
        assert r.error_msg == ""

    def test_tool_result_defaults(self):
        r = ToolExecutionResult(tool_name="T", success=True)
        assert r.data == {}
        assert r.error_msg == ""
        assert r.error_code == ""


# ── TenantAdapter abstract base ───────────────────────────────────────────────

class TestTenantAdapterAbstract:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            TenantAdapter()  # type: ignore[abstract]

    def test_defines_client_id_property(self):
        assert "client_id" in dir(TenantAdapter)

    def test_defines_authenticate_method(self):
        assert hasattr(TenantAdapter, "authenticate")

    def test_defines_health_check_method(self):
        assert hasattr(TenantAdapter, "health_check")

    def test_defines_supported_tools_method(self):
        assert hasattr(TenantAdapter, "supported_tools")

    def test_defines_execute_method(self):
        assert hasattr(TenantAdapter, "execute")

    def test_all_methods_abstract(self):
        abstract_methods = getattr(TenantAdapter, "__abstractmethods__", set())
        for method in ("authenticate", "health_check", "supported_tools", "execute"):
            assert method in abstract_methods


# ── UnityBankAdapter ──────────────────────────────────────────────────────────

class TestUnityBankAdapter:
    def test_client_id(self, adapter):
        assert adapter.client_id == "unity_bank"

    def test_implements_tenant_adapter(self, adapter):
        assert isinstance(adapter, TenantAdapter)

    def test_authenticate_returns_auth_result(self, adapter):
        result = adapter.authenticate()
        assert isinstance(result, AuthResult)

    def test_authenticate_stub_returns_success(self, adapter):
        result = adapter.authenticate()
        assert result.success is True

    def test_health_check_returns_health_result(self, adapter):
        result = adapter.health_check()
        assert isinstance(result, HealthResult)

    def test_health_check_stub_returns_healthy(self, adapter):
        result = adapter.health_check()
        assert result.healthy is True

    def test_supported_tools_returns_tuple(self, adapter):
        assert isinstance(adapter.supported_tools(), tuple)

    def test_supported_tools_count_is_10(self, adapter):
        assert len(adapter.supported_tools()) == 10

    def test_supported_tools_contains_get_user_details(self, adapter):
        assert "GetUserDetails" in adapter.supported_tools()

    def test_supported_tools_all_expected(self, adapter):
        tools = set(adapter.supported_tools())
        assert tools == set(UNITY_TOOLS)

    def test_execute_supported_tool_returns_result(self, adapter):
        result = adapter.execute("GetUserDetails", {"user_id": "123"})
        assert isinstance(result, ToolExecutionResult)

    def test_execute_returns_not_implemented_error_code(self, adapter):
        result = adapter.execute("GetUserDetails", {})
        assert result.error_code == "NOT_IMPLEMENTED"

    def test_execute_returns_success_false(self, adapter):
        result = adapter.execute("GetSessionDetails", {})
        assert result.success is False

    def test_execute_returns_tool_name(self, adapter):
        result = adapter.execute("GetFailureReason", {})
        assert result.tool_name == "GetFailureReason"

    def test_execute_unsupported_tool_returns_tool_not_supported(self, adapter):
        result = adapter.execute("FakeUnknownTool", {})
        assert result.error_code == "TOOL_NOT_SUPPORTED"
        assert result.success is False

    def test_execute_does_not_raise(self, adapter):
        # per contract: execute() never raises
        result = adapter.execute("AnyTool", {})
        assert isinstance(result, ToolExecutionResult)

    def test_authenticate_does_not_raise(self, adapter):
        result = adapter.authenticate()
        assert isinstance(result, AuthResult)

    def test_health_check_does_not_raise(self, adapter):
        result = adapter.health_check()
        assert isinstance(result, HealthResult)

    def test_credentials_ref_stored_but_not_exposed(self):
        a = UnityBankAdapter(credentials_ref="unity_bank_api_credentials")
        assert a._credentials_ref == "unity_bank_api_credentials"

    def test_each_supported_tool_returns_not_implemented(self, adapter):
        for tool in UNITY_TOOLS:
            result = adapter.execute(tool, {})
            assert result.error_code == "NOT_IMPLEMENTED"
            assert result.success is False


# ── build_adapter_for_context() ───────────────────────────────────────────────

class TestBuildAdapterForContext:
    def test_known_context_returns_adapter(self, unity_context):
        adapter = build_adapter_for_context(unity_context)
        assert isinstance(adapter, TenantAdapter)

    def test_unity_context_returns_unity_bank_adapter(self, unity_context):
        adapter = build_adapter_for_context(unity_context)
        assert adapter.client_id == "unity_bank"

    def test_unknown_context_raises_unknown_client_error(self, unknown_context):
        with pytest.raises(UnknownClientError):
            build_adapter_for_context(unknown_context)

    def test_adapter_credentials_ref_from_context(self, unity_context):
        adapter = build_adapter_for_context(unity_context)
        assert isinstance(adapter, UnityBankAdapter)
        assert adapter._credentials_ref == unity_context.credentials_ref


# ── build_adapter_for_client() ────────────────────────────────────────────────

class TestBuildAdapterForClient:
    def test_known_client_id_returns_adapter(self):
        adapter = build_adapter_for_client("unity_bank")
        assert isinstance(adapter, TenantAdapter)
        assert adapter.client_id == "unity_bank"

    def test_unknown_client_id_raises(self):
        with pytest.raises(UnknownClientError):
            build_adapter_for_client("bank_of_baroda")

    def test_empty_client_id_raises(self):
        with pytest.raises(UnknownClientError):
            build_adapter_for_client("")


# ── registered_adapter_client_ids() ─────────────────────────────────────────

class TestRegisteredAdapterClientIds:
    def test_returns_list(self):
        result = registered_adapter_client_ids()
        assert isinstance(result, list)

    def test_includes_unity_bank(self):
        assert "unity_bank" in registered_adapter_client_ids()

    def test_is_nonempty(self):
        assert len(registered_adapter_client_ids()) >= 1
