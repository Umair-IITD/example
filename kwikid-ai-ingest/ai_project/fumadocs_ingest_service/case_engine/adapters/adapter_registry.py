"""
case_engine/adapters/adapter_registry.py

Sprint 2.27: AdapterRegistry — thread-safe adapter catalog.

Responsibilities:
  register_adapter()   — add an adapter (duplicate type protection)
  get_adapter()        — look up adapter by AdapterType
  list_adapters()      — list all registered adapters
  health_summary()     — aggregate health status across all adapters

Design:
  - Thread-safe: threading.RLock on all mutations and reads
  - Never raises on get/list/health
  - Fail-closed: duplicate type registration is refused (raises on register only)
  - build_default() registers all 4 placeholder adapters
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from case_engine.adapters.base import Adapter
from case_engine.adapters.models import AdapterType

LOGGER = logging.getLogger(__name__)


class DuplicateAdapterError(Exception):
    """Raised when attempting to register an adapter for an already-registered type."""

    def __init__(self, adapter_type: AdapterType) -> None:
        self.adapter_type = adapter_type
        super().__init__(f"Adapter already registered for type: {adapter_type.value}")


class AdapterRegistry:
    """
    Thread-safe registry of external system adapters.

    Stores one adapter per AdapterType. Duplicate registrations are refused.
    All read operations (get, list, health) never raise.
    """

    def __init__(self) -> None:
        self._lock: threading.RLock = threading.RLock()
        self._adapters: dict[AdapterType, Adapter] = {}

    # ── Registration ──────────────────────────────────────────────────────────

    def register_adapter(self, adapter: Adapter) -> None:
        """
        Register an adapter.

        Raises DuplicateAdapterError if an adapter for this AdapterType is
        already registered. This is a programmer error — duplicate registration
        indicates a wiring bug and should fail fast at startup.
        """
        with self._lock:
            if adapter.adapter_type in self._adapters:
                raise DuplicateAdapterError(adapter.adapter_type)
            self._adapters[adapter.adapter_type] = adapter
            LOGGER.info(
                "adapter_registry: registered adapter=%s type=%s",
                adapter.adapter_name, adapter.adapter_type.value,
            )

    # ── Lookup ────────────────────────────────────────────────────────────────

    def get_adapter(self, adapter_type: AdapterType) -> Adapter | None:
        """
        Return the adapter for the given type, or None if not registered.

        Never raises.
        """
        try:
            with self._lock:
                return self._adapters.get(adapter_type)
        except Exception as exc:
            LOGGER.warning("adapter_registry.get_adapter error: %s", exc)
            return None

    # ── Listing ───────────────────────────────────────────────────────────────

    def list_adapters(self) -> list[Adapter]:
        """
        Return a list of all registered adapters.

        Never raises. Order is not guaranteed.
        """
        try:
            with self._lock:
                return list(self._adapters.values())
        except Exception as exc:
            LOGGER.warning("adapter_registry.list_adapters error: %s", exc)
            return []

    def list_adapter_types(self) -> list[AdapterType]:
        """Return a list of all registered AdapterType values. Never raises."""
        try:
            with self._lock:
                return list(self._adapters.keys())
        except Exception as exc:
            LOGGER.warning("adapter_registry.list_adapter_types error: %s", exc)
            return []

    def is_registered(self, adapter_type: AdapterType) -> bool:
        """Return True if an adapter is registered for this type. Never raises."""
        try:
            with self._lock:
                return adapter_type in self._adapters
        except Exception:
            return False

    def count(self) -> int:
        """Return the number of registered adapters. Never raises."""
        try:
            with self._lock:
                return len(self._adapters)
        except Exception:
            return 0

    # ── Health ────────────────────────────────────────────────────────────────

    def health_summary(self) -> dict[str, Any]:
        """
        Return aggregate health status for all registered adapters.

        Never raises. Adapter health_check() exceptions are caught per-adapter.
        """
        results: dict[str, Any] = {}
        overall_healthy = True
        adapters = self.list_adapters()

        for adapter in adapters:
            try:
                h = adapter.health_check()
                results[adapter.adapter_type.value] = h
                if not h.get("healthy", False):
                    overall_healthy = False
            except Exception as exc:
                results[adapter.adapter_type.value] = {
                    "healthy": False,
                    "adapter": adapter.adapter_name,
                    "error":   str(exc),
                }
                overall_healthy = False

        return {
            "overall_healthy":   overall_healthy,
            "adapter_count":     len(adapters),
            "adapters":          results,
        }

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def build_default(cls) -> "AdapterRegistry":
        """
        Build and return a registry with all 4 placeholder adapters registered.

        Never raises. Failed adapter builds are skipped with a WARNING log.
        """
        registry = cls()

        adapter_factories = [
            ("FreshdeskAdapter",    "case_engine.adapters.freshdesk_adapter"),
            ("AdminPortalAdapter",  "case_engine.adapters.portal_adapter"),
            ("AsanaAdapter",        "case_engine.adapters.asana_adapter"),
            ("MonitoringAdapter",   "case_engine.adapters.monitoring_adapter"),
        ]

        for cls_name, module_name in adapter_factories:
            try:
                import importlib
                mod    = importlib.import_module(module_name)
                klass  = getattr(mod, cls_name)
                adapter = klass()
                registry.register_adapter(adapter)
            except Exception as exc:
                LOGGER.warning(
                    "adapter_registry.build_default: failed to register %s error=%s",
                    cls_name, exc,
                )

        LOGGER.info(
            "adapter_registry.build_default: registered %d adapters",
            registry.count(),
        )
        return registry
