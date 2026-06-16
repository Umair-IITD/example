"""
tests/test_sprint217_tool_framework.py

Sprint 2.17 Part B — Investigation Tool Framework Tests

Covers:
  - ToolDefinition: immutability, to_dict
  - ToolInput: construction, to_dict
  - ToolResult: ok/fail factories, to_dict
  - ToolRegistry: register, get, has_tool, list_tools, build_default
  - ToolExecutor: happy path, missing tool, missing inputs, exception containment
"""
from __future__ import annotations

import pytest

from case_engine.tools.tool_models import ToolDefinition, ToolInput, ToolResult
from case_engine.tools.tool_registry import ToolRegistry
from case_engine.tools.tool_executor import BaseTool, ToolExecutor


# ── Fixtures ──────────────────────────────────────────────────────────────────

class _EchoTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_name="EchoTool",
            description="Echoes all inputs back as payload",
            required_inputs=("message",),
            output_schema={"message": "str"},
            version="1.0",
            tags=("test",),
        )

    def run(self, inputs):
        return {"message": inputs["message"]}


class _FailingTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_name="FailingTool",
            description="Always raises RuntimeError",
            required_inputs=("x",),
            output_schema={},
        )

    def run(self, inputs):
        raise RuntimeError("simulated tool failure")


class _SlowTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_name="SlowTool",
            description="Returns after delay",
            required_inputs=("delay",),
            output_schema={"done": "bool"},
        )

    def run(self, inputs):
        return {"done": True}


# ── ToolDefinition ─────────────────────────────────────────────────────────────

class TestToolDefinition:
    def test_frozen(self):
        defn = ToolDefinition(
            tool_name="TestTool",
            description="test",
            required_inputs=("a", "b"),
            output_schema={"result": "str"},
        )
        with pytest.raises((AttributeError, TypeError)):
            defn.tool_name = "changed"  # type: ignore

    def test_to_dict_has_required_keys(self):
        defn = ToolDefinition(
            tool_name="TestTool",
            description="Test description",
            required_inputs=("a",),
            output_schema={"x": "int"},
            version="2.0",
            tags=("tag1",),
        )
        d = defn.to_dict()
        assert d["tool_name"] == "TestTool"
        assert d["description"] == "Test description"
        assert d["required_inputs"] == ["a"]
        assert d["output_schema"] == {"x": "int"}
        assert d["version"] == "2.0"
        assert d["tags"] == ["tag1"]

    def test_required_inputs_is_tuple(self):
        defn = ToolDefinition(
            tool_name="T",
            description="",
            required_inputs=("x", "y"),
            output_schema={},
        )
        assert isinstance(defn.required_inputs, tuple)

    def test_tags_default_empty(self):
        defn = ToolDefinition(
            tool_name="T",
            description="",
            required_inputs=(),
            output_schema={},
        )
        assert defn.tags == ()


# ── ToolInput ─────────────────────────────────────────────────────────────────

class TestToolInput:
    def test_has_invocation_id(self):
        inp = ToolInput(tool_name="T", inputs={"x": 1})
        assert inp.invocation_id
        assert len(inp.invocation_id) > 8

    def test_to_dict_has_all_keys(self):
        inp = ToolInput(tool_name="EchoTool", inputs={"message": "hello"}, requested_by="test")
        d = inp.to_dict()
        assert d["tool_name"] == "EchoTool"
        assert d["inputs"] == {"message": "hello"}
        assert d["requested_by"] == "test"
        assert "invocation_id" in d
        assert "requested_at" in d

    def test_different_instances_have_different_ids(self):
        a = ToolInput(tool_name="T", inputs={})
        b = ToolInput(tool_name="T", inputs={})
        assert a.invocation_id != b.invocation_id


# ── ToolResult ────────────────────────────────────────────────────────────────

class TestToolResult:
    def test_ok_factory(self):
        r = ToolResult.ok("EchoTool", {"message": "hi"})
        assert r.success is True
        assert r.tool_name == "EchoTool"
        assert r.payload == {"message": "hi"}
        assert r.error_code is None

    def test_fail_factory(self):
        r = ToolResult.fail("EchoTool", "MISSING_INPUT", "Missing x")
        assert r.success is False
        assert r.error_code == "MISSING_INPUT"
        assert r.error_message == "Missing x"
        assert r.payload == {}

    def test_to_dict_success(self):
        r = ToolResult.ok("T", {"k": "v"})
        d = r.to_dict()
        assert d["success"] is True
        assert d["payload"] == {"k": "v"}
        assert d["error_code"] is None
        assert "executed_at" in d

    def test_to_dict_failure(self):
        r = ToolResult.fail("T", "ERR", "msg")
        d = r.to_dict()
        assert d["success"] is False
        assert d["error_code"] == "ERR"

    def test_unique_invocation_ids(self):
        a = ToolResult.ok("T", {})
        b = ToolResult.ok("T", {})
        assert a.invocation_id != b.invocation_id

    def test_duration_ms_default_zero(self):
        r = ToolResult.ok("T", {})
        assert r.duration_ms == 0


