"""
case_engine/workflows/playbooks/defaults.py

Sprint 2.40: Workflow Playbook System — nine default WorkflowPlaybook instances.

Topics covered:
  MINIMAL_INVESTIGATION  — absolute fallback; always present
  OTP_DELIVERY_FAILURE
  VKYC_SESSION_FAILURE
  DOCUMENT_OCR_FAILURE
  API_CALLBACK_FAILURE
  AGENT_PORTAL_ISSUE
  SESSION_FAILURE
  DOCUMENT_UPLOAD_FAILURE
  UNKNOWN_ISSUE

All playbooks are ACTIVE + enabled. build_defaults() returns them as a list;
get_minimal_playbook() returns only the fallback.
"""
from __future__ import annotations

from case_engine.investigation.evidence.models import EvidencePriority
from case_engine.investigation.planner.models import (
    EvidenceKind,
    EvidenceRequirement,
    RetryPolicy,
)
from case_engine.tools.tool_models import ToolCapability
from case_engine.workflows.playbooks.models import (
    PlaybookEntryCondition,
    PlaybookExitCondition,
    PlaybookGraph,
    PlaybookStatus,
    PlaybookStep,
    PlaybookStepKind,
    RiskLevel,
    WorkflowPlaybook,
)
from case_engine.workflows.playbooks.versioning import WorkflowVersion

# ── Shared defaults ────────────────────────────────────────────────────────────

_V1        = WorkflowVersion(major=1, minor=0, patch=0)
_CREATED   = "2024-01-01T00:00:00+00:00"
_RETRY_STD = RetryPolicy(max_attempts=2, backoff_seconds=1.0, retry_on_timeout=True,  retry_on_partial=False)
_RETRY_VIS = RetryPolicy(max_attempts=1, backoff_seconds=0.0, retry_on_timeout=False, retry_on_partial=False)


# ── Builder helpers ────────────────────────────────────────────────────────────

def _req(
    req_id:  str,
    kind:    EvidenceKind,
    title:   str,
    desc:    str,
    *,
    priority:   EvidencePriority        = EvidencePriority.NORMAL,
    required:   bool                    = True,
    fields:     tuple[str, ...]         = (),
    hints:      tuple[str, ...]         = (),
    capability: ToolCapability | None   = None,
) -> EvidenceRequirement:
    return EvidenceRequirement(
        requirement_id=req_id,
        kind=kind,
        title=title,
        description=desc,
        priority=priority,
        required=required,
        expected_fields=fields,
        validation_hints=hints,
        capability_hint=capability,
    )


def _step(
    step_id:          str,
    name:             str,
    desc:             str,
    kind:             PlaybookStepKind,
    evidence:         tuple[EvidenceRequirement, ...] = (),
    *,
    depends_on:       tuple[str, ...]       = (),
    parallel_group:   str | None            = None,
    fallback:         str | None            = None,
    retry:            RetryPolicy           = _RETRY_STD,
    capability:       ToolCapability | None = None,
    timeout:          float                 = 30.0,
    optional:         bool                  = False,
    critical:         bool                  = False,
    duration:         float                 = 10.0,
) -> PlaybookStep:
    return PlaybookStep(
        step_id=step_id,
        name=name,
        description=desc,
        kind=kind,
        evidence_required=evidence,
        depends_on=depends_on,
        parallel_group=parallel_group,
        fallback=fallback,
        retry_policy=retry,
        required_capability=capability,
        timeout_seconds=timeout,
        optional=optional,
        critical=critical,
        estimated_duration_seconds=duration,
        validation_rules=(),
    )


