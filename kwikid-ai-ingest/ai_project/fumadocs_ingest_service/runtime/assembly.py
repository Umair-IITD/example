"""
runtime/assembly.py

Sprint 2.6: Production runtime assembly.
Sprint 2.10: Added MetricsService and audit factory wiring.

build_production_runtime() is the single composition point.
Application startup calls it once and receives a fully-wired ProductionRuntime.

Key design constraint: ALL components share a single ActionRepository instance.
This prevents split-brain between the gateway (which records state transitions)
and the runtime/worker (which reads state). The factory guarantees this invariant.
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

    Access pattern:
        stack = build_production_runtime(...)
        rollbacks, forwards = stack.worker.process(client="unity_bank")
        result = stack.watchdog.run(client="unity_bank")
    """
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

    return ProductionRuntime(
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
    )


def _register_all_executors(
    registry: ActionExecutorRegistry,
    router: ProviderRouter,
) -> None:
    """Register all Sprint 2.5 production executors. Fails fast on duplicate."""
    registry.register_executor(AddTicketNoteExecutor(router))
    registry.register_executor(UpdateTicketStatusExecutor(router))
    registry.register_executor(IdentityResetOtpExecutor(router))