# ── ToolRegistry ──────────────────────────────────────────────────────────────

class TestToolRegistry:
    def test_register_and_get(self):
        registry = ToolRegistry()
        tool = _EchoTool()
        registry.register(tool)
        assert registry.get("EchoTool") is tool

    def test_get_returns_none_for_unknown(self):
        registry = ToolRegistry()
        assert registry.get("NonExistentTool") is None

    def test_has_tool(self):
        registry = ToolRegistry()
        registry.register(_EchoTool())
        assert registry.has_tool("EchoTool") is True
        assert registry.has_tool("Other") is False

    def test_len(self):
        registry = ToolRegistry()
        assert len(registry) == 0
        registry.register(_EchoTool())
        assert len(registry) == 1
        registry.register(_FailingTool())
        assert len(registry) == 2

    def test_list_tools_sorted(self):
        registry = ToolRegistry()
        registry.register(_FailingTool())
        registry.register(_EchoTool())
        names = [d.tool_name for d in registry.list_tools()]
        assert names == sorted(names)

    def test_tool_names(self):
        registry = ToolRegistry()
        registry.register(_EchoTool())
        registry.register(_FailingTool())
        names = registry.tool_names()
        assert "EchoTool" in names
        assert "FailingTool" in names

    def test_re_registration_replaces(self):
        registry = ToolRegistry()
        tool1 = _EchoTool()
        tool2 = _EchoTool()
        registry.register(tool1)
        registry.register(tool2)
        assert registry.get("EchoTool") is tool2
        assert len(registry) == 1

    def test_build_default_loads_5_tools(self):
        registry = ToolRegistry.build_default()
        assert len(registry) == 5

    def test_build_default_contains_all_mock_tools(self):
        registry = ToolRegistry.build_default()
        expected = [
            "GetCaseHistoryTool",
            "GetFailureReasonTool",
            "GetOnboardingStatusTool",
            "GetSessionDetailsTool",
            "GetUserDetailsTool",
        ]
        for name in expected:
            assert registry.has_tool(name), f"Missing tool: {name}"


# ── ToolExecutor ──────────────────────────────────────────────────────────────

class TestToolExecutor:
    def setup_method(self):
        self.registry = ToolRegistry()
        self.registry.register(_EchoTool())
        self.registry.register(_FailingTool())
        self.executor = ToolExecutor(self.registry)

    def test_execute_success(self):
        result = self.executor.execute("EchoTool", {"message": "hello"})
        assert result.success is True
        assert result.payload["message"] == "hello"
        assert result.tool_name == "EchoTool"

    def test_execute_missing_tool(self):
        result = self.executor.execute("UnknownTool", {"x": 1})
        assert result.success is False
        assert result.error_code == "TOOL_NOT_FOUND"
        assert "UnknownTool" in result.error_message

    def test_execute_missing_required_input(self):
        result = self.executor.execute("EchoTool", {})  # missing "message"
        assert result.success is False
        assert result.error_code == "MISSING_REQUIRED_INPUTS"
        assert "message" in result.error_message

    def test_execute_tool_exception_contained(self):
        result = self.executor.execute("FailingTool", {"x": "value"})
        assert result.success is False
        assert result.error_code == "TOOL_EXECUTION_ERROR"
        assert "simulated tool failure" in result.error_message

    def test_execute_never_raises(self):
        # Even with a completely broken executor state, should not raise
        executor = ToolExecutor(ToolRegistry())  # empty registry
        result = executor.execute("anything", {})
        assert isinstance(result, ToolResult)

    def test_execute_records_duration(self):
        result = self.executor.execute("EchoTool", {"message": "test"})
        assert result.duration_ms >= 0

    def test_execute_propagates_invocation_id_through_input(self):
        # The invocation_id is created inside execute but should be in result
        result = self.executor.execute("EchoTool", {"message": "test"})
        assert result.invocation_id

    def test_execute_with_custom_requested_by(self):
        result = self.executor.execute(
            "EchoTool",
            {"message": "hi"},
            requested_by="workflow:step_001",
        )
        assert result.success is True

    def test_execute_failing_tool_returns_correct_tool_name(self):
        result = self.executor.execute("FailingTool", {"x": "v"})
        assert result.tool_name == "FailingTool"
