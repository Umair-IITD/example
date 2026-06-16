"""
tests/test_sprint222_backwards_compat.py

Sprint 2.22: Backwards compatibility regression tests.

Verifies that ALL Sprint 2.1 imports of case_engine.action_gateway continue to
work unchanged after the package refactoring.

Critical: If any of these imports break, Sprint 2.1/2.2 production code breaks.
"""
import pytest


# ── Import compatibility ───────────────────────────────────────────────────────

class TestImportCompat:
    def test_action_gateway_importable(self):
        from case_engine.action_gateway import ActionGateway
        assert ActionGateway is not None

    def test_action_gateway_error_importable(self):
        from case_engine.action_gateway import ActionGatewayError
        assert ActionGatewayError is not None

    def test_duplicate_action_error_importable(self):
        from case_engine.action_gateway import DuplicateActionError
        assert DuplicateActionError is not None

    def test_build_action_gateway_importable(self):
        from case_engine.action_gateway import build_action_gateway
        assert callable(build_action_gateway)

    def test_sprint222_imports_all_accessible(self):
        from case_engine.action_gateway import (
            ActionGatewayService,
            ApprovalEngine,
            GatewayApprovalDecision,
            GatewayApprovalStatus,
            GatewayRiskLevel,
            GatewayValidationStatus,
            ProposalGateway,
            build_action_gateway_service,
        )
        assert ActionGatewayService is not None
        assert build_action_gateway_service is not None


# ── Sprint 2.1 ActionGateway behaviour unchanged ──────────────────────────────

class TestActionGatewayBehaviour:
    def test_action_gateway_is_a_class(self):
        from case_engine.action_gateway import ActionGateway
        assert isinstance(ActionGateway, type)

    def test_action_gateway_error_is_exception(self):
        from case_engine.action_gateway import ActionGatewayError
        assert issubclass(ActionGatewayError, RuntimeError)

    def test_duplicate_action_error_is_gateway_error(self):
        from case_engine.action_gateway import ActionGatewayError, DuplicateActionError
        assert issubclass(DuplicateActionError, ActionGatewayError)

    def test_build_action_gateway_returns_gateway(self):
        from case_engine.action_gateway import ActionGateway, build_action_gateway
        gw = build_action_gateway(supabase_client=None)
        assert isinstance(gw, ActionGateway)

    def test_action_gateway_has_propose(self):
        from case_engine.action_gateway import ActionGateway
        assert hasattr(ActionGateway, "propose")

    def test_action_gateway_has_approve(self):
        from case_engine.action_gateway import ActionGateway
        assert hasattr(ActionGateway, "approve")

    def test_action_gateway_has_reject(self):
        from case_engine.action_gateway import ActionGateway
        assert hasattr(ActionGateway, "reject")

    def test_action_gateway_has_begin_execution(self):
        from case_engine.action_gateway import ActionGateway
        assert hasattr(ActionGateway, "begin_execution")

    def test_action_gateway_has_record_success(self):
        from case_engine.action_gateway import ActionGateway
        assert hasattr(ActionGateway, "record_success")

    def test_action_gateway_has_record_failure(self):
        from case_engine.action_gateway import ActionGateway
        assert hasattr(ActionGateway, "record_failure")

    def test_action_gateway_has_propose_rollback(self):
        from case_engine.action_gateway import ActionGateway
        assert hasattr(ActionGateway, "propose_rollback")


# ── Other modules that import action_gateway ──────────────────────────────────

class TestDownstreamImports:
    def test_action_runtime_still_imports(self):
        try:
            from case_engine import action_runtime
            assert action_runtime is not None
        except ImportError as e:
            pytest.fail(f"case_engine.action_runtime import broke after package refactor: {e}")

    def test_action_repository_still_imports(self):
        try:
            from case_engine import action_repository
            assert action_repository is not None
        except ImportError as e:
            pytest.fail(f"case_engine.action_repository import broke: {e}")

    def test_workflow_engine_still_imports(self):
        try:
            from case_engine.workflows.workflow_engine import WorkflowEngine
            assert WorkflowEngine is not None
        except ImportError as e:
            pytest.fail(f"WorkflowEngine import broke after package refactor: {e}")


# ── Sprint 2.21 action proposal still works ───────────────────────────────────

class TestSprint221StillWorks:
    def test_action_proposal_service_importable(self):
        from case_engine.actions import build_action_proposal_service
        assert callable(build_action_proposal_service)

    def test_workflow_execution_result_has_action_proposal_result(self):
        from case_engine.workflows.models import WorkflowExecutionResult
        r = WorkflowExecutionResult()
        assert hasattr(r, "action_proposal_result")
        assert r.action_proposal_result is None

    def test_workflow_execution_result_has_gateway_result(self):
        from case_engine.workflows.models import WorkflowExecutionResult
        r = WorkflowExecutionResult()
        assert hasattr(r, "gateway_result")
        assert r.gateway_result is None
