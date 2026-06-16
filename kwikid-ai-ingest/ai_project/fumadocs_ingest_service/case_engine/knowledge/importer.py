"""
case_engine/knowledge/importer.py

Sprint 2.20: StackOverflow Teams export importer.

Per blueprint Section 31 (Knowledge System):
  Knowledge Source: StackOverflow Teams Export
  Knowledge Format: JSON export uploaded manually by administrators.

Prepares architecture for future StackOverflow zip ingestion.

SO Teams export format supported:
  {
      "questions": [
          {
              "Id": 1,
              "Title": "How to reset a failed VKYC session",
              "Body": "<p>...</p>",
              "Tags": ["vkyc", "session_reset"],
              "Score": 10,
              "AcceptedAnswerId": 2,
              "CreationDate": "2024-01-15T10:00:00Z",
              "Answers": [
                  {
                      "Id": 2,
                      "Body": "<p>1. Navigate to Admin Portal...</p>",
                      "Score": 8,
                      "IsAccepted": true,
                      "CreationDate": "2024-01-15T11:00:00Z"
                  }
              ]
          }
      ]
  }

Design:
  - Never raises — malformed entries are skipped with a warning
  - No LLM — deterministic tag→topic/root_cause mapping
  - Idempotent — re-importing same export produces same entry_ids
  - HTML is stripped from body text
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from case_engine.knowledge.models import (
    KnowledgeEntry,
    KnowledgeEntryStatus,
    KnowledgeEntryType,
)

LOGGER = logging.getLogger(__name__)

# ── Tag → domain mapping tables ───────────────────────────────────────────────
# These deterministic maps convert SO tags into structured domain values.
# Future: extend via admin configuration.

_TAG_TO_TOPIC: dict[str, str] = {
    "vkyc":             "VKYC_Session_Failure",
    "kyc":              "VKYC_Session_Failure",
    "session":          "VKYC_Session_Failure",
    "video_kyc":        "VKYC_Session_Failure",
    "otp":              "OTP_Delivery_Failure",
    "sms":              "OTP_Delivery_Failure",
    "otp_delivery":     "OTP_Delivery_Failure",
    "ocr":              "Document_OCR_Failure",
    "document":         "Document_OCR_Failure",
    "document_ocr":     "Document_OCR_Failure",
    "pan":              "Document_OCR_Failure",
    "aadhaar":          "Document_OCR_Failure",
    "portal":           "Agent_Portal_Issue",
    "agent_portal":     "Agent_Portal_Issue",
    "agent":            "Agent_Portal_Issue",
    "callback":         "API_Callback_Failure",
    "api":              "API_Callback_Failure",
    "webhook":          "API_Callback_Failure",
    "callback_failure": "API_Callback_Failure",
}

_TAG_TO_ROOT_CAUSE: dict[str, str] = {
    "expired_session":    "EXPIRED_SESSION",
    "session_timeout":    "EXPIRED_SESSION",
    "session_expired":    "EXPIRED_SESSION",
    "timeout":            "TIMEOUT",
    "network":            "NETWORK_FAILURE",
    "network_failure":    "NETWORK_FAILURE",
    "quota_exceeded":     "QUOTA_EXCEEDED",
    "repeated_failure":   "REPEATED_FAILURE",
    "liveness":           "LIVENESS_FAILURE",
    "liveness_failure":   "LIVENESS_FAILURE",
    "document_failure":   "DOCUMENT_FAILURE",
    "ocr_failure":        "DOCUMENT_FAILURE",
    "validation":         "VALIDATION_FAILURE",
    "validation_failure": "VALIDATION_FAILURE",
    "kyc_rejected":       "KYC_REJECTED",
    "sms_failure":        "SMS_DELIVERY_FAILURE",
    "sms_delivery":       "SMS_DELIVERY_FAILURE",
    "callback_failure":   "CALLBACK_FAILURE",
    "api_failure":        "CALLBACK_FAILURE",
    "onboarding_blocked": "ONBOARDING_BLOCKED",
    "portal_down":        "PORTAL_UNAVAILABLE",
    "portal_unavailable": "PORTAL_UNAVAILABLE",
}

_TAG_TO_RECOMMENDED_ACTION: dict[str, str] = {
    "session_reset":    "SESSION_RESET",
    "reset_session":    "SESSION_RESET",
    "otp_resend":       "OTP_RESEND",
    "resend_otp":       "OTP_RESEND",
    "portal_refresh":   "PORTAL_REFRESH",
    "refresh_portal":   "PORTAL_REFRESH",
    "callback_retry":   "CALLBACK_RETRY",
    "retry_callback":   "CALLBACK_RETRY",
    "auto_advance":     "AUTO_ADVANCE",
    "manual_review":    "MANUAL_REVIEW",
    "escalate":         "ESCALATE",
    "retry":            "RETRY",
}

_TAG_TO_ENTRY_TYPE: dict[str, KnowledgeEntryType] = {
    "sop":                   KnowledgeEntryType.SOP,
    "faq":                   KnowledgeEntryType.FAQ,
    "runbook":               KnowledgeEntryType.RUNBOOK,
    "known_issue":           KnowledgeEntryType.KNOWN_ISSUE,
    "engineering_fix":       KnowledgeEntryType.ENGINEERING_FIX,
    "historical_resolution": KnowledgeEntryType.HISTORICAL_RESOLUTION,
}

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_SPACE_RE = re.compile(r"\s{2,}")


def _strip_html(text: str) -> str:
    """Remove HTML tags and normalise whitespace."""
    text = _HTML_TAG_RE.sub(" ", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    return text.strip()


def _extract_steps(body: str) -> tuple[str, ...]:
    """
    Heuristically extract numbered steps from a body.

    Looks for lines that start with a number followed by a period or
    parenthesis, or lines starting with common step markers.
    Returns empty tuple if no structured steps are detected.
    """
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    step_pattern = re.compile(r"^\d+[\.\)]\s+.+")
    steps = [l for l in lines if step_pattern.match(l)]
    return tuple(steps[:20])  # cap at 20 steps


def _map_tags(
    tags: list[str],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """
    Map raw SO tag strings to topic_keys, root_cause_categories, recommended_actions.

    Returns (topic_keys, root_cause_categories, recommended_actions) — each deduplicated.
    """
    topics: list[str] = []
    root_causes: list[str] = []
    actions: list[str] = []

    for tag in tags:
        tag_lower = tag.lower().replace("-", "_")
        if t := _TAG_TO_TOPIC.get(tag_lower):
            if t not in topics:
                topics.append(t)
        if rc := _TAG_TO_ROOT_CAUSE.get(tag_lower):
            if rc not in root_causes:
                root_causes.append(rc)
        if a := _TAG_TO_RECOMMENDED_ACTION.get(tag_lower):
            if a not in actions:
                actions.append(a)

    return tuple(topics), tuple(root_causes), tuple(actions)


def _entry_type_from_tags(tags: list[str]) -> KnowledgeEntryType:
    for tag in tags:
        if et := _TAG_TO_ENTRY_TYPE.get(tag.lower()):
            return et
    return KnowledgeEntryType.FAQ


def _stable_entry_id(source_id: str, source: str) -> str:
    """
    Generate a stable UUID from source_id + source.

    Re-importing the same export always produces the same entry_id.
    Uses UUID5 (SHA-1 namespace + name → deterministic UUID).
    """
    namespace = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # UUID_NAMESPACE_URL
    return str(uuid.uuid5(namespace, f"{source}:{source_id}"))


class StackOverflowImporter:
    """
    Converts a StackOverflow Teams JSON export into KnowledgeEntry objects.

    Usage:
        importer = StackOverflowImporter()
        entries = importer.import_from_dict(so_export_dict)

    The returned list is ready to be stored in any KnowledgeRepository via
    repository.bulk_store(entries).

    Never raises — malformed questions are logged and skipped.
    """

    def import_from_dict(self, data: dict[str, Any]) -> list[KnowledgeEntry]:
        """
        Import from a parsed StackOverflow Teams export dict.

        Handles both 'questions' and 'items' top-level keys.
        """
        questions = data.get("questions") or data.get("items") or []
        entries: list[KnowledgeEntry] = []

        for q in questions:
            try:
                entry = self._parse_question(q)
                if entry is not None:
                    entries.append(entry)
            except Exception:
                q_id = q.get("Id", "unknown") if isinstance(q, dict) else "unknown"
                LOGGER.warning(
                    "knowledge_importer: skipping malformed question id=%s",
                    q_id,
                )

        LOGGER.info(
            "knowledge_importer.import_complete total_questions=%d entries_created=%d",
            len(questions),
            len(entries),
        )
        return entries

    def import_from_json(self, json_str: str) -> list[KnowledgeEntry]:
        """Import from a raw JSON string."""
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            LOGGER.error("knowledge_importer: invalid JSON — %s", exc)
            return []
        return self.import_from_dict(data)

    # ── Private ───────────────────────────────────────────────────────────────

    def _parse_question(self, q: dict[str, Any]) -> KnowledgeEntry | None:
        question_id = str(q.get("Id", ""))
        title       = str(q.get("Title") or "").strip()
        question_body = _strip_html(str(q.get("Body") or ""))
        tags          = [str(t) for t in (q.get("Tags") or [])]
        score         = int(q.get("Score") or 0)
        accepted_id   = q.get("AcceptedAnswerId")
        created_at    = str(q.get("CreationDate") or "")
        answers       = q.get("Answers") or []

        if not title:
            return None

        # Find best body: accepted answer > highest-score answer > question body
        body_text, is_accepted = self._best_answer_body(answers, accepted_id)
        if not body_text:
            body_text = question_body
            is_accepted = False

        topic_keys, root_causes, rec_actions = _map_tags(tags)
        entry_type = _entry_type_from_tags(tags)
        steps = _extract_steps(body_text)
        entry_id = _stable_entry_id(question_id, "stackoverflow_teams")

        return KnowledgeEntry(
            entry_id=entry_id,
            title=title,
            body=body_text,
            entry_type=entry_type,
            tags=tuple(tags),
            topic_keys=topic_keys,
            root_cause_categories=root_causes,
            recommended_actions=rec_actions,
            resolution_steps=steps,
            source="stackoverflow_teams",
            source_id=question_id,
            accepted_answer=is_accepted,
            score=score,
            created_at=created_at,
            status=KnowledgeEntryStatus.ACTIVE,
        )

    def _best_answer_body(
        self,
        answers: list[dict[str, Any]],
        accepted_id: Any,
    ) -> tuple[str, bool]:
        """
        Return (body_text, is_accepted_answer) for the best answer.

        Preference order:
        1. Accepted answer (IsAccepted=true or Id==AcceptedAnswerId)
        2. Highest score answer
        """
        if not answers:
            return "", False

        accepted: dict[str, Any] | None = None
        best_by_score: dict[str, Any] = answers[0]

        for ans in answers:
            ans_id = ans.get("Id")
            is_accepted_flag = bool(ans.get("IsAccepted", False))
            if is_accepted_flag or (accepted_id is not None and ans_id == accepted_id):
                accepted = ans
                break
            ans_score = int(ans.get("Score") or 0)
            if ans_score > int(best_by_score.get("Score") or 0):
                best_by_score = ans

        chosen = accepted if accepted is not None else best_by_score
        body = _strip_html(str(chosen.get("Body") or ""))
        was_accepted = chosen is accepted and accepted is not None
        return body, was_accepted
