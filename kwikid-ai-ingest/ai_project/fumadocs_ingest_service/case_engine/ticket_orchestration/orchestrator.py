"""
case_engine/ticket_orchestration/orchestrator.py

Sprint 2.27.5: TicketOrchestrator — ticket lifecycle orchestration.

Per blueprint flow_diagram.mermaid:
  FD → TICKET → CASE → CLASSIFIER → ... → CLOSE

The TicketOrchestrator owns the ticket lifecycle:
  1. process_ticket(context)  — full ingestion → agent run pipeline
  2. resume_ticket(ticket_id, message_text) — process customer reply
  3. close_ticket(ticket_id)  — mark ticket as closed after resolution
  4. escalate_ticket(ticket_id, reason) — force-escalate to engineering

Design:
  - Never raises: all exceptions return TicketOrchestrationResult with success=False
  - SupportAgentRuntime is the single agent entrypoint; orchestrator delegates to it
  - CaseService is used only for open_case(); the agent handles the rest
  - In-memory ticket registry tracks lifecycle state (production: Freshdesk + DB)
  - Audit events emitted at each lifecycle transition
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from case_engine.ticket_orchestration.models import (
    TicketContext,
    TicketLifecycleState,
    TicketOrchestrationResult,
)

if TYPE_CHECKING:
    from case_engine.audit import AuditLogger
    from case_engine.runtime.support_agent_runtime import SupportAgentRuntime
    from case_engine.service import CaseService
    from case_engine.tenant.resolver import ClientResolver

from case_engine.trace import make_trace_id, trace_log

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    import uuid
    return str(uuid.uuid4())


def _ms_elapsed(started_ms: int) -> int:
    return max(0, int(time.monotonic() * 1000) - started_ms)


class TicketOrchestrator:
    """
    Ticket lifecycle orchestration layer.

    Owns the full ticket lifecycle:
      RECEIVED → OPEN → PROCESSING → (WAITING | ESCALATED | CLOSED | FAILED)

    Delegates agent work to SupportAgentRuntime.
    Delegates case lifecycle to CaseService (open_case only).

    In-memory registry: ticket_id → (TicketContext, case_id, TicketLifecycleState)
    Production extension point: override _load_ticket() and _save_ticket()
    to integrate with a persistent store.
    """

    def __init__(
        self,
        agent_runtime:   "SupportAgentRuntime | None" = None,
        case_service:    "CaseService | None" = None,
        audit_logger:    "AuditLogger | None" = None,
        client_resolver: "ClientResolver | None" = None,
    ) -> None:
        self._agent           = agent_runtime
        self._case_svc        = case_service
        self._audit           = audit_logger
        self._client_resolver = client_resolver
        # In-memory store: ticket_id → {"context": TicketContext, "case_id": str, "state": TicketLifecycleState}
        self._registry: dict[str, dict[str, Any]] = {}

    # ── Primary API ───────────────────────────────────────────────────────────

    def process_ticket(self, context: TicketContext) -> TicketOrchestrationResult:
        """
        Full ticket processing pipeline: ingest → open case → run agent.

        Per blueprint: FD → TICKET → CASE → agent pipeline → USERRESPONSE

        Returns TicketOrchestrationResult. Never raises.
        """
        started_ms = int(time.monotonic() * 1000)
        try:
            return self._process_ticket(context, started_ms)
        except Exception as exc:
            LOGGER.exception(
                "ticket_orchestrator.process_ticket fatal error ticket_id=%s error=%s",
                context.ticket_id, exc,
            )
            return TicketOrchestrationResult.failure(
                ticket_id=context.ticket_id,
                operation="process",
                error_code="TICKET_ORCHESTRATOR_FATAL",
                error_msg=str(exc),
                duration_ms=_ms_elapsed(started_ms),
            )

    def resume_ticket(
        self,
        ticket_id:    str,
        message_text: str,
        *,
        case_id: str | None = None,
    ) -> TicketOrchestrationResult:
        """
        Resume processing a ticket after a customer reply (CLARIFY loop).

        Per blueprint: CLOSECHECK -->|No| CLARIFICATION → customer replies → resume.

        Args:
            case_id: Optional hint for registry recovery after server restart.
                     When provided and ticket_id is missing from the in-memory
                     registry, the case is soft-recovered from CaseService.

        Returns TicketOrchestrationResult. Never raises.
        """
        LOGGER.info("ENTER_ORCHESTRATOR_RESUME_TICKET ticket_id=%s case_id=%s", ticket_id, case_id)
        started_ms = int(time.monotonic() * 1000)
        try:
            _result = self._resume_ticket(ticket_id, message_text, started_ms, case_id=case_id)
            LOGGER.info(
                "RETURN_ORCHESTRATOR_RESUME_TICKET ticket_id=%s error_code=%s",
                ticket_id, _result.error_code,
            )
            return _result
        except Exception as exc:
            LOGGER.exception(
                "ticket_orchestrator.resume_ticket fatal error ticket_id=%s error=%s",
                ticket_id, exc,
            )
            return TicketOrchestrationResult.failure(
                ticket_id=ticket_id,
                operation="resume",
                error_code="TICKET_RESUME_FATAL",
                error_msg=str(exc),
                duration_ms=_ms_elapsed(started_ms),
            )

    def close_ticket(self, ticket_id: str) -> TicketOrchestrationResult:
        """
        Mark ticket as closed after resolution confirmed.

        Per blueprint: CLOSECHECK -->|Yes| CLOSE.

        Returns TicketOrchestrationResult. Never raises.
        """
        started_ms = int(time.monotonic() * 1000)
        try:
            entry = self._registry.get(ticket_id)
            if entry is None:
                return TicketOrchestrationResult.failure(
                    ticket_id=ticket_id,
                    operation="close",
                    error_code="TICKET_NOT_FOUND",
                    error_msg=f"Ticket {ticket_id} not found in orchestrator registry",
                    duration_ms=_ms_elapsed(started_ms),
                )

            entry["state"] = TicketLifecycleState.CLOSED
            LOGGER.info("ticket_orchestrator.close ticket_id=%s", ticket_id)

            return TicketOrchestrationResult(
                orchestration_id=_new_id(),
                ticket_id=ticket_id,
                case_id=entry.get("case_id"),
                lifecycle_state=TicketLifecycleState.CLOSED,
                agent_result=None,
                operation="close",
                success=True,
                error_code=None,
                error_msg=None,
                executed_at=_now_iso(),
                duration_ms=_ms_elapsed(started_ms),
            )
        except Exception as exc:
            LOGGER.exception("ticket_orchestrator.close_ticket error ticket_id=%s error=%s", ticket_id, exc)
            return TicketOrchestrationResult.failure(
                ticket_id=ticket_id,
                operation="close",
                error_code="TICKET_CLOSE_FAILED",
                error_msg=str(exc),
                duration_ms=_ms_elapsed(started_ms),
            )

    def escalate_ticket(
        self,
        ticket_id: str,
        reason:    str = "",
    ) -> TicketOrchestrationResult:
        """
        Force-escalate a ticket to engineering / L2 team.

        Bypasses the L2CHECK decision node; used for manual escalations.

        Returns TicketOrchestrationResult. Never raises.
        """
        started_ms = int(time.monotonic() * 1000)
        try:
            entry = self._registry.get(ticket_id)
            case_id = entry.get("case_id") if entry else None

            if entry is not None:
                entry["state"] = TicketLifecycleState.ESCALATED

            agent_result: dict[str, Any] | None = None

            # If we have a case and an agent, run engineering escalation
            if case_id and self._agent is not None and self._case_svc is not None:
                try:
                    case = self._case_svc.get_case(case_id)
                    if case is not None and self._agent._engineering is not None:
                        eng_result = self._agent._engineering.create_ticket(
                            case=case,
                            topic=case.topic or "UNKNOWN",
                            freshdesk_ticket_id=ticket_id,
                            escalation_reason=reason or "Manual escalation requested.",
                        )
                        agent_result = {"engineering_escalation": eng_result.to_dict()}
                except Exception as exc:
                    LOGGER.warning(
                        "ticket_orchestrator.escalate_ticket engineering failed ticket_id=%s error=%s",
                        ticket_id, exc,
                    )

            LOGGER.info(
                "ticket_orchestrator.escalate ticket_id=%s case_id=%s reason=%s",
                ticket_id, case_id, reason,
            )
            return TicketOrchestrationResult(
                orchestration_id=_new_id(),
                ticket_id=ticket_id,
                case_id=case_id,
                lifecycle_state=TicketLifecycleState.ESCALATED,
                agent_result=agent_result,
                operation="escalate",
                success=True,
                error_code=None,
                error_msg=None,
                executed_at=_now_iso(),
                duration_ms=_ms_elapsed(started_ms),
            )
        except Exception as exc:
            LOGGER.exception("ticket_orchestrator.escalate_ticket error ticket_id=%s error=%s", ticket_id, exc)
            return TicketOrchestrationResult.failure(
                ticket_id=ticket_id,
                operation="escalate",
                error_code="TICKET_ESCALATE_FAILED",
                error_msg=str(exc),
                duration_ms=_ms_elapsed(started_ms),
            )

    def get_lifecycle_state(self, ticket_id: str) -> TicketLifecycleState | None:
        """Return current lifecycle state for a ticket, or None if not found."""
        entry = self._registry.get(ticket_id)
        if entry is None:
            return None
        return entry.get("state")

    def get_case_id(self, ticket_id: str) -> str | None:
        """Return the case_id associated with a ticket, or None."""
        entry = self._registry.get(ticket_id)
        if entry is None:
            return None
        return entry.get("case_id")

    # ── Private pipeline ──────────────────────────────────────────────────────

    def _process_ticket(
        self,
        context:    TicketContext,
        started_ms: int,
    ) -> TicketOrchestrationResult:
        # TRACE_ENTER_ORCHESTRATOR — always emits at WARNING; proves orchestrator was reached
        LOGGER.warning(
            "TRACE_ENTER_ORCHESTRATOR ticket_id=%s client=%s agent_wired=%s case_svc_wired=%s",
            context.ticket_id, context.client,
            self._agent is not None,
            self._case_svc is not None,
        )

        # 1. TICKET — register
        self._registry[context.ticket_id] = {
            "context":        context,
            "case_id":        None,
            "state":          TicketLifecycleState.RECEIVED,
            "tenant_context": None,
        }
        trace_id = context.metadata.get("trace_id") or make_trace_id(context.ticket_id)
        LOGGER.info(
            "ticket_orchestrator.received ticket_id=%s client=%s",
            context.ticket_id, context.client,
        )
        # Sprint 2.30.1 — TRACE_ORCHESTRATOR
        trace_log("TRACE_ORCHESTRATOR", trace_id,
                  ticket_id=context.ticket_id,
                  client=context.client,
                  resolver_wired=self._client_resolver is not None,
                  agent_wired=self._agent is not None)
        # TRACE_PAYLOAD_05_ORCHESTRATOR — final proof: ticket_id survived every transformation
        _orch_email_domain = context.requester_email.split("@")[-1] if "@" in (context.requester_email or "") else "(empty)"
        LOGGER.warning(
            "TRACE_PAYLOAD_05_ORCHESTRATOR ticket_id=%s subject=%r client=%r email_domain=%s description_len=%d",
            context.ticket_id,
            (context.subject or "")[:60],
            context.client,
            _orch_email_domain,
            len(context.description or ""),
        )

        # 1.5. CLIENT RESOLUTION — per blueprint/flow_diagram:
        #   TICKET → CLIENTRESOLVE → TENANTREG → TENANTCTX → CASE
        tenant_context = None
        if self._client_resolver is not None and context.requester_email:
            try:
                from case_engine.tenant.models import UnknownClientError  # noqa: PLC0415
                tenant_context = self._client_resolver.resolve(context.requester_email)
                self._registry[context.ticket_id]["tenant_context"] = tenant_context
                LOGGER.info(
                    "ticket_orchestrator.client_resolved ticket_id=%s client_id=%s domain=%s",
                    context.ticket_id, tenant_context.client_id, tenant_context.domain,
                )
                if self._audit is not None:
                    try:
                        self._audit.log_client_resolved(
                            ticket_id=context.ticket_id,
                            client_id=tenant_context.client_id,
                            client_name=tenant_context.client_name,
                            domain=tenant_context.domain,
                        )
                    except Exception:
                        pass
            except Exception as _uce:
                from case_engine.tenant.models import UnknownClientError  # noqa: PLC0415
                if isinstance(_uce, UnknownClientError):
                    domain = _uce.domain or "(no domain)"
                    LOGGER.warning(
                        "ticket_orchestrator.unknown_client ticket_id=%s domain=%s — escalating",
                        context.ticket_id, domain,
                    )
                    if self._audit is not None:
                        try:
                            self._audit.log_unknown_client(
                                ticket_id=context.ticket_id,
                                domain=domain,
                            )
                            self._audit.log_unknown_client_escalated(
                                ticket_id=context.ticket_id,
                                domain=domain,
                            )
                        except Exception:
                            pass
                    self._registry[context.ticket_id]["state"] = TicketLifecycleState.ESCALATED
                    return TicketOrchestrationResult(
                        orchestration_id=_new_id(),
                        ticket_id=context.ticket_id,
                        case_id=None,
                        lifecycle_state=TicketLifecycleState.ESCALATED,
                        agent_result=None,
                        operation="process",
                        success=False,
                        error_code="UNKNOWN_CLIENT",
                        error_msg=(
                            f"Client resolution failed for domain {domain!r}. "
                            "Ticket routed to human review per policy."
                        ),
                        executed_at=_now_iso(),
                        duration_ms=_ms_elapsed(started_ms),
                    )
                else:
                    LOGGER.warning(
                        "ticket_orchestrator.client_resolution_error ticket_id=%s error=%s — skipping resolution",
                        context.ticket_id, _uce,
                    )

        # 2. CASE — open case
        case = None
        if self._case_svc is not None:
            try:
                case = self._case_svc.open_case(context.ticket_id, context.client)
                # Attach resolved TenantContext to the Case (Sprint 2.27.9)
                if tenant_context is not None:
                    case.tenant_context = tenant_context
                    if self._audit is not None:
                        try:
                            self._audit.log_tenant_context_attached(
                                case_id=case.case_id,
                                ticket_id=context.ticket_id,
                                client_id=tenant_context.client_id,
                                client_name=tenant_context.client_name,
                                environment=tenant_context.environment.value,
                                tool_count=len(tenant_context.enabled_tools),
                            )
                        except Exception:
                            pass
                self._registry[context.ticket_id]["case_id"]  = case.case_id
                self._registry[context.ticket_id]["state"]    = TicketLifecycleState.OPEN
                LOGGER.info(
                    "ticket_orchestrator.case_opened ticket_id=%s case_id=%s",
                    context.ticket_id, case.case_id,
                )
            except Exception as exc:
                LOGGER.warning(
                    "ticket_orchestrator.open_case failed ticket_id=%s error=%s — continuing without case",
                    context.ticket_id, exc,
                )

        # 3. PROCESSING — run agent
        self._registry[context.ticket_id]["state"] = TicketLifecycleState.PROCESSING
        agent_result_dict: dict[str, Any] | None = None
        lifecycle_state   = TicketLifecycleState.PROCESSING

        if self._agent is not None and case is not None:
            try:
                agent_result = self._agent.run_case(
                    case=case,
                    message_text=context.message_text(),
                )
                agent_result_dict = agent_result.to_dict()

                # Map agent status to lifecycle state
                from case_engine.runtime.agent_models import AgentStatus
                if agent_result.agent_status == AgentStatus.SUCCESS:
                    lifecycle_state = TicketLifecycleState.CLOSED
                elif agent_result.agent_status == AgentStatus.AWAITING_CLARIFICATION:
                    lifecycle_state = TicketLifecycleState.WAITING
                elif agent_result.agent_status == AgentStatus.ESCALATED:
                    lifecycle_state = TicketLifecycleState.ESCALATED
                elif agent_result.agent_status == AgentStatus.AWAITING_APPROVAL:
                    lifecycle_state = TicketLifecycleState.WAITING
                else:
                    lifecycle_state = TicketLifecycleState.FAILED

                LOGGER.info(
                    "ticket_orchestrator.agent_complete ticket_id=%s status=%s lifecycle=%s",
                    context.ticket_id, agent_result.agent_status.value, lifecycle_state.value,
                )
            except Exception as exc:
                LOGGER.warning(
                    "ticket_orchestrator.agent_run failed ticket_id=%s error=%s",
                    context.ticket_id, exc,
                )
                lifecycle_state = TicketLifecycleState.FAILED
        elif self._agent is None:
            LOGGER.info(
                "ticket_orchestrator.no_agent ticket_id=%s — returning PROCESSING state",
                context.ticket_id,
            )
        else:
            LOGGER.warning(
                "ticket_orchestrator.no_case ticket_id=%s — agent skipped",
                context.ticket_id,
            )

        self._registry[context.ticket_id]["state"] = lifecycle_state

        return TicketOrchestrationResult(
            orchestration_id=_new_id(),
            ticket_id=context.ticket_id,
            case_id=case.case_id if case else None,
            lifecycle_state=lifecycle_state,
            agent_result=agent_result_dict,
            operation="process",
            success=lifecycle_state not in (TicketLifecycleState.FAILED,),
            error_code=None,
            error_msg=None,
            executed_at=_now_iso(),
            duration_ms=_ms_elapsed(started_ms),
        )

    def _resume_ticket(
        self,
        ticket_id:    str,
        message_text: str,
        started_ms:   int,
        *,
        case_id: str | None = None,
    ) -> TicketOrchestrationResult:
        LOGGER.info("ENTER_REGISTRY_GET ticket_id=%s", ticket_id)
        entry = self._registry.get(ticket_id)
        LOGGER.info("RETURN_REGISTRY_GET ticket_id=%s found=%s", ticket_id, entry is not None)
        if entry is None:
            # Soft recovery after server restart: if caller provides case_id and
            # CaseService is wired, reconstruct a minimal registry entry so the
            # agent can resume the case without requiring the in-memory registry.
            if case_id and self._case_svc is not None:
                try:
                    _case = self._case_svc.get_case(case_id)
                    if _case is not None:
                        entry = {
                            "context":  None,
                            "case_id":  case_id,
                            "state":    TicketLifecycleState.WAITING,
                        }
                        self._registry[ticket_id] = entry
                        LOGGER.warning(
                            "ORCHESTRATOR_REGISTRY_RECOVERED ticket_id=%s case_id=%s",
                            ticket_id, case_id,
                        )
                except Exception as _exc:
                    LOGGER.warning(
                        "ticket_orchestrator.resume soft_recovery_failed ticket_id=%s case_id=%s error=%s",
                        ticket_id, case_id, _exc,
                    )
            if entry is None:
                return TicketOrchestrationResult.failure(
                    ticket_id=ticket_id,
                    operation="resume",
                    error_code="TICKET_NOT_FOUND",
                    error_msg=f"Ticket {ticket_id} not in orchestrator registry",
                    duration_ms=_ms_elapsed(started_ms),
                )

        case_id = entry.get("case_id")
        case    = None
        if case_id and self._case_svc is not None:
            try:
                case = self._case_svc.get_case(case_id)
            except Exception as exc:
                LOGGER.warning(
                    "ticket_orchestrator.resume get_case failed ticket_id=%s error=%s",
                    ticket_id, exc,
                )

        agent_result_dict: dict[str, Any] | None = None
        lifecycle_state = TicketLifecycleState.PROCESSING
        entry["state"]  = TicketLifecycleState.PROCESSING

        if self._agent is not None and case is not None:
            try:
                agent_result = self._agent.run_case(
                    case=case,
                    message_text=message_text,
                )
                agent_result_dict = agent_result.to_dict()

                from case_engine.runtime.agent_models import AgentStatus
                if agent_result.agent_status == AgentStatus.SUCCESS:
                    lifecycle_state = TicketLifecycleState.CLOSED
                elif agent_result.agent_status in (AgentStatus.AWAITING_CLARIFICATION, AgentStatus.AWAITING_APPROVAL):
                    lifecycle_state = TicketLifecycleState.WAITING
                elif agent_result.agent_status == AgentStatus.ESCALATED:
                    lifecycle_state = TicketLifecycleState.ESCALATED
                else:
                    lifecycle_state = TicketLifecycleState.FAILED
            except Exception as exc:
                LOGGER.warning(
                    "ticket_orchestrator.resume agent_run failed ticket_id=%s error=%s",
                    ticket_id, exc,
                )
                lifecycle_state = TicketLifecycleState.FAILED

        entry["state"] = lifecycle_state

        return TicketOrchestrationResult(
            orchestration_id=_new_id(),
            ticket_id=ticket_id,
            case_id=case_id,
            lifecycle_state=lifecycle_state,
            agent_result=agent_result_dict,
            operation="resume",
            success=lifecycle_state not in (TicketLifecycleState.FAILED,),
            error_code=None,
            error_msg=None,
            executed_at=_now_iso(),
            duration_ms=_ms_elapsed(started_ms),
        )


# ── Factory ───────────────────────────────────────────────────────────────────

def build_ticket_orchestrator(
    agent_runtime:   Any = None,
    case_service:    Any = None,
    audit_logger:    Any = None,
    client_resolver: Any = None,
) -> TicketOrchestrator:
    """
    Factory: build a TicketOrchestrator.

    Args:
        agent_runtime:   SupportAgentRuntime (required for full pipeline).
        case_service:    CaseService (required for case lifecycle).
        audit_logger:    Optional AuditLogger.
        client_resolver: Optional ClientResolver (Sprint 2.27.9).
                         If provided, performs multi-tenant resolution before
                         opening a case. If None, resolution is skipped and
                         TicketContext.client is used as-is (backward compatible).

    Returns:
        TicketOrchestrator ready to process tickets.
    """
    return TicketOrchestrator(
        agent_runtime=agent_runtime,
        case_service=case_service,
        audit_logger=audit_logger,
        client_resolver=client_resolver,
    )