def _playbook(
    playbook_id:   str,
    name:          str,
    topic:         str,
    desc:          str,
    steps:         list[PlaybookStep],
    *,
    required_slots:   tuple[str, ...]              = (),
    optional_slots:   tuple[str, ...]              = (),
    entry_conditions: list[PlaybookEntryCondition] = (),
    exit_conditions:  list[PlaybookExitCondition]  = (),
    required_evidence: tuple[EvidenceRequirement, ...] = (),
    priority:          EvidencePriority             = EvidencePriority.NORMAL,
    risk_level:        RiskLevel                    = RiskLevel.SAFE,
    duration:          float                        = 60.0,
    requires_approval: bool                         = False,
    tags:              tuple[str, ...]              = (),
) -> WorkflowPlaybook:
    graph = PlaybookGraph(steps=tuple(steps), parallel_groups=())
    return WorkflowPlaybook(
        playbook_id=playbook_id,
        name=name,
        version=_V1,
        topic=topic,
        description=desc,
        required_slots=required_slots,
        optional_slots=optional_slots,
        investigation_graph=graph,
        entry_conditions=tuple(entry_conditions),
        exit_conditions=tuple(exit_conditions),
        required_evidence=required_evidence,
        priority=priority,
        risk_level=risk_level,
        estimated_duration_seconds=duration,
        requires_approval=requires_approval,
        client_scope=(),
        enabled=True,
        metadata={},
        tags=tags,
        created_at=_CREATED,
        updated_at=_CREATED,
        status=PlaybookStatus.ACTIVE,
    )


# ── 1. MINIMAL_INVESTIGATION ───────────────────────────────────────────────────

def _make_minimal() -> WorkflowPlaybook:
    req = _req(
        "min-01", EvidenceKind.KNOWLEDGE, "Basic SOP Lookup",
        "Search knowledge base for any applicable SOP for the unclassified issue",
        priority=EvidencePriority.NORMAL, required=True,
        fields=("sop_id", "sop_title", "resolution_steps"),
        capability=ToolCapability.QUERY,
    )
    s1 = _step(
        "min_step_01", "Collect Basic Case Context",
        "Minimal evidence collection for unclassified or unknown issues",
        PlaybookStepKind.COLLECT_EVIDENCE,
        evidence=(req,),
        critical=True, duration=10.0,
    )
    return _playbook(
        "minimal_investigation_v1", "Minimal Investigation",
        "MINIMAL_INVESTIGATION",
        "Absolute fallback playbook — used when no specific playbook matches the topic",
        [s1],
        required_evidence=(req,),
        priority=EvidencePriority.LOW,
        tags=("fallback", "minimal"),
    )


# ── 2. OTP_DELIVERY_FAILURE ────────────────────────────────────────────────────

def _make_otp_delivery_failure() -> WorkflowPlaybook:
    r_log = _req(
        "otp-01", EvidenceKind.LOG, "OTP Delivery Log",
        "Retrieve OTP delivery status and gateway response code",
        priority=EvidencePriority.HIGH, required=True,
        fields=("delivery_status", "gateway_code", "timestamp"),
        hints=("Check SMS gateway timeout", "Check recipient blacklist status"),
        capability=ToolCapability.READ,
    )
    r_user = _req(
        "otp-02", EvidenceKind.API, "User Phone Details",
        "Fetch user phone number and carrier validation status",
        priority=EvidencePriority.NORMAL, required=True,
        fields=("phone_number", "phone_verified", "carrier"),
        capability=ToolCapability.READ,
    )
    r_hist = _req(
        "otp-03", EvidenceKind.SUMMARY, "OTP Failure History",
        "Aggregate recent OTP failure count for this user over 24 hours",
        priority=EvidencePriority.LOW, required=False,
        fields=("failure_count_24h", "last_success"),
        capability=ToolCapability.QUERY,
    )

    s1 = _step("otp_step_01", "Collect OTP Delivery Status",
               "Retrieve OTP delivery log and gateway response for this send attempt",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_log,),
               critical=True, duration=10.0)
    s2 = _step("otp_step_02", "Collect User Phone Details",
               "Retrieve user's phone number and carrier for delivery validation",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_user,),
               depends_on=("otp_step_01",), duration=8.0)
    s3 = _step("otp_step_03", "Collect OTP Failure History",
               "Aggregate recent OTP failures to identify repeat patterns",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_hist,),
               depends_on=("otp_step_01",), optional=True, duration=8.0)
    s4 = _step("otp_step_04", "Correlate Delivery Failure Root Cause",
               "Correlate delivery log with user phone data to identify root cause",
               PlaybookStepKind.CORRELATE, evidence=(),
               depends_on=("otp_step_02", "otp_step_03"), duration=5.0)

    entry = [PlaybookEntryCondition("otp-entry-01", "Phone number must be present",
                                   "phone_number", "exists")]
    exit_conds = [PlaybookExitCondition("otp-exit-01", "OTP delivery log retrieved",
                                        "otp_delivery_status", required=True)]
    return _playbook(
        "otp_delivery_failure_v1", "OTP Delivery Failure Investigation",
        "OTP_DELIVERY_FAILURE",
        "Investigate OTP delivery failures by checking gateway logs and user phone status",
        [s1, s2, s3, s4],
        required_slots=("phone_number",),
        optional_slots=("case_id", "otp_channel"),
        entry_conditions=entry,
        exit_conditions=exit_conds,
        required_evidence=(r_log, r_user, r_hist),
        priority=EvidencePriority.HIGH,
        duration=60.0,
        tags=("otp", "delivery", "sms"),
    )


