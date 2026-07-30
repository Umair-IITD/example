"""
case_engine/service.py

CaseService — high-level orchestrator for the case lifecycle.

Sprint 1 (Level 1): classify ticket, evaluate RAG result, resolve or escalate.
Sprint 2.15 (foundation): receive_message handles slot filling and clarification.

Contract:
- Phase 1 RAG pipeline is never called from here — it is called by the
  webhook handler and its result is passed IN to this service.
- If any method here fails, the exception is caught by the webhook handler
  and the Phase 1 flow continues unaffected.
- All state machine transitions are atomic in-memory; persistence is best-effort.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from case_engine.audit import AuditLogger
from case_engine.case_state import CaseState
from case_engine.clarification_engine import ClarificationEngine
from case_engine.classifier import TopicClassifier
from case_engine.escalation import EscalationDecision, EscalationEngine, EscalationTrigger
from case_engine.models import Case, TopicKey
from case_engine.repository import CaseRepository
from case_engine.slot_filling.models import ClarificationQuestion, SlotStatus, SlotValue
from case_engine.state_machine import CaseStateMachine
from case_engine.transfer_context import TransferContextPayload
from case_engine.workflows.consistency import WorkflowConsistencyChecker, WorkflowConsistencyError
from case_engine.workflows.models import WorkflowExecutionResult, WorkflowState
from case_engine.workflows.workflow_engine import WorkflowEngine

LOGGER = logging.getLogger(__name__)

# Workflow states that indicate an active execution already running
_ACTIVE_WORKFLOW_STATES: frozenset[str] = frozenset({
    WorkflowState.RUNNING.value,
    WorkflowState.PAUSED.value,
})

# ── UNKNOWN topic discovery ────────────────────────────────────────────────────
# Reserved slot name used to track topic-discovery clarification attempts.
# This slot never appears in any SlotRegistry; it is managed exclusively by
# _handle_unknown_topic_discovery().
_TOPIC_DISCOVERY_SLOT = "__topic_discovery__"

# Maximum number of topic-discovery clarification rounds before escalation.
_MAX_DISCOVERY_ATTEMPTS: int = 2

# The single topic-discovery question sent to the customer when topic is UNKNOWN.
_TOPIC_DISCOVERY_QUESTION: dict[str, Any] = {
    "slot_name":   _TOPIC_DISCOVERY_SLOT,
    "prompt_text": (
        "Could you please describe your issue in more detail? "
        "For example: are you experiencing a Video KYC failure, OTP not received, "
        "document scan error, agent portal issue, or something else?"
    ),
    "is_required": True,
}


class WorkflowAlreadyStartedError(RuntimeError):
    """Raised when start_workflow is called for a case that already has an active workflow."""


# ── Message result ─────────────────────────────────────────────────────────────

@dataclass
class ReceiveMessageResult:
    """Result returned by CaseService.receive_message."""
    case_id:          str
    state:            CaseState
    slot_values:      dict[str, dict]        # {slot_name: {status, value, attempt_count}}
    next_question:    dict[str, Any] | None  # serialized ClarificationQuestion or None
    all_slots_filled: bool = False
    escalated:        bool = False
    workflow_started: bool = False           # True if workflow began after slot completion


@dataclass
class WorkflowStartResult:
    """Result returned by CaseService.start_workflow."""
    case_id:           str
    state:             CaseState
    workflow_id:       str | None
    workflow_state:    str                    # WorkflowState value
    current_step_id:   str | None
    pending_action_id: str | None = None
    resolved:          bool = False
    escalated:         bool = False
    step_results:      list[dict[str, Any]] = field(default_factory=list)
    resolution_note:   str | None = None
    escalation_reason: str | None = None


# ── Service ────────────────────────────────────────────────────────────────────

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
        clarification_engine: ClarificationEngine | None = None,
        workflow_engine: WorkflowEngine | None = None,
        playbook_registry: Any = None,
        action_gateway: Any = None,
    ) -> None:
        self._repo      = repository
        self._audit     = audit_logger
        # Sprint 2.5.6: build classifier with LLM router if no classifier is provided
        if classifier is None:
            try:
                from case_engine.classifier import build_topic_classifier  # noqa: PLC0415
                classifier = build_topic_classifier()
            except Exception as _exc:
                LOGGER.warning("case_service: failed to build LLM classifier error=%s — using TopicClassifier()", _exc)
                classifier = TopicClassifier()
        self._clf       = classifier
        self._esc_eng   = escalation_engine or EscalationEngine()
        self._clarify   = clarification_engine or ClarificationEngine()
        self._wf_engine = workflow_engine or WorkflowEngine()
        self._registry  = playbook_registry   # PlaybookRegistry | None
        self._gateway   = action_gateway      # ActionGateway | None
        self._sm        = CaseStateMachine(
            on_transition=self._on_transition,
        )
        self._wf_checker = WorkflowConsistencyChecker()

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

        LOGGER.warning(
            "ENTER_CLASSIFIER_SVC case_id=%s query_len=%d query_preview=%r",
            case.case_id, len(query_text or ""), (query_text or "")[:120],
        )
        result = self._clf.classify(query_text)
        LOGGER.warning(
            "EXIT_CLASSIFIER topic=%s confidence=%s meets_threshold=%s tier=%s nlp_signal_present=%s",
            result.topic.value, result.confidence, result.meets_threshold, result.tier_used,
            result.nlp_signal is not None,
        )

        # Sprint 2.5.6: propagate NLPSignal to case for downstream slot extraction.
        # The NLPSignal carries pre-extracted entities (urn, session_id, etc.) and the
        # needs_clarification flag. SupportAgentRuntime reads case.nlp_signal to
        # pre-fill slots from LLM-extracted entities instead of regex.
        if result.nlp_signal is not None:
            try:
                case.nlp_signal = result.nlp_signal  # type: ignore[attr-defined]
            except Exception:
                pass

        # TRACE_AUDIT_IDENTITY — proves which AuditLogger class is injected at runtime
        LOGGER.warning(
            "TRACE_AUDIT_IDENTITY_CLASSIFY audit_type=%s audit_module=%s has_log_classification=%s",
            type(self._audit).__name__,
            getattr(type(self._audit), "__module__", "?"),
            hasattr(self._audit, "log_classification"),
        )
        # Log classification event
        LOGGER.info("ENTER_CASESVC_AUDIT case_id=%s type=%s", case.case_id, type(case.case_id).__name__)
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
            # Below threshold or unknown topic.
            #
            # Per flow_diagram.mermaid (CLASSIFIER --> SLOTEXTRACT), UNKNOWN-topic
            # tickets must proceed to Slot Extraction and Clarification so the
            # system can ask the user for more information to determine the topic.
            # Transitioning directly to ESCALATED here violates that contract and
            # terminates the pipeline before it can clarify the issue.
            #
            # Only BELOW_THRESHOLD (known topic, insufficient confidence) is
            # escalated immediately; UNKNOWN topics are allowed to continue so
            # that the Clarification Engine can collect the missing context.
            case.topic = result.topic.value      # preserve UNKNOWN on case
            case.confidence = result.confidence  # preserve 0.0 on case

            if result.topic.value == "UNKNOWN":
                # UNKNOWN → proceed to TRIAGE_COMPLETE so runtime enters
                # SLOT_EXTRACT / CLARIFY per the approved blueprint.
                LOGGER.warning(
                    "UNKNOWN_TOPIC_TRIAGE_COMPLETE case_id=%s confidence=%s — "
                    "proceeding to Slot Extraction for clarification",
                    case.case_id, result.confidence,
                )
                self._sm.safe_transition(
                    case,
                    CaseState.TRIAGE_COMPLETE,
                    reason="unknown_topic_proceeding_to_clarification",
                )
            else:
                # Known topic but below confidence threshold → escalate
                trigger = EscalationTrigger.BELOW_THRESHOLD.value
                reason  = f"Confidence {result.confidence:.3f} below 0.85 threshold"
                LOGGER.warning(
                    "RETURN_ESCALATED trigger=%s reason=%r confidence=%s topic=%s",
                    trigger, reason, result.confidence, result.topic.value,
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

    # ── Case lookup ────────────────────────────────────────────────────────────

    def get_case(self, case_id: str) -> Case | None:
        """Look up a case by its ID. Returns None if not found or offline."""
        return self._repo.get_case(case_id)

    # ── Sprint 2.15: Slot filling and message handling ─────────────────────────

    def get_slot_state(self, case: Case) -> dict[str, SlotValue]:
        """Deserialize current slot values from case.slot_state."""
        return ClarificationEngine.slot_values_from_dict(case.slot_state)

    def receive_message(
        self,
        case: Case,
        message_text: str,
        *,
        slot_name: str | None = None,
        slot_value_str: str | None = None,
    ) -> ReceiveMessageResult:
        """
        Process an incoming message for slot filling.

        Processing order:
        1. Determine topic from case; skip if not yet classified.
        2. Load current slot values.
        3. If explicit slot_name + slot_value_str: accept that slot.
        4. Otherwise: try deterministic extraction from message_text (enum slots only).
        5. Check max_attempts_exceeded → transition to ESCALATED.
        6. Check all_required_filled → transition to WORKFLOW_ACTIVE.
        7. Otherwise → transition to AWAITING_INPUT and return next question.
        8. Persist updated slot_state and new case state.

        Never raises — exceptions are caught and logged. Returns current state on error.
        """
        topic_str = case.topic
        try:
            topic = TopicKey(topic_str) if topic_str else None
        except ValueError:
            topic = None

        slot_values = self.get_slot_state(case)

        if topic is None:
            return ReceiveMessageResult(
                case_id=case.case_id,
                state=case.current_state,
                slot_values=ClarificationEngine.slot_values_to_dict(slot_values),
                next_question=None,
            )

        # ── UNKNOWN topic: topic-discovery clarification loop ──────────────────
        # Blueprint (flow_diagram.mermaid): CLASSIFIER → SLOTEXTRACT → CLARIFICATION
        # For UNKNOWN topics, the clarification engine has no slot registry, so we
        # implement topic-discovery directly here using a reserved __topic_discovery__
        # tracking slot.
        #
        # Pass 1 (first message):  ask "Could you please describe your issue?"
        # Pass 2 (customer reply): re-classify using enriched text (original + reply).
        #   - If topic now KNOWN → reset case.topic, proceed through normal slot path.
        #   - If still UNKNOWN and attempt_count < _MAX_DISCOVERY_ATTEMPTS → ask again.
        #   - If attempt_count >= _MAX_DISCOVERY_ATTEMPTS → escalate.
        if topic == TopicKey.UNKNOWN:
            return self._handle_unknown_topic_discovery(
                case=case,
                message_text=message_text,
                slot_values=slot_values,
            )

        try:
            # Accept explicit slot value
            if slot_name is not None and slot_value_str is not None:
                updated_sv = self._clarify.accept_slot_value(
                    topic, slot_name, slot_value_str, slot_values
                )
                slot_values[slot_name] = updated_sv
                self._audit.log_slot_filled(
                    case,
                    slot_name=slot_name,
                    value_hash=str(hash(slot_value_str)),
                    turn_count=sum(
                        1 for sv in slot_values.values()
                        if sv.status == SlotStatus.FILLED
                    ),
                )
            elif message_text:
                # Deterministic extraction from free-form text (enum slots only)
                slot_values = self._clarify.extract_from_text(topic, message_text, slot_values)

            # Check escalation condition
            if self._clarify.any_max_attempts_exceeded(topic, slot_values):
                case.slot_state = ClarificationEngine.slot_values_to_dict(slot_values)
                self._sm.safe_transition(
                    case,
                    CaseState.ESCALATED,
                    reason="slot_fill_max_attempts_exceeded",
                )
                case.escalation_reason = EscalationTrigger.SLOT_FILL_TIMEOUT.value
                self._repo.update_case_state(case, CaseState.ESCALATED)
                return ReceiveMessageResult(
                    case_id=case.case_id,
                    state=case.current_state,
                    slot_values=ClarificationEngine.slot_values_to_dict(slot_values),
                    next_question=None,
                    escalated=True,
                )

            # Check completion condition
            all_filled = self._clarify.all_required_filled(topic, slot_values)
            case.slot_state = ClarificationEngine.slot_values_to_dict(slot_values)

            if all_filled:
                if case.current_state != CaseState.WORKFLOW_ACTIVE:
                    self._sm.safe_transition(
                        case,
                        CaseState.WORKFLOW_ACTIVE,
                        reason="all_required_slots_filled",
                    )
                self._repo.update_case_state(case, CaseState.WORKFLOW_ACTIVE)

                # Auto-start workflow if a registry is available
                wf_started = False
                if self._registry is not None:
                    try:
                        self.start_workflow(case, slot_values)
                        wf_started = True
                    except WorkflowAlreadyStartedError:
                        # Workflow was already started (idempotent: treat as success)
                        wf_started = True
                        LOGGER.debug(
                            "case_service.receive_message: workflow already active case_id=%s — skipping restart",
                            case.case_id,
                        )
                    except Exception as wf_exc:
                        LOGGER.exception(
                            "case_service.receive_message: workflow start failed case_id=%s error=%s",
                            case.case_id, wf_exc,
                        )

                return ReceiveMessageResult(
                    case_id=case.case_id,
                    state=case.current_state,
                    slot_values=ClarificationEngine.slot_values_to_dict(slot_values),
                    next_question=None,
                    all_slots_filled=True,
                    workflow_started=wf_started,
                )

            # More slots needed: ensure AWAITING_INPUT state.
            # If coming from TRIAGE_COMPLETE, must first go through WORKFLOW_ACTIVE.
            if case.current_state == CaseState.TRIAGE_COMPLETE:
                self._sm.safe_transition(
                    case,
                    CaseState.WORKFLOW_ACTIVE,
                    reason="slot_filling_started",
                )
            if case.current_state not in (CaseState.AWAITING_INPUT, CaseState.ESCALATED, CaseState.CLOSED):
                self._sm.safe_transition(
                    case,
                    CaseState.AWAITING_INPUT,
                    reason="awaiting_slot_input",
                )
            self._repo.update_case_state(case, case.current_state)

            next_q: ClarificationQuestion | None = self._clarify.next_question(topic, slot_values)

            return ReceiveMessageResult(
                case_id=case.case_id,
                state=case.current_state,
                slot_values=ClarificationEngine.slot_values_to_dict(slot_values),
                next_question=next_q.to_dict() if next_q else None,
            )

        except Exception as exc:
            LOGGER.exception(
                "case_service.receive_message failed case_id=%s error=%s",
                case.case_id, exc,
            )
            return ReceiveMessageResult(
                case_id=case.case_id,
                state=case.current_state,
                slot_values=ClarificationEngine.slot_values_to_dict(slot_values),
                next_question=None,
            )

    # ── UNKNOWN topic: topic-discovery clarification loop ─────────────────────

    def _handle_unknown_topic_discovery(
        self,
        case: Case,
        message_text: str,
        slot_values: dict[str, SlotValue],
    ) -> ReceiveMessageResult:
        """
        Implement the topic-discovery clarification loop for UNKNOWN-topic cases.

        Pass 1 (no prior discovery attempt):
          → Transition to AWAITING_INPUT.
          → Return topic-discovery question (_TOPIC_DISCOVERY_QUESTION).

        Pass 2+ (customer has replied):
          → Re-classify using message_text (the customer's reply).
          → If topic now KNOWN:
              * Update case.topic and case.confidence.
              * Delegate back to the normal receive_message() slot-filling path.
          → If still UNKNOWN and attempts < _MAX_DISCOVERY_ATTEMPTS:
              * Increment attempt counter, return question again.
          → If attempts >= _MAX_DISCOVERY_ATTEMPTS:
              * Escalate the case.

        Never raises.
        """
        try:
            # Read current discovery attempt count from slot_state
            discovery_raw = slot_values.get(_TOPIC_DISCOVERY_SLOT)
            attempt_count = discovery_raw.attempt_count if discovery_raw else 0

            # Check max attempts before doing anything else
            if attempt_count >= _MAX_DISCOVERY_ATTEMPTS:
                LOGGER.warning(
                    "UNKNOWN_TOPIC_MAX_ATTEMPTS_EXCEEDED case_id=%s attempts=%d — escalating",
                    case.case_id, attempt_count,
                )
                case.escalation_reason = EscalationTrigger.SLOT_FILL_TIMEOUT.value
                self._sm.safe_transition(
                    case, CaseState.ESCALATED,
                    reason="unknown_topic_discovery_max_attempts_exceeded",
                )
                self._repo.update_case_state(case, CaseState.ESCALATED)
                return ReceiveMessageResult(
                    case_id=case.case_id,
                    state=case.current_state,
                    slot_values=ClarificationEngine.slot_values_to_dict(slot_values),
                    next_question=None,
                    escalated=True,
                )

            # Pass 2+: customer has replied; attempt re-classification
            if attempt_count > 0 and message_text:
                LOGGER.info(
                    "UNKNOWN_TOPIC_RECLASSIFY case_id=%s attempt=%d message_len=%d",
                    case.case_id, attempt_count, len(message_text),
                )
                try:
                    reclassify_result = self._clf.classify(message_text)
                    if reclassify_result.meets_threshold:
                        # Topic now KNOWN — update case and re-enter normal slot path
                        LOGGER.info(
                            "UNKNOWN_TOPIC_RECLASSIFY_SUCCESS case_id=%s new_topic=%s confidence=%s",
                            case.case_id, reclassify_result.topic.value, reclassify_result.confidence,
                        )
                        case.topic = reclassify_result.topic.value
                        case.confidence = reclassify_result.confidence
                        # Remove the discovery tracking slot so normal slot filling is clean
                        slot_values.pop(_TOPIC_DISCOVERY_SLOT, None)
                        case.slot_state = ClarificationEngine.slot_values_to_dict(slot_values)
                        self._repo.update_case_state(case, case.current_state)
                        # Delegate to normal receive_message() with the now-known topic
                        return self.receive_message(case, message_text)
                    else:
                        LOGGER.info(
                            "UNKNOWN_TOPIC_RECLASSIFY_STILL_UNKNOWN case_id=%s attempt=%d",
                            case.case_id, attempt_count,
                        )
                except Exception as exc:
                    LOGGER.warning(
                        "UNKNOWN_TOPIC_RECLASSIFY_FAILED case_id=%s error=%s",
                        case.case_id, exc,
                    )

            # Increment attempt count and ask the discovery question
            new_attempt = attempt_count + 1
            slot_values[_TOPIC_DISCOVERY_SLOT] = SlotValue(
                slot_name=_TOPIC_DISCOVERY_SLOT,
                status=SlotStatus.PENDING,
                value=None,
                attempt_count=new_attempt,
            )
            case.slot_state = ClarificationEngine.slot_values_to_dict(slot_values)

            # Transition state to AWAITING_INPUT
            if case.current_state == CaseState.TRIAGE_COMPLETE:
                self._sm.safe_transition(
                    case, CaseState.WORKFLOW_ACTIVE, reason="unknown_topic_discovery_started",
                )
            if case.current_state not in (CaseState.AWAITING_INPUT, CaseState.ESCALATED, CaseState.CLOSED):
                self._sm.safe_transition(
                    case, CaseState.AWAITING_INPUT, reason="awaiting_topic_discovery",
                )
            self._repo.update_case_state(case, case.current_state)

            return ReceiveMessageResult(
                case_id=case.case_id,
                state=case.current_state,
                slot_values=ClarificationEngine.slot_values_to_dict(slot_values),
                next_question=_TOPIC_DISCOVERY_QUESTION,
            )

        except Exception as exc:
            LOGGER.exception(
                "case_service._handle_unknown_topic_discovery failed case_id=%s error=%s",
                case.case_id, exc,
            )
            return ReceiveMessageResult(
                case_id=case.case_id,
                state=case.current_state,
                slot_values=ClarificationEngine.slot_values_to_dict(slot_values),
                next_question=None,
            )

    # ── Case listing (for admin / visibility) ─────────────────────────────────

    def list_workflow_cases(self, states: list[str] | None = None) -> list[Case]:
        """
        Return cases that have workflow state set, optionally filtered by workflow_state.

        Used by the workflow admin endpoints. Returns empty list if offline or on error.
        """
        return self._repo.list_cases_by_workflow_state(states)

    # ── Sprint 2.16: Workflow orchestration ───────────────────────────────────

    def start_workflow(
        self,
        case: Case,
        slot_values: dict[str, SlotValue],
    ) -> WorkflowStartResult:
        """
        Select and begin executing the workflow for a case.

        Called automatically from receive_message when all required slots
        are filled (if a PlaybookRegistry is wired to this service).

        Also callable directly for cases that enter WORKFLOW_ACTIVE via
        other paths (e.g., manual trigger by admin endpoint).

        Mutates case.workflow_id, case.workflow_state, case.workflow_step_index,
        case.workflow_context in place, then persists via repository.

        Never raises — returns a FAILED WorkflowStartResult on internal error.
        """
        if self._registry is None:
            LOGGER.warning("case_service.start_workflow: no registry — skipping case=%s", case.case_id)
            return WorkflowStartResult(
                case_id=case.case_id,
                state=case.current_state,
                workflow_id=None,
                workflow_state=WorkflowState.FAILED.value,
                current_step_id=None,
                escalation_reason="no_registry",
            )

        # A3: Double-start idempotency guard
        if case.workflow_state in _ACTIVE_WORKFLOW_STATES:
            LOGGER.warning(
                "case_service.start_workflow: workflow already active case_id=%s state=%s — rejecting",
                case.case_id, case.workflow_state,
            )
            raise WorkflowAlreadyStartedError(
                f"Case {case.case_id} already has an active workflow "
                f"(workflow_state={case.workflow_state}). "
                "Call resume_workflow instead."
            )

        try:
            wf_result = self._wf_engine.start(
                case,
                self._registry,
                slot_values,
                gateway=self._gateway,
                audit=self._audit,
            )

            # Persist workflow state back to case
            case.workflow_id = wf_result.workflow_id or case.workflow_id
            case.workflow_state = wf_result.workflow_state.value
            case.workflow_step_index = len(wf_result.step_results)
            case.workflow_context = wf_result.to_dict()

            # Transition case state based on workflow outcome
            if wf_result.workflow_state == WorkflowState.COMPLETED:
                self._sm.safe_transition(case, CaseState.RESOLVED, reason="workflow_resolved")
            elif wf_result.workflow_state == WorkflowState.ESCALATED:
                if case.current_state not in (CaseState.ESCALATED, CaseState.CLOSED):
                    case.escalation_reason = wf_result.escalation_reason
                    self._sm.safe_transition(case, CaseState.ESCALATED, reason="workflow_escalated")
            elif wf_result.workflow_state == WorkflowState.PAUSED:
                # Action proposed and pending approval — transition to ACTION_PENDING
                if case.current_state == CaseState.WORKFLOW_ACTIVE:
                    self._sm.safe_transition(case, CaseState.ACTION_PENDING, reason="action_pending_approval")
            elif wf_result.workflow_state == WorkflowState.FAILED:
                if case.current_state not in (CaseState.ESCALATED, CaseState.FAILED, CaseState.CLOSED):
                    self._sm.safe_transition(case, CaseState.FAILED, reason="workflow_failed")

            self._repo.update_case_state(case, case.current_state)

            return WorkflowStartResult(
                case_id=case.case_id,
                state=case.current_state,
                workflow_id=wf_result.workflow_id,
                workflow_state=wf_result.workflow_state.value,
                current_step_id=wf_result.current_step_id,
                pending_action_id=wf_result.pending_action_id,
                resolved=wf_result.workflow_state == WorkflowState.COMPLETED,
                escalated=wf_result.workflow_state in (WorkflowState.ESCALATED, WorkflowState.FAILED),
                step_results=wf_result.step_results,
                resolution_note=wf_result.resolution_note,
                escalation_reason=wf_result.escalation_reason,
            )

        except Exception as exc:
            LOGGER.exception(
                "case_service.start_workflow failed case_id=%s error=%s",
                case.case_id, exc,
            )
            return WorkflowStartResult(
                case_id=case.case_id,
                state=case.current_state,
                workflow_id=None,
                workflow_state=WorkflowState.FAILED.value,
                current_step_id=None,
                escalation_reason=f"internal_error: {type(exc).__name__}",
            )

    def resume_workflow(
        self,
        case: Case,
        action_request: Any,
        slot_values: dict[str, SlotValue],
    ) -> WorkflowStartResult:
        """
        Resume a paused workflow after its pending action completes.

        Called by the workflow resume API endpoint when an action gateway
        action reaches a terminal state for a case in ACTION_PENDING state.

        Never raises.
        """
        if self._registry is None:
            return WorkflowStartResult(
                case_id=case.case_id,
                state=case.current_state,
                workflow_id=None,
                workflow_state=WorkflowState.FAILED.value,
                current_step_id=None,
                escalation_reason="no_registry",
            )

        # A4: Consistency guard — only PAUSED workflows can be resumed
        try:
            self._wf_checker.validate_resume(case)
        except WorkflowConsistencyError as consistency_exc:
            LOGGER.warning(
                "case_service.resume_workflow: consistency check failed case_id=%s error=%s",
                case.case_id, consistency_exc,
            )
            return WorkflowStartResult(
                case_id=case.case_id,
                state=case.current_state,
                workflow_id=case.workflow_id,
                workflow_state=case.workflow_state or WorkflowState.FAILED.value,
                current_step_id=None,
                escalation_reason=f"consistency_error: {consistency_exc}",
            )

        try:
            wf_result = self._wf_engine.resume_after_action(
                case,
                self._registry,
                action_request,
                slot_values,
                audit=self._audit,
            )

            case.workflow_state = wf_result.workflow_state.value
            case.workflow_step_index = len(wf_result.step_results)
            case.workflow_context = wf_result.to_dict()

            if wf_result.workflow_state == WorkflowState.COMPLETED:
                # A1 fix: ACTION_PENDING → RESOLVED is illegal; must hop through WORKFLOW_ACTIVE first
                if case.current_state == CaseState.ACTION_PENDING:
                    self._sm.safe_transition(case, CaseState.WORKFLOW_ACTIVE, reason="action_completed")
                self._sm.safe_transition(case, CaseState.RESOLVED, reason="workflow_resolved")
            elif wf_result.workflow_state == WorkflowState.ESCALATED:
                if case.current_state not in (CaseState.ESCALATED, CaseState.CLOSED):
                    case.escalation_reason = wf_result.escalation_reason
                    # Also hop through WORKFLOW_ACTIVE if needed
                    if case.current_state == CaseState.ACTION_PENDING:
                        self._sm.safe_transition(case, CaseState.WORKFLOW_ACTIVE, reason="action_completed")
                    self._sm.safe_transition(case, CaseState.ESCALATED, reason="workflow_escalated")
            elif wf_result.workflow_state == WorkflowState.PAUSED:
                if case.current_state != CaseState.ACTION_PENDING:
                    self._sm.safe_transition(case, CaseState.ACTION_PENDING, reason="next_action_pending")
            elif wf_result.workflow_state == WorkflowState.RUNNING:
                if case.current_state == CaseState.ACTION_PENDING:
                    self._sm.safe_transition(case, CaseState.WORKFLOW_ACTIVE, reason="workflow_resumed")
            elif wf_result.workflow_state == WorkflowState.FAILED:
                if case.current_state not in (CaseState.ESCALATED, CaseState.FAILED, CaseState.CLOSED):
                    self._sm.safe_transition(case, CaseState.FAILED, reason="workflow_failed")

            self._repo.update_case_state(case, case.current_state)

            return WorkflowStartResult(
                case_id=case.case_id,
                state=case.current_state,
                workflow_id=wf_result.workflow_id,
                workflow_state=wf_result.workflow_state.value,
                current_step_id=wf_result.current_step_id,
                pending_action_id=wf_result.pending_action_id,
                resolved=wf_result.workflow_state == WorkflowState.COMPLETED,
                escalated=wf_result.workflow_state in (WorkflowState.ESCALATED, WorkflowState.FAILED),
                step_results=wf_result.step_results,
                resolution_note=wf_result.resolution_note,
                escalation_reason=wf_result.escalation_reason,
            )

        except Exception as exc:
            LOGGER.exception(
                "case_service.resume_workflow failed case_id=%s error=%s",
                case.case_id, exc,
            )
            return WorkflowStartResult(
                case_id=case.case_id,
                state=case.current_state,
                workflow_id=None,
                workflow_state=WorkflowState.FAILED.value,
                current_step_id=None,
                escalation_reason=f"internal_error: {type(exc).__name__}",
            )

    def resume_clarification_workflow(
        self,
        case: Case,
        updated_slot_values: dict[str, SlotValue],
        *,
        max_attempts: int = 2,
    ) -> WorkflowStartResult:
        """
        Resume a workflow PAUSED at a CLARIFY step after the customer provides missing slots.

        Sprint 2.26 — closes the P0 clarification resume loop.

        Processing order:
        1. Validate: registry must exist and workflow must be PAUSED.
        2. Increment attempt_count for each slot that was PENDING (previously asked but
           not yet filled). Persist updated slot_state to DB.
        3. Check if any slot has exceeded max_attempts — escalate if so.
        4. Call WorkflowEngine.resume_after_clarification() with updated slot_values.
        5. Handle outcome: READY→continue, NEEDS_CLARIFICATION→stay PAUSED, else escalate.

        Never raises. Returns a WorkflowStartResult with the outcome.
        """
        if self._registry is None:
            return WorkflowStartResult(
                case_id=case.case_id,
                state=case.current_state,
                workflow_id=None,
                workflow_state=WorkflowState.FAILED.value,
                current_step_id=None,
                escalation_reason="no_registry",
            )

        # ── Part C: increment attempt_count for slots that were pending ────────
        try:
            for slot_name, sv in updated_slot_values.items():
                if sv.status == SlotStatus.PENDING:
                    updated_slot_values[slot_name] = SlotValue(
                        slot_name=sv.slot_name,
                        status=sv.status,
                        value=sv.value,
                        attempt_count=sv.attempt_count + 1,
                    )
                    # Emit audit event for each incremented slot
                    try:
                        wf_ctx = case.workflow_context or {}
                        wf_id  = wf_ctx.get("workflow_id", "")
                        step_id = wf_ctx.get("current_step_id", "")
                        self._audit.log_clarification_attempt_incremented(
                            case,
                            slot_name=slot_name,
                            attempt_count=updated_slot_values[slot_name].attempt_count,
                            max_attempts=max_attempts,
                            workflow_id=wf_id,
                            step_id=step_id,
                        )
                    except Exception:
                        pass

            # Persist updated slot_state
            from case_engine.clarification_engine import ClarificationEngine as _CE
            case.slot_state = _CE.slot_values_to_dict(updated_slot_values)
            self._repo.update_case_state(case, case.current_state)

            # Check max_attempts across all slots
            any_exceeded = any(
                sv.attempt_count >= max_attempts
                for sv in updated_slot_values.values()
                if sv.status == SlotStatus.PENDING
            )
            if any_exceeded:
                exceeded_slots = [
                    name for name, sv in updated_slot_values.items()
                    if sv.status == SlotStatus.PENDING and sv.attempt_count >= max_attempts
                ]
                LOGGER.warning(
                    "case_service.resume_clarification_workflow: max_attempts exceeded "
                    "case_id=%s slots=%s — escalating",
                    case.case_id, exceeded_slots,
                )
                from case_engine.escalation import EscalationTrigger
                case.escalation_reason = EscalationTrigger.SLOT_FILL_TIMEOUT.value
                self._sm.safe_transition(
                    case, CaseState.ESCALATED, reason="clarification_max_attempts_exceeded"
                )
                self._repo.update_case_state(case, CaseState.ESCALATED)
                return WorkflowStartResult(
                    case_id=case.case_id,
                    state=case.current_state,
                    workflow_id=case.workflow_id,
                    workflow_state=WorkflowState.ESCALATED.value,
                    current_step_id=None,
                    escalated=True,
                    escalation_reason=f"clarification_max_attempts_exceeded: slots={exceeded_slots}",
                )

        except Exception as exc:
            LOGGER.exception(
                "case_service.resume_clarification_workflow: attempt tracking failed "
                "case_id=%s error=%s",
                case.case_id, exc,
            )

        # ── Part B: re-run CLARIFY step with updated slot context ─────────────
        try:
            wf_result = self._wf_engine.resume_after_clarification(
                case,
                self._registry,
                updated_slot_values,
                audit=self._audit,
            )

            case.workflow_state    = wf_result.workflow_state.value
            case.workflow_step_index = len(wf_result.step_results)
            case.workflow_context  = wf_result.to_dict()

            if wf_result.workflow_state == WorkflowState.COMPLETED:
                if case.current_state == CaseState.ACTION_PENDING:
                    self._sm.safe_transition(case, CaseState.WORKFLOW_ACTIVE, reason="clarification_resolved")
                self._sm.safe_transition(case, CaseState.RESOLVED, reason="workflow_resolved")
            elif wf_result.workflow_state == WorkflowState.ESCALATED:
                if case.current_state not in (CaseState.ESCALATED, CaseState.CLOSED):
                    case.escalation_reason = wf_result.escalation_reason
                    self._sm.safe_transition(case, CaseState.ESCALATED, reason="workflow_escalated")
            elif wf_result.workflow_state == WorkflowState.PAUSED:
                # Still needs more clarification — stay in AWAITING_INPUT
                if case.current_state not in (CaseState.AWAITING_INPUT, CaseState.ESCALATED):
                    self._sm.safe_transition(
                        case, CaseState.AWAITING_INPUT, reason="awaiting_clarification_response"
                    )
            elif wf_result.workflow_state == WorkflowState.FAILED:
                if case.current_state not in (CaseState.ESCALATED, CaseState.FAILED, CaseState.CLOSED):
                    self._sm.safe_transition(case, CaseState.FAILED, reason="workflow_failed")

            self._repo.update_case_state(case, case.current_state)

            return WorkflowStartResult(
                case_id=case.case_id,
                state=case.current_state,
                workflow_id=wf_result.workflow_id,
                workflow_state=wf_result.workflow_state.value,
                current_step_id=wf_result.current_step_id,
                pending_action_id=wf_result.pending_action_id,
                resolved=wf_result.workflow_state == WorkflowState.COMPLETED,
                escalated=wf_result.workflow_state in (WorkflowState.ESCALATED, WorkflowState.FAILED),
                step_results=wf_result.step_results,
                resolution_note=wf_result.resolution_note,
                escalation_reason=wf_result.escalation_reason,
            )

        except Exception as exc:
            LOGGER.exception(
                "case_service.resume_clarification_workflow failed case_id=%s error=%s",
                case.case_id, exc,
            )
            return WorkflowStartResult(
                case_id=case.case_id,
                state=case.current_state,
                workflow_id=None,
                workflow_state=WorkflowState.FAILED.value,
                current_step_id=None,
                escalation_reason=f"internal_error: {type(exc).__name__}",
            )

    # ── Internal callback ──────────────────────────────────────────────────────

    def _on_transition(self, case: Case, transition: Any) -> None:
        """Called by state machine on every valid transition."""
        # TRACE_AUDIT_IDENTITY — proves which AuditLogger class is injected at runtime
        LOGGER.warning(
            "TRACE_AUDIT_IDENTITY_TRANSITION audit_type=%s audit_module=%s has_log_transition=%s",
            type(self._audit).__name__,
            getattr(type(self._audit), "__module__", "?"),
            hasattr(self._audit, "log_transition"),
        )
        self._audit.log_transition(case, transition)
        self._repo.record_transition(transition)


def build_case_service(
    supabase_client: Any = None,
    *,
    playbook_registry: Any = None,
    action_gateway: Any = None,
) -> CaseService:
    """
    Factory: build a CaseService with the given Supabase client.

    Pass supabase_client=None for offline / test use.
    playbook_registry and action_gateway are wired in at startup; None is safe for tests.
    """
    repo    = CaseRepository(supabase_client)
    auditor = AuditLogger(supabase_client)
    return CaseService(
        repository=repo,
        audit_logger=auditor,
        playbook_registry=playbook_registry,
        action_gateway=action_gateway,
    )
