"""
case_engine/investigation/observation/contracts.py

Sprint 2.44: Contracts (protocols) for the Observation Generator.

ObservationTemplate           — Protocol all templates must satisfy.
ObservationGeneratorProtocol  — Protocol for the generator itself.

Architectural invariant enforced by these contracts:
  Templates FORMAT ONLY. They receive an ObservationDraft and return a
  string. They must never reason, retrieve, call tools/providers/APIs,
  invoke LLMs, or modify the analysis or evidence.

Dependency direction:
  contracts.py → observation/models.py (ObservationDraft, Observation)
  contracts.py → stdlib (typing)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from case_engine.investigation.observation.models import Observation, ObservationDraft

if TYPE_CHECKING:
    from case_engine.investigation.models import EvidenceBundle
    from case_engine.investigation.root_cause.models import RootCauseAnalysis


@runtime_checkable
class ObservationTemplate(Protocol):
    """
    Protocol that all observation templates must satisfy.

    Templates are stateless and deterministic — render() must produce the
    same text for the same draft, with no side effects and no I/O.
    """

    @property
    def template_id(self) -> str: ...

    @property
    def template_name(self) -> str: ...

    @property
    def priority(self) -> int:
        """Lower value = higher priority; matched first."""
        ...

    @property
    def is_fallback(self) -> bool:
        """True only for the universal fallback template."""
        ...

    def matches(self, category: str, topic: str) -> bool:
        """
        Return True if this template handles the given root-cause category
        (RootCauseCategory.value) and/or topic string.
        """
        ...

    def render(self, draft: ObservationDraft) -> str:
        """Format the draft into the observation note text."""
        ...


@runtime_checkable
class ObservationGeneratorProtocol(Protocol):
    """
    Protocol for the Observation Generator.

    generate() converts a RootCauseAnalysis (plus optional pipeline inputs)
    into an Observation. It only formats — everything analytical comes
    from the RootCauseAnalysis.
    """

    def generate(
        self,
        analysis: "RootCauseAnalysis",
        bundle: "EvidenceBundle | None" = None,
        context: object | None = None,
        sop: object | None = None,
        playbook: object | None = None,
    ) -> Observation: ...
