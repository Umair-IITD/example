"""
Stage 1 — File Loading.
Reads all source files into tagged raw DataFrames without modifying originals.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from ..config import PipelineConfig, default_config
from ..loaders.loader_csv import load_csv, load_xls_spreadsheetml
from ..loaders.loader_xlsx import load_xlsx

logger = logging.getLogger(__name__)


def run(cfg: Optional[PipelineConfig] = None) -> dict[str, pd.DataFrame]:
    """
    Load all 4 source files into raw DataFrames keyed by source_key.

    Returns:
        {
            "csv":      DataFrame (UTF-8 CSV export),
            "xls":      DataFrame (SpreadsheetML XLS export),
            "rbl_rca1": DataFrame (RBL_RCA 1.xlsx, Sheet1),
            "rbl_rca":  DataFrame (RBL_RCA.xlsx, Unity sheet),
        }
    """
    if cfg is None:
        cfg = default_config()

    frames: dict[str, pd.DataFrame] = {}

    # CSV
    csv_path = cfg.source_files["csv"]
    logger.info("[S1] Loading CSV: %s", csv_path)
    df_csv = load_csv(csv_path)
    df_csv["_source_key"] = "csv"
    df_csv["_source_file"] = Path(csv_path).name
    frames["csv"] = df_csv

    # XLS (SpreadsheetML XML)
    xls_path = cfg.source_files["xls"]
    logger.info("[S1] Loading XLS (SpreadsheetML): %s", xls_path)
    df_xls = load_xls_spreadsheetml(xls_path)
    df_xls["_source_key"] = "xls"
    df_xls["_source_file"] = Path(xls_path).name
    frames["xls"] = df_xls

    # RBL_RCA 1.xlsx
    rbl1_path = cfg.source_files["rbl_rca1"]
    sheet1 = cfg.xlsx_sheets["rbl_rca1"]
    logger.info("[S1] Loading XLSX: %s (sheet: %s)", rbl1_path, sheet1)
    df_rbl1 = load_xlsx(rbl1_path, sheet_name=sheet1)
    df_rbl1["_source_key"] = "rbl_rca1"
    df_rbl1["_source_file"] = Path(rbl1_path).name
    frames["rbl_rca1"] = df_rbl1

    # RBL_RCA.xlsx
    rbl_path = cfg.source_files["rbl_rca"]
    sheet_unity = cfg.xlsx_sheets["rbl_rca"]
    logger.info("[S1] Loading XLSX: %s (sheet: %s)", rbl_path, sheet_unity)
    df_rbl = load_xlsx(rbl_path, sheet_name=sheet_unity)
    df_rbl["_source_key"] = "rbl_rca"
    df_rbl["_source_file"] = Path(rbl_path).name
    frames["rbl_rca"] = df_rbl

    # Summary
    total = sum(len(df) for df in frames.values())
    logger.info(
        "[S1] Loaded %d total rows across %d source files: %s",
        total,
        len(frames),
        {k: len(v) for k, v in frames.items()},
    )
    return frames
