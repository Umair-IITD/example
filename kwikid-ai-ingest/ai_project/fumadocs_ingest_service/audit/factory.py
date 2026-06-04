"""
audit/factory.py

Sprint 2.10: AuditRepository factory — backend selection via environment.

build_audit_repository() reads AUDIT_BACKEND to select the storage backend:

  inmemory  — InMemoryAuditRepository (default, safe for dev/test)
  supabase  — SupabaseAuditRepository (requires supabase_client)

Future backends (postgres, s3) can be added here without changing any
router, service, or runtime code. The AuditRepository ABC ensures all
backends satisfy the same contract.

No route changes required when backend changes — the AuditService and
API routes interact only with the AuditRepository interface.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from audit.repository import AuditRepository, InMemoryAuditRepository

LOGGER = logging.getLogger(__name__)

_VALID_BACKENDS = ("inmemory", "supabase")


def build_audit_repository(
    supabase_client: Any = None,
    *,
    backend_override: str | None = None,
) -> AuditRepository:
    """
    Build and return an AuditRepository for the configured backend.

    Args:
        supabase_client: Required when AUDIT_BACKEND=supabase.
                         Ignored for inmemory backend.
        backend_override: Override AUDIT_BACKEND env var (useful in tests).

    Returns:
        Configured AuditRepository implementation.

    Raises:
        ValueError: backend is not recognised or supabase_client missing for supabase.
    """
    backend = (backend_override or os.environ.get("AUDIT_BACKEND", "inmemory")).strip().lower()

    if backend not in _VALID_BACKENDS:
        raise ValueError(
            f"AUDIT_BACKEND={backend!r} is not supported. "
            f"Valid values: {', '.join(_VALID_BACKENDS)}"
        )

    if backend == "supabase":
        if supabase_client is None:
            raise ValueError(
                "AUDIT_BACKEND=supabase requires a supabase_client. "
                "Ensure SUPABASE_URL and SUPABASE_KEY are set and "
                "the supabase_client is passed to build_audit_repository()."
            )
        from audit.repository_supabase import SupabaseAuditRepository
        repo = SupabaseAuditRepository(supabase_client)
        LOGGER.info("audit.factory: using SupabaseAuditRepository")
        return repo

    # Default: inmemory
    LOGGER.info("audit.factory: using InMemoryAuditRepository (backend=%s)", backend)
    return InMemoryAuditRepository()
