"""
unity — Unity Admin Portal read-only integration (Sprint 2.51).

Public surface used by the production Unity tool adapters:

    UnityConfig                  — env-driven configuration
    UnityClient / build_unity_client — async READ-ONLY httpx client
    UnityTokenManager            — JWT lifecycle cache with proactive refresh
    select_most_relevant_session — pick 1-of-N sessions for a support ticket
    parse_session / parse_session_list / parse_session_details
                                 — raw JSON → canonical models
    build_unity_evidence         — canonical UnityEvidence normaliser
    derive_failure_summary / derive_recent_summary / derive_onboarding_stage
                                 — derivations used by specific tool adapters
    parse_extras                 — best-effort JSON-string field parser

Domain models: UnityEvidence, UnitySession, SessionList, CustomerInfo,
AgentInfo, AuditorInfo, FeedbackInfo, TimelineInfo, MetadataInfo,
MediaInfo, JourneyStep, QnA, VerificationDoc.

Enums: SessionStatus (7 values + UNKNOWN), EvidenceAvailability.

Traces (10 canonical tags): ENTER_UNITY_TOKEN, EXIT_UNITY_TOKEN,
ENTER_UNITY_LOOKUP, EXIT_UNITY_LOOKUP, ENTER_UNITY_DETAILS,
EXIT_UNITY_DETAILS, ENTER_EVIDENCE_MAPPING, EXIT_EVIDENCE_MAPPING,
UNITY_API_FAILURE, UNITY_AUTH_FAILURE.

Exceptions: UnityError, UnityAuthenticationFailure, UnityApiError,
UnitySessionNotFound, UnityMultipleSessionCandidates, UnityNetworkFailure,
UnityTimeoutFailure, UnityServiceUnavailable, UnityUnexpectedResponse,
UnityDisabled.

Dependency direction:
    unity → httpx + stdlib.
    unity → NO imports from case_engine / freshdesk / metrics_platform.
"""
from unity.client import UnityClient, build_unity_client
from unity.config import UnityConfig
from unity.exceptions import (
    UnityApiError,
    UnityAuthenticationFailure,
    UnityDisabled,
    UnityError,
    UnityMultipleSessionCandidates,
    UnityNetworkFailure,
    UnityServiceUnavailable,
    UnitySessionNotFound,
    UnityTimeoutFailure,
    UnityUnexpectedResponse,
)
from unity.models import (
    AgentInfo,
    AuditorInfo,
    CustomerInfo,
    EvidenceAvailability,
    FeedbackInfo,
    JourneyStep,
    MediaInfo,
    MetadataInfo,
    QnA,
    SessionList,
    SessionStatus,
    TimelineInfo,
    UnityEvidence,
    UnitySession,
    VerificationDoc,
)
from unity.normalizer import (
    build_unity_evidence,
    derive_failure_summary,
    derive_onboarding_stage,
    derive_recent_summary,
    parse_extras,
    parse_session,
    parse_session_details,
    parse_session_list,
)
from unity.session_resolver import select_most_relevant_session
from unity.token_manager import UnityTokenManager
from unity.traces import (
    ALL_UNITY_TRACES,
    TRACE_ENTER_EVIDENCE_MAPPING,
    TRACE_ENTER_UNITY_DETAILS,
    TRACE_ENTER_UNITY_LOOKUP,
    TRACE_ENTER_UNITY_TOKEN,
    TRACE_EXIT_EVIDENCE_MAPPING,
    TRACE_EXIT_UNITY_DETAILS,
    TRACE_EXIT_UNITY_LOOKUP,
    TRACE_EXIT_UNITY_TOKEN,
    TRACE_UNITY_API_FAILURE,
    TRACE_UNITY_AUTH_FAILURE,
    emit_unity_trace,
)

__all__ = [
    # Config + client
    "UnityConfig",
    "UnityClient",
    "build_unity_client",
    "UnityTokenManager",
    # Resolver / normaliser
    "select_most_relevant_session",
    "parse_session",
    "parse_session_list",
    "parse_session_details",
    "parse_extras",
    "build_unity_evidence",
    "derive_failure_summary",
    "derive_recent_summary",
    "derive_onboarding_stage",
    # Domain models
    "UnityEvidence",
    "UnitySession",
    "SessionList",
    "SessionStatus",
    "EvidenceAvailability",
    "CustomerInfo",
    "AgentInfo",
    "AuditorInfo",
    "FeedbackInfo",
    "TimelineInfo",
    "MetadataInfo",
    "MediaInfo",
    "JourneyStep",
    "QnA",
    "VerificationDoc",
    # Traces
    "emit_unity_trace",
    "ALL_UNITY_TRACES",
    "TRACE_ENTER_UNITY_TOKEN",
    "TRACE_EXIT_UNITY_TOKEN",
    "TRACE_ENTER_UNITY_LOOKUP",
    "TRACE_EXIT_UNITY_LOOKUP",
    "TRACE_ENTER_UNITY_DETAILS",
    "TRACE_EXIT_UNITY_DETAILS",
    "TRACE_ENTER_EVIDENCE_MAPPING",
    "TRACE_EXIT_EVIDENCE_MAPPING",
    "TRACE_UNITY_API_FAILURE",
    "TRACE_UNITY_AUTH_FAILURE",
    # Exceptions
    "UnityError",
    "UnityAuthenticationFailure",
    "UnityApiError",
    "UnitySessionNotFound",
    "UnityMultipleSessionCandidates",
    "UnityNetworkFailure",
    "UnityTimeoutFailure",
    "UnityServiceUnavailable",
    "UnityUnexpectedResponse",
    "UnityDisabled",
]
