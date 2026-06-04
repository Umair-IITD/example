"""
api/app.py

Sprint 2.11.2: Thin wrapper — delegates to app.main.create_app().

After consolidation, app/main.py is the single production FastAPI application
and the authoritative create_app() factory. This module re-exports create_app()
so that all existing test files (test_sprint27_api.py through test_sprint211_*.py)
that import ``from api.app import create_app`` continue to work without changes.

The full application factory, route registration, middleware stack, and lifespan
are defined in app/main.py. This file contains no application logic.
"""
from app.main import create_app  # noqa: F401 — re-export for test compatibility

__all__ = ["create_app"]
