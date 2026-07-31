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
    from intelligence import IntelligenceOrchestrator

LOGGER = logging.getLogger(__name__)


# ── Sprint 2.53 Wave 4A: intelligence-layer runtime-boundary traces ─────────
# ENTER_INTELLIGENCE / EXIT_INTELLIGENCE are emitted at the SupportAgentRuntime
# wiring point. Inner tags (ENTER_PROMPT / ENTER_LLM / ENTER_REASONING / etc.)
# fire inside intelligence/orchestrator.py per pipeline stage.
_ENTER_INTELLIGENCE = "ENTER_INTELLIGENCE"
_EXIT_INTELLIGENCE  = "EXIT_INTELLIGENCE"


def _intel_boundary_trace(tag: str, **fields: object) -> None:
    """Emit an intelligence runtime-boundary tag at WARNING level. Never raises."""
    try:
        parts = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
        LOGGER.warning("%s %s", tag, parts)
    except Exception:
        pass


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


# ── Sprint 2.53 Wave 4A: Intelligence-layer bridge helpers ────────────────────

def _to_retrieved_chunk(entry: Any, RetrievedChunkCls: Any) -> Any | None:
    """
    Normalize a knowledge entry (from HYBRIDRAG or InvestigationOrchestrator)
    into an `intelligence.RetrievedChunk`. Returns None if the shape can't be
    normalized. Never raises.
    """
    if entry is None:
        return None
    try:
        # Case 1: already a RetrievedChunk instance.
        if isinstance(entry, RetrievedChunkCls):
            return entry
        # Case 2: dict-like from KnowledgeOrchestrator.
        get = entry.get if isinstance(entry, dict) else lambda k, d=None: getattr(entry, k, d)
        content = get("content") or get("body") or get("text") or ""
        if not content:
            return None
        return RetrievedChunkCls(
            chunk_id=str(get("chunk_id") or get("id") or ""),
            source=  str(get("source") or get("provider") or "unknown"),
            title=   str(get("title") or get("name") or ""),
            content= str(content),
            score=   float(get("score") or get("relevance_score") or get("similarity") or 0.0),
            url=     str(get("url") or ""),
            metadata=dict(get("metadata") or {}),
        )
    except Exception:
        return None


def _extract_tenant_id(case: "Case") -> str:
    """Best-effort tenant_id extraction (never raises)."""
    for attr in ("tenant_id", "client_id"):
        val = getattr(case, attr, None)
        if val:
            return str(val)
    ctx = getattr(case, "tenant_context", None)
    if isinstance(ctx, dict):
        for key in ("tenant_id", "client_id", "tenant", "id"):
            if ctx.get(key):
                return str(ctx[key])
    meta = getattr(case, "metadata", None)
    if isinstance(meta, dict):
        val = meta.get("tenant_id") or meta.get("client_id")
        if val:
            return str(val)
    return "unknown"


def _extract_slot(case: "Case", keys: tuple[str, ...]) -> str:
    """Look up any of `keys` in case.slot_state. Returns first non-empty match."""
    slot_state = getattr(case, "slot_state", None) or {}
    if not isinstance(slot_state, dict):
        return ""
    for k in keys:
        v = slot_state.get(k)
        if v:
            return str(v)
    return ""


def _extract_trace_id(case: "Case") -> str:
    """Return a stable trace_id derived from ticket_id / case_id."""
    return str(getattr(case, "ticket_id", None) or getattr(case, "case_id", "") or "")


def _first_str(*candidates: Any) -> str:
    """Return the first non-empty string among candidates."""
    for c in candidates:
        if c is None:
            continue
        s = str(c).strip()
        if s:
            return s
    return ""


def _first_str_from_dict(d: Any, key: str) -> str:
    if isinstance(d, dict):
        v = d.get(key)
        if v is not None:
            return str(v)
    return ""


