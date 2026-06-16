"""
tests/test_sprint217_mock_tools.py

Sprint 2.17 Part C — Mock Investigation Tools Tests

Verifies that each of the 5 mock tools:
  - Has a correct ToolDefinition (tool_name, required_inputs, output_schema)
  - Returns deterministic fake data with expected keys
  - Is registered in the default ToolRegistry
  - Can be invoked via ToolExecutor
"""
from __future__ import annotations

import pytest

from case_engine.tools.mock_tools import (
    GetCaseHistoryTool,
    GetFailureReasonTool,
    GetOnboardingStatusTool,
    GetSessionDetailsTool,
    GetUserDetailsTool,
)
from case_engine.tools.tool_executor import ToolExecutor
from case_engine.tools.tool_registry import ToolRegistry


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def default_registry() -> ToolRegistry:
    return ToolRegistry.build_default()


@pytest.fixture
def executor(default_registry) -> ToolExecutor:
    return ToolExecutor(default_registry)


# ── GetSessionDetailsTool ─────────────────────────────────────────────────────

class TestGetSessionDetailsTool:
    def setup_method(self):
        self.tool = GetSessionDetailsTool()

    def test_tool_name(self):
        assert self.tool.definition.tool_name == "GetSessionDetailsTool"

    def test_required_inputs(self):
        assert "session_id" in self.tool.definition.required_inputs

    def test_output_schema_has_expected_keys(self):
        schema = self.tool.definition.output_schema
        for key in ("session_id", "status", "created_at", "expires_at", "attempt_count", "can_reset"):
            assert key in schema, f"Missing key in output_schema: {key}"

    def test_run_returns_expected_structure(self):
        result = self.tool.run({"session_id": "KID-AB12CD34"})
        assert result["session_id"] == "KID-AB12CD34"
        assert result["status"] in ("ACTIVE", "EXPIRED", "FAILED", "RESET")
        assert isinstance(result["attempt_count"], int)
        assert isinstance(result["can_reset"], bool)

    def test_run_via_executor(self, executor):
        result = executor.execute("GetSessionDetailsTool", {"session_id": "KID-XYZ"})
        assert result.success is True
        assert result.payload["session_id"] == "KID-XYZ"

    def test_has_vkyc_tag(self):
        assert "vkyc" in self.tool.definition.tags or "session" in self.tool.definition.tags


# ── GetUserDetailsTool ────────────────────────────────────────────────────────

class TestGetUserDetailsTool:
    def setup_method(self):
        self.tool = GetUserDetailsTool()

    def test_tool_name(self):
        assert self.tool.definition.tool_name == "GetUserDetailsTool"

    def test_required_inputs(self):
        assert "phone_number" in self.tool.definition.required_inputs

    def test_output_schema_has_expected_keys(self):
        schema = self.tool.definition.output_schema
        for key in ("user_id", "kyc_status", "account_active", "risk_tier"):
            assert key in schema

    def test_run_returns_expected_structure(self):
        result = self.tool.run({"phone_number": "+91-9876543210"})
        assert "user_id" in result
        assert result["kyc_status"] in ("PENDING", "PARTIAL", "COMPLETE", "REJECTED")
        assert isinstance(result["account_active"], bool)
        assert result["risk_tier"] in ("LOW", "MEDIUM", "HIGH")

    def test_run_masks_phone_number(self):
        result = self.tool.run({"phone_number": "+91-9876543210"})
        # Phone should be partially masked
        assert "*" in result.get("phone_number", "")

    def test_run_via_executor(self, executor):
        result = executor.execute("GetUserDetailsTool", {"phone_number": "+91-1234567890"})
        assert result.success is True
        assert "user_id" in result.payload


# ── GetFailureReasonTool ──────────────────────────────────────────────────────

class TestGetFailureReasonTool:
    def setup_method(self):
        self.tool = GetFailureReasonTool()

    def test_tool_name(self):
        assert self.tool.definition.tool_name == "GetFailureReasonTool"

    def test_required_inputs(self):
        assert "operation_id" in self.tool.definition.required_inputs

    def test_output_schema_has_expected_keys(self):
        schema = self.tool.definition.output_schema
        for key in ("failure_category", "failure_code", "is_transient", "recommended_action"):
            assert key in schema

    def test_run_returns_expected_structure(self):
        result = self.tool.run({"operation_id": "OP-001"})
        assert result["operation_id"] == "OP-001"
        assert result["failure_category"] in ("NETWORK", "TIMEOUT", "VALIDATION", "QUOTA", "UNKNOWN")
        assert isinstance(result["is_transient"], bool)
        assert result["recommended_action"] in ("RETRY", "ESCALATE", "MANUAL_REVIEW")

    def test_run_via_executor(self, executor):
        result = executor.execute("GetFailureReasonTool", {"operation_id": "OP-XYZ"})
        assert result.success is True
        assert result.payload["operation_id"] == "OP-XYZ"


