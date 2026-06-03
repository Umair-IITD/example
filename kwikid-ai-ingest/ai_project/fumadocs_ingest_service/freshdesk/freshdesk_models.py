"""
freshdesk/freshdesk_models.py

Sprint 2.4: Freshdesk configuration and payload models.

FreshdeskConfig is the sole configuration object for FreshdeskProvider.
It is a frozen dataclass: once constructed it cannot be mutated, making it
safe to share across threads and provider instances.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FreshdeskConfig:
    """
    Validated, immutable configuration for FreshdeskProvider.

    Args:
        domain:          Freshdesk domain without protocol prefix.
                         e.g. "acme.freshdesk.com", NOT "https://acme.freshdesk.com"
        api_key:         Freshdesk API key for HTTP Basic Auth.
                         Auth header: Base64(api_key + ":X")
        timeout_seconds: Per-request HTTP timeout in seconds. Must be > 0.
                         Default: 10.0 seconds.

    Raises:
        ValueError: if domain is empty, has a protocol prefix, api_key is
                    empty, or timeout_seconds is not positive.
    """

    domain: str
    api_key: str
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not self.domain:
            raise ValueError("domain must be non-empty")
        if self.domain.startswith(("http://", "https://")):
            raise ValueError(
                f"domain must not include a protocol prefix, got {self.domain!r}. "
                "Use 'acme.freshdesk.com', not 'https://acme.freshdesk.com'."
            )
        if not self.api_key:
            raise ValueError("api_key must be non-empty")
        if self.timeout_seconds <= 0:
            raise ValueError(
                f"timeout_seconds must be > 0, got {self.timeout_seconds}"
            )

    @property
    def base_url(self) -> str:
        """Full HTTPS base URL derived from domain (no trailing slash)."""
        return f"https://{self.domain}"