def _extract_slots_from_nlp_signal(
    case: "Case",
    topic: str | None,
    message_text: str,
) -> dict[str, str]:
    """
    Extract slot values for pre-fill, using NLPSignal entities as primary source.

    Sprint 2.5.6: NLPSignal entities from the LLM Semantic Router are the
    primary extraction method. Regex fallback is retained for slots not covered
    by NLPSignal (session_id format normalization, agent_id, endpoint_url).

    PII discipline: callers must NOT log the returned values, only the slot names.
    """
    import re as _re  # noqa: PLC0415

    extracted: dict[str, str] = {}

    # Primary: NLPSignal entities (LLM-extracted — more accurate than regex)
    nlp_signal = getattr(case, "nlp_signal", None)
    if isinstance(nlp_signal, dict):
        entities = nlp_signal.get("entities") or {}
        if isinstance(entities, dict):
            for slot_name, value in entities.items():
                if isinstance(value, str) and value.strip():
                    extracted[slot_name] = value.strip()

    if not message_text or not topic:
        return extracted

    text = message_text

    # Fallback regex for session_id normalization (KID-XXXXXXXX format)
    # Only applies if NLPSignal didn't already extract session_id
    if "session_id" not in extracted:
        ms = _re.search(r"\b(KID[-_]?[A-Z0-9]{6,12})\b", text, _re.IGNORECASE)
        if ms:
            extracted["session_id"] = ms.group(1).upper().replace("_", "-")

    # Fallback regex for agent_id (Agent Portal topic)
    if topic == "Agent_Portal_Issue" and "agent_id" not in extracted:
        ma = _re.search(r"\b(AG[T]?[-_]?[0-9A-Z]{4,12}|\d{6,10})\b", text, _re.IGNORECASE)
        if ma:
            extracted["agent_id"] = ma.group(1)

    # Fallback regex for channel (OTP topic, optional slot)
    if topic == "OTP_Delivery_Failure" and "channel" not in extracted:
        mc = _re.search(r"\b(email|e[\s\-]?mail|voice|sms)\b", text, _re.IGNORECASE)
        if mc:
            raw = mc.group(1).lower().replace(" ", "").replace("-", "")
            if raw in ("email", "e-mail"):
                extracted["channel"] = "EMAIL"
            elif raw == "voice":
                extracted["channel"] = "VOICE"
            else:
                extracted["channel"] = "SMS"

    # Fallback regex for phone_number (optional — used post-investigation for resend)
    if topic in ("OTP_Delivery_Failure", "VKYC_Session_Failure") and "phone_number" not in extracted:
        m = _re.search(r"\b([6-9]\d{9})\b", text)
        if m:
            extracted["phone_number"] = m.group(1)[-4:]

    # Fallback regex for endpoint_url (API Callback Failure)
    if topic == "API_Callback_Failure" and "endpoint_url" not in extracted:
        mu = _re.search(r"https?://[^\s,\"'<>]+", text)
        if mu:
            extracted["endpoint_url"] = mu.group(0).rstrip(".")

    return extracted


