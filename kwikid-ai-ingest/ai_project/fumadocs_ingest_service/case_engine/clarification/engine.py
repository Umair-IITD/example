"""
case_engine/clarification/engine.py

Sprint 2.25: WorkflowClarificationEngine -- checks workflow slot state and
generates clarification questions when required slots are missing.

flow_diagram path:
    Missing Slots --> CLARIFICATION_ENGINE --> Clarification Question --> Customer
    Customer Response --> Slot Filled --> Resume Workflow

Design:
  - Deterministic. No LLM. Never raises.
  - Input:  topic, slot_context (dict[str, str]), required_slots, slot_state
  - Output: ClarificationResult with READY | NEEDS_CLARIFICATION | ESCALATE

Blueprint alignment:
  The clarification loop (Missing Slots -> Clarification -> User Response -> Resume)
  is the pre-condition gate before the INVESTIGATE step in the workflow pipeline.
  Slot completeness is mandatory before evidence collection begins.
"""
from __future__ import annotations

import logging
from typing import Any

from case_engine.clarification.models import (
    ClarificationResult,
    ClarificationStatus,
    MissingSlotInfo,
    _new_id,
)

LOGGER = logging.getLogger(__name__)

# Default max attempts before escalation
_DEFAULT_MAX_ATTEMPTS: int = 2

# Customer-facing prompt templates for known slot names
_SLOT_PROMPTS: dict[str, str] = {
    "session_id":     "Please provide the session ID (format: KID-XXXXXXXX).",
    "phone_number":   "Please provide the registered mobile phone number.",
    "channel":        "Please specify the OTP delivery channel: SMS, EMAIL, or VOICE.",
    "callback_type":  "Please specify the callback type: CBS, DMS, SFDC, or WEBHOOK.",
    "application_id": "Please provide the application ID.",
    "document_type":  "Please specify the document type: AADHAAR, PAN, PASSPORT, or VOTERID.",
    "agent_id":       "Please provide your agent ID.",
    "portal_type":    "Please specify the portal type: WEB, MOBILE, or DESKTOP.",
    "operation_id":   "Please provide the operation ID for this request.",
    "failure_code":   "Please provide the failure code shown in the error message.",
    "error_message":  "Please provide the error message you received.",
    "attempt_count":  "Please confirm how many OTP attempts have been made.",
}

_DEFAULT_PROMPT = "Please provide your {slot_name}."


def _prompt_for(slot_name: str) -> str:
    return _SLOT_PROMPTS.get(
        slot_name,
        _DEFAULT_PROMPT.format(slot_name=slot_name.replace("_", " ")),
    )


class WorkflowClarificationEngine:
    """
    Determines whether a workflow can proceed based on slot state.

    Same input --> same output (deterministic).
    Never raises: exceptions produce a READY result with empty missing_slots.
    """

    def clarify(
        self,
        topic:          str,
        slot_context:   dict[str, str],
        required_slots: tuple[str, ...] | list[str],
        slot_state:     dict[str, Any] | None = None,
    ) -> ClarificationResult:
        """
        Check required_slots against slot_context.

        Args:
          topic          : TopicKey string (informational — used for logging)
          slot_context   : flat dict of currently-available slot values
          required_slots : names of slots that must be present
          slot_state     : optional JSONB slot state (dict of slot_name -> {attempt_count, ...})
                           used to detect max-attempts-exceeded conditions

        Returns:
          READY               when all required_slots are present in slot_context
          NEEDS_CLARIFICATION when at least one slot is missing (first missing slot)
          ESCALATE            when a slot's attempt_count >= max_attempts
        """
        try:
            return self._clarify(
                topic=topic,
                slot_context=slot_context,
                required_slots=tuple(required_slots),
                slot_state=slot_state or {},
            )
        except Exception:
            LOGGER.exception(
                "clarification_engine.clarify failed topic=%s — returning READY (fail-open)",
                topic,
            )
            return self._ready_result()

    # ── Private ────────────────────────────────────────────────────────────────

    def _clarify(
        self,
        topic:          str,
        slot_context:   dict[str, str],
        required_slots: tuple[str, ...],
        slot_state:     dict[str, Any],
    ) -> ClarificationResult:
        if not required_slots:
            return self._ready_result()

        missing: list[str] = [
            s for s in required_slots
            if not slot_context.get(s)
        ]

        if not missing:
            return self._ready_result()

        # Check if any missing slot has exceeded max attempts
        for slot_name in missing:
            attempt_count, max_attempts = self._slot_attempts(slot_name, slot_state)
            if attempt_count >= max_attempts:
                LOGGER.warning(
                    "clarification_engine: slot '%s' exceeded max_attempts=%d"
                    " topic=%s — escalating",
                    slot_name, max_attempts, topic,
                )
                return ClarificationResult(
                    result_id=_new_id(),
                    status=ClarificationStatus.ESCALATE,
                    missing_slots=tuple(missing),
                    next_question=None,
                    clarification_message=(
                        f"Maximum clarification attempts reached for '{slot_name}'. "
                        "Escalating to a human support agent."
                    ),
                    ready_to_continue=False,
                )

        # Generate question for the first missing slot
        first_missing = missing[0]
        attempt_count, max_attempts = self._slot_attempts(first_missing, slot_state)
        prompt = _prompt_for(first_missing)

        question = MissingSlotInfo(
            slot_name=first_missing,
            prompt_text=prompt,
            attempt_count=attempt_count,
            max_attempts=max_attempts,
        )

        LOGGER.info(
            "clarification_engine: %d missing slot(s) topic=%s first='%s'",
            len(missing), topic, first_missing,
        )

        return ClarificationResult(
            result_id=_new_id(),
            status=ClarificationStatus.NEEDS_CLARIFICATION,
            missing_slots=tuple(missing),
            next_question=question,
            clarification_message=prompt,
            ready_to_continue=False,
        )

    def _slot_attempts(
        self,
        slot_name:  str,
        slot_state: dict[str, Any],
    ) -> tuple[int, int]:
        """Return (attempt_count, max_attempts) for a slot from slot_state."""
        info = slot_state.get(slot_name)
        if isinstance(info, dict):
            return (
                int(info.get("attempt_count", 0)),
                int(info.get("max_attempts", _DEFAULT_MAX_ATTEMPTS)),
            )
        return (0, _DEFAULT_MAX_ATTEMPTS)

    def _ready_result(self) -> ClarificationResult:
        return ClarificationResult(
            result_id=_new_id(),
            status=ClarificationStatus.READY,
            missing_slots=(),
            next_question=None,
            clarification_message="All required information is available.",
            ready_to_continue=True,
        )


def build_clarification_engine() -> WorkflowClarificationEngine:
    """Factory: return a configured WorkflowClarificationEngine."""
    return WorkflowClarificationEngine()
