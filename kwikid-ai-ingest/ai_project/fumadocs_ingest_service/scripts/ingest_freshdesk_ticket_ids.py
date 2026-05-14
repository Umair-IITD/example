#!/usr/bin/env python3
"""
Load Freshdesk ticket IDs from JSON and run the full ingest pipeline (chunk, embed, Supabase upsert).

Requires the same .env as the FastAPI service:
  FRESHDESK_DOMAIN or config freshdesk_domain
  FRESHDESK_API_KEY
  SUPABASE_URL, SUPABASE_KEY, SUPABASE_TABLE (or equivalents from app.config)

JSON shape: { "ticket_ids": [186158, ...] } or a bare array [186158, ...].

Examples:
  cd fumadocs_ingest_service
  python scripts/ingest_freshdesk_ticket_ids.py
  python scripts/ingest_freshdesk_ticket_ids.py --ids-file path/to/ids.json --full-reindex
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _service_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_ticket_ids(path: Path) -> list[int]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "ticket_ids" in raw:
        raw = raw["ticket_ids"]
    if not isinstance(raw, list):
        raise SystemExit("JSON must be a list of integers or an object with key 'ticket_ids'.")
    out: list[int] = []
    for item in raw:
        try:
            out.append(int(item))
        except (TypeError, ValueError) as exc:
            raise SystemExit(f"Invalid ticket id: {item!r}") from exc
    return out


def main() -> None:
    root = _service_root()
    default_file = root / "data" / "freshdesk_video_issue_ticket_ids.json"
    parser = argparse.ArgumentParser(description="Ingest specific Freshdesk tickets into Supabase.")
    parser.add_argument(
        "--ids-file",
        type=Path,
        default=default_file,
        help=f"JSON file with ticket IDs (default: {default_file})",
    )
    parser.add_argument("--full-reindex", action="store_true", help="Upsert-only refresh flag passed to run_ingest.")
    args = parser.parse_args()

    ids_path = args.ids_file.expanduser().resolve()
    if not ids_path.is_file():
        raise SystemExit(f"IDs file not found: {ids_path}")

    ticket_ids = _load_ticket_ids(ids_path)
    if not ticket_ids:
        raise SystemExit("No ticket IDs to ingest.")

    sys.path.insert(0, str(root))

    from app.config import get_settings
    from app.ingest import run_ingest

    settings = get_settings()
    result = run_ingest(
        settings,
        freshdesk_ticket_ids=ticket_ids,
        full_reindex=args.full_reindex,
    )
    print(result)


if __name__ == "__main__":
    main()
