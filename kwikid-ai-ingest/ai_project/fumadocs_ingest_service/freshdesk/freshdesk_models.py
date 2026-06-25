"""
freshdesk/freshdesk_models.py

Sprint 2.4:   FreshdeskConfig — provider configuration.
Sprint 2.28.1: FreshdeskStatus, FreshdeskPriority enums; FreshdeskTicketPayload,
               FreshdeskWebhookPayload, FreshdeskConversation, FreshdeskUpdateEvent
               dataclasses for webhook ingestion and conversation tracking.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


@dataclass(frozen=True)
class FreshdeskConfig:
    """
    Validated, immutable configuration for FreshdeskProvider.

    Args:
        domain:          Freshdesk domain without protocol prefix.
                         e.g. "acme.freshdesk.com", NOT "https://acme.freshdesk.com"
        api_key:         Freshdesk API key for HTTP Basic Auth.
                         Auth header: Base64(api_key + ":X")
        timeout_seconds: Per-request HTTP timeout in seconds. Must be > 0.
                         Default: 10.0 seconds.

    Raises:
        ValueError: if domain is empty, has a protocol prefix, api_key is
                    empty, or timeout_seconds is not positive.
    """

    domain: str
    api_key: str
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not self.domain:
            raise ValueError("domain must be non-empty")
        if self.domain.startswith(("http://", "https://")):
            raise ValueError(
                f"domain must not include a protocol prefix, got {self.domain!r}. "
                "Use 'acme.freshdesk.com', not 'https://acme.freshdesk.com'."
            )
        if not self.api_key:
            raise ValueError("api_key must be non-empty")
        if self.timeout_seconds <= 0:
            raise ValueError(
                f"timeout_seconds must be > 0, got {self.timeout_seconds}"
            )

    @property
    def base_url(self) -> str:
        """Full HTTPS base URL derived from domain (no trailing slash)."""
        return f"https://{self.domain}"

    @property
    def masked_api_key(self) -> str:
        """Show only first 4 chars — safe for logs."""
        return self.api_key[:4] + "****" if len(self.api_key) > 4 else "****"


# ── Sprint 2.28.1: Status / Priority enums ────────────────────────────────────

class FreshdeskStatus(IntEnum):
    OPEN       = 2
    PENDING    = 3
    RESOLVED   = 4
    CLOSED     = 5
    IN_PROCESS = 10

    @classmethod
    def from_int(cls, value: int) -> "FreshdeskStatus":
        try:
            return cls(value)
        except ValueError:
            return cls.OPEN


class FreshdeskPriority(IntEnum):
    LOW      = 1
    MEDIUM   = 2
    HIGH     = 3
    URGENT   = 4

    @classmethod
    def from_int(cls, value: int) -> "FreshdeskPriority":
        try:
            return cls(value)
        except ValueError:
            return cls.MEDIUM


# ── Sprint 2.28.1: Webhook payload models ─────────────────────────────────────

@dataclass
class FreshdeskCustomFields:
    """Custom ticket fields extracted from ticket_custom_fields."""
    cf_clients: str = ""
    cf_session_ids: str = ""
    cf_environment: str = ""
    cf_issue_area: str = ""
    cf_portal: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FreshdeskCustomFields":
        return cls(
            cf_clients=d.get("cf_clients", ""),
            cf_session_ids=d.get("cf_session_ids", ""),
            cf_environment=d.get("cf_environment", ""),
            cf_issue_area=d.get("cf_issue_area", ""),
            cf_portal=d.get("cf_portal", ""),
            raw=d,
        )


@dataclass
class FreshdeskTicketPayload:
    """
    Parsed representation of the freshdesk_webhook object from a Freshdesk webhook.

    Mirrors the exact structure produced by Freshdesk Dispatch'r webhook actions.
    All fields are optional to tolerate partial payloads from different trigger types.
    """
    id: int = 0
    subject: str = ""
    description: str = ""
    description_text: str = ""
    status: FreshdeskStatus = FreshdeskStatus.OPEN
    priority: FreshdeskPriority = FreshdeskPriority.MEDIUM
    ticket_type: str = ""
    created_at: str = ""
    requester_email: str = ""
    requester_name: str = ""
    tags: list[str] = field(default_factory=list)
    custom_fields: FreshdeskCustomFields = field(default_factory=FreshdeskCustomFields)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    group_id: int | None = None
    responder_id: int | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FreshdeskTicketPayload":
        # ── Field extraction with Dispatch'r template variant fallbacks ──────────
        # Freshdesk Dispatch'r webhook templates may use different key names depending
        # on how the Dispatch'r rule was configured.  We check the canonical key first
        # and fall back to the alternative before using the default.

        # Ticket ID: "id" (canonical) or "ticket_id" (Dispatch'r template variant)
        _raw_id = d.get("id")
        if _raw_id is None:
            _raw_id = d.get("ticket_id", 0)

        # Subject: "subject" or "ticket_subject"
        subject = d.get("subject") or d.get("ticket_subject") or ""

        # Description: "description" or "ticket_description"
        description = d.get("description") or d.get("ticket_description") or ""
        description_text = d.get("description_text") or d.get("ticket_description_text") or ""

        # Timestamp: "created_at" or "ticket_created_at"
        created_at = d.get("created_at") or d.get("ticket_created_at") or ""

        # Requester email: top-level "requester_email" or nested under "requester"
        requester_email = d.get("requester_email", "")
        if not requester_email:
            _req_nested = d.get("requester")
            if isinstance(_req_nested, dict):
                requester_email = _req_nested.get("email", "")

        # Requester name: top-level "requester_name" or nested under "requester"
        requester_name = d.get("requester_name", "")
        if not requester_name:
            _req_nested = d.get("requester")
            if isinstance(_req_nested, dict):
                requester_name = _req_nested.get("name", "")

        # Tags: may be a CSV string or a list
        raw_tags = d.get("tags", "")
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()] if isinstance(raw_tags, str) else list(raw_tags)

        # Custom fields: "ticket_custom_fields" (canonical) or "custom_fields" or "ticket_cf"
        cf_dict = (
            d.get("ticket_custom_fields")
            or d.get("custom_fields")
            or d.get("ticket_cf")
            or {}
        )

        return cls(
            id=int(_raw_id),
            subject=subject,
            description=description,
            description_text=description_text,
            status=FreshdeskStatus.from_int(d.get("status", 2)),
            priority=FreshdeskPriority.from_int(d.get("priority", 2)),
            ticket_type=d.get("ticket_type") or d.get("type") or "",
            created_at=created_at,
            requester_email=requester_email,
            requester_name=requester_name,
            tags=tags,
            custom_fields=FreshdeskCustomFields.from_dict(cf_dict),
            attachments=list(d.get("attachments", [])),
            group_id=d.get("group_id"),
            responder_id=d.get("responder_id"),
        )

    @property
    def ticket_id(self) -> str:
        return str(self.id)

    @property
    def client_name(self) -> str:
        return self.custom_fields.cf_clients

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "status": int(self.status),
            "priority": int(self.priority),
            "ticket_type": self.ticket_type,
            "created_at": self.created_at,
            "requester_name": self.requester_name,
            "tags": self.tags,
            "cf_clients": self.custom_fields.cf_clients,
            "cf_environment": self.custom_fields.cf_environment,
            "cf_issue_area": self.custom_fields.cf_issue_area,
            "cf_portal": self.custom_fields.cf_portal,
        }


@dataclass
class FreshdeskWebhookPayload:
    """
    Top-level structure of a Freshdesk webhook POST body.

    Freshdesk wraps all ticket data under "freshdesk_webhook" key.
    """
    ticket: FreshdeskTicketPayload
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FreshdeskWebhookPayload":
        inner = d.get("freshdesk_webhook", d)
        return cls(
            ticket=FreshdeskTicketPayload.from_dict(inner),
            raw=d,
        )


@dataclass
class FreshdeskConversation:
    """
    A single conversation entry (reply, note) from GET /api/v2/tickets/{id}/conversations.

    incoming=True,  private=False → customer reply
    incoming=False, private=False → agent public reply
    incoming=False, private=True  → internal note
    """
    id: int = 0
    ticket_id: int = 0
    body: str = ""
    body_text: str = ""
    incoming: bool = False
    private: bool = False
    created_at: str = ""
    from_email: str = ""
    user_id: int | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FreshdeskConversation":
        return cls(
            id=int(d.get("id", 0)),
            ticket_id=int(d.get("ticket_id", 0)),
            body=d.get("body", ""),
            body_text=d.get("body_text", ""),
            incoming=bool(d.get("incoming", False)),
            private=bool(d.get("private", False)),
            created_at=d.get("created_at", ""),
            from_email=d.get("from_email", ""),
            user_id=d.get("user_id"),
            attachments=list(d.get("attachments", [])),
        )

    @property
    def is_customer_reply(self) -> bool:
        return self.incoming and not self.private

    @property
    def is_agent_reply(self) -> bool:
        return not self.incoming and not self.private

    @property
    def is_internal_note(self) -> bool:
        return not self.incoming and self.private


@dataclass
class FreshdeskLatestComment:
    """
    The latest_comment sub-object in Freshdesk update webhooks.

    Freshdesk includes this on ticket-updated events to identify what type of
    interaction triggered the update.

    Detection table (Section 10.1 of freshdesk_integration.md):
      incoming=True,  private=False → Customer Reply
      incoming=False, private=False → Agent Public Reply
      incoming=False, private=True  → Internal Agent Note
    """
    body: str = ""
    body_text: str = ""
    incoming: bool = False
    private: bool = False
    user_id: int | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FreshdeskLatestComment":
        return cls(
            body=d.get("body", ""),
            body_text=d.get("body_text", ""),
            incoming=bool(d.get("incoming", False)),
            private=bool(d.get("private", False)),
            user_id=d.get("user_id"),
        )

    @property
    def is_customer_reply(self) -> bool:
        """incoming=True, private=False → customer replied to the ticket."""
        return self.incoming and not self.private

    @property
    def is_agent_reply(self) -> bool:
        """incoming=False, private=False → agent sent a public reply."""
        return not self.incoming and not self.private

    @property
    def is_internal_note(self) -> bool:
        """incoming=False, private=True → agent added a private note."""
        return not self.incoming and self.private


@dataclass
class FreshdeskUpdateEvent:
    """
    Parsed ticket-updated webhook event.

    Freshdesk sends this when a ticket is updated (reply added, status changed,
    agent reassigned, tags changed, etc.). The changes dict contains before/after
    values for each modified field.

    latest_comment: Present when the update was triggered by a reply or note.
      This is the authoritative source for reply detection — inspect this
      BEFORE looking at the changes dict (Section 10.1 freshdesk_integration.md).
    """
    ticket_id: int = 0
    updated_at: str = ""
    changes: dict[str, Any] = field(default_factory=dict)
    ticket: FreshdeskTicketPayload = field(default_factory=FreshdeskTicketPayload)
    latest_comment: FreshdeskLatestComment | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FreshdeskUpdateEvent":
        inner = d.get("freshdesk_webhook", d)
        lc_data = inner.get("latest_comment")
        # ID: "id" (canonical) or "ticket_id" (Dispatch'r template variant)
        _raw_id = inner.get("id")
        if _raw_id is None:
            _raw_id = inner.get("ticket_id", 0)
        # Timestamp: "updated_at" or "ticket_updated_at"
        updated_at = inner.get("updated_at") or inner.get("ticket_updated_at") or ""
        return cls(
            ticket_id=int(_raw_id),
            updated_at=updated_at,
            changes=inner.get("changes", {}),
            ticket=FreshdeskTicketPayload.from_dict(inner),
            latest_comment=FreshdeskLatestComment.from_dict(lc_data) if lc_data else None,
        )
