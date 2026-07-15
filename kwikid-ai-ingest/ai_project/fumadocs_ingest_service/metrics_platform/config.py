"""
metrics_platform/config.py

Sprint 2.50: Centralized configuration for the Uptime Kuma monitoring
platform integration.

Everything the client, the adapters, and the tools need must come through
`MetricsPlatformConfig`. No local constants, no hardcoded credentials.

Environment variables (all optional; empty → feature disabled):
    METRICS_PLATFORM_BASE_URL       default: http://status.getkwikid.com:3001
    METRICS_PLATFORM_API_KEY        default: "" — Uptime Kuma REST /metrics bearer key
    METRICS_PLATFORM_TIMEOUT_S      default: 10
    METRICS_PLATFORM_MAX_RETRIES    default: 2
    METRICS_PLATFORM_DEFAULT_SLUG   default: kwikid
    METRICS_PLATFORM_AUTH_MODE      default: bearer (options: bearer | basic | none)
    METRICS_PLATFORM_USER_AGENT     default: KwikID-Support-Automation/2.50 (+metrics)
    METRICS_PLATFORM_ENABLED        default: true (set "false" to disable adapter)

Sprint 2.52 additions — explicit Prometheus basic-auth credentials.
When BOTH `METRICS_PROMETHEUS_USERNAME` and `METRICS_PROMETHEUS_PASSWORD` are
set, they take precedence over `METRICS_PLATFORM_API_KEY` for the /metrics
endpoint and the effective auth_mode becomes `basic` regardless of
`METRICS_PLATFORM_AUTH_MODE`. This lets operators distinguish between:
  1. Uptime Kuma REST /metrics — Bearer with the API-key value
  2. A separate Prometheus scrape endpoint requiring username+password basic auth
    METRICS_PROMETHEUS_USERNAME    default: ""
    METRICS_PROMETHEUS_PASSWORD    default: ""

Dependency direction:
    config.py → stdlib only. No case_engine / freshdesk imports.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _read_env(name: str, default: str) -> str:
    val = os.environ.get(name, "")
    return val.strip() if val else default


def _read_int(name: str, default: int, *, min_value: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    return max(v, min_value)


def _read_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in _TRUE_VALUES


@dataclass(frozen=True)
class MetricsPlatformConfig:
    """
    Immutable configuration for the Uptime Kuma integration.

    Constructed via `MetricsPlatformConfig.from_env()` in production, or
    with explicit kwargs in tests.
    """
    base_url:            str
    api_key:             str
    timeout_s:           int
    max_retries:         int
    default_slug:        str
    auth_mode:           str
    user_agent:          str
    enabled:             bool = True
    # Sprint 2.52 — explicit Prometheus basic-auth credentials.
    prometheus_username: str  = ""
    prometheus_password: str  = ""

    # ── Validation ───────────────────────────────────────────────────────────

    def __post_init__(self) -> None:
        if self.timeout_s < 1:
            raise ValueError("timeout_s must be >= 1")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.auth_mode not in {"bearer", "basic", "none"}:
            raise ValueError(f"invalid auth_mode: {self.auth_mode!r}")
        if not self.base_url:
            raise ValueError("base_url is required")
        base_lower = self.base_url.lower()
        if not (base_lower.startswith("http://") or base_lower.startswith("https://")):
            raise ValueError("base_url must include http:// or https:// scheme")
        # Uptime Kuma base URL should not carry a path.
        if self.base_url.rstrip("/").count("/") > 2:
            raise ValueError("base_url must be a bare origin (no path)")

    # ── Derived ──────────────────────────────────────────────────────────────

    @property
    def normalized_base_url(self) -> str:
        return self.base_url.rstrip("/")

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    @property
    def has_prometheus_credentials(self) -> bool:
        """
        True iff a Prometheus password is set (username is optional).

        Sprint 2.53 (Wave 3, Part J) reality: the production Prometheus scrape
        endpoint accepts an empty username and uses the value in
        `METRICS_PROMETHEUS_PASSWORD` as the API key. Username-only is
        insufficient — password is the credential.
        """
        return bool(self.prometheus_password)

    @property
    def effective_auth_mode(self) -> str:
        """
        Sprint 2.52: When explicit Prometheus basic-auth credentials are set,
        they take precedence over `auth_mode` for the /metrics endpoint.
        Returns 'basic' in that case; otherwise returns the configured
        `auth_mode` verbatim.
        """
        if self.has_prometheus_credentials:
            return "basic"
        return self.auth_mode

    @property
    def masked_api_key(self) -> str:
        """Return an 8-char prefix + '****'. Never log the raw key."""
        if not self.api_key:
            return ""
        return f"{self.api_key[:8]}****" if len(self.api_key) > 8 else "****"

    @property
    def masked_prometheus_username(self) -> str:
        if not self.prometheus_username:
            return ""
        if len(self.prometheus_username) <= 3:
            return "***"
        return f"{self.prometheus_username[:2]}****"

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "MetricsPlatformConfig":
        return cls(
            base_url=            _read_env("METRICS_PLATFORM_BASE_URL", "http://status.getkwikid.com:3001"),
            api_key=             _read_env("METRICS_PLATFORM_API_KEY", ""),
            timeout_s=           _read_int("METRICS_PLATFORM_TIMEOUT_S", 10, min_value=1),
            max_retries=         _read_int("METRICS_PLATFORM_MAX_RETRIES", 2, min_value=0),
            default_slug=        _read_env("METRICS_PLATFORM_DEFAULT_SLUG", "kwikid"),
            auth_mode=           _read_env("METRICS_PLATFORM_AUTH_MODE", "bearer").lower(),
            user_agent=          _read_env("METRICS_PLATFORM_USER_AGENT", "KwikID-Support-Automation/2.50 (+metrics)"),
            enabled=             _read_bool("METRICS_PLATFORM_ENABLED", True),
            prometheus_username= _read_env("METRICS_PROMETHEUS_USERNAME", ""),
            prometheus_password= _read_env("METRICS_PROMETHEUS_PASSWORD", ""),
        )
