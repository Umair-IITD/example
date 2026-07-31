"""
case_engine/engineering/service.py

Sprint 2.27.5: EngineeringEscalationService — L2/Asana escalation layer.

Per blueprint flow_diagram.mermaid — L2 workflow:
  L2CHECK -->|Yes| ASANACREATE
  ASANACREATE --> ASANA
  ASANA <--> DEV
  ASANA --> FIXED --> FDUPDATE

Sprint 2.27.5: mock adapter — tickets are created in-memory.
Sprint 2.28:   inject asana_client → real Asana API calls via AsanaAdapter.

Design:
  - Never raises: all exceptions return EngineeringEscalationResult with success=False
  - Asana-injectable: asana_client parameter accepted at construction
  - In-memory store supports sync_status() for status polling
  - Audit: emits ENGINEERING_ESCALATION_CREATED + ENGINEERING_ESCALATION_RESOLVED
  - All output is JSON-serializable via EngineeringEscalationResult.to_dict()
"""
from __future__ import annotations

import html as _html
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from case_engine.engineering.models import (
    EngineeringEscalationResult,
    EngineeringPriority,
    EngineeringStatus,
    EngineeringTicket,
)

if TYPE_CHECKING:
    from case_engine.audit import AuditLogger
    from case_engine.models import Case

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _infer_priority(root_cause: dict[str, Any] | None, topic: str) -> EngineeringPriority:
    """Infer ticket priority from root cause and topic."""
    if root_cause is None:
        return EngineeringPriority.MEDIUM

    category = (root_cause.get("category") or "").lower()
    severity = (root_cause.get("severity") or "").lower()

    if severity in ("critical", "p0") or "outage" in category:
        return EngineeringPriority.CRITICAL
    if severity in ("high", "p1") or "failure" in category:
        return EngineeringPriority.HIGH
    if "api" in topic.lower() or "callback" in topic.lower():
        return EngineeringPriority.HIGH
    return EngineeringPriority.MEDIUM


def _esc(value: Any) -> str:
    """HTML-escape any value for safe interpolation into Asana html_notes.

    Asana's rich-text writer rejects malformed/unescaped XML with a 400 —
    every piece of dynamic text (case IDs, tool payload values, free-text
    explanations) MUST be escaped before going into the body.
    """
    return _html.escape(str(value), quote=False)


def _render_evidence_value(value: Any, *, max_len: int = 240) -> str:
    """Render one evidence payload value as a short, safe, escaped string."""
    if isinstance(value, (dict, list)):
        rendered = json.dumps(value, default=str, ensure_ascii=False)
    else:
        rendered = str(value)
    if len(rendered) > max_len:
        rendered = rendered[: max_len - 1] + "…"
    return _esc(rendered)


