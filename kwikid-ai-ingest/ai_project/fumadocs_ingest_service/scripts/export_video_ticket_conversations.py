from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:  # noqa: BLE001
    load_dotenv = None


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
    cleaned = domain.strip().rstrip("/")
    if not cleaned.startswith(("http://", "https://")):
        cleaned = f"https://{cleaned}"
    return cleaned


def _to_serializable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _to_serializable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_serializable(v) for v in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:  # noqa: BLE001
            return str(value)
    if pd.isna(value):
        return None
    return value


def _request_with_retries(
    client: httpx.Client,
    *,
    url: str,
    params: dict[str, Any] | None,
    max_retries: int,
    retry_delay_s: float,
) -> httpx.Response:
    attempt = 0
    while True:
        response = client.get(url, params=params)
        if response.status_code < 400:
            return response
        if response.status_code in {401, 403, 404}:
            response.raise_for_status()
        if response.status_code not in {408, 409, 429, 500, 502, 503, 504}:
            response.raise_for_status()
        if attempt >= max_retries:
            response.raise_for_status()
        sleep_s = retry_delay_s * (2**attempt)
        time.sleep(sleep_s)
        attempt += 1


def _read_excel_rows(excel_path: Path) -> tuple[list[int], dict[int, dict[str, Any]]]:
    df = pd.read_excel(excel_path)
    if "Ticket ID" not in df.columns:
        raise ValueError("Expected 'Ticket ID' column in Excel.")

    ticket_ids: list[int] = []
    ticket_context: dict[int, dict[str, Any]] = {}
    for _, row in df.iterrows():
        raw_id = row.get("Ticket ID")
        if pd.isna(raw_id):
            continue
        try:
            ticket_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if ticket_id not in ticket_ids:
            ticket_ids.append(ticket_id)
        ticket_context[ticket_id] = _to_serializable(row.to_dict())
    return ticket_ids, ticket_context


def _fetch_ticket_details(
    client: httpx.Client,
    *,
    domain: str,
    ticket_id: int,
    max_retries: int,
    retry_delay_s: float,
) -> dict[str, Any]:
    response = _request_with_retries(
        client,
        url=f"{domain}/api/v2/tickets/{ticket_id}",
        params=None,
        max_retries=max_retries,
        retry_delay_s=retry_delay_s,
    )
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _fetch_ticket_conversations(
    client: httpx.Client,
    *,
    domain: str,
    ticket_id: int,
    max_retries: int,
    retry_delay_s: float,
) -> list[dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []
    page_size = 100
    for page in range(1, 101):
        response = _request_with_retries(
            client,
            url=f"{domain}/api/v2/tickets/{ticket_id}/conversations",
            params={"page": page, "per_page": page_size},
            max_retries=max_retries,
            retry_delay_s=retry_delay_s,
        )
        payload = response.json()
        if not isinstance(payload, list):
            break
        rows = [item for item in payload if isinstance(item, dict)]
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
    return all_rows


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export Freshdesk ticket details + conversations for ticket IDs in video issue Excel."
    )
    parser.add_argument(
        "--excel",
        default="../../../../video related queries excel sheet.xlsx",
        help="Path to source Excel file containing Ticket ID column.",
    )
    parser.add_argument(
        "--out",
        default="./data/exports/video_related_freshdesk_conversations.json",
        help="Output JSON path.",
    )
    parser.add_argument("--max-tickets", type=int, default=0, help="Limit number of tickets (0 = all).")
    parser.add_argument("--max-retries", type=int, default=4, help="Max retries for transient errors.")
    parser.add_argument("--retry-delay-s", type=float, default=0.8, help="Base retry delay in seconds.")
    parser.add_argument("--delay-between-tickets-s", type=float, default=0.0, help="Optional sleep per ticket.")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    excel_path = Path(args.excel).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    domain = _normalize_domain(_require_env("FRESHDESK_DOMAIN"))
    api_key = _require_env("FRESHDESK_API_KEY")

    ticket_ids, ticket_context = _read_excel_rows(excel_path)
    if args.max_tickets and args.max_tickets > 0:
        ticket_ids = ticket_ids[: args.max_tickets]

    exported: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    with httpx.Client(auth=(api_key, "X"), timeout=30) as client:
        total = len(ticket_ids)
        for idx, ticket_id in enumerate(ticket_ids, start=1):
            print(f"[{idx}/{total}] Fetching ticket {ticket_id} ...")
            try:
                ticket = _fetch_ticket_details(
                    client,
                    domain=domain,
                    ticket_id=ticket_id,
                    max_retries=args.max_retries,
                    retry_delay_s=args.retry_delay_s,
                )
                conversations = _fetch_ticket_conversations(
                    client,
                    domain=domain,
                    ticket_id=ticket_id,
                    max_retries=args.max_retries,
                    retry_delay_s=args.retry_delay_s,
                )
                exported.append(
                    {
                        "ticket_id": ticket_id,
                        "excel_context": ticket_context.get(ticket_id, {}),
                        "ticket": _to_serializable(ticket),
                        "conversations": _to_serializable(conversations),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                failures.append({"ticket_id": ticket_id, "error": str(exc)})
            if args.delay_between_tickets_s > 0:
                time.sleep(args.delay_between_tickets_s)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source_excel": str(excel_path),
        "ticket_count_requested": len(ticket_ids),
        "ticket_count_exported": len(exported),
        "failure_count": len(failures),
        "tickets": exported,
        "failures": failures,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    print(f"Exported: {len(exported)} tickets")
    print(f"Failures: {len(failures)}")
    print(f"Output: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

