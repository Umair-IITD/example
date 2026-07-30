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
    # ── Enterprise Intelligence Layer (Sprint 2.53 Wave 4A) ──────────────────
    intelligence_orchestrator:       Any = None   # intelligence.IntelligenceOrchestrator
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

    # Workflow/case-engine services require case_engine.audit.AuditLogger (has
    # log_investigation_started, log_workflow_*, etc.).  The outer `audit_logger`
    # is audit.logger.AuditLogger (action-gateway layer — different class).
    try:
        from case_engine.audit import AuditLogger as _CeAuditLogger  # noqa: PLC0415
        _ce_audit: Any = _CeAuditLogger()  # supabase_client=None → log-only mode
    except Exception as _ce_exc:
        _LOG.warning("assembly: case_engine.audit.AuditLogger failed error=%s — using outer logger", _ce_exc)
        _ce_audit = audit_logger  # fallback: outer logger (some methods may be missing)

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
        # Sprint 2.53 Wave 4A — Enterprise Intelligence Layer
        "intelligence_orchestrator":     None,
        # Sprint 2.27.9: Multi-Tenant Resolution
        "tenant_registry":               None,
        "client_resolver":               None,
        "tenant_tool_registry":          None,
    }

    # 1. ClarificationService
    try:
        from case_engine.clarification.service import ClarificationService  # noqa: PLC0415
        result["clarification_service"] = ClarificationService(audit_logger=_ce_audit)
        _LOG.info("assembly: clarification_service wired")
    except Exception as exc:
        _LOG.warning("assembly: clarification_service failed to build error=%s", exc)

    # 2. InvestigationService — production tool registry (Unity + Metrics)
    try:
        from case_engine.investigation import build_investigation_service  # noqa: PLC0415
        from case_engine.tools.tool_registry import ToolRegistry          # noqa: PLC0415
        from case_engine.tools.tool_executor import ToolExecutor          # noqa: PLC0415
        _tool_registry = ToolRegistry()  # start empty; register production tools below

        # Register production Unity tools (replace Sprint 2.17 mocks).
        # Gracefully falls back to mock tools if Unity credentials are unavailable.
        try:
            from case_engine.tools.adapters.unity_tools import register_unity_tools  # noqa: PLC0415
            _unity_outcomes = register_unity_tools(_tool_registry, config=None)
            _LOG.info("assembly: unity_tools registered outcomes=%s", _unity_outcomes)
        except Exception as _unity_exc:
            _LOG.warning(
                "assembly: unity_tools failed to register error=%s — using mock tools",
                _unity_exc,
            )
            from case_engine.tools.mock_tools import (  # noqa: PLC0415
                GetCaseHistoryTool, GetFailureReasonTool, GetOnboardingStatusTool,
                GetSessionDetailsTool, GetUserDetailsTool,
            )
            for _mt in (
                GetSessionDetailsTool(), GetUserDetailsTool(), GetFailureReasonTool(),
                GetCaseHistoryTool(), GetOnboardingStatusTool(),
            ):
                _tool_registry.register(_mt)

        # Register production Metrics tools (MetricTool + ServerTool).
        try:
            from case_engine.tools.adapters.metrics_tool import register_metrics_tools  # noqa: PLC0415
            _metrics_outcomes = register_metrics_tools(_tool_registry, config=None)
            _LOG.info("assembly: metrics_tools registered outcomes=%s", _metrics_outcomes)
        except Exception as _metrics_exc:
            _LOG.warning(
                "assembly: metrics_tools failed to register error=%s",
                _metrics_exc,
            )

        # Register production Loki tools (Grafana Loki backend log retrieval).
        try:
            from case_engine.tools.adapters.log_tools import register_loki_tools  # noqa: PLC0415
            _loki_outcomes = register_loki_tools(_tool_registry, config=None)
            _LOG.info("assembly: loki_tools registered outcomes=%s", _loki_outcomes)
        except Exception as _loki_exc:
            _LOG.warning(
                "assembly: loki_tools failed to register error=%s",
                _loki_exc,
            )

        _tool_executor = ToolExecutor(_tool_registry)
        result["investigation_service"] = build_investigation_service(
            tool_executor=_tool_executor,
            audit_logger=_ce_audit,
        )
        _LOG.info("assembly: investigation_service wired tools=%d", len(_tool_registry))
    except Exception as exc:
        _LOG.warning("assembly: investigation_service failed to build error=%s", exc)

    # 3. KnowledgeService — seeded with built-in SOP entries at startup
    try:
        from case_engine.knowledge import build_knowledge_service  # noqa: PLC0415
        from case_engine.knowledge.models import (  # noqa: PLC0415
            KnowledgeEntry,
            KnowledgeEntryStatus,
            KnowledgeEntryType,
        )
        _seed_entries = _build_seed_knowledge_entries()
        result["knowledge_service"] = build_knowledge_service(
            seed_entries=_seed_entries,
            audit_logger=_ce_audit,
        )
        _LOG.info(
            "assembly: knowledge_service wired seed_entries=%d",
            len(_seed_entries),
        )
    except Exception as exc:
        _LOG.warning("assembly: knowledge_service failed to build error=%s", exc)

    # 4. ReasoningService
    try:
        from case_engine.reasoning.service import build_reasoning_service  # noqa: PLC0415
        result["reasoning_service"] = build_reasoning_service(audit_logger=_ce_audit)
        _LOG.info("assembly: reasoning_service wired")
    except Exception as exc:
        _LOG.warning("assembly: reasoning_service failed to build error=%s", exc)

    # 5. ActionProposalService
    try:
        from case_engine.actions import build_action_proposal_service  # noqa: PLC0415
        result["action_proposal_service"] = build_action_proposal_service(
            audit_logger=_ce_audit
        )
        _LOG.info("assembly: action_proposal_service wired")
    except Exception as exc:
        _LOG.warning("assembly: action_proposal_service failed to build error=%s", exc)

    # 6. ActionGatewayService
    try:
        from case_engine.action_gateway.service import build_action_gateway_service  # noqa: PLC0415
        result["action_gateway_service"] = build_action_gateway_service(
            audit_logger=_ce_audit
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
            # DO NOT overwrite cs._audit here.
            # build_case_service() creates case_engine.audit.AuditLogger (which has
            # log_transition() and log_classification()). The audit_logger variable
            # here is audit.logger.AuditLogger (Action Gateway logger), which has
            # only emit() — wrong interface for CaseService.
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

    # 14. EngineeringEscalationService — ASANACREATE node
    # Sprint 2.5.8: inject live AsanaClient when credentials are configured.
    # Falls back to mock/in-memory mode (asana_client=None) when credentials
    # are absent, matching DRY_RUN behaviour.
    try:
        from case_engine.engineering import build_engineering_escalation_service  # noqa: PLC0415
        from asana.client import build_asana_client                              # noqa: PLC0415
        _asana_client = build_asana_client()
        result["engineering_escalation_service"] = build_engineering_escalation_service(
            audit_logger=audit_logger,
            asana_client=_asana_client,
        )
        _LOG.info(
            "assembly: engineering_escalation_service wired asana_live=%s",
            _asana_client is not None,
        )
    except Exception as exc:
        _LOG.warning("assembly: engineering_escalation_service failed to build error=%s", exc)

    # 14.5. IntelligenceOrchestrator (Sprint 2.53 Wave 4A) — LLM Reasoning Layer.
    # Built BEFORE SupportAgentRuntime so it can be injected. Graceful
    # degradation: when INTELLIGENCE_ENABLED=false OR the config raises,
    # the runtime falls back to deterministic templating.
    try:
        from intelligence import IntelligenceConfig, IntelligenceOrchestrator  # noqa: PLC0415
        _intel_cfg = IntelligenceConfig.from_env()
        if _intel_cfg.enabled:
            result["intelligence_orchestrator"] = IntelligenceOrchestrator(config=_intel_cfg)
            _LOG.info(
                "assembly: intelligence_orchestrator wired provider=%s model=%s has_api_key=%s",
                _intel_cfg.llm_provider, _intel_cfg.llm_model, _intel_cfg.has_api_key,
            )
        else:
            _LOG.info(
                "assembly: intelligence_orchestrator DISABLED via INTELLIGENCE_ENABLED=false"
            )
    except Exception as exc:
        _LOG.warning("assembly: intelligence_orchestrator failed to build error=%s", exc)

    # 15. SupportAgentRuntime — THE AGENT (single run_case() entrypoint)
    try:
        from case_engine.runtime import build_support_agent_runtime  # noqa: PLC0415
        result["support_agent_runtime"] = build_support_agent_runtime(
            case_service=result["case_service"],
            response_generation_service=result["response_generation_service"],
            engineering_escalation_service=result["engineering_escalation_service"],
            audit_logger=audit_logger,
            intelligence_orchestrator=result["intelligence_orchestrator"],
        )
        _LOG.info(
            "assembly: support_agent_runtime wired intelligence_wired=%s",
            result["intelligence_orchestrator"] is not None,
        )
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


def _build_seed_knowledge_entries() -> list:
    """
    Build the startup KnowledgeEntry seed list from the approved support SOP playbook.

    Source: SUPPORT_OPERATIONS_BLUEPRINT.md (Section 31, Knowledge System) and the
    5 known KwikID support topics described there and in the flow_diagram.mermaid.

    This function is deterministic — it always produces the same entries.
    Adding a real StackOverflow Teams JSON export via StackOverflowImporter in the
    future is purely additive: pass its output alongside these entries.

    Returns a list[KnowledgeEntry] ready for build_knowledge_service(seed_entries=...).
    """
    from case_engine.knowledge.models import (  # noqa: PLC0415
        KnowledgeEntry,
        KnowledgeEntryStatus,
        KnowledgeEntryType,
    )

    CREATED_AT = "2024-01-01T00:00:00Z"

    entries = [
        # ── 1. VKYC Session Failure ───────────────────────────────────────────
        KnowledgeEntry(
            entry_id="seed-vkyc-expired-session",
            title="VKYC Session Reset — Expired or Timed-Out Session",
            body=(
                "A Video KYC session has expired or timed out before the user "
                "could complete identification. "
                "The session logs show EXPIRED_SESSION or session_timeout errors. "
                "Resolution: reset the session from the Admin Portal so the user "
                "can restart their VKYC journey without repeating completed steps."
            ),
            entry_type=KnowledgeEntryType.SOP,
            tags=("vkyc", "session_reset", "expired_session", "session_timeout", "kyc"),
            topic_keys=("VKYC_Session_Failure",),
            root_cause_categories=("EXPIRED_SESSION", "TIMEOUT"),
            recommended_actions=("SESSION_RESET",),
            resolution_steps=(
                "1. Navigate to the Admin Portal and locate the session by Session ID or URN.",
                "2. Verify the session status shows EXPIRED or TIMED_OUT.",
                "3. Click 'Reset Session' to restore the session to an in-progress state.",
                "4. Confirm the reset succeeded — session status should change to ACTIVE.",
                "5. Notify the user that their session has been reset and they may retry.",
                "6. Add an internal note to the Freshdesk ticket with Session ID and outcome.",
            ),
            source="manual",
            source_id="sop-vkyc-001",
            accepted_answer=True,
            score=20,
            created_at=CREATED_AT,
            status=KnowledgeEntryStatus.ACTIVE,
        ),
        KnowledgeEntry(
            entry_id="seed-vkyc-liveness-failure",
            title="VKYC Session Failure — Liveness Check Rejected",
            body=(
                "The KYC session failed because the liveness detection algorithm rejected "
                "the user's video. This can occur due to poor camera quality, low lighting, "
                "background motion, or the user not following liveness instructions. "
                "If liveness_failure or LIVENESS_FAILURE appears in session logs, "
                "advise the user to retry in better lighting and escalate if repeated failures occur."
            ),
            entry_type=KnowledgeEntryType.KNOWN_ISSUE,
            tags=("vkyc", "liveness", "liveness_failure", "kyc", "manual_review"),
            topic_keys=("VKYC_Session_Failure",),
            root_cause_categories=("LIVENESS_FAILURE",),
            recommended_actions=("SESSION_RESET", "MANUAL_REVIEW"),
            resolution_steps=(
                "1. Open the session summary and confirm the VKYC_OUTCOME is REJECTED.",
                "2. Check the session logs for LIVENESS_FAILURE entries.",
                "3. Review the video recording for environmental issues (lighting, motion, audio).",
                "4. If user error: reset session and advise user on retry conditions.",
                "5. If technical liveness engine issue: escalate to engineering with session ID and video evidence.",
            ),
            source="manual",
            source_id="sop-vkyc-002",
            accepted_answer=True,
            score=15,
            created_at=CREATED_AT,
            status=KnowledgeEntryStatus.ACTIVE,
        ),

        # ── 2. OTP Delivery Failure ───────────────────────────────────────────
        KnowledgeEntry(
            entry_id="seed-otp-sms-delivery-failure",
            title="OTP Delivery Failure — SMS Not Received by User",
            body=(
                "The user reports not receiving the OTP SMS. "
                "Logs show GENERATE_OTP succeeded but SEND_SMS failed or returned a "
                "provider timeout. This is an SMS_DELIVERY_FAILURE root cause. "
                "Resolution: resend the OTP. If repeated failures occur for the same number, "
                "check carrier blacklists or switch to email OTP delivery."
            ),
            entry_type=KnowledgeEntryType.SOP,
            tags=("otp", "sms", "sms_failure", "sms_delivery", "otp_resend"),
            topic_keys=("OTP_Delivery_Failure",),
            root_cause_categories=("SMS_DELIVERY_FAILURE",),
            recommended_actions=("OTP_RESEND",),
            resolution_steps=(
                "1. Locate the user by URN and confirm the phone number on record.",
                "2. Check the SEND_SMS log entry — confirm OTP was generated but SMS dispatch failed.",
                "3. Verify the phone number format (country code, no spaces, valid digits).",
                "4. Trigger OTP Resend via the Admin Portal or Identity Reset API.",
                "5. Confirm the user receives the new OTP.",
                "6. If resend still fails, escalate to SMS provider or switch delivery channel.",
                "7. Add internal Freshdesk note documenting the failure and resend outcome.",
            ),
            source="manual",
            source_id="sop-otp-001",
            accepted_answer=True,
            score=18,
            created_at=CREATED_AT,
            status=KnowledgeEntryStatus.ACTIVE,
        ),
        KnowledgeEntry(
            entry_id="seed-otp-quota-exceeded",
            title="OTP Delivery Failure — Quota Exceeded or Rate Limited",
            body=(
                "OTP delivery is failing for multiple users simultaneously. "
                "Logs show QUOTA_EXCEEDED or provider rate-limit responses on SEND_SMS. "
                "This is a platform-wide SMS quota issue rather than a single-user failure. "
                "Escalate to engineering immediately and pause OTP-dependent onboarding workflows."
            ),
            entry_type=KnowledgeEntryType.RUNBOOK,
            tags=("otp", "sms", "quota_exceeded", "escalate"),
            topic_keys=("OTP_Delivery_Failure",),
            root_cause_categories=("QUOTA_EXCEEDED",),
            recommended_actions=("ESCALATE",),
            resolution_steps=(
                "1. Confirm quota_exceeded in SEND_SMS logs for multiple sessions.",
                "2. Do NOT attempt individual OTP resends — they will also fail.",
                "3. Escalate immediately to engineering with log timestamps and affected user count.",
                "4. Engineering contacts SMS provider to reset quota or failover to backup provider.",
                "5. Once resolved, bulk-resend OTPs for affected users.",
                "6. Update Freshdesk tickets for all affected users with resolution note.",
            ),
            source="manual",
            source_id="sop-otp-002",
            accepted_answer=True,
            score=12,
            created_at=CREATED_AT,
            status=KnowledgeEntryStatus.ACTIVE,
        ),

        # ── 3. Document OCR Failure ───────────────────────────────────────────
        KnowledgeEntry(
            entry_id="seed-ocr-document-failure",
            title="Document OCR Failure — PAN or Aadhaar Extraction Failed",
            body=(
                "The document OCR pipeline failed to extract identity data from a PAN card "
                "or Aadhaar. Logs show PAN_VALIDATION or AADHAAR_VALIDATION failure events. "
                "Root cause is typically DOCUMENT_FAILURE due to image quality, glare, or "
                "damaged documents. Resolution: ask user to re-upload a clearer photo or "
                "trigger an OCR retry if the system supports it."
            ),
            entry_type=KnowledgeEntryType.SOP,
            tags=("ocr", "document", "pan", "aadhaar", "document_ocr", "document_failure"),
            topic_keys=("Document_OCR_Failure",),
            root_cause_categories=("DOCUMENT_FAILURE",),
            recommended_actions=("RETRY", "MANUAL_REVIEW"),
            resolution_steps=(
                "1. Identify the failed document type from logs (PAN_VALIDATION vs AADHAAR_VALIDATION).",
                "2. Check the DMS_OPERATION_LOG for the uploaded image — confirm it was received.",
                "3. Review OCR confidence score in the session summary.",
                "4. If confidence < threshold: ask user to re-upload a clearer, unobstructed photo.",
                "5. If document is valid but OCR repeatedly fails: trigger manual document review.",
                "6. Record the session ID and document type in the Freshdesk internal note.",
            ),
            source="manual",
            source_id="sop-ocr-001",
            accepted_answer=True,
            score=16,
            created_at=CREATED_AT,
            status=KnowledgeEntryStatus.ACTIVE,
        ),

        # ── 4. Agent Portal Issue ─────────────────────────────────────────────
        KnowledgeEntry(
            entry_id="seed-portal-unavailable",
            title="Agent Portal Issue — Portal Unavailable or Unresponsive",
            body=(
                "The Support Admin Portal is returning errors or is inaccessible to agents. "
                "This blocks all investigation steps that require portal access. "
                "Root cause is PORTAL_UNAVAILABLE — typically a backend service outage or "
                "maintenance window. Agents should refresh the portal; if the issue persists, "
                "escalate to the portal infrastructure team."
            ),
            entry_type=KnowledgeEntryType.RUNBOOK,
            tags=("portal", "agent_portal", "portal_unavailable", "portal_refresh", "portal_down"),
            topic_keys=("Agent_Portal_Issue",),
            root_cause_categories=("PORTAL_UNAVAILABLE",),
            recommended_actions=("PORTAL_REFRESH", "ESCALATE"),
            resolution_steps=(
                "1. Confirm the portal issue by attempting to load the portal in a new browser tab.",
                "2. Check the portal status page or internal status channel for outage notices.",
                "3. If transient: wait 2 minutes and refresh — most portal blips self-resolve.",
                "4. If persistent (>5 minutes): escalate to portal infrastructure team with timestamp.",
                "5. Queue any active investigations — do not attempt manual workarounds that bypass the portal.",
                "6. Once portal is restored, continue queued investigations and update affected tickets.",
            ),
            source="manual",
            source_id="sop-portal-001",
            accepted_answer=True,
            score=14,
            created_at=CREATED_AT,
            status=KnowledgeEntryStatus.ACTIVE,
        ),

        # ── 5. API Callback Failure ───────────────────────────────────────────
        KnowledgeEntry(
            entry_id="seed-api-callback-failure",
            title="API Callback Failure — Webhook Not Delivered to Client System",
            body=(
                "A KYC completion or status callback was not delivered to the client's system. "
                "CALLBACK_EVENTS logs show the callback was dispatched but no acknowledgement "
                "was received, or the endpoint returned an error. "
                "Root cause is CALLBACK_FAILURE. Resolution: retry the callback or investigate "
                "the client's endpoint availability."
            ),
            entry_type=KnowledgeEntryType.SOP,
            tags=("callback", "api", "webhook", "callback_failure", "callback_retry", "api_failure"),
            topic_keys=("API_Callback_Failure",),
            root_cause_categories=("CALLBACK_FAILURE",),
            recommended_actions=("CALLBACK_RETRY", "ESCALATE"),
            resolution_steps=(
                "1. Locate the CALLBACK_EVENTS log for the affected session — note the endpoint URL and HTTP status.",
                "2. Confirm whether the client endpoint was unreachable (timeout) or returned an error (4xx/5xx).",
                "3. If client endpoint was temporarily unreachable: trigger callback retry via Admin Portal.",
                "4. If client endpoint returned a 4xx error: notify the client's integration team.",
                "5. If callback delivery has been failing for >1 hour: escalate to engineering.",
                "6. Document the callback URL, failure status, and retry outcome in the Freshdesk note.",
            ),
            source="manual",
            source_id="sop-callback-001",
            accepted_answer=True,
            score=17,
            created_at=CREATED_AT,
            status=KnowledgeEntryStatus.ACTIVE,
        ),
    ]

    return entries
