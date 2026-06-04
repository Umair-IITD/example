"""
api — Sprint 2.7 FastAPI application package.

Intentionally empty: importing api.middleware.request_id from app.main
would trigger a circular import if this __init__ imported from api.app,
which re-exports from app.main.

Chain that broke production startup:
  app.main → api.middleware.request_id → api (package) → api.app → app.main

All callers use explicit submodule imports:
  from api.app import create_app
  from api.middleware.request_id import RequestIdMiddleware
  from api.health_providers import ConfigHealthProvider
"""