def _extract_free_form_slots(topic: str | None, message_text: str) -> dict[str, str]:
    """
    Legacy regex-only slot extraction (used when no Case/NLPSignal is available).

    Sprint 2.5.6: This function is kept for callers that don't have access to the
    Case object. New code should use _extract_slots_from_nlp_signal() instead.

    PII discipline: callers must NOT log the returned values, only the slot names.
    """
    import re as _re  # noqa: PLC0415
    if not message_text or not topic:
        return {}
    extracted: dict[str, str] = {}
    text = message_text

    # session_id: KID-XXXXXXXX format — only for VKYC and OCR topics
    # (OTP investigation requires URN + session_id, but session_id must come from
    #  the agent-provided context, not regex-guessed from unrelated ticket text)
    if topic in ("VKYC_Session_Failure", "Document_OCR_Failure"):
        ms = _re.search(r"\b(KID[-_]?[A-Z0-9]{6,12})\b", text, _re.IGNORECASE)
        if ms:
            extracted["session_id"] = ms.group(1).upper().replace("_", "-")

    # channel: OTP delivery channel (optional slot)
    if topic == "OTP_Delivery_Failure":
        mc = _re.search(r"\b(email|e[\s\-]?mail|voice|sms)\b", text, _re.IGNORECASE)
        if mc:
            raw = mc.group(1).lower().replace(" ", "").replace("-", "")
            if raw in ("email", "e-mail"):
                extracted["channel"] = "EMAIL"
            elif raw == "voice":
                extracted["channel"] = "VOICE"
            else:
                extracted["channel"] = "SMS"

    # phone_number: optional, post-investigation (10-digit mobile or "last N digits" mention)
    if topic in ("OTP_Delivery_Failure", "VKYC_Session_Failure"):
        m = _re.search(r"\b([6-9]\d{9})\b", text)
        if m:
            extracted["phone_number"] = m.group(1)[-4:]
        elif "phone_number" not in extracted:
            m4 = _re.search(
                r"(?:last\s+\d+\s+digits?)\s+(?:are\s+|is\s+)?(\d{4})\b",
                text,
                _re.IGNORECASE,
            )
            if m4:
                extracted["phone_number"] = m4.group(1)

    # document_id: alphanumeric document ID for OCR failures (e.g., AB12345678)
    if topic == "Document_OCR_Failure":
        md = _re.search(r"\b([A-Z]{2}\d{8})\b", text, _re.IGNORECASE)
        if md:
            extracted["document_id"] = md.group(1).upper()

    # agent_id: Agent Portal
    if topic == "Agent_Portal_Issue":
        ma = _re.search(r"\b(AG[T]?[-_]?[0-9A-Z]{4,12}|\d{6,10})\b", text, _re.IGNORECASE)
        if ma:
            extracted["agent_id"] = ma.group(1)

    # endpoint_url: API Callback
    if topic == "API_Callback_Failure":
        mu = _re.search(r"https?://[^\s,\"'<>]+", text)
        if mu:
            extracted["endpoint_url"] = mu.group(0).rstrip(".")

    return extracted


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
        intelligence_orchestrator:   "IntelligenceOrchestrator | None" = None,
    ) -> None:
        self._case_svc      = case_service
        self._response_svc  = response_generation_service
        self._engineering   = engineering_escalation_service
        self._audit         = audit_logger
        self._mode          = mode
        # Sprint 2.53 Wave 4A: Intelligence Layer (Reasoning + Observation +
        # Customer Reply + Action Proposal via LLM). When None, the runtime
        # falls through to the legacy ResponseGenerationService path — no
        # LLM calls, deterministic templates only.
        self._intelligence  = intelligence_orchestrator

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

        # Frozen state guard: ESCALATED cases cannot re-enter automated pipeline.
        # ESCALATED can only transition to CLOSED (human agent closes it).
        # Attempting ESCALATED → CLASSIFYING/WORKFLOW_ACTIVE is an illegal transition.
        if case.current_state == CaseState.ESCALATED:
            LOGGER.warning(
                "RETURN_RUNTIME_FROZEN case_id=%s state=ESCALATED — skipping pipeline",
                case.case_id,
            )
            return self._build_result(
                case=case, steps_completed=steps_completed,
                agent_status=AgentStatus.ESCALATED, workflow_result=None,
                response_draft=None, engineering_result=None,
                classification={"topic": case.topic, "confidence": case.confidence or 0.0},
                started_at=started_at, started_ms=started_ms, clarification_question="",
            )

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

                # Pre-extract slots from NLPSignal entities (primary) + regex fallback.
                # Sprint 2.5.6: NLPSignal from LLM router is more accurate than regex.
                # Entities include urn, session_id, phone_number, agent_id, etc.
                _free_form = _extract_slots_from_nlp_signal(case, case.topic, message_text)
                # Merge: explicit_slot (caller-provided) takes priority over NLPSignal/regex
                _pre_slots: dict[str, str] = {
                    k: v for k, v in {**_free_form, **explicit_slot}.items() if v
                }

                # Pre-fill each free-form slot through the official CaseService API.
                # This persists to case.slot_state and may auto-start the workflow
                # (idempotent — WorkflowAlreadyStartedError is caught inside).
                for _sn, _sv in _pre_slots.items():
                    LOGGER.warning(
                        "ENTER_PREFILL_SLOT case_id=%s topic=%s slot=%s",
                        case.case_id, case.topic, _sn,
                    )
                    self._case_svc.receive_message(
                        case, message_text,
                        slot_name=_sn,
                        slot_value_str=str(_sv),
                    )

                # Final receive_message: enum extraction + all_slots_filled check.
                # Idempotent if workflow was auto-started by a pre-fill call above.
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
                    # Capture the clarification question for use in Step 7 response
                    # generation, but do NOT return early — investigation runs first
                    # per Blueprint §7 "Investigate First" policy (Sprint 2.54 Wave 4B).
                    if msg_result.next_question:
                        clarification_question = (
                            msg_result.next_question.get("prompt_text")
                            or msg_result.next_question.get("text")
                            or ""
                        )
                    steps_completed.append("CLARIFY")
                    LOGGER.warning(
                        "ENTER_CLARIFICATION_POLICY case_id=%s question=%r — investigating with available slots",
                        case.case_id, clarification_question[:80] if clarification_question else "",
                    )
                    # Fall through to investigation (Step 4). EvidenceCollector handles
                    # missing slots by producing failed evidence items rather than crashing.
                else:
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

        # Recover workflow_result when a pre-fill receive_message() auto-started the
        # workflow (return value was discarded in the for-loop so workflow_result is
        # still None here, but case.workflow_id proves the workflow already started).
        # Without this guard the explicit start_workflow() below runs a second time,
        # causing the investigation planner to execute twice.
        if workflow_result is None and getattr(case, "workflow_id", None):
            workflow_result = dict(getattr(case, "workflow_context", None) or {})
            workflow_result["workflow_state"] = case.workflow_state
            workflow_result["workflow_id"]    = case.workflow_id
            steps_completed.append("WORKFLOW_PREFILL_AUTOSTARTED")
            LOGGER.warning(
                "ENTER_WORKFLOW_PREFILL_AUTOSTARTED case_id=%s workflow_id=%s",
                case.case_id, case.workflow_id,
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
                    "workflow_id":        wf_start.workflow_id,
                    "workflow_state":     wf_start.workflow_state,
                    "step_results":       wf_start.step_results,
                    "resolved":           wf_start.resolved,
                    "escalated":          wf_start.escalated,
                    "escalation_reason":  wf_start.escalation_reason,
                    "resolution_note":    wf_start.resolution_note,
                    "workflow_context":   getattr(wf_start, "workflow_context", None),
                    "investigation_result": getattr(wf_start, "investigation_result", None),
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

        # ── Step 4.5: INTELLIGENCE (Wave 4A) ─────────────────────────────────
        # Runs the LLM-based reasoning + observation + reply + action-proposal
        # pipeline. Blueprint path: EVIDENCE → ROOTCAUSE → HYBRIDRAG →
        # REASONING → LLM → GUARDRAILS → (OBSGEN + ACTIONPROPOSAL) →
        # USERRESPONSE. Never raises — falls back to legacy templating on any
        # error so no ticket is ever blocked by an LLM outage.
        intelligence_result: dict[str, Any] | None = None
        if self._intelligence is not None:
            intelligence_result = self._run_intelligence(
                case=case,
                message_text=message_text,
                workflow_result=workflow_result,
            )
            if intelligence_result is not None:
                steps_completed.append("INTELLIGENCE")

        # ── Step 5: NOTEGEN + L2CHECK ────────────────────────────────────────
        steps_completed.append("NOTEGEN")
        needs_l2   = _needs_engineering_escalation(case.topic, workflow_result)
        # Sprint 2.53 Wave 4A: intelligence reasoning outcome ESCALATE also
        # triggers L2 (Reasoning Engine — Blueprint §13 outputs
        # Recommended Escalation).
        if not needs_l2 and intelligence_result:
            try:
                intel_outcome = (intelligence_result.get("reasoning") or {}).get("outcome", "")
                if intel_outcome == "ESCALATE":
                    needs_l2 = True
            except Exception:
                pass
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

        response_draft = self._response_from_intelligence(
            intelligence_result=intelligence_result,
            response_type=response_type,
        )
        if response_draft is None:
            response_draft = self._generate_response(
                case=case,
                topic=case.topic or "Support Request",
                response_type=response_type,
                workflow_result=workflow_result,
                clarification_question=clarification_question,
            )

        LOGGER.warning(
            "EXIT_RESPONSE_GENERATION case_id=%s draft_is_none=%s draft_source=%s draft_keys=%s",
            case.case_id, response_draft is None,
            "intelligence" if intelligence_result and intelligence_result.get("customer_reply") else "legacy",
            list(response_draft.keys()) if isinstance(response_draft, dict) else None,
        )

        # ── CLOSECHECK ────────────────────────────────────────────────────────
        # Clarification takes priority over L2 escalation: the case is waiting for
        # customer input. Blueprint §7: investigation runs first, then clarification
        # is issued only if evidence is genuinely incomplete — not skipped for L2.
        if response_type == ResponseType.RESOLUTION:
            agent_status = AgentStatus.SUCCESS
        elif response_type == ResponseType.CLARIFICATION:
            agent_status = AgentStatus.AWAITING_CLARIFICATION
        elif response_type == ResponseType.ESCALATION or needs_l2:
            agent_status = AgentStatus.ESCALATED
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
            intelligence_result=intelligence_result,
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
        intelligence_result:    dict[str, Any] | None = None,
    ) -> AgentExecutionResult:
        from case_engine.runtime.agent_models import _new_id
        metadata: dict[str, Any] = {}
        if intelligence_result is not None:
            metadata["intelligence_result"] = intelligence_result
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
            metadata=metadata,
        )

    # ── Sprint 2.53 Wave 4A: Intelligence Layer runtime bridge ────────────────

    def _run_intelligence(
        self,
        case:            "Case",
        message_text:    str,
        workflow_result: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """
        Run the Intelligence Layer (LLM Reasoning + Observation + Reply +
        Action Proposal) for the current case. Returns IntelligenceResult
        as dict, or None on any failure. Never raises.

        Blueprint path (from flow_diagram.mermaid):
            EVIDENCE → ROOTCAUSE → HYBRIDRAG → REASONING → LLM →
            GUARDRAILS → (OBSGEN + ACTIONPROPOSAL) → USERRESPONSE
        """
        if self._intelligence is None:
            return None

        started = time.monotonic()
        _intel_boundary_trace(
            _ENTER_INTELLIGENCE,
            case_id=case.case_id, ticket_id=case.ticket_id,
            topic=case.topic, workflow_id=(case.workflow_id or ""),
        )

        try:
            from intelligence import build_llm_context, RetrievedChunk  # noqa: PLC0415

            investigation   = _extract_investigation(workflow_result)
            evidence_bundle = None
            if investigation is not None:
                evidence_bundle = (
                    investigation.get("evidence_bundle")
                    or investigation.get("evidence")
                    or investigation.get("evidence_items")
                )

            # Sprint 2.53 Wave 4A — Task 2: Knowledge Integration.
            # Combine chunks from two sources per blueprint:
            #   1. HYBRIDRAG output via WorkflowEngine.KnowledgeOrchestrator
            #      (workflow_context.knowledge_result.chunks)
            #   2. InvestigationOrchestrator.knowledge_entries
            knowledge_chunks: list = []
            wf_knowledge = _extract_workflow_knowledge(workflow_result)
            for entry in (wf_knowledge.get("chunks") or []):
                chunk = _to_retrieved_chunk(entry, RetrievedChunk)
                if chunk is not None:
                    knowledge_chunks.append(chunk)
            if investigation is not None:
                for entry in (investigation.get("knowledge_entries") or []):
                    chunk = _to_retrieved_chunk(entry, RetrievedChunk)
                    if chunk is not None:
                        knowledge_chunks.append(chunk)
            # KnowledgeResult.to_dict() stores SOP content in
            # search_result.matches[*].entry — not under a "chunks" key. Extract
            # top-3 matches as RetrievedChunks so the LLM receives SOP body text.
            # This is the primary knowledge path when the workflow ran KNOWLEDGE_LOOKUP.
            if not knowledge_chunks:
                for match in (wf_knowledge.get("search_result") or {}).get("matches", [])[:3]:
                    if isinstance(match, dict):
                        entry = match.get("entry") or {}
                        merged = {**entry, "score": match.get("relevance_score", 0.0)}
                        chunk = _to_retrieved_chunk(merged, RetrievedChunk)
                        if chunk is not None:
                            knowledge_chunks.append(chunk)

            # Tenant + customer-identifier extraction (all PII-safe from here;
            # context_builder does the actual masking).
            tenant_id = _extract_tenant_id(case)
            phone     = _extract_slot(case, ("phone_number", "phone", "urn"))
            email     = _extract_slot(case, ("email", "customer_email"))

            llm_ctx = build_llm_context(
                case_id=            case.case_id or "",
                ticket_id=          case.ticket_id or "",
                tenant_id=          tenant_id,
                topic=              case.topic or "",
                workflow_id=        case.workflow_id or "",
                classification=     (case.topic or ""),
                ticket_subject=     _first_str(getattr(case, "subject", None),
                                               _first_str_from_dict(
                                                   getattr(case, "metadata", None), "subject")),
                ticket_description= message_text or "",
                customer_phone=     phone,
                customer_email=     email,
                evidence_bundle=    evidence_bundle,
                retrieved_chunks=   knowledge_chunks,
                conversation=       [],
                tool_results=       investigation if isinstance(investigation, dict) else {},
                trace_id=           _extract_trace_id(case),
            )

            # Async→sync bridge. Reset the orchestrator's httpx client so the
            # client is bound to THIS event loop (httpx.AsyncClient is loop-
            # bound; sharing across loops causes RuntimeError). See
            # IntelligenceOrchestrator._ensure_client / close().
            try:
                self._intelligence._client      = None      # noqa: SLF001
                self._intelligence._owns_client = True      # noqa: SLF001
            except Exception:
                pass

            import asyncio  # noqa: PLC0415

            async def _do():
                try:
                    return await self._intelligence.orchestrate(llm_ctx)
                finally:
                    try:
                        await self._intelligence.close()
                    except Exception:
                        pass

            # asyncio.run() cannot be called from a running event loop (FastAPI async
            # background tasks). Detect the running loop and use a ThreadPoolExecutor
            # so asyncio.run() executes in a thread with no existing event loop.
            try:
                asyncio.get_running_loop()
                import concurrent.futures as _cf  # noqa: PLC0415
                with _cf.ThreadPoolExecutor(max_workers=1) as _pool:
                    intel_result = _pool.submit(asyncio.run, _do()).result()
            except RuntimeError:
                intel_result = asyncio.run(_do())
            duration_ms  = int((time.monotonic() - started) * 1000)

            _intel_boundary_trace(
                _EXIT_INTELLIGENCE,
                case_id=case.case_id, ticket_id=case.ticket_id,
                outcome=intel_result.reasoning.outcome.value,
                confidence=f"{intel_result.reasoning.confidence:.2f}",
                has_reply=intel_result.customer_reply is not None,
                proposals=len(intel_result.action_proposals),
                duration_ms=duration_ms,
                chunks=len(knowledge_chunks),
                evidence_hints=len(llm_ctx.evidence_hints),
            )
            return intel_result.to_dict()
        except Exception as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            _intel_boundary_trace(
                _EXIT_INTELLIGENCE,
                case_id=case.case_id, ticket_id=case.ticket_id,
                status=f"ERROR:{type(exc).__name__}",
                duration_ms=duration_ms,
            )
            LOGGER.warning(
                "support_agent_runtime.intelligence failed case_id=%s error=%s",
                case.case_id, exc,
            )
            return None

    def _response_from_intelligence(
        self,
        intelligence_result: dict[str, Any] | None,
        response_type:       ResponseType,
    ) -> dict[str, Any] | None:
        """
        Convert IntelligenceResult.customer_reply into a legacy ResponseDraft
        dict shape, so the downstream Freshdesk write-path
        (FreshdeskResponseService + ReplySafetyGate) can consume it unchanged.
        Returns None if the intelligence layer didn't produce a reply.
        """
        if not intelligence_result:
            return None
        reply = intelligence_result.get("customer_reply")
        if not isinstance(reply, dict):
            return None
        body_html = reply.get("body_html") or ""
        if not body_html:
            return None
        return {
            "body_html":      body_html,
            "reply_kind":     reply.get("reply_kind", ""),
            "confidence":     reply.get("confidence", 0.0),
            "confidence_level": reply.get("confidence_level", "LOW"),
            "response_type":  response_type.value if hasattr(response_type, "value") else str(response_type),
            "citations":      list(reply.get("citations", [])),
            "source":         "intelligence_layer",
        }

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
    intelligence_orchestrator:   Any = None,
) -> SupportAgentRuntime:
    """
    Factory: build a SupportAgentRuntime.

    Args:
        case_service:                  CaseService (required for full pipeline).
        response_generation_service:   ResponseGenerationService (optional; skips USERRESPONSE if None).
        engineering_escalation_service: EngineeringEscalationService (optional; skips ASANACREATE if None).
        audit_logger:                  Optional AuditLogger.
        mode:                          SupportAgentMode (defaults to env var SUPPORT_AGENT_MODE, else DRY_RUN).
        intelligence_orchestrator:     Optional Sprint 2.53 IntelligenceOrchestrator.
                                       When None, runtime falls back to deterministic templating.

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

    LOGGER.info(
        "build_support_agent_runtime mode=%s intelligence_wired=%s",
        mode.value, intelligence_orchestrator is not None,
    )
    return SupportAgentRuntime(
        case_service=case_service,
        response_generation_service=response_generation_service,
        engineering_escalation_service=engineering_escalation_service,
        audit_logger=audit_logger,
        mode=mode,
        intelligence_orchestrator=intelligence_orchestrator,
    )
