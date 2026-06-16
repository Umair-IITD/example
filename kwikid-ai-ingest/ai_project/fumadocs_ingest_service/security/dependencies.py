"""
security/dependencies.py

Sprint 2.8: FastAPI dependency injection for RBAC authorization.

Usage in route handlers:
    from fastapi import Depends
    from security.dependencies import require_approver, require_operator, require_admin
    from security.auth import AuthContext

    @router.post("/actions/{action_id}/approve")
    async def approve_action(
        action_id: str,
        request: Request,
        auth: AuthContext = Depends(require_approver),
    ) -> JSONResponse:
        # auth.identity and auth.role are available for audit logging
        ...

Authentication:
  Reads X-API-Key header. Missing/invalid key → 401 UNAUTHORIZED.
  Key found but wrong role → 403 FORBIDDEN.

Auth disabled:
  When AUTH_ENABLED=false, all require_* functions return an anonymous
  admin context without inspecting the header. This mode is for local
  development ONLY.

Error responses follow the canonical envelope:
  {"error": {"code": "UNAUTHORIZED", "message": "..."}}
  {"error": {"code": "FORBIDDEN",    "message": "..."}}

Security:
  The raw API key is NEVER logged or included in error responses.
  Error messages are generic enough to prevent oracle attacks.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader

from api.error_models import error_body
from security.auth import AuthContext
from security.roles import Permission, Role

_api_key_scheme = APIKeyHeader(name="x-api-key", auto_error=False)


def _get_authenticator(request: Request):
    """Extract the ApiKeyAuthenticator from app.state."""
    try:
        return request.app.state.authenticator
    except AttributeError:
        raise HTTPException(
            status_code=503,
            detail=error_body("SERVICE_UNAVAILABLE", "Auth subsystem not initialised"),
        )


def _authenticate_and_authorize(
    request: Request,
    api_key: str | None,
    required_permission: Permission,
) -> AuthContext:
    """
    Shared auth+authz logic — used by all require_* dependencies.

    Raises HTTPException 401 on authentication failure.
    Raises HTTPException 403 on authorization failure.
    Returns AuthContext on success.
    """
    authenticator = _get_authenticator(request)
    result = authenticator.authenticate(api_key)

    if not result.authenticated:
        raise HTTPException(
            status_code=401,
            detail=error_body("UNAUTHORIZED", "Invalid or missing API key"),
        )

    if not result.has_permission(required_permission):
        raise HTTPException(
            status_code=403,
            detail=error_body(
                "FORBIDDEN",
                f"Role '{result.role.value if result.role else 'none'}' "
                f"does not have permission for this operation",
            ),
        )

    return AuthContext(identity=result.identity, role=result.role)


def require_approver(
    request: Request,
    api_key: str | None = Depends(_api_key_scheme),
) -> AuthContext:
    """
    Dependency: caller must have APPROVE_ACTION permission (APPROVER or ADMIN role).
    """
    return _authenticate_and_authorize(request, api_key, Permission.APPROVE_ACTION)


def require_operator(
    request: Request,
    api_key: str | None = Depends(_api_key_scheme),
) -> AuthContext:
    """
    Dependency: caller must have WORKER_TICK permission (OPERATOR or ADMIN role).
    """
    return _authenticate_and_authorize(request, api_key, Permission.WORKER_TICK)


def require_watchdog(
    request: Request,
    api_key: str | None = Depends(_api_key_scheme),
) -> AuthContext:
    """
    Dependency: caller must have WATCHDOG_RUN permission (OPERATOR or ADMIN role).
    """
    return _authenticate_and_authorize(request, api_key, Permission.WATCHDOG_RUN)


def require_admin(
    request: Request,
    api_key: str | None = Depends(_api_key_scheme),
) -> AuthContext:
    """
    Dependency: caller must be ADMIN (has all permissions).
    Checked via WORKER_TICK + APPROVE_ACTION — only ADMIN has both.
    """
    authenticator = _get_authenticator(request)
    result = authenticator.authenticate(api_key)

    if not result.authenticated:
        raise HTTPException(
            status_code=401,
            detail=error_body("UNAUTHORIZED", "Invalid or missing API key"),
        )

    if result.role != Role.ADMIN:
        raise HTTPException(
            status_code=403,
            detail=error_body("FORBIDDEN", "Admin role required for this operation"),
        )

    return AuthContext(identity=result.identity, role=result.role)
