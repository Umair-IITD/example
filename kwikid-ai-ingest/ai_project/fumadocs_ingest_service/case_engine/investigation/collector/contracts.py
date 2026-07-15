"""
case_engine/investigation/collector/contracts.py

Sprint 2.42: Provider protocol contracts for the Evidence Collector Engine.

Defines:
  CollectionContext     — lightweight data bag passed to every provider
  CollectionResult      — what a provider returns after executing a step
  EvidenceProvider      — base Protocol all providers must satisfy
  ToolProvider          — Protocol for tool-based evidence (SESSION, LOG, API, SUMMARY)
  KnowledgeProvider     — Protocol for KNOWLEDGE evidence
  SOPProvider           — Protocol for SOP-backed evidence
  WorkflowProvider      — Protocol for WORKFLOW evidence
  VisionProvider        — Protocol for VISION evidence

Dependency direction:
  contracts.py → stdlib only (dataclasses, typing)
  contracts.py does NOT import from case_engine — providers are injected
  by callers; the collector depends only on these interfaces.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ── Collection context ─────────────────────────────────────────────────────────

@dataclass
class CollectionContext:
    """
    Lightweight data bag passed to every provider during execution.

    Contains only the data a provider needs to satisfy an evidence
    requirement — no services, no engines, no heavy state.

    Fields:
        case_id:   Case identifier (for tracing and logging)
        topic:     Normalised investigation topic
        slots:     Resolved slot values available at collection time
        tenant_id: Tenant client_id for routing (may be empty string)
        metadata:  Arbitrary key-value pairs for provider-specific hints
    """
    case_id:   str
    topic:     str
    slots:     dict[str, Any] = field(default_factory=dict)
    tenant_id: str = ""
    metadata:  dict[str, Any] = field(default_factory=dict)

    def get_slot(self, name: str, default: Any = None) -> Any:
        """Return a slot value, or default if not present."""
        return self.slots.get(name, default)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id":   self.case_id,
            "topic":     self.topic,
            "tenant_id": self.tenant_id,
            "slot_keys": list(self.slots.keys()),
        }


# ── Collection result ──────────────────────────────────────────────────────────

@dataclass
class CollectionResult:
    """
    What an EvidenceProvider returns after executing a PlanningStep.

    Fields:
        success:        True if the provider produced usable evidence
        payload:        Key-value evidence payload (empty dict on failure)
        provider_name:  Name of the provider that produced this result
        step_id:        step_id this result is for (set by dispatcher)
        evidence_kind:  What kind of evidence this result represents (EvidenceKind.value)
        duration_ms:    Wall-clock duration of provider execution
        error_code:     Short error identifier (if success=False)
        error_message:  Human-readable error explanation (if success=False)
        partial:        True if result has some data but is incomplete
        metadata:       Provider-specific extra info for audit/metrics
    """
    success:       bool
    payload:       dict[str, Any] = field(default_factory=dict)
    provider_name: str = ""
    step_id:       str = ""
    evidence_kind: str = ""
    duration_ms:   int = 0
    error_code:    str | None = None
    error_message: str | None = None
    partial:       bool = False
    metadata:      dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(
        cls,
        provider_name: str,
        step_id: str,
        evidence_kind: str,
        payload: dict[str, Any],
        duration_ms: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> "CollectionResult":
        """Convenience constructor for successful results."""
        return cls(
            success=True,
            payload=payload,
            provider_name=provider_name,
            step_id=step_id,
            evidence_kind=evidence_kind,
            duration_ms=duration_ms,
            metadata=metadata or {},
        )

    @classmethod
    def fail(
        cls,
        provider_name: str,
        step_id: str,
        evidence_kind: str,
        error_code: str,
        error_message: str,
        duration_ms: int = 0,
    ) -> "CollectionResult":
        """Convenience constructor for failed results."""
        return cls(
            success=False,
            payload={},
            provider_name=provider_name,
            step_id=step_id,
            evidence_kind=evidence_kind,
            duration_ms=duration_ms,
            error_code=error_code,
            error_message=error_message,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "success":       self.success,
            "provider_name": self.provider_name,
            "step_id":       self.step_id,
            "evidence_kind": self.evidence_kind,
            "duration_ms":   self.duration_ms,
            "error_code":    self.error_code,
            "error_message": self.error_message,
            "partial":       self.partial,
            "payload_keys":  list(self.payload.keys()),
        }


# ── Provider protocols ─────────────────────────────────────────────────────────

@runtime_checkable
class EvidenceProvider(Protocol):
    """
    Base protocol for all evidence providers.

    An EvidenceProvider is responsible for satisfying one category of
    EvidenceRequirement. The collector selects a provider from the registry
    based on EvidenceKind — the provider never knows which step it serves.

    Contract:
      - execute() MUST NOT raise — return CollectionResult.fail() on error
      - execute() MUST complete in bounded time
      - execute() is stateless — same inputs produce the same output
      - can_handle() is a hint used for initial registry validation
    """
    @property
    def provider_name(self) -> str:
        """Stable identifier for this provider (for logging and metrics)."""
        ...

    def can_handle(self, evidence_kind: str) -> bool:
        """Return True if this provider can satisfy the given EvidenceKind value."""
        ...

    def execute(
        self,
        step_id: str,
        evidence_kind: str,
        context: CollectionContext,
        requirement_metadata: dict[str, Any],
    ) -> CollectionResult:
        """
        Execute evidence collection for the given step.

        Args:
            step_id:              The PlanningStep.step_id being collected
            evidence_kind:        EvidenceKind.value string
            context:              CollectionContext with slots and tenant info
            requirement_metadata: Arbitrary hints from EvidenceRequirement

        Returns:
            CollectionResult — never raises.
        """
        ...


@runtime_checkable
class ToolProvider(EvidenceProvider, Protocol):
    """
    Protocol for tool-based evidence providers.

    Wraps the existing ToolExecutor / TenantAwareToolRegistry and exposes
    an evidence-oriented interface. Handles SESSION, LOG, API, and SUMMARY
    EvidenceKinds depending on which tools are registered.
    """
    def list_tools(self) -> list[str]:
        """Return tool names this provider can invoke."""
        ...


@runtime_checkable
class KnowledgeProvider(EvidenceProvider, Protocol):
    """
    Protocol for KNOWLEDGE evidence providers.

    Satisfies EvidenceKind.KNOWLEDGE — retrieves SOP articles, KB entries,
    and engineering notes for a given topic.
    """
    def get_knowledge_count(self) -> int:
        """Return the number of knowledge items this provider has indexed."""
        ...


@runtime_checkable
class SOPProvider(EvidenceProvider, Protocol):
    """
    Protocol for SOP evidence providers.

    Satisfies EvidenceKind.KNOWLEDGE (SOP flavour) — retrieves SOPDocument
    for a given topic and packages findings as KNOWLEDGE evidence.
    """
    ...


@runtime_checkable
class VisionProvider(EvidenceProvider, Protocol):
    """
    Protocol for VISION evidence providers.

    Satisfies EvidenceKind.VISION — analyses image attachments from tickets.
    Full implementation is Sprint 2.49; this protocol is structural only.
    """
    def is_available(self) -> bool:
        """Return True if the vision model is reachable."""
        ...


@runtime_checkable
class WorkflowProvider(EvidenceProvider, Protocol):
    """
    Protocol for WORKFLOW evidence providers.

    Satisfies EvidenceKind.WORKFLOW — retrieves workflow execution state and
    playbook step completion status.
    """
    ...
