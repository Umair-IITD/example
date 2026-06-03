"""
runtime/health.py

Sprint 2.6: Service-layer health reporting.

No HTTP. No FastAPI. No transport logic.
Sprint 2.7 builds an HTTP endpoint on top of HealthService.

Design:
  HealthService aggregates health from all subsystems and returns a single
  ServiceHealth snapshot. Never raises — all exceptions from subsystem
  checks are caught and represented as unhealthy components.

  Consumers:
    - Health endpoint handler (Sprint 2.7)
    - Alerting / monitoring (Sprint 2.X)
    - Startup readiness gate (current sprint)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from case_engine.executor_registry import ActionExecutorRegistry
from case_engine.provider_registry import ProviderRegistry

if TYPE_CHECKING:
    from case_engine.action_runtime import ActionRuntime
    from worker.action_worker import ActionWorker


@dataclass(frozen=True)
class ServiceHealth:
    """
    Snapshot of service health at a point in time.

    is_healthy: overall health — True only when all subsystems healthy.
    provider_statuses: per-provider health map (empty dict = no providers).
    executor_count: number of registered executor types.
    executor_keys: sorted (namespace, action_type) pairs.
    runtime_ready: True when the runtime object is wired.
    worker_available: True when at least one worker is configured.
    checked_at: wall-clock time of this snapshot.
    """
    is_healthy: bool
    provider_statuses: dict[str, bool]
    executor_count: int
    executor_keys: list[tuple[str, str]]
    runtime_ready: bool
    worker_available: bool
    checked_at: datetime


class HealthService:
    """
    Aggregates health across providers, executors, runtime, and worker.

    Never raises. All per-subsystem exceptions are caught and represented
    as is_healthy=False in the relevant field.
    """

    def __init__(
        self,
        provider_registry: ProviderRegistry,
        executor_registry: ActionExecutorRegistry,
        runtime: "ActionRuntime",
        worker: "ActionWorker | None" = None,
    ) -> None:
        self._provider_registry = provider_registry
        self._executor_registry = executor_registry
        self._runtime = runtime
        self._worker = worker

    def check(self) -> ServiceHealth:
        """
        Run all subsystem health checks and return a ServiceHealth snapshot.

        Provider health is determined by ProviderRegistry.health_check_all().
        Executor health is the count — executors have no runtime health state.
        Runtime health is whether the runtime object is non-None.
        Worker health is whether a worker is configured.
        """
        provider_statuses = self._check_providers()
        executor_count = self._safe_executor_count()
        executor_keys = self._safe_executor_keys()
        runtime_ready = self._runtime is not None
        worker_available = self._worker is not None

        # Overall health: all registered providers healthy + executors present + runtime wired
        all_providers_healthy = all(provider_statuses.values()) if provider_statuses else True
        is_healthy = all_providers_healthy and executor_count > 0 and runtime_ready

        return ServiceHealth(
            is_healthy=is_healthy,
            provider_statuses=provider_statuses,
            executor_count=executor_count,
            executor_keys=executor_keys,
            runtime_ready=runtime_ready,
            worker_available=worker_available,
            checked_at=datetime.now(tz=timezone.utc),
        )

    def _check_providers(self) -> dict[str, bool]:
        try:
            results = self._provider_registry.health_check_all()
            return {name: h.is_healthy for name, h in results.items()}
        except Exception:
            return {}

    def _safe_executor_count(self) -> int:
        try:
            return self._executor_registry.registered_count()
        except Exception:
            return 0

    def _safe_executor_keys(self) -> list[tuple[str, str]]:
        try:
            return self._executor_registry.registered_keys()
        except Exception:
            return []
