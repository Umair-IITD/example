"""
runtime/assembly.py

Sprint 2.6:  Production runtime assembly.
Sprint 2.10: Added MetricsService and audit factory wiring.
Sprint 2.26: Added full workflow service stack to ProductionRuntime.

build_production_runtime() is the single composition point.
Application startup calls it once and receives a fully-wired ProductionRuntime.

Key design constraint: ALL components share a single ActionRepository instance.
This prevents split-brain between the gateway (which records state transitions)
and the runtime/worker (which reads state). The factory guarantees this invariant.

Sprint 2.26 Runtime Dependency Graph (services → WorkflowEngine):
  AuditLogger
  ToolRegistry → ToolExecutor → InvestigationService
  KnowledgeService (standalone)
  ReasoningService (standalone)
  ActionProposalService (standalone)
  ActionGatewayService (standalone)
  ExecutionService (standalone)
  ClarificationService (standalone)
  WorkflowEngine ← all 7 above
  PlaybookRegistry (standalone)
  CaseService ← WorkflowEngine, PlaybookRegistry, ActionGateway, AuditLogger
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from audit.factory import build_audit_repository
from audit.logger import AuditLogger
from audit.repository import AuditRepository
from audit.service import AuditService
from case_engine.action_gateway import ActionGateway
from case_engine.action_gateway_operations import ActionGatewayOperationsService
from case_engine.action_gateway_recovery import ActionGatewayRecoveryService
from case_engine.action_repository import ActionRepository
from case_engine.action_runtime import ActionRuntime, EXECUTION_TIMEOUT_DEFAULT
from case_engine.executor_registry import ActionExecutorRegistry
from case_engine.provider_registry import ProviderRegistry
from case_engine.provider_router import ProviderRouter
from case_engine.sla_watchdog import SLAWatchdog
from executors import (
    AddTicketNoteExecutor,
    IdentityResetOtpExecutor,
    UpdateTicketStatusExecutor,
)
from metrics import MetricsCollector, MetricsService
from runtime.health import HealthService
from worker.action_worker import ActionWorker


@dataclass
class ProductionRuntime:
    """
    Fully-wired execution stack returned by build_production_runtime().

    All components share the same repository instance. Use this object
    at application startup to drive the worker loop.

    Sprint 2.26: Workflow services (clarification_service through case_service)
    are now included so all downstream callers receive fully-wired instances.

    Sprint 2.27.5: Architecture convergence — KnowledgeOrchestrator,
    RouterService, ResponseGenerationService, EngineeringEscalationService,
    SupportAgentRuntime, and TicketOrchestrator added.

    Access pattern:
        stack = build_production_runtime(...)
        rollbacks, forwards = stack.worker.process(client="unity_bank")
        result = stack.watchdog.run(client="unity_bank")
    """
    # ── Action execution stack (Sprint 2.6+) ──────────────────────────────────
    runtime: ActionRuntime
    gateway: ActionGateway
    repository: ActionRepository
    executor_registry: ActionExecutorRegistry
    provider_registry: ProviderRegistry
    router: ProviderRouter
    worker: ActionWorker
    health: HealthService
    watchdog: SLAWatchdog
    audit_repository: AuditRepository
    audit_service: AuditService
    audit_logger: AuditLogger
    metrics_service: MetricsService
    operations: ActionGatewayOperationsService
    recovery: ActionGatewayRecoveryService
    # ── Workflow service stack (Sprint 2.26) ──────────────────────────────────
    clarification_service: Any = None   # case_engine.clarification.service.ClarificationService
    investigation_service: Any = None   # case_engine.investigation.service.InvestigationService
    knowledge_service:     Any = None   # case_engine.knowledge.service.KnowledgeService
    reasoning_service:     Any = None   # case_engine.reasoning.service.ReasoningService
    action_proposal_service: Any = None # case_engine.actions.service.ActionProposalService
    action_gateway_service: Any = None  # case_engine.action_gateway.service.ActionGatewayService
    execution_service:     Any = None   # case_engine.execution.service.ExecutionService
    workflow_engine:       Any = None   # case_engine.workflows.workflow_engine.WorkflowEngine
    playbook_registry:     Any = None   # case_engine.workflows.playbook_registry.PlaybookRegistry
    case_service:          Any = None   # case_engine.service.CaseService
    # ── Adapter Framework (Sprint 2.27) ──────────────────────────────────────
    adapter_registry:      Any = None   # case_engine.adapters.AdapterRegistry
    adapter_router:        Any = None   # case_engine.adapters.AdapterRouter
    # ── Architecture Convergence (Sprint 2.27.5) ─────────────────────────────
    router_service:                  Any = None   # case_engine.integrations.RouterService
    knowledge_orchestrator:          Any = None   # case_engine.knowledge.KnowledgeOrchestrator
    response_generation_service:     Any = None   # case_engine.response_generation.ResponseGenerationService
    engineering_escalation_service:  Any = None   # case_engine.engineering.EngineeringEscalationService
    support_agent_runtime:           Any = None   # case_engine.runtime.SupportAgentRuntime
    ticket_orchestrator:             Any = None   # case_engine.ticket_orchestration.TicketOrchestrator
    # ── Multi-Tenant Resolution (Sprint 2.27.9) ──────────────────────────────
    tenant_registry:                 Any = None   # case_engine.tenant.TenantRegistry
    client_resolver:                 Any = None   # case_engine.tenant.ClientResolver
    tenant_tool_registry:            Any = None   # case_engine.tenant.TenantAwareToolRegistry
    # ── Startup Validation (Sprint 2.27.8) ───────────────────────────────────
    startup_validation_result:       Any = None   # runtime.startup_validation.RuntimeValidationResult


def build_production_runtime(
    *,
    freshdesk_config: Any = None,
    supabase_client: Any = None,
    worker_id: str = "worker-1",
    batch_size: int = 10,
    execution_timeout: timedelta = EXECUTION_TIMEOUT_DEFAULT,
) -> ProductionRuntime:
    """
    Compose and return the complete execution stack.

    Args:
        freshdesk_config: FreshdeskConfig for the Freshdesk HTTP provider.
                          None → offline/test mode (no HTTP calls).
        supabase_client:  supabase-py Client for persistence.
                          None → offline/test mode (in-memory only).
        worker_id:        Stable identifier claimed as executor_id on actions.
                          Must be unique across concurrent workers.
        batch_size:       Maximum actions processed per worker.tick() call.
        execution_timeout: Wall-clock deadline budget per execution attempt.

    Returns:
        ProductionRuntime with all components wired and ready.

    Raises:
        ValueError:                  freshdesk_config validation failed.
        ProviderRegistrationError:   provider already registered (programmer error).
        ExecutorRegistrationError:   executor duplicate detected (programmer error).
    """
    # Metrics stack — wired into gateway, runtime, and watchdog
    metrics_collector = MetricsCollector()
    metrics_service = MetricsService(collector=metrics_collector)

    # Single shared repository — all components use the same instance
    repository = ActionRepository(supabase_client=supabase_client)
    gateway = ActionGateway(repository=repository, metrics_service=metrics_service)

    # Audit stack — backend selected via AUDIT_BACKEND env var
    audit_repository = build_audit_repository(supabase_client)
    audit_service = AuditService(repository=audit_repository)
    audit_logger = AuditLogger(repository=audit_repository)

    # Provider layer
    provider_registry = ProviderRegistry()
    if freshdesk_config is not None:
        from freshdesk.freshdesk_provider import FreshdeskProvider
        provider_registry.register(FreshdeskProvider(freshdesk_config))

    router = ProviderRouter(provider_registry)

    # Executor layer
    executor_registry = ActionExecutorRegistry()
    _register_all_executors(executor_registry, router)

    # Runtime (gateway + repo + registry share the same instances)
    runtime = ActionRuntime(
        gateway=gateway,
        repository=repository,
        registry=executor_registry,
        execution_timeout=execution_timeout,
        audit_service=audit_service,
        metrics_service=metrics_service,
    )

    worker = ActionWorker(
        runtime=runtime,
        repository=repository,
        worker_id=worker_id,
        batch_size=batch_size,
    )

    health = HealthService(
        provider_registry=provider_registry,
        executor_registry=executor_registry,
        runtime=runtime,
        worker=worker,
    )

    watchdog = SLAWatchdog(
        gateway=gateway,
        repository=repository,
        audit_service=audit_service,
        metrics_service=metrics_service,
    )

    operations = ActionGatewayOperationsService(
        repository=repository,
        metrics_service=metrics_service,
    )

    recovery = ActionGatewayRecoveryService(
        repository=repository,
        gateway=gateway,
        audit_service=audit_service,
    )

    # ── Adapter Framework (Sprint 2.27) ──────────────────────────────────────
    adapter_registry, adapter_router = _build_adapter_stack(audit_logger=audit_logger)

    # ── Workflow service stack (Sprint 2.26 + 2.27 + 2.27.5) ─────────────────
    workflow_services = _build_workflow_services(
        audit_logger,
        gateway,
        adapter_router=adapter_router,
        adapter_registry=adapter_registry,
    )

    production_runtime = ProductionRuntime(
        runtime=runtime,
        gateway=gateway,
        repository=repository,
        executor_registry=executor_registry,
        provider_registry=provider_registry,
        router=router,
        worker=worker,
        health=health,
        watchdog=watchdog,
        audit_repository=audit_repository,
        audit_service=audit_service,
        audit_logger=audit_logger,
        metrics_service=metrics_service,
        operations=operations,
        recovery=recovery,
        **workflow_services,
    )

    # ── Sprint 2.27.8: Startup Validation ────────────────────────────────────
    import logging as _logging
    _LOG_ASSEMBLY = _logging.getLogger(__name__)
    try:
        from runtime.startup_validation import validate_production_runtime  # noqa: PLC0415
        validation_result = validate_production_runtime(production_runtime)
        production_runtime.startup_validation_result = validation_result
        if not validation_result.overall_passed:
            _LOG_ASSEMBLY.error(
                "assembly: startup_validation FAILED critical_failed=%s",
                validation_result.critical_failed,
            )
        else:
            _LOG_ASSEMBLY.info(
                "assembly: startup_validation PASSED warnings=%d",
                len(validation_result.warnings),
            )
    except Exception as _val_exc:
        _LOG_ASSEMBLY.warning("assembly: startup_validation failed to run error=%s", _val_exc)

    return production_runtime


def _register_all_executors(
    registry: ActionExecutorRegistry,
    router: ProviderRouter,
) -> None:
    """Register all Sprint 2.5 production executors. Fails fast on duplicate."""
    registry.register_executor(AddTicketNoteExecutor(router))
    registry.register_executor(UpdateTicketStatusExecutor(router))
    registry.register_executor(IdentityResetOtpExecutor(router))


def _build_adapter_stack(audit_logger: Any = None) -> tuple[Any, Any]:
    """
    Build AdapterRegistry (all 4 placeholder adapters) and AdapterRouter.

    Returns (adapter_registry, adapter_router). Both are None on failure.
    Never raises.

    Sprint 2.27.5: audit_logger now passed to AdapterRouter so adapter
    events are captured in the audit trail.
    """
    import logging as _logging
    _LOG = _logging.getLogger(__name__)
    try:
        from case_engine.adapters import AdapterRegistry, AdapterRouter  # noqa: PLC0415
        registry = AdapterRegistry.build_default()
        router   = AdapterRouter(registry=registry, audit_logger=audit_logger)
        _LOG.info(
            "assembly: adapter_stack built adapters=%d audit_wired=%s",
            registry.count(), audit_logger is not None,
        )
        return registry, router
    except Exception as exc:
        _logging.getLogger(__name__).warning(
            "assembly: adapter_stack failed error=%s", exc
        )
        return None, None


def _build_workflow_services(
    audit_logger: AuditLogger,
    gateway: ActionGateway,
    adapter_router: Any = None,
    adapter_registry: Any = None,
) -> dict[str, Any]:
    """
    Build and return the full workflow service stack (Sprint 2.26).

    Returns a dict keyed by ProductionRuntime field names. Any service that fails
    to build is set to None — the system falls back to no-service mode gracefully
    (each WorkflowEngine step has a fallback path when a service is None).

    Dependency order:
      1. ClarificationService (no dependencies)
      2. InvestigationService (requires ToolExecutor)
      3. KnowledgeService (no dependencies)
      4. ReasoningService (no dependencies)
      5. ActionProposalService (no dependencies)
      6. ActionGatewayService (no dependencies)
      7. ExecutionService (no dependencies)
      8. WorkflowEngine ← all 7 above
      9. PlaybookRegistry (no dependencies)
     10. CaseService ← WorkflowEngine, PlaybookRegistry, gateway, audit_logger
    """
    import logging
    _LOG = logging.getLogger(__name__)

    result: dict[str, Any] = {
        "clarification_service":         None,
        "investigation_service":         None,
        "knowledge_service":             None,
        "reasoning_service":             None,
        "action_proposal_service":       None,
        "action_gateway_service":        None,
        "execution_service":             None,
        "workflow_engine":               None,
        "playbook_registry":             None,
        "case_service":                  None,
        "adapter_registry":              adapter_registry,
        "adapter_router":                adapter_router,
        # Sprint 2.27.5
        "router_service":                None,
        "knowledge_orchestrator":        None,
        "response_generation_service":   None,
        "engineering_escalation_service": None,
        "support_agent_runtime":         None,
        "ticket_orchestrator":           None,
        # Sprint 2.27.9: Multi-Tenant Resolution
        "tenant_registry":               None,
        "client_resolver":               None,
        "tenant_tool_registry":          None,
    }

    # 1. ClarificationService
    try:
        from case_engine.clarification.service import ClarificationService  # noqa: PLC0415
        result["clarification_service"] = ClarificationService(audit_logger=audit_logger)
        _LOG.info("assembly: clarification_service wired")
    except Exception as exc:
        _LOG.warning("assembly: clarification_service failed to build error=%s", exc)

    # 2. InvestigationService (via build factory which handles ToolRegistry + Executor)
    try:
        from case_engine.investigation import build_investigation_service  # noqa: PLC0415
        from case_engine.tools.tool_registry import ToolRegistry          # noqa: PLC0415
        from case_engine.tools.tool_executor import ToolExecutor          # noqa: PLC0415
        _tool_registry = ToolRegistry.build_default()
        _tool_executor = ToolExecutor(_tool_registry)
        result["investigation_service"] = build_investigation_service(
            tool_executor=_tool_executor,
            audit_logger=audit_logger,
        )
        _LOG.info("assembly: investigation_service wired tools=%d", len(_tool_registry))
    except Exception as exc:
        _LOG.warning("assembly: investigation_service failed to build error=%s", exc)

    # 3. KnowledgeService
    try:
        from case_engine.knowledge import build_knowledge_service  # noqa: PLC0415
        result["knowledge_service"] = build_knowledge_service(audit_logger=audit_logger)
        _LOG.info("assembly: knowledge_service wired")
    except Exception as exc:
        _LOG.warning("assembly: knowledge_service failed to build error=%s", exc)

    # 4. ReasoningService
    try:
        from case_engine.reasoning.service import build_reasoning_service  # noqa: PLC0415
        result["reasoning_service"] = build_reasoning_service(audit_logger=audit_logger)
        _LOG.info("assembly: reasoning_service wired")
    except Exception as exc:
        _LOG.warning("assembly: reasoning_service failed to build error=%s", exc)

    # 5. ActionProposalService
    try:
        from case_engine.actions import build_action_proposal_service  # noqa: PLC0415
        result["action_proposal_service"] = build_action_proposal_service(
            audit_logger=audit_logger
        )
        _LOG.info("assembly: action_proposal_service wired")
    except Exception as exc:
        _LOG.warning("assembly: action_proposal_service failed to build error=%s", exc)

    # 6. ActionGatewayService
    try:
        from case_engine.action_gateway.service import build_action_gateway_service  # noqa: PLC0415
        result["action_gateway_service"] = build_action_gateway_service(
            audit_logger=audit_logger
        )
        _LOG.info("assembly: action_gateway_service wired")
    except Exception as exc:
        _LOG.warning("assembly: action_gateway_service failed to build error=%s", exc)

    # 7. ExecutionService — backed by AdapterRouter if available
    try:
        from case_engine.execution.service import build_execution_service  # noqa: PLC0415
        result["execution_service"] = build_execution_service(
            adapter_router=adapter_router,
        )
        _LOG.info(
            "assembly: execution_service wired adapter_router=%s",
            adapter_router is not None,
        )
    except Exception as exc:
        _LOG.warning("assembly: execution_service failed to build error=%s", exc)

    # 8. WorkflowEngine — inject all 7 services
    try:
        from case_engine.workflows.workflow_engine import WorkflowEngine  # noqa: PLC0415
        result["workflow_engine"] = WorkflowEngine(
            investigation_service=result["investigation_service"],
            knowledge_service=result["knowledge_service"],
            reasoning_service=result["reasoning_service"],
            action_proposal_service=result["action_proposal_service"],
            action_gateway_service=result["action_gateway_service"],
            execution_service=result["execution_service"],
            clarification_service=result["clarification_service"],
        )
        wired = [
            k for k in (
                "investigation_service", "knowledge_service", "reasoning_service",
                "action_proposal_service", "action_gateway_service",
                "execution_service", "clarification_service",
            )
            if result[k] is not None
        ]
        _LOG.info("assembly: workflow_engine wired services=%s", wired)
    except Exception as exc:
        _LOG.warning("assembly: workflow_engine failed to build error=%s", exc)

    # 9. PlaybookRegistry
    try:
        from case_engine.workflows.playbook_registry import PlaybookRegistry  # noqa: PLC0415
        result["playbook_registry"] = PlaybookRegistry.build()
        _LOG.info("assembly: playbook_registry wired playbooks=%d", len(result["playbook_registry"]))
    except Exception as exc:
        _LOG.warning("assembly: playbook_registry failed to build error=%s", exc)

    # 10. CaseService — fully wired with WorkflowEngine + PlaybookRegistry + gateway
    try:
        from case_engine.service import build_case_service  # noqa: PLC0415
        if result["workflow_engine"] is not None or result["playbook_registry"] is not None:
            from case_engine.repository import CaseRepository     # noqa: PLC0415
            from case_engine.workflows.workflow_engine import WorkflowEngine  # noqa: PLC0415
            repo    = CaseRepository(supabase_client=None)
            wf_eng  = result["workflow_engine"] or WorkflowEngine()
            cs = build_case_service(
                supabase_client=None,
                playbook_registry=result["playbook_registry"],
                action_gateway=gateway,
            )
            cs._wf_engine = wf_eng
            cs._audit     = audit_logger
            result["case_service"] = cs
            _LOG.info("assembly: case_service wired workflow_engine=%s", result["workflow_engine"] is not None)
    except Exception as exc:
        _LOG.warning("assembly: case_service failed to build error=%s", exc)

    # ── Sprint 2.27.5: Architecture Convergence Services ─────────────────────

    # 11. RouterService — universal action dispatch over AdapterRouter
    try:
        from case_engine.integrations import build_router_service  # noqa: PLC0415
        if adapter_router is not None:
            result["router_service"] = build_router_service(
                adapter_router=adapter_router,
                audit_logger=audit_logger,
            )
            _LOG.info("assembly: router_service wired")
    except Exception as exc:
        _LOG.warning("assembly: router_service failed to build error=%s", exc)

    # 12. KnowledgeOrchestrator — converges KnowledgeService + HybridRAG (placeholder)
    try:
        from case_engine.knowledge import build_knowledge_orchestrator  # noqa: PLC0415
        result["knowledge_orchestrator"] = build_knowledge_orchestrator(
            knowledge_service=result["knowledge_service"],
            audit_logger=audit_logger,
        )
        _LOG.info("assembly: knowledge_orchestrator wired")
    except Exception as exc:
        _LOG.warning("assembly: knowledge_orchestrator failed to build error=%s", exc)

    # 13. ResponseGenerationService — USERRESPONSE node
    try:
        from case_engine.response_generation import build_response_generation_service  # noqa: PLC0415
        result["response_generation_service"] = build_response_generation_service(
            audit_logger=audit_logger,
        )
        _LOG.info("assembly: response_generation_service wired")
    except Exception as exc:
        _LOG.warning("assembly: response_generation_service failed to build error=%s", exc)

    # 14. EngineeringEscalationService — ASANACREATE node (mock in 2.27.5)
    try:
        from case_engine.engineering import build_engineering_escalation_service  # noqa: PLC0415
        result["engineering_escalation_service"] = build_engineering_escalation_service(
            audit_logger=audit_logger,
        )
        _LOG.info("assembly: engineering_escalation_service wired")
    except Exception as exc:
        _LOG.warning("assembly: engineering_escalation_service failed to build error=%s", exc)

    # 15. SupportAgentRuntime — THE AGENT (single run_case() entrypoint)
    try:
        from case_engine.runtime import build_support_agent_runtime  # noqa: PLC0415
        result["support_agent_runtime"] = build_support_agent_runtime(
            case_service=result["case_service"],
            response_generation_service=result["response_generation_service"],
            engineering_escalation_service=result["engineering_escalation_service"],
            audit_logger=audit_logger,
        )
        _LOG.info("assembly: support_agent_runtime wired")
    except Exception as exc:
        _LOG.warning("assembly: support_agent_runtime failed to build error=%s", exc)

    # ── Sprint 2.27.9: Multi-Tenant Resolution Stack ──────────────────────────

    # 17. TenantRegistry
    try:
        from case_engine.tenant.registry import build_default_tenant_registry  # noqa: PLC0415
        result["tenant_registry"] = build_default_tenant_registry()
        _LOG.info(
            "assembly: tenant_registry wired tenants=%d",
            result["tenant_registry"].count(),
        )
    except Exception as exc:
        _LOG.warning("assembly: tenant_registry failed to build error=%s", exc)

    # 18. ClientResolver — depends on TenantRegistry
    try:
        from case_engine.tenant.resolver import ClientResolver  # noqa: PLC0415
        if result["tenant_registry"] is not None:
            result["client_resolver"] = ClientResolver(result["tenant_registry"])
            _LOG.info("assembly: client_resolver wired")
    except Exception as exc:
        _LOG.warning("assembly: client_resolver failed to build error=%s", exc)

    # 19. TenantAwareToolRegistry — depends on TenantRegistry
    try:
        from case_engine.tenant.tool_registry import TenantAwareToolRegistry  # noqa: PLC0415
        if result["tenant_registry"] is not None:
            result["tenant_tool_registry"] = TenantAwareToolRegistry(result["tenant_registry"])
            _LOG.info("assembly: tenant_tool_registry wired")
    except Exception as exc:
        _LOG.warning("assembly: tenant_tool_registry failed to build error=%s", exc)

    # 16. TicketOrchestrator — ticket lifecycle layer (with client_resolver)
    try:
        from case_engine.ticket_orchestration import build_ticket_orchestrator  # noqa: PLC0415
        result["ticket_orchestrator"] = build_ticket_orchestrator(
            agent_runtime=result["support_agent_runtime"],
            case_service=result["case_service"],
            audit_logger=audit_logger,
            client_resolver=result["client_resolver"],
        )
        _LOG.info(
            "assembly: ticket_orchestrator wired client_resolver=%s",
            result["client_resolver"] is not None,
        )
    except Exception as exc:
        _LOG.warning("assembly: ticket_orchestrator failed to build error=%s", exc)

    return result
