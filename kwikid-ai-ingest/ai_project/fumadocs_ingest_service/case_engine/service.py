"""
case_engine/service.py

CaseService — high-level orchestrator for Sprint 1 case lifecycle.

This is the single entry point used by app/main.py.

Contract:
- Phase 1 RAG pipeline is never called from here — it is called by the
  webhook handler and its result is passed IN to this service.
- If any method here fails, the exception is caught by the webhook handler
  and the Phase 1 flow continues unaffected.
- All state machine transitions are atomic in-memory; persistence is best-effort.
"""
from __future__ import annotations

import logging
from typing import Any

from case_engine.audit import AuditLogger
from case_engine.case_state import CaseState
from case_engine.classifier import TopicClassifier
from case_engine.escalation import EscalationDecision, EscalationEngine, EscalationTrigger
from case_engine.models import Case
from case_engine.repository import CaseRepository
from case_engine.state_machine import CaseStateMachine
from case_engine.transfer_context import TransferContextPayload

LOGGER = logging.getLogger(__name__)


class CaseService:
    """
    Orchestrates the full case lifecycle for a single webhook event.

    Designed to be called from the Freshdesk webhook handler.
    Each method is safe to call even if a previous step failed.
    """

    def __init__(
        self,
        repository: CaseRepository,
        audit_logger: AuditLogger,
        classifier: TopicClassifier | None = None,
        escalation_engine: EscalationEngine | None = None,
    ) -> None:
        self._repo    = repository
        self._audit   = audit_logger
        self._clf     = classifier or TopicClassifier()
        self._esc_eng = escalation_engine or EscalationEngine()
        self._sm      = CaseStateMachine(
            on_transition=self._on_transition,
        )

    # ── Phase 1 integration points ─────────────────────────────────────────────

    def open_case(self, ticket_id: str, client: str) -> Case:
        """
        Create (or retrieve existing) case for this ticket.

        State: NEW (or existing state if case already existed).
        Called at webhook ingress, before any classification or retrieval.
        """
        # Check for existing case (webhook retry scenario)
        existing = self._repo.get_case_by_ticket(ticket_id, client)
        if existing is not None:
            LOGGER.info(
                "case_service.open_case: found existing case %s state=%s ticket=%s",
                existing.case_id, existing.current_state.value, ticket_id,
            )
            return existing

        case = self._repo.create_case(ticket_id, client)
        if case is None:
            # Should not happen (repo always returns a case), but be defensive
            case = Case(ticket_id=ticket_id, client=client)
            LOGGER.warning(
                "case_service.open_case: repo returned None, using in-memory case ticket=%s",
                ticket_id,
            )

        LOGGER.info(
            "case_service.open_case: created case=%s state=%s ticket=%s client=%s",
            case.case_id, case.current_state.value, ticket_id, client,
        )
        return case

    def classify_case(self, case: Case, query_text: str) -> Case:
        """
        Run topic classifier and transition case to TRIAGE_COMPLETE or ESCALATED.

        On TRIAGE_COMPLETE: case.topic and case.confidence are set.
        On ESCALATED: case.escalation_reason is set.
        """
        # Transition NEW → CLASSIFYING
        self._sm.safe_transition(case, CaseState.CLASSIFYING, reason="topic_classification_started")

        result = self._clf.classify(query_text)

        # Log classification event
        self._audit.log_classification(
            case,
            topic=result.topic.value,
            confidence=result.confidence,
            tier_used=result.tier_used,
            meets_threshold=result.meets_threshold,
        )

        if result.meets_threshold:
            case.topic      = result.topic.value
            case.confidence = result.confidence
            self._repo.update_case_state(case, CaseState.CLASSIFYING)
            self._sm.safe_transition(
                case,
                CaseState.TRIAGE_COMPLETE,
                reason=f"classified:{result.topic.value} confidence={result.confidence:.3f} tier={result.tier_used}",
            )
        else:
            # Below threshold or unknown topic → escalate
            trigger = (
                EscalationTrigger.UNKNOWN_TOPIC.value
                if result.topic.value == "UNKNOWN"
                else EscalationTrigger.BELOW_THRESHOLD.value
            )
            reason = (
                f"Topic unknown — confidence {result.confidence:.3f} below 0.85"
                if result.topic.value == "UNKNOWN"
                else f"Confidence {result.confidence:.3f} below 0.85 threshold"
            )
            case.escalation_reason = trigger
            self._sm.safe_transition(case, CaseState.ESCALATED, reason=reason)
            self._audit.log_escalation(
                case,
                trigger=trigger,
                reason=reason,
                priority="medium",
            )

        self._repo.update_case_state(case, case.current_state)
        return case

    def evaluate_rag_result(
        self,
        case: Case,
        *,
        match_type: str,
        confidence: str,
        requires_human: bool,
        chunks_count: int,
        ticket_text: str = "",
        cited_sop_ids: list[str] | None = None,
    ) -> EscalationDecision:
        """
        Evaluate the RAG result and determine if the case should be escalated.

        This is called AFTER the existing Phase 1 RAG pipeline completes.
        It does not modify the RAG pipeline.

        Returns EscalationDecision.
        """
        # Log the RAG call
        self._audit.log_rag_call(
            case,
            match_type=match_type,
            confidence=confidence,
            chunks_count=chunks_count,
            requires_human=requires_human,
            cited_sop_ids=cited_sop_ids,
        )
        case.retrieval_match_type = match_type

        decision = self._esc_eng.evaluate(
            case,
            classification_confidence=case.confidence,
            topic_known=case.topic is not None and case.topic != "UNKNOWN",
            match_type=match_type,
            generation_confidence=confidence,
            requires_human=requires_human,
            ticket_text=ticket_text,
        )

        if decision.should_escalate and case.current_state not in (
            CaseState.ESCALATED, CaseState.RESOLVED, CaseState.FAILED, CaseState.CLOSED,
        ):
            trigger = decision.trigger.value if decision.trigger else "unknown"
            case.escalation_reason = trigger
            self._sm.safe_transition(
                case,
                CaseState.ESCALATED,
                reason=decision.reason,
            )
            self._audit.log_escalation(
                case,
                trigger=trigger,
                reason=decision.reason,
                priority=decision.priority.value,
            )
            self._repo.update_case_state(case, CaseState.ESCALATED)

        return decision

    def record_note_posted(
        self,
        case: Case,
        *,
        note_type: str,   # "diagnostic" | "escalation" | "transfer_context"
        confidence: str,
    ) -> None:
        """Record that a Freshdesk note was posted successfully."""
        self._audit.log_note_posted(case, note_type=note_type, confidence=confidence)

    def resolve_case(self, case: Case, *, reason: str = "workflow_resolved") -> None:
        """Close the case after Level 1 note posting.

        Level 1 path: TRIAGE_COMPLETE → CLOSED.
        WORKFLOW_ACTIVE is reserved exclusively for Level 2 durable workflow execution.
        """
        if case.current_state != CaseState.TRIAGE_COMPLETE:
            return  # Only TRIAGE_COMPLETE is closeable at Level 1

        self._sm.safe_transition(case, CaseState.CLOSED, reason=reason)
        self._repo.update_case_state(case, CaseState.CLOSED)

    def build_transfer_context(
        self,
        case: Case,
        *,
        escalation_trigger: str | None = None,
        escalation_reason: str | None = None,
        root_cause_analysis: str | None = None,
        recommended_action: str | None = None,
        cited_sop_ids: list[str] | None = None,
    ) -> TransferContextPayload:
        """Build a Transfer Context Payload for this case."""
        return TransferContextPayload.build_from_case(
            case,
            escalation_trigger=escalation_trigger,
            escalation_reason=escalation_reason,
            root_cause_analysis=root_cause_analysis,
            recommended_action=recommended_action,
            cited_sop_ids=cited_sop_ids,
        )

    def record_error(self, case: Case, error_type: str, error_msg: str) -> None:
        """Record a system error against this case (never raises)."""
        self._audit.log_error(case, error_type=error_type, error_msg=error_msg)

    # ── Internal callback ──────────────────────────────────────────────────────

    def _on_transition(self, case: Case, transition: Any) -> None:
        """Called by state machine on every valid transition."""
        self._audit.log_transition(case, transition)
        self._repo.record_transition(transition)


def build_case_service(supabase_client: Any = None) -> CaseService:
    """
    Factory: build a CaseService with the given Supabase client.

    Pass supabase_client=None for offline / test use.
    """
    repo    = CaseRepository(supabase_client)
    auditor = AuditLogger(supabase_client)
    return CaseService(repository=repo, audit_logger=auditor)
