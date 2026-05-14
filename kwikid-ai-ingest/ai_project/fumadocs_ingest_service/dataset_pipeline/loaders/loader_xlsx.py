"""
Loader for OOXML XLSX Freshdesk exports (RBL_RCA.xlsx and RBL_RCA 1.xlsx).
Source files are NEVER modified — all operations are read-only.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def load_xlsx(path: Path, sheet_name: str = "Sheet1") -> pd.DataFrame:
    """
    Load a single sheet from an XLSX file into a raw DataFrame.
    All cells are read as strings to avoid silent type coercion.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"XLSX source file not found: {path}")

    try:
        df = pd.read_excel(
            path,
            sheet_name=sheet_name,
            dtype=str,
            engine="openpyxl",
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to read sheet '{sheet_name}' from {path.name}: {exc}"
        ) from exc

    # Normalize column names: strip surrounding whitespace
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all")

    logger.info(
        "Loaded XLSX %s (sheet '%s'): %d rows, %d columns",
        path.name, sheet_name, len(df), len(df.columns)
    )
    return df
