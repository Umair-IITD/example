from __future__ import annotations

import csv
import logging
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from app.chunker import SourceDocument

NBSP_RE = re.compile(r"[\xa0\u200b]")
EMPTY_VALUE = "(empty)"
TABULAR_EXTENSIONS = {".xlsx", ".xls", ".csv"}
LOGGER = logging.getLogger(__name__)

try:
    import pandas as pd  # type: ignore
except Exception:  # noqa: BLE001
    pd = None


def _normalize_header(raw: Any) -> str:
    s = str(raw or "")
    s = NBSP_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _unique_headers(raw_headers: list[str]) -> tuple[list[str], list[str]]:
    """Return display headers; warnings for duplicates after normalize."""
    warnings: list[str] = []
    seen: dict[str, int] = {}
    out: list[str] = []
    for raw in raw_headers:
        base = _normalize_header(raw) or "Column"
        n = seen.get(base, 0)
        if n:
            label = f"{base} ({n + 1})"
            warnings.append(f"Duplicate header normalized to: {label}")
        else:
            label = base
        seen[base] = n + 1
        out.append(label)
    return out, warnings


def _format_cell(value: Any) -> str:
    if value is None:
        return EMPTY_VALUE
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat(timespec="seconds")
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    text = str(value).strip()
    if not text:
        return EMPTY_VALUE
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if len(lines) > 1:
        return "\n\n".join(lines)
    return text


def _row_is_empty(values: list[Any]) -> bool:
    for v in values:
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return False
    return True


def _find_tabular_path(root: Path, only_file_path: str) -> Path | None:
    candidate = Path(only_file_path).expanduser()
    if candidate.is_absolute():
        resolved_candidate = candidate.resolve()
        if resolved_candidate.is_file() and resolved_candidate.suffix.lower() in TABULAR_EXTENSIONS:
            return resolved_candidate

    root = root.resolve()
    if not root.is_dir():
        return None
    only = only_file_path.strip().replace("\\", "/")
    if only.startswith("./"):
        only = only[2:]
    if only.startswith("/"):
        only = only[1:]
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TABULAR_EXTENSIONS:
            continue
        try:
            rel = str(path.resolve().relative_to(root)).replace("\\", "/")
        except ValueError:
            continue
        if rel == only or path.name == only:
            return path
    return None


