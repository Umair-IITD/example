"""
tests/test_sprint2279_conformance.py

Sprint 2.27.9: Architecture conformance tests.

Covers:
  - flow_diagram compliance: TICKET → CLIENTRESOLVE → TENANTREG → TENANTCTX → CASE
  - AuditEventType count ≥ 80 (74 from Sprint 2.27.8 + 6 from Sprint 2.27.9)
  - ProductionRuntime fields ≥ 37 (34 from Sprint 2.27.8 + 3 from Sprint 2.27.9)
  - Backward compat: client_resolver=None in TicketOrchestrator
  - Unity Bank: 10 tools, exact expected list
  - TenantRegistry: data-driven (no if/else chains in resolution path)
  - TenantContext immutability (frozen dataclass)
  - tenant package exports all public symbols via __all__
  - ClientResolver.extract_domain is a staticmethod (no instance needed)
  - Adding new tenant = only config change (code requires no new branches)
  - UnknownClientError stops automation (pipeline design contract)
  - Tenant services at IMPORTANT tier (not CRITICAL)
"""
from __future__ import annotations

import dataclasses
import inspect
import pytest


# ── AuditEventType count ──────────────────────────────────────────────────────

class TestAuditEventTypeCount:
    def test_has_at_least_80_event_types(self):
        from case_engine.models import AuditEventType
        count = len(list(AuditEventType))
        assert count >= 80, f"Expected ≥80 AuditEventType values, found {count}"

    def test_has_all_sprint_2279_events(self):
        from case_engine.models import AuditEventType
        required = {
            "CLIENT_RESOLVED",
            "CLIENT_RESOLUTION_FAILED",
            "TENANT_CONTEXT_ATTACHED",
            "UNKNOWN_CLIENT_ESCALATED",
            "TENANT_REGISTRY_VALIDATED",
            "TENANT_REGISTRY_VALIDATION_FAILED",
        }
        existing = {e.value for e in AuditEventType}
        missing = required - existing
        assert not missing, f"Missing Sprint 2.27.9 AuditEventType values: {missing}"

    def test_has_all_sprint_2278_events(self):
        from case_engine.models import AuditEventType
        required = {
            "DRY_RUN_MODE_ACTIVE", "PRODUCTION_MODE_ACTIVE",
            "DRY_RUN_EXECUTION", "DRY_RUN_ROUTE", "DRY_RUN_ACTION",
            "STARTUP_VALIDATION_PASSED", "STARTUP_VALIDATION_FAILED",
            "STARTUP_VALIDATION_WARNING", "INVARIANT_VIOLATION",
        }
        existing = {e.value for e in AuditEventType}
        missing = required - existing
        assert not missing, f"Missing Sprint 2.27.8 events: {missing}"


# ── ProductionRuntime field count ─────────────────────────────────────────────

class TestProductionRuntimeFieldCount:
    def test_has_at_least_37_fields(self):
        from runtime.assembly import ProductionRuntime
        fields = dataclasses.fields(ProductionRuntime)
        count = len(fields)
        assert count >= 37, f"Expected ≥37 ProductionRuntime fields, found {count}"

    def test_has_tenant_registry_field(self):
        from runtime.assembly import ProductionRuntime
        field_names = {f.name for f in dataclasses.fields(ProductionRuntime)}
        assert "tenant_registry" in field_names

    def test_has_client_resolver_field(self):
        from runtime.assembly import ProductionRuntime
        field_names = {f.name for f in dataclasses.fields(ProductionRuntime)}
        assert "client_resolver" in field_names

    def test_has_tenant_tool_registry_field(self):
        from runtime.assembly import ProductionRuntime
        field_names = {f.name for f in dataclasses.fields(ProductionRuntime)}
        assert "tenant_tool_registry" in field_names

    def test_tenant_fields_default_to_none(self):
        from runtime.assembly import ProductionRuntime
        for f in dataclasses.fields(ProductionRuntime):
            if f.name in ("tenant_registry", "client_resolver", "tenant_tool_registry"):
                assert f.default is None


# ── flow_diagram compliance ───────────────────────────────────────────────────

