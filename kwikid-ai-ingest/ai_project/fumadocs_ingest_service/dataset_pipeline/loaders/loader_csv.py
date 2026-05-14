"""
Loaders for CSV and SpreadsheetML XLS (Office 2003 XML) source files.
Source files are NEVER modified — all operations are read-only.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def load_csv(path: Path, encoding: str = "utf-8") -> pd.DataFrame:
    """
    Load a UTF-8 CSV Freshdesk export into a raw DataFrame.
    Falls back to latin-1 if UTF-8 decode fails.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"CSV source file not found: {path}")

    try:
        df = pd.read_csv(path, encoding=encoding, low_memory=False, dtype=str)
    except UnicodeDecodeError:
        logger.warning("UTF-8 decode failed for %s; retrying with latin-1", path.name)
        df = pd.read_csv(path, encoding="latin-1", low_memory=False, dtype=str)

    df = df.dropna(how="all")
    logger.info("Loaded CSV %s: %d rows, %d columns", path.name, len(df), len(df.columns))
    return df


def load_xls_spreadsheetml(path: Path) -> pd.DataFrame:
    """
    Parse a Microsoft Office 2003 SpreadsheetML XML file (.xls).
    These files start with <?xml and use namespace
    urn:schemas-microsoft-com:office:spreadsheet — xlrd cannot handle them.
    """
    from lxml import etree  # type: ignore

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"XLS source file not found: {path}")

    NS = "urn:schemas-microsoft-com:office:spreadsheet"
    ns = {"ss": NS}

    logger.info("Parsing SpreadsheetML XLS: %s", path.name)
    tree = etree.parse(str(path))
    root = tree.getroot()

    # Find first worksheet
    worksheet = root.find(".//ss:Worksheet", ns)
    if worksheet is None:
        raise ValueError(f"No Worksheet element found in {path.name}")

    table = worksheet.find("ss:Table", ns)
    if table is None:
        raise ValueError(f"No Table element found in {path.name}")

    rows = table.findall("ss:Row", ns)
    if not rows:
        raise ValueError(f"No Row elements found in {path.name}")

    def _cell_value(cell) -> Optional[str]:
        data = cell.find("ss:Data", ns)
        if data is None or data.text is None:
            return None
        return str(data.text).strip()

    # First row is headers
    headers = [_cell_value(c) or f"col_{i}" for i, c in enumerate(rows[0].findall("ss:Cell", ns))]

    records = []
    for row in rows[1:]:
        cells = row.findall("ss:Cell", ns)
        # SpreadsheetML cells may have ss:Index attribute for sparse rows
        row_data: dict[str, Optional[str]] = {h: None for h in headers}
        col_idx = 0
        for cell in cells:
            index_attr = cell.get(f"{{{NS}}}Index")
            if index_attr is not None:
                col_idx = int(index_attr) - 1  # 1-based → 0-based
            if col_idx < len(headers):
                row_data[headers[col_idx]] = _cell_value(cell)
            col_idx += 1
        records.append(row_data)

    df = pd.DataFrame(records, columns=headers)
    df = df.dropna(how="all")
    logger.info(
        "Loaded SpreadsheetML XLS %s: %d rows, %d columns",
        path.name, len(df), len(df.columns)
    )
    return df
