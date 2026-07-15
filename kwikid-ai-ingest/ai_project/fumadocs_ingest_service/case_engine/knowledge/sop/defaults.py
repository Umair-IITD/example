"""
case_engine/knowledge/sop/defaults.py

Sprint 2.41: Default SOP library — 9 production SOPs covering all supported topics.

Topics covered:
  1. MINIMAL_INVESTIGATION    — generic single-step fallback
  2. OTP_DELIVERY_FAILURE     — OTP delivery diagnosis and retry
  3. VKYC_SESSION_FAILURE     — Video KYC session diagnosis and rescheduling
  4. DOCUMENT_OCR_FAILURE     — Document OCR failure and retry
  5. API_CALLBACK_FAILURE     — API callback retry and pattern analysis
  6. AGENT_PORTAL_ISSUE       — Agent portal access and session recovery
  7. SESSION_FAILURE          — User session diagnosis and token refresh
  8. DOCUMENT_UPLOAD_FAILURE  — Document upload retry and validation
  9. UNKNOWN_ISSUE            — Unknown issue triage and escalation

All SOPs:
  - Are ACTIVE and enabled
  - Are global (no client scope)
  - Contain realistic, non-placeholder operational steps
  - Use SOPStepKind for operational type
  - Use SOPActionType for high-level category
  - Are valid against SOPValidator
"""
from __future__ import annotations

from case_engine.knowledge.sop.models import (
    SOPActionType,
    SOPDocument,
    SOPExecutionHints,
    SOPParameter,
    SOPStatus,
    SOPStep,
    SOPStepKind,
    SOPTriggerCondition,
    SOPTriggerOperator,
    SOPVersion,
)

# ── Shared constants ───────────────────────────────────────────────────────────

_V1 = SOPVersion(version="1.0", created_by="system", change_note="Initial default SOP")

_HINTS_QUICK = SOPExecutionHints(estimated_seconds=30, can_be_automated=True)
_HINTS_MEDIUM = SOPExecutionHints(estimated_seconds=120, can_be_automated=True)
_HINTS_MANUAL = SOPExecutionHints(estimated_seconds=300, can_be_automated=False,
                                   requires_approval=False)
_HINTS_APPROVAL = SOPExecutionHints(estimated_seconds=120, can_be_automated=True,
                                     requires_approval=True)


# ── Builder helpers ────────────────────────────────────────────────────────────

def _step(
    step_number: int,
    step_id: str,
    kind: SOPStepKind,
    action_type: SOPActionType,
    title: str,
    instruction: str,
    expected_outcome: str = "",
    on_failure: str | None = None,
    required_inputs: tuple[str, ...] = (),
    expected_outputs: tuple[str, ...] = (),
    dependencies: tuple[str, ...] = (),
    critical: bool = False,
    optional: bool = False,
    timeout_seconds: int = 300,
    execution_hints: SOPExecutionHints | None = None,
) -> SOPStep:
    return SOPStep(
        step_number=step_number,
        step_id=step_id,
        kind=kind,
        action_type=action_type,
        title=title,
        instruction=instruction,
        expected_outcome=expected_outcome,
        on_failure=on_failure,
        required_inputs=required_inputs,
        expected_outputs=expected_outputs,
        dependencies=dependencies,
        critical=critical,
        optional=optional,
        timeout_seconds=timeout_seconds,
        execution_hints=execution_hints,
    )


def _sop(
    sop_id: str,
    title: str,
    topic: str,
    steps: tuple[SOPStep, ...],
    description: str,
    trigger_conditions: tuple[SOPTriggerCondition, ...] = (),
    escalation_threshold: float = 0.4,
    tags: tuple[str, ...] = (),
    estimated_resolution_minutes: int = 30,
    requires_approval: bool = False,
) -> SOPDocument:
    return SOPDocument(
        sop_id=sop_id,
        title=title,
        topic=topic,
        status=SOPStatus.ACTIVE,
        version="1.0",
        trigger_conditions=trigger_conditions,
        steps=steps,
        escalation_threshold=escalation_threshold,
        applicable_to=(),
        tags=tags,
        current_version=_V1,
        description=description,
        estimated_resolution_minutes=estimated_resolution_minutes,
        requires_approval=requires_approval,
        enabled=True,
        client_scope=(),
    )


# ── SOP 1: MINIMAL_INVESTIGATION ──────────────────────────────────────────────

def _build_minimal_investigation() -> SOPDocument:
    return _sop(
        sop_id="sop_minimal_investigation_v1",
        title="Minimal Investigation",
        topic="MINIMAL_INVESTIGATION",
        description=(
            "Generic single-step investigation procedure used as an absolute "
            "fallback when no topic-specific SOP is available. Collects all "
            "available context and records observations for manual review."
        ),
        tags=("fallback", "generic", "investigation"),
        estimated_resolution_minutes=10,
        steps=(
            _step(
                step_number=1,
                step_id="min_read_context",
                kind=SOPStepKind.READ,
                action_type=SOPActionType.INVESTIGATE,
                title="Read Available Context",
                instruction=(
                    "Read all available context from the ticket including subject, "
                    "description, customer email, and any attached files or logs. "
                    "Record the issue type and all relevant details for manual review."
                ),
                expected_outcome="All available context has been read and recorded.",
                required_inputs=("ticket_id",),
                expected_outputs=("context_summary",),
                critical=True,
                execution_hints=_HINTS_QUICK,
            ),
        ),
    )


# ── SOP 2: OTP_DELIVERY_FAILURE ───────────────────────────────────────────────

