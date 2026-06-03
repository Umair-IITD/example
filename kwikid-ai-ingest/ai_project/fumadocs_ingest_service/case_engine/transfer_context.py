"""
case_engine/transfer_context.py

TransferContextPayload — the structured diagnostic package posted as a
Freshdesk private note when a case is escalated.

Schema follows 07_GOVERNANCE_POLICY_AND_HANDOFF.md §4.

Requirements:
- Serializable to JSON (no datetime objects in output, no unserializable types)
- PII must be masked before this payload is constructed (caller's responsibility)
- Stored in case_audit_log.action_detail on escalation events
"""
from __future__ import annotations

import html as _html
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from case_engine.models import Case


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass
class AttemptedRemediation:
    """A single action that was attempted before escalation."""
    action:     str
    outcome:    str              # "success" | "failure" | "skipped"
    timestamp:  str = field(default_factory=_now_iso)
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class TransferContextPayload:
    """
    Full transfer context posted to the human agent on ESCALATED state.

    Follows 07_GOVERNANCE_POLICY_AND_HANDOFF.md §4 schema.
    """
    case_id:                str
    ticket_id:              str
    client:                 str
    generated_at:           str = field(default_factory=_now_iso)
    payload_version:        str = "1.0"

    topic:                  str | None = None
    confidence:             float | None = None
    classifier_tier:        int | None = None

    retrieval_match_type:   str | None = None

    escalation_trigger:     str | None = None
    escalation_reason:      str | None = None

    root_cause_analysis:    str | None = None
    failure_code:           str | None = None
    attempted_remediations: list[AttemptedRemediation] = field(default_factory=list)

    cited_sop_ids:          list[str] = field(default_factory=list)
    pii_masked:             bool = True
    sla_breach_at:          str | None = None
    recommended_action:     str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return {
            "payload_version":         self.payload_version,
            "generated_at":            self.generated_at,
            "case_id":                 self.case_id,
            "ticket_id":               self.ticket_id,
            "client":                  self.client,
            "pii_masked":              self.pii_masked,
            "diagnostic_summary": {
                "detected_topic":          self.topic,
                "classifier_confidence":   self.confidence,
                "classifier_tier":         self.classifier_tier,
                "retrieval_match_type":    self.retrieval_match_type,
                "root_cause_analysis":     self.root_cause_analysis,
                "failure_code":            self.failure_code,
                "escalation_trigger":      self.escalation_trigger,
                "escalation_reason":       self.escalation_reason,
                "attempted_remediations":  [r.to_dict() for r in self.attempted_remediations],
            },
            "cited_sop_ids":           self.cited_sop_ids,
            "sla_breach_at":           self.sla_breach_at,
            "recommended_action":      self.recommended_action,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)

    def to_html_note(self) -> str:
        """Format as HTML for a Freshdesk private note."""
        esc = _html.escape

        topic_str = esc(self.topic or "Unknown")
        conf_str  = f"{self.confidence:.2f}" if self.confidence is not None else "N/A"
        trigger   = esc(self.escalation_trigger or "")
        reason    = esc(self.escalation_reason or "")
        rca       = esc(self.root_cause_analysis or "")
        match_t   = esc(self.retrieval_match_type or "")
        rec_act   = esc(self.recommended_action or "")

        remediations_html = ""
        for r in self.attempted_remediations:
            remediations_html += (
                f"<li>{esc(r.action)} — {esc(r.outcome)}"
                + (f" ({esc(r.error_code)})" if r.error_code else "")
                + f" at {esc(r.timestamp)}</li>"
            )
        if remediations_html:
            remediations_html = f"<ul>{remediations_html}</ul>"
        else:
            remediations_html = "<p><em>No remediations attempted.</em></p>"

        rca_html = f"<p><strong>Root Cause:</strong> {rca}</p>" if rca else ""
        rec_html = f"<p><strong>Recommended Action:</strong> {rec_act}</p>" if rec_act else ""

        return (
            f'<p><strong>🤖 AI Transfer Context</strong> — Case <code>{esc(self.case_id)}</code></p>'
            f"<hr>"
            f"<p><strong>Topic:</strong> {topic_str} | <strong>Confidence:</strong> {conf_str}"
            f" | <strong>Match:</strong> {match_t}</p>"
            f"<p><strong>Escalation Trigger:</strong> {trigger}</p>"
            f"<p><strong>Reason:</strong> {reason}</p>"
            f"{rca_html}"
            f"<p><strong>Attempted Remediations:</strong></p>{remediations_html}"
            f"{rec_html}"
            f"<hr>"
            f"<p><em>⚠️ PII masked: {self.pii_masked}. Generated: {esc(self.generated_at)}."
            f" Payload v{esc(self.payload_version)}.</em></p>"
        )

    @classmethod
    def build_from_case(
        cls,
        case: Case,
        *,
        escalation_trigger: str | None = None,
        escalation_reason: str | None = None,
        root_cause_analysis: str | None = None,
        recommended_action: str | None = None,
        cited_sop_ids: list[str] | None = None,
        sla_breach_at: str | None = None,
    ) -> TransferContextPayload:
        """Convenience constructor from a Case object."""
        return cls(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            topic=case.topic,
            confidence=case.confidence,
            retrieval_match_type=case.retrieval_match_type,
            escalation_trigger=escalation_trigger or case.escalation_reason,
            escalation_reason=escalation_reason or case.escalation_reason,
            root_cause_analysis=root_cause_analysis,
            failure_code=case.failure_code,
            attempted_remediations=[
                AttemptedRemediation(
                    action=r.get("action", "unknown"),
                    outcome=r.get("outcome", "unknown"),
                    timestamp=r.get("timestamp", _now_iso()),
                    error_code=r.get("error_code"),
                )
                for r in case.attempted_remediations
            ],
            cited_sop_ids=cited_sop_ids or [],
            recommended_action=recommended_action,
            sla_breach_at=sla_breach_at,
        )
