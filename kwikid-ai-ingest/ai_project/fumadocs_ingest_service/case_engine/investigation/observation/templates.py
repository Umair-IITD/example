"""
case_engine/investigation/observation/templates.py

Sprint 2.44: Deterministic observation templates.

Seven templates (blueprint Section 14):
  OtpObservationTemplate      — OTP delivery failures
  VkycObservationTemplate     — VKYC session failures
  OcrObservationTemplate      — Document OCR failures
  ApiObservationTemplate      — API / callback failures
  PortalObservationTemplate   — Agent portal issues
  SessionObservationTemplate  — Session failures
  FallbackObservationTemplate — universal fallback

Invariants:
  - NO LLM. NO I/O. Pure string formatting from the ObservationDraft.
  - Every analytical value is rendered VERBATIM from RootCauseAnalysis.
  - Empty sections render "None detected." — uncertainty is never hidden.
  - Same draft → same text (fully deterministic; payload keys sorted).

Templates differ ONLY in static domain framing (issue summary line and
analyst note). All analytical sections are shared and identical.

Dependency direction:
  templates.py → observation/models.py (ObservationDraft, section headers)
  templates.py → stdlib only
"""
from __future__ import annotations

from case_engine.investigation.observation.models import ObservationDraft

__all__ = [
    "OtpObservationTemplate",
    "VkycObservationTemplate",
    "OcrObservationTemplate",
    "ApiObservationTemplate",
    "PortalObservationTemplate",
    "SessionObservationTemplate",
    "FallbackObservationTemplate",
    "build_issue_summary",
    "build_evidence_summary",
]

_NONE_DETECTED = "None detected."


# ── Shared section builders (identical across all templates) ───────────────────

def build_issue_summary(
    draft: ObservationDraft,
    framing_line: str,
    analyst_note: str,
) -> str:
    analysis = draft.analysis
    return (
        "=== ISSUE SUMMARY ===\n"
        f"{framing_line}\n"
        f"Case ID:  {draft.case_id}\n"
        f"Topic:    {draft.topic or 'UNKNOWN'}\n"
        f"Category: {analysis.category.value}\n"
        f"{analyst_note}"
    )


def build_evidence_summary(draft: ObservationDraft) -> str:
    lines: list[str] = ["=== EVIDENCE SUMMARY ==="]
    if not draft.evidence_items:
        lines.append("No evidence bundle was available for this observation.")
        return "\n".join(lines)

    for item in draft.evidence_items:
        status = "OK" if item.success else "FAILED"
        lines.append(f"[{status}] {item.tool_name} (evidence_id={item.evidence_id})")
        if item.success and item.payload:
            for key in sorted(item.payload):
                lines.append(f"  {key}: {item.payload[key]}")
        elif not item.success:
            code = item.error_code or "UNKNOWN_ERROR"
            message = item.error_message or "No details available."
            lines.append(f"  Error: {code} — {message}")

    successful = sum(1 for item in draft.evidence_items if item.success)
    lines.append(
        f"Evidence collected: {successful}/{len(draft.evidence_items)} tools succeeded."
    )
    return "\n".join(lines)


def _build_id_list_section(header: str, values: tuple[str, ...]) -> str:
    lines = [header]
    if not values:
        lines.append(_NONE_DETECTED)
    else:
        for value in values:
            lines.append(f"- {value}")
    return "\n".join(lines)


def _build_supporting_evidence(draft: ObservationDraft) -> str:
    return _build_id_list_section(
        "=== SUPPORTING EVIDENCE ===", tuple(draft.analysis.supporting_evidence)
    )


def _build_contradicting_evidence(draft: ObservationDraft) -> str:
    return _build_id_list_section(
        "=== CONTRADICTING EVIDENCE ===", tuple(draft.analysis.contradicting_evidence)
    )


def _build_missing_evidence(draft: ObservationDraft) -> str:
    return _build_id_list_section(
        "=== MISSING EVIDENCE ===", tuple(draft.analysis.missing_evidence)
    )


def _build_contradictions(draft: ObservationDraft) -> str:
    lines = ["=== CONTRADICTIONS ==="]
    contradictions = draft.analysis.decision_trace.contradictions_detected
    if not contradictions:
        lines.append(_NONE_DETECTED)
        return "\n".join(lines)
    for record in contradictions:
        lines.append(
            f"- [{record.severity.value}] {record.contradiction_id}: {record.description}"
        )
        lines.append(
            f"  evidence_a={record.evidence_a_id or 'N/A'}  "
            f"evidence_b={record.evidence_b_id or 'N/A'}"
        )
    return "\n".join(lines)


def _build_root_cause(draft: ObservationDraft) -> str:
    analysis = draft.analysis
    return (
        "=== ROOT CAUSE ===\n"
        f"Category:    {analysis.category.value}\n"
        f"Explanation: {analysis.explanation}"
    )


