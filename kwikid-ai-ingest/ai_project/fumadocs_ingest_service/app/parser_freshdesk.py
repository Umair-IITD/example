from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any

import httpx

from app.chunker import SourceDocument

LOGGER = logging.getLogger(__name__)

HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
# Image / media URL extraction from ticket HTML (Freshdesk embeds <img>, links, markdown).
IMG_SRC_RE = re.compile(r"<img[^>]+?src\s*=\s*[\"']([^\"'>]+)[\"']", re.IGNORECASE)
IMG_SRCSET_RE = re.compile(r"<img[^>]+?srcset\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
# Strip script/style before parsing body text.
SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
BLOCK_BREAK_RE = re.compile(
    r"</(p|div|tr|h[1-6]|li|table|section|article|blockquote)\s*>|<br\s*/?>",
    re.IGNORECASE,
)
STRUCTURED_DESC_JSON_MAX = 12000
DATA_URI_PREFIX = "data:"
IMAGE_FILE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp")

STATUS_MAP = {
    2: "open",
    3: "pending",
    4: "resolved",
    5: "closed",
}

PRIORITY_MAP = {
    1: "low",
    2: "medium",
    3: "high",
    4: "urgent",
}


@dataclass(frozen=True)
class FreshdeskTicketFilters:
    updated_until: str | None = None
    ticket_types: list[str] | None = None
    requester_ids: list[int] | None = None
    responder_ids: list[int] | None = None
    group_ids: list[int] | None = None
    statuses: list[str] | None = None
    priorities: list[str] | None = None


@dataclass(frozen=True)
class _PreparedFilters:
    updated_until_dt: datetime | None
    ticket_types_set: set[str]
    requester_ids_set: set[int]
    responder_ids_set: set[int]
    group_ids_set: set[int]
    statuses_set: set[str]
    priorities_set: set[str]


def _clean_text(value: Any) -> str:
    text = str(value or "")
    text = HTML_TAG_RE.sub(" ", text)
    text = unescape(text)
    text = WHITESPACE_RE.sub(" ", text)
    return text.strip()


def _dedupe_preserve_order(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in urls:
        u = (raw or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _is_skippable_data_uri(url: str) -> bool:
    return url.lower().startswith(DATA_URI_PREFIX)


def _collect_img_src_urls(html: str) -> list[str]:
    out: list[str] = []
    for match in IMG_SRC_RE.finditer(html):
        u = unescape(match.group(1).strip().strip('"').strip("'"))
        if u and not _is_skippable_data_uri(u):
            out.append(u)
    return out


def _collect_srcset_urls(html: str) -> list[str]:
    out: list[str] = []
    for match in IMG_SRCSET_RE.finditer(html):
        for part in match.group(1).split(","):
            token = part.strip().split()[0] if part.strip() else ""
            u = unescape(token.strip().strip('"').strip("'"))
            if u and not _is_skippable_data_uri(u):
                out.append(u)
    return out


def _collect_markdown_image_urls(html: str) -> list[str]:
    out: list[str] = []
    for match in MD_IMAGE_RE.finditer(html):
        u = match.group(1).strip().strip('"').strip("'")
        if u and not _is_skippable_data_uri(u):
            out.append(u)
    return out


def _collect_image_href_urls(html: str) -> list[str]:
    out: list[str] = []
    for match in re.finditer(
        r'<a[^>]+?href\s*=\s*["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    ):
        u = unescape(match.group(1).strip())
        if _is_skippable_data_uri(u):
            continue
        path = u.lower().split("?", 1)[0]
        if path.endswith(IMAGE_FILE_SUFFIXES):
            out.append(u)
    return out


def _extract_image_urls_from_html(html: str) -> list[str]:
    """Collect image/media URLs from HTML img src, srcset, and markdown image syntax."""
    if not html or not html.strip():
        return []
    collected: list[str] = []
    collected.extend(_collect_img_src_urls(html))
    collected.extend(_collect_srcset_urls(html))
    collected.extend(_collect_markdown_image_urls(html))
    collected.extend(_collect_image_href_urls(html))
    return _dedupe_preserve_order(collected)


def _html_to_plain_body(html: str) -> str:
    """Convert HTML ticket body to plain text with paragraph breaks preserved."""
    if not html or not html.strip():
        return ""
    text = SCRIPT_RE.sub(" ", html)
    text = STYLE_RE.sub(" ", text)
    text = BLOCK_BREAK_RE.sub("\n\n", text)
    text = HTML_TAG_RE.sub(" ", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _normalize_plain_description(value: Any) -> str:
    """Plain-text description field: normalize whitespace, no HTML stripping."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = unescape(text)
    return WHITESPACE_RE.sub(" ", text).strip()


def _structured_description_extras(ticket: dict[str, Any]) -> tuple[str, list[str]]:
    """Return optional text block + extra image URLs from structured_description."""
    sd = ticket.get("structured_description")
    extra_urls: list[str] = []
    block = ""
    if sd is None:
        return "", extra_urls
    if isinstance(sd, str):
        s = sd.strip()
        if not s:
            return "", extra_urls
        if "<" in s and ">" in s:
            extra_urls = _extract_image_urls_from_html(s)
            block = _html_to_plain_body(s)
        else:
            block = _normalize_plain_description(s)
        return block, extra_urls
    if isinstance(sd, dict):
        try:
            raw = json.dumps(sd, ensure_ascii=False)
        except (TypeError, ValueError):
            raw = str(sd)
        if len(raw) > STRUCTURED_DESC_JSON_MAX:
            raw = raw[:STRUCTURED_DESC_JSON_MAX] + "\n... [truncated]"
        block = f"Structured description (JSON):\n{raw}"
        extra_urls = _extract_image_urls_from_html(raw)
        return block, extra_urls
    block = str(sd).strip()
    return block, _extract_image_urls_from_html(block)


def _attachment_urls_from_ticket(ticket: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for att in ticket.get("attachments") or []:
        if not isinstance(att, dict):
            continue
        for key in ("attachment_url", "url", "content_url"):
            u = att.get(key)
            if isinstance(u, str) and u.strip():
                out.append(u.strip())
                break
    return _dedupe_preserve_order(out)


def _collapse_ws(value: str) -> str:
    return WHITESPACE_RE.sub(" ", value).strip()


def _safe_json_dump(value: Any, *, max_len: int | None = None) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        raw = str(value)
    if max_len is not None and len(raw) > max_len:
        return raw[:max_len] + "\n... [truncated]"
    return raw


def _build_ticket_body_content(ticket: dict[str, Any]) -> tuple[str, list[str]]:
    """
    Full semantic body: plain description, HTML-derived text, structured_description,
    and all discovered image/media URLs for embedding + metadata.
    """
    html_raw = str(ticket.get("description") or "").strip()
    plain_api = _normalize_plain_description(ticket.get("description_text"))
    html_plain = _html_to_plain_body(html_raw) if html_raw else ""
    urls = _extract_image_urls_from_html(html_raw)

    struct_block, struct_urls = _structured_description_extras(ticket)
    urls.extend(struct_urls)
    urls.extend(_attachment_urls_from_ticket(ticket))
    urls = _dedupe_preserve_order(urls)

    parts: list[str] = []
    plain_c = _collapse_ws(plain_api) if plain_api else ""
    html_c = _collapse_ws(html_plain) if html_plain else ""

    if html_plain:
        if plain_c and plain_c == html_c:
            parts.append(f"Description:\n{html_plain}")
        elif plain_c and plain_c in html_c:
            parts.append(f"Description:\n{html_plain}")
        elif plain_api:
            parts.append(f"Description (plain): {plain_api}")
            parts.append(f"Description (from HTML):\n{html_plain}")
        else:
            parts.append(f"Description:\n{html_plain}")
    elif plain_api:
        parts.append(f"Description:\n{plain_api}")

    if struct_block:
        parts.append(struct_block)

    if urls:
        lines = "\n".join(f"- {u}" for u in urls)
        parts.append(f"Image and media URLs (for retrieval):\n{lines}")

    body = "\n\n".join(p for p in parts if p.strip()).strip()
    return body, urls


def _conversation_body_text(conversation: dict[str, Any]) -> str:
    html_raw = str(conversation.get("body") or "").strip()
    plain_raw = _normalize_plain_description(conversation.get("body_text"))
    html_plain = _html_to_plain_body(html_raw) if html_raw else ""
    if html_plain and plain_raw:
        if _collapse_ws(html_plain) == _collapse_ws(plain_raw):
            return html_plain
        return f"{plain_raw}\n\n{html_plain}"
    return html_plain or plain_raw


def _build_conversations_block(conversations: list[dict[str, Any]]) -> tuple[str, list[str]]:
    if not conversations:
        return "", []
    lines: list[str] = []
    all_urls: list[str] = []
    for idx, conversation in enumerate(conversations, start=1):
        header_bits = _conversation_header_bits(conversation, idx=idx)
        lines.append(" | ".join(header_bits))
        conv_text = _conversation_body_text(conversation)
        if conv_text:
            lines.append(conv_text)
        attachment_urls, extracted_urls = _conversation_urls(conversation)
        all_urls.extend(attachment_urls)
        all_urls.extend(extracted_urls)
        if attachment_urls:
            lines.append("Attachments:")
            lines.extend(f"- {url}" for url in attachment_urls)
        lines.append("")
    return "\n".join(lines).strip(), _dedupe_preserve_order(all_urls)


def _conversation_header_bits(conversation: dict[str, Any], *, idx: int) -> list[str]:
    created_at = str(conversation.get("created_at") or "")
    conv_id = str(conversation.get("id") or "")
    incoming = bool(conversation.get("incoming", False))
    private = bool(conversation.get("private", False))
    channel = str(conversation.get("channel") or "")
    user_id = conversation.get("user_id")
    source = "incoming" if incoming else "outgoing"
    visibility = "private_note" if private else "public_reply"
    header_bits = [f"Conversation {idx}"]
    if conv_id:
        header_bits.append(f"id={conv_id}")
    header_bits.extend([source, visibility])
    if channel:
        header_bits.append(f"channel={channel}")
    if user_id is not None:
        header_bits.append(f"user_id={user_id}")
    if created_at:
        header_bits.append(f"created_at={created_at}")
    return header_bits


def _conversation_urls(conversation: dict[str, Any]) -> tuple[list[str], list[str]]:
    attachment_urls = _attachment_urls_from_ticket(conversation)
    html_raw = str(conversation.get("body") or "").strip()
    extracted_urls = _extract_image_urls_from_html(html_raw)
    return attachment_urls, extracted_urls


def _build_ticket_fields_block(ticket: dict[str, Any]) -> str:
    fields_json = _safe_json_dump(ticket)
    return f"Ticket fields (JSON):\n{fields_json}"


def _build_conversation_fields_block(conversations: list[dict[str, Any]]) -> str:
    if not conversations:
        return ""
    conversations_json = _safe_json_dump(conversations)
    return f"Ticket conversations and notes (JSON):\n{conversations_json}"


def _status_label(value: Any) -> str:
    try:
        return STATUS_MAP[int(value)]
    except (KeyError, TypeError, ValueError):
        return str(value or "unknown")


def _priority_label(value: Any) -> str:
    try:
        return PRIORITY_MAP[int(value)]
    except (KeyError, TypeError, ValueError):
        return str(value or "unknown")


def _ticket_url(domain: str, ticket_id: str) -> str:
    normalized_domain = domain.strip().rstrip("/")
    if not normalized_domain.startswith(("http://", "https://")):
        normalized_domain = f"https://{normalized_domain}"
    return f"{normalized_domain}/a/tickets/{ticket_id}"


def _freshdesk_retry_wait_s(
    *,
    status_code: int,
    retry_after_header: str | None,
    attempt: int,
    base_delay_s: float,
    min_wait_on_429_s: float,
) -> float:
    """Honor Retry-After (delta-seconds or HTTP-date); add jitter; avoid sub-second 429 storms."""
    exponential = base_delay_s * (2**attempt)
    raw = (retry_after_header or "").strip()
    wait_s: float
    if raw:
        try:
            wait_s = float(raw)
            if wait_s < 1.0:
                wait_s = exponential
        except ValueError:
            try:
                dt = parsedate_to_datetime(raw)
                if dt is None:
                    wait_s = exponential
                else:
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    wait_s = (dt - datetime.now(UTC)).total_seconds()
                    if wait_s < 1.0:
                        wait_s = exponential
            except (TypeError, ValueError, OSError):
                wait_s = exponential
    else:
        wait_s = exponential

    wait_s = max(wait_s, base_delay_s)
    wait_s += random.uniform(0.0, min(1.0, base_delay_s))
    if status_code == 429:
        wait_s = max(wait_s, min_wait_on_429_s)
    return wait_s


def _request_with_retries(
    client: httpx.Client,
    *,
    endpoint: str,
    params: dict[str, object],
    max_retries: int,
    base_delay_s: float,
    min_wait_on_429_s: float = 25.0,
) -> httpx.Response:
    attempt = 0
    while True:
        response = client.get(endpoint, params=params)
        if response.status_code < 400:
            return response
        if response.status_code in {401, 403}:
            response.raise_for_status()
        if response.status_code not in {408, 409, 429, 500, 502, 503, 504}:
            response.raise_for_status()
        if attempt >= max_retries:
            response.raise_for_status()

        wait_s = _freshdesk_retry_wait_s(
            status_code=response.status_code,
            retry_after_header=response.headers.get("Retry-After"),
            attempt=attempt,
            base_delay_s=base_delay_s,
            min_wait_on_429_s=min_wait_on_429_s,
        )
        LOGGER.warning(
            "Freshdesk request failed (%s). Retrying in %.2fs (attempt %s/%s).",
            response.status_code,
            wait_s,
            attempt + 1,
            max_retries + 1,
        )
        time.sleep(wait_s)
        attempt += 1


def _build_source_document(
    *,
    ticket: dict[str, Any],
    conversations: list[dict[str, Any]],
    domain: str,
    ingest_run_ts: str,
    freshdesk_sync_cursor: str | None,
) -> SourceDocument | None:
    ticket_id = ticket.get("id")
    if ticket_id is None:
        LOGGER.warning("Skipping Freshdesk ticket without id.")
        return None
    ticket_id_str = str(ticket_id)

    subject = _clean_text(ticket.get("subject"))
    body_block, image_urls = _build_ticket_body_content(ticket)
    conversation_block, conversation_urls = _build_conversations_block(conversations)
    image_urls = _dedupe_preserve_order([*image_urls, *conversation_urls])
    ticket_fields_block = _build_ticket_fields_block(ticket)
    conversation_fields_block = _build_conversation_fields_block(conversations)
    tags = [str(tag).strip() for tag in (ticket.get("tags") or []) if str(tag).strip()]
    status = _status_label(ticket.get("status"))
    priority = _priority_label(ticket.get("priority"))
    ticket_type = str(ticket.get("type") or "").strip() or None
    ticket_url = _ticket_url(domain, ticket_id_str)

    parts = _build_ticket_content_parts(
        subject=subject,
        ticket_url=ticket_url,
        body_block=body_block,
        conversation_block=conversation_block,
        ticket_fields_block=ticket_fields_block,
        conversation_fields_block=conversation_fields_block,
        tags=tags,
        status=status,
        priority=priority,
        ticket_type=ticket_type,
    )
    content = "\n\n".join(part for part in parts if part.strip()).strip()
    if not content:
        LOGGER.info("Skipping Freshdesk ticket %s because normalized content is empty.", ticket_id_str)
        return None

    created_at = ticket.get("created_at")
    updated_at = ticket.get("updated_at")
    metadata = _build_ticket_metadata(
        ticket=ticket,
        ticket_id_str=ticket_id_str,
        ticket_url=ticket_url,
        status=status,
        priority=priority,
        tags=tags,
        created_at=created_at,
        updated_at=updated_at,
        ticket_type=ticket_type,
        ingest_run_ts=ingest_run_ts,
        freshdesk_sync_cursor=freshdesk_sync_cursor,
        image_urls=image_urls,
        conversations=conversations,
    )
    return SourceDocument(
        source_type="freshdesk",
        source_id=ticket_id_str,
        content=content,
        title=subject or f"Freshdesk Ticket {ticket_id_str}",
        heading=subject or None,
        tags=tags,
        creation_date=str(created_at) if created_at is not None else None,
        metadata=metadata,
    )


def _build_ticket_content_parts(
    *,
    subject: str,
    ticket_url: str,
    body_block: str,
    conversation_block: str,
    ticket_fields_block: str,
    conversation_fields_block: str,
    tags: list[str],
    status: str,
    priority: str,
    ticket_type: str | None,
) -> list[str]:
    parts: list[str] = []
    if subject:
        parts.append(f"Subject: {subject}")
    if ticket_url:
        parts.append(f"Ticket Link: {ticket_url}")
    if body_block:
        parts.append(body_block)
    if conversation_block:
        parts.append(f"Conversation and notes:\n{conversation_block}")
    if ticket_fields_block:
        parts.append(ticket_fields_block)
    if conversation_fields_block:
        parts.append(conversation_fields_block)
    if tags:
        parts.append(f"Tags: {', '.join(tags)}")
    parts.extend([f"Status: {status}", f"Priority: {priority}"])
    if ticket_type:
        parts.append(f"Type: {ticket_type}")
    return parts


def _build_ticket_metadata(
    *,
    ticket: dict[str, Any],
    ticket_id_str: str,
    ticket_url: str,
    status: str,
    priority: str,
    tags: list[str],
    created_at: Any,
    updated_at: Any,
    ticket_type: str | None,
    ingest_run_ts: str,
    freshdesk_sync_cursor: str | None,
    image_urls: list[str],
    conversations: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "source_type": "freshdesk",
        "ticket_id": ticket_id_str,
        "ticket_url": ticket_url,
        "ticket_link": ticket_url,
        "post_link": ticket_url,
        "status": status,
        "priority": priority,
        "requester_id": ticket.get("requester_id"),
        "responder_id": ticket.get("responder_id"),
        "group_id": ticket.get("group_id"),
        "tags": tags,
        "created_at": str(created_at) if created_at is not None else None,
        "updated_at": str(updated_at) if updated_at is not None else None,
        "type": ticket_type,
        "ingest_run_ts": ingest_run_ts,
        "freshdesk_sync_cursor": freshdesk_sync_cursor,
        "image_urls": image_urls,
        "image_url_count": len(image_urls),
        "conversation_count": len(conversations),
        "has_private_notes": any(bool(item.get("private", False)) for item in conversations),
        # Keep retrieval-critical metadata compact; avoid per-chunk raw payload bloat.
        "raw_payload_ref": f"freshdesk-ticket:{ticket_id_str}",
        "raw_ticket_id": ticket_id_str,
    }


def _build_ticket_params(page: int, page_size: int, updated_since: str | None) -> dict[str, object]:
    params: dict[str, object] = {"page": page, "per_page": page_size}
    if updated_since:
        params["updated_since"] = updated_since
    return params


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


def _parse_ticket_updated_at(ticket: dict[str, Any]) -> datetime | None:
    updated_at = ticket.get("updated_at")
    if updated_at is None:
        return None
    try:
        return _parse_iso8601(str(updated_at))
    except ValueError:
        return None


def _normalize_str_set(values: list[str] | None) -> set[str]:
    if not values:
        return set()
    return {str(item).strip().lower() for item in values if str(item).strip()}


def _normalize_status_set(values: list[str] | None) -> set[str]:
    raw_values = _normalize_str_set(values)
    normalized: set[str] = set()
    for raw in raw_values:
        if raw.isdigit():
            mapped = STATUS_MAP.get(int(raw))
            if mapped:
                normalized.add(mapped.lower())
                continue
        normalized.add(raw)
    return normalized


def _normalize_priority_set(values: list[str] | None) -> set[str]:
    raw_values = _normalize_str_set(values)
    normalized: set[str] = set()
    for raw in raw_values:
        if raw.isdigit():
            mapped = PRIORITY_MAP.get(int(raw))
            if mapped:
                normalized.add(mapped.lower())
                continue
        normalized.add(raw)
    return normalized


def _normalize_int_set(values: list[int] | None) -> set[int]:
    if not values:
        return set()
    return {int(item) for item in values}


def _build_prepared_filters(filters: FreshdeskTicketFilters | None) -> _PreparedFilters:
    if filters is None:
        filters = FreshdeskTicketFilters()
    return _PreparedFilters(
        updated_until_dt=_parse_iso8601(filters.updated_until),
        ticket_types_set=_normalize_str_set(filters.ticket_types),
        requester_ids_set=_normalize_int_set(filters.requester_ids),
        responder_ids_set=_normalize_int_set(filters.responder_ids),
        group_ids_set=_normalize_int_set(filters.group_ids),
        statuses_set=_normalize_status_set(filters.statuses),
        priorities_set=_normalize_priority_set(filters.priorities),
    )


def _matches_int_field(ticket: dict[str, Any], field_name: str, allowed_values: set[int]) -> bool:
    if not allowed_values:
        return True
    value = ticket.get(field_name)
    if value is None:
        return False
    try:
        return int(value) in allowed_values
    except (TypeError, ValueError):
        return False


def _matches_updated_until(ticket: dict[str, Any], updated_until_dt: datetime | None) -> bool:
    if updated_until_dt is None:
        return True
    ticket_updated_at = _parse_ticket_updated_at(ticket)
    return ticket_updated_at is not None and ticket_updated_at <= updated_until_dt


def _matches_ticket_type(ticket: dict[str, Any], allowed_types: set[str]) -> bool:
    if not allowed_types:
        return True
    ticket_type = str(ticket.get("type") or "").strip().lower()
    return ticket_type in allowed_types


def _matches_status(ticket: dict[str, Any], allowed_statuses: set[str]) -> bool:
    if not allowed_statuses:
        return True
    ticket_status = _status_label(ticket.get("status")).strip().lower()
    return ticket_status in allowed_statuses


def _matches_priority(ticket: dict[str, Any], allowed_priorities: set[str]) -> bool:
    if not allowed_priorities:
        return True
    ticket_priority = _priority_label(ticket.get("priority")).strip().lower()
    return ticket_priority in allowed_priorities


def _ticket_passes_filters(
    ticket: dict[str, Any],
    *,
    filters: _PreparedFilters,
) -> bool:
    return all(
        [
            _matches_updated_until(ticket, filters.updated_until_dt),
            _matches_ticket_type(ticket, filters.ticket_types_set),
            _matches_int_field(ticket, "requester_id", filters.requester_ids_set),
            _matches_int_field(ticket, "responder_id", filters.responder_ids_set),
            _matches_int_field(ticket, "group_id", filters.group_ids_set),
            _matches_status(ticket, filters.statuses_set),
            _matches_priority(ticket, filters.priorities_set),
        ]
    )


def _fetch_tickets_page(
    client: httpx.Client,
    *,
    endpoint: str,
    page: int,
    page_size: int,
    updated_since: str | None,
    max_retries: int,
    retry_base_delay_s: float,
    min_wait_on_429_s: float = 25.0,
) -> tuple[list[dict[str, Any]], int]:
    response = _request_with_retries(
        client,
        endpoint=endpoint,
        params=_build_ticket_params(page, page_size, updated_since),
        max_retries=max_retries,
        base_delay_s=retry_base_delay_s,
        min_wait_on_429_s=min_wait_on_429_s,
    )
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("Freshdesk tickets response must be a JSON list.")
    LOGGER.info("Fetched Freshdesk page=%s count=%s", page, len(payload))
    return [ticket for ticket in payload if isinstance(ticket, dict)], len(payload)


def _fetch_ticket_conversations(
    client: httpx.Client,
    *,
    endpoint_base: str,
    ticket_id: str,
    max_retries: int,
    retry_base_delay_s: float,
    min_wait_on_429_s: float = 25.0,
    request_spacing_s: float = 0.0,
) -> list[dict[str, Any]]:
    endpoint = f"{endpoint_base}/{ticket_id}/conversations"
    all_rows: list[dict[str, Any]] = []
    page_size = 100
    for page in range(1, 101):
        if page > 1 and request_spacing_s > 0:
            time.sleep(request_spacing_s)
        response = _request_with_retries(
            client,
            endpoint=endpoint,
            params={"page": page, "per_page": page_size},
            max_retries=max_retries,
            base_delay_s=retry_base_delay_s,
            min_wait_on_429_s=min_wait_on_429_s,
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


def _fetch_ticket_details(
    client: httpx.Client,
    *,
    endpoint_base: str,
    ticket_id: str,
    max_retries: int,
    retry_base_delay_s: float,
    min_wait_on_429_s: float = 25.0,
) -> dict[str, Any]:
    endpoint = f"{endpoint_base}/{ticket_id}"
    response = _request_with_retries(
        client,
        endpoint=endpoint,
        params={},
        max_retries=max_retries,
        base_delay_s=retry_base_delay_s,
        min_wait_on_429_s=min_wait_on_429_s,
    )
    payload = response.json()
    if isinstance(payload, dict):
        return payload
    return {}


def _to_source_documents(
    tickets: list[dict[str, Any]],
    *,
    client: httpx.Client,
    tickets_endpoint_base: str,
    max_retries: int,
    retry_base_delay_s: float,
    min_wait_on_429_s: float = 25.0,
    request_spacing_s: float = 0.0,
    normalized_domain: str,
    ingest_run_ts: str,
    updated_since: str | None,
) -> tuple[list[SourceDocument], int]:
    docs: list[SourceDocument] = []
    skipped = 0
    for idx, ticket in enumerate(tickets):
        ticket_id = ticket.get("id")
        detailed_ticket = ticket
        conversations: list[dict[str, Any]] = []
        if ticket_id is not None:
            if idx > 0 and request_spacing_s > 0:
                time.sleep(request_spacing_s)
            try:
                ticket_details = _fetch_ticket_details(
                    client,
                    endpoint_base=tickets_endpoint_base,
                    ticket_id=str(ticket_id),
                    max_retries=max_retries,
                    retry_base_delay_s=retry_base_delay_s,
                    min_wait_on_429_s=min_wait_on_429_s,
                )
                if ticket_details:
                    detailed_ticket = {**ticket, **ticket_details}
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Failed to fetch details for Freshdesk ticket %s: %s", ticket_id, exc)
            try:
                if request_spacing_s > 0:
                    time.sleep(request_spacing_s)
                conversations = _fetch_ticket_conversations(
                    client,
                    endpoint_base=tickets_endpoint_base,
                    ticket_id=str(ticket_id),
                    max_retries=max_retries,
                    retry_base_delay_s=retry_base_delay_s,
                    min_wait_on_429_s=min_wait_on_429_s,
                    request_spacing_s=request_spacing_s,
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Failed to fetch conversations for Freshdesk ticket %s: %s", ticket_id, exc)
        doc = _build_source_document(
            ticket=detailed_ticket,
            conversations=conversations,
            domain=normalized_domain,
            ingest_run_ts=ingest_run_ts,
            freshdesk_sync_cursor=updated_since,
        )
        if doc is None:
            skipped += 1
            continue
        docs.append(doc)
    return docs, skipped


def _filter_tickets(
    tickets: list[dict[str, Any]],
    *,
    filters: _PreparedFilters,
) -> tuple[list[dict[str, Any]], int]:
    filtered_tickets = [ticket for ticket in tickets if _ticket_passes_filters(ticket, filters=filters)]
    filtered_out = len(tickets) - len(filtered_tickets)
    return filtered_tickets, filtered_out


def _collect_documents_from_page(
    tickets: list[dict[str, Any]],
    *,
    client: httpx.Client,
    tickets_endpoint_base: str,
    max_retries: int,
    retry_base_delay_s: float,
    min_wait_on_429_s: float = 25.0,
    request_spacing_s: float = 0.0,
    prepared_filters: _PreparedFilters,
    normalized_domain: str,
    ingest_run_ts: str,
    updated_since: str | None,
) -> tuple[list[SourceDocument], int, int]:
    filtered_tickets, filtered_out = _filter_tickets(tickets, filters=prepared_filters)
    docs, skipped_count = _to_source_documents(
        filtered_tickets,
        client=client,
        tickets_endpoint_base=tickets_endpoint_base,
        max_retries=max_retries,
        retry_base_delay_s=retry_base_delay_s,
        min_wait_on_429_s=min_wait_on_429_s,
        request_spacing_s=request_spacing_s,
        normalized_domain=normalized_domain,
        ingest_run_ts=ingest_run_ts,
        updated_since=updated_since,
    )
    return docs, skipped_count, filtered_out


def parse_freshdesk_tickets(
    *,
    domain: str,
    api_key: str,
    updated_since: str | None = None,
    ticket_ids: list[int] | None = None,
    filters: FreshdeskTicketFilters | None = None,
    apply_filters_with_ticket_ids: bool = False,
    page_size: int = 100,
    max_pages: int = 100,
    max_retries: int = 4,
    retry_base_delay_s: float = 1.0,
    min_wait_on_429_s: float = 25.0,
    # Seconds between consecutive Freshdesk HTTP calls (ticket-by-ID path / listing enrichment).
    request_spacing_s: float = 3.5,
    timeout_s: int = 30,
) -> list[SourceDocument]:
    normalized_domain = domain.strip().rstrip("/")
    if not normalized_domain.startswith(("http://", "https://")):
        normalized_domain = f"https://{normalized_domain}"
    endpoint = f"{normalized_domain}/api/v2/tickets"
    ingest_run_ts = datetime.now(UTC).isoformat()
    prepared_filters = _build_prepared_filters(filters)
    all_docs: list[SourceDocument] = []
    skipped = 0
    filtered_out = 0
    pages_fetched = 0

    id_list = [int(x) for x in ticket_ids] if ticket_ids else []
    unique_ids: list[int] = []
    seen: set[int] = set()
    for tid in id_list:
        if tid not in seen:
            seen.add(tid)
            unique_ids.append(tid)

    spacing_s = max(0.0, request_spacing_s)

    if unique_ids:
        with httpx.Client(auth=(api_key, "X"), timeout=timeout_s) as client:
            for idx, ticket_id in enumerate(unique_ids):
                if idx > 0 and spacing_s > 0:
                    time.sleep(spacing_s)
                tid_str = str(ticket_id)
                try:
                    ticket_details = _fetch_ticket_details(
                        client,
                        endpoint_base=endpoint,
                        ticket_id=tid_str,
                        max_retries=max_retries,
                        retry_base_delay_s=retry_base_delay_s,
                        min_wait_on_429_s=min_wait_on_429_s,
                    )
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning("Failed to fetch Freshdesk ticket %s: %s", tid_str, exc)
                    skipped += 1
                    continue
                if not ticket_details or ticket_details.get("id") is None:
                    LOGGER.warning("Freshdesk ticket %s missing or empty response.", tid_str)
                    skipped += 1
                    continue
                # In ticket-ID mode, only apply listing filters when explicitly requested.
                if apply_filters_with_ticket_ids and not _ticket_passes_filters(ticket_details, filters=prepared_filters):
                    filtered_out += 1
                    continue
                try:
                    if spacing_s > 0:
                        time.sleep(spacing_s)
                    conversations = _fetch_ticket_conversations(
                        client,
                        endpoint_base=endpoint,
                        ticket_id=tid_str,
                        max_retries=max_retries,
                        retry_base_delay_s=retry_base_delay_s,
                        min_wait_on_429_s=min_wait_on_429_s,
                        request_spacing_s=spacing_s,
                    )
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning("Failed to fetch conversations for Freshdesk ticket %s: %s", tid_str, exc)
                    conversations = []
                doc = _build_source_document(
                    ticket=ticket_details,
                    conversations=conversations,
                    domain=normalized_domain,
                    ingest_run_ts=ingest_run_ts,
                    freshdesk_sync_cursor=updated_since,
                )
                if doc is None:
                    skipped += 1
                    continue
                all_docs.append(doc)

        LOGGER.info(
            "Freshdesk ingest by id prepared records=%s skipped=%s filtered_out=%s ticket_ids=%s updated_since=%s updated_until=%s",
            len(all_docs),
            skipped,
            filtered_out,
            len(unique_ids),
            updated_since or "",
            filters.updated_until if filters else "",
        )
        return all_docs

    with httpx.Client(auth=(api_key, "X"), timeout=timeout_s) as client:
        for page in range(1, max_pages + 1):
            if page > 1 and spacing_s > 0:
                time.sleep(spacing_s)
            tickets, raw_count = _fetch_tickets_page(
                client,
                endpoint=endpoint,
                page=page,
                page_size=page_size,
                updated_since=updated_since,
                max_retries=max_retries,
                retry_base_delay_s=retry_base_delay_s,
                min_wait_on_429_s=min_wait_on_429_s,
            )
            pages_fetched += 1
            if not tickets:
                break

            docs, skipped_count, filtered_count = _collect_documents_from_page(
                tickets,
                client=client,
                tickets_endpoint_base=endpoint,
                max_retries=max_retries,
                retry_base_delay_s=retry_base_delay_s,
                min_wait_on_429_s=min_wait_on_429_s,
                request_spacing_s=spacing_s,
                prepared_filters=prepared_filters,
                normalized_domain=normalized_domain,
                ingest_run_ts=ingest_run_ts,
                updated_since=updated_since,
            )
            all_docs.extend(docs)
            skipped += skipped_count
            filtered_out += filtered_count

            if raw_count < page_size:
                break

    LOGGER.info(
        "Freshdesk ingest prepared records=%s skipped=%s filtered_out=%s pages=%s updated_since=%s updated_until=%s",
        len(all_docs),
        skipped,
        filtered_out,
        pages_fetched,
        updated_since or "",
        filters.updated_until if filters else "",
    )
    return all_docs
