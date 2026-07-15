"""
unity/config.py

Sprint 2.51: Centralized configuration for Unity Admin Portal integration.

Environment variables (all optional; sensible defaults):
    UNITY_BASE_URL              default: https://vkyc360.unitybank.co.in
    UNITY_DOMAIN                default: unity
    UNITY_USERNAME              default: unity        (service account; override in prod)
    UNITY_PASSWORD              default: ""            (must be set in prod)
    UNITY_TIMEOUT_CONNECT_S     default: 5
    UNITY_TIMEOUT_READ_S        default: 15
    UNITY_MAX_RETRIES           default: 2
    UNITY_RETRY_BACKOFF_S       default: 1.0
    UNITY_TOKEN_REFRESH_BUFFER_S default: 120   (2 min proactive refresh window)
    UNITY_ENABLED               default: true

Dependency direction: config.py → stdlib only.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _read_env(name: str, default: str) -> str:
    val = os.environ.get(name, "")
    return val.strip() if val else default


def _read_int(name: str, default: int, *, min_value: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(int(raw), min_value)
    except ValueError:
        return default


def _read_float(name: str, default: float, *, min_value: float = 0.0) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(float(raw), min_value)
    except ValueError:
        return default


def _read_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    return raw in _TRUE_VALUES if raw else default


@dataclass(frozen=True)
class UnityConfig:
    """Immutable Unity Admin Portal configuration."""
    base_url:                 str
    domain:                   str
    username:                 str
    password:                 str
    timeout_connect_s:        int
    timeout_read_s:           int
    max_retries:              int
    retry_backoff_s:          float
    token_refresh_buffer_s:   int
    user_agent:               str
    enabled:                  bool = True

    def __post_init__(self) -> None:
        if not self.base_url:
            raise ValueError("base_url is required")
        low = self.base_url.lower()
        if not (low.startswith("http://") or low.startswith("https://")):
            raise ValueError("base_url must include http:// or https:// scheme")
        if self.timeout_connect_s < 1:
            raise ValueError("timeout_connect_s must be >= 1")
        if self.timeout_read_s < 1:
            raise ValueError("timeout_read_s must be >= 1")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if not self.domain:
            raise ValueError("domain is required (e.g. 'unity')")

    @property
    def normalized_base_url(self) -> str:
        return self.base_url.rstrip("/")

    @property
    def has_credentials(self) -> bool:
        return bool(self.username and self.password)

    @property
    def masked_username(self) -> str:
        if not self.username:
            return ""
        if len(self.username) <= 3:
            return "***"
        return f"{self.username[:2]}****"

    @classmethod
    def from_env(cls) -> "UnityConfig":
        return cls(
            base_url=              _read_env("UNITY_BASE_URL", "https://vkyc360.unitybank.co.in"),
            domain=                _read_env("UNITY_DOMAIN", "unity"),
            username=              _read_env("UNITY_USERNAME", "unity"),
            password=              _read_env("UNITY_PASSWORD", ""),
            timeout_connect_s=     _read_int("UNITY_TIMEOUT_CONNECT_S", 5, min_value=1),
            timeout_read_s=        _read_int("UNITY_TIMEOUT_READ_S", 15, min_value=1),
            max_retries=           _read_int("UNITY_MAX_RETRIES", 2, min_value=0),
            retry_backoff_s=       _read_float("UNITY_RETRY_BACKOFF_S", 1.0, min_value=0.0),
            token_refresh_buffer_s=_read_int("UNITY_TOKEN_REFRESH_BUFFER_S", 120, min_value=0),
            user_agent=            _read_env("UNITY_USER_AGENT", "KwikID-Support-Automation/2.51 (+unity)"),
            enabled=               _read_bool("UNITY_ENABLED", True),
        )