# ── 3. VKYC_SESSION_FAILURE ────────────────────────────────────────────────────

def _make_vkyc_session_failure() -> WorkflowPlaybook:
    r_sess = _req(
        "vkyc-01", EvidenceKind.SESSION, "VKYC Session Details",
        "Retrieve session state, failure code, and timestamp from VKYC service",
        priority=EvidencePriority.CRITICAL, required=True,
        fields=("session_id", "status", "failure_code", "created_at"),
        hints=("Verify session exists", "Classify failure code category"),
        capability=ToolCapability.READ,
    )
    r_user = _req(
        "vkyc-02", EvidenceKind.API, "User KYC Profile",
        "Fetch user identity details and KYC tier before considering reset",
        priority=EvidencePriority.HIGH, required=True,
        fields=("phone_number", "kyc_tier", "identity_verified"),
        capability=ToolCapability.READ,
    )
    r_hist = _req(
        "vkyc-03", EvidenceKind.SUMMARY, "VKYC Failure History",
        "Aggregate recent VKYC failures for escalation risk assessment",
        priority=EvidencePriority.NORMAL, required=False,
        fields=("failure_count_7d", "last_success"),
        capability=ToolCapability.QUERY,
    )
    r_vis = _req(
        "vkyc-04", EvidenceKind.VISION, "VKYC Session Frame Analysis",
        "Analyse session frames for image quality and liveness detection issues",
        priority=EvidencePriority.NORMAL, required=False,
        fields=("image_quality_score", "liveness_score", "failure_frame"),
        capability=ToolCapability.ANALYZE,
    )

    s1 = _step("vkyc_step_01", "Collect VKYC Session Details",
               "Retrieve session state and failure code from the VKYC service",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_sess,),
               critical=True, timeout=30.0, duration=12.0)
    s2 = _step("vkyc_step_02", "Collect User KYC Profile",
               "Retrieve user KYC tier and identity verification status",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_user,),
               depends_on=("vkyc_step_01",), critical=True, timeout=20.0, duration=8.0)
    s3 = _step("vkyc_step_03", "Collect VKYC Failure History",
               "Aggregate recent failures for escalation risk scoring",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_hist,),
               depends_on=("vkyc_step_01",), optional=True, duration=8.0)
    s4 = _step("vkyc_step_04", "Analyze Session Recording",
               "Vision analysis on session frames for image and liveness quality",
               PlaybookStepKind.ANALYZE, evidence=(r_vis,),
               depends_on=("vkyc_step_01",), optional=True,
               retry=_RETRY_VIS, capability=ToolCapability.ANALYZE, timeout=45.0, duration=20.0)
    s5 = _step("vkyc_step_05", "Assess Reset Eligibility",
               "Correlate session, user profile, and history to assess reset eligibility",
               PlaybookStepKind.ASSESS, evidence=(),
               depends_on=("vkyc_step_02", "vkyc_step_03", "vkyc_step_04"), duration=5.0)

    entry = [
        PlaybookEntryCondition("vkyc-entry-01", "Session ID must be present", "session_id", "exists"),
        PlaybookEntryCondition("vkyc-entry-02", "Phone number must be present", "phone_number", "exists"),
    ]
    exit_conds = [
        PlaybookExitCondition("vkyc-exit-01", "Session details must be retrieved", "vkyc_session_details"),
        PlaybookExitCondition("vkyc-exit-02", "User KYC profile must be available", "user_kyc_profile"),
    ]
    return _playbook(
        "vkyc_session_failure_v1", "VKYC Session Failure Investigation",
        "VKYC_SESSION_FAILURE",
        "Investigate VKYC session failures: collect session state, user KYC profile, failure history, and optional vision analysis",
        [s1, s2, s3, s4, s5],
        required_slots=("session_id", "phone_number"),
        optional_slots=("failure_code",),
        entry_conditions=entry,
        exit_conditions=exit_conds,
        required_evidence=(r_sess, r_user, r_hist, r_vis),
        priority=EvidencePriority.HIGH,
        risk_level=RiskLevel.REVERSIBLE,
        duration=120.0,
        requires_approval=True,
        tags=("vkyc", "session", "kyc"),
    )


