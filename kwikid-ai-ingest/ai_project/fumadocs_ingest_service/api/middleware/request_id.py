"""
api/middleware/request_id.py

Sprint 2.11 B5: Request correlation ID middleware.

Behaviour:
  1. Reads X-Request-ID header from the incoming request.
  2. If absent or empty: generates a new UUID4 as the request_id.
  3. Injects request_id into the structured logging context (via observability.structured_logger).
  4. Adds X-Request-ID to the response headers so clients can correlate logs.

All log records emitted during request handling automatically include the
request_id field via the context variable set in structured_logger.

Usage (wired in api/app.py):
    from api.middleware.request_id import RequestIdMiddleware
    app.add_middleware(RequestIdMiddleware)

Header name: X-Request-ID (case-insensitive via Starlette)
"""
from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from observability.structured_logger import set_request_id

_HEADER_NAME = "x-request-id"
_RESPONSE_HEADER = "X-Request-ID"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """
    Starlette middleware that assigns a correlation ID to every request.

    The ID is read from X-Request-ID (if provided by the caller) or generated
    fresh as a UUID4. It is stored in the structured logger context so all
    log records during the request include it automatically.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(_HEADER_NAME, "").strip()
        if not request_id:
            request_id = str(uuid.uuid4())

        # Inject into logging context
        set_request_id(request_id)

        # Make request_id available on request.state for route handlers
        request.state.request_id = request_id

        response = await call_next(request)

        # Propagate request_id in response header
        response.headers[_RESPONSE_HEADER] = request_id
        return response
