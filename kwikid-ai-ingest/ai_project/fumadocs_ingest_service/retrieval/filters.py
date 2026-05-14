"""
retrieval/filters.py
---------------------
Centralized metadata filtering utilities shared across retrieval paths.
Keeps filter logic in one place and out of search-specific modules.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


def parse_dt(value: object) -> datetime | None:
    """Parse an ISO-8601 datetime string, returning None on failure."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def build_metadata_filter_set(
    source_types: list[str] | None = None,
    tenant: str | None = None,
    access_scope: str | None = None,
    index_version: str | None = None,
    updated_at_from: str | None = None,
    updated_at_to: str | None = None,
) -> dict[str, Any]:
    """
    Build a normalized filter dict for passing between retrieval stages.
    All values are kept as-is; None means "no filter".
    """
    return {
        "source_types": source_types,
        "tenant": tenant,
        "access_scope": access_scope,
        "index_version": index_version,
        "updated_at_from": updated_at_from,
        "updated_at_to": updated_at_to,
    }


def matches_metadata_filters(
    metadata: dict[str, Any],
    filters: dict[str, Any],
) -> bool:
    """
    Return True if a document's metadata satisfies all active filters.
    A None filter value is treated as "no constraint" (pass-through).
    """
    source_types = filters.get("source_types")
    tenant = filters.get("tenant")
    access_scope = filters.get("access_scope")
    index_version = filters.get("index_version")
    updated_at_from = filters.get("updated_at_from")
    updated_at_to = filters.get("updated_at_to")

    # Source type filter
    if source_types is not None:
        doc_source = (metadata.get("source_type") or "").lower()
        if doc_source not in {s.lower() for s in source_types}:
            return False

    # Exact match filters
    if tenant is not None and metadata.get("tenant") != tenant:
        return False
    if access_scope is not None and metadata.get("access_scope") != access_scope:
        return False
    if index_version is not None and metadata.get("index_version") != index_version:
        return False

    # Date range filter
    dt_from = parse_dt(updated_at_from)
    dt_to = parse_dt(updated_at_to)
    if dt_from or dt_to:
        row_dt = parse_dt(metadata.get("updated_at"))
        if row_dt is None:
            return False
        if dt_from and row_dt < dt_from:
            return False
        if dt_to and row_dt > dt_to:
            return False

    return True


def apply_metadata_filters(
    rows: list[dict[str, Any]],
    filters: dict[str, Any],
) -> list[dict[str, Any]]:
    """Filter a list of row dicts based on metadata constraints."""
    if not any(v is not None for v in filters.values()):
        return rows
    return [r for r in rows if matches_metadata_filters(r.get("metadata") or {}, filters)]
