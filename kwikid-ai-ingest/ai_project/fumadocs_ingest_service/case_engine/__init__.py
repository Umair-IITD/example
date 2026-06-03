"""
case_engine — Phase 2 Case Foundation Layer (Sprint 1 + Sprint 2.1 + Sprint 2.2 + Sprint 2.3)

Public API:
    CaseState         — state enum
    Case              — in-memory case record
    ClassificationResult — classifier output
    EscalationDecision  — escalation engine output
    TransferContextPayload — human handoff payload
    CaseService       — high-level orchestrator
    build_case_service — factory

Sprint 2.1 — Action Gateway:
    ActionState, ActionRiskLevel — action state machine enums
    ActionRequest, ActionTransitionRecord, ActionProposal — domain models
    ActionGateway, build_action_gateway — gateway service + factory
    DuplicateActionError, ActionGatewayError, ActionTransitionError — exceptions

Sprint 2.2 — Execution Runtime:
    ActionExecutionError, RetryableExecutionError, PermanentExecutionError, RollbackError
    ExecutionContext, ExecutionResult — per-attempt metadata and outcome
    ActionExecutor — ABC all provider executors must implement
    ActionExecutorRegistry, ExecutorRegistrationError, UnknownExecutorError — executor routing
    ActionRuntime, build_action_runtime, EXECUTION_TIMEOUT_DEFAULT — orchestrator + factory

Sprint 2.3 — Provider Abstraction Layer:
    ProviderCapability — capability enum (EXECUTE, ROLLBACK, IDEMPOTENT, HEALTH_CHECK)
    ProviderRequest, ProviderResponse, ProviderHealth, ProviderMetadata — provider models
    ProviderError — base exception; ProviderTransientError / ProviderPermanentError branches
    Provider — ABC all provider implementations must satisfy
    ProviderRegistry, ProviderRegistrationError, UnknownProviderError — provider lookup table
    ProviderRouter — executor → provider dispatch
"""
from case_engine.case_state import CaseState, ALLOWED_TRANSITIONS, TERMINAL_STATES
from case_engine.models import (
    Case,
    CaseTransition,
    AuditEntry,
    AuditEventType,
    ClassificationResult,
    MatchType,
    TopicKey,
)
from case_engine.state_machine import CaseStateMachine, TransitionError
from case_engine.audit import AuditLogger
from case_engine.transfer_context import TransferContextPayload, AttemptedRemediation
from case_engine.escalation import EscalationDecision, EscalationEngine, EscalationTrigger, EscalationPriority
from case_engine.classifier import TopicClassifier
from case_engine.repository import CaseRepository
from case_engine.service import CaseService, build_case_service

# Sprint 2.1: Action Gateway
from case_engine.action_state import ActionState, ActionRiskLevel, ActionTransitionError
from case_engine.action_models import ActionRequest, ActionTransitionRecord, ActionProposal
from case_engine.action_repository import ActionRepository
from case_engine.action_gateway import (
    ActionGateway,
    ActionGatewayError,
    DuplicateActionError,
    build_action_gateway,
)

# Sprint 2.2: Execution Runtime
from case_engine.action_executor import (
    ActionExecutionError,
    RetryableExecutionError,
    PermanentExecutionError,
    RollbackError,
    ExecutionContext,
    ExecutionResult,
    ActionExecutor,
)
from case_engine.executor_registry import (
    ActionExecutorRegistry,
    ExecutorRegistrationError,
    UnknownExecutorError,
)
from case_engine.action_runtime import (
    ActionRuntime,
    build_action_runtime,
    EXECUTION_TIMEOUT_DEFAULT,
)

# Sprint 2.3: Provider Abstraction Layer
from case_engine.provider_models import (
    ProviderCapability,
    ProviderRequest,
    ProviderResponse,
    ProviderHealth,
    ProviderMetadata,
)
from case_engine.provider_exceptions import (
    ProviderError,
    ProviderTransientError,
    ProviderPermanentError,
    ProviderUnavailableError,
    ProviderTimeoutError,
    ProviderRateLimitError,
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderValidationError,
    ProviderCapabilityError,
    ProviderExecutionError,
)
from case_engine.provider_interface import Provider
from case_engine.provider_registry import (
    ProviderRegistry,
    ProviderRegistrationError,
    UnknownProviderError,
)
from case_engine.provider_router import ProviderRouter

__all__ = [
    # Sprint 1: Case Foundation
    "CaseState",
    "ALLOWED_TRANSITIONS",
    "TERMINAL_STATES",
    "Case",
    "CaseTransition",
    "AuditEntry",
    "AuditEventType",
    "ClassificationResult",
    "MatchType",
    "TopicKey",
    "CaseStateMachine",
    "TransitionError",
    "AuditLogger",
    "TransferContextPayload",
    "AttemptedRemediation",
    "EscalationDecision",
    "EscalationEngine",
    "EscalationTrigger",
    "EscalationPriority",
    "TopicClassifier",
    "CaseRepository",
    "CaseService",
    "build_case_service",
    # Sprint 2.1: Action Gateway
    "ActionState",
    "ActionRiskLevel",
    "ActionTransitionError",
    "ActionRequest",
    "ActionTransitionRecord",
    "ActionProposal",
    "ActionRepository",
    "ActionGateway",
    "ActionGatewayError",
    "DuplicateActionError",
    "build_action_gateway",
    # Sprint 2.2: Execution Runtime
    "ActionExecutionError",
    "RetryableExecutionError",
    "PermanentExecutionError",
    "RollbackError",
    "ExecutionContext",
    "ExecutionResult",
    "ActionExecutor",
    "ActionExecutorRegistry",
    "ExecutorRegistrationError",
    "UnknownExecutorError",
    "ActionRuntime",
    "build_action_runtime",
    "EXECUTION_TIMEOUT_DEFAULT",
    # Sprint 2.3: Provider Abstraction Layer
    "ProviderCapability",
    "ProviderRequest",
    "ProviderResponse",
    "ProviderHealth",
    "ProviderMetadata",
    "ProviderError",
    "ProviderTransientError",
    "ProviderPermanentError",
    "ProviderUnavailableError",
    "ProviderTimeoutError",
    "ProviderRateLimitError",
    "ProviderAuthenticationError",
    "ProviderAuthorizationError",
    "ProviderValidationError",
    "ProviderCapabilityError",
    "ProviderExecutionError",
    "Provider",
    "ProviderRegistry",
    "ProviderRegistrationError",
    "UnknownProviderError",
    "ProviderRouter",
]
