"""
tests/test_sprint227_adapter_base.py

Sprint 2.27: Tests for the abstract Adapter base class.
"""
import pytest
from abc import ABCMeta

from case_engine.adapters.base import Adapter
from case_engine.adapters.models import (
    AdapterOperation,
    AdapterRequest,
    AdapterType,
)


class _ConcreteAdapter(Adapter):
    """Minimal concrete implementation for testing."""

    @property
    def adapter_name(self) -> str:
        return "test-adapter"

    @property
    def adapter_type(self) -> AdapterType:
        return AdapterType.FRESHDESK

    def supported_operations(self):
        return frozenset({AdapterOperation.READ, AdapterOperation.WRITE})

    def execute(self, request):
        raise NotImplementedError

    def health_check(self):
        return {"healthy": True}


class TestAdapterABC:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            Adapter()  # type: ignore[abstract]

    def test_concrete_can_be_instantiated(self):
        a = _ConcreteAdapter()
        assert a is not None

    def test_adapter_name_property(self):
        a = _ConcreteAdapter()
        assert a.adapter_name == "test-adapter"

    def test_adapter_type_property(self):
        a = _ConcreteAdapter()
        assert a.adapter_type == AdapterType.FRESHDESK

    def test_supports_returns_true_for_supported(self):
        a = _ConcreteAdapter()
        assert a.supports(AdapterOperation.READ)  is True
        assert a.supports(AdapterOperation.WRITE) is True

    def test_supports_returns_false_for_unsupported(self):
        a = _ConcreteAdapter()
        assert a.supports(AdapterOperation.EXECUTE) is False
        assert a.supports(AdapterOperation.CREATE)  is False
        assert a.supports(AdapterOperation.UPDATE)  is False
        assert a.supports(AdapterOperation.SEARCH)  is False

    def test_supported_operations_returns_frozenset(self):
        a = _ConcreteAdapter()
        ops = a.supported_operations()
        assert isinstance(ops, frozenset)

    def test_supported_operations_contains_expected(self):
        a = _ConcreteAdapter()
        ops = a.supported_operations()
        assert AdapterOperation.READ  in ops
        assert AdapterOperation.WRITE in ops

    def test_is_abc(self):
        assert isinstance(Adapter, ABCMeta)


class TestAdapterMissingAbstractMethods:
    def test_missing_adapter_name_raises(self):
        class Bad(Adapter):
            @property
            def adapter_type(self): return AdapterType.FRESHDESK
            def supported_operations(self): return frozenset()
            def execute(self, r): pass
            def health_check(self): return {}
        with pytest.raises(TypeError):
            Bad()

    def test_missing_execute_raises(self):
        class Bad(Adapter):
            @property
            def adapter_name(self): return "x"
            @property
            def adapter_type(self): return AdapterType.FRESHDESK
            def supported_operations(self): return frozenset()
            def health_check(self): return {}
        with pytest.raises(TypeError):
            Bad()

    def test_missing_health_check_raises(self):
        class Bad(Adapter):
            @property
            def adapter_name(self): return "x"
            @property
            def adapter_type(self): return AdapterType.FRESHDESK
            def supported_operations(self): return frozenset()
            def execute(self, r): pass
        with pytest.raises(TypeError):
            Bad()