def _build_description(
    case_id:              str,
    topic:                str,
    freshdesk_ticket_id:  str,
    priority:             "EngineeringPriority",
    investigation_result: dict[str, Any] | None,
    root_cause:           dict[str, Any] | None,
    sop_steps:            list[str] | None,
    escalation_reason:    str,
) -> str:
    """
    Build a structured Asana html_notes body for an engineering escalation
    ticket — everything a dev needs to understand and fix the issue without
    leaving Asana: escalation context, root cause, the L1 observation note,
    every piece of evidence collected (tool name + key data points), and any
    SOP steps already attempted.

    Sprint 2.63.2: rewritten to use investigation_result's REAL shape
    (case_engine/investigation/models.py::InvestigationResult.to_dict() —
    keys "observation", "evidence", "root_cause") instead of a "summary" key
    that never actually existed in that dict, which meant the Investigation
    Summary section silently rendered empty on every prior ticket.

    Output is Asana rich-text HTML (html_notes) — ONLY the tags Asana's API
    accepts for tasks are used: body, h1, h2, hr, strong, em, ul, ol, li,
    pre, a. No <p> or <br> — Asana's rich text has no paragraph tag; block
    separation is done with <hr/> and headers instead (see
    https://developers.asana.com/docs/rich-text). Headers/lists/pre may not
    be nested inside each other, so the structure below stays flat.
    """
    parts: list[str] = ["<body>"]

    parts.append(f"<h1>[{_esc(priority.value)}] {_esc(topic)}</h1>")
    parts.append(
        f"<strong>Freshdesk Ticket:</strong> #{_esc(freshdesk_ticket_id or 'unknown')}"
        f" |<strong>Case ID:</strong> {_esc(case_id)}"
    )
    parts.append("<hr/>")

    parts.append("<h2>Escalation Reason</h2>")
    parts.append(_esc(escalation_reason or "L1 automation could not resolve this issue automatically."))
    parts.append("<hr/>")

    if root_cause:
        category    = root_cause.get("category", "Unknown")
        confidence  = root_cause.get("confidence")
        explanation = root_cause.get("explanation", "")
        recommended = root_cause.get("recommended_action", "")
        parts.append("<h2>Root Cause Analysis</h2>")
        conf_str = f"{confidence:.0%}" if isinstance(confidence, (int, float)) else "unknown"
        parts.append(f"<strong>Category:</strong> {_esc(category)} |<strong>Confidence:</strong> {_esc(conf_str)}")
        if recommended:
            parts.append(f"<strong>Recommended Action:</strong> {_esc(recommended)}")
        if explanation:
            parts.append(_esc(explanation))
        parts.append("<hr/>")

    observation = (investigation_result or {}).get("observation") or ""
    if observation:
        parts.append("<h2>Investigation Observation (L1 note)</h2>")
        parts.append(f"<pre>{_esc(observation)}</pre>")
        parts.append("<hr/>")

    evidence = (investigation_result or {}).get("evidence") or {}
    items = evidence.get("items") or []
    if items:
        total = evidence.get("total_items", len(items))
        success_count = evidence.get("success_count", sum(1 for i in items if i.get("success")))
        parts.append(f"<h2>Evidence Collected ({_esc(success_count)}/{_esc(total)} tools succeeded)</h2>")
        parts.append("<ul>")
        for item in items:
            status = "OK" if item.get("success") else "FAILED"
            tool_name = item.get("tool_name", "unknown_tool")
            line = f"<li><strong>{_esc(tool_name)}</strong> [{status}]"
            payload = item.get("payload") or {}
            if isinstance(payload, dict) and payload:
                pairs = "; ".join(f"{_esc(k)}: {_render_evidence_value(v)}" for k, v in payload.items())
                line += f" — {pairs}"
            if not item.get("success"):
                err = item.get("error_message") or item.get("error_code") or ""
                if err:
                    line += f" — <strong>Error:</strong> {_esc(err)}"
            line += "</li>"
            parts.append(line)
        parts.append("</ul>")
        parts.append("<hr/>")

    if sop_steps:
        parts.append("<h2>SOP Steps Already Attempted</h2>")
        parts.append("<ol>")
        for step in sop_steps:
            parts.append(f"<li>{_esc(step)}</li>")
        parts.append("</ol>")
        parts.append("<hr/>")

    parts.append("<em>Generated automatically by the KwikID L1 Support Agent.</em>")
    parts.append("</body>")
    # Joined with newlines (not ""): Asana renders rich-text body whitespace
    # with white-space: pre-wrap, so this gives real visual line breaks
    # between fields/sections without needing a <br> tag (which Asana's rich
    # text does not support at all — see module docstring above).
    return "\n".join(parts)


