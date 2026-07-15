"""
intelligence/config.py

Wave 3 (Sprint 2.53): Enterprise Intelligence Layer configuration.

Provider-agnostic LLM configuration + reasoning knobs. No hardcoded vendor.
The runtime chooses the concrete client via `INTELLIGENCE_LLM_PROVIDER`.

Environment variables (all optional; sensible defaults):
    INTELLIGENCE_ENABLED              default: true
    INTELLIGENCE_LLM_PROVIDER         default: openai      (options: openai | anthropic | azure_openai | mock)
    INTELLIGENCE_LLM_MODEL            default: gpt-4o-mini
    INTELLIGENCE_LLM_BASE_URL         default: https://api.openai.com/v1
    INTELLIGENCE_LLM_API_KEY          default: ""          (falls back to OPENAI_CHAT_API_KEY, then OPENAI_API_KEY)
    INTELLIGENCE_LLM_TIMEOUT_S        default: 45
    INTELLIGENCE_LLM_MAX_RETRIES      default: 2
    INTELLIGENCE_LLM_TEMPERATURE      default: 0.2         (deterministic-ish for structured reasoning)
    INTELLIGENCE_LLM_MAX_OUTPUT_TOKENS default: 1200
    INTELLIGENCE_LLM_JSON_MODE        default: true
    INTELLIGENCE_CONFIDENCE_THRESHOLD default: 0.75        (below → clarification required)
    INTELLIGENCE_USER_AGENT           default: KwikID-Support-Automation/2.53 (+intelligence)

Dependency direction: config.py → stdlib only.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_VALID_PROVIDERS = frozenset({"openai", "anthropic", "azure_openai", "mock"})


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


def _read_float(name: str, default: float, *, min_value: float = 0.0,
                max_value: float | None = None) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = float(raw)
    except ValueError:
        return default
    v = max(v, min_value)
    if max_value is not None:
        v = min(v, max_value)
    return v


def _read_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    return raw in _TRUE_VALUES if raw else default


def _resolve_api_key() -> str:
    """
    Provider-agnostic API key resolution.

    Order: INTELLIGENCE_LLM_API_KEY → OPENAI_CHAT_API_KEY → OPENAI_API_KEY.
    Empty string returned if none of the three are set.
    """
    for name in ("INTELLIGENCE_LLM_API_KEY", "OPENAI_CHAT_API_KEY", "OPENAI_API_KEY"):
        val = os.environ.get(name, "").strip()
        if val:
            return val
    return ""


@dataclass(frozen=True)
class IntelligenceConfig:
    """Immutable configuration for the Enterprise Intelligence Layer."""

    enabled:              bool
    llm_provider:         str
    llm_model:            str
    llm_base_url:         str
    llm_api_key:          str
    llm_timeout_s:        int
    llm_max_retries:      int
    llm_temperature:      float
    llm_max_output_tokens: int
    llm_json_mode:        bool
    confidence_threshold: float
    user_agent:           str

    def __post_init__(self) -> None:
        if self.llm_provider not in _VALID_PROVIDERS:
            raise ValueError(
                f"invalid llm_provider: {self.llm_provider!r} — "
                f"expected one of {sorted(_VALID_PROVIDERS)}"
            )
        if not self.llm_model:
            raise ValueError("llm_model is required")
        if self.llm_timeout_s < 1:
            raise ValueError("llm_timeout_s must be >= 1")
        if self.llm_max_retries < 0:
            raise ValueError("llm_max_retries must be >= 0")
        if not (0.0 <= self.llm_temperature <= 2.0):
            raise ValueError("llm_temperature must be in [0.0, 2.0]")
        if self.llm_max_output_tokens < 1:
            raise ValueError("llm_max_output_tokens must be >= 1")
        if not (0.0 <= self.confidence_threshold <= 1.0):
            raise ValueError("confidence_threshold must be in [0.0, 1.0]")

    # ── Derived ──────────────────────────────────────────────────────────────

    @property
    def has_api_key(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def masked_api_key(self) -> str:
        """First 8 chars + '****'. Never log the raw key."""
        if not self.llm_api_key:
            return ""
        return f"{self.llm_api_key[:8]}****" if len(self.llm_api_key) > 8 else "****"

    @property
    def normalized_base_url(self) -> str:
        return self.llm_base_url.rstrip("/")

    @property
    def is_mock(self) -> bool:
        """True when the provider is the deterministic mock — no HTTP calls."""
        return self.llm_provider == "mock"

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "IntelligenceConfig":
        return cls(
            enabled=              _read_bool("INTELLIGENCE_ENABLED", True),
            llm_provider=         _read_env("INTELLIGENCE_LLM_PROVIDER", "openai").lower(),
            llm_model=            _read_env("INTELLIGENCE_LLM_MODEL", "gpt-4o-mini"),
            llm_base_url=         _read_env("INTELLIGENCE_LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_api_key=          _resolve_api_key(),
            llm_timeout_s=        _read_int("INTELLIGENCE_LLM_TIMEOUT_S", 45, min_value=1),
            llm_max_retries=      _read_int("INTELLIGENCE_LLM_MAX_RETRIES", 2, min_value=0),
            llm_temperature=      _read_float("INTELLIGENCE_LLM_TEMPERATURE", 0.2,
                                              min_value=0.0, max_value=2.0),
            llm_max_output_tokens=_read_int("INTELLIGENCE_LLM_MAX_OUTPUT_TOKENS", 1200, min_value=1),
            llm_json_mode=        _read_bool("INTELLIGENCE_LLM_JSON_MODE", True),
            confidence_threshold= _read_float("INTELLIGENCE_CONFIDENCE_THRESHOLD", 0.75,
                                              min_value=0.0, max_value=1.0),
            user_agent=           _read_env("INTELLIGENCE_USER_AGENT",
                                            "KwikID-Support-Automation/2.53 (+intelligence)"),
        )
