"""
api/error_models.py

Canonical API error envelope: {"error": {"code": "...", "message": "..."}}.

All route handlers use http_error() to return consistent error responses.
Raw exceptions and stack traces must NEVER reach the HTTP response body.
"""
from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import JSONResponse


def error_body(code: str, message: str) -> dict:
    """Return the standard error envelope dict."""
    return {"error": {"code": code, "message": message}}


def http_error(status_code: int, code: str, message: str) -> HTTPException:
    """Raise a FastAPI HTTPException with the standard error envelope."""
    raise HTTPException(
        status_code=status_code,
        detail=error_body(code, message),
    )