# ── 4. DOCUMENT_OCR_FAILURE ────────────────────────────────────────────────────

def _make_document_ocr_failure() -> WorkflowPlaybook:
    r_ocr = _req(
        "ocr-01", EvidenceKind.LOG, "OCR Processing Log",
        "Retrieve OCR result, confidence score, and failure reason from OCR service",
        priority=EvidencePriority.HIGH, required=True,
        fields=("document_id", "ocr_status", "confidence_score", "failure_reason"),
        capability=ToolCapability.READ,
    )
    r_img = _req(
        "ocr-02", EvidenceKind.VISION, "Document Image Quality Analysis",
        "Analyse uploaded document image for quality, clarity, and orientation",
        priority=EvidencePriority.HIGH, required=True,
        fields=("image_quality_score", "blur_score", "orientation", "detected_text"),
        hints=("Check image resolution >= 300dpi", "Verify document corners visible"),
        capability=ToolCapability.ANALYZE,
    )
    r_hist = _req(
        "ocr-03", EvidenceKind.API, "Document Submission History",
        "Check prior document submission attempts and OCR outcomes for this user",
        priority=EvidencePriority.LOW, required=False,
        fields=("submission_count", "last_success"),
        capability=ToolCapability.QUERY,
    )

    s1 = _step("ocr_step_01", "Collect OCR Processing Log",
               "Retrieve OCR service result and failure code for the document",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_ocr,),
               critical=True, duration=10.0)
    s2 = _step("ocr_step_02", "Analyze Document Image Quality",
               "Run vision analysis on the document image to detect quality issues",
               PlaybookStepKind.ANALYZE, evidence=(r_img,),
               depends_on=("ocr_step_01",), critical=True,
               retry=_RETRY_VIS, capability=ToolCapability.ANALYZE, timeout=45.0, duration=20.0)
    s3 = _step("ocr_step_03", "Collect Document Submission History",
               "Aggregate prior submission attempts to identify repeat patterns",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_hist,),
               depends_on=("ocr_step_01",), optional=True, duration=8.0)
    s4 = _step("ocr_step_04", "Validate OCR Results",
               "Validate extracted fields and confidence thresholds against expected schema",
               PlaybookStepKind.VALIDATE, evidence=(),
               depends_on=("ocr_step_02",), duration=5.0)

    entry = [PlaybookEntryCondition("ocr-entry-01", "Document ID must be present",
                                   "document_id", "exists")]
    exit_conds = [
        PlaybookExitCondition("ocr-exit-01", "OCR log must be retrieved", "ocr_log"),
        PlaybookExitCondition("ocr-exit-02", "Image quality score must be available", "image_quality_score"),
    ]
    return _playbook(
        "document_ocr_failure_v1", "Document OCR Failure Investigation",
        "DOCUMENT_OCR_FAILURE",
        "Investigate document OCR failures by collecting processing logs and performing image quality analysis",
        [s1, s2, s3, s4],
        required_slots=("document_id",),
        optional_slots=("user_id", "failure_code"),
        entry_conditions=entry,
        exit_conditions=exit_conds,
        required_evidence=(r_ocr, r_img, r_hist),
        priority=EvidencePriority.HIGH,
        duration=90.0,
        tags=("ocr", "document", "vision"),
    )


