"""
tests/test_sprint226_assembly.py

Sprint 2.26 Part A: Runtime assembly wiring tests.

Coverage:
  - ProductionRuntime has all workflow service fields
  - build_production_runtime() constructs ProductionRuntime
  - Offline mode (no supabase, no freshdesk): all existing fields still wired
  - Workflow service fields are populated in offline mode
  - WorkflowEngine receives injected services
  - CaseService in ProductionRuntime has WorkflowEngine
  - _build_workflow_services() returns dict with all 10 keys
  - Services are not None after offline build (most should build without supabase)
  - WorkflowEngine has all 7 injected services (not None in offline mode)
"""
from __future__ import annotations

import dataclasses
import pytest

from runtime.assembly import ProductionRuntime, build_production_runtime, _build_workflow_services


# ── ProductionRuntime dataclass fields ────────────────────────────────────────

class TestProductionRuntimeFields:
    def _field_names(self) -> set[str]:
        return {f.name for f in dataclasses.fields(ProductionRuntime)}

    def test_original_action_fields_present(self):
        fields = self._field_names()
        for f in ("runtime", "gateway", "repository", "executor_registry",
                   "provider_registry", "router", "worker", "health", "watchdog",
                   "audit_repository", "audit_service", "audit_logger",
                   "metrics_service", "operations", "recovery"):
            assert f in fields, f"Missing field: {f}"

    def test_workflow_service_fields_present(self):
        fields = self._field_names()
        for f in ("clarification_service", "investigation_service", "knowledge_service",
                   "reasoning_service", "action_proposal_service", "action_gateway_service",
                   "execution_service", "workflow_engine", "playbook_registry", "case_service"):
            assert f in fields, f"Missing Sprint 2.26 field: {f}"

    def test_total_field_count(self):
        # 15 original + 10 workflow = 25
        fields = self._field_names()
        assert len(fields) >= 25


# ── build_production_runtime() ────────────────────────────────────────────────

class TestBuildProductionRuntime:
    def test_returns_production_runtime(self):
        stack = build_production_runtime(supabase_client=None, freshdesk_config=None)
        assert isinstance(stack, ProductionRuntime)

    def test_action_fields_wired(self):
        stack = build_production_runtime(supabase_client=None, freshdesk_config=None)
        assert stack.runtime   is not None
        assert stack.gateway   is not None
        assert stack.worker    is not None
        assert stack.watchdog  is not None
        assert stack.audit_logger is not None

    def test_workflow_engine_wired(self):
        stack = build_production_runtime(supabase_client=None, freshdesk_config=None)
        assert stack.workflow_engine is not None

    def test_playbook_registry_wired(self):
        stack = build_production_runtime(supabase_client=None, freshdesk_config=None)
        assert stack.playbook_registry is not None

    def test_clarification_service_wired(self):
        stack = build_production_runtime(supabase_client=None, freshdesk_config=None)
        assert stack.clarification_service is not None

    def test_execution_service_wired(self):
        stack = build_production_runtime(supabase_client=None, freshdesk_config=None)
        assert stack.execution_service is not None

    def test_reasoning_service_wired(self):
        stack = build_production_runtime(supabase_client=None, freshdesk_config=None)
        assert stack.reasoning_service is not None

    def test_action_proposal_service_wired(self):
        stack = build_production_runtime(supabase_client=None, freshdesk_config=None)
        assert stack.action_proposal_service is not None

    def test_case_service_wired(self):
        stack = build_production_runtime(supabase_client=None, freshdesk_config=None)
        assert stack.case_service is not None


# ── _build_workflow_services() ────────────────────────────────────────────────

class TestBuildWorkflowServices:
    def _audit(self):
        from audit.logger import AuditLogger
        return AuditLogger(repository=None)

    def _gateway(self):
        from case_engine.action_gateway import ActionGateway
        from case_engine.action_repository import ActionRepository
        from metrics import MetricsCollector, MetricsService
        repo  = ActionRepository(supabase_client=None)
        ms    = MetricsService(MetricsCollector())
        return ActionGateway(repository=repo, metrics_service=ms)

    def test_returns_dict(self):
        result = _build_workflow_services(self._audit(), self._gateway())
        assert isinstance(result, dict)

    def test_has_all_10_keys(self):
        result = _build_workflow_services(self._audit(), self._gateway())
        expected = {
            "clarification_service", "investigation_service", "knowledge_service",
            "reasoning_service", "action_proposal_service", "action_gateway_service",
            "execution_service", "workflow_engine", "playbook_registry", "case_service",
        }
        for k in expected:
            assert k in result, f"Missing key: {k}"

    def test_workflow_engine_not_none(self):
        result = _build_workflow_services(self._audit(), self._gateway())
        assert result["workflow_engine"] is not None

    def test_playbook_registry_not_none(self):
        result = _build_workflow_services(self._audit(), self._gateway())
        assert result["playbook_registry"] is not None

    def test_clarification_service_not_none(self):
        result = _build_workflow_services(self._audit(), self._gateway())
        assert result["clarification_service"] is not None

    def test_execution_service_not_none(self):
        result = _build_workflow_services(self._audit(), self._gateway())
        assert result["execution_service"] is not None

    def test_reasoning_service_not_none(self):
        result = _build_workflow_services(self._audit(), self._gateway())
        assert result["reasoning_service"] is not None

    def test_workflow_engine_has_clarification_service(self):
        result = _build_workflow_services(self._audit(), self._gateway())
        wf_eng = result["workflow_engine"]
        assert wf_eng is not None
        assert wf_eng._clarification_service is not None

    def test_workflow_engine_has_execution_service(self):
        result = _build_workflow_services(self._audit(), self._gateway())
        wf_eng = result["workflow_engine"]
        assert wf_eng._execution_service is not None

    def test_workflow_engine_has_reasoning_service(self):
        result = _build_workflow_services(self._audit(), self._gateway())
        wf_eng = result["workflow_engine"]
        assert wf_eng._reasoning_service is not None


# ── ProductionRuntime backward compatibility ──────────────────────────────────

class TestProductionRuntimeBackwardCompat:
    def test_old_fields_accessible_by_attribute(self):
        stack = build_production_runtime(supabase_client=None)
        # All original Sprint 2.6 fields must still be directly accessible
        _ = stack.runtime
        _ = stack.gateway
        _ = stack.repository
        _ = stack.executor_registry
        _ = stack.audit_logger

    def test_new_fields_default_to_none_if_failed(self):
        """If a service fails to build, its field is None (not AttributeError)."""
        stack = build_production_runtime(supabase_client=None)
        # All new fields should be accessible (may be None for some)
        _ = stack.clarification_service
        _ = stack.workflow_engine
        _ = stack.playbook_registry
