"""
security/config_validator.py

Sprint 2.8: Startup configuration validation.
Sprint 2.10: Added AUDIT_BACKEND validation + env consistency check.

validate_startup_config() is called during application lifespan startup.
It raises ConfigurationValidationError on any misconfiguration that would
result in a security gap or non-functional system.

Fail-fast principle: better to crash at startup with a clear error than
to serve requests with broken security invariants.

Checks:
  1. AUTH_ENABLED=true but no API keys configured → fail
  2. FRESHDESK_WEBHOOK_ENFORCE_HMAC=true but no secret → fail
  3. AUDIT_BACKEND is a supported value (inmemory | supabase) → fail if unknown
  4. Required env vars present in os.environ → fail if REQUIRED vars absent
     (Sprint 2.10: ConfigConsistencyValidator.validate_against_environ())
"""
from __future__ import annotations

import logging
import os

LOGGER = logging.getLogger(__name__)

_VALID_AUDIT_BACKENDS = frozenset({"inmemory", "supabase"})


class ConfigurationValidationError(RuntimeError):
    """Raised at startup when a required configuration is invalid or missing."""


def validate_startup_config() -> None:
    """
    Validate production configuration.

    Raises ConfigurationValidationError with a descriptive message on any failure.
    Safe to call in development (no network calls).
    """
    _check_auth_config()
    _check_webhook_config()
    _check_audit_config()
    _check_env_consistency()


def _check_env_consistency() -> None:
    """
    Run env consistency check. Logs warnings for drift; raises only when
    REQUIRED_IN_PRODUCTION vars are absent from os.environ.
    """
    try:
        from security.env_consistency import ConfigConsistencyValidator, ConfigConsistencyError
        validator = ConfigConsistencyValidator()
        validator.validate_against_environ()
    except ConfigConsistencyError as exc:
        raise ConfigurationValidationError(str(exc)) from exc
    except Exception as exc:
        # Env consistency failure must never block startup unexpectedly —
        # only re-raise ConfigConsistencyError (hard required-var violations).
        LOGGER.error("config_validator: env consistency check error: %s", exc)


def _check_auth_config() -> None:
    raw_enabled = os.environ.get("AUTH_ENABLED", "false").strip().lower()
    auth_enabled = raw_enabled in ("true", "1", "yes")
    if not auth_enabled:
        return

    approver_keys = os.environ.get("APPROVER_API_KEYS", "").strip()
    operator_keys = os.environ.get("OPERATOR_API_KEYS", "").strip()
    admin_keys    = os.environ.get("ADMIN_API_KEYS",    "").strip()

    if not any([approver_keys, operator_keys, admin_keys]):
        raise ConfigurationValidationError(
            "AUTH_ENABLED=true but no API keys are configured. "
            "Set at least one of: APPROVER_API_KEYS, OPERATOR_API_KEYS, ADMIN_API_KEYS. "
            "Format: one 'identity_name:api_key_value' entry per line."
        )


def _check_webhook_config() -> None:
    raw_enforce = os.environ.get("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "true").strip().lower()
    enforce_hmac = raw_enforce not in ("false", "0", "no")
    if not enforce_hmac:
        return

    secret = os.environ.get("FRESHDESK_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise ConfigurationValidationError(
            "FRESHDESK_WEBHOOK_ENFORCE_HMAC=true but FRESHDESK_WEBHOOK_SECRET is not set. "
            "Either set the secret or set FRESHDESK_WEBHOOK_ENFORCE_HMAC=false for development."
        )


def _check_audit_config() -> None:
    backend = os.environ.get("AUDIT_BACKEND", "inmemory").strip().lower()
    if backend not in _VALID_AUDIT_BACKENDS:
        raise ConfigurationValidationError(
            f"AUDIT_BACKEND={backend!r} is not a supported value. "
            f"Supported values: {', '.join(sorted(_VALID_AUDIT_BACKENDS))}. "
            "Set AUDIT_BACKEND=inmemory for development or AUDIT_BACKEND=supabase for production."
        )