# ── GetCaseHistoryTool ────────────────────────────────────────────────────────

class TestGetCaseHistoryTool:
    def setup_method(self):
        self.tool = GetCaseHistoryTool()

    def test_tool_name(self):
        assert self.tool.definition.tool_name == "GetCaseHistoryTool"

    def test_required_inputs(self):
        assert "phone_number" in self.tool.definition.required_inputs

    def test_output_schema_has_expected_keys(self):
        schema = self.tool.definition.output_schema
        for key in ("case_count", "recent_cases", "escalation_rate"):
            assert key in schema

    def test_run_returns_expected_structure(self):
        result = self.tool.run({"phone_number": "+91-1234567890"})
        assert isinstance(result["case_count"], int)
        assert isinstance(result["recent_cases"], list)
        assert isinstance(result["escalation_rate"], float)

    def test_recent_cases_have_required_fields(self):
        result = self.tool.run({"phone_number": "+91-1234567890"})
        for case in result["recent_cases"]:
            assert "case_id" in case
            assert "topic" in case
            assert "state" in case

    def test_run_via_executor(self, executor):
        result = executor.execute("GetCaseHistoryTool", {"phone_number": "+91-9999"})
        assert result.success is True
        assert "case_count" in result.payload


# ── GetOnboardingStatusTool ───────────────────────────────────────────────────

class TestGetOnboardingStatusTool:
    def setup_method(self):
        self.tool = GetOnboardingStatusTool()

    def test_tool_name(self):
        assert self.tool.definition.tool_name == "GetOnboardingStatusTool"

    def test_required_inputs(self):
        assert "application_id" in self.tool.definition.required_inputs

    def test_output_schema_has_expected_keys(self):
        schema = self.tool.definition.output_schema
        for key in ("onboarding_stage", "completion_pct", "can_auto_advance"):
            assert key in schema

    def test_run_returns_expected_structure(self):
        result = self.tool.run({"application_id": "APP-001"})
        assert result["application_id"] == "APP-001"
        assert result["onboarding_stage"] in ("REGISTRATION", "OTP_VERIFY", "KYC", "VKYC", "COMPLETE")
        assert 0 <= result["completion_pct"] <= 100
        assert isinstance(result["can_auto_advance"], bool)

    def test_run_via_executor(self, executor):
        result = executor.execute("GetOnboardingStatusTool", {"application_id": "APP-XYZ"})
        assert result.success is True
        assert "completion_pct" in result.payload


# ── Default registry integration ──────────────────────────────────────────────

class TestDefaultRegistryIntegration:
    def test_all_5_tools_registered(self, default_registry):
        assert len(default_registry) == 5

    def test_all_tools_have_definitions(self, default_registry):
        for defn in default_registry.list_tools():
            assert defn.tool_name
            assert defn.description
            assert len(defn.required_inputs) >= 1
            assert len(defn.output_schema) >= 1

    def test_all_tools_execute_successfully(self, executor):
        test_inputs = {
            "GetSessionDetailsTool": {"session_id": "KID-001"},
            "GetUserDetailsTool": {"phone_number": "+91-1234567890"},
            "GetFailureReasonTool": {"operation_id": "OP-001"},
            "GetCaseHistoryTool": {"phone_number": "+91-1234567890"},
            "GetOnboardingStatusTool": {"application_id": "APP-001"},
        }
        for tool_name, inputs in test_inputs.items():
            result = executor.execute(tool_name, inputs)
            assert result.success is True, f"Tool {tool_name} failed: {result.error_message}"

    def test_executor_missing_required_input_for_each_tool(self, executor):
        """Each tool correctly rejects empty inputs."""
        for name in executor._registry.tool_names():
            result = executor.execute(name, {})
            assert result.success is False
            assert result.error_code == "MISSING_REQUIRED_INPUTS"
