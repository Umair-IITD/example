"""
app/freshdesk_webhook.py

Phase B3: Freshdesk webhook helpers.

Responsibilities:
- Extract normalized ticket info from raw Freshdesk webhook JSON
- Resolve tenant slug from ticket tags / custom fields / default
- Verify optional webhook token
- Build the RAG query string from ticket subject + description
- Format the AI draft as HTML for a private note or public reply
- Post the note/reply back to Freshdesk via the REST API
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
from typing import Any

import httpx

LOGGER = logging.getLogger(__name__)

_CONFIDENCE_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


# ── Payload extraction ─────────────────────────────────────────────────────────

def extract_ticket_info(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize a Freshdesk webhook payload into a flat ticket dict.

    Supports two formats:
      1. Standard Freshdesk webhook wrapper: {"freshdesk_webhook": {...}}
      2. Flat custom payload configured in the automation rule
    """
    body: dict[str, Any] = raw.get("freshdesk_webhook", raw)
    if not isinstance(body, dict):
        body = raw

    raw_tags = body.get("ticket_tags") or body.get("tags") or []
    if isinstance(raw_tags, str):
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
    elif isinstance(raw_tags, list):
        tags = [str(t).strip() for t in raw_tags if str(t).strip()]
    else:
        tags = []

    custom_fields = body.get("custom_fields") or body.get("ticket_custom_fields") or {}
    if not isinstance(custom_fields, dict):
        custom_fields = {}

    return {
        "ticket_id": str(body.get("ticket_id") or body.get("id") or "").strip(),
        "subject": str(body.get("ticket_subject") or body.get("subject") or "").strip(),
        "description": str(body.get("ticket_description") or body.get("description") or "").strip(),
        "description_text": str(body.get("ticket_description_text") or body.get("description_text") or "").strip(),
        "requester_email": str(body.get("requester_email") or body.get("ticket_requester_email") or "").strip().lower(),
        "tags": tags,
        "custom_fields": custom_fields,
        "status": str(body.get("ticket_status") or body.get("status") or ""),
        "priority": str(body.get("ticket_priority") or body.get("priority") or ""),
    }


# ── Tenant resolution ──────────────────────────────────────────────────────────

def resolve_tenant(
    ticket: dict[str, Any],
    *,
    tag_prefix: str,
    default_client: str | None,
) -> str | None:
    """
    Determine tenant slug from ticket info.

    Priority:
      1. Tag matching the prefix  (e.g. "client:unity_bank" → "unity_bank")
      2. Custom field: cf_client_slug or cf_client
      3. Configured default
    """
    prefix_lower = tag_prefix.lower()
    for tag in ticket.get("tags") or []:
        if tag.lower().startswith(prefix_lower):
            slug = tag[len(tag_prefix):].strip()
            if slug:
                return slug

    cf = ticket.get("custom_fields") or {}
    for key in ("cf_client_slug", "cf_client", "client_slug", "client"):
        val = cf.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()

    return default_client


# ── Query building ─────────────────────────────────────────────────────────────

def build_query_text(ticket: dict[str, Any]) -> str:
    """Construct the RAG query string from ticket subject + description."""
    subject = ticket.get("subject") or ""
    desc = ticket.get("description_text") or ticket.get("description") or ""

    # Strip HTML
    desc_clean = _HTML_TAG_RE.sub(" ", desc)
    desc_clean = _WHITESPACE_RE.sub(" ", desc_clean).strip()

    # Avoid sending huge payloads to the LLM
    if len(desc_clean) > 2000:
        desc_clean = desc_clean[:2000].rsplit(" ", 1)[0] + " ..."

    parts: list[str] = []
    if subject:
        parts.append(f"Issue: {subject}")
    if desc_clean:
        parts.append(f"Description: {desc_clean}")
    return "\n\n".join(parts)


# ── Signature / token verification ────────────────────────────────────────────

