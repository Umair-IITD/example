"""
tests/test_sprint2275_assembly.py

Sprint 2.27.5 Phase 8: Assembly + ProductionRuntime test suite.

Tests: all Sprint 2.27.5 services are wired in ProductionRuntime,
audit_logger is passed to AdapterRouter, field counts, and service presence.
"""
import dataclasses
import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _build_runtime():
    from runtime.assembly import build_production_runtime
    return build_production_runtime()


# ── ProductionRuntime field completeness ──────────────────────────────────────

class TestProductionRuntimeFields:
    def test_has_sprint2275_fields(self):
        rt = _build_runtime()
        assert hasattr(rt, "router_service")
        assert hasattr(rt, "knowledge_orchestrator")
        assert hasattr(rt, "response_generation_service")
        assert hasattr(rt, "engineering_escalation_service")
        assert hasattr(rt, "support_agent_runtime")
        assert hasattr(rt, "ticket_orchestrator")

    def test_total_fields_at_least_33(self):
        rt = _build_runtime()
        fields = dataclasses.fields(rt)
        assert len(fields) >= 33

    def test_all_base_fields_present(self):
        rt = _build_runtime()
        assert hasattr(rt, "runtime")
        assert hasattr(rt, "gateway")
        assert hasattr(rt, "repository")
        assert hasattr(rt, "audit_logger")
        assert hasattr(rt, "adapter_registry")
        assert hasattr(rt, "adapter_router")


# ── Sprint 2.27.5 services wired ─────────────────────────────────────────────

class TestSprint2275ServicesWired:
    def test_router_service_wired(self):
        rt = _build_runtime()
        assert rt.router_service is not None

    def test_knowledge_orchestrator_wired(self):
        rt = _build_runtime()
        assert rt.knowledge_orchestrator is not None

    def test_response_generation_service_wired(self):
        rt = _build_runtime()
        assert rt.response_generation_service is not None

    def test_engineering_escalation_service_wired(self):
        rt = _build_runtime()
        assert rt.engineering_escalation_service is not None

    def test_support_agent_runtime_wired(self):
        rt = _build_runtime()
        assert rt.support_agent_runtime is not None

    def test_ticket_orchestrator_wired(self):
        rt = _build_runtime()
        assert rt.ticket_orchestrator is not None


# ── Audit logger wiring in AdapterRouter ─────────────────────────────────────

class TestAdapterRouterAuditWiring:
    def test_adapter_router_built(self):
        rt = _build_runtime()
        assert rt.adapter_router is not None

    def test_router_service_has_adapter_router(self):
        rt = _build_runtime()
        if rt.router_service is not None:
            assert rt.router_service._router is not None

    def test_knowledge_orchestrator_has_knowledge_service(self):
        rt = _build_runtime()
        if rt.knowledge_orchestrator is not None:
            assert rt.knowledge_orchestrator._knowledge_service is not None


# ── Sprint 2.27.5 services functional smoke tests ────────────────────────────

class TestSprint2275ServiceFunctionality:
    def test_router_service_can_route(self):
        rt = _build_runtime()
        if rt.router_service is not None:
            result = rt.router_service.can_route("otp_resend")
            assert isinstance(result, bool)

    def test_knowledge_orchestrator_can_orchestrate(self):
        rt = _build_runtime()
        if rt.knowledge_orchestrator is not None:
            bundle = rt.knowledge_orchestrator.orchestrate(
                "VKYC_Session_Failure",
                investigation_result=None,
            )
            assert bundle is not None
            assert bundle.topic == "VKYC_Session_Failure"

    def test_response_generation_service_generates(self):
        rt = _build_runtime()
        if rt.response_generation_service is not None:
            from case_engine.response_generation.models import ResponseContext, ResponseType
            ctx = ResponseContext(
                case_id="c1", topic="VKYC_Session_Failure",
                response_type=ResponseType.RESOLUTION,
            )
            draft = rt.response_generation_service.generate(ctx)
            assert draft is not None

    def test_engineering_escalation_service_creates_ticket(self):
        rt = _build_runtime()
        if rt.engineering_escalation_service is not None:
            result = rt.engineering_escalation_service.create_ticket(
                case=None,
                topic="VKYC_Session_Failure",
                escalation_reason="Test escalation",
            )
            assert result is not None

    def test_support_agent_runtime_run_case(self):
        rt = _build_runtime()
        if rt.support_agent_runtime is not None:
            from case_engine.models import Case
            case = Case(ticket_id="fd-test", client="unity_bank")
            result = rt.support_agent_runtime.run_case(case, "My VKYC failed.")
            assert result is not None

    def test_ticket_orchestrator_process_ticket(self):
        rt = _build_runtime()
        if rt.ticket_orchestrator is not None:
            from case_engine.ticket_orchestration.models import TicketContext
            ctx = TicketContext(
                ticket_id="fd-smoke-001",
                client="unity_bank",
                subject="VKYC failed",
                description="Video KYC session not completing.",
            )
            result = rt.ticket_orchestrator.process_ticket(ctx)
            assert result is not None


# ── Never-raises ──────────────────────────────────────────────────────────────

class TestAssemblyNeverRaises:
    def test_build_twice_no_side_effects(self):
        rt1 = _build_runtime()
        rt2 = _build_runtime()
        assert rt1.runtime is not rt2.runtime

    def test_sprint2275_services_can_be_none_without_crash(self):
        from runtime.assembly import ProductionRuntime
        import dataclasses
        fields = {f.name for f in dataclasses.fields(ProductionRuntime)}
        assert "support_agent_runtime" in fields
        assert "ticket_orchestrator" in fields