# ── 5. API_CALLBACK_FAILURE ────────────────────────────────────────────────────

def _make_api_callback_failure() -> WorkflowPlaybook:
    r_log = _req(
        "api-01", EvidenceKind.LOG, "API Callback Log",
        "Retrieve callback request/response logs and HTTP status codes",
        priority=EvidencePriority.HIGH, required=True,
        fields=("callback_id", "endpoint", "http_status", "response_body", "timestamp"),
        hints=("Distinguish 4xx client errors from 5xx server errors",
               "Check request timeout duration"),
        capability=ToolCapability.READ,
    )
    r_cfg = _req(
        "api-02", EvidenceKind.CONFIGURATION, "API Endpoint Configuration",
        "Retrieve endpoint configuration, retry policy, and authentication settings",
        priority=EvidencePriority.NORMAL, required=True,
        fields=("endpoint_url", "auth_type", "retry_enabled", "timeout_ms"),
        capability=ToolCapability.READ,
    )
    r_pattern = _req(
        "api-03", EvidenceKind.SUMMARY, "API Failure Pattern",
        "Aggregate recent callback failures to identify systematic issues",
        priority=EvidencePriority.LOW, required=False,
        fields=("failure_count_1h", "failure_rate", "last_success"),
        capability=ToolCapability.QUERY,
    )

    s1 = _step("api_step_01", "Collect API Callback Log",
               "Retrieve full request/response log for the failed callback",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_log,),
               critical=True, duration=10.0)
    s2 = _step("api_step_02", "Collect Endpoint Configuration",
               "Retrieve API endpoint configuration and authentication details",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_cfg,),
               depends_on=("api_step_01",), duration=8.0)
    s3 = _step("api_step_03", "Collect API Failure Pattern",
               "Aggregate recent failures to detect systematic gateway issues",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_pattern,),
               depends_on=("api_step_01",), optional=True, duration=8.0)
    s4 = _step("api_step_04", "Assess Retry Eligibility",
               "Determine whether the callback can be automatically retried",
               PlaybookStepKind.ASSESS, evidence=(),
               depends_on=("api_step_02", "api_step_03"), duration=5.0)

    entry = [PlaybookEntryCondition("api-entry-01", "Callback ID must be present",
                                   "callback_id", "exists")]
    exit_conds = [PlaybookExitCondition("api-exit-01", "Callback log must be retrieved",
                                        "callback_log")]
    return _playbook(
        "api_callback_failure_v1", "API Callback Failure Investigation",
        "API_CALLBACK_FAILURE",
        "Investigate API callback failures by collecting request logs, endpoint config, and failure patterns",
        [s1, s2, s3, s4],
        required_slots=("callback_id",),
        optional_slots=("api_endpoint", "failure_code"),
        entry_conditions=entry,
        exit_conditions=exit_conds,
        required_evidence=(r_log, r_cfg, r_pattern),
        priority=EvidencePriority.NORMAL,
        duration=60.0,
        tags=("api", "callback", "integration"),
    )


# ── 6. AGENT_PORTAL_ISSUE ──────────────────────────────────────────────────────

