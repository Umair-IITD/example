"""
tests/test_sprint2279_startup_validation.py

Sprint 2.27.9: Startup validation tests for tenant registry.

Covers:
  - validate_tenant_registry() with loaded registry → PASSED IMPORTANT checks
  - validate_tenant_registry() with None registry → WARNING check
  - validate_tenant_registry() with empty registry → FAILED check
  - validate_tenant_registry() with invalid registry → WARNING with errors
  - validate_production_runtime() includes _TENANT_SERVICES checks
  - _TENANT_SERVICES tuple contains expected service names
  - Tenant service failures are IMPORTANT tier (not CRITICAL)
  - Runtime with all tenant services → passes tenant checks
  - Runtime with no tenant services → IMPORTANT warnings only (not CRITICAL fail)
  - validate_tenant_registry returns RuntimeValidationCheck instances
"""
from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from runtime.startup_validation import (
    ValidationStatus,
    ValidationTier,
    RuntimeValidationCheck,
    validate_production_runtime,
    validate_tenant_registry,
    _TENANT_SERVICES,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_registry(count: int = 1, validate_errors: list[str] | None = None):
    reg = MagicMock()
    reg.count.return_value = count
    reg.validate.return_value = validate_errors if validate_errors is not None else []
    reg.domain_count.return_value = count
    reg.all_tenants.return_value = []
    return reg


def _make_runtime(**kwargs):
    runtime = MagicMock()
    for key, value in kwargs.items():
        setattr(runtime, key, value)
    # Default: all CRITICAL services present
    for svc in (
        "workflow_engine", "case_service", "audit_logger",
        "action_gateway_service", "execution_service",
    ):
        if not hasattr(runtime, svc) or getattr(runtime, svc) is None:
            setattr(runtime, svc, MagicMock())
    # Default: IMPORTANT services
    for svc in (
        "clarification_service", "investigation_service",
        "knowledge_service", "reasoning_service", "adapter_router",
    ):
        if not hasattr(runtime, svc) or getattr(runtime, svc) is None:
            setattr(runtime, svc, MagicMock())
    # Default: GOLDEN_PATH services
    for svc in (
        "support_agent_runtime", "ticket_orchestrator", "router_service",
        "knowledge_orchestrator", "response_generation_service",
        "engineering_escalation_service",
    ):
        if not hasattr(runtime, svc) or getattr(runtime, svc) is None:
            setattr(runtime, svc, MagicMock())
    # Playbook registry
    if not hasattr(runtime, "playbook_registry") or runtime.playbook_registry is None:
        setattr(runtime, "playbook_registry", None)
    # Adapter registry
    if not hasattr(runtime, "adapter_registry") or runtime.adapter_registry is None:
        setattr(runtime, "adapter_registry", None)
    return runtime


# ── _TENANT_SERVICES tuple ────────────────────────────────────────────────────

class TestTenantServicesTuple:
    def test_is_tuple(self):
        assert isinstance(_TENANT_SERVICES, tuple)

    def test_contains_tenant_registry(self):
        assert "tenant_registry" in _TENANT_SERVICES

    def test_contains_client_resolver(self):
        assert "client_resolver" in _TENANT_SERVICES

    def test_contains_tenant_tool_registry(self):
        assert "tenant_tool_registry" in _TENANT_SERVICES

    def test_has_three_services(self):
        assert len(_TENANT_SERVICES) == 3


# ── validate_tenant_registry() ────────────────────────────────────────────────

class TestValidateTenantRegistry:
    def test_none_registry_returns_warning(self):
        runtime = MagicMock()
        runtime.tenant_registry = None
        checks = validate_tenant_registry(runtime)
        assert len(checks) >= 1
        statuses = [c.status for c in checks]
        assert ValidationStatus.WARNING in statuses or ValidationStatus.FAILED in statuses

    def test_none_registry_check_is_important_tier(self):
        runtime = MagicMock()
        runtime.tenant_registry = None
        checks = validate_tenant_registry(runtime)
        for check in checks:
            assert check.tier == ValidationTier.IMPORTANT

    def test_empty_registry_returns_failed(self):
        runtime = MagicMock()
        runtime.tenant_registry = _make_registry(count=0)
        checks = validate_tenant_registry(runtime)
        failed = [c for c in checks if c.status == ValidationStatus.FAILED]
        assert len(failed) >= 1

    def test_valid_registry_returns_passed(self):
        runtime = MagicMock()
        runtime.tenant_registry = _make_registry(count=1)
        checks = validate_tenant_registry(runtime)
        passed = [c for c in checks if c.status == ValidationStatus.PASSED]
        assert len(passed) >= 1

    def test_valid_registry_no_critical_failures(self):
        runtime = MagicMock()
        runtime.tenant_registry = _make_registry(count=1)
        checks = validate_tenant_registry(runtime)
        critical_fails = [
            c for c in checks
            if c.tier == ValidationTier.CRITICAL and c.status == ValidationStatus.FAILED
        ]
        assert critical_fails == []

    def test_registry_with_validation_errors_returns_warning(self):
        runtime = MagicMock()
        runtime.tenant_registry = _make_registry(
            count=1,
            validate_errors=["Tenant 'x' is enabled but has no tools configured"],
        )
        checks = validate_tenant_registry(runtime)
        statuses = [c.status for c in checks]
        assert ValidationStatus.WARNING in statuses

    def test_valid_registry_all_checks_are_important(self):
        runtime = MagicMock()
        runtime.tenant_registry = _make_registry(count=2)
        checks = validate_tenant_registry(runtime)
        for check in checks:
            assert check.tier == ValidationTier.IMPORTANT

    def test_returns_list_of_runtime_validation_checks(self):
        runtime = MagicMock()
        runtime.tenant_registry = _make_registry(count=1)
        checks = validate_tenant_registry(runtime)
        assert isinstance(checks, list)
        for c in checks:
            assert isinstance(c, RuntimeValidationCheck)

    def test_count_reported_in_checks(self):
        runtime = MagicMock()
        runtime.tenant_registry = _make_registry(count=3)
        checks = validate_tenant_registry(runtime)
        # should have a count check
        count_checks = [c for c in checks if "count" in c.service_name.lower()]
        assert len(count_checks) >= 1

    def test_domain_count_reported(self):
        runtime = MagicMock()
        reg = _make_registry(count=1)
        reg.domain_count.return_value = 2
        runtime.tenant_registry = reg
        checks = validate_tenant_registry(runtime)
        domain_checks = [c for c in checks if "domain" in c.service_name.lower()]
        assert len(domain_checks) >= 1


# ── validate_production_runtime() tenant service checks ───────────────────────

class TestValidateProductionRuntimeTenantServices:
    def test_tenant_services_checked_in_full_validation(self):
        runtime = _make_runtime(
            tenant_registry=_make_registry(count=1),
            client_resolver=MagicMock(),
            tenant_tool_registry=MagicMock(),
        )
        result = validate_production_runtime(runtime)
        service_names = [c.service_name for c in result.checks]
        assert "tenant_registry" in service_names
        assert "client_resolver" in service_names
        assert "tenant_tool_registry" in service_names

    def test_tenant_services_are_important_tier(self):
        runtime = _make_runtime(
            tenant_registry=_make_registry(count=1),
            client_resolver=MagicMock(),
            tenant_tool_registry=MagicMock(),
        )
        result = validate_production_runtime(runtime)
        for check in result.checks:
            if check.service_name in _TENANT_SERVICES:
                assert check.tier == ValidationTier.IMPORTANT

    def test_missing_tenant_services_do_not_cause_critical_failure(self):
        runtime = _make_runtime(
            tenant_registry=None,
            client_resolver=None,
            tenant_tool_registry=None,
        )
        result = validate_production_runtime(runtime)
        # Critical failures should only come from _CRITICAL_SERVICES, not tenant services
        critical_fails = [
            c.service_name for c in result.checks
            if c.tier == ValidationTier.CRITICAL and not c.passed
        ]
        for svc in _TENANT_SERVICES:
            assert svc not in critical_fails

    def test_runtime_with_all_tenant_services_passes_tenant_checks(self):
        runtime = _make_runtime(
            tenant_registry=_make_registry(count=1),
            client_resolver=MagicMock(),
            tenant_tool_registry=MagicMock(),
        )
        result = validate_production_runtime(runtime)
        tenant_svc_checks = [
            c for c in result.checks
            if c.service_name in _TENANT_SERVICES
        ]
        for check in tenant_svc_checks:
            assert check.status == ValidationStatus.PASSED

    def test_missing_all_tenant_services_only_warnings(self):
        runtime = _make_runtime(
            tenant_registry=None,
            client_resolver=None,
            tenant_tool_registry=None,
        )
        result = validate_production_runtime(runtime)
        tenant_svc_checks = [
            c for c in result.checks
            if c.service_name in _TENANT_SERVICES
        ]
        for check in tenant_svc_checks:
            assert check.tier == ValidationTier.IMPORTANT
            assert check.status != ValidationStatus.PASSED

    def test_missing_tenant_services_only_cause_important_failures(self):
        runtime = _make_runtime(
            tenant_registry=None,
            client_resolver=None,
            tenant_tool_registry=None,
        )
        result = validate_production_runtime(runtime)
        # Tenant service failures must be IMPORTANT (not CRITICAL)
        for svc in ("tenant_registry", "client_resolver", "tenant_tool_registry"):
            matching = [c for c in result.checks if c.service_name == svc]
            for check in matching:
                assert check.tier != ValidationTier.CRITICAL

    def test_validate_tenant_registry_called_in_full_validation(self):
        runtime = _make_runtime(tenant_registry=_make_registry(count=1))
        result = validate_production_runtime(runtime)
        # validate_tenant_registry produces extra checks beyond just the service presence check
        tenant_checks = [
            c for c in result.checks
            if "tenant" in c.service_name.lower()
        ]
        assert len(tenant_checks) > 1  # more than just service presence

    def test_result_has_checks_tuple(self):
        runtime = _make_runtime(tenant_registry=_make_registry(count=1))
        result = validate_production_runtime(runtime)
        assert isinstance(result.checks, tuple)

    def test_overall_passed_false_only_when_critical_fails(self):
        runtime = _make_runtime()
        runtime.workflow_engine = None  # CRITICAL service missing
        result = validate_production_runtime(runtime)
        assert result.overall_passed is False
        assert "workflow_engine" in result.critical_failed
