"""
security/config.py

Sprint 2.8: Parse authentication configuration from environment variables.

Environment variable format:
  AUTH_ENABLED=true

  APPROVER_API_KEYS=alice:key_value_1
  compliance:key_value_2

  OPERATOR_API_KEYS=scheduler:key_value_3
  worker-1:key_value_4

  ADMIN_API_KEYS=admin:key_value_5

Format rules:
  - One entry per line: <identity_name>:<api_key_value>
  - identity_name: label used in audit logs (never the key itself)
  - api_key_value: the actual secret to be transmitted in X-API-Key header
  - Lines without ':' are silently ignored
  - Blank lines are silently ignored
  - Keys must be unique — first occurrence wins on duplicate

Security note:
  This module reads raw key values from env vars. They are passed to
  ApiKeyAuthenticator which digests them at construction and never stores
  the raw values beyond this function's scope.
"""
from __future__ import annotations

import logging
import os

from security.auth import ApiKeyAuthenticator
from security.roles import Role

LOGGER = logging.getLogger(__name__)


def _parse_key_lines(raw: str, role: Role) -> dict[str, tuple[str, Role]]:
    """
    Parse newline-delimited 'identity:key' pairs into a registry dict.

    Returns {key_value: (identity, role)}.
    """
    registry: dict[str, tuple[str, Role]] = {}
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        identity, _, key = line.partition(":")
        identity = identity.strip()
        key = key.strip()
        if not identity or not key:
            continue
        if key in registry:
            LOGGER.warning("auth.config: duplicate key for identity=%s — first entry wins", identity)
            continue
        registry[key] = (identity, role)
    return registry


def build_authenticator_from_env() -> ApiKeyAuthenticator:
    """
    Build an ApiKeyAuthenticator from environment variables.

    Reads:
      AUTH_ENABLED           — "true" enables auth (default: false for safety)
      APPROVER_API_KEYS      — newline-delimited identity:key pairs for APPROVER role
      OPERATOR_API_KEYS      — newline-delimited identity:key pairs for OPERATOR role
      ADMIN_API_KEYS         — newline-delimited identity:key pairs for ADMIN role

    Returns ApiKeyAuthenticator ready for injection into app.state.
    """
    raw_enabled = os.environ.get("AUTH_ENABLED", "false").strip().lower()
    auth_enabled = raw_enabled in ("true", "1", "yes")

    registry: dict[str, tuple[str, Role]] = {}

    for env_var, role in (
        ("APPROVER_API_KEYS", Role.APPROVER),
        ("OPERATOR_API_KEYS", Role.OPERATOR),
        ("ADMIN_API_KEYS",    Role.ADMIN),
    ):
        raw = os.environ.get(env_var, "")
        parsed = _parse_key_lines(raw, role)
        for key, (identity, r) in parsed.items():
            if key in registry:
                LOGGER.warning(
                    "auth.config: key for identity=%s appears in multiple role env vars — "
                    "first role wins", identity,
                )
                continue
            registry[key] = (identity, r)

    LOGGER.info(
        "auth.config: auth_enabled=%s total_keys=%d", auth_enabled, len(registry)
    )
    return ApiKeyAuthenticator(registry, auth_enabled=auth_enabled)
