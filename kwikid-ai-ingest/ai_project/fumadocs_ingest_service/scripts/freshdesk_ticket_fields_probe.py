from __future__ import annotations

import argparse
import json
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:  # noqa: BLE001
    load_dotenv = None


STATUS_MAP = {2: "open", 3: "pending", 4: "resolved", 5: "closed"}
PRIORITY_MAP = {1: "low", 2: "medium", 3: "high", 4: "urgent"}


def _load_env_file_fallback() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


if load_dotenv:
    load_dotenv()
else:
    _load_env_file_fallback()


def _require_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _normalize_domain(domain: str) -> str:
    normalized = domain.strip().rstrip("/")
    if not normalized.startswith(("http://", "https://")):
        normalized = f"https://{normalized}"
    return normalized


def _parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _status_label(value: Any) -> str:
    try:
        return STATUS_MAP[int(value)]
    except (TypeError, ValueError, KeyError):
        return str(value or "unknown")


def _priority_label(value: Any) -> str:
    try:
        return PRIORITY_MAP[int(value)]
    except (TypeError, ValueError, KeyError):
        return str(value or "unknown")


def _retry_get(
    client: httpx.Client,
    *,
    url: str,
    params: dict[str, object],
    max_retries: int,
    base_delay_s: float,
) -> httpx.Response:
    attempt = 0
    while True:
        response = client.get(url, params=params)
        if response.status_code < 400:
            return response
        if response.status_code not in {408, 409, 429, 500, 502, 503, 504}:
            response.raise_for_status()
        if attempt >= max_retries:
            response.raise_for_status()
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                wait_s = max(base_delay_s, float(retry_after))
            except ValueError:
                wait_s = base_delay_s
        else:
            wait_s = base_delay_s * (2**attempt)
        wait_s += random.uniform(0.0, min(1.0, base_delay_s))
        time.sleep(wait_s)
        attempt += 1


def _matches_filters(
    ticket: dict[str, Any],
    *,
    updated_until: datetime | None,
    ticket_types: set[str],
    requester_ids: set[int],
    responder_ids: set[int],
    group_ids: set[int],
    statuses: set[str],
    priorities: set[str],
) -> bool:
    checks = [
        _match_updated_until(ticket, updated_until),
        _match_type(ticket, ticket_types),
        _match_int_field(ticket, "requester_id", requester_ids),
        _match_int_field(ticket, "responder_id", responder_ids),
        _match_int_field(ticket, "group_id", group_ids),
        _match_status(ticket, statuses),
        _match_priority(ticket, priorities),
    ]
    return all(checks)


def _match_updated_until(ticket: dict[str, Any], updated_until: datetime | None) -> bool:
    if updated_until is None:
        return True
    updated_at = _parse_iso8601(str(ticket.get("updated_at") or ""))
    return updated_at is not None and updated_at <= updated_until


def _match_type(ticket: dict[str, Any], ticket_types: set[str]) -> bool:
    if not ticket_types:
        return True
    t_type = str(ticket.get("type") or "").strip().lower()
    return t_type in ticket_types


def _match_int_field(ticket: dict[str, Any], field_name: str, allowed_ids: set[int]) -> bool:
    if not allowed_ids:
        return True
    value = ticket.get(field_name)
    try:
        return int(value) in allowed_ids
    except (TypeError, ValueError):
        return False


def _match_status(ticket: dict[str, Any], statuses: set[str]) -> bool:
    if not statuses:
        return True
    return _status_label(ticket.get("status")).lower() in statuses


def _match_priority(ticket: dict[str, Any], priorities: set[str]) -> bool:
    if not priorities:
        return True
    return _priority_label(ticket.get("priority")).lower() in priorities


def _to_str_set(values: list[str] | None) -> set[str]:
    if not values:
        return set()
    normalized = [v.strip().lower() for v in values if v]
    return {v for v in normalized if v}


def _to_int_set(values: list[int] | None) -> set[int]:
    if not values:
        return set()
    return {int(v) for v in values}


def _collect_fields(records: list[dict[str, Any]]) -> list[str]:
    fields: set[str] = set()
    for item in records:
        fields.update(item.keys())
    return sorted(fields)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch Freshdesk tickets and export discovered ticket fields.")
    parser.add_argument("--updated-since", default=None, help="ISO-8601 lower bound for Freshdesk API updated_since")
    parser.add_argument("--updated-until", default=None, help="ISO-8601 upper bound (filtered locally)")
    parser.add_argument("--ticket-type", action="append", default=None, help="Ticket type filter; repeatable")
    parser.add_argument("--requester-id", action="append", type=int, default=None, help="Requester/client ID filter; repeatable")
    parser.add_argument("--responder-id", action="append", type=int, default=None, help="Responder/agent ID filter; repeatable")
    parser.add_argument("--group-id", action="append", type=int, default=None, help="Group ID filter; repeatable")
    parser.add_argument("--status", action="append", default=None, help="Status filter (open/pending/resolved/closed); repeatable")
    parser.add_argument("--priority", action="append", default=None, help="Priority filter (low/medium/high/urgent); repeatable")
    parser.add_argument("--page-size", type=int, default=100, help="Freshdesk per_page (1-100)")
    parser.add_argument("--max-pages", type=int, default=5, help="Maximum pages to fetch")
    parser.add_argument("--max-retries", type=int, default=4, help="Retry count for transient errors")
    parser.add_argument("--retry-base-delay-s", type=float, default=1.0, help="Base backoff delay seconds")
    parser.add_argument("--out", default="./data/freshdesk_probe.json", help="Output JSON path for fetched tickets")
    return parser