class TestFlowDiagramCompliance:
    def test_ticket_orchestrator_has_client_resolver_param(self):
        from case_engine.ticket_orchestration.orchestrator import TicketOrchestrator
        sig = inspect.signature(TicketOrchestrator.__init__)
        assert "client_resolver" in sig.parameters

    def test_orchestrator_client_resolver_defaults_none(self):
        from case_engine.ticket_orchestration.orchestrator import TicketOrchestrator
        sig = inspect.signature(TicketOrchestrator.__init__)
        param = sig.parameters["client_resolver"]
        assert param.default is None

    def test_client_resolver_has_resolve_method(self):
        from case_engine.tenant.resolver import ClientResolver
        assert hasattr(ClientResolver, "resolve")

    def test_client_resolver_resolve_returns_tenant_context(self):
        from case_engine.tenant.resolver import ClientResolver
        from case_engine.tenant.models import TenantContext
        from case_engine.tenant.registry import build_default_tenant_registry
        reg = build_default_tenant_registry()
        resolver = ClientResolver(reg)
        ctx = resolver.resolve("agent@unitybank.co.in")
        assert isinstance(ctx, TenantContext)

    def test_tenant_registry_lookup_is_dict_based(self):
        from case_engine.tenant.registry import TenantRegistry
        reg = TenantRegistry()
        result = reg.lookup_by_domain("unitybank.co.in")
        assert result is not None

    def test_case_has_tenant_context_field(self):
        from case_engine.models import Case
        field_names = {f.name for f in dataclasses.fields(Case)}
        assert "tenant_context" in field_names

    def test_case_tenant_context_not_in_db_row(self):
        from case_engine.models import Case
        c = Case(ticket_id="T-001", client="unity_bank")
        row = c.to_db_row()
        assert "tenant_context" not in row


# ── Backward compatibility ────────────────────────────────────────────────────

class TestBackwardCompatibility:
    def test_orchestrator_works_without_client_resolver(self):
        from case_engine.ticket_orchestration.orchestrator import TicketOrchestrator
        orch = TicketOrchestrator()
        assert orch._client_resolver is None

    def test_build_ticket_orchestrator_accepts_no_args(self):
        from case_engine.ticket_orchestration.orchestrator import build_ticket_orchestrator
        orch = build_ticket_orchestrator()
        assert orch is not None

    def test_build_ticket_orchestrator_accepts_client_resolver(self):
        from case_engine.ticket_orchestration.orchestrator import build_ticket_orchestrator
        from unittest.mock import MagicMock
        resolver = MagicMock()
        orch = build_ticket_orchestrator(client_resolver=resolver)
        assert orch._client_resolver is resolver

    def test_default_tenant_registry_has_unity_bank(self):
        from case_engine.tenant.registry import build_default_tenant_registry
        reg = build_default_tenant_registry()
        cfg = reg.lookup_by_client_id("unity_bank")
        assert cfg is not None


# ── Unity Bank tool registry ──────────────────────────────────────────────────

class TestUnityBankToolRegistry:
    EXPECTED_TOOLS = frozenset({
        "GetUserDetails", "GetSessionDetails", "GetFailureReason",
        "GetCaseHistory", "GetOnboardingStatus", "SessionLogsTool",
        "SessionSummaryTool", "SessionVideoTool", "MetricsTool", "ServerStatusTool",
    })

    def test_unity_bank_has_exactly_10_tools(self):
        from case_engine.tenant.registry import build_default_tenant_registry
        reg = build_default_tenant_registry()
        cfg = reg.lookup_by_client_id("unity_bank")
        assert len(cfg.tool_config.enabled_tools) == 10

    def test_unity_bank_tools_exact_set(self):
        from case_engine.tenant.registry import build_default_tenant_registry
        reg = build_default_tenant_registry()
        cfg = reg.lookup_by_client_id("unity_bank")
        assert set(cfg.tool_config.enabled_tools) == self.EXPECTED_TOOLS

    def test_unity_bank_adapter_supports_exact_same_10_tools(self):
        from case_engine.tenant.adapters import UnityBankAdapter
        adapter = UnityBankAdapter()
        assert set(adapter.supported_tools()) == self.EXPECTED_TOOLS

    def test_unity_bank_tenant_type_is_bank(self):
        from case_engine.tenant.registry import build_default_tenant_registry
        from case_engine.tenant.models import TenantType
        reg = build_default_tenant_registry()
        cfg = reg.lookup_by_client_id("unity_bank")
        assert cfg.tenant_type == TenantType.BANK

    def test_unity_bank_domain_is_unitybank_co_in(self):
        from case_engine.tenant.registry import build_default_tenant_registry
        reg = build_default_tenant_registry()
        cfg = reg.lookup_by_client_id("unity_bank")
        assert "unitybank.co.in" in cfg.domains


# ── Data-driven design (no if/else chains) ────────────────────────────────────

