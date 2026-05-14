"""
rag_engine/database/supabase_client.py

Supabase client factory for Phase B1.
Reads SUPABASE_URL and SUPABASE_KEY from existing app config to avoid
duplicating environment variables.
"""
from __future__ import annotations

import os

from supabase import Client, create_client


def build_supabase_client(
    supabase_url: str | None = None,
    supabase_key: str | None = None,
) -> Client:
    """
    Build a Supabase client.
    Falls back to SUPABASE_URL / SUPABASE_KEY env vars if not provided directly.
    Uses the service-role key (bypasses RLS) for ingestion operations.
    """
    url = supabase_url or os.environ.get("SUPABASE_URL", "")
    key = supabase_key or os.environ.get("SUPABASE_KEY", "")

    if not url:
        raise ValueError(
            "SUPABASE_URL is required. Set it in .env or pass as argument."
        )
    if not key:
        raise ValueError(
            "SUPABASE_KEY is required. Set it in .env or pass as argument. "
            "Use the service-role key for ingestion."
        )

    return create_client(url, key)


def build_supabase_client_from_settings() -> Client:
    """
    Build Supabase client using the existing app.config.get_settings().
    Avoids duplicating .env reads between app and rag_engine.
    """
    from app.config import get_settings
    settings = get_settings()
    return build_supabase_client(
        supabase_url=settings.supabase_url,
        supabase_key=settings.supabase_key,
    )