@dataclass(frozen=True)
class _FetchOptions:
    updated_since: str | None
    updated_until_dt: datetime | None
    ticket_types: set[str]
    requester_ids: set[int]
    responder_ids: set[int]
    group_ids: set[int]
    statuses: set[str]
    priorities: set[str]
    page_size: int
    max_pages: int
    max_retries: int
    retry_base_delay_s: float


def _fetch_filtered_tickets(
    *,
    endpoint: str,
    api_key: str,
    options: _FetchOptions,
) -> tuple[list[dict[str, Any]], int]:
    all_tickets: list[dict[str, Any]] = []
    raw_total = 0
    with httpx.Client(auth=(api_key, "X"), timeout=30) as client:
        for page in range(1, options.max_pages + 1):
            params: dict[str, object] = {"page": page, "per_page": options.page_size}
            if options.updated_since:
                params["updated_since"] = options.updated_since
            response = _retry_get(
                client,
                url=endpoint,
                params=params,
                max_retries=options.max_retries,
                base_delay_s=options.retry_base_delay_s,
            )
            payload = response.json()
            if not isinstance(payload, list):
                raise RuntimeError("Freshdesk /tickets response must be a list.")
            raw_total += len(payload)
            if not payload:
                break
            valid = [item for item in payload if isinstance(item, dict)]
            filtered = [
                ticket
                for ticket in valid
                if _matches_filters(
                    ticket,
                    updated_until=options.updated_until_dt,
                    ticket_types=options.ticket_types,
                    requester_ids=options.requester_ids,
                    responder_ids=options.responder_ids,
                    group_ids=options.group_ids,
                    statuses=options.statuses,
                    priorities=options.priorities,
                )
            ]
            all_tickets.extend(filtered)
            if len(payload) < options.page_size:
                break
    return all_tickets, raw_total


def _print_suggestions(args: argparse.Namespace, domain: str) -> None:
    print("Suggested .env config:")
    print("FRESHDESK_ENABLED=true")
    print(f"FRESHDESK_DOMAIN={domain.replace('https://', '').replace('http://', '')}")
    print("FRESHDESK_API_KEY=<your_api_key>")
    print(f"FRESHDESK_UPDATED_SINCE={args.updated_since or ''}")
    print("")
    print("Suggested curl payload for /ingest:")
    payload = {
        "full_reindex": False,
        "freshdesk_updated_since": args.updated_since,
        "freshdesk_updated_until": args.updated_until,
        "freshdesk_ticket_types": args.ticket_type or [],
        "freshdesk_requester_ids": args.requester_id or [],
        "freshdesk_responder_ids": args.responder_id or [],
        "freshdesk_group_ids": args.group_id or [],
        "freshdesk_statuses": args.status or [],
        "freshdesk_priorities": args.priority or [],
    }
    print(json.dumps(payload, indent=2))
    print("")
    print("Example curl:")
    compact_payload = json.dumps(payload, separators=(",", ":"))
    print('curl -sS -X POST "http://localhost:8000/ingest" -H "Content-Type: application/json" -d \'' + compact_payload + "'")


def main() -> int:
    args = _build_parser().parse_args()
    domain = _normalize_domain(_require_env("FRESHDESK_DOMAIN"))
    api_key = _require_env("FRESHDESK_API_KEY")
    endpoint = f"{domain}/api/v2/tickets"

    updated_until_dt = _parse_iso8601(args.updated_until)
    ticket_types = _to_str_set(args.ticket_type)
    requester_ids = _to_int_set(args.requester_id)
    responder_ids = _to_int_set(args.responder_id)
    group_ids = _to_int_set(args.group_id)
    statuses = _to_str_set(args.status)
    priorities = _to_str_set(args.priority)
    options = _FetchOptions(
        updated_since=args.updated_since,
        updated_until_dt=updated_until_dt,
        ticket_types=ticket_types,
        requester_ids=requester_ids,
        responder_ids=responder_ids,
        group_ids=group_ids,
        statuses=statuses,
        priorities=priorities,
        page_size=args.page_size,
        max_pages=args.max_pages,
        max_retries=args.max_retries,
        retry_base_delay_s=args.retry_base_delay_s,
    )

    all_tickets, raw_total = _fetch_filtered_tickets(
        endpoint=endpoint,
        api_key=api_key,
        options=options,
    )

    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(all_tickets, indent=2, ensure_ascii=True), encoding="utf-8")

    fields = _collect_fields(all_tickets)
    print(f"Fetched tickets (raw): {raw_total}")
    print(f"Tickets after filters: {len(all_tickets)}")
    print(f"Output file: {out_path}")
    print("")
    print("Discovered Freshdesk ticket fields:")
    print(", ".join(fields) if fields else "(none)")
    print("")
    _print_suggestions(args, domain)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
