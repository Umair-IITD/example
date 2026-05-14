"""List Freshdesk contact (requester) IDs whose primary email matches a domain suffix.

Usage (from repo service dir, with .env loaded):
  python scripts/list_freshdesk_contacts_by_email_domain.py --domain-suffix unitybank.co.in

Requires: FRESHDESK_DOMAIN, FRESHDESK_API_KEY in environment or .env
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


def _load_env() -> None:
    if load_dotenv:
        load_dotenv()
        return
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> int:
    parser = argparse.ArgumentParser(description="List Freshdesk contacts by email domain.")
    parser.add_argument(
        "--domain-suffix",
        default="unitybank.co.in",
        help="Match primary email ending with @<suffix> (default: unitybank.co.in)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=500,
        help="Safety cap on contact list pages (100 per page)",
    )
    parser.add_argument(
        "--ids-only",
        action="store_true",
        help="Print only a JSON array of integer requester IDs (for ingest body)",
    )
    args = parser.parse_args()

    _load_env()
    domain = (os.getenv("FRESHDESK_DOMAIN") or "").strip().rstrip("/")
    key = (os.getenv("FRESHDESK_API_KEY") or "").strip()
    if not domain or not key:
        print("Set FRESHDESK_DOMAIN and FRESHDESK_API_KEY in .env", file=sys.stderr)
        return 2

    base = domain if domain.startswith(("http://", "https://")) else f"https://{domain}"
    suffix = args.domain_suffix.strip().lower()
    if not suffix.startswith("@"):
        suffix = "@" + suffix

    matches: list[tuple[int | None, str, str]] = []
    page = 1
    per_page = 100

    with httpx.Client(auth=(key, "X"), timeout=60.0) as client:
        while page <= args.max_pages:
            r = client.get(
                f"{base}/api/v2/contacts",
                params={"page": page, "per_page": per_page},
            )
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list) or not data:
                break
            for c in data:
                if not isinstance(c, dict):
                    continue
                email = (c.get("email") or "").strip().lower()
                cid = c.get("id")
                if email.endswith(suffix):
                    name = (c.get("name") or "").strip()
                    matches.append((int(cid) if cid is not None else None, email, name))
            if len(data) < per_page:
                break
            page += 1

    matches.sort(key=lambda x: (x[1], x[0] or 0))
    if args.ids_only:
        ids = [cid for cid, _, _ in matches if cid is not None]
        print(json.dumps(ids))
        return 0
    print(f"count\t{len(matches)}")
    print("requester_id\temail\tname")
    for cid, em, name in matches:
        print(f"{cid}\t{em}\t{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
