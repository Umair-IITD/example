"""
case_engine/runtime/support_agent_runtime.py

Sprint 2.27.5: SupportAgentRuntime — THE AGENT.

Per blueprint: ONE AGENT — single cohesive entrypoint for case processing.

Full 11-step pipeline per flow_diagram.mermaid:
  CLASSIFY → SLOT_EXTRACT → CLARIFY → INVESTIGATE → KNOWLEDGE →
  REASON → PROPOSE → GATEWAY → EXECUTE → VERIFY → RESOLVE

Followed by post-workflow steps:
  RESOLVE → NOTEGEN → L2CHECK → (ASANACREATE | USERRESPONSE) → CLOSECHECK

This runtime does NOT duplicate WorkflowEngine. Instead:
  - Pre-workflow steps (CLASSIFY, SLOT_EXTRACT) → handled here directly
  - Workflow pipeline (INVESTIGATE through RESOLVE) → delegated to CaseService/WorkflowEngine
  - Post-workflow steps (NOTEGEN, L2CHECK, USERRESPONSE) → handled here

Design principles:
  - Never raises: all exceptions caught, return AgentExecutionResult.failure()
  - Single run_case(case, message_text) entrypoint
  - All services are optional (graceful degradation)
  - Deterministic: same input → same output path
  - Audit events emitted at each major step boundary
  - LLM-ready: ResponseGenerationService is injectable
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from case_engine.case_state import CaseState
from case_engine.runtime.agent_models import (
    AgentExecutionResult,
    AgentStatus,
    SupportAgentMode,
    _mode_from_env,
)
from case_engine.response_generation.models import ResponseContext, ResponseType
from case_engine.trace import make_trace_id, trace_log

if TYPE_CHECKING:
    from case_engine.audit import AuditLogger
    from case_engine.engineering.service import EngineeringEscalationService
    from case_engine.models import Case
    from case_engine.response_generation.service import ResponseGenerationService
    from case_engine.service import CaseService

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _ms_elapsed(started_ms: int) -> int:
    return max(0, int(time.monotonic() * 1000) - started_ms)


# ── Escalation Reason Inference ───────────────────────────────────────────────

_L2_ESCALATION_TOPICS: frozenset[str] = frozenset({
    "API_Callback_Failure",
    "VKYC_Session_Failure",
})

_L2_ROOT_CAUSE_CATEGORIES: frozenset[str] = frozenset({
    "infrastructure",
    "backend_service",
    "third_party_api",
    "database",
    "network",
})


def _needs_engineering_escalation(
    topic:         str | None,
    workflow_result: dict[str, Any] | None,
) -> bool:
    """
    L2CHECK decision: does this case need engineering escalation?

    Per blueprint: L2CHECK is triggered after NOTEGEN.
    Returns True iff the case root cause requires engineering involvement.
    """
    if workflow_result is None:
        return False

    # Check if workflow explicitly escalated
    if workflow_result.get("workflow_state") in ("ESCALATED", "FAILED"):
        return True

    # Check investigation / root cause for infrastructure issues
    investigation = workflow_result.get("investigation_result") or {}
    root_cause    = investigation.get("root_cause") or {}
    category      = (root_cause.get("category") or "").lower()

    for l2_cat in _L2_ROOT_CAUSE_CATEGORIES:
        if l2_cat in category:
            return True

    # Topic-based heuristic
    if topic in _L2_ESCALATION_TOPICS:
        resolution_outcome = workflow_result.get("resolution_outcome") or {}
        if not resolution_outcome.get("resolved", False):
            return True

    return False


def _determine_response_type(
    case:            "Case",
    workflow_result: dict[str, Any] | None,
    needs_l2:        bool,
    clarification_question: str = "",
) -> ResponseType:
    """Map case state and workflow outcome to a ResponseType."""
    if case.current_state == CaseState.AWAITING_INPUT or clarification_question:
        return ResponseType.CLARIFICATION
    if needs_l2:
        return ResponseType.ESCALATION
    if case.current_state in (CaseState.RESOLVED, CaseState.CLOSED):
        return ResponseType.RESOLUTION
    if workflow_result and workflow_result.get("workflow_state") in ("RESOLVED",):
        return ResponseType.RESOLUTION
    if case.current_state == CaseState.ESCALATED:
        return ResponseType.ESCALATION
    if workflow_result and workflow_result.get("pending_action_id"):
        return ResponseType.APPROVAL_NEEDED
    return ResponseType.STATUS_UPDATE


def _extract_workflow_knowledge(workflow_result: dict[str, Any] | None) -> dict[str, Any]:
    """Extract knowledge-layer outputs from a WorkflowExecutionResult dict."""
    if workflow_result is None:
        return {}
    # WorkflowExecutionResult stores knowledge in workflow_context
    ctx = workflow_result.get("workflow_context") or {}
    return ctx.get("knowledge_result") or {}


def _extract_investigation(workflow_result: dict[str, Any] | None) -> dict[str, Any] | None:
    """Extract investigation result from WorkflowExecutionResult dict."""
    if workflow_result is None:
        return None
    ctx = workflow_result.get("workflow_context") or {}
    inv = ctx.get("investigation_result")
    if not inv:
        inv = workflow_result.get("investigation_result")
    return inv


def _extract_root_cause(investigation: dict[str, Any] | None) -> dict[str, Any] | None:
    """Extract root_cause sub-dict from investigation result."""
    if investigation is None:
        return None
    return investigation.get("root_cause") or investigation.get("root_cause_analysis") or {}


# ── SupportAgentRuntime ───────────────────────────────────────────────────────

class SupportAgentRuntime:
    """
    THE AGENT — single cohesive entrypoint for case-level AI processing.

    Delegates to:
      - CaseService (classify, slot fill, workflow)
      - ResponseGenerationService (USERRESPONSE node)
      - EngineeringEscalationService (ASANACREATE node)

    Public API:
      run_case(case, message_text, slot_values=None) → AgentExecutionResult

    Never raises. Thread-safe: all state lives in Case and return values.
    Services are optional: if None, the corresponding pipeline step is skipped.
    """

    def __init__(
        self,
        case_service:                "CaseService | None" = None,
        response_generation_service: "ResponseGenerationService | None" = None,
        engineering_escalation_service: "EngineeringEscalationService | None" = None,
        audit_logger:                "AuditLogger | None" = None,
        mode:                        SupportAgentMode = SupportAgentMode.DRY_RUN,
    ) -> None:
        self._case_svc      = case_service
        self._response_svc  = response_generation_service
        self._engineering   = engineering_escalation_service
        self._audit         = audit_logger
        self._mode          = mode

    # ── Primary API ───────────────────────────────────────────────────────────

    def run_case(
        self,
        case:         "Case",
        message_text: str,
        slot_values:  dict[str, Any] | None = None,
    ) -> AgentExecutionResult:
        """
        Process a single case — the ONE ENTRYPOINT for the support agent.

        Pipeline:
          1. CLASSIFY: classify message topic (if not yet classified)
          2. SLOT_EXTRACT: extract / fill required slots
          3. CLARIFY: return clarification request if slots incomplete
          4. WORKFLOW: run full investigation → knowledge → reason → propose → execute → resolve
          5. NOTEGEN: build knowledge context for response
          6. L2CHECK: determine if engineering escalation needed
          7. ASANACREATE: create engineering ticket if L2 needed
          8. USERRESPONSE: generate customer-facing reply

        Args:
            case:         The Case object (must already exist — created by CaseService.open_case)
            message_text: Raw text from the customer/Freshdesk ticket
            slot_values:  Pre-filled slot values (optional; used for API callers that
                         pre-extract slots from structured data)

        Returns:
            AgentExecutionResult — never raises, always returns structured result
        """
        LOGGER.info("ENTER_RUN_CASE ticket_id=%s", getattr(case, "ticket_id", "?"))
        started_ms = int(time.monotonic() * 1000)
        started_at = _now_iso()
        steps_completed: list[str] = []

        # Sprint 2.30.1 — TRACE_RUNTIME
        trace_id = make_trace_id(case.ticket_id or case.case_id)
        trace_log("TRACE_RUNTIME", trace_id,
                  case_id=case.case_id,
                  ticket_id=case.ticket_id,
                  mode=self._mode.value,
                  case_svc_wired=self._case_svc is not None,
                  response_svc_wired=self._response_svc is not None)

        try:
            return self._run_pipeline(
                case=case,
                message_text=message_text,
                slot_values=slot_values,
                started_ms=started_ms,
                started_at=started_at,
                steps_completed=steps_completed,
            )
        except Exception as exc:
            LOGGER.exception(
                "support_agent_runtime.fatal_error case_id=%s error=%s",
                case.case_id, exc,
            )
            return AgentExecutionResult.failure(
                case_id=case.case_id,
                error_code="AGENT_RUNTIME_FATAL_ERROR",
                error_msg=f"{type(exc).__name__}: {exc}",
                started_at=started_at,
                steps_completed=tuple(steps_completed),
                duration_ms=_ms_elapsed(started_ms),
            )

    # ── Private pipeline ──────────────────────────────────────────────────────

    def _run_pipeline(
        self,
        case:            "Case",
        message_text:    str,
        slot_values:     dict[str, Any] | None,
        started_ms:      int,
        started_at:      str,
        steps_completed: list[str],
    ) -> AgentExecutionResult:
        """Full pipeline execution. Raises on unrecoverable errors (caught by run_case)."""
        self._emit_agent_started(case)

        LOGGER.warning(
            "ENTER_CLASSIFICATION case_id=%s response_svc_wired=%s case_svc_wired=%s topic_pre=%s",
            case.case_id, self._response_svc is not None, self._case_svc is not None, case.topic,
        )

        # ── Step 1: CLASSIFY ──────────────────────────────────────────────────
        classification: dict[str, Any] | None = None
        if self._case_svc is not None and case.topic is None:
            try:
                case = self._case_svc.classify_case(case, message_text)
                classification = {
                    "topic":      case.topic,
                    "confidence": case.confidence,
                    "state":      case.current_state.value,
                }
                steps_completed.append("CLASSIFY")
                LOGGER.debug(
                    "support_agent_runtime.classify case_id=%s topic=%s",
                    case.case_id, case.topic,
                )
            except Exception as exc:
                LOGGER.warning(
                    "support_agent_runtime.classify failed case_id=%s error=%s — continuing",
                    case.case_id, exc,
                )
        elif case.topic is not None:
            classification = {"topic": case.topic, "confidence": case.confidence}
            steps_completed.append("CLASSIFY_CACHED")

        LOGGER.warning(
            "EXIT_CLASSIFICATION case_id=%s topic=%s state=%s confidence=%s",
            case.case_id, case.topic, case.current_state.value, case.confidence,
        )

        # If case was escalated during classification → skip to response
        if case.current_state == CaseState.ESCALATED:
            LOGGER.warning(
                "RETURN_RUNTIME_RESULT reason=ESCALATED_POST_CLASSIFY case_id=%s topic=%s",
                case.case_id, case.topic,
            )
            LOGGER.warning("RETURN_WORKFLOW_NOT_ENTERED reason=ESCALATED_POST_CLASSIFY case_id=%s", case.case_id)
            LOGGER.warning("RETURN_CLARIFICATION_NOT_ENTERED reason=ESCALATED_POST_CLASSIFY case_id=%s", case.case_id)
            return self._build_result(
                case=case,
                steps_completed=steps_completed,
                agent_status=AgentStatus.ESCALATED,
                workflow_result=None,
                response_draft=None,
                engineering_result=None,
                classification=classification,
                started_at=started_at,
                started_ms=started_ms,
                clarification_question="",
            )

        # ── Step 2+3: SLOT_EXTRACT / CLARIFY ─────────────────────────────────
        clarification_question = ""
        workflow_result: dict[str, Any] | None = None

        if self._case_svc is not None:
            try:
                # Build explicit slot overrides if caller provided them
                explicit_slot: dict[str, str | None] = {}
                if slot_values:
                    for k, v in slot_values.items():
                        if v is not None:
                            explicit_slot[k] = str(v)

                # receive_message handles slot extraction + workflow auto-start
                LOGGER.warning(
                    "ENTER_SLOT_EXTRACTION case_id=%s topic=%s slot_state_keys=%s",
                    case.case_id, case.topic, list((case.slot_state or {}).keys()),
                )
                msg_result = self._case_svc.receive_message(
                    case, message_text,
                )
                steps_completed.append("SLOT_EXTRACT")
                LOGGER.warning(
                    "EXIT_SLOT_EXTRACTION case_id=%s all_slots_filled=%s workflow_started=%s next_question=%s",
                    case.case_id, msg_result.all_slots_filled,
                    getattr(msg_result, "workflow_started", None),
                    bool(getattr(msg_result, "next_question", None)),
                )

                if not msg_result.all_slots_filled:
                    # Clarification needed
                    if msg_result.next_question:
                        # ClarificationQuestion.to_dict() uses "prompt_text" key.
                        # Also accept "text" for forward compatibility with older callers.
                        clarification_question = (
                            msg_result.next_question.get("prompt_text")
                            or msg_result.next_question.get("text")
                            or ""
                        )
                    steps_completed.append("CLARIFY")

                    LOGGER.warning(
                        "ENTER_CLARIFICATION_ENGINE case_id=%s question=%r response_svc_wired=%s",
                        case.case_id, clarification_question[:80] if clarification_question else "",
                        self._response_svc is not None,
                    )

                    response_draft = self._generate_response(
                        case=case,
                        topic=case.topic or "",
                        response_type=ResponseType.CLARIFICATION,
                        workflow_result=None,
                        clarification_question=clarification_question,
                    )

                    LOGGER.warning(
                        "RETURN_CLARIFICATION_REQUIRED case_id=%s draft_is_none=%s response_svc_wired=%s",
                        case.case_id, response_draft is None, self._response_svc is not None,
                    )

                    return self._build_result(
                        case=case,
                        steps_completed=steps_completed,
                        agent_status=AgentStatus.AWAITING_CLARIFICATION,
                        workflow_result=None,
                        response_draft=response_draft,
                        engineering_result=None,
                        classification=classification,
                        started_at=started_at,
                        started_ms=started_ms,
                        clarification_question=clarification_question,
                    )

                steps_completed.append("SLOTS_COMPLETE")

                # Workflow was auto-started by receive_message if all slots filled
                if msg_result.workflow_started:
                    steps_completed.append("WORKFLOW_AUTOSTARTED")
                    # Retrieve workflow_context from case (set during auto-start)
                    workflow_result = dict(case.workflow_context) if case.workflow_context else {}
                    workflow_result["workflow_state"] = case.workflow_state
                    workflow_result["workflow_id"]    = case.workflow_id

            except Exception as exc:
                LOGGER.warning(
                    "support_agent_runtime.slot_extract failed case_id=%s error=%s — using fallback",
                    case.case_id, exc,
                )

        # ── Step 4: WORKFLOW (if not auto-started) ────────────────────────────
        LOGGER.warning(
            "ENTER_WORKFLOW_SELECTION case_id=%s workflow_result_is_none=%s case_svc_wired=%s",
            case.case_id, workflow_result is None, self._case_svc is not None,
        )
        if workflow_result is None and self._case_svc is not None:
            try:
                from case_engine.clarification_engine import ClarificationEngine
                sv = ClarificationEngine.slot_values_from_dict(case.slot_state or {})
                wf_start = self._case_svc.start_workflow(case, sv)
                workflow_result = {
                    "workflow_id":     wf_start.workflow_id,
                    "workflow_state":  wf_start.workflow_state,
                    "step_results":    wf_start.step_results,
                    "resolved":        wf_start.resolved,
                    "escalated":       wf_start.escalated,
                    "escalation_reason": wf_start.escalation_reason,
                    "resolution_note": wf_start.resolution_note,
                }
                steps_completed.append("WORKFLOW")
            except Exception as exc:
                LOGGER.warning(
                    "support_agent_runtime.workflow failed case_id=%s error=%s — continuing to response",
                    case.case_id, exc,
                )

        LOGGER.warning(
            "EXIT_WORKFLOW_SELECTION case_id=%s workflow_state=%s workflow_id=%s result_is_none=%s",
            case.case_id,
            (workflow_result or {}).get("workflow_state"),
            (workflow_result or {}).get("workflow_id"),
            workflow_result is None,
        )

        # Emit DRY_RUN_EXECUTION if workflow ran in dry-run mode
        if self._mode == SupportAgentMode.DRY_RUN and workflow_result is not None:
            self._emit_dry_run_execution(case, workflow_result)

        # ── Step 5: NOTEGEN + L2CHECK ────────────────────────────────────────
        steps_completed.append("NOTEGEN")
        needs_l2   = _needs_engineering_escalation(case.topic, workflow_result)
        steps_completed.append("L2CHECK")

        # ── Step 6: ASANACREATE (if L2 needed) ───────────────────────────────
        engineering_result: dict[str, Any] | None = None
        if needs_l2 and self._engineering is not None:
            if self._mode == SupportAgentMode.DRY_RUN:
                # DRY_RUN: skip actual Asana creation, emit audit event
                steps_completed.append("ASANACREATE_DRY_RUN")
                self._emit_dry_run_action(case, "engineering_escalation", "ASANACREATE")
                LOGGER.info(
                    "support_agent_runtime.asanacreate dry_run case_id=%s — skipped",
                    case.case_id,
                )
            else:
                steps_completed.append("ASANACREATE")
                investigation = _extract_investigation(workflow_result)
                root_cause    = _extract_root_cause(investigation)
                knowledge     = _extract_workflow_knowledge(workflow_result)
                sop_steps     = list(knowledge.get("sop_steps") or [])

                escalation_reason = (
                    (workflow_result or {}).get("escalation_reason")
                    or "Automated L1 resolution unsuccessful — engineering review required."
                )

                try:
                    eng_result = self._engineering.create_ticket(
                        case=case,
                        topic=case.topic or "UNKNOWN",
                        freshdesk_ticket_id=case.ticket_id,
                        investigation_result=investigation,
                        root_cause=root_cause,
                        sop_steps=sop_steps,
                        escalation_reason=escalation_reason,
                    )
                    engineering_result = eng_result.to_dict()
                    LOGGER.info(
                        "support_agent_runtime.engineering_escalation case_id=%s ticket_id=%s",
                        case.case_id, eng_result.ticket.ticket_id,
                    )
                except Exception as exc:
                    LOGGER.warning(
                        "support_agent_runtime.asanacreate failed case_id=%s error=%s",
                        case.case_id, exc,
                    )

        # ── Step 7: USERRESPONSE ──────────────────────────────────────────────
        steps_completed.append("USERRESPONSE")
        response_type = _determine_response_type(
            case=case,
            workflow_result=workflow_result,
            needs_l2=needs_l2,
            clarification_question=clarification_question,
        )

        LOGGER.warning(
            "ENTER_RESPONSE_GENERATION case_id=%s response_svc_wired=%s response_type=%s needs_l2=%s",
            case.case_id, self._response_svc is not None, response_type, needs_l2,
        )

        response_draft = self._generate_response(
            case=case,
            topic=case.topic or "Support Request",
            response_type=response_type,
            workflow_result=workflow_result,
            clarification_question=clarification_question,
        )

        LOGGER.warning(
            "EXIT_RESPONSE_GENERATION case_id=%s draft_is_none=%s draft_keys=%s",
            case.case_id, response_draft is None,
            list(response_draft.keys()) if isinstance(response_draft, dict) else None,
        )

        # ── CLOSECHECK ────────────────────────────────────────────────────────
        if response_type == ResponseType.RESOLUTION:
            agent_status = AgentStatus.SUCCESS
        elif response_type == ResponseType.ESCALATION or needs_l2:
            agent_status = AgentStatus.ESCALATED
        elif response_type == ResponseType.CLARIFICATION:
            agent_status = AgentStatus.AWAITING_CLARIFICATION
        elif response_type == ResponseType.APPROVAL_NEEDED:
            agent_status = AgentStatus.AWAITING_APPROVAL
        else:
            agent_status = AgentStatus.SUCCESS

        LOGGER.warning(
            "RETURN_RUNTIME_RESULT case_id=%s agent_status=%s response_draft_is_none=%s steps=%s",
            case.case_id, agent_status, response_draft is None, steps_completed,
        )

        result = self._build_result(
            case=case,
            steps_completed=steps_completed,
            agent_status=agent_status,
            workflow_result=workflow_result,
            response_draft=response_draft,
            engineering_result=engineering_result,
            classification=classification,
            started_at=started_at,
            started_ms=started_ms,
            clarification_question=clarification_question,
        )
        self._emit_agent_completed(result, case)
        return result

    # ── Response generation helper ────────────────────────────────────────────

    def _generate_response(
        self,
        case:                   "Case",
        topic:                  str,
        response_type:          ResponseType,
        workflow_result:        dict[str, Any] | None,
        clarification_question: str = "",
    ) -> dict[str, Any] | None:
        """Generate response draft via ResponseGenerationService. Never raises."""
        if self._response_svc is None:
            LOGGER.warning(
                "ENTER_RESPONSE_GENERATION case_id=%s response_svc=NONE — returning None",
                case.case_id,
            )
            return None

        investigation = _extract_investigation(workflow_result)
        root_cause    = _extract_root_cause(investigation)
        knowledge     = _extract_workflow_knowledge(workflow_result)

        sop_steps_raw = knowledge.get("sop_steps") or []
        citations_raw = knowledge.get("citations") or []

        action_summary = ""
        if workflow_result:
            action_summary = (
                workflow_result.get("resolution_note")
                or workflow_result.get("action_summary")
                or ""
            )

        escalation_reason = ""
        if workflow_result:
            escalation_reason = workflow_result.get("escalation_reason") or ""

        ctx = ResponseContext(
            case_id=case.case_id,
            topic=topic,
            response_type=response_type,
            investigation_result=investigation,
            root_cause=root_cause,
            knowledge_result=knowledge if knowledge else None,
            sop_steps=tuple(str(s) for s in sop_steps_raw),
            citations=tuple(str(c) for c in citations_raw),
            action_summary=action_summary,
            escalation_reason=escalation_reason,
            clarification_question=clarification_question,
        )

        try:
            draft = self._response_svc.generate(ctx, case=case)
            return draft.to_dict()
        except Exception as exc:
            LOGGER.warning(
                "support_agent_runtime.generate_response failed case_id=%s error=%s",
                case.case_id, exc,
            )
            return None

    # ── Result builder ────────────────────────────────────────────────────────

    def _build_result(
        self,
        case:                   "Case",
        steps_completed:        list[str],
        agent_status:           AgentStatus,
        workflow_result:        dict[str, Any] | None,
        response_draft:         dict[str, Any] | None,
        engineering_result:     dict[str, Any] | None,
        classification:         dict[str, Any] | None,
        started_at:             str,
        started_ms:             int,
        clarification_question: str,
    ) -> AgentExecutionResult:
        from case_engine.runtime.agent_models import _new_id
        return AgentExecutionResult(
            run_id=_new_id(),
            case_id=case.case_id,
            agent_status=agent_status,
            workflow_result=workflow_result,
            response_draft=response_draft,
            engineering_result=engineering_result,
            classification=classification,
            steps_completed=tuple(steps_completed),
            error_code=None,
            error_msg=None,
            started_at=started_at,
            completed_at=_now_iso(),
            duration_ms=_ms_elapsed(started_ms),
        )

    # ── Audit ─────────────────────────────────────────────────────────────────

    def _emit_dry_run_execution(
        self,
        case: "Case",
        workflow_result: dict[str, Any],
    ) -> None:
        """Emit DRY_RUN_EXECUTION audit event. Never raises."""
        if self._audit is None:
            return
        try:
            step_results = workflow_result.get("step_results") or []
            executed_actions = [
                s.get("action_type", "")
                for s in step_results
                if isinstance(s, dict) and s.get("step_type") == "EXECUTE"
            ]
            action_summary = ", ".join(executed_actions) if executed_actions else "workflow"
            self._audit.log_dry_run_execution(
                case=case,
                action_type=action_summary,
                workflow_id=workflow_result.get("workflow_id", ""),
            )
        except Exception as exc:
            LOGGER.debug("support_agent_runtime: dry_run_execution emit failed: %s", exc)

    def _emit_dry_run_action(
        self,
        case: "Case",
        action_type: str,
        step: str,
    ) -> None:
        """Emit DRY_RUN_ACTION audit event. Never raises."""
        if self._audit is None:
            return
        try:
            self._audit.log_dry_run_action(case=case, action_type=action_type, step=step)
        except Exception as exc:
            LOGGER.debug("support_agent_runtime: dry_run_action emit failed: %s", exc)

    def _emit_agent_started(self, case: "Case") -> None:
        """Emit audit event for agent run start. Never raises."""
        if self._audit is None:
            return
        try:
            self._audit.log_agent_run_started(case)
        except Exception as exc:
            LOGGER.debug("support_agent_runtime: audit started emit failed: %s", exc)

    def _emit_agent_completed(self, result: AgentExecutionResult, case: "Case") -> None:
        """Emit audit event for agent run completion. Never raises."""
        if self._audit is None:
            return
        try:
            self._audit.log_agent_run_completed(
                case,
                run_id=result.run_id,
                agent_status=result.agent_status.value,
                steps_completed=list(result.steps_completed),
                duration_ms=result.duration_ms,
            )
        except Exception as exc:
            LOGGER.debug("support_agent_runtime: audit completed emit failed: %s", exc)


# ── Factory ───────────────────────────────────────────────────────────────────

def build_support_agent_runtime(
    case_service:                Any = None,
    response_generation_service: Any = None,
    engineering_escalation_service: Any = None,
    audit_logger:                Any = None,
    mode:                        Any = None,
) -> SupportAgentRuntime:
    """
    Factory: build a SupportAgentRuntime.

    Args:
        case_service:                  CaseService (required for full pipeline).
        response_generation_service:   ResponseGenerationService (optional; skips USERRESPONSE if None).
        engineering_escalation_service: EngineeringEscalationService (optional; skips ASANACREATE if None).
        audit_logger:                  Optional AuditLogger.
        mode:                          SupportAgentMode (defaults to env var SUPPORT_AGENT_MODE, else DRY_RUN).

    Returns:
        SupportAgentRuntime ready to process cases.
    """
    if mode is None:
        mode = _mode_from_env()

    # Build defaults if services not provided
    if response_generation_service is None:
        try:
            from case_engine.response_generation.service import build_response_generation_service
            response_generation_service = build_response_generation_service(
                audit_logger=audit_logger,
            )
        except Exception as exc:
            LOGGER.warning("build_support_agent_runtime: response_svc failed error=%s", exc)

    if engineering_escalation_service is None:
        try:
            from case_engine.engineering.service import build_engineering_escalation_service
            engineering_escalation_service = build_engineering_escalation_service(
                audit_logger=audit_logger,
            )
        except Exception as exc:
            LOGGER.warning("build_support_agent_runtime: engineering_svc failed error=%s", exc)

    LOGGER.info("build_support_agent_runtime mode=%s", mode.value)
    return SupportAgentRuntime(
        case_service=case_service,
        response_generation_service=response_generation_service,
        engineering_escalation_service=engineering_escalation_service,
        audit_logger=audit_logger,
        mode=mode,
    )
