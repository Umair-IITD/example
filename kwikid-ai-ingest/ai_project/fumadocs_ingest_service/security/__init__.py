"""
security — Sprint 2.8 API authentication and authorization layer.

Public API:
    ApiKeyAuthenticator   — validates API keys (constant-time)
    AuthResult            — authentication outcome
    AuthContext           — rich context after successful auth+authz
    Role                  — APPROVER / OPERATOR / ADMIN
    Permission            — fine-grained permission tokens
    require_approver      — FastAPI dependency
    require_operator      — FastAPI dependency
    require_watchdog      — FastAPI dependency
    require_admin         — FastAPI dependency
    build_authenticator_from_env — factory
    validate_startup_config      — fail-fast config checker
    ConfigurationValidationError — raised on misconfiguration
"""
from security.auth import ApiKeyAuthenticator, AuthContext, AuthResult
from security.config import build_authenticator_from_env
from security.config_validator import ConfigurationValidationError, validate_startup_config
from security.dependencies import require_admin, require_approver, require_operator, require_watchdog
from security.roles import Permission, Role

__all__ = [
    "ApiKeyAuthenticator",
    "AuthResult",
    "AuthContext",
    "Role",
    "Permission",
    "require_approver",
    "require_operator",
    "require_watchdog",
    "require_admin",
    "build_authenticator_from_env",
    "validate_startup_config",
    "ConfigurationValidationError",
]
