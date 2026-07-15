"""
case_engine/workflows/playbooks/loader.py

Sprint 2.40: PlaybookLoader — loads default playbooks into a repository
and builds a fully-configured WorkflowPlaybookResolver.

Usage:
    loader = PlaybookLoader()
    count  = loader.load_defaults()      # populate repository
    resolver = loader.build_resolver()   # ready to use
"""
from __future__ import annotations

import logging

from case_engine.workflows.playbooks.defaults import build_defaults, get_minimal_playbook
from case_engine.workflows.playbooks.exceptions import DuplicatePlaybookError
from case_engine.workflows.playbooks.repository import WorkflowPlaybookRepository
from case_engine.workflows.playbooks.resolver import WorkflowPlaybookResolver
from case_engine.workflows.playbooks.validators import PlaybookValidator

LOGGER = logging.getLogger(__name__)


class PlaybookLoader:
    """
    Loads default WorkflowPlaybooks into a repository and builds a resolver.

    The loader is idempotent: loading defaults twice does not raise; it logs
    and skips duplicate (playbook_id, version) pairs.

    Constructor:
        repository: Pre-existing repository to load into. If None, creates one.
        validator:  Validator instance. If None, uses PlaybookValidator defaults.
    """

    def __init__(
        self,
        repository: WorkflowPlaybookRepository | None = None,
        validator:  PlaybookValidator | None = None,
    ) -> None:
        self._repo      = repository if repository is not None else WorkflowPlaybookRepository(validator)
        self._validator = validator or PlaybookValidator()

    # ── Public API ─────────────────────────────────────────────────────────────

    def load_defaults(self) -> int:
        """
        Register all nine default playbooks into the repository.

        Returns:
            Number of playbooks successfully registered (skips duplicates).
        """
        defaults = build_defaults()
        count = 0
        for pb in defaults:
            try:
                self._repo.register(pb)
                count += 1
            except DuplicatePlaybookError:
                LOGGER.debug(
                    "playbook_loader.skip_duplicate playbook_id=%s version=%s",
                    pb.playbook_id, pb.version,
                )
        LOGGER.info("playbook_loader.loaded count=%d total_registered=%d",
                    count, len(self._repo))
        return count

    def build_resolver(self) -> WorkflowPlaybookResolver:
        """
        Build a WorkflowPlaybookResolver backed by this repository.

        The minimal fallback playbook is constructed fresh so the resolver
        always has a guaranteed non-None fallback even if the registry is empty.
        """
        minimal = get_minimal_playbook()
        return WorkflowPlaybookResolver(self._repo, minimal)

    # ── Properties ─────────────────────────────────────────────────────────────

    @property
    def repository(self) -> WorkflowPlaybookRepository:
        """The underlying repository (possibly populated with defaults)."""
        return self._repo
