"""
security/auth.py

Sprint 2.8: API Key Authentication.

ApiKeyAuthenticator:
  - Validates API keys using hmac.compare_digest() (constant-time, mandatory)
  - Maps keys → (identity, Role) at construction time
  - Never exposes raw key values in logs or exceptions
  - Supports disabled mode for local development (AUTH_ENABLED=false)

AuthResult:
  - Immutable result of an authentication attempt
  - authenticated=False means the caller must be rejected with HTTP 401

AuthContext:
  - Rich context passed into route handlers after successful auth+authz
  - Carries identity and role for audit logging

Security invariant:
  Keys are stored as SHA-256 digests internally and compared with
  hmac.compare_digest() to prevent timing attacks. Even key length
  is obscured by digesting both sides before comparison.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass

from security.roles import Permission, Role, has_permission

LOGGER = logging.getLogger(__name__)


def _digest(key: str) -> bytes:
    """Return SHA-256 digest of key bytes — used for constant-time comparison."""
    return hashlib.sha256(key.encode("utf-8")).digest()


@dataclass(frozen=True)
class AuthResult:
    """
    Result of an authentication attempt.

    authenticated=False: caller must be rejected (HTTP 401).
    identity: actor label used in audit logs (never the raw key).
    role: assigned role; None when authenticated=False.
    """
    authenticated: bool
    identity: str = "anonymous"
    role: Role | None = None

    def has_permission(self, permission: Permission) -> bool:
        if not self.authenticated or self.role is None:
            return False
        return has_permission(self.role, permission)

    @classmethod
    def anonymous(cls) -> "AuthResult":
        """Unauthenticated result — for auth-disabled mode."""
        return cls(authenticated=True, identity="anonymous", role=Role.ADMIN)

    @classmethod
    def denied(cls) -> "AuthResult":
        return cls(authenticated=False)


@dataclass(frozen=True)
class AuthContext:
    """
    Rich auth context injected into route handlers after successful auth+authz.

    identity: actor name from API key registry (e.g. "compliance", "scheduler").
    role: the role granted by the API key.
    """
    identity: str
    role: Role

    def has_permission(self, permission: Permission) -> bool:
        return has_permission(self.role, permission)


class ApiKeyAuthenticator:
    """
    Validates inbound API keys against a configured registry.

    Construction:
      key_registry: {key_value: (identity, Role)} — built from env vars.
      auth_enabled: when False, every call returns AuthResult.anonymous().

    Key comparison:
      hmac.compare_digest(digest(stored), digest(provided))
      Both sides are SHA-256 digested before comparison so that:
        1. Raw keys never appear in memory comparisons.
        2. Length differences are eliminated (all digests are 32 bytes).
        3. Constant-time guarantee holds regardless of key length.

    Secrets in logs:
      The raw key is NEVER logged. Only the identity label is used.
    """

    def __init__(
        self,
        key_registry: dict[str, tuple[str, Role]],
        *,
        auth_enabled: bool = True,
    ) -> None:
        # Pre-compute digests at construction time — not on every request
        self._digests: list[tuple[bytes, str, Role]] = [
            (_digest(key), identity, role)
            for key, (identity, role) in key_registry.items()
        ]
        self._auth_enabled = auth_enabled
        self._key_count = len(self._digests)

    @property
    def auth_enabled(self) -> bool:
        return self._auth_enabled

    @property
    def key_count(self) -> int:
        return self._key_count

    def authenticate(self, raw_key: str | None) -> AuthResult:
        """
        Authenticate a raw API key value from the HTTP request.

        When auth_enabled=False: returns anonymous admin context immediately.
        When raw_key is None/empty: returns denied.
        Uses constant-time comparison (hmac.compare_digest) after SHA-256 digest.

        Never raises. All exceptions are caught and returned as denied.
        """
        try:
            if not self._auth_enabled:
                return AuthResult.anonymous()

            if not raw_key:
                return AuthResult.denied()

            provided_digest = _digest(raw_key)

            for stored_digest, identity, role in self._digests:
                if hmac.compare_digest(stored_digest, provided_digest):
                    LOGGER.debug("auth: authenticated identity=%s role=%s", identity, role.value)
                    return AuthResult(authenticated=True, identity=identity, role=role)

            LOGGER.warning("auth: authentication failed — no matching key found")
            return AuthResult.denied()

        except Exception as exc:
            LOGGER.error("auth: unexpected error during authentication: %s", type(exc).__name__)
            return AuthResult.denied()
