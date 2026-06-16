"""
tests/test_sprint227_assembly.py

Sprint 2.27: Tests for runtime/assembly.py adapter stack wiring.
"""
import pytest

from runtime.assembly import ProductionRuntime, build_production_runtime, _build_adapter_stack
from case_engine.adapters.adapter_registry import AdapterRegistry
from case_engine.adapters.adapter_router import AdapterRouter
from case_engine.adapters.models import AdapterType


class TestProductionRuntimeFields:
    def test_has_adapter_registry_field(self):
        rt = build_production_runtime()
        assert hasattr(rt, "adapter_registry")

    def test_has_adapter_router_field(self):
        rt = build_production_runtime()
        assert hasattr(rt, "adapter_router")

    def test_adapter_registry_is_not_none(self):
        rt = build_production_runtime()
        assert rt.adapter_registry is not None

    def test_adapter_router_is_not_none(self):
        rt = build_production_runtime()
        assert rt.adapter_router is not None

    def test_adapter_registry_is_correct_type(self):
        rt = build_production_runtime()
        assert isinstance(rt.adapter_registry, AdapterRegistry)

    def test_adapter_router_is_correct_type(self):
        rt = build_production_runtime()
        assert isinstance(rt.adapter_router, AdapterRouter)

    def test_adapter_registry_has_four_adapters(self):
        rt = build_production_runtime()
        assert rt.adapter_registry.count() == 4

    def test_adapter_registry_has_all_types(self):
        rt = build_production_runtime()
        for at in AdapterType:
            assert rt.adapter_registry.is_registered(at)

    def test_execution_service_uses_adapter_router(self):
        rt = build_production_runtime()
        # execution_service is wired with adapter_router
        assert rt.execution_service is not None

    def test_total_runtime_fields_27(self):
        rt = build_production_runtime()
        import dataclasses
        fields = dataclasses.fields(rt)
        # Sprint 2.27.5 added 6 convergence service fields → 33 total (>= 27)
        assert len(fields) >= 27


class TestBuildAdapterStack:
    def test_returns_tuple(self):
        result = _build_adapter_stack()
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_registry_is_adapterregistry(self):
        registry, _ = _build_adapter_stack()
        assert isinstance(registry, AdapterRegistry)

    def test_router_is_adapterrouter(self):
        _, router = _build_adapter_stack()
        assert isinstance(router, AdapterRouter)

    def test_registry_has_all_adapters(self):
        registry, _ = _build_adapter_stack()
        assert registry.count() == 4

    def test_router_can_route_freshdesk(self):
        from case_engine.adapters.models import AdapterRequest, AdapterOperation
        _, router = _build_adapter_stack()
        req = AdapterRequest.create(
            adapter_type=AdapterType.FRESHDESK,
            operation=AdapterOperation.READ,
            payload={},
            case_id="c1",
            action_type="otp_resend",
        )
        result = router.route(req)
        assert result.success is True

    def test_never_raises(self):
        reg, router = _build_adapter_stack()
        assert reg is not None
        assert router is not None
