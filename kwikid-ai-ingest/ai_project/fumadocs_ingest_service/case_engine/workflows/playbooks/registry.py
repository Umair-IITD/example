"""
case_engine/workflows/playbooks/registry.py

Sprint 2.40: Singleton playbook registry — default-loaded at application startup.

Usage:
    registry = get_default_registry()
    playbook = registry.resolver.resolve("VKYC_SESSION_FAILURE", client_id="unity")
    stats    = registry.repository.statistics()

Testing:
    reset_registry()   # wipe singleton so tests get a fresh instance
"""
from __future__ import annotations

import logging
import threading

from case_engine.workflows.playbooks.loader import PlaybookLoader
from case_engine.workflows.playbooks.repository import WorkflowPlaybookRepository
from case_engine.workflows.playbooks.resolver import WorkflowPlaybookResolver

LOGGER = logging.getLogger(__name__)

_lock:     threading.Lock         = threading.Lock()
_registry: _PlaybookRegistry | None = None


class _PlaybookRegistry:
    """
    Fully initialised, default-populated playbook registry.

    Built once on first call to get_default_registry(); subsequent calls
    return the same instance. Thread-safe via double-checked locking.
    """

    def __init__(self) -> None:
        loader = PlaybookLoader()
        count  = loader.load_defaults()
        self._repository = loader.repository
        self._resolver   = loader.build_resolver()
        LOGGER.info("playbook_registry.initialised count=%d", count)

    @property
    def repository(self) -> WorkflowPlaybookRepository:
        """All registered playbooks."""
        return self._repository

    @property
    def resolver(self) -> WorkflowPlaybookResolver:
        """Priority-based resolver — never returns None."""
        return self._resolver


def get_default_registry() -> _PlaybookRegistry:
    """
    Return the application-wide default PlaybookRegistry.

    Thread-safe; initialised once on first call.
    """
    global _registry
    if _registry is None:
        with _lock:
            if _registry is None:
                _registry = _PlaybookRegistry()
    return _registry


def reset_registry() -> None:
    """
    Destroy the singleton so the next call to get_default_registry() creates
    a fresh instance. Intended for tests only.
    """
    global _registry
    with _lock:
        _registry = None
    LOGGER.debug("playbook_registry.reset")
