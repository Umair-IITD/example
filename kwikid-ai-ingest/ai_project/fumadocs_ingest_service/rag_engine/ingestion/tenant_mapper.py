"""
rag_engine/ingestion/tenant_mapper.py

Maps Stack Overflow tags to KwikID tenant client slugs.

Built-in default mapping covers all tags observed in the Think360 More_data/ export.
An optional JSON file override (B3_TENANT_MAP_PATH env var) allows extending or
overriding the built-in mapping without code changes.

Usage:
    mapper = TenantMapper.from_env()
    clients = mapper.map_tags(["cbi", "otp", "kyc"])
    # → ["cbi"]   (only client-specific tags mapped; generic tags skipped)

Rules:
    - Post has client tags → clients = [mapped slugs] → only those tenants retrieve
    - Post has NO client tags → clients = [] → global (all tenants can retrieve)
    - Post has BOTH client + generic tags → only client slugs returned
    - Multiple client tags → all corresponding slugs returned (multi-tenant knowledge)
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

LOGGER = logging.getLogger(__name__)

# Built-in default mapping: SO tag → tenant client slug
# All keys are lowercase to match tag normalization in the parser.
_DEFAULT_TAG_TO_CLIENT: dict[str, str] = {
    # ── Central Bank of India ────────────────────────────────────────────────
    "cbi":          "cbi",
    "cbi-dkyc":     "cbi",
    "cbi-prod":     "cbi",
    "cbi-uat":      "cbi",
    "cbi-kyc":      "cbi",

    # ── Unity Bank ───────────────────────────────────────────────────────────
    "unity":        "unity_bank",
    "unity-bank":   "unity_bank",
    "unity-prod":   "unity_bank",

    # ── RBL Bank ─────────────────────────────────────────────────────────────
    "rbl":          "rbl_bank",
    "rbl-prod":     "rbl_bank",
    "rbl-uat":      "rbl_bank",
    "rbl-bank":     "rbl_bank",

    # ── Bank of Baroda ────────────────────────────────────────────────────────
    "bob":          "bob_bank",
    "bob-prod":     "bob_bank",
    "bob-uat":      "bob_bank",

    # ── Canara Bank ──────────────────────────────────────────────────────────
    "canarabank":   "canara_bank",
    "canara":       "canara_bank",
    "canara-prod":  "canara_bank",

    # ── Bajaj Finance ────────────────────────────────────────────────────────
    "bajajfin":     "bajaj_finance",
    "bajaj":        "bajaj_finance",
    "bajaj-prod":   "bajaj_finance",
    "bfl":          "bajaj_finance",

    # ── Fino Bank ────────────────────────────────────────────────────────────
    "fino":         "fino_bank",
    "fino-prod":    "fino_bank",

    # ── NRFSI ────────────────────────────────────────────────────────────────
    "nrfsi":        "nrfsi",

    # ── TCook ────────────────────────────────────────────────────────────────
    "tcook":        "tcook",
}


class TenantMapper:
    """
    Maps Stack Overflow tags to tenant client slugs.
    Thread-safe (read-only after construction).
    """

    def __init__(self, mapping: Optional[dict[str, str]] = None) -> None:
        self._mapping: dict[str, str] = dict(_DEFAULT_TAG_TO_CLIENT)
        if mapping:
            # Override / extend built-in mapping
            self._mapping.update({k.lower(): v for k, v in mapping.items()})

    @classmethod
    def from_env(cls) -> "TenantMapper":
        """
        Construct TenantMapper, optionally loading a JSON override file from
        the B3_TENANT_MAP_PATH environment variable.
        """
        override_path = os.getenv("B3_TENANT_MAP_PATH", "").strip()
        extra: dict[str, str] = {}
        if override_path:
            path = Path(override_path)
            if path.exists():
                try:
                    extra = json.loads(path.read_text(encoding="utf-8"))
                    LOGGER.info("TenantMapper: loaded override mapping from %s (%d entries)", path, len(extra))
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning("TenantMapper: failed to read override mapping %s: %s", path, exc)
            else:
                LOGGER.warning("TenantMapper: override path does not exist: %s", path)
        return cls(mapping=extra if extra else None)

    def map_tags(self, tags: list[str]) -> list[str]:
        """
        Return unique, sorted client slugs for the given tag list.

        Returns [] (empty list = global knowledge) if no tag maps to a client.
        Tags that don't match any client are silently ignored — they represent
        generic topics (otp, kyc, video-kyc, etc.) that apply to all tenants.
        """
        clients: set[str] = set()
        for tag in tags:
            slug = self._mapping.get(tag.lower().strip())
            if slug:
                clients.add(slug)
        return sorted(clients)

    @property
    def known_clients(self) -> set[str]:
        """All client slugs known to this mapper (for validation)."""
        return set(self._mapping.values())

    def is_client_tag(self, tag: str) -> bool:
        """True if this tag maps to a specific client."""
        return tag.lower().strip() in self._mapping