def _build_confidence(draft: ObservationDraft) -> str:
    analysis = draft.analysis
    breakdown = analysis.confidence_breakdown
    lines = [
        "=== CONFIDENCE ===",
        f"Final confidence: {analysis.confidence:.2f}",
        f"Base confidence:  {breakdown.base_confidence:.2f}",
    ]
    if breakdown.adjustments:
        lines.append("Adjustments:")
        for adjustment in breakdown.adjustments:
            lines.append(
                f"- {adjustment.delta:+.2f} [{adjustment.component}] {adjustment.reason}"
            )
    else:
        lines.append("Adjustments: none")
    return "\n".join(lines)


def _build_recommendation(draft: ObservationDraft) -> str:
    return (
        "=== RECOMMENDATION ===\n"
        f"Recommended action: {draft.analysis.recommended_action.value}"
    )


def _build_escalation(draft: ObservationDraft) -> str:
    escalation = draft.analysis.escalation_recommendation
    required = "YES" if escalation.should_escalate else "NO"
    return (
        "=== ESCALATION ===\n"
        f"Escalation required: {required}\n"
        f"Level:   {escalation.level.value}\n"
        f"Reason:  {escalation.reason}\n"
        f"Urgency: {escalation.urgency_score:.2f}"
    )


def _build_timeline(draft: ObservationDraft) -> str:
    lines = ["=== TIMELINE ==="]
    if not draft.timeline:
        lines.append(_NONE_DETECTED)
        return "\n".join(lines)
    for entry in draft.timeline:
        suffix = f" (evidence_id={entry.evidence_id})" if entry.evidence_id else ""
        lines.append(f"- {entry.timestamp} [{entry.source}] {entry.event}{suffix}")
    return "\n".join(lines)


def _build_investigation_steps(draft: ObservationDraft) -> str:
    lines = ["=== INVESTIGATION STEPS ==="]
    if not draft.investigation_steps:
        lines.append(_NONE_DETECTED)
        return "\n".join(lines)
    for step in draft.investigation_steps:
        collected = "evidence collected" if step.evidence_collected else "no evidence"
        lines.append(
            f"{step.sequence}. {step.tool_name} — {step.purpose} [{collected}]"
        )
    return "\n".join(lines)


def _build_decision_trace(draft: ObservationDraft) -> str:
    summary = draft.decision_trace_summary
    lines = [
        "=== DECISION TRACE ===",
        f"Rules evaluated: {summary.rules_evaluated}",
        f"Matches: {summary.matches}  Partial: {summary.partial_matches}  "
        f"No-match: {summary.no_matches}  Unknown: {summary.unknowns}",
        f"Contradictions: {summary.contradictions}",
        f"Missing evidence types: {summary.missing_evidence_count}",
    ]
    if summary.evaluation_order:
        lines.append("Evaluation order: " + " → ".join(summary.evaluation_order))
    else:
        lines.append("Evaluation order: none recorded")
    return "\n".join(lines)


def _build_evidence_references(draft: ObservationDraft) -> str:
    lines = ["=== EVIDENCE REFERENCES ==="]
    references = draft.analysis.evidence_references
    if not references:
        lines.append(_NONE_DETECTED)
        return "\n".join(lines)
    for reference in references:
        lines.append(
            f"- {reference.evidence_id} type={reference.evidence_type} "
            f"source={reference.source} weight={reference.weight:.2f}"
        )
    return "\n".join(lines)


def _build_audit(draft: ObservationDraft) -> str:
    analysis = draft.analysis
    audit = analysis.audit_metadata
    return (
        "=== AUDIT ===\n"
        f"Analysis ID:        {analysis.analysis_id}\n"
        f"Engine version:     {audit.engine_version}\n"
        f"Evidence bundle:    {audit.evidence_bundle_id}\n"
        f"Analyzed at:        {analysis.timestamp}\n"
        f"Generator version:  {draft.generator_version}\n"
        f"Generated at:       {draft.generated_at}\n"
        "[Generated by KwikID AI Support Agent — L1 Investigation Layer]"
    )


# ── Base template ──────────────────────────────────────────────────────────────

class _BaseObservationTemplate:
    """
    Shared rendering skeleton for all observation templates.

    Concrete templates override ONLY static domain framing
    (_framing_line / _analyst_note). All analytical sections are shared.
    """

    template_id:   str = ""
    template_name: str = ""
    priority:      int = 9999
    is_fallback:   bool = False
    _categories:   frozenset[str] = frozenset()
    _topics:       frozenset[str] = frozenset()

    def matches(self, category: str, topic: str) -> bool:
        normalized_topic = (topic or "").strip().upper()
        return (
            normalized_topic in self._topics
            or (category or "") in self._categories
        )

    def _framing_line(self, draft: ObservationDraft) -> str:
        return f"Support investigation completed for topic: {draft.topic or 'UNKNOWN'}."

    def _analyst_note(self, draft: ObservationDraft) -> str:
        return "Analyst note: review the evidence and root cause sections below."

    def render(self, draft: ObservationDraft) -> str:
        sections = [
            build_issue_summary(
                draft, self._framing_line(draft), self._analyst_note(draft)
            ),
            build_evidence_summary(draft),
            _build_supporting_evidence(draft),
            _build_contradicting_evidence(draft),
            _build_missing_evidence(draft),
            _build_contradictions(draft),
            _build_root_cause(draft),
            _build_confidence(draft),
            _build_recommendation(draft),
            _build_escalation(draft),
            _build_timeline(draft),
            _build_investigation_steps(draft),
            _build_decision_trace(draft),
            _build_evidence_references(draft),
            _build_audit(draft),
        ]
        return "\n\n".join(sections)