def _make_agent_portal_issue() -> WorkflowPlaybook:
    r_sess = _req(
        "portal-01", EvidenceKind.SESSION, "Agent Portal Session",
        "Retrieve portal session state, access token, and error details",
        priority=EvidencePriority.HIGH, required=True,
        fields=("agent_id", "session_token", "error_code", "permissions"),
        capability=ToolCapability.READ,
    )
    r_acct = _req(
        "portal-02", EvidenceKind.API, "Agent Account Details",
        "Fetch agent account details, role assignments, and active status",
        priority=EvidencePriority.HIGH, required=True,
        fields=("agent_id", "role", "account_status", "last_login"),
        capability=ToolCapability.READ,
    )
    r_flags = _req(
        "portal-03", EvidenceKind.CONFIGURATION, "Portal Feature Flags",
        "Check feature flag states relevant to the agent portal",
        priority=EvidencePriority.LOW, required=False,
        fields=("portal_maintenance_mode", "feature_flags"),
        capability=ToolCapability.READ,
    )

    s1 = _step("portal_step_01", "Collect Portal Session Details",
               "Retrieve agent portal session state and error details",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_sess,),
               critical=True, duration=10.0)
    s2 = _step("portal_step_02", "Collect Agent Account Details",
               "Retrieve agent account status and role permissions",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_acct,),
               depends_on=("portal_step_01",), critical=True, duration=8.0)
    s3 = _step("portal_step_03", "Check Portal Feature Flags",
               "Verify portal maintenance mode and relevant feature flags",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_flags,),
               depends_on=("portal_step_01",), optional=True, duration=6.0)
    s4 = _step("portal_step_04", "Validate Agent Access Rights",
               "Cross-reference session state with account permissions to identify access issue",
               PlaybookStepKind.CORRELATE, evidence=(),
               depends_on=("portal_step_02", "portal_step_03"), duration=5.0)

    entry = [PlaybookEntryCondition("portal-entry-01", "Agent ID must be present",
                                   "agent_id", "exists")]
    exit_conds = [
        PlaybookExitCondition("portal-exit-01", "Portal session must be available", "portal_session"),
        PlaybookExitCondition("portal-exit-02", "Agent account details must be available", "agent_details"),
    ]
    return _playbook(
        "agent_portal_issue_v1", "Agent Portal Issue Investigation",
        "AGENT_PORTAL_ISSUE",
        "Investigate agent portal issues by collecting session state, account details, and feature flag status",
        [s1, s2, s3, s4],
        required_slots=("agent_id",),
        optional_slots=("portal_url", "error_code"),
        entry_conditions=entry,
        exit_conditions=exit_conds,
        required_evidence=(r_sess, r_acct, r_flags),
        priority=EvidencePriority.NORMAL,
        duration=60.0,
        tags=("agent", "portal", "access"),
    )


# ── 7. SESSION_FAILURE ─────────────────────────────────────────────────────────

