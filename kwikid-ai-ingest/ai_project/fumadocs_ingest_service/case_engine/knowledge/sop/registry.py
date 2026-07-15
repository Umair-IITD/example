"""
case_engine/knowledge/sop/registry.py

Sprint 2.41: Module-level singleton for the default SOPDocumentRepository.

Provides:
  get_default_sop_registry()  — returns the singleton, loading defaults on first call.
  reset_sop_registry()        — resets singleton (test use only).
  get_default_sop_resolver()  — convenience: resolver backed by singleton registry.

Thread safety: double-checked locking pattern with a module-level lock.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from case_engine.knowledge.sop.loader import SOPLoader
from case_engine.knowledge.sop.repository import SOPDocumentRepository
from case_engine.knowledge.sop.resolver import SOPDocumentResolver

LOGGER = logging.getLogger(__name__)

_lock: threading.RLock = threading.RLock()  # reentrant: resolver calls registry inside same lock
_registry: SOPDocumentRepository | None = None
_resolver: SOPDocumentResolver | None = None


def get_default_sop_registry() -> SOPDocumentRepository:
    """
    Return the module-level singleton SOPDocumentRepository.

    On first call, loads all 9 default SOPs. Subsequent calls return the
    same repository without reloading. Thread-safe via double-checked locking.
    """
    global _registry
    if _registry is None:
        with _lock:
            if _registry is None:
                loader = SOPLoader()
                loader.load_defaults()
                _registry = loader.repository
                LOGGER.info(
                    "sop_registry.initialized total_sops=%d", len(_registry)
                )
    return _registry


def get_default_sop_resolver() -> SOPDocumentResolver:
    """
    Return the module-level singleton SOPDocumentResolver.

    Backed by get_default_sop_registry(). Thread-safe.
    """
    global _resolver
    if _resolver is None:
        with _lock:
            if _resolver is None:
                registry = get_default_sop_registry()
                loader = SOPLoader(repository=registry)
                _resolver = loader.build_resolver()
                LOGGER.info("sop_registry.resolver_initialized")
    return _resolver


def reset_sop_registry() -> None:
    """
    Reset the singleton registry and resolver to None.

    Intended for test isolation ONLY. Not safe to call in production
    while requests are being served.
    """
    global _registry, _resolver
    with _lock:
        _registry = None
        _resolver = None
    LOGGER.debug("sop_registry.reset")
