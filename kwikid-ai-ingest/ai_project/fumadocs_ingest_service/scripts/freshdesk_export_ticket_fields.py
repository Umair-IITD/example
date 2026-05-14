#!/usr/bin/env python3
"""
Export Freshdesk ticket field definitions and (optionally) a ticket's populated values.

Uses the same env vars as the n8n workflow:
  FRESHDESK_BASE_URL   e.g. https://yourcompany.freshdesk.com
  FRESHDESK_BASIC_AUTH Basic auth header value (often "Basic <base64(api_key:X)>")

Or:
  FRESHDESK_API_KEY    API key only — script builds Basic auth as api_key + ":X"

Endpoints (Freshdesk v2):
  GET /api/v2/ticket_fields     — all ticket fields (system + custom), choices, required, etc.
  GET /api/v2/tickets/{id}      — optional: one ticket JSON (subject, custom_fields, tags, …)

Examples:
  python scripts/freshdesk_export_ticket_fields.py
  python scripts/freshdesk_export_ticket_fields.py --ticket-id 187870
  python scripts/freshdesk_export_ticket_fields.py --out data/reports/freshdesk_ticket_fields.json
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore[import-not-found]

        load_dotenv()
    except ImportError:
        pass


def _auth_header() -> str:
    basic = (os.getenv("FRESHDESK_BASIC_AUTH") or "").strip()
    if basic:
        return basic if basic.lower().startswith("basic ") else f"Basic {basic}"
    key = (os.getenv("FRESHDESK_API_KEY") or "").strip()
    if not key:
        print(
            "Missing FRESHDESK_BASIC_AUTH or FRESHDESK_API_KEY in environment.",
            file=sys.stderr,
        )
        sys.exit(1)
    token = base64.b64encode(f"{key}:X".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _base_url() -> str:
    raw = (os.getenv("FRESHDESK_BASE_URL") or "https://kwikid.freshdesk.com").strip().rstrip("/")
    return raw


def _request_json(method: str, url: str, headers: dict[str, str]) -> Any:
    req = urllib.request.Request(url, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {e.code} {e.reason} for {url}\n{detail}") from e
    if not body.strip():
        return None
    return json.loads(body)


def _summarize_field(f: dict[str, Any]) -> dict[str, Any]:
    """Flatten one ticket_field record for CSV-like viewing."""
    return {
        "id": f.get("id"),
        "name": f.get("name"),
        "label": f.get("label"),
        "type": f.get("type"),
        "field_type": f.get("field_type"),
        "required_for_agents": f.get("required_for_agents"),
        "required_for_closure": f.get("required_for_closure"),
        "visible_in_portal": f.get("visible_in_portal"),
        "editable_in_portal": f.get("editable_in_portal"),
        "position": f.get("position"),
        "default": f.get("default"),
        "choices": f.get("choices"),
    }


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Export Freshdesk ticket fields and optional ticket snapshot.")
    parser.add_argument("--ticket-id", type=str, default=None, help="If set, also GET /api/v2/tickets/{id}")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write full JSON payload to this file (UTF-8).",
    )
    parser.add_argument("--quiet", action="store_true", help="Only write --out JSON, no stdout summary.")
    args = parser.parse_args()

    base = _base_url()
    headers = {
        "Authorization": _auth_header(),
        "Content-Type": "application/json",
    }

    fields_url = f"{base}/api/v2/ticket_fields"
    ticket_fields = _request_json("GET", fields_url, headers)

    if not isinstance(ticket_fields, list):
        print("Unexpected ticket_fields response (expected list).", file=sys.stderr)
        sys.exit(1)

    ticket: dict[str, Any] | None = None
    if args.ticket_id:
        t_url = f"{base}/api/v2/tickets/{args.ticket_id}"
        ticket = _request_json("GET", t_url, headers)
        if not isinstance(ticket, dict):
            print("Unexpected ticket response.", file=sys.stderr)
            sys.exit(1)

    payload: dict[str, Any] = {
        "freshdesk_base_url": base,
        "ticket_fields_url": fields_url,
        "ticket_fields_count": len(ticket_fields),
        "ticket_fields": ticket_fields,
        "ticket_fields_summary": [_summarize_field(f) for f in ticket_fields if isinstance(f, dict)],
    }
    if ticket is not None:
        payload["sample_ticket_id"] = args.ticket_id
        payload["sample_ticket"] = ticket
        payload["sample_ticket_custom_fields"] = ticket.get("custom_fields") or {}

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        if not args.quiet:
            print(f"Wrote {args.out}")

    if args.quiet:
        return

    print("=== Freshdesk ticket fields (summary) ===")
    for row in payload["ticket_fields_summary"]:
        cid = row.get("choices")
        choices_note = ""
        if isinstance(cid, list) and cid:
            choices_note = f" choices={len(cid)}"
        print(
            f"- id={row.get('id')} name={row.get('name')!r} label={row.get('label')!r} "
            f"type={row.get('type')!r}{choices_note}"
        )

    if ticket:
        print("\n=== Sample ticket (top-level keys) ===")
        for k in sorted(ticket.keys()):
            if k in ("description", "description_text"):
                continue
            print(f"  {k}: {_short(ticket.get(k))}")
        print("\n=== Sample ticket custom_fields ===")
        cf = ticket.get("custom_fields") or {}
        if not cf:
            print("  (none)")
        else:
            for k, v in sorted(cf.items(), key=lambda x: str(x[0])):
                print(f"  {k}: {_short(v)}")


def _short(v: Any, max_len: int = 120) -> str:
    s = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else repr(v)
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


if __name__ == "__main__":
    main()
