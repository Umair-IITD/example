"""
runtime/startup_validation.py

Sprint 2.27.8: Startup Validation Framework.

Validates ProductionRuntime construction before the app serves traffic.
Fail-fast on broken CRITICAL services: if any CRITICAL service is None,
assert_production_ready() raises RuntimeError.

Tiers:
  CRITICAL    — Core pipeline breaks without these. App refuses to start.
  IMPORTANT   — Feature degradation. Log warning, continue.
  GOLDEN_PATH — Full ticket→response path. Log warning if missing.

Usage:
    from runtime.startup_validation import assert_production_ready, validate_production_runtime
    result = validate_production_runtime(production_runtime)
    assert_production_ready(production_runtime)   # raises on CRITICAL failures
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

LOGGER = logging.getLogger(__name__)


class ValidationTier(str, Enum):
    CRITICAL    = "CRITICAL"
    IMPORTANT   = "IMPORTANT"
    GOLDEN_PATH = "GOLDEN_PATH"


class ValidationStatus(str, Enum):
    PASSED  = "PASSED"
    FAILED  = "FAILED"
    WARNING = "WARNING"


@dataclass(frozen=True)
class RuntimeValidationCheck:
    """Result of a single service presence check."""
    service_name: str
    tier:         ValidationTier
    status:       ValidationStatus
    error_msg:    str = ""

    @property
    def passed(self) -> bool:
        return self.status == ValidationStatus.PASSED

    def to_dict(self) -> dict[str, Any]:
        return {
            "service_name": self.service_name,
            "tier":         self.tier.value,
            "status":       self.status.value,
            "error_msg":    self.error_msg,
        }


@dataclass(frozen=True)
class RuntimeValidationResult:
    """Aggregated result of all startup checks."""
    checks:          tuple[RuntimeValidationCheck, ...]
    overall_passed:  bool
    critical_failed: tuple[str, ...]
    warnings:        tuple[str, ...]

    @property
    def critical_failures(self) -> list[RuntimeValidationCheck]:
        return [c for c in self.checks if c.tier == ValidationTier.CRITICAL and not c.passed]

    @property
    def golden_path_ready(self) -> bool:
        return all(
            c.passed for c in self.checks
            if c.tier == ValidationTier.GOLDEN_PATH
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_passed":  self.overall_passed,
            "critical_failed": list(self.critical_failed),
            "warnings":        list(self.warnings),
            "checks":          [c.to_dict() for c in self.checks],
            "golden_path_ready": self.golden_path_ready,
        }


# ── Service tier registry ─────────────────────────────────────────────────────

_CRITICAL_SERVICES: tuple[str, ...] = (
    "workflow_engine",
    "case_service",
    "audit_logger",
    "action_gateway_service",
    "execution_service",
)

_IMPORTANT_SERVICES: tuple[str, ...] = (
    "clarification_service",
    "investigation_service",
    "knowledge_service",
    "reasoning_service",
    "adapter_router",
)

_GOLDEN_PATH_SERVICES: tuple[str, ...] = (
    "support_agent_runtime",
    "ticket_orchestrator",
    "router_service",
    "knowledge_orchestrator",
    "response_generation_service",
    "engineering_escalation_service",
)

# Sprint 2.27.9: Multi-Tenant Resolution services
_TENANT_SERVICES: tuple[str, ...] = (
    "tenant_registry",
    "client_resolver",
    "tenant_tool_registry",
)


# ── Validators ────────────────────────────────────────────────────────────────

def _check_service(
    runtime: Any,
    service_name: str,
    tier: ValidationTier,
) -> RuntimeValidationCheck:
    """Check that a service attribute is not None on the runtime."""
    value = getattr(runtime, service_name, None)
    if value is None:
        return RuntimeValidationCheck(
            service_name=service_name,
            tier=tier,
            status=ValidationStatus.FAILED,
            error_msg=f"{service_name} is None — not built during assembly",
        )
    return RuntimeValidationCheck(
        service_name=service_name,
        tier=tier,
        status=ValidationStatus.PASSED,
    )


def validate_playbooks(runtime: Any) -> list[RuntimeValidationCheck]:
    """Validate that playbooks have at least one terminal step each."""
    checks: list[RuntimeValidationCheck] = []
    registry = getattr(runtime, "playbook_registry", None)
    if registry is None:
        checks.append(RuntimeValidationCheck(
            service_name="playbook_registry",
            tier=ValidationTier.CRITICAL,
            status=ValidationStatus.FAILED,
            error_msg="playbook_registry is None — no playbooks loaded",
        ))
        return checks

    try:
        try:
            from case_engine.workflows.models import WorkflowStepType  # noqa: PLC0415
            _terminal_type_values: frozenset[str] = frozenset({
                WorkflowStepType.ESCALATE_CASE.value,
                WorkflowStepType.RESOLVE_CASE.value,
            })
        except Exception:
            _terminal_type_values = frozenset({"ESCALATE_CASE", "RESOLVE_CASE"})

        playbooks = list(registry)  # PlaybookRegistry is iterable
        if not playbooks:
            checks.append(RuntimeValidationCheck(
                service_name="playbook_registry",
                tier=ValidationTier.CRITICAL,
                status=ValidationStatus.FAILED,
                error_msg="playbook_registry is empty — no playbooks loaded",
            ))
            return checks

        for playbook in playbooks:
            step_type_values: set[str] = set()
            for step in playbook.steps:
                st = getattr(step, "step_type", None)
                if st is None:
                    continue
                # Handle both enum (.value) and plain string
                step_type_values.add(st.value if hasattr(st, "value") else str(st))
            has_terminal = bool(step_type_values & _terminal_type_values)
            status = ValidationStatus.PASSED if has_terminal else ValidationStatus.FAILED
            checks.append(RuntimeValidationCheck(
                service_name=f"playbook:{playbook.playbook_id}",
                tier=ValidationTier.CRITICAL,
                status=status,
                error_msg="" if has_terminal else (
                    f"Playbook {playbook.playbook_id} has no ESCALATE_CASE or RESOLVE_CASE terminal steps. "
                    f"Found: {sorted(step_type_values)}"
                ),
            ))

        checks.append(RuntimeValidationCheck(
            service_name="playbook_registry",
            tier=ValidationTier.CRITICAL,
            status=ValidationStatus.PASSED,
        ))
    except Exception as exc:
        checks.append(RuntimeValidationCheck(
            service_name="playbook_registry",
            tier=ValidationTier.IMPORTANT,
            status=ValidationStatus.WARNING,
            error_msg=f"Playbook validation skipped: {exc}",
        ))
    return checks


def validate_adapters(runtime: Any) -> list[RuntimeValidationCheck]:
    """Validate that at least one adapter is registered and healthy."""
    checks: list[RuntimeValidationCheck] = []
    registry = getattr(runtime, "adapter_registry", None)
    if registry is None:
        checks.append(RuntimeValidationCheck(
            service_name="adapter_registry",
            tier=ValidationTier.IMPORTANT,
            status=ValidationStatus.WARNING,
            error_msg="adapter_registry is None — no adapters available",
        ))
        return checks

    try:
        count = registry.count() if hasattr(registry, "count") else 0
        if count == 0:
            checks.append(RuntimeValidationCheck(
                service_name="adapter_registry",
                tier=ValidationTier.IMPORTANT,
                status=ValidationStatus.WARNING,
                error_msg="adapter_registry has 0 registered adapters",
            ))
        else:
            checks.append(RuntimeValidationCheck(
                service_name="adapter_registry",
                tier=ValidationTier.IMPORTANT,
                status=ValidationStatus.PASSED,
            ))
    except Exception as exc:
        checks.append(RuntimeValidationCheck(
            service_name="adapter_registry",
            tier=ValidationTier.IMPORTANT,
            status=ValidationStatus.WARNING,
            error_msg=f"Adapter count check failed: {exc}",
        ))
    return checks


def validate_tenant_registry(runtime: Any) -> list[RuntimeValidationCheck]:
    """
    Validate the tenant registry on the runtime.

    Per Sprint 2.27.9 Part 8:
    - Tenant registry must be loaded
    - At least one tenant configured
    - Domains unique (enforced by TenantRegistry.register())
    - Client IDs unique (enforced by TenantRegistry.register())

    Returns a list of RuntimeValidationCheck instances (IMPORTANT tier).
    """
    checks: list[RuntimeValidationCheck] = []
    registry = getattr(runtime, "tenant_registry", None)

    if registry is None:
        checks.append(RuntimeValidationCheck(
            service_name="tenant_registry",
            tier=ValidationTier.IMPORTANT,
            status=ValidationStatus.WARNING,
            error_msg="tenant_registry is None — multi-tenant client resolution unavailable",
        ))
        return checks

    try:
        count = registry.count() if hasattr(registry, "count") else 0
        if count == 0:
            checks.append(RuntimeValidationCheck(
                service_name="tenant_registry",
                tier=ValidationTier.IMPORTANT,
                status=ValidationStatus.FAILED,
                error_msg="TenantRegistry has no tenants — at least one tenant required",
            ))
            return checks

        errors: list[str] = registry.validate() if hasattr(registry, "validate") else []
        if errors:
            checks.append(RuntimeValidationCheck(
                service_name="tenant_registry",
                tier=ValidationTier.IMPORTANT,
                status=ValidationStatus.WARNING,
                error_msg=f"TenantRegistry validation warnings: {'; '.join(errors)}",
            ))
        else:
            checks.append(RuntimeValidationCheck(
                service_name="tenant_registry",
                tier=ValidationTier.IMPORTANT,
                status=ValidationStatus.PASSED,
            ))

        # Report tenant count
        checks.append(RuntimeValidationCheck(
            service_name="tenant_registry.count",
            tier=ValidationTier.IMPORTANT,
            status=ValidationStatus.PASSED,
            error_msg=f"{count} tenant(s) registered",
        ))

        # Check domain uniqueness constraint (already enforced by registry, verify)
        if hasattr(registry, "domain_count") and hasattr(registry, "all_tenants"):
            domain_count = registry.domain_count()
            checks.append(RuntimeValidationCheck(
                service_name="tenant_registry.domains",
                tier=ValidationTier.IMPORTANT,
                status=ValidationStatus.PASSED,
                error_msg=f"{domain_count} unique domain(s) registered",
            ))

    except Exception as exc:
        checks.append(RuntimeValidationCheck(
            service_name="tenant_registry",
            tier=ValidationTier.IMPORTANT,
            status=ValidationStatus.WARNING,
            error_msg=f"Tenant registry validation error: {exc}",
        ))

    return checks


def validate_production_runtime(runtime: Any) -> RuntimeValidationResult:
    """
    Run all startup validation checks against a ProductionRuntime.

    Returns RuntimeValidationResult. Does not raise.
    Callers who want fail-fast behaviour should use assert_production_ready().
    """
    checks: list[RuntimeValidationCheck] = []

    # CRITICAL tier
    for svc in _CRITICAL_SERVICES:
        checks.append(_check_service(runtime, svc, ValidationTier.CRITICAL))

    # IMPORTANT tier
    for svc in _IMPORTANT_SERVICES:
        checks.append(_check_service(runtime, svc, ValidationTier.IMPORTANT))

    # GOLDEN_PATH tier
    for svc in _GOLDEN_PATH_SERVICES:
        checks.append(_check_service(runtime, svc, ValidationTier.GOLDEN_PATH))

    # Sprint 2.27.9: Tenant resolution services (IMPORTANT tier)
    for svc in _TENANT_SERVICES:
        checks.append(_check_service(runtime, svc, ValidationTier.IMPORTANT))

    # Playbook validation
    checks.extend(validate_playbooks(runtime))

    # Adapter validation
    checks.extend(validate_adapters(runtime))

    # Tenant registry validation (Sprint 2.27.9)
    checks.extend(validate_tenant_registry(runtime))

    critical_failed = tuple(
        c.service_name for c in checks
        if c.tier == ValidationTier.CRITICAL and not c.passed
    )
    warnings = tuple(
        c.service_name for c in checks
        if not c.passed and c.tier != ValidationTier.CRITICAL
    )
    overall_passed = len(critical_failed) == 0

    result = RuntimeValidationResult(
        checks=tuple(checks),
        overall_passed=overall_passed,
        critical_failed=critical_failed,
        warnings=warnings,
    )

    _log_validation_result(result)
    return result


def assert_production_ready(runtime: Any) -> RuntimeValidationResult:
    """
    Validate runtime and raise RuntimeError if any CRITICAL check fails.

    Use this at application startup: if it raises, do not serve traffic.
    Returns the RuntimeValidationResult on success.
    """
    result = validate_production_runtime(runtime)
    if not result.overall_passed:
        failed_names = ", ".join(result.critical_failed)
        raise RuntimeError(
            f"Startup validation failed — CRITICAL services unavailable: {failed_names}. "
            "Fix assembly before serving traffic."
        )
    return result


def _log_validation_result(result: RuntimeValidationResult) -> None:
    """Log all check results at appropriate levels."""
    for check in result.checks:
        if check.status == ValidationStatus.PASSED:
            LOGGER.info(
                "startup_validation PASSED tier=%s service=%s",
                check.tier.value, check.service_name,
            )
        elif check.status == ValidationStatus.FAILED:
            if check.tier == ValidationTier.CRITICAL:
                LOGGER.error(
                    "startup_validation FAILED tier=CRITICAL service=%s error=%s",
                    check.service_name, check.error_msg,
                )
            else:
                LOGGER.warning(
                    "startup_validation FAILED tier=%s service=%s error=%s",
                    check.tier.value, check.service_name, check.error_msg,
                )
        else:
            LOGGER.warning(
                "startup_validation WARNING tier=%s service=%s msg=%s",
                check.tier.value, check.service_name, check.error_msg,
            )

    if result.overall_passed:
        LOGGER.info(
            "startup_validation COMPLETE overall=PASSED warnings=%d",
            len(result.warnings),
        )
    else:
        LOGGER.error(
            "startup_validation COMPLETE overall=FAILED critical_failed=%s",
            result.critical_failed,
        )
