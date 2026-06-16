"""
case_engine/tools/mock_tools.py

Sprint 2.17: Mock Investigation Tools.

These tools return deterministic fake data for architecture validation.
They prove the tool → workflow → resume integration path works correctly.

IMPORTANT: These are placeholders only. Real KwikID API integrations will
replace these implementations in a future sprint. The interface (BaseTool,
ToolDefinition, run()) will remain stable.

Tools:
  GetSessionDetailsTool    — session metadata for a VKYC session
  GetUserDetailsTool       — user profile and KYC status
  GetFailureReasonTool     — backend failure log for a failed operation
  GetCaseHistoryTool       — prior support cases for a user
  GetOnboardingStatusTool  — current onboarding stage and completion percentage
"""
from __future__ import annotations

from typing import Any

from case_engine.tools.tool_executor import BaseTool
from case_engine.tools.tool_models import ToolDefinition


class GetSessionDetailsTool(BaseTool):
    """Returns fake session metadata for VKYC session investigation."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_name="GetSessionDetailsTool",
            description=(
                "Retrieve session metadata for a VKYC session. "
                "Returns session status, creation time, attempt count, and expiry."
            ),
            required_inputs=("session_id",),
            output_schema={
                "session_id":      "str — the queried session ID",
                "status":          "str — ACTIVE | EXPIRED | FAILED | RESET",
                "created_at":      "str — ISO timestamp of session creation",
                "expires_at":      "str — ISO timestamp of session expiry",
                "attempt_count":   "int — number of VKYC attempts made",
                "failure_code":    "str | null — last failure code if status is FAILED",
                "can_reset":       "bool — whether session can be automatically reset",
            },
            version="1.0",
            tags=("vkyc", "session"),
        )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        session_id = inputs["session_id"]
        return {
            "session_id":    session_id,
            "status":        "FAILED",
            "created_at":    "2026-06-08T08:00:00+00:00",
            "expires_at":    "2026-06-08T20:00:00+00:00",
            "attempt_count": 3,
            "failure_code":  "SESSION_TIMEOUT",
            "can_reset":     True,
        }


class GetUserDetailsTool(BaseTool):
    """Returns fake user profile and KYC status for investigation."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_name="GetUserDetailsTool",
            description=(
                "Retrieve user profile including KYC status and registration details. "
                "Used to verify user identity before taking automated actions."
            ),
            required_inputs=("phone_number",),
            output_schema={
                "user_id":           "str — internal user identifier",
                "phone_number":      "str — masked phone number",
                "kyc_status":        "str — PENDING | PARTIAL | COMPLETE | REJECTED",
                "registration_date": "str — ISO timestamp of account creation",
                "account_active":    "bool — whether the user account is active",
                "risk_tier":         "str — LOW | MEDIUM | HIGH",
            },
            version="1.0",
            tags=("user", "kyc"),
        )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        phone = inputs["phone_number"]
        return {
            "user_id":           "USR-MOCK-0001",
            "phone_number":      phone[-4:].rjust(len(phone), "*"),
            "kyc_status":        "PARTIAL",
            "registration_date": "2026-01-15T10:00:00+00:00",
            "account_active":    True,
            "risk_tier":         "LOW",
        }


class GetFailureReasonTool(BaseTool):
    """Returns fake failure reason from backend audit logs."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_name="GetFailureReasonTool",
            description=(
                "Retrieve the root cause failure reason from backend operation logs. "
                "Used to understand why an OTP, VKYC, document check, or API call failed."
            ),
            required_inputs=("operation_id",),
            output_schema={
                "operation_id":    "str — the queried operation ID",
                "failure_category": "str — NETWORK | TIMEOUT | VALIDATION | QUOTA | UNKNOWN",
                "failure_code":    "str — specific failure code from backend",
                "failure_message": "str — human-readable failure description",
                "is_transient":    "bool — whether failure is likely transient (safe to retry)",
                "recommended_action": "str — RETRY | ESCALATE | MANUAL_REVIEW",
            },
            version="1.0",
            tags=("diagnostics", "failure_analysis"),
        )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        operation_id = inputs["operation_id"]
        return {
            "operation_id":       operation_id,
            "failure_category":   "TIMEOUT",
            "failure_code":       "BACKEND_TIMEOUT_5000MS",
            "failure_message":    "Backend service did not respond within 5000ms threshold",
            "is_transient":       True,
            "recommended_action": "RETRY",
        }


class GetCaseHistoryTool(BaseTool):
    """Returns fake prior support case history for a user."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_name="GetCaseHistoryTool",
            description=(
                "Retrieve the recent support case history for a user. "
                "Used to detect repeat issues and assess escalation risk."
            ),
            required_inputs=("phone_number",),
            output_schema={
                "phone_number":   "str — phone used to look up history",
                "case_count":     "int — total number of prior cases",
                "recent_cases":   "list[dict] — last 5 cases (case_id, topic, state, created_at)",
                "repeat_topic":   "str | null — topic that appears most often",
                "escalation_rate": "float — fraction of prior cases escalated",
            },
            version="1.0",
            tags=("case_history", "user"),
        )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        phone = inputs["phone_number"]
        return {
            "phone_number":   phone,
            "case_count":     2,
            "recent_cases": [
                {
                    "case_id":    "MOCK-CASE-001",
                    "topic":      "VKYC_Session_Failure",
                    "state":      "RESOLVED",
                    "created_at": "2026-05-20T09:00:00+00:00",
                },
            ],
            "repeat_topic":    "VKYC_Session_Failure",
            "escalation_rate": 0.0,
        }


class GetOnboardingStatusTool(BaseTool):
    """Returns fake onboarding stage and completion percentage."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_name="GetOnboardingStatusTool",
            description=(
                "Retrieve the current onboarding stage and completion status for a user. "
                "Used to understand where the user is in the KwikID onboarding funnel."
            ),
            required_inputs=("application_id",),
            output_schema={
                "application_id":    "str — the queried application ID",
                "onboarding_stage":  "str — REGISTRATION | OTP_VERIFY | KYC | VKYC | COMPLETE",
                "completion_pct":    "int — 0–100 percentage complete",
                "blocking_step":     "str | null — current step blocking progression",
                "can_auto_advance":  "bool — whether system can advance without user input",
                "last_activity_at":  "str — ISO timestamp of last user action",
            },
            version="1.0",
            tags=("onboarding", "kyc"),
        )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        application_id = inputs["application_id"]
        return {
            "application_id":   application_id,
            "onboarding_stage": "VKYC",
            "completion_pct":   75,
            "blocking_step":    "vkyc_session_completion",
            "can_auto_advance": False,
            "last_activity_at": "2026-06-08T07:30:00+00:00",
        }