def _make_session_failure() -> WorkflowPlaybook:
    r_sess = _req(
        "sess-01", EvidenceKind.SESSION, "Session State",
        "Retrieve generic session state, status, and failure details",
        priority=EvidencePriority.HIGH, required=True,
        fields=("session_id", "status", "error_code", "created_at", "updated_at"),
        capability=ToolCapability.READ,
    )
    r_user = _req(
        "sess-02", EvidenceKind.API, "User Context",
        "Fetch user account details and list of active sessions",
        priority=EvidencePriority.NORMAL, required=False,
        fields=("user_id", "account_status", "active_sessions"),
        capability=ToolCapability.READ,
    )
    r_log = _req(
        "sess-03", EvidenceKind.LOG, "Session Error Log",
        "Retrieve error log entries for the failing session",
        priority=EvidencePriority.NORMAL, required=False,
        fields=("error_message", "stack_trace", "timestamp"),
        capability=ToolCapability.READ,
    )

    s1 = _step("sess_step_01", "Collect Session State",
               "Retrieve session state and failure details from the session service",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_sess,),
               critical=True, duration=10.0)
    s2 = _step("sess_step_02", "Collect User Context",
               "Retrieve user account details for the session owner",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_user,),
               depends_on=("sess_step_01",), optional=True, duration=8.0)
    s3 = _step("sess_step_03", "Collect Session Error Log",
               "Retrieve error log entries for additional failure context",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_log,),
               depends_on=("sess_step_01",), optional=True, duration=8.0)
    s4 = _step("sess_step_04", "Assess Resolution Path",
               "Evaluate session data, user context, and error logs to determine resolution path",
               PlaybookStepKind.ASSESS, evidence=(),
               depends_on=("sess_step_02", "sess_step_03"), duration=5.0)

    entry = [PlaybookEntryCondition("sess-entry-01", "Session ID must be present",
                                   "session_id", "exists")]
    exit_conds = [PlaybookExitCondition("sess-exit-01", "Session state must be retrieved",
                                        "session_state")]
    return _playbook(
        "session_failure_v1", "Generic Session Failure Investigation",
        "SESSION_FAILURE",
        "Investigate generic session failures by collecting session state, user context, and error logs",
        [s1, s2, s3, s4],
        required_slots=("session_id",),
        optional_slots=("phone_number", "error_code"),
        entry_conditions=entry,
        exit_conditions=exit_conds,
        required_evidence=(r_sess, r_user, r_log),
        priority=EvidencePriority.NORMAL,
        duration=60.0,
        tags=("session", "generic"),
    )


# ── 8. DOCUMENT_UPLOAD_FAILURE ─────────────────────────────────────────────────

def _make_document_upload_failure() -> WorkflowPlaybook:
    r_upload = _req(
        "upload-01", EvidenceKind.LOG, "Upload Attempt Log",
        "Retrieve upload attempt status, error code, and S3 storage details",
        priority=EvidencePriority.HIGH, required=True,
        fields=("document_id", "upload_status", "error_code", "s3_key", "file_size"),
        capability=ToolCapability.READ,
    )
    r_val = _req(
        "upload-02", EvidenceKind.DATABASE, "Document Validation Status",
        "Retrieve document format validation result and rejection reason",
        priority=EvidencePriority.HIGH, required=True,
        fields=("document_type", "mime_type", "file_size_bytes", "validation_error"),
        capability=ToolCapability.QUERY,
    )
    r_quota = _req(
        "upload-03", EvidenceKind.API, "User Document Quota",
        "Check user document upload quota and submission eligibility",
        priority=EvidencePriority.LOW, required=False,
        fields=("documents_submitted", "quota_limit", "quota_remaining"),
        capability=ToolCapability.READ,
    )

    s1 = _step("upload_step_01", "Collect Upload Attempt Log",
               "Retrieve upload attempt details and S3 storage status",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_upload,),
               critical=True, duration=10.0)
    s2 = _step("upload_step_02", "Collect Document Validation Status",
               "Retrieve format validation result and rejection reason",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_val,),
               depends_on=("upload_step_01",), critical=True, duration=8.0)
    s3 = _step("upload_step_03", "Collect User Document Quota",
               "Check user document quota to detect quota exhaustion",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_quota,),
               depends_on=("upload_step_01",), optional=True, duration=6.0)
    s4 = _step("upload_step_04", "Validate Document Requirements",
               "Validate upload against document type requirements and format constraints",
               PlaybookStepKind.VALIDATE, evidence=(),
               depends_on=("upload_step_02",), duration=5.0)

    entry = [PlaybookEntryCondition("upload-entry-01", "Document ID must be present",
                                   "document_id", "exists")]
    exit_conds = [
        PlaybookExitCondition("upload-exit-01", "Upload log must be retrieved", "upload_log"),
        PlaybookExitCondition("upload-exit-02", "Validation status must be available", "validation_status"),
    ]
    return _playbook(
        "document_upload_failure_v1", "Document Upload Failure Investigation",
        "DOCUMENT_UPLOAD_FAILURE",
        "Investigate document upload failures by collecting upload logs, format validation, and user quota status",
        [s1, s2, s3, s4],
        required_slots=("document_id",),
        optional_slots=("user_id", "case_id"),
        entry_conditions=entry,
        exit_conditions=exit_conds,
        required_evidence=(r_upload, r_val, r_quota),
        priority=EvidencePriority.NORMAL,
        duration=60.0,
        tags=("document", "upload", "storage"),
    )