# ── Concrete templates ─────────────────────────────────────────────────────────

class OtpObservationTemplate(_BaseObservationTemplate):
    template_id   = "tpl-otp"
    template_name = "OTP Delivery Failure Observation"
    priority      = 10
    _categories   = frozenset({"SMS_DELIVERY_FAILURE"})
    _topics       = frozenset({"OTP_DELIVERY_FAILURE"})

    def _framing_line(self, draft: ObservationDraft) -> str:
        return "User reported not receiving a One-Time Password (OTP)."

    def _analyst_note(self, draft: ObservationDraft) -> str:
        return (
            "Analyst note: OTP delivery case — SMS gateway delivery status and "
            "provider responses are captured in the evidence summary below."
        )


class VkycObservationTemplate(_BaseObservationTemplate):
    template_id   = "tpl-vkyc"
    template_name = "VKYC Session Failure Observation"
    priority      = 20
    _categories   = frozenset({
        "LIVENESS_FAILURE",
        "KYC_REJECTED",
        "VALIDATION_FAILURE",
        "ONBOARDING_BLOCKED",
    })
    _topics       = frozenset({"VKYC_SESSION_FAILURE"})

    def _framing_line(self, draft: ObservationDraft) -> str:
        return "User reported a Video KYC (VKYC) session failure."

    def _analyst_note(self, draft: ObservationDraft) -> str:
        return (
            "Analyst note: VKYC case — session state, attempt counts, and "
            "failure codes are captured in the evidence summary below."
        )


class OcrObservationTemplate(_BaseObservationTemplate):
    template_id   = "tpl-ocr"
    template_name = "Document OCR Failure Observation"
    priority      = 30
    _categories   = frozenset({"DOCUMENT_FAILURE"})
    _topics       = frozenset({"DOCUMENT_OCR_FAILURE"})

    def _framing_line(self, draft: ObservationDraft) -> str:
        return "Document OCR processing failed during onboarding."

    def _analyst_note(self, draft: ObservationDraft) -> str:
        return (
            "Analyst note: OCR case — document processing results and failure "
            "codes are captured in the evidence summary below."
        )


class ApiObservationTemplate(_BaseObservationTemplate):
    template_id   = "tpl-api"
    template_name = "API Callback Failure Observation"
    priority      = 40
    _categories   = frozenset({
        "CALLBACK_FAILURE",
        "NETWORK_FAILURE",
        "TIMEOUT",
        "QUOTA_EXCEEDED",
    })
    _topics       = frozenset({"API_CALLBACK_FAILURE"})

    def _framing_line(self, draft: ObservationDraft) -> str:
        return "API callback to the partner system failed to deliver."

    def _analyst_note(self, draft: ObservationDraft) -> str:
        return (
            "Analyst note: API/callback case — HTTP statuses, failure codes, "
            "and transience flags are captured in the evidence summary below."
        )


class PortalObservationTemplate(_BaseObservationTemplate):
    template_id   = "tpl-portal"
    template_name = "Agent Portal Issue Observation"
    priority      = 50
    _categories   = frozenset({"PORTAL_UNAVAILABLE"})
    _topics       = frozenset({"AGENT_PORTAL_ISSUE"})

    def _framing_line(self, draft: ObservationDraft) -> str:
        return "Agent reported an issue with the support portal."

    def _analyst_note(self, draft: ObservationDraft) -> str:
        return (
            "Analyst note: portal case — availability and access evidence is "
            "captured in the evidence summary below."
        )


class SessionObservationTemplate(_BaseObservationTemplate):
    template_id   = "tpl-session"
    template_name = "Session Failure Observation"
    priority      = 60
    _categories   = frozenset({"EXPIRED_SESSION", "REPEATED_FAILURE"})
    _topics       = frozenset({"SESSION_FAILURE"})

    def _framing_line(self, draft: ObservationDraft) -> str:
        return "A user session failed or expired during the flow."

    def _analyst_note(self, draft: ObservationDraft) -> str:
        return (
            "Analyst note: session case — session lifecycle and retry evidence "
            "is captured in the evidence summary below."
        )


class FallbackObservationTemplate(_BaseObservationTemplate):
    """
    Universal fallback — matches every category and topic.

    Guarantees the generator can always produce a note, including for
    UNKNOWN categories and unrecognized topics.
    """
    template_id   = "tpl-fallback"
    template_name = "Fallback Observation"
    priority      = 9999
    is_fallback   = True

    def matches(self, category: str, topic: str) -> bool:
        return True

    def _framing_line(self, draft: ObservationDraft) -> str:
        return f"Support investigation completed for topic: {draft.topic or 'UNKNOWN'}."

    def _analyst_note(self, draft: ObservationDraft) -> str:
        return (
            "Analyst note: no domain-specific template matched this case — "
            "review all sections below before acting."
        )
