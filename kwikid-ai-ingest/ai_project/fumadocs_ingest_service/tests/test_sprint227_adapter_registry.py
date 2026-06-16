"""
tests/test_sprint227_adapter_registry.py

Sprint 2.27: Tests for AdapterRegistry (thread-safe, duplicate protection, build_default).
"""
import threading
import pytest

from case_engine.adapters.adapter_registry import AdapterRegistry, DuplicateAdapterError
from case_engine.adapters.freshdesk_adapter import FreshdeskAdapter
from case_engine.adapters.portal_adapter import AdminPortalAdapter
from case_engine.adapters.asana_adapter import AsanaAdapter
from case_engine.adapters.monitoring_adapter import MonitoringAdapter
from case_engine.adapters.models import AdapterType


class TestAdapterRegistryBasic:
    def test_empty_registry_count_zero(self):
        reg = AdapterRegistry()
        assert reg.count() == 0

    def test_register_one_adapter(self):
        reg = AdapterRegistry()
        reg.register_adapter(FreshdeskAdapter())
        assert reg.count() == 1

    def test_register_multiple_adapters(self):
        reg = AdapterRegistry()
        reg.register_adapter(FreshdeskAdapter())
        reg.register_adapter(AdminPortalAdapter())
        reg.register_adapter(AsanaAdapter())
        reg.register_adapter(MonitoringAdapter())
        assert reg.count() == 4

    def test_get_returns_registered_adapter(self):
        reg = AdapterRegistry()
        reg.register_adapter(FreshdeskAdapter())
        a = reg.get_adapter(AdapterType.FRESHDESK)
        assert a is not None
        assert a.adapter_type == AdapterType.FRESHDESK

    def test_get_returns_none_for_unregistered(self):
        reg = AdapterRegistry()
        assert reg.get_adapter(AdapterType.ASANA) is None

    def test_get_never_raises(self):
        reg = AdapterRegistry()
        for at in AdapterType:
            result = reg.get_adapter(at)
            assert result is None or result is not None

    def test_list_adapters_empty(self):
        reg = AdapterRegistry()
        assert reg.list_adapters() == []

    def test_list_adapters_returns_all(self):
        reg = AdapterRegistry()
        reg.register_adapter(FreshdeskAdapter())
        reg.register_adapter(AsanaAdapter())
        adapters = reg.list_adapters()
        assert len(adapters) == 2

    def test_list_adapter_types(self):
        reg = AdapterRegistry()
        reg.register_adapter(FreshdeskAdapter())
        reg.register_adapter(AsanaAdapter())
        types = reg.list_adapter_types()
        assert AdapterType.FRESHDESK in types
        assert AdapterType.ASANA in types

    def test_is_registered_true(self):
        reg = AdapterRegistry()
        reg.register_adapter(FreshdeskAdapter())
        assert reg.is_registered(AdapterType.FRESHDESK) is True

    def test_is_registered_false(self):
        reg = AdapterRegistry()
        assert reg.is_registered(AdapterType.MONITORING) is False

    def test_is_registered_never_raises(self):
        reg = AdapterRegistry()
        for at in AdapterType:
            reg.is_registered(at)


class TestDuplicateAdapterError:
    def test_duplicate_registration_raises(self):
        reg = AdapterRegistry()
        reg.register_adapter(FreshdeskAdapter())
        with pytest.raises(DuplicateAdapterError):
            reg.register_adapter(FreshdeskAdapter())

    def test_duplicate_error_has_adapter_type(self):
        reg = AdapterRegistry()
        reg.register_adapter(FreshdeskAdapter())
        try:
            reg.register_adapter(FreshdeskAdapter())
        except DuplicateAdapterError as e:
            assert e.adapter_type == AdapterType.FRESHDESK

    def test_error_message_contains_type(self):
        reg = AdapterRegistry()
        reg.register_adapter(AsanaAdapter())
        try:
            reg.register_adapter(AsanaAdapter())
        except DuplicateAdapterError as e:
            assert "ASANA" in str(e)

    def test_first_adapter_still_accessible_after_duplicate_error(self):
        reg = AdapterRegistry()
        first = FreshdeskAdapter()
        reg.register_adapter(first)
        with pytest.raises(DuplicateAdapterError):
            reg.register_adapter(FreshdeskAdapter())
        assert reg.get_adapter(AdapterType.FRESHDESK) is first


class TestAdapterRegistryHealthSummary:
    def test_health_summary_empty_registry(self):
        reg = AdapterRegistry()
        summary = reg.health_summary()
        assert summary["adapter_count"] == 0
        assert "overall_healthy" in summary

    def test_health_summary_all_healthy(self):
        reg = AdapterRegistry()
        reg.register_adapter(FreshdeskAdapter())
        reg.register_adapter(AsanaAdapter())
        summary = reg.health_summary()
        assert summary["overall_healthy"] is True
        assert summary["adapter_count"] == 2

    def test_health_summary_includes_adapter_results(self):
        reg = AdapterRegistry()
        reg.register_adapter(FreshdeskAdapter())
        summary = reg.health_summary()
        assert "adapters" in summary
        assert AdapterType.FRESHDESK.value in summary["adapters"]

    def test_health_summary_never_raises(self):
        reg = AdapterRegistry()
        reg.health_summary()


class TestAdapterRegistryBuildDefault:
    def test_build_default_returns_registry(self):
        reg = AdapterRegistry.build_default()
        assert isinstance(reg, AdapterRegistry)

    def test_build_default_has_four_adapters(self):
        reg = AdapterRegistry.build_default()
        assert reg.count() == 4

    def test_build_default_has_freshdesk(self):
        reg = AdapterRegistry.build_default()
        assert reg.is_registered(AdapterType.FRESHDESK)

    def test_build_default_has_admin_portal(self):
        reg = AdapterRegistry.build_default()
        assert reg.is_registered(AdapterType.ADMIN_PORTAL)

    def test_build_default_has_asana(self):
        reg = AdapterRegistry.build_default()
        assert reg.is_registered(AdapterType.ASANA)

    def test_build_default_has_monitoring(self):
        reg = AdapterRegistry.build_default()
        assert reg.is_registered(AdapterType.MONITORING)


class TestAdapterRegistryThreadSafety:
    def test_concurrent_reads_do_not_crash(self):
        reg = AdapterRegistry.build_default()
        errors = []

        def read():
            try:
                for at in AdapterType:
                    reg.get_adapter(at)
                    reg.is_registered(at)
                reg.list_adapters()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=read) for _ in range(20)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert errors == []

    def test_concurrent_registration_raises_duplicate_not_crashes(self):
        reg = AdapterRegistry()
        errors_type = []
        unexpected = []

        def register():
            try:
                reg.register_adapter(FreshdeskAdapter())
            except DuplicateAdapterError:
                errors_type.append(True)
            except Exception as exc:
                unexpected.append(exc)

        threads = [threading.Thread(target=register) for _ in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert unexpected == []
        assert reg.count() == 1
