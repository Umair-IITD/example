"""
case_engine/tools/framework/bridge.py

Sprint 2.45: ToolExecutorProvider — bridges ProductionToolExecutor into the
Sprint 2.42 EvidenceProvider protocol.

Architecture:
  EvidenceCollector → ToolExecutorProvider → ProductionToolExecutor
                                           → ProductionToolRegistry
                                           → BaseTool.run()

The bridge translates:
  EvidenceProvider.execute(step_id, evidence_kind, context, metadata)
      → ToolExecutionRequest
      → ProductionToolExecutor.execute_request()
      → ToolExecutionResult
      → CollectionResult

This allows EvidenceCollector to remain unchanged (it still uses
ProviderRegistry + EvidenceProvider interface from Sprint 2.42), but
the actual execution now goes through the production Tool Framework.

EvidenceKind → slot mapping:
  SESSION       → slot "session_id"    → tool input "session_id"
  LOG           → slot "session_id"    → tool input "operation_id"
  API           → slot "phone_number"  → tool input "phone_number"
  SUMMARY       → slot "phone_number"  → tool input "phone_number"
  DATABASE      → slot "phone_number"  → tool input "phone_number"
  WORKFLOW      → slot "application_id" → tool input "application_id"
  CONFIGURATION → slot "session_id"    → tool input "session_id"
  HUMAN         → (no inputs needed)

Dependency direction:
  bridge.py → framework/executor.py (ProductionToolExecutor)
  bridge.py → framework/models.py (ToolContext, ToolExecutionRequest)
  bridge.py → case_engine/investigation/collector/contracts.py (CollectionResult)
  bridge.py → stdlib only
"""
from __future__ import annotations

import logging
from typing import Any

from case_engine.investigation.collector.contracts import (
    CollectionContext,
    CollectionResult,
)
from case_engine.tools.framework.executor import ProductionToolExecutor
from case_engine.tools.framework.models import ToolContext, ToolExecutionRequest

LOGGER = logging.getLogger(__name__)

# Maps EvidenceKind value → (slot_name, tool_input_key)
# slot_name: the slot from CollectionContext.slots to extract
# tool_input_key: the key to use in the tool's required_inputs
_KIND_INPUT_MAP: dict[str, tuple[str, str]] = {
    "SESSION":       ("session_id",     "session_id"),
    "LOG":           ("session_id",     "operation_id"),   # session_id used as operation_id
    "API":           ("phone_number",   "phone_number"),
    "SUMMARY":       ("phone_number",   "phone_number"),
    "DATABASE":      ("phone_number",   "phone_number"),
    "WORKFLOW":      ("application_id", "application_id"),
    "CONFIGURATION": ("session_id",     "session_id"),
    "VISION":        ("session_id",     "session_id"),
    "KNOWLEDGE":     ("",               ""),               # knowledge evidence needs no tool input
    "HUMAN":         ("",               ""),
}

# Evidence kinds this bridge can handle via the ToolExecutor
_HANDLEABLE_KINDS: frozenset[str] = frozenset({
    "SESSION", "LOG", "API", "SUMMARY", "DATABASE",
    "WORKFLOW", "CONFIGURATION", "VISION",
})


class ToolExecutorProvider:
    """
    Sprint 2.45 bridge: implements EvidenceProvider by delegating to ProductionToolExecutor.

    Registered in ProviderRegistry by EvidenceCollector when a ProductionToolExecutor
    is injected into the collector's constructor.

    The collector stays unchanged — it calls provider.execute() as before.
    This bridge converts that call into a ToolExecutionRequest and routes it
    through the full production execution pipeline.
    """

    def __init__(self, executor: ProductionToolExecutor) -> None:
        self._executor = executor

    @property
    def provider_name(self) -> str:
        return "ToolExecutorProvider"

    def can_handle(self, evidence_kind: str) -> bool:
        """Return True for evidence kinds the ToolExecutor can satisfy."""
        return evidence_kind in _HANDLEABLE_KINDS

    def execute(
        self,
        step_id:              str,
        evidence_kind:        str,
        context:              CollectionContext,
        requirement_metadata: dict[str, Any],
    ) -> CollectionResult:
        """
        Execute evidence collection by routing through ProductionToolExecutor.

        1. Extract the appropriate slot value from CollectionContext.slots.
        2. Build ToolExecutionRequest with evidence_kind for capability routing.
        3. Execute via ProductionToolExecutor.
        4. Convert ToolExecutionResult → CollectionResult.

        Never raises.
        """
        slot_name, input_key = _KIND_INPUT_MAP.get(evidence_kind, ("", ""))

        # Build inputs from context slots
        inputs: dict[str, Any] = {}
        if slot_name and input_key:
            slot_val = context.slots.get(slot_name)
            if slot_val is not None:
                inputs[input_key] = slot_val
            else:
                # Try fallback slot names for more flexible slot extraction
                for slot_key, slot_value in context.slots.items():
                    if input_key in slot_key or slot_key in input_key:
                        inputs[input_key] = slot_value
                        break
                if input_key not in inputs:
                    # Last resort: pass all slots through
                    inputs.update(context.slots)

        # Also pass through all slots so the tool can access anything it needs
        for k, v in context.slots.items():
            if k not in inputs:
                inputs[k] = v

        tool_ctx = ToolContext(
            tool_name=f"<{evidence_kind}>",
            tenant_id=context.tenant_id,
            case_id=context.case_id,
            topic=context.topic,
            slots=inputs,
        )
        request = ToolExecutionRequest(
            context=tool_ctx,
            evidence_kind=evidence_kind,
        )

        try:
            result = self._executor.execute_request(request)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(
                "tool_executor_provider.unexpected_error kind=%s step=%s error=%s",
                evidence_kind, step_id, exc,
            )
            return CollectionResult.fail(
                provider_name=self.provider_name,
                step_id=step_id,
                evidence_kind=evidence_kind,
                error_code="BRIDGE_EXECUTION_ERROR",
                error_message=str(exc)[:500],
            )

        if result.success:
            return CollectionResult.ok(
                provider_name=self.provider_name,
                step_id=step_id,
                evidence_kind=evidence_kind,
                payload=result.payload,
                duration_ms=result.duration_ms,
                metadata={
                    "invocation_id": result.invocation_id,
                    "tool_name":     result.tool_name,
                    "attempts":      result.metrics.total_attempts,
                },
            )
        return CollectionResult.fail(
            provider_name=self.provider_name,
            step_id=step_id,
            evidence_kind=evidence_kind,
            error_code=result.error_code or "TOOL_EXECUTION_FAILED",
            error_message=result.error_message or "tool execution failed",
            duration_ms=result.duration_ms,
        )


def _wire_tool_executor_into_registry(
    provider_registry: Any,  # ProviderRegistry from Sprint 2.42
    executor:          ProductionToolExecutor,
) -> None:
    """
    Register a ToolExecutorProvider for all handleable EvidenceKind values
    in the Sprint 2.42 ProviderRegistry.

    Called by EvidenceCollector.__init__ when tool_executor is provided.
    """
    from case_engine.investigation.planner.models import EvidenceKind

    bridge = ToolExecutorProvider(executor)

    for kind in EvidenceKind:
        if bridge.can_handle(kind.value):
            provider_registry.register(kind, bridge)
            LOGGER.debug(
                "tool_executor_bridge.wired kind=%s provider=%s",
                kind.value, bridge.provider_name,
            )
