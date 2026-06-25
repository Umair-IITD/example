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

Sprint 2.28.3 changes:
- resolve_tenant(): fixed to check cf_clients (Freshdesk's actual field name)
  and added email-domain fallback per freshdesk_integration.md Section 4.2.
- Added structured failure logging at each resolution step.
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

# ── Tenant registry (freshdesk_integration.md Section 4.2) ────────────────────
# Maps requester email domain → canonical tenant slug.
# Slug must match the value Freshdesk Dispatch'r rules write into cf_clients.
_TENANT_EMAIL_DOMAIN_MAP: dict[str, str] = {
    "unitybank.co.in": "Unity",
    "bankofbaroda.com": "BOB",
    "centralbank.co.in": "CBI",
    "rblbank.com": "RBL",
    "bajajfinserv.in": "BAJAJ_FIN",
    "thomascook.in": "THOMAS_COOK",
    "canarabank.com": "CANARA",
    "finobank.com": "FINO",
    "tfsin.co.in": "TOYOTA",
    "grihumhousing.com": "GHF",
}

# cf_clients values that indicate no specific tenant was resolved by Dispatch'r.
# When these are present, fall through to email-domain lookup.
_CF_CLIENTS_SKIP: frozenset[str] = frozenset({"others", "unknown", ""})


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

    Priority order (per freshdesk_integration.md Section 4.2):
      1. cf_clients custom field — set by Freshdesk Dispatch'r rules (PRIMARY)
         Skip if value is "Others", "unknown", or empty (fall through).
      2. Tag matching the prefix  (e.g. "client:unity_bank" → "unity_bank")
      3. Other cf field aliases (cf_client_slug, cf_client, client_slug, client)
      4. Requester email domain → _TENANT_EMAIL_DOMAIN_MAP lookup
      5. Configured default_client fallback

    Logs every resolution step at INFO on success, WARNING on failure.
    Never raises.
    """
    ticket_id = ticket.get("ticket_id", "?")
    cf = ticket.get("custom_fields") or {}

    # Priority 1: cf_clients — the authoritative Freshdesk Dispatch'r field.
    # Freshdesk writes the tenant name here before the webhook fires.
    # Real field name is "cf_clients" (plural) — NOT "cf_client".
    cf_clients_raw = cf.get("cf_clients", "").strip()
    if cf_clients_raw and cf_clients_raw.lower() not in _CF_CLIENTS_SKIP:
        LOGGER.info(
            "resolve_tenant: via=cf_clients ticket=%s tenant=%s",
            ticket_id, cf_clients_raw,
        )
        return cf_clients_raw

    if cf_clients_raw:
        LOGGER.info(
            "resolve_tenant: cf_clients=%r is passthrough-value ticket=%s — continuing resolution",
            cf_clients_raw, ticket_id,
        )

    # Priority 2: Tag prefix matching (e.g. FRESHDESK_WEBHOOK_TENANT_TAG_PREFIX="client:")
    prefix_lower = tag_prefix.lower()
    for tag in ticket.get("tags") or []:
        if tag.lower().startswith(prefix_lower):
            slug = tag[len(tag_prefix):].strip()
            if slug:
                LOGGER.info(
                    "resolve_tenant: via=tag_prefix ticket=%s tag=%s tenant=%s",
                    ticket_id, tag, slug,
                )
                return slug

    # Priority 3: Other custom field name aliases
    for key in ("cf_client_slug", "cf_client", "client_slug", "client"):
        val = cf.get(key)
        if val and isinstance(val, str) and val.strip():
            LOGGER.info(
                "resolve_tenant: via=cf_alias field=%s ticket=%s tenant=%s",
                key, ticket_id, val.strip(),
            )
            return val.strip()

    # Priority 4: Requester email domain lookup
    email = ticket.get("requester_email", "").strip().lower()
    if email and "@" in email:
        domain = email.split("@")[-1]
        tenant = _TENANT_EMAIL_DOMAIN_MAP.get(domain)
        if tenant:
            LOGGER.info(
                "resolve_tenant: via=email_domain domain=%s ticket=%s tenant=%s",
                domain, ticket_id, tenant,
            )
            return tenant
        LOGGER.warning(
            "resolve_tenant: email_domain_not_registered domain=%s ticket=%s",
            domain, ticket_id,
        )

    # Priority 5: Configured default
    if default_client:
        LOGGER.info(
            "resolve_tenant: via=default_client ticket=%s tenant=%s",
            ticket_id, default_client,
        )
        return default_client

    LOGGER.warning(
        "resolve_tenant: resolution_failed ticket=%s cf_clients=%r "
        "email=%s tags=%s — no_tenant",
        ticket_id, cf_clients_raw,
        email.split("@")[-1] if "@" in email else "none",
        ticket.get("tags"),
    )
    return None


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
    mode: str = "hmac",
) -> bool:
    """
    Constant-time webhook token verification.

    Two modes are supported via FRESHDESK_WEBHOOK_MODE in .env:

    "static"  ── Freshdesk → FastAPI (Sprint 2.28 direct testing)
        Freshdesk automation rules can only send a static header value.
        The X-Webhook-Token header is compared directly against the
        configured secret using constant-time comparison to prevent
        timing attacks.

    "hmac"  ── Freshdesk → n8n → FastAPI (production target)
        n8n computes HMAC-SHA256(secret, raw_body) before forwarding.
        The X-Webhook-Token header is verified against that computed
        digest. Switch to this mode once n8n is in the pipeline.
    """
    if not provided_token:
        return False

    if mode == "static":
        # Freshdesk sends the raw secret as the token value.
        # Constant-time comparison prevents timing-based token guessing.
        return hmac.compare_digest(
            provided_token.strip(),
            expected_secret.strip(),
        )

    # mode == "hmac": n8n has pre-computed the signature before forwarding.
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