class EngineeringEscalationService:
    """
    L2/Asana escalation service.

    Sprint 2.27.5: mock in-memory adapter.
    Sprint 2.28: inject asana_client for real Asana task creation.

    Public API:
      create_ticket(case, topic, ...)  → EngineeringEscalationResult
      update_ticket(ticket_id, ...)    → EngineeringEscalationResult
      resolve_ticket(ticket_id, ...)   → EngineeringEscalationResult
      sync_status(ticket_id)           → EngineeringEscalationResult
      get_ticket(ticket_id)            → EngineeringTicket | None

    Never raises. Thread-safe: uses dict (in-memory store).
    """

    def __init__(
        self,
        audit_logger: "AuditLogger | None" = None,
        asana_client: Any = None,
    ) -> None:
        self._audit        = audit_logger
        self._asana        = asana_client
        # In-memory store: ticket_id → EngineeringTicket
        self._store: dict[str, EngineeringTicket] = {}

    # ── Primary API ───────────────────────────────────────────────────────────

    def create_ticket(
        self,
        case:                 "Case | None",
        topic:                str,
        freshdesk_ticket_id:  str = "",
        investigation_result: dict[str, Any] | None = None,
        root_cause:           dict[str, Any] | None = None,
        sop_steps:            list[str] | None = None,
        escalation_reason:    str = "",
    ) -> EngineeringEscalationResult:
        """
        Create an engineering escalation ticket (ASANACREATE node).

        Sprint 2.27.5: mock — stored in-memory.
        Sprint 2.28: delegates to asana_client.create_task().

        Returns EngineeringEscalationResult. Never raises.
        """
        started_ms = int(time.monotonic() * 1000)
        case_id = case.case_id if case else "unknown"
        fd_id   = freshdesk_ticket_id or (case.ticket_id if case else "")
        try:
            ticket = self._create_ticket_internal(
                case_id=case_id,
                freshdesk_ticket_id=fd_id,
                topic=topic,
                investigation_result=investigation_result,
                root_cause=root_cause,
                sop_steps=sop_steps,
                escalation_reason=escalation_reason,
            )
            self._store[ticket.ticket_id] = ticket
            duration_ms = max(0, int(time.monotonic() * 1000) - started_ms)

            LOGGER.info(
                "engineering.create_ticket ticket_id=%s case_id=%s priority=%s",
                ticket.ticket_id, case_id, ticket.priority.value,
            )
            self._emit_created(ticket, case)

            return EngineeringEscalationResult(
                result_id=_new_id(),
                success=True,
                ticket=ticket,
                operation="create",
                error_code=None,
                error_msg=None,
                executed_at=_now_iso(),
                duration_ms=duration_ms,
            )
        except Exception as exc:
            duration_ms = max(0, int(time.monotonic() * 1000) - started_ms)
            LOGGER.exception(
                "engineering.create_ticket failed case_id=%s error=%s", case_id, exc
            )
            return EngineeringEscalationResult.failure(
                case_id=case_id,
                freshdesk_ticket_id=fd_id,
                operation="create",
                error_code="ENGINEERING_CREATE_FAILED",
                error_msg=str(exc),
                duration_ms=duration_ms,
            )

    def update_ticket(
        self,
        ticket_id: str,
        status:    EngineeringStatus | None = None,
        assignee:  str | None = None,
        metadata:  dict[str, Any] | None = None,
    ) -> EngineeringEscalationResult:
        """
        Update an existing engineering ticket's status or assignee.

        Never raises.
        """
        started_ms = int(time.monotonic() * 1000)
        try:
            ticket = self._store.get(ticket_id)
            if ticket is None:
                return EngineeringEscalationResult.failure(
                    case_id="unknown",
                    freshdesk_ticket_id="",
                    operation="update",
                    error_code="ENGINEERING_TICKET_NOT_FOUND",
                    error_msg=f"Ticket {ticket_id} not found",
                    duration_ms=max(0, int(time.monotonic() * 1000) - started_ms),
                )

            new_status = status or ticket.status
            updated = EngineeringTicket(
                ticket_id=ticket.ticket_id,
                external_id=ticket.external_id,
                case_id=ticket.case_id,
                freshdesk_ticket_id=ticket.freshdesk_ticket_id,
                title=ticket.title,
                description=ticket.description,
                priority=ticket.priority,
                status=new_status,
                assignee=assignee if assignee is not None else ticket.assignee,
                asana_project_id=ticket.asana_project_id,
                created_at=ticket.created_at,
                updated_at=_now_iso(),
                resolved_at=ticket.resolved_at,
                metadata={**ticket.metadata, **(metadata or {})},
            )
            self._store[ticket_id] = updated
            duration_ms = max(0, int(time.monotonic() * 1000) - started_ms)

            LOGGER.info(
                "engineering.update_ticket ticket_id=%s status=%s",
                ticket_id, new_status.value,
            )
            return EngineeringEscalationResult(
                result_id=_new_id(),
                success=True,
                ticket=updated,
                operation="update",
                error_code=None,
                error_msg=None,
                executed_at=_now_iso(),
                duration_ms=duration_ms,
            )
        except Exception as exc:
            LOGGER.exception("engineering.update_ticket failed ticket_id=%s error=%s", ticket_id, exc)
            return EngineeringEscalationResult.failure(
                case_id="unknown",
                freshdesk_ticket_id="",
                operation="update",
                error_code="ENGINEERING_UPDATE_FAILED",
                error_msg=str(exc),
                duration_ms=max(0, int(time.monotonic() * 1000) - started_ms),
            )

    def resolve_ticket(
        self,
        ticket_id:  str,
        case:       "Case | None" = None,
        resolution: str = "",
    ) -> EngineeringEscalationResult:
        """
        Mark ticket as RESOLVED — triggers FDUPDATE → USERRESPONSE flow.

        Never raises.
        """
        started_ms = int(time.monotonic() * 1000)
        try:
            ticket = self._store.get(ticket_id)
            if ticket is None:
                return EngineeringEscalationResult.failure(
                    case_id="unknown",
                    freshdesk_ticket_id="",
                    operation="resolve",
                    error_code="ENGINEERING_TICKET_NOT_FOUND",
                    error_msg=f"Ticket {ticket_id} not found",
                    duration_ms=max(0, int(time.monotonic() * 1000) - started_ms),
                )

            resolved_at = _now_iso()
            updated = ticket.with_status(EngineeringStatus.RESOLVED, resolved_at=resolved_at)
            self._store[ticket_id] = updated
            duration_ms = max(0, int(time.monotonic() * 1000) - started_ms)

            LOGGER.info(
                "engineering.resolve_ticket ticket_id=%s case_id=%s",
                ticket_id, ticket.case_id,
            )
            self._emit_resolved(updated, case)

            return EngineeringEscalationResult(
                result_id=_new_id(),
                success=True,
                ticket=updated,
                operation="resolve",
                error_code=None,
                error_msg=None,
                executed_at=_now_iso(),
                duration_ms=duration_ms,
            )
        except Exception as exc:
            LOGGER.exception("engineering.resolve_ticket failed ticket_id=%s error=%s", ticket_id, exc)
            return EngineeringEscalationResult.failure(
                case_id="unknown",
                freshdesk_ticket_id="",
                operation="resolve",
                error_code="ENGINEERING_RESOLVE_FAILED",
                error_msg=str(exc),
                duration_ms=max(0, int(time.monotonic() * 1000) - started_ms),
            )

    def sync_status(self, ticket_id: str) -> EngineeringEscalationResult:
        """
        Sync ticket status from external system (Asana in Sprint 2.28).

        Sprint 2.27.5: returns current in-memory state.
        Sprint 2.28: calls asana_client.get_task(external_id) and updates status.

        Never raises.
        """
        started_ms = int(time.monotonic() * 1000)
        try:
            ticket = self._store.get(ticket_id)
            if ticket is None:
                return EngineeringEscalationResult.failure(
                    case_id="unknown",
                    freshdesk_ticket_id="",
                    operation="sync",
                    error_code="ENGINEERING_TICKET_NOT_FOUND",
                    error_msg=f"Ticket {ticket_id} not found",
                    duration_ms=max(0, int(time.monotonic() * 1000) - started_ms),
                )

            # Sprint 2.28 injection point: poll real Asana status
            if self._asana is not None and ticket.external_id:
                try:
                    asana_task = self._asana.get_task(ticket.external_id)
                    completed  = asana_task.get("completed", False)
                    if completed and ticket.status != EngineeringStatus.RESOLVED:
                        ticket = ticket.with_status(
                            EngineeringStatus.RESOLVED,
                            resolved_at=_now_iso(),
                        )
                        self._store[ticket_id] = ticket
                except Exception as exc:
                    LOGGER.warning("engineering.sync_status asana_call failed: %s", exc)

            duration_ms = max(0, int(time.monotonic() * 1000) - started_ms)
            return EngineeringEscalationResult(
                result_id=_new_id(),
                success=True,
                ticket=ticket,
                operation="sync",
                error_code=None,
                error_msg=None,
                executed_at=_now_iso(),
                duration_ms=duration_ms,
            )
        except Exception as exc:
            LOGGER.exception("engineering.sync_status failed ticket_id=%s error=%s", ticket_id, exc)
            return EngineeringEscalationResult.failure(
                case_id="unknown",
                freshdesk_ticket_id="",
                operation="sync",
                error_code="ENGINEERING_SYNC_FAILED",
                error_msg=str(exc),
                duration_ms=max(0, int(time.monotonic() * 1000) - started_ms),
            )

    def get_ticket(self, ticket_id: str) -> EngineeringTicket | None:
        """Return ticket by ID or None. Never raises."""
        try:
            return self._store.get(ticket_id)
        except Exception:
            return None

    def list_tickets_for_case(self, case_id: str) -> list[EngineeringTicket]:
        """Return all tickets for a given case_id. Never raises."""
        try:
            return [t for t in self._store.values() if t.case_id == case_id]
        except Exception:
            return []

    def get_ticket_by_external_id(self, external_id: str) -> EngineeringTicket | None:
        """
        Look up a ticket by its Asana task GID (external_id).

        Used by the Asana webhook receiver to map an incoming
        "task completed" event back to the internal ticket. Never raises.
        """
        try:
            for ticket in self._store.values():
                if ticket.external_id == external_id:
                    return ticket
            return None
        except Exception:
            return None

    def notify_asana_progress(self, ticket: EngineeringTicket, progress: str) -> None:
        """
        Set the "Task Progress" custom field on the ticket's Asana task.

        Sprint 2.63.2: called by the resolution webhook (api/routes/webhooks/
        asana.py) with progress="Done" once the Freshdesk closure loop
        actually completes — gives the dev team a visible board-level signal
        distinct from the raw `completed` checkbox they set themselves.

        No-op if this ticket has no external_id (mock mode) or no Asana
        client is configured. Never raises — this is a UI nicety, not
        something that should ever block ticket closure.
        """
        if self._asana is None or not ticket.external_id:
            return
        try:
            self._asana.set_task_progress(ticket.external_id, progress)
        except Exception as exc:
            LOGGER.debug(
                "engineering.notify_asana_progress failed ticket_id=%s error=%s",
                ticket.ticket_id, exc,
            )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _create_ticket_internal(
        self,
        case_id:              str,
        freshdesk_ticket_id:  str,
        topic:                str,
        investigation_result: dict[str, Any] | None,
        root_cause:           dict[str, Any] | None,
        sop_steps:            list[str] | None,
        escalation_reason:    str,
    ) -> EngineeringTicket:
        priority    = _infer_priority(root_cause, topic)
        description = _build_description(
            case_id=case_id,
            topic=topic,
            freshdesk_ticket_id=freshdesk_ticket_id,
            priority=priority,
            investigation_result=investigation_result,
            root_cause=root_cause,
            sop_steps=sop_steps,
            escalation_reason=escalation_reason,
        )
        # Sprint 2.63.2: priority prefix makes triage possible from Asana's
        # list/board view alone (see AsanaClient.create_task docstring), on
        # top of the real Priority custom field the task also gets set.
        case_ref = (case_id or "")[:8]
        title = f"[{priority.value}] {topic}: {escalation_reason[:80]}" if escalation_reason else f"[{priority.value}] {topic} Escalation"
        if case_ref:
            title = f"{title} (Case {case_ref})"
        now       = _now_iso()
        ticket_id = _new_id()

        # Sprint 2.28: real Asana call goes here
        external_id      = None
        asana_project_id = None
        if self._asana is not None:
            try:
                result = self._asana.create_task(
                    title=title, description=description, priority=priority.value,
                    html_notes=True,
                )
                external_id      = result.get("gid")
                asana_project_id = result.get("project_id")
            except Exception as exc:
                try:
                    import httpx as _httpx  # noqa: PLC0415
                    if isinstance(exc, _httpx.HTTPStatusError):
                        LOGGER.warning(
                            "engineering: asana.create_task http_error status=%d "
                            "body=%.800s — verify ASANA_PROJECT_ID and ASANA_WORKSPACE_ID",
                            exc.response.status_code, exc.response.text,
                        )
                    else:
                        LOGGER.warning("engineering: asana.create_task failed error=%s", exc)
                except Exception:
                    LOGGER.warning("engineering: asana.create_task failed error=%s", exc)

        return EngineeringTicket(
            ticket_id=ticket_id,
            external_id=external_id,
            case_id=case_id,
            freshdesk_ticket_id=freshdesk_ticket_id,
            title=title,
            description=description,
            priority=priority,
            status=EngineeringStatus.PENDING,
            assignee=None,
            asana_project_id=asana_project_id,
            created_at=now,
            updated_at=now,
        )

    # ── Audit ─────────────────────────────────────────────────────────────────

    def _emit_created(self, ticket: EngineeringTicket, case: "Case | None") -> None:
        """Emit audit event for ticket creation. Never raises."""
        if self._audit is None or case is None:
            return
        try:
            self._audit.log_engineering_escalation_created(
                case,
                ticket_id=ticket.ticket_id,
                priority=ticket.priority.value,
                external_id=ticket.external_id,
            )
        except Exception as exc:
            LOGGER.debug("engineering: audit emit (created) failed: %s", exc)

    def _emit_resolved(self, ticket: EngineeringTicket, case: "Case | None") -> None:
        """Emit audit event for ticket resolution. Never raises."""
        if self._audit is None or case is None:
            return
        try:
            self._audit.log_engineering_escalation_resolved(
                case,
                ticket_id=ticket.ticket_id,
                resolved_at=ticket.resolved_at,
            )
        except Exception as exc:
            LOGGER.debug("engineering: audit emit (resolved) failed: %s", exc)


# ── Factory ───────────────────────────────────────────────────────────────────

def build_engineering_escalation_service(
    audit_logger: Any = None,
    asana_client: Any = None,
) -> EngineeringEscalationService:
    """
    Factory: build an EngineeringEscalationService.

    Args:
        audit_logger: Optional AuditLogger for engineering events.
        asana_client: Optional Asana client (None = mock/in-memory mode).

    Returns:
        EngineeringEscalationService ready to create engineering tickets.
    """
    return EngineeringEscalationService(
        audit_logger=audit_logger,
        asana_client=asana_client,
    )
