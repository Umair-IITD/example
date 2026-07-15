"""
intelligence — Enterprise Intelligence Layer (Wave 3, Sprint 2.53).

Public surface:

    IntelligenceConfig                 — env-driven configuration
    IntelligenceOrchestrator           — top-level entry point
    LLMClient / build_llm_client       — provider-agnostic protocol + factory
    OpenAILLMClient / MockLLMClient    — concrete implementations
    PromptBuilder                      — versioned prompt templates façade
    ReasoningPromptTemplate            — individual template classes
    ClarificationPromptTemplate        — ...
    ObservationPromptTemplate          — ...
    CustomerReplyPromptTemplate        — ...
    ActionProposalPromptTemplate       — ...

    build_llm_context                  — Context Builder (pure function)

    parse_reasoning_result / parse_clarification_decision /
    parse_observation_draft / parse_customer_reply /
    parse_action_proposals             — schema-strict JSON parsers

Domain models: LLMContext, ReasoningResult, ClarificationDecision,
ObservationDraft, CustomerReplyDraft, ActionProposal, IntelligenceResult,
RetrievedChunk, EvidenceHint, ConfidenceLevel, RiskLevel, ActionKind,
ReasoningOutcome.

Traces: emit_wave3_trace + ALL_WAVE3_TRACES + 22 TRACE_NN_* constants.

Exceptions: IntelligenceError, IntelligenceDisabled, IntelligenceConfigError,
LLMRequestError, LLMTimeoutError, LLMAuthError, LLMRateLimitError,
LLMServerError, ReasoningParseError, ContextBuildError.

Dependency direction:
    intelligence → httpx + stdlib
    intelligence → NO imports from case_engine / freshdesk / unity /
                   metrics_platform (they are downstream consumers).
"""
from intelligence.config import IntelligenceConfig
from intelligence.context_builder import build_llm_context
from intelligence.exceptions import (
    ContextBuildError,
    IntelligenceConfigError,
    IntelligenceDisabled,
    IntelligenceError,
    LLMAuthError,
    LLMRateLimitError,
    LLMRequestError,
    LLMServerError,
    LLMTimeoutError,
    ReasoningParseError,
)
from intelligence.llm_client import (
    LLMClient,
    MockLLMClient,
    OpenAILLMClient,
    build_llm_client,
)
from intelligence.models import (
    ActionKind,
    ActionProposal,
    ClarificationDecision,
    ConfidenceLevel,
    CustomerReplyDraft,
    EvidenceHint,
    IntelligenceResult,
    LLMContext,
    ObservationDraft,
    ReasoningOutcome,
    ReasoningResult,
    RetrievedChunk,
    RiskLevel,
)
from intelligence.orchestrator import IntelligenceOrchestrator
from intelligence.prompt_builder import (
    ActionProposalPromptTemplate,
    ClarificationPromptTemplate,
    CustomerReplyPromptTemplate,
    ObservationPromptTemplate,
    PromptBuilder,
    PromptPair,
    ReasoningPromptTemplate,
)
from intelligence.reasoning_parser import (
    parse_action_proposals,
    parse_clarification_decision,
    parse_customer_reply,
    parse_observation_draft,
    parse_reasoning_result,
)
from intelligence.traces import (
    ALL_WAVE3_TRACES,
    TRACE_01_WEBHOOK_RECEIVED,
    TRACE_02_PAYLOAD_NORMALIZED,
    TRACE_03_TENANT_RESOLVED,
    TRACE_04_CASE_CREATED,
    TRACE_05_CLASSIFICATION_STARTED,
    TRACE_06_CLASSIFICATION_COMPLETED,
    TRACE_07_WORKFLOW_SELECTED,
    TRACE_08_INVESTIGATION_STARTED,
    TRACE_09_EVIDENCE_COLLECTION_STARTED,
    TRACE_10_UNITY_TOOL_EXECUTED,
    TRACE_11_METRIC_TOOL_EXECUTED,
    TRACE_12_EVIDENCE_BUNDLE_READY,
    TRACE_13_CONTEXT_BUILDER,
    TRACE_14_PROMPT_BUILDER,
    TRACE_15_HYBRID_RAG,
    TRACE_16_LLM_REQUEST,
    TRACE_17_LLM_RESPONSE,
    TRACE_18_REASONING_COMPLETE,
    TRACE_19_OBSERVATION_GENERATED,
    TRACE_20_CUSTOMER_REPLY_GENERATED,
    TRACE_21_ACTION_PROPOSAL,
    TRACE_22_PIPELINE_COMPLETE,
    emit_wave3_trace,
)

__all__ = [
    # Config + orchestrator
    "IntelligenceConfig",
    "IntelligenceOrchestrator",
    # LLM client
    "LLMClient",
    "OpenAILLMClient",
    "MockLLMClient",
    "build_llm_client",
    # Prompt building
    "PromptBuilder",
    "PromptPair",
    "ReasoningPromptTemplate",
    "ClarificationPromptTemplate",
    "ObservationPromptTemplate",
    "CustomerReplyPromptTemplate",
    "ActionProposalPromptTemplate",
    # Context builder
    "build_llm_context",
    # Parsers
    "parse_reasoning_result",
    "parse_clarification_decision",
    "parse_observation_draft",
    "parse_customer_reply",
    "parse_action_proposals",
    # Domain models
    "LLMContext",
    "RetrievedChunk",
    "EvidenceHint",
    "ReasoningResult",
    "ReasoningOutcome",
    "ClarificationDecision",
    "ObservationDraft",
    "CustomerReplyDraft",
    "ActionProposal",
    "ActionKind",
    "RiskLevel",
    "ConfidenceLevel",
    "IntelligenceResult",
    # Traces
    "emit_wave3_trace",
    "ALL_WAVE3_TRACES",
    "TRACE_01_WEBHOOK_RECEIVED",
    "TRACE_02_PAYLOAD_NORMALIZED",
    "TRACE_03_TENANT_RESOLVED",
    "TRACE_04_CASE_CREATED",
    "TRACE_05_CLASSIFICATION_STARTED",
    "TRACE_06_CLASSIFICATION_COMPLETED",
    "TRACE_07_WORKFLOW_SELECTED",
    "TRACE_08_INVESTIGATION_STARTED",
    "TRACE_09_EVIDENCE_COLLECTION_STARTED",
    "TRACE_10_UNITY_TOOL_EXECUTED",
    "TRACE_11_METRIC_TOOL_EXECUTED",
    "TRACE_12_EVIDENCE_BUNDLE_READY",
    "TRACE_13_CONTEXT_BUILDER",
    "TRACE_14_PROMPT_BUILDER",
    "TRACE_15_HYBRID_RAG",
    "TRACE_16_LLM_REQUEST",
    "TRACE_17_LLM_RESPONSE",
    "TRACE_18_REASONING_COMPLETE",
    "TRACE_19_OBSERVATION_GENERATED",
    "TRACE_20_CUSTOMER_REPLY_GENERATED",
    "TRACE_21_ACTION_PROPOSAL",
    "TRACE_22_PIPELINE_COMPLETE",
    # Exceptions
    "IntelligenceError",
    "IntelligenceDisabled",
    "IntelligenceConfigError",
    "LLMRequestError",
    "LLMTimeoutError",
    "LLMAuthError",
    "LLMRateLimitError",
    "LLMServerError",
    "ReasoningParseError",
    "ContextBuildError",
]
