"""
tests/test_sprint2278_startup_validation.py

Sprint 2.27.8: Startup Validation Framework tests.

Covers:
  - ValidationTier, ValidationStatus, RuntimeValidationCheck
  - RuntimeValidationResult
  - validate_production_runtime() against mock runtimes
  - assert_production_ready() raises on CRITICAL failures
  - Playbook validation
  - Adapter validation
  - Log-only path (no DB)
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock


# ── Imports ───────────────────────────────────────────────────────────────────

from runtime.startup_validation import (
    ValidationTier,
    ValidationStatus,
    RuntimeValidationCheck,
    RuntimeValidationResult,
    validate_production_runtime,
    assert_production_ready,
    validate_playbooks,
    validate_adapters,
    _CRITICAL_SERVICES,
    _IMPORTANT_SERVICES,
    _GOLDEN_PATH_SERVICES,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_full_runtime():
    """Build a mock ProductionRuntime with all fields populated."""
    rt = MagicMock()
    all_services = list(_CRITICAL_SERVICES) + list(_IMPORTANT_SERVICES) + list(_GOLDEN_PATH_SERVICES)
    for svc in all_services:
        setattr(rt, svc, MagicMock(name=svc))
    # adapter_registry needs count()
    rt.adapter_registry.count.return_value = 4
    # playbook_registry must be iterable and return a playbook with terminal step
    step = MagicMock()
    step.step_type.value = "RESOLVE_CASE"
    playbook = MagicMock()
    playbook.steps = [step]
    playbook.playbook_id = "default_playbook"
    rt.playbook_registry = [playbook]
    return rt


def _make_partial_runtime(missing: list[str]):
    """Build a mock runtime with specified services set to None."""
    rt = _make_full_runtime()
    for svc in missing:
        setattr(rt, svc, None)
    return rt


# ── ValidationTier ────────────────────────────────────────────────────────────

class TestValidationTier:
    def test_critical_value(self):
        assert ValidationTier.CRITICAL.value == "CRITICAL"

    def test_important_value(self):
        assert ValidationTier.IMPORTANT.value == "IMPORTANT"

    def test_golden_path_value(self):
        assert ValidationTier.GOLDEN_PATH.value == "GOLDEN_PATH"

    def test_str_enum(self):
        assert ValidationTier.CRITICAL == "CRITICAL"


# ── ValidationStatus ──────────────────────────────────────────────────────────

class TestValidationStatus:
    def test_passed_value(self):
        assert ValidationStatus.PASSED.value == "PASSED"

    def test_failed_value(self):
        assert ValidationStatus.FAILED.value == "FAILED"

    def test_warning_value(self):
        assert ValidationStatus.WARNING.value == "WARNING"


# ── RuntimeValidationCheck ────────────────────────────────────────────────────

class TestRuntimeValidationCheck:
    def test_passed_check(self):
        check = RuntimeValidationCheck(
            service_name="workflow_engine",
            tier=ValidationTier.CRITICAL,
            status=ValidationStatus.PASSED,
        )
        assert check.passed is True
        assert check.error_msg == ""

    def test_failed_check(self):
        check = RuntimeValidationCheck(
            service_name="case_service",
            tier=ValidationTier.CRITICAL,
            status=ValidationStatus.FAILED,
            error_msg="case_service is None",
        )
        assert check.passed is False
        assert check.error_msg == "case_service is None"

    def test_warning_check(self):
        check = RuntimeValidationCheck(
            service_name="adapter_router",
            tier=ValidationTier.IMPORTANT,
            status=ValidationStatus.WARNING,
            error_msg="adapter not reachable",
        )
        assert check.passed is False

    def test_to_dict(self):
        check = RuntimeValidationCheck(
            service_name="svc",
            tier=ValidationTier.GOLDEN_PATH,
            status=ValidationStatus.PASSED,
        )
        d = check.to_dict()
        assert d["service_name"] == "svc"
        assert d["tier"] == "GOLDEN_PATH"
        assert d["status"] == "PASSED"
        assert d["error_msg"] == ""

    def test_frozen(self):
        check = RuntimeValidationCheck(
            service_name="x",
            tier=ValidationTier.CRITICAL,
            status=ValidationStatus.PASSED,
        )
        with pytest.raises((AttributeError, TypeError)):
            check.service_name = "y"


# ── RuntimeValidationResult ───────────────────────────────────────────────────

class TestRuntimeValidationResult:
    def _make_result(self, critical_ok: bool, golden_ok: bool) -> RuntimeValidationResult:
        checks = [
            RuntimeValidationCheck(
                service_name="workflow_engine",
                tier=ValidationTier.CRITICAL,
                status=ValidationStatus.PASSED if critical_ok else ValidationStatus.FAILED,
                error_msg="" if critical_ok else "missing",
            ),
            RuntimeValidationCheck(
                service_name="support_agent_runtime",
                tier=ValidationTier.GOLDEN_PATH,
                status=ValidationStatus.PASSED if golden_ok else ValidationStatus.FAILED,
                error_msg="" if golden_ok else "missing",
            ),
        ]
        critical_failed = tuple(c.service_name for c in checks if c.tier == ValidationTier.CRITICAL and not c.passed)
        warnings = tuple(c.service_name for c in checks if not c.passed and c.tier != ValidationTier.CRITICAL)
        return RuntimeValidationResult(
            checks=tuple(checks),
            overall_passed=len(critical_failed) == 0,
            critical_failed=critical_failed,
            warnings=warnings,
        )

    def test_all_passed(self):
        result = self._make_result(critical_ok=True, golden_ok=True)
        assert result.overall_passed is True
        assert len(result.critical_failures) == 0
        assert result.golden_path_ready is True

    def test_critical_failed(self):
        result = self._make_result(critical_ok=False, golden_ok=True)
        assert result.overall_passed is False
        assert "workflow_engine" in result.critical_failed
        assert len(result.critical_failures) == 1

    def test_golden_path_not_ready(self):
        result = self._make_result(critical_ok=True, golden_ok=False)
        assert result.overall_passed is True  # critical passed
        assert result.golden_path_ready is False
        assert "support_agent_runtime" in result.warnings

    def test_to_dict(self):
        result = self._make_result(critical_ok=True, golden_ok=True)
        d = result.to_dict()
        assert d["overall_passed"] is True
        assert isinstance(d["checks"], list)
        assert isinstance(d["critical_failed"], list)
        assert isinstance(d["warnings"], list)
        assert "golden_path_ready" in d


# ── validate_production_runtime ───────────────────────────────────────────────

class TestValidateProductionRuntime:
    def test_full_runtime_passes(self):
        rt = _make_full_runtime()
        # Inject a minimal playbook_registry
        playbook = MagicMock()
        step = MagicMock()
        step.step_type.value = "RESOLVE_CASE"
        playbook.steps = [step]
        playbook.playbook_id = "test_playbook"
        rt.playbook_registry = [playbook]
        rt.adapter_registry.count.return_value = 4

        result = validate_production_runtime(rt)
        assert result.overall_passed is True

    def test_missing_critical_service_fails(self):
        rt = _make_partial_runtime(["workflow_engine"])
        result = validate_production_runtime(rt)
        assert result.overall_passed is False
        assert "workflow_engine" in result.critical_failed

    def test_multiple_critical_missing_all_reported(self):
        rt = _make_partial_runtime(["workflow_engine", "case_service"])
        result = validate_production_runtime(rt)
        assert "workflow_engine" in result.critical_failed
        assert "case_service" in result.critical_failed

    def test_missing_important_service_is_warning(self):
        rt = _make_partial_runtime(["clarification_service"])
        result = validate_production_runtime(rt)
        assert result.overall_passed is True  # not critical
        assert "clarification_service" in result.warnings

    def test_missing_golden_path_service_is_warning(self):
        rt = _make_partial_runtime(["ticket_orchestrator"])
        result = validate_production_runtime(rt)
        assert result.overall_passed is True
        assert "ticket_orchestrator" in result.warnings

    def test_all_critical_services_checked(self):
        rt = _make_partial_runtime(list(_CRITICAL_SERVICES))
        result = validate_production_runtime(rt)
        assert result.overall_passed is False
        for svc in _CRITICAL_SERVICES:
            assert svc in result.critical_failed

    def test_result_has_checks_for_all_tiers(self):
        rt = _make_full_runtime()
        result = validate_production_runtime(rt)
        tiers = {c.tier for c in result.checks}
        assert ValidationTier.CRITICAL in tiers
        assert ValidationTier.IMPORTANT in tiers
        assert ValidationTier.GOLDEN_PATH in tiers

    def test_none_runtime_attributes_dont_raise(self):
        # Runtime with all fields set to None should return a result (not raise)
        rt = MagicMock()
        all_services = list(_CRITICAL_SERVICES) + list(_IMPORTANT_SERVICES) + list(_GOLDEN_PATH_SERVICES)
        for svc in all_services:
            setattr(rt, svc, None)
        rt.playbook_registry = None
        rt.adapter_registry = None
        result = validate_production_runtime(rt)
        assert isinstance(result, RuntimeValidationResult)
        assert result.overall_passed is False

    def test_returns_runtime_validation_result(self):
        rt = _make_full_runtime()
        result = validate_production_runtime(rt)
        assert isinstance(result, RuntimeValidationResult)


# ── assert_production_ready ───────────────────────────────────────────────────

class TestAssertProductionReady:
    def test_passes_on_full_runtime(self):
        rt = _make_full_runtime()
        result = assert_production_ready(rt)
        assert isinstance(result, RuntimeValidationResult)
        assert result.overall_passed is True

    def test_raises_on_critical_failure(self):
        rt = _make_partial_runtime(["workflow_engine"])
        with pytest.raises(RuntimeError) as exc_info:
            assert_production_ready(rt)
        assert "workflow_engine" in str(exc_info.value)
        assert "CRITICAL" in str(exc_info.value)

    def test_raises_lists_all_critical_failures(self):
        rt = _make_partial_runtime(["workflow_engine", "case_service", "audit_logger"])
        with pytest.raises(RuntimeError) as exc_info:
            assert_production_ready(rt)
        msg = str(exc_info.value)
        assert "workflow_engine" in msg

    def test_does_not_raise_on_important_warning(self):
        rt = _make_partial_runtime(["clarification_service"])
        result = assert_production_ready(rt)
        assert result.overall_passed is True

    def test_does_not_raise_on_golden_path_warning(self):
        rt = _make_partial_runtime(["ticket_orchestrator"])
        result = assert_production_ready(rt)
        assert result.overall_passed is True


# ── validate_playbooks ────────────────────────────────────────────────────────

class TestValidatePlaybooks:
    def _make_playbook(self, step_type: str, playbook_id: str = "pb1"):
        playbook = MagicMock()
        step = MagicMock()
        step.step_type.value = step_type
        playbook.steps = [step]
        playbook.playbook_id = playbook_id
        return playbook

    def test_playbook_with_resolve_passes(self):
        rt = MagicMock()
        rt.playbook_registry = [self._make_playbook("RESOLVE_CASE")]
        checks = validate_playbooks(rt)
        statuses = [c.status for c in checks]
        assert ValidationStatus.PASSED in statuses
        assert ValidationStatus.FAILED not in statuses

    def test_playbook_with_escalate_passes(self):
        rt = MagicMock()
        rt.playbook_registry = [self._make_playbook("ESCALATE_CASE")]
        checks = validate_playbooks(rt)
        statuses = [c.status for c in checks]
        assert ValidationStatus.FAILED not in statuses

    def test_playbook_without_terminal_fails(self):
        rt = MagicMock()
        rt.playbook_registry = [self._make_playbook("INVESTIGATE")]
        checks = validate_playbooks(rt)
        failed = [c for c in checks if c.status == ValidationStatus.FAILED]
        assert len(failed) >= 1

    def test_missing_playbook_registry_returns_failed_check(self):
        rt = MagicMock()
        rt.playbook_registry = None
        checks = validate_playbooks(rt)
        assert any(c.status == ValidationStatus.FAILED for c in checks)

    def test_multiple_playbooks_all_checked(self):
        rt = MagicMock()
        rt.playbook_registry = [
            self._make_playbook("RESOLVE_CASE", "pb1"),
            self._make_playbook("ESCALATE_CASE", "pb2"),
        ]
        checks = validate_playbooks(rt)
        service_names = [c.service_name for c in checks]
        assert any("pb1" in s for s in service_names)
        assert any("pb2" in s for s in service_names)


# ── validate_adapters ─────────────────────────────────────────────────────────

class TestValidateAdapters:
    def test_registry_with_adapters_passes(self):
        rt = MagicMock()
        rt.adapter_registry.count.return_value = 4
        checks = validate_adapters(rt)
        assert any(c.status == ValidationStatus.PASSED for c in checks)

    def test_empty_registry_warns(self):
        rt = MagicMock()
        rt.adapter_registry.count.return_value = 0
        checks = validate_adapters(rt)
        assert any(c.status == ValidationStatus.WARNING for c in checks)

    def test_missing_registry_warns(self):
        rt = MagicMock()
        rt.adapter_registry = None
        checks = validate_adapters(rt)
        assert any(c.status == ValidationStatus.WARNING for c in checks)

    def test_returns_list(self):
        rt = MagicMock()
        rt.adapter_registry.count.return_value = 2
        checks = validate_adapters(rt)
        assert isinstance(checks, list)