class TestDataDrivenDesign:
    def test_registry_lookup_is_dict_based_no_if_else(self):
        # Adding a new tenant to the registry should make it resolvable
        # without any code changes — verified by dynamically adding a config
        from case_engine.tenant.models import (
            TenantAuthConfig, TenantConfig, TenantEnvironment,
            TenantToolConfig, TenantType,
        )
        from case_engine.tenant.registry import TenantRegistry
        from case_engine.tenant.resolver import ClientResolver
        reg = TenantRegistry()
        new_cfg = TenantConfig(
            client_id="test_bank",
            client_name="Test Bank",
            domains=("testbank.co.in",),
            tenant_type=TenantType.BANK,
            environment=TenantEnvironment.UAT,
            tool_config=TenantToolConfig(enabled_tools=("ToolA",)),
            auth_config=TenantAuthConfig(credentials_ref="test_bank_ref"),
        )
        reg.register(new_cfg)
        resolver = ClientResolver(reg)
        ctx = resolver.resolve("agent@testbank.co.in")
        assert ctx.client_id == "test_bank"

    def test_adapter_map_is_dict_based(self):
        from case_engine.tenant.adapters import _ADAPTER_MAP
        assert isinstance(_ADAPTER_MAP, dict)
        assert "unity_bank" in _ADAPTER_MAP


# ── Tenant package exports ────────────────────────────────────────────────────

class TestTenantPackageExports:
    def test_package_has_all_attribute(self):
        import case_engine.tenant as tenant_pkg
        assert hasattr(tenant_pkg, "__all__")

    def test_tenant_context_exported(self):
        from case_engine.tenant import TenantContext
        assert TenantContext is not None

    def test_tenant_config_exported(self):
        from case_engine.tenant import TenantConfig
        assert TenantConfig is not None

    def test_tenant_registry_exported(self):
        from case_engine.tenant import TenantRegistry
        assert TenantRegistry is not None

    def test_client_resolver_exported(self):
        from case_engine.tenant import ClientResolver
        assert ClientResolver is not None

    def test_unknown_client_error_exported(self):
        from case_engine.tenant import UnknownClientError
        assert UnknownClientError is not None

    def test_tenant_aware_tool_registry_exported(self):
        from case_engine.tenant import TenantAwareToolRegistry
        assert TenantAwareToolRegistry is not None


# ── TenantContext immutability and pipeline contract ──────────────────────────

class TestTenantContextContract:
    def test_tenant_context_is_frozen(self):
        from case_engine.tenant.models import TenantContext
        assert TenantContext.__dataclass_params__.frozen

    def test_extract_domain_is_static(self):
        from case_engine.tenant.resolver import ClientResolver
        assert isinstance(
            inspect.getattr_static(ClientResolver, "extract_domain"),
            staticmethod,
        )

    def test_unknown_client_error_is_exception_subclass(self):
        from case_engine.tenant.models import UnknownClientError
        assert issubclass(UnknownClientError, Exception)

    def test_credentials_ref_never_the_actual_secret(self):
        from case_engine.tenant.registry import build_default_tenant_registry
        reg = build_default_tenant_registry()
        cfg = reg.lookup_by_client_id("unity_bank")
        # credentials_ref must be a short reference name, never an actual API key
        ref = cfg.auth_config.credentials_ref
        assert len(ref) < 100
        assert ref.strip() == ref  # no whitespace
        assert " " not in ref


# ── Startup validation tier conformance ──────────────────────────────────────

class TestStartupValidationTiers:
    def test_tenant_services_not_critical(self):
        from runtime.startup_validation import _TENANT_SERVICES, _CRITICAL_SERVICES
        for svc in _TENANT_SERVICES:
            assert svc not in _CRITICAL_SERVICES

    def test_tenant_services_checked_at_important_in_full_validation(self):
        from runtime.startup_validation import (
            validate_production_runtime,
            ValidationTier,
            _TENANT_SERVICES,
        )
        from unittest.mock import MagicMock
        runtime = MagicMock()
        # Ensure all critical services are present
        for svc in ("workflow_engine", "case_service", "audit_logger",
                     "action_gateway_service", "execution_service"):
            setattr(runtime, svc, MagicMock())
        for svc in ("clarification_service", "investigation_service",
                     "knowledge_service", "reasoning_service", "adapter_router"):
            setattr(runtime, svc, MagicMock())
        for svc in ("support_agent_runtime", "ticket_orchestrator", "router_service",
                     "knowledge_orchestrator", "response_generation_service",
                     "engineering_escalation_service"):
            setattr(runtime, svc, MagicMock())
        runtime.playbook_registry = None
        runtime.adapter_registry = None
        runtime.tenant_registry = MagicMock()
        runtime.tenant_registry.count.return_value = 1
        runtime.tenant_registry.validate.return_value = []
        runtime.tenant_registry.domain_count.return_value = 1
        runtime.tenant_registry.all_tenants.return_value = []
        runtime.client_resolver = MagicMock()
        runtime.tenant_tool_registry = MagicMock()
        result = validate_production_runtime(runtime)
        for check in result.checks:
            if check.service_name in _TENANT_SERVICES:
                assert check.tier == ValidationTier.IMPORTANT