# ── 9. UNKNOWN_ISSUE ───────────────────────────────────────────────────────────

def _make_unknown_issue() -> WorkflowPlaybook:
    r_sop = _req(
        "unknown-01", EvidenceKind.KNOWLEDGE, "SOP Lookup",
        "Search knowledge base for any applicable SOP or known issue pattern",
        priority=EvidencePriority.HIGH, required=True,
        fields=("sop_id", "sop_title", "resolution_steps"),
        hints=("Try fuzzy topic matching", "Search by error code if available"),
        capability=ToolCapability.QUERY,
    )
    r_hist = _req(
        "unknown-02", EvidenceKind.SUMMARY, "Case History Summary",
        "Retrieve recent case history for the user or session",
        priority=EvidencePriority.NORMAL, required=False,
        fields=("case_count", "topics", "last_resolution"),
        capability=ToolCapability.QUERY,
    )
    r_wf = _req(
        "unknown-03", EvidenceKind.WORKFLOW, "Active Workflow State",
        "Check for active workflows that might explain the issue",
        priority=EvidencePriority.NORMAL, required=False,
        fields=("workflow_id", "current_step", "workflow_status"),
        capability=ToolCapability.READ,
    )

    s1 = _step("unknown_step_01", "Search Knowledge Base",
               "Search SOP knowledge base for any applicable resolution guidance",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_sop,),
               critical=True, duration=10.0)
    s2 = _step("unknown_step_02", "Collect Case History",
               "Retrieve recent case history to identify recurring patterns",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_hist,),
               depends_on=("unknown_step_01",), optional=True, duration=8.0)
    s3 = _step("unknown_step_03", "Check Active Workflows",
               "Check for active workflows contributing to the issue",
               PlaybookStepKind.COLLECT_EVIDENCE, evidence=(r_wf,),
               depends_on=("unknown_step_01",), optional=True, duration=8.0)
    s4 = _step("unknown_step_04", "Assess Escalation Requirement",
               "Evaluate all collected evidence to determine if escalation is required",
               PlaybookStepKind.ASSESS, evidence=(),
               depends_on=("unknown_step_02", "unknown_step_03"), duration=5.0)

    exit_conds = [PlaybookExitCondition("unknown-exit-01", "SOP lookup must complete",
                                        "sop_result")]
    return _playbook(
        "unknown_issue_v1", "Unknown Issue Investigation",
        "UNKNOWN_ISSUE",
        "Generic investigation for unclassified issues — searches SOPs, case history, and workflow state before escalation",
        [s1, s2, s3, s4],
        optional_slots=("phone_number", "session_id", "case_id"),
        exit_conditions=exit_conds,
        required_evidence=(r_sop, r_hist, r_wf),
        priority=EvidencePriority.NORMAL,
        duration=60.0,
        tags=("unknown", "generic", "escalation"),
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def build_defaults() -> list[WorkflowPlaybook]:
    """Return all nine default WorkflowPlaybook instances."""
    return [
        _make_minimal(),
        _make_otp_delivery_failure(),
        _make_vkyc_session_failure(),
        _make_document_ocr_failure(),
        _make_api_callback_failure(),
        _make_agent_portal_issue(),
        _make_session_failure(),
        _make_document_upload_failure(),
        _make_unknown_issue(),
    ]


def get_minimal_playbook() -> WorkflowPlaybook:
    """Return only the MINIMAL_INVESTIGATION fallback playbook."""
    return _make_minimal()
