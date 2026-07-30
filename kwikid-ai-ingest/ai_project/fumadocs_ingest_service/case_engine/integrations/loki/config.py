"""case_engine/integrations/loki/config.py — Loki tenant configuration.

Sprint 2.60: Multi-Tenant Log Platform (Grafana Loki) integration config.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from enum import Enum


class LokiLogAvailability(str, Enum):
    AVAILABLE   = "AVAILABLE"
    PARTIAL     = "PARTIAL"      # HTTP 200 but zero lines — may be retention aging
    UNAVAILABLE = "UNAVAILABLE"  # HTTP call failed
    DISABLED    = "DISABLED"     # credentials missing or LOKI_ENABLED=false


@dataclass
class LokiConfig:
    saas_base_url: str
    saas_username: str
    saas_password: str
    enabled: bool
    timeout: float = 30.0
    query_limit: int = 1000
    # Sprint 2.60 tenant registration (Canara, BOB): per-tenant credential
    # fields, additive only. These do NOT affect `enabled`/`has_credentials`,
    # which remain SaaS-scoped exactly as before — see LokiClient.__init__
    # (client.py) for where these are actually consumed per tenant_key.
    canara_base_url: str = ""
    canara_username: str = ""
    canara_password: str = ""
    bob_base_url: str = ""
    bob_username: str = ""
    bob_password: str = ""

    @classmethod
    def from_env(cls) -> "LokiConfig":
        username  = os.getenv("LOKI_SAAS_USERNAME", "")
        password  = os.getenv("LOKI_SAAS_PASSWORD", "")
        base_url  = os.getenv("LOKI_SAAS_URL", "https://utility-server-sfd.app.getkwikid.com")
        flag      = os.getenv("LOKI_ENABLED", "").lower()
        if flag in ("0", "false", "no", "off"):
            enabled = False
        else:
            enabled = bool(username and password)
        return cls(
            saas_base_url=base_url,
            saas_username=username,
            saas_password=password,
            enabled=enabled,
            timeout=float(os.getenv("LOKI_TIMEOUT", "30")),
            query_limit=int(os.getenv("LOKI_QUERY_LIMIT", "1000")),
            canara_base_url=os.getenv("LOKI_CANARA_URL", ""),
            canara_username=os.getenv("LOKI_CANARA_USERNAME", ""),
            canara_password=os.getenv("LOKI_CANARA_PASSWORD", ""),
            bob_base_url=os.getenv("LOKI_BOB_URL", ""),
            bob_username=os.getenv("LOKI_BOB_USERNAME", ""),
            bob_password=os.getenv("LOKI_BOB_PASSWORD", ""),
        )

    @property
    def has_credentials(self) -> bool:
        return bool(self.saas_username and self.saas_password)

    @property
    def masked_username(self) -> str:
        if not self.saas_username:
            return "(not set)"
        return self.saas_username[:2] + "***"
