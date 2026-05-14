"""
Pipeline configuration. All paths and tuning parameters live here.
Override via environment variables or pass a PipelineConfig instance
directly when calling pipeline stages programmatically.
"""
from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import FrozenSet

# ── Base paths ─────────────────────────────────────────────────────────────────

_SERVICE_ROOT = Path(__file__).parent.parent
_PIPELINE_ROOT = Path(__file__).parent
_THINK360_ROOT = _SERVICE_ROOT.parent.parent.parent  # C:\Users\HP\Desktop\Think360

# Raw source files — NEVER modified
RAW_DATA_DIR = _THINK360_ROOT          # The 4 source files live here

# Output directories
DATA_DIR         = _SERVICE_ROOT / "data"
INTERMEDIATE_DIR = DATA_DIR / "intermediate"
PROCESSED_DIR    = DATA_DIR / "processed"
REPORTS_DIR      = DATA_DIR / "reports"

# Source file paths
SOURCE_FILES = {
    "xls": RAW_DATA_DIR / "84000794141_tickets-May-12-2026-09_53 (1).xls",
    "csv": RAW_DATA_DIR / "84000794166_tickets-May-12-2026-10_29.csv",
    "rbl_rca1": RAW_DATA_DIR / "RBL_RCA 1.xlsx",
    "rbl_rca":  RAW_DATA_DIR / "RBL_RCA.xlsx",
}

# Sheet mappings for XLSX files
XLSX_SHEETS = {
    "rbl_rca1": "Sheet1",
    "rbl_rca":  "Unity",
}

# ── Filtering ──────────────────────────────────────────────────────────────────

# Query types that are NOT customer-facing support — exclude from AI training
EXCLUDED_QUERY_TYPES: FrozenSet[str] = frozenset({
    "Server Alert",
    "Test Mail",
    "Meeting",
    "Non support related (internal)",
    "Call Test",
    "Patching Activity",
    "Manual Repush",
    "DC-DR",
    "Production Deployment",
    "Manual API Trigger",
    "Manual callback trigger",
    "Manual session status update",
    "ID Mapping Change",
    "ID De-duplication",
    "BRANCH_MASTER UPDATE",
    "SUMMARY_DATA_UPDATE (OTHERS)",
    "SUMMARY_DATA_UPDATE [IMAGE_MISSING]",
    "SUMMARY_DATA_UPDATE [LAT_LONG_MISSING]",
    "SUMMARY_DATA_UPDATE [USER_IP_MISSING]",
    "Summary Data Update",
    "UAT Related Issues",
    "Stage Data Reset",
    "TOKEN GENERATION",
    "Production Scheduled Downtime",
    "Logs Required",
    "Test Mail",
    "UAT Deployment",
    "DC-DR",
    "Ekyc Data Missing",
})

# Escalation-triggering query types
ESCALATION_QUERY_TYPES: FrozenSet[str] = frozenset({
    "Server Alert",
    "Patching Activity",
    "DC-DR",
    "Production Deployment",
    "Production Down",
    "Production Scheduled Downtime",
})

# Trivial RCA phrases that add no training value
TRIVIAL_RCA_PHRASES: FrozenSet[str] = frozenset({
    "manually updated",
    "id deactivation",
    "id creation",
    "under observation",
    "id correction in database",
    "role changes",
    "id deactivated",
    "na",
    "n/a",
    "not found yet",
    "facing issue mentions",
    "meeting",
    "language routing",
    "human error from client side",
    "wrong password login",
    "given data for the json",
    "rca pending from dev",
    "server utilization high",
})

# ── Thresholds ─────────────────────────────────────────────────────────────────

MIN_DESCRIPTION_CHARS_AFTER_CLEAN = 80    # Drop tickets with less content after cleaning
MIN_RCA_CHARS_GOLD = 150                  # Minimum chars for GOLD RCA
MIN_RCA_CHARS_SILVER = 50                 # Minimum chars for SILVER RCA
MIN_RCA_CHARS_USABLE = 30                 # Below this → TRIVIAL

# Agent interactions threshold for AUTO_RESOLVABLE
AUTO_MAX_AGENT_INTERACTIONS = 3

# Eval dataset: minimum description length for inclusion
EVAL_MIN_DESCRIPTION_CHARS = 100

# ── Tenant normalization map ──────────────────────────────────────────────────

TENANT_SLUG_MAP: dict[str, str] = {
    "unity":         "unity_bank",
    "unity small":   "unity_bank",
    "unity bank":    "unity_bank",
    "rbl":           "rbl_bank",
    "rbl bank":      "rbl_bank",
    "bob":           "bank_of_baroda",
    "bankofbaroda":  "bank_of_baroda",
    "bajaj":         "bajaj_finance",
    "bajajfin":      "bajaj_finance",
    "integratecloud":"integratecloud",
    "fino":          "fino",
    "nrfsi":         "nrfsi",
}

# ── Dataclass config ─────────────────────────────────────────────────────────

@dataclass
class PipelineConfig:
    source_files: dict = field(default_factory=lambda: SOURCE_FILES)
    xlsx_sheets: dict = field(default_factory=lambda: XLSX_SHEETS)
    intermediate_dir: Path = INTERMEDIATE_DIR
    processed_dir: Path = PROCESSED_DIR
    reports_dir: Path = REPORTS_DIR
    excluded_query_types: FrozenSet[str] = field(default_factory=lambda: EXCLUDED_QUERY_TYPES)
    escalation_query_types: FrozenSet[str] = field(default_factory=lambda: ESCALATION_QUERY_TYPES)
    trivial_rca_phrases: FrozenSet[str] = field(default_factory=lambda: TRIVIAL_RCA_PHRASES)
    min_description_chars: int = MIN_DESCRIPTION_CHARS_AFTER_CLEAN
    min_rca_chars_gold: int = MIN_RCA_CHARS_GOLD
    min_rca_chars_silver: int = MIN_RCA_CHARS_SILVER
    min_rca_chars_usable: int = MIN_RCA_CHARS_USABLE
    auto_max_agent_interactions: int = AUTO_MAX_AGENT_INTERACTIONS
    eval_min_description_chars: int = EVAL_MIN_DESCRIPTION_CHARS
    tenant_slug_map: dict = field(default_factory=lambda: TENANT_SLUG_MAP)
    log_level: str = "INFO"

    def ensure_dirs(self) -> None:
        for d in [self.intermediate_dir, self.processed_dir, self.reports_dir]:
            d.mkdir(parents=True, exist_ok=True)


def default_config() -> PipelineConfig:
    cfg = PipelineConfig()
    cfg.ensure_dirs()
    return cfg