def _build_otp_delivery_failure() -> SOPDocument:
    return _sop(
        sop_id="sop_otp_delivery_failure_v1",
        title="OTP Delivery Failure Resolution",
        topic="OTP_DELIVERY_FAILURE",
        description=(
            "Diagnoses and resolves One-Time Password delivery failures. "
            "Verifies phone number validity, examines delivery logs, identifies "
            "failure patterns, and triggers a resend with confirmation."
        ),
        tags=("otp", "sms", "delivery", "phone"),
        estimated_resolution_minutes=15,
        steps=(
            _step(
                1, "otp_lookup_phone",
                SOPStepKind.LOOKUP, SOPActionType.INVESTIGATE,
                "Look Up Customer Phone Number",
                "Retrieve the customer's registered phone number from the user profile "
                "service using the customer_id from the ticket. Confirm the number is "
                "stored and not masked/redacted.",
                expected_outcome="Phone number retrieved and confirmed present.",
                required_inputs=("customer_id",),
                expected_outputs=("phone_number",),
                critical=True, execution_hints=_HINTS_QUICK,
            ),
            _step(
                2, "otp_verify_format",
                SOPStepKind.VERIFY, SOPActionType.VERIFY,
                "Verify Phone Number Format",
                "Verify the phone number is in E.164 international format (e.g., "
                "+91XXXXXXXXXX) and the country code matches the customer's "
                "registered country. Flag numbers with incorrect format.",
                expected_outcome="Phone number format validated.",
                required_inputs=("phone_number",),
                expected_outputs=("phone_format_valid",),
                dependencies=("otp_lookup_phone",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                3, "otp_search_logs",
                SOPStepKind.SEARCH, SOPActionType.INVESTIGATE,
                "Search OTP Delivery Logs",
                "Search the OTP delivery service logs for all delivery attempts "
                "for this phone number in the last 60 minutes. Collect the last "
                "5 attempts including timestamps, delivery status, and error codes.",
                expected_outcome="Recent OTP delivery attempts retrieved.",
                required_inputs=("phone_number",),
                expected_outputs=("delivery_logs", "last_error_code"),
                dependencies=("otp_lookup_phone",),
                critical=True, execution_hints=_HINTS_MEDIUM,
            ),
            _step(
                4, "otp_compare_errors",
                SOPStepKind.COMPARE, SOPActionType.INVESTIGATE,
                "Compare Error Codes Against Known Patterns",
                "Compare the error code from the delivery logs against the known "
                "failure patterns: 30003=invalid number, 30004=carrier blocked, "
                "30005=unknown, 30006=landline, 21211=invalid format. "
                "Identify the root cause category.",
                expected_outcome="Failure root cause category identified.",
                required_inputs=("last_error_code",),
                expected_outputs=("failure_category",),
                dependencies=("otp_search_logs",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                5, "otp_execute_resend",
                SOPStepKind.EXECUTE, SOPActionType.EXECUTE,
                "Trigger OTP Resend",
                "Trigger an OTP resend for the customer via the delivery provider "
                "API. Set retry priority=HIGH to bypass standard rate limiting. "
                "Record the new delivery SID for tracking.",
                expected_outcome="OTP resend triggered and SID recorded.",
                required_inputs=("customer_id", "phone_number"),
                expected_outputs=("delivery_sid",),
                dependencies=("otp_compare_errors",),
                critical=True, execution_hints=_HINTS_APPROVAL,
            ),
            _step(
                6, "otp_validate_delivery",
                SOPStepKind.VALIDATE, SOPActionType.VERIFY,
                "Validate OTP Delivery",
                "Poll the delivery service for the status of the new delivery SID "
                "for up to 60 seconds. Confirm delivery status is 'delivered'. "
                "If still pending after 60 seconds, escalate.",
                expected_outcome="OTP delivery confirmed as 'delivered'.",
                required_inputs=("delivery_sid",),
                expected_outputs=("delivery_confirmed",),
                dependencies=("otp_execute_resend",),
                on_failure="otp_escalate",
                execution_hints=SOPExecutionHints(estimated_seconds=60, can_be_automated=True),
            ),
            _step(
                7, "otp_note",
                SOPStepKind.NOTE, SOPActionType.DOCUMENT,
                "Document Resolution",
                "Record the OTP failure reason, resend action taken, delivery SID, "
                "and final delivery status in the case notes.",
                expected_outcome="Resolution documented in case notes.",
                required_inputs=("failure_category", "delivery_sid"),
                dependencies=("otp_validate_delivery",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                8, "otp_complete",
                SOPStepKind.COMPLETE, SOPActionType.COMMUNICATE,
                "Mark OTP Issue Resolved",
                "Mark the OTP delivery issue as resolved. Notify the customer that "
                "a new OTP has been sent and they should check their phone.",
                expected_outcome="Case marked resolved, customer notified.",
                dependencies=("otp_note",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                9, "otp_escalate",
                SOPStepKind.ESCALATE, SOPActionType.ESCALATE,
                "Escalate Persistent OTP Failure",
                "Escalate to L2 if OTP delivery cannot be confirmed after resend. "
                "Attach delivery logs, error codes, and SID to the escalation ticket.",
                expected_outcome="Escalation ticket created with delivery logs.",
                optional=True,
                execution_hints=_HINTS_MANUAL,
            ),
        ),
    )


# ── SOP 3: VKYC_SESSION_FAILURE ───────────────────────────────────────────────

def _build_vkyc_session_failure() -> SOPDocument:
    return _sop(
        sop_id="sop_vkyc_session_failure_v1",
        title="Video KYC Session Failure Resolution",
        topic="VKYC_SESSION_FAILURE",
        description=(
            "Diagnoses Video KYC session failures including connectivity, device "
            "compatibility, session expiry, and agent availability issues. "
            "Resolves by either fixing the root cause or scheduling a new session."
        ),
        tags=("vkyc", "video", "kyc", "session", "identity"),
        estimated_resolution_minutes=25,
        requires_approval=True,
        steps=(
            _step(
                1, "vkyc_lookup_session",
                SOPStepKind.LOOKUP, SOPActionType.INVESTIGATE,
                "Look Up Failed VKYC Session",
                "Retrieve the failed VKYC session record using the session_id from "
                "the ticket or the customer_id. Record session state, timestamp, "
                "agent_id, and all error codes.",
                expected_outcome="Session record retrieved with error codes.",
                required_inputs=("customer_id",),
                expected_outputs=("session_id", "session_state", "error_code"),
                critical=True, execution_hints=_HINTS_QUICK,
            ),
            _step(
                2, "vkyc_read_errors",
                SOPStepKind.READ, SOPActionType.INVESTIGATE,
                "Read Session Error Details",
                "Read full session error details: video quality metrics, network "
                "diagnostics, device type, browser, and connection type (WiFi/4G). "
                "Note if error occurred during agent join, document capture, or liveness.",
                expected_outcome="Full session error context collected.",
                required_inputs=("session_id",),
                expected_outputs=("error_details", "device_info"),
                dependencies=("vkyc_lookup_session",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                3, "vkyc_verify_permissions",
                SOPStepKind.VERIFY, SOPActionType.VERIFY,
                "Verify Device Permissions",
                "Verify that the customer's device had camera and microphone "
                "permissions granted. Check if browser permissions were blocked "
                "at OS level (iOS Settings or Android Permissions).",
                expected_outcome="Permission status confirmed.",
                required_inputs=("device_info",),
                expected_outputs=("permissions_granted",),
                dependencies=("vkyc_read_errors",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                4, "vkyc_search_compatibility",
                SOPStepKind.SEARCH, SOPActionType.INVESTIGATE,
                "Search Device and Browser Compatibility",
                "Search the VKYC compatibility matrix for the customer's device "
                "model and browser version. Identify any known compatibility issues "
                "or minimum requirements not met.",
                expected_outcome="Compatibility status determined.",
                required_inputs=("device_info",),
                expected_outputs=("compatibility_status",),
                dependencies=("vkyc_read_errors",),
                execution_hints=_HINTS_MEDIUM,
            ),
            _step(
                5, "vkyc_compare_requirements",
                SOPStepKind.COMPARE, SOPActionType.VERIFY,
                "Compare Session Parameters Against Requirements",
                "Compare network speed (require >2Mbps), browser version "
                "(Chrome 90+, Safari 14+), and resolution (>720p) against "
                "minimum VKYC requirements. Document which requirements were not met.",
                expected_outcome="Gaps between session parameters and requirements identified.",
                required_inputs=("error_details", "compatibility_status"),
                expected_outputs=("requirements_gap",),
                dependencies=("vkyc_verify_permissions", "vkyc_search_compatibility"),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                6, "vkyc_schedule_session",
                SOPStepKind.EXECUTE, SOPActionType.EXECUTE,
                "Schedule New VKYC Session",
                "Schedule a new VKYC session for the customer via the scheduling "
                "service. Set priority=URGENT. Send the session link via SMS and "
                "email. Attach the compatibility requirements in the invitation.",
                expected_outcome="New VKYC session scheduled and link sent.",
                required_inputs=("customer_id", "session_id"),
                expected_outputs=("new_session_id", "session_link"),
                dependencies=("vkyc_compare_requirements",),
                critical=True, execution_hints=_HINTS_APPROVAL,
                on_failure="vkyc_escalate",
            ),
            _step(
                7, "vkyc_validate_session",
                SOPStepKind.VALIDATE, SOPActionType.VERIFY,
                "Validate New Session Readiness",
                "Confirm the new VKYC session is in SCHEDULED state and the "
                "customer link is accessible. Verify the agent pool has "
                "available agents for the scheduled timeslot.",
                expected_outcome="New session confirmed scheduled and accessible.",
                required_inputs=("new_session_id",),
                expected_outputs=("session_ready",),
                dependencies=("vkyc_schedule_session",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                8, "vkyc_note",
                SOPStepKind.NOTE, SOPActionType.DOCUMENT,
                "Document VKYC Failure and Resolution",
                "Record: original session error, root cause, compatibility issues "
                "found, new session ID, and scheduled timeslot in case notes.",
                expected_outcome="VKYC failure and resolution documented.",
                required_inputs=("error_code", "new_session_id"),
                dependencies=("vkyc_validate_session",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                9, "vkyc_complete",
                SOPStepKind.COMPLETE, SOPActionType.COMMUNICATE,
                "Notify Customer of Rescheduled Session",
                "Notify the customer that their VKYC session has been rescheduled. "
                "Include the new session link, troubleshooting tips (use Chrome, "
                "ensure good lighting, stable WiFi), and support contact.",
                expected_outcome="Customer notified with new session details.",
                dependencies=("vkyc_note",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                10, "vkyc_escalate",
                SOPStepKind.ESCALATE, SOPActionType.ESCALATE,
                "Escalate VKYC Session Failure",
                "Escalate to L2 if new session scheduling fails or if the "
                "root cause cannot be determined from available evidence. "
                "Attach session logs, device info, and error codes.",
                expected_outcome="Escalation created with full session context.",
                optional=True,
                execution_hints=_HINTS_MANUAL,
            ),
        ),
    )


# ── SOP 4: DOCUMENT_OCR_FAILURE ───────────────────────────────────────────────

def _build_document_ocr_failure() -> SOPDocument:
    return _sop(
        sop_id="sop_document_ocr_failure_v1",
        title="Document OCR Failure Resolution",
        topic="DOCUMENT_OCR_FAILURE",
        description=(
            "Diagnoses and resolves document OCR processing failures. "
            "Checks document format, quality, and size. Retries OCR "
            "with enhanced preprocessing if quality meets minimum threshold."
        ),
        tags=("ocr", "document", "pdf", "image", "processing"),
        estimated_resolution_minutes=20,
        steps=(
            _step(
                1, "ocr_read_job",
                SOPStepKind.READ, SOPActionType.INVESTIGATE,
                "Read OCR Job Details",
                "Read the failed OCR job record: job_id, document_id, document_type, "
                "file_format, file_size_kb, timestamp, and error message from the "
                "document processing service.",
                expected_outcome="OCR job details retrieved.",
                required_inputs=("customer_id",),
                expected_outputs=("document_id", "ocr_error", "file_format"),
                critical=True, execution_hints=_HINTS_QUICK,
            ),
            _step(
                2, "ocr_verify_format",
                SOPStepKind.VERIFY, SOPActionType.VERIFY,
                "Verify Document Format",
                "Verify the document was uploaded in an accepted format: "
                "PDF (single or multi-page), JPEG, PNG, or TIFF. "
                "Reject HEIC, WebP, and BMP which are not supported by the OCR engine.",
                expected_outcome="Document format confirmed as accepted or rejected.",
                required_inputs=("file_format",),
                expected_outputs=("format_accepted",),
                dependencies=("ocr_read_job",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                3, "ocr_collect_quality",
                SOPStepKind.COLLECT, SOPActionType.INVESTIGATE,
                "Collect Document Quality Metrics",
                "Collect the OCR confidence score (overall and per-field), "
                "DPI (minimum 150 required), file size (must be ≤10MB), and "
                "whether the document is encrypted or password-protected.",
                expected_outcome="Quality metrics collected.",
                required_inputs=("document_id",),
                expected_outputs=("confidence_score", "dpi", "is_encrypted"),
                dependencies=("ocr_read_job",),
                execution_hints=_HINTS_MEDIUM,
            ),
            _step(
                4, "ocr_compare_quality",
                SOPStepKind.COMPARE, SOPActionType.VERIFY,
                "Compare Against OCR Quality Thresholds",
                "Compare document quality against requirements: confidence_score ≥ 0.60, "
                "DPI ≥ 150, file_size ≤ 10MB, not encrypted. "
                "Document which thresholds were not met.",
                expected_outcome="Quality gap analysis complete.",
                required_inputs=("confidence_score", "dpi", "is_encrypted"),
                expected_outputs=("quality_pass",),
                dependencies=("ocr_collect_quality",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                5, "ocr_retry",
                SOPStepKind.EXECUTE, SOPActionType.EXECUTE,
                "Retry OCR with Enhanced Preprocessing",
                "If quality_pass=True, retry OCR with enhanced preprocessing: "
                "auto-rotate, deskew, contrast enhancement, and denoising. "
                "Submit as priority=HIGH job to the OCR queue.",
                expected_outcome="OCR retry job submitted.",
                required_inputs=("document_id",),
                expected_outputs=("retry_job_id",),
                dependencies=("ocr_compare_quality",),
                critical=True, execution_hints=_HINTS_APPROVAL,
                on_failure="ocr_escalate",
            ),
            _step(
                6, "ocr_validate_result",
                SOPStepKind.VALIDATE, SOPActionType.VERIFY,
                "Validate OCR Retry Result",
                "Wait for the retry job to complete (max 120 seconds). "
                "Confirm the final confidence_score ≥ 0.60 and all required "
                "document fields were successfully extracted.",
                expected_outcome="OCR result validated with acceptable confidence.",
                required_inputs=("retry_job_id",),
                expected_outputs=("ocr_complete",),
                dependencies=("ocr_retry",),
                on_failure="ocr_escalate",
                execution_hints=SOPExecutionHints(estimated_seconds=120, can_be_automated=True),
            ),
            _step(
                7, "ocr_note",
                SOPStepKind.NOTE, SOPActionType.DOCUMENT,
                "Document OCR Failure and Resolution",
                "Record: original error, quality metrics, retry outcome, and "
                "final confidence score in the case notes.",
                expected_outcome="OCR failure and resolution documented.",
                required_inputs=("ocr_error", "confidence_score"),
                dependencies=("ocr_validate_result",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                8, "ocr_complete",
                SOPStepKind.COMPLETE, SOPActionType.COMMUNICATE,
                "Mark Document Processing Complete",
                "Mark the document as processed. Notify the customer that "
                "their document has been successfully processed and their "
                "application can proceed.",
                expected_outcome="Case resolved, customer notified.",
                dependencies=("ocr_note",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                9, "ocr_escalate",
                SOPStepKind.ESCALATE, SOPActionType.ESCALATE,
                "Escalate Document OCR Failure",
                "Escalate if document format is unsupported, quality is below "
                "threshold and cannot be improved, or retry fails. "
                "Attach the document metadata and OCR logs to the escalation.",
                expected_outcome="Escalation created with document and OCR details.",
                optional=True, execution_hints=_HINTS_MANUAL,
            ),
        ),
    )


# ── SOP 5: API_CALLBACK_FAILURE ───────────────────────────────────────────────

def _build_api_callback_failure() -> SOPDocument:
    return _sop(
        sop_id="sop_api_callback_failure_v1",
        title="API Callback Failure Resolution",
        topic="API_CALLBACK_FAILURE",
        description=(
            "Diagnoses and resolves API webhook/callback delivery failures. "
            "Checks endpoint reachability, HTTP response codes, failure patterns, "
            "and retries with exponential backoff."
        ),
        tags=("api", "callback", "webhook", "http", "delivery"),
        estimated_resolution_minutes=20,
        steps=(
            _step(
                1, "cb_read_event",
                SOPStepKind.READ, SOPActionType.INVESTIGATE,
                "Read Failed Callback Event",
                "Read the failed callback event details: event_id, endpoint_url, "
                "HTTP method, request payload, HTTP response code, response body, "
                "and all retry attempts with timestamps.",
                expected_outcome="Callback event details retrieved.",
                required_inputs=("customer_id",),
                expected_outputs=("event_id", "endpoint_url", "http_status"),
                critical=True, execution_hints=_HINTS_QUICK,
            ),
            _step(
                2, "cb_verify_endpoint",
                SOPStepKind.VERIFY, SOPActionType.VERIFY,
                "Verify Endpoint Reachability",
                "Perform a health check ping to the callback endpoint URL. "
                "Confirm it responds within 5 seconds. Check DNS resolution "
                "and TLS certificate validity.",
                expected_outcome="Endpoint reachability confirmed or DNS/TLS issue identified.",
                required_inputs=("endpoint_url",),
                expected_outputs=("endpoint_reachable",),
                dependencies=("cb_read_event",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                3, "cb_collect_logs",
                SOPStepKind.COLLECT, SOPActionType.INVESTIGATE,
                "Collect Callback Attempt Logs",
                "Retrieve all callback attempt logs for this event_id and endpoint "
                "in the last 24 hours. Collect HTTP status codes, response times, "
                "and retry intervals.",
                expected_outcome="Full callback attempt history retrieved.",
                required_inputs=("event_id", "endpoint_url"),
                expected_outputs=("attempt_log",),
                dependencies=("cb_read_event",),
                execution_hints=_HINTS_MEDIUM,
            ),
            _step(
                4, "cb_search_pattern",
                SOPStepKind.SEARCH, SOPActionType.INVESTIGATE,
                "Search for Systemic Failure Pattern",
                "Search callback logs for other endpoints experiencing similar "
                "failures in the same 24-hour window. Determine if failure is "
                "isolated to this endpoint or affects multiple clients.",
                expected_outcome="Failure pattern classified as isolated or systemic.",
                required_inputs=("http_status",),
                expected_outputs=("failure_scope",),
                dependencies=("cb_collect_logs",),
                execution_hints=_HINTS_MEDIUM,
            ),
            _step(
                5, "cb_retry",
                SOPStepKind.EXECUTE, SOPActionType.EXECUTE,
                "Retry Callback with Exponential Backoff",
                "Trigger a manual retry of the callback event with exponential "
                "backoff: attempt 1 immediately, attempt 2 after 60s, attempt 3 "
                "after 300s. Use the same payload and headers as the original attempt.",
                expected_outcome="Retry sequence initiated.",
                required_inputs=("event_id",),
                expected_outputs=("retry_id",),
                dependencies=("cb_verify_endpoint",),
                critical=True, execution_hints=_HINTS_APPROVAL,
                on_failure="cb_escalate",
            ),
            _step(
                6, "cb_validate",
                SOPStepKind.VALIDATE, SOPActionType.VERIFY,
                "Validate Callback Acknowledgement",
                "Confirm the receiving endpoint returned HTTP 200 with an "
                "acknowledgement payload. Check for idempotency key confirmation "
                "to prevent duplicate processing.",
                expected_outcome="Callback delivery acknowledged by endpoint.",
                required_inputs=("retry_id",),
                expected_outputs=("callback_acked",),
                dependencies=("cb_retry",),
                on_failure="cb_escalate",
                execution_hints=SOPExecutionHints(estimated_seconds=360, can_be_automated=True),
            ),
            _step(
                7, "cb_note",
                SOPStepKind.NOTE, SOPActionType.DOCUMENT,
                "Document Callback Failure and Resolution",
                "Record the failure reason, failure scope, retry outcome, "
                "and final HTTP status in case notes.",
                expected_outcome="Resolution documented.",
                required_inputs=("event_id", "http_status"),
                dependencies=("cb_validate",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                8, "cb_complete",
                SOPStepKind.COMPLETE, SOPActionType.COMMUNICATE,
                "Mark Callback Issue Resolved",
                "Mark the callback issue as resolved. If systemic, notify the "
                "client's technical contact of the failure pattern and resolution.",
                expected_outcome="Case resolved.",
                dependencies=("cb_note",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                9, "cb_escalate",
                SOPStepKind.ESCALATE, SOPActionType.ESCALATE,
                "Escalate Callback Failure",
                "Escalate if endpoint is unreachable, retry fails, or failure is "
                "systemic across multiple clients. Attach all attempt logs.",
                expected_outcome="Escalation created.",
                optional=True, execution_hints=_HINTS_MANUAL,
            ),
        ),
    )


# ── SOP 6: AGENT_PORTAL_ISSUE ─────────────────────────────────────────────────

def _build_agent_portal_issue() -> SOPDocument:
    return _sop(
        sop_id="sop_agent_portal_issue_v1",
        title="Agent Portal Issue Resolution",
        topic="AGENT_PORTAL_ISSUE",
        description=(
            "Diagnoses and resolves agent portal access issues including login "
            "failures, session problems, permission errors, and missing modules. "
            "Restores agent access and confirms operational capability."
        ),
        tags=("portal", "agent", "access", "session", "permissions"),
        estimated_resolution_minutes=20,
        steps=(
            _step(
                1, "portal_lookup_agent",
                SOPStepKind.LOOKUP, SOPActionType.INVESTIGATE,
                "Look Up Agent Account",
                "Retrieve the agent's account profile using agent_id or email. "
                "Confirm the account exists, is active, and has the correct role "
                "assignments (agent, supervisor, or admin).",
                expected_outcome="Agent account profile retrieved.",
                required_inputs=("customer_id",),
                expected_outputs=("agent_id", "role", "account_status"),
                critical=True, execution_hints=_HINTS_QUICK,
            ),
            _step(
                2, "portal_read_session",
                SOPStepKind.READ, SOPActionType.INVESTIGATE,
                "Read Current Portal Session State",
                "Read the agent's current portal session state. Check for active "
                "sessions, session age, and any error events in the last session log.",
                expected_outcome="Portal session state retrieved.",
                required_inputs=("agent_id",),
                expected_outputs=("session_state", "last_error"),
                dependencies=("portal_lookup_agent",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                3, "portal_verify_permissions",
                SOPStepKind.VERIFY, SOPActionType.VERIFY,
                "Verify Role and Module Permissions",
                "Verify the agent has the required role permissions for the modules "
                "they reported as inaccessible. Compare assigned permissions against "
                "the expected permission set for their role.",
                expected_outcome="Permission gaps identified or confirmed adequate.",
                required_inputs=("agent_id", "role"),
                expected_outputs=("permissions_valid",),
                dependencies=("portal_lookup_agent",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                4, "portal_search_maintenance",
                SOPStepKind.SEARCH, SOPActionType.INVESTIGATE,
                "Search for Recent Portal Changes",
                "Search the portal change log for any deployments, config changes, "
                "or maintenance windows in the last 48 hours that could affect "
                "agent access.",
                expected_outcome="Recent portal changes reviewed for impact.",
                required_inputs=(),
                expected_outputs=("recent_changes",),
                dependencies=("portal_read_session",),
                optional=True, execution_hints=_HINTS_MEDIUM,
            ),
            _step(
                5, "portal_reset_session",
                SOPStepKind.EXECUTE, SOPActionType.EXECUTE,
                "Reset Agent Portal Session",
                "Terminate all active sessions for the agent and issue a new session "
                "token. Clear the session cache. Send the agent a password reset "
                "link if account lockout is suspected.",
                expected_outcome="Agent session reset, new token issued.",
                required_inputs=("agent_id",),
                expected_outputs=("reset_complete",),
                dependencies=("portal_verify_permissions",),
                critical=True, execution_hints=_HINTS_APPROVAL,
                on_failure="portal_escalate",
            ),
            _step(
                6, "portal_validate_access",
                SOPStepKind.VALIDATE, SOPActionType.VERIFY,
                "Validate Agent Can Access Portal",
                "Instruct the agent to attempt login. Confirm they can access "
                "the portal and reach all previously inaccessible modules. "
                "Ask the agent to confirm 2FA is working.",
                expected_outcome="Agent confirms portal access is restored.",
                required_inputs=("reset_complete",),
                expected_outputs=("access_confirmed",),
                dependencies=("portal_reset_session",),
                on_failure="portal_escalate",
                execution_hints=_HINTS_MANUAL,
            ),
            _step(
                7, "portal_note",
                SOPStepKind.NOTE, SOPActionType.DOCUMENT,
                "Document Portal Issue and Resolution",
                "Record: root cause of portal issue, actions taken, session reset "
                "confirmation, and agent confirmation of access restoration.",
                expected_outcome="Issue and resolution documented.",
                required_inputs=("last_error",),
                dependencies=("portal_validate_access",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                8, "portal_complete",
                SOPStepKind.COMPLETE, SOPActionType.COMMUNICATE,
                "Confirm Agent Has Resumed Operations",
                "Confirm with the agent they have resumed normal operations. "
                "Close the portal access issue case.",
                expected_outcome="Agent confirmed operational, case closed.",
                dependencies=("portal_note",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                9, "portal_escalate",
                SOPStepKind.ESCALATE, SOPActionType.ESCALATE,
                "Escalate Portal Access Issue",
                "Escalate if session reset fails or agent cannot access portal "
                "after reset. Attach session logs and permission audit report.",
                expected_outcome="Escalation created with portal logs.",
                optional=True, execution_hints=_HINTS_MANUAL,
            ),
        ),
    )


# ── SOP 7: SESSION_FAILURE ────────────────────────────────────────────────────

def _build_session_failure() -> SOPDocument:
    return _sop(
        sop_id="sop_session_failure_v1",
        title="User Session Failure Resolution",
        topic="SESSION_FAILURE",
        description=(
            "Diagnoses and resolves user session failures including expired tokens, "
            "concurrent session conflicts, and session service errors. "
            "Invalidates stale sessions and issues fresh session tokens."
        ),
        tags=("session", "token", "authentication", "login"),
        estimated_resolution_minutes=15,
        steps=(
            _step(
                1, "sess_read_state",
                SOPStepKind.READ, SOPActionType.INVESTIGATE,
                "Read Session State",
                "Read the current session state for the customer: session_id, "
                "created_at, last_active_at, expiry_time, and error_type. "
                "Confirm session service is operational.",
                expected_outcome="Session state and error type retrieved.",
                required_inputs=("customer_id",),
                expected_outputs=("session_id", "error_type"),
                critical=True, execution_hints=_HINTS_QUICK,
            ),
            _step(
                2, "sess_lookup_active",
                SOPStepKind.LOOKUP, SOPActionType.INVESTIGATE,
                "Look Up All Active Sessions",
                "List all active sessions for this customer_id. Identify any "
                "concurrent sessions from multiple devices that may be causing "
                "conflict or exceeding the concurrent session limit.",
                expected_outcome="All active sessions for customer listed.",
                required_inputs=("customer_id",),
                expected_outputs=("active_session_count",),
                dependencies=("sess_read_state",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                3, "sess_verify_token",
                SOPStepKind.VERIFY, SOPActionType.VERIFY,
                "Verify Session Token Validity",
                "Verify the customer's current session token: check signature, "
                "expiry timestamp, and issuer. Confirm whether token is expired, "
                "revoked, or malformed.",
                expected_outcome="Token validity status confirmed.",
                required_inputs=("session_id",),
                expected_outputs=("token_valid", "token_error"),
                dependencies=("sess_read_state",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                4, "sess_collect_events",
                SOPStepKind.COLLECT, SOPActionType.INVESTIGATE,
                "Collect Session Error Events",
                "Collect session service error events for this customer in the "
                "last 2 hours: error codes, device types, and client versions.",
                expected_outcome="Session error events collected.",
                required_inputs=("customer_id",),
                expected_outputs=("error_events",),
                dependencies=("sess_read_state",),
                execution_hints=_HINTS_MEDIUM,
            ),
            _step(
                5, "sess_invalidate",
                SOPStepKind.EXECUTE, SOPActionType.EXECUTE,
                "Invalidate Stale Sessions and Issue New Token",
                "Invalidate all stale or conflicting sessions for the customer. "
                "Issue a fresh session token with standard expiry (8 hours). "
                "Do NOT reset password — only the session is refreshed.",
                expected_outcome="Stale sessions invalidated, new token issued.",
                required_inputs=("customer_id", "session_id"),
                expected_outputs=("new_session_id",),
                dependencies=("sess_verify_token",),
                critical=True, execution_hints=_HINTS_APPROVAL,
                on_failure="sess_escalate",
            ),
            _step(
                6, "sess_validate",
                SOPStepKind.VALIDATE, SOPActionType.VERIFY,
                "Validate New Session is Accessible",
                "Confirm the new session_id is in ACTIVE state and the customer "
                "can authenticate successfully. Run a test authentication request.",
                expected_outcome="New session validated as accessible.",
                required_inputs=("new_session_id",),
                expected_outputs=("session_restored",),
                dependencies=("sess_invalidate",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                7, "sess_note",
                SOPStepKind.NOTE, SOPActionType.DOCUMENT,
                "Document Session Failure",
                "Record error type, token validity issue, sessions invalidated, "
                "and new session ID in case notes.",
                expected_outcome="Session failure documented.",
                required_inputs=("error_type", "new_session_id"),
                dependencies=("sess_validate",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                8, "sess_complete",
                SOPStepKind.COMPLETE, SOPActionType.COMMUNICATE,
                "Confirm User Session Restored",
                "Confirm the customer's session is restored. Notify the customer "
                "they may need to log in again on their device.",
                expected_outcome="Customer notified, case closed.",
                dependencies=("sess_note",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                9, "sess_escalate",
                SOPStepKind.ESCALATE, SOPActionType.ESCALATE,
                "Escalate Session Failure",
                "Escalate if session service is unresponsive, invalidation fails, "
                "or issue affects multiple customers (systemic session service failure).",
                expected_outcome="Escalation created with session service logs.",
                optional=True, execution_hints=_HINTS_MANUAL,
            ),
        ),
    )


# ── SOP 8: DOCUMENT_UPLOAD_FAILURE ────────────────────────────────────────────

def _build_document_upload_failure() -> SOPDocument:
    return _sop(
        sop_id="sop_document_upload_failure_v1",
        title="Document Upload Failure Resolution",
        topic="DOCUMENT_UPLOAD_FAILURE",
        description=(
            "Diagnoses and resolves document upload failures including file size "
            "limits, format restrictions, storage errors, and network timeouts. "
            "Retries the upload with corrected parameters."
        ),
        tags=("upload", "document", "storage", "file"),
        estimated_resolution_minutes=15,
        steps=(
            _step(
                1, "upload_read_failure",
                SOPStepKind.READ, SOPActionType.INVESTIGATE,
                "Read Upload Failure Details",
                "Read the upload failure event: document_id, file_name, file_size_kb, "
                "file_type, upload_endpoint, HTTP status, and error message from "
                "the document upload service.",
                expected_outcome="Upload failure details retrieved.",
                required_inputs=("customer_id",),
                expected_outputs=("document_id", "upload_error", "file_type"),
                critical=True, execution_hints=_HINTS_QUICK,
            ),
            _step(
                2, "upload_verify_size",
                SOPStepKind.VERIFY, SOPActionType.VERIFY,
                "Verify File Size Within Limits",
                "Verify file_size_kb is within the allowed limit: 10MB for "
                "images and 25MB for PDFs. Flag if oversized and advise customer "
                "to compress or split the document.",
                expected_outcome="File size compliance confirmed.",
                required_inputs=("file_type",),
                expected_outputs=("size_compliant",),
                dependencies=("upload_read_failure",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                3, "upload_collect_logs",
                SOPStepKind.COLLECT, SOPActionType.INVESTIGATE,
                "Collect Upload Attempt Logs",
                "Collect upload service logs for this document_id and customer_id "
                "in the last 4 hours. Retrieve HTTP response codes, timeout "
                "events, and storage backend responses.",
                expected_outcome="Upload attempt logs collected.",
                required_inputs=("document_id",),
                expected_outputs=("upload_logs",),
                dependencies=("upload_read_failure",),
                execution_hints=_HINTS_MEDIUM,
            ),
            _step(
                4, "upload_compare_type",
                SOPStepKind.COMPARE, SOPActionType.VERIFY,
                "Compare File Type Against Allowed Types",
                "Compare file_type against the allowed document types: "
                "PDF, JPEG, PNG, TIFF for ID documents; PDF only for "
                "income/address proof. Reject HEIC, BMP, GIF, WebP.",
                expected_outcome="File type compliance confirmed.",
                required_inputs=("file_type",),
                expected_outputs=("type_compliant",),
                dependencies=("upload_collect_logs",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                5, "upload_retry",
                SOPStepKind.EXECUTE, SOPActionType.EXECUTE,
                "Retry Document Upload",
                "If size_compliant and type_compliant are both True, trigger a "
                "server-side re-upload of the document to the storage backend. "
                "Use multipart upload if size > 5MB.",
                expected_outcome="Document upload retry initiated.",
                required_inputs=("document_id",),
                expected_outputs=("retry_upload_id",),
                dependencies=("upload_compare_type", "upload_verify_size"),
                critical=True, execution_hints=_HINTS_APPROVAL,
                on_failure="upload_escalate",
            ),
            _step(
                6, "upload_validate",
                SOPStepKind.VALIDATE, SOPActionType.VERIFY,
                "Validate Document Stored and Accessible",
                "Confirm the document is stored at the expected storage path "
                "and the download URL is accessible. Verify document integrity "
                "via SHA-256 checksum if available.",
                expected_outcome="Document confirmed stored and accessible.",
                required_inputs=("retry_upload_id",),
                expected_outputs=("upload_confirmed",),
                dependencies=("upload_retry",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                7, "upload_note",
                SOPStepKind.NOTE, SOPActionType.DOCUMENT,
                "Document Upload Failure and Resolution",
                "Record: failure reason, compliance checks, retry outcome, "
                "and storage confirmation in case notes.",
                expected_outcome="Upload failure and resolution documented.",
                required_inputs=("upload_error",),
                dependencies=("upload_validate",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                8, "upload_complete",
                SOPStepKind.COMPLETE, SOPActionType.COMMUNICATE,
                "Mark Document Upload Resolved",
                "Mark the upload issue as resolved. Notify the customer "
                "their document has been successfully received.",
                expected_outcome="Customer notified, case closed.",
                dependencies=("upload_note",),
                execution_hints=_HINTS_QUICK,
            ),
            _step(
                9, "upload_escalate",
                SOPStepKind.ESCALATE, SOPActionType.ESCALATE,
                "Escalate Document Upload Failure",
                "Escalate if file is compliant but storage write fails, or if "
                "storage service is unresponsive. Attach upload logs.",
                expected_outcome="Escalation created with upload logs.",
                optional=True, execution_hints=_HINTS_MANUAL,
            ),
        ),
    )


# ── SOP 9: UNKNOWN_ISSUE ──────────────────────────────────────────────────────

def _build_unknown_issue() -> SOPDocument:
    return _sop(
        sop_id="sop_unknown_issue_v1",
        title="Unknown Issue Triage and Escalation",
        topic="UNKNOWN_ISSUE",
        description=(
            "Generic triage procedure for issues that do not match a known topic. "
            "Collects all available evidence, searches the knowledge base, "
            "documents findings, and escalates to L2 engineering."
        ),
        tags=("unknown", "triage", "escalation", "generic"),
        estimated_resolution_minutes=15,
        escalation_threshold=0.3,
        steps=(
            _step(
                1, "unk_read_context",
                SOPStepKind.READ, SOPActionType.INVESTIGATE,
                "Read All Available Context",
                "Read the full ticket: subject, description, customer history, "
                "attachments, and any error messages or screenshots provided. "
                "Record everything without filtering.",
                expected_outcome="All ticket context read and recorded.",
                required_inputs=("customer_id",),
                expected_outputs=("issue_summary",),
                critical=True, execution_hints=_HINTS_QUICK,
            ),
            _step(
                2, "unk_collect_evidence",
                SOPStepKind.COLLECT, SOPActionType.INVESTIGATE,
                "Collect All Available Evidence",
                "Collect customer account status, recent transactions or activity, "
                "error logs associated with the customer_id, and any error codes "
                "mentioned in the ticket.",
                expected_outcome="Available evidence collected.",
                required_inputs=("customer_id",),
                expected_outputs=("evidence_bundle",),
                dependencies=("unk_read_context",),
                execution_hints=_HINTS_MEDIUM,
            ),
            _step(
                3, "unk_search_kb",
                SOPStepKind.SEARCH, SOPActionType.INVESTIGATE,
                "Search Knowledge Base",
                "Search the knowledge base for similar issues using keywords from "
                "the ticket description. Look for matching error codes, symptoms, "
                "or resolution patterns.",
                expected_outcome="Knowledge base search complete.",
                required_inputs=("issue_summary",),
                expected_outputs=("kb_matches",),
                dependencies=("unk_read_context",),
                optional=True, execution_hints=_HINTS_MEDIUM,
            ),
            _step(
                4, "unk_note",
                SOPStepKind.NOTE, SOPActionType.DOCUMENT,
                "Document Issue Details for Engineering Review",
                "Write a structured note with: issue symptoms, evidence collected, "
                "knowledge base results, customer impact, and priority assessment. "
                "This note will be attached to the engineering escalation.",
                expected_outcome="Structured issue summary documented.",
                required_inputs=("evidence_bundle",),
                expected_outputs=("escalation_note",),
                dependencies=("unk_collect_evidence",),
                execution_hints=_HINTS_MANUAL,
            ),
            _step(
                5, "unk_escalate",
                SOPStepKind.ESCALATE, SOPActionType.ESCALATE,
                "Escalate to L2 Engineering",
                "Escalate the unknown issue to L2 engineering with the structured "
                "note, all collected evidence, and customer details. "
                "Set priority based on customer impact: CRITICAL, HIGH, or NORMAL.",
                expected_outcome="Escalation ticket created with full context.",
                required_inputs=("escalation_note",),
                expected_outputs=("escalation_id",),
                dependencies=("unk_note",),
                critical=True, execution_hints=_HINTS_MANUAL,
            ),
        ),
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def build_defaults() -> list[SOPDocument]:
    """
    Build and return all 9 default SOPDocuments.

    Returns:
        List of 9 SOPDocument instances, all ACTIVE and globally scoped.
    """
    return [
        _build_minimal_investigation(),
        _build_otp_delivery_failure(),
        _build_vkyc_session_failure(),
        _build_document_ocr_failure(),
        _build_api_callback_failure(),
        _build_agent_portal_issue(),
        _build_session_failure(),
        _build_document_upload_failure(),
        _build_unknown_issue(),
    ]


def get_minimal_sop() -> SOPDocument:
    """Return the MINIMAL_INVESTIGATION SOPDocument (for use as a fallback)."""
    return _build_minimal_investigation()
