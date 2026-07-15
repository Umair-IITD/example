"""
case_engine/knowledge/sop/loader.py

Sprint 2.41: SOPLoader — idempotent SOP loading and resolver construction.

Responsibilities:
  - Load the default SOP library into a SOPDocumentRepository.
  - Build a SOPDocumentResolver backed by the loaded repository.
  - Idempotent: calling load_defaults() twice skips duplicates silently.

Mirrors WorkflowPlaybookLoader design from Sprint 2.40.
"""
from __future__ import annotations

import logging
from typing import Any

from case_engine.knowledge.sop.defaults import build_defaults, get_minimal_sop
from case_engine.knowledge.sop.exceptions import DuplicateSOPError
from case_engine.knowledge.sop.repository import SOPDocumentRepository
from case_engine.knowledge.sop.resolver import SOPDocumentResolver
from case_engine.knowledge.sop.validators import SOPValidator

LOGGER = logging.getLogger(__name__)


class SOPLoader:
    """
    Loads SOPDocument objects into a SOPDocumentRepository and builds resolvers.

    Constructor:
        repository:  SOPDocumentRepository to load into. If None, a new one is created.
        validator:   SOPValidator for the repository. Defaults to SOPValidator().

    Usage:
        loader = SOPLoader()
        loaded = loader.load_defaults()    # → 9
        resolver = loader.build_resolver() # → SOPDocumentResolver
    """

    def __init__(
        self,
        repository: SOPDocumentRepository | None = None,
        validator: SOPValidator | None = None,
    ) -> None:
        self._validator = validator or SOPValidator()
        self._repo = repository if repository is not None else SOPDocumentRepository(self._validator)

    # ── Public API ─────────────────────────────────────────────────────────────

    def load_defaults(self) -> int:
        """
        Register all default SOPs into the repository.

        Idempotent — DuplicateSOPError is swallowed silently.
        Returns the number of SOPs successfully registered (0 on second call).
        """
        sops = build_defaults()
        loaded = 0
        for sop in sops:
            try:
                self._repo.register(sop)
                loaded += 1
                LOGGER.debug("sop_loader.loaded sop_id=%s version=%s", sop.sop_id, sop.version)
            except DuplicateSOPError:
                LOGGER.debug("sop_loader.skip_duplicate sop_id=%s version=%s", sop.sop_id, sop.version)
        LOGGER.info("sop_loader.load_defaults loaded=%d total_in_repo=%d", loaded, len(self._repo))
        return loaded

    def build_resolver(self) -> SOPDocumentResolver:
        """
        Return a SOPDocumentResolver backed by this loader's repository.

        The fallback SOP is MINIMAL_INVESTIGATION (single READ step).
        Call load_defaults() before build_resolver() to ensure the repository
        is populated; build_resolver() does NOT implicitly load defaults.
        """
        fallback = get_minimal_sop()
        return SOPDocumentResolver(
            repository=self._repo,
            fallback_sop=fallback,
        )

    def load_and_build(self) -> SOPDocumentResolver:
        """
        Convenience: load_defaults() then build_resolver() in one call.

        Returns a fully-initialised SOPDocumentResolver.
        """
        self.load_defaults()
        return self.build_resolver()

    @property
    def repository(self) -> SOPDocumentRepository:
        """The underlying SOPDocumentRepository."""
        return self._repo