def verify_webhook_token(
    raw_body: bytes,
    *,
    provided_token: str | None,
    expected_secret: str,
) -> bool:
    """
    Constant-time HMAC-SHA256 verification.

    Freshdesk does not sign webhook bodies natively; this validates an
    X-Webhook-Token header that n8n or your automation platform can inject
    by computing HMAC-SHA256(secret, body) before forwarding.
    """
    if not provided_token:
        return False
    expected = hmac.new(
        expected_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(provided_token.lower(), expected.lower())


# ── Confidence gate ────────────────────────────────────────────────────────────

def meets_confidence_threshold(confidence: str, min_confidence: str) -> bool:
    return _CONFIDENCE_RANK.get(confidence, 0) >= _CONFIDENCE_RANK.get(min_confidence, 0)


# ── HTML formatters ────────────────────────────────────────────────────────────

def _safe_html(val: Any) -> str:
    import html as _html
    return _html.escape(str(val or ""), quote=True)


def _safe_url(val: Any) -> str:
    """Return a URL safe for use inside an href attribute, or empty string if unsafe."""
    url = str(val or "").strip()
    # Only allow http/https; block javascript:, data:, vbscript:, etc.
    if url and not url.lower().startswith(("http://", "https://")):
        return ""
    return _safe_html(url)


def _answer_to_html(answer: str) -> str:
    escaped = _safe_html(answer)
    # Preserve paragraph breaks and line breaks
    escaped = escaped.replace("\n\n", "</p><p>").replace("\n", "<br>")
    return f"<p>{escaped}</p>"


def format_note_html(
    answer: str,
    *,
    confidence: str,
    citations: list[dict[str, Any]],
    ticket_id: str,
    client: str,
) -> str:
    """Format the AI draft as HTML for a private agent note."""
    badge = {"high": "🟢 HIGH", "medium": "🟡 MEDIUM", "low": "🔴 LOW"}.get(confidence, "⚪ UNKNOWN")

    cit_html = ""
    if citations:
        items = "".join(
            "<li>"
            + _safe_html(c.get("title") or c.get("ticket_id") or c.get("id") or "source")
            + (
                f' — <a href="{_safe_url(c.get("url") or c.get("ticket_url") or "")}">'
                f'{_safe_html(c.get("url") or c.get("ticket_url") or "")}</a>'
                if (c.get("url") or c.get("ticket_url"))
                else ""
            )
            + "</li>"
            for c in citations[:5]
        )
        cit_html = f"<p><strong>Sources:</strong><ul>{items}</ul></p>"

    return (
        f"<p><strong>AI Draft Reply</strong> &mdash; Confidence: {badge} "
        f"| client: <code>{_safe_html(client)}</code> | ticket: <code>{_safe_html(ticket_id)}</code></p>"
        f"<hr>"
        f"{_answer_to_html(answer)}"
        f"{cit_html}"
        f"<hr>"
        f"<p><em>&#9888;&#65039; AI-generated draft. Review before sending to the customer.</em></p>"
    )


def format_reply_html(answer: str) -> str:
    """Format the AI answer as a public reply HTML body."""
    return _answer_to_html(answer)


# ── Freshdesk reply client ─────────────────────────────────────────────────────

class FreshdeskReplyClient:
    """Posts private notes or public replies back to Freshdesk tickets."""

    def __init__(
        self,
        domain: str,
        api_key: str,
        timeout_s: int = 30,
        max_retries: int = 3,
    ) -> None:
        normalized = domain.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            normalized = f"https://{normalized}"
        self._base_url = f"{normalized}/api/v2/tickets"
        self._auth = (api_key, "X")
        self._timeout = timeout_s
        self._max_retries = max_retries

    def post_note(self, ticket_id: str, body_html: str, *, private: bool = True) -> dict[str, Any]:
        """Post a note. Private=True makes it visible only to agents."""
        url = f"{self._base_url}/{ticket_id}/notes"
        return self._post(url, {"body": body_html, "private": private})

    def post_reply(self, ticket_id: str, body_html: str) -> dict[str, Any]:
        """Post a public reply visible to the requester."""
        url = f"{self._base_url}/{ticket_id}/reply"
        return self._post(url, {"body": body_html})

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                with httpx.Client(auth=self._auth, timeout=self._timeout) as client:
                    resp = client.post(url, json=payload)
                    if resp.status_code in {401, 403, 404}:
                        resp.raise_for_status()
                    if resp.status_code >= 400:
                        raise httpx.HTTPStatusError(
                            f"HTTP {resp.status_code}", request=resp.request, response=resp
                        )
                    return resp.json() if resp.content else {}
            except httpx.HTTPStatusError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < self._max_retries:
                    time.sleep(1.0 * (2 ** attempt))
        raise RuntimeError(
            f"Freshdesk API call failed after {self._max_retries + 1} attempts"
        ) from last_exc