def _safe_display_path(path: Path, root: Path) -> str:
    resolved_path = path.resolve()
    try:
        return str(resolved_path.relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(resolved_path).replace("\\", "/")


def _semantic_key_for_header(header: str) -> str | None:
    normalized = header.lower()
    if normalized == "id":
        return "id"
    if "bank name" in normalized:
        return "bank"
    if "type of issue" in normalized:
        return "issue"
    if "application type" in normalized:
        return "app_type"
    return None


def _extract_semantic_labels(headers: list[str], values: list[Any]) -> dict[str, str]:
    labels: dict[str, str] = {"id": "", "bank": "", "issue": "", "app_type": ""}
    for index, header in enumerate(headers):
        if index >= len(values):
            break
        semantic_key = _semantic_key_for_header(header)
        if not semantic_key:
            continue
        text = _format_cell(values[index])
        labels[semantic_key] = "" if text == EMPTY_VALUE else text
    return labels


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _build_row_title(row_id: str | None, bank: str, issue: str) -> str:
    parts = [p for p in (bank, issue) if p]
    if row_id:
        return f"Support case ID {row_id}" + (f" — {' · '.join(parts)}" if parts else "")
    if parts:
        return " · ".join(parts)
    return "Excel support row"


def _title_and_tags(headers: list[str], values: list[Any]) -> tuple[str, list[str], str | None]:
    labels = _extract_semantic_labels(headers, values)
    row_id = labels["id"] or None
    bank = labels["bank"]
    issue = labels["issue"]
    app_type = labels["app_type"]
    title = _build_row_title(row_id, bank, issue)
    tags = _dedupe_preserve_order([bank, app_type, issue])
    return title, tags[:12], row_id


def _build_row_content(title: str, headers: list[str], values: list[Any]) -> str:
    blocks: list[str] = [title]
    for i, h in enumerate(headers):
        v = values[i] if i < len(values) else None
        cell = _format_cell(v)
        blocks.append(f"{h}:\n{cell}")
    return "\n\n".join(blocks)


def _resolve_sheet(workbook: Any, sheet_name: str | None, summary: dict[str, Any]) -> Any | None:
    if sheet_name:
        if sheet_name not in workbook.sheetnames:
            summary["warnings"].append(f"Sheet {sheet_name!r} not found; available: {workbook.sheetnames}")
            return None
        return workbook[sheet_name]
    return workbook.active


def _create_source_document(
    *,
    path: Path,
    sheet_title: str,
    headers: list[str],
    values: list[Any],
    row_num: int,
    excel_rel_path: str | None,
) -> SourceDocument:
    title, tags, row_id = _title_and_tags(headers, values)
    content = _build_row_content(title, headers, values)
    stem = path.stem.replace(" ", "-").lower()
    safe_sheet = sheet_title.replace(" ", "-")
    source_id = f"{stem}:sheet:{safe_sheet}:row:{row_num}"
    if row_id:
        source_id = f"{stem}:sheet:{safe_sheet}:id:{row_id}"

    metadata: dict[str, Any] = {
        "excel_file": path.name,
        "excel_rel_path": excel_rel_path,
        "sheet": sheet_title,
        "excel_row": row_num,
        "column_count": len(headers),
    }
    if row_id:
        metadata["spreadsheet_id"] = row_id

    return SourceDocument(
        source_type="excel",
        source_id=source_id,
        content=content,
        title=title,
        heading=title,
        tags=tags,
        creation_date=None,
        metadata=metadata,
    )


def _build_sheet_documents(path: Path, sheet: Any, headers: list[str], excel_rel_path: str | None) -> tuple[list[SourceDocument], int]:
    max_col = sheet.max_column or 0
    max_row = sheet.max_row or 0
    docs: list[SourceDocument] = []
    skipped = 0
    for excel_row_num in range(2, max_row + 1):
        values = [sheet.cell(row=excel_row_num, column=col).value for col in range(1, max_col + 1)]
        if _row_is_empty(values):
            skipped += 1
            continue
        docs.append(
            _create_source_document(
                path=path,
                sheet_title=sheet.title,
                headers=headers,
                values=values,
                row_num=excel_row_num,
                excel_rel_path=excel_rel_path,
            )
        )
    return docs, skipped


def _build_documents_from_rows(
    *,
    path: Path,
    sheet_title: str,
    headers: list[str],
    rows: list[list[Any]],
    excel_rel_path: str | None,
) -> tuple[list[SourceDocument], int]:
    docs: list[SourceDocument] = []
    skipped = 0
    for row_offset, values in enumerate(rows, start=2):
        if _row_is_empty(values):
            skipped += 1
            continue
        docs.append(
            _create_source_document(
                path=path,
                sheet_title=sheet_title,
                headers=headers,
                values=values,
                row_num=row_offset,
                excel_rel_path=excel_rel_path,
            )
        )
    return docs, skipped


def _parse_csv_rows(path: Path) -> tuple[list[str], list[list[Any]], list[str]]:
    warnings: list[str] = []
    try:
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
    except OSError as exc:
        return [], [], [f"Failed to read CSV file: {exc}"]
    reader = csv.reader(text.splitlines())
    rows = list(reader)
    if not rows:
        return [], [], ["CSV is empty"]
    raw_headers = [str(cell) for cell in rows[0]]
    headers, dup_warnings = _unique_headers(raw_headers)
    warnings.extend(dup_warnings)
    data_rows = [list(row) for row in rows[1:]]
    return headers, data_rows, warnings


def _parse_xls_with_pandas(path: Path) -> tuple[dict[str, tuple[list[str], list[list[Any]]]], list[str]]:
    warnings: list[str] = []
    if pd is None:
        return {}, ["pandas is not installed; cannot parse .xls file"]
    try:
        workbook = pd.read_excel(path, sheet_name=None)
    except ImportError as exc:
        return {}, [f"Unable to parse .xls (missing dependency): {exc}"]
    except Exception as exc:  # noqa: BLE001
        return {}, [f"Unable to parse .xls: {exc}"]

    parsed: dict[str, tuple[list[str], list[list[Any]]]] = {}
    for sheet_name, frame in workbook.items():
        frame = frame.fillna("")
        raw_headers = [str(col) for col in frame.columns.tolist()]
        headers, dup_warnings = _unique_headers(raw_headers)
        warnings.extend(f"{sheet_name}: {warning}" for warning in dup_warnings)
        rows = frame.values.tolist()
        parsed[sheet_name] = (headers, rows)
    return parsed, warnings


def parse_excel_rows(
    excel_root: Path,
    only_file_path: str | None,
    sheet_name: str | None = None,
    ingest_all_sheets: bool = False,
) -> tuple[list[SourceDocument], dict[str, Any]]:
    """
    Load one .xlsx under excel_root when only_file_path is set (relative path or file name).
    Returns (documents, summary) for observability.
    """
    summary: dict[str, Any] = {
        "file": None,
        "sheet": None,
        "column_count": 0,
        "data_row_count": 0,
        "skipped_empty_rows": 0,
        "headers_normalized": [],
        "warnings": [],
    }

    if not only_file_path:
        return [], summary

    path = _find_tabular_path(excel_root, only_file_path)
    if path is None:
        summary["warnings"].append(f"No .xlsx/.xls/.csv found under {excel_root} for {only_file_path!r}")
        return [], summary

    summary["file"] = _safe_display_path(path, excel_root)
    suffix = path.suffix.lower()
    docs: list[SourceDocument] = []
    total_skipped = 0
    discovered_sheets: list[str] = []

    if suffix == ".csv":
        headers, rows, warnings = _parse_csv_rows(path)
        summary["warnings"].extend(warnings)
        summary["sheet"] = "csv"
        summary["headers_normalized"] = headers
        summary["column_count"] = len(headers)
        parsed_docs, skipped = _build_documents_from_rows(
            path=path,
            sheet_title="csv",
            headers=headers,
            rows=rows,
            excel_rel_path=summary["file"],
        )
        docs.extend(parsed_docs)
        total_skipped += skipped
        discovered_sheets.append("csv")
    elif suffix == ".xls":
        parsed_sheets, warnings = _parse_xls_with_pandas(path)
        summary["warnings"].extend(warnings)
        if not parsed_sheets:
            LOGGER.warning("Excel parse yielded no sheets file=%s", path)
            return [], summary
        selected_sheet_names = list(parsed_sheets.keys())
        if sheet_name:
            if sheet_name not in parsed_sheets:
                summary["warnings"].append(f"Sheet {sheet_name!r} not found; available: {selected_sheet_names}")
                return [], summary
            selected_sheet_names = [sheet_name]
        elif not ingest_all_sheets:
            selected_sheet_names = [selected_sheet_names[0]]
        for current_sheet in selected_sheet_names:
            headers, rows = parsed_sheets[current_sheet]
            if not headers:
                continue
            parsed_docs, skipped = _build_documents_from_rows(
                path=path,
                sheet_title=current_sheet,
                headers=headers,
                rows=rows,
                excel_rel_path=summary["file"],
            )
            docs.extend(parsed_docs)
            total_skipped += skipped
            discovered_sheets.append(current_sheet)
            if summary["headers_normalized"] == []:
                summary["headers_normalized"] = headers
                summary["column_count"] = len(headers)
    else:
        # read_only mode reports wrong dimensions for some exports (e.g. Forms); use normal load.
        wb = load_workbook(path, data_only=True)
        try:
            selected_sheets: list[Any]
            if ingest_all_sheets and not sheet_name:
                selected_sheets = [wb[name] for name in wb.sheetnames]
            else:
                ws = _resolve_sheet(wb, sheet_name, summary)
                if ws is None:
                    return [], summary
                selected_sheets = [ws]
            for ws in selected_sheets:
                max_col = ws.max_column or 0
                max_row = ws.max_row or 0
                if max_col < 1 or max_row < 1:
                    summary["warnings"].append(f"Worksheet is empty: {ws.title}")
                    continue
                header_row = [ws.cell(row=1, column=col).value for col in range(1, max_col + 1)]
                raw_headers = [str(c) if c is not None else "" for c in header_row]
                headers, dup_warnings = _unique_headers(raw_headers)
                summary["warnings"].extend(f"{ws.title}: {warning}" for warning in dup_warnings)
                parsed_docs, skipped = _build_sheet_documents(path, ws, headers, summary["file"])
                docs.extend(parsed_docs)
                total_skipped += skipped
                discovered_sheets.append(ws.title)
                if summary["headers_normalized"] == []:
                    summary["headers_normalized"] = headers
                    summary["column_count"] = len(headers)
        finally:
            wb.close()

    if discovered_sheets:
        summary["sheet"] = discovered_sheets[0] if len(discovered_sheets) == 1 else ",".join(discovered_sheets)
    summary["data_row_count"] = len(docs)
    summary["skipped_empty_rows"] = total_skipped
    summary["sheet_count"] = len(discovered_sheets)
    summary["detected_file_type"] = suffix
    return docs, summary
