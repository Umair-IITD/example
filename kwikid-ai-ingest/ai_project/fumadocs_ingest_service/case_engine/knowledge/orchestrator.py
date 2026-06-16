"""
case_engine/knowledge/orchestrator.py

Sprint 2.27.5: KnowledgeOrchestrator — unified knowledge pipeline.

Per blueprint flow_diagram.mermaid:
  ROOTCAUSE --> HYBRIDRAG --> REASONING

This orchestrator converges two knowledge systems:
  System A: Phase 1 RAG (HybridRAG in app/main.py — Sprint 2.28 integration)
  System B: case_engine KnowledgeService (SOP matcher — currently active)

Sprint 2.27.5: System A is placeholder. Architecture is wired and tested.
Sprint 2.28:   Replace _query_rag() with real HybridRAG provider injection.

Public API:
  orchestrate(topic, investigation_result, case) → UnifiedKnowledgeBundle
  search(topic, investigation_result, case)       → dict (backward-compatible)

Design:
  - KnowledgeOrchestrator wraps KnowledgeService — not replaces it.
  - search() signature is identical to KnowledgeService.search() for drop-in use.
  - UnifiedKnowledgeBundle.to_dict() includes all fields from the original
    KnowledgeResult.to_dict() — ZERO backward compatibility breaks.
  - rag_provider is None by default; inject it for real RAG in Sprint 2.28.
  - Never raises: all paths produce safe bundle.
  - Audit events emitted when audit_logger is provided.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from case_engine.knowledge.unified_bundle import (
    RAGEvidence,
    SOPEvidence,
    UnifiedKnowledgeBundle,
)

if TYPE_CHECKING:
    from case_engine.audit import AuditLogger
    from case_engine.knowledge.service import KnowledgeService
    from case_engine.models import Case

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


class KnowledgeOrchestrator:
    """
    Unified knowledge orchestration layer.

    Responsibilities (per blueprint Section 31 — Knowledge Retrieval Flow):
      1. Query SOP Repository via KnowledgeService
      2. Query HybridRAG (placeholder in Sprint 2.27.5)
      3. Merge evidence
      4. Rank results
      5. Produce UnifiedKnowledgeBundle

    Drop-in replacement for KnowledgeService:
      orchestrator.search(topic, investigation_result, case) is API-compatible
      with KnowledgeService.search(topic, investigation_result, case).

    Sprint 2.28 injection point:
      KnowledgeOrchestrator(knowledge_service=svc, rag_provider=real_rag)
      The rag_provider protocol is:
        rag_provider.retrieve(query, topic) -> dict with "chunks" list
    """

    def __init__(
        self,
        knowledge_service: "KnowledgeService",
        audit_logger:      "AuditLogger | None" = None,
        rag_provider:      Any = None,
    ) -> None:
        self._knowledge_service = knowledge_service
        self._audit             = audit_logger
        self._rag_provider      = rag_provider

    # ── Primary orchestration API ──────────────────────────────────────────────

    def orchestrate(
        self,
        topic:                str,
        investigation_result: dict[str, Any] | None,
        case:                 "Case | None" = None,
    ) -> UnifiedKnowledgeBundle:
        """
        Run the full knowledge orchestration pipeline.

        1. Query SOP knowledge base (always)
        2. Query HybridRAG (placeholder: returns empty evidence)
        3. Merge into UnifiedKnowledgeBundle

        Returns UnifiedKnowledgeBundle. Never raises.
        """
        try:
            return self._orchestrate(topic, investigation_result, case)
        except Exception as exc:
            LOGGER.exception(
                "knowledge_orchestrator.fatal_error topic=%s error=%s", topic, exc
            )
            return self._error_bundle(topic, str(exc))

    def search(
        self,
        topic:                str,
        investigation_result: dict[str, Any] | None,
        case:                 "Case | None" = None,
    ) -> dict[str, Any]:
        """
        Backward-compatible API: same signature as KnowledgeService.search().

        Returns UnifiedKnowledgeBundle.to_dict() which includes all original
        KnowledgeResult fields PLUS the new unified fields.

        This method makes KnowledgeOrchestrator a drop-in replacement for
        KnowledgeService in the WorkflowEngine KNOWLEDGE_LOOKUP step.

        Never raises.
        """
        bundle = self.orchestrate(topic, investigation_result, case)
        return bundle.to_dict()

    # ── Private pipeline ───────────────────────────────────────────────────────

    def _orchestrate(
        self,
        topic:                str,
        investigation_result: dict[str, Any] | None,
        case:                 "Case | None",
    ) -> UnifiedKnowledgeBundle:
        # 1. SOP knowledge base
        LOGGER.debug(
            "knowledge_orchestrator.sop_search topic=%s inv_present=%s",
            topic, investigation_result is not None,
        )
        knowledge_dict = self._knowledge_service.search(topic, investigation_result, case)

        # 2. HybridRAG (Sprint 2.28: replace with real provider)
        rag_evidence = self._query_rag(topic, investigation_result)

        # 3. Merge into UnifiedKnowledgeBundle
        bundle = UnifiedKnowledgeBundle.from_knowledge_result(
            knowledge_dict=knowledge_dict,
            rag_evidence=rag_evidence,
            topic=topic,
        )

        LOGGER.info(
            "knowledge_orchestrator.complete topic=%s bundle_id=%s sop_found=%s"
            " rag_placeholder=%s confidence=%.2f escalate=%s",
            topic,
            bundle.bundle_id,
            bundle.sop_evidence.sop_match_found,
            bundle.rag_evidence.placeholder,
            bundle.overall_confidence,
            bundle.escalation_required,
        )

        self._emit_orchestration_complete(bundle, case)
        return bundle

    def _query_rag(
        self,
        topic:                str,
        investigation_result: dict[str, Any] | None,
    ) -> RAGEvidence:
        """
        Query HybridRAG system.

        Sprint 2.27.5: placeholder — returns empty evidence.
        Sprint 2.28: inject rag_provider and call real retrieval.

        Never raises.
        """
        if self._rag_provider is None:
            # Sprint 2.27.5: placeholder evidence
            query = topic
            if investigation_result:
                rc = (investigation_result.get("root_cause") or {})
                category = rc.get("category", "")
                if category:
                    query = f"{topic} {category}"
            return RAGEvidence.placeholder_evidence(query=query)

        # Sprint 2.28 path: real RAG provider
        try:
            query = self._build_rag_query(topic, investigation_result)
            raw   = self._rag_provider.retrieve(query=query, topic=topic)
            return RAGEvidence(
                evidence_id=_new_id(),
                query=query,
                chunks=tuple(raw.get("chunks", [])),
                source="hybrid_rag",
                retrieval_confidence=float(raw.get("confidence", 0.0)),
                retrieved_at=_now_iso(),
                placeholder=False,
            )
        except Exception as exc:
            LOGGER.warning(
                "knowledge_orchestrator.rag_failed topic=%s error=%s — using placeholder",
                topic, exc,
            )
            return RAGEvidence.placeholder_evidence(query=topic)

    def _build_rag_query(
        self,
        topic:                str,
        investigation_result: dict[str, Any] | None,
    ) -> str:
        """Build a rich RAG query from topic + investigation context."""
        parts = [topic]
        if investigation_result:
            rc = investigation_result.get("root_cause") or {}
            if rc.get("category"):
                parts.append(rc["category"])
            if rc.get("recommended_action"):
                parts.append(rc["recommended_action"])
        return " ".join(parts)

    def _error_bundle(self, topic: str, error_msg: str) -> UnifiedKnowledgeBundle:
        """Build a safe error-state bundle when the pipeline crashes."""
        sop_evidence = SOPEvidence(
            evidence_id=_new_id(),
            sop_match_found=False,
            sop_entry_id=None,
            sop_title=None,
            relevance_score=0.0,
            recommended_action="ESCALATE",
            sop_steps=(),
        )
        rag_evidence = RAGEvidence.placeholder_evidence(query=topic)
        now = _now_iso()
        return UnifiedKnowledgeBundle(
            bundle_id=_new_id(),
            topic=topic,
            sop_evidence=sop_evidence,
            rag_evidence=rag_evidence,
            citations=(),
            overall_confidence=0.0,
            recommended_action="ESCALATE",
            escalation_required=True,
            correlation_metadata={"error": error_msg, "merge_strategy": "error_fallback"},
            created_at=now,
            raw_knowledge_dict={
                "result_id":       _new_id(),
                "topic":           topic,
                "sop_match_found": False,
                "sop_match":       None,
                "recommendation":  {
                    "recommended_action": "ESCALATE",
                    "confidence":         0.0,
                    "escalation_required": True,
                    "explanation":        f"Knowledge orchestrator error: {error_msg}",
                    "sop_steps":          [],
                },
                "search_result":   {"matches": [], "total_found": 0},
                "completed_at":    now,
            },
        )

    # ── Audit ──────────────────────────────────────────────────────────────────

    def _emit_orchestration_complete(
        self,
        bundle: UnifiedKnowledgeBundle,
        case:   "Case | None",
    ) -> None:
        """Emit audit event for completed orchestration. Never raises."""
        if self._audit is None or case is None:
            return
        try:
            self._audit.log_knowledge_orchestration_completed(
                case,
                bundle_id=bundle.bundle_id,
                topic=bundle.topic,
                sop_found=bundle.sop_evidence.sop_match_found,
                rag_placeholder=bundle.rag_evidence.placeholder,
                confidence=bundle.overall_confidence,
                escalation_required=bundle.escalation_required,
            )
        except Exception as exc:
            LOGGER.debug("knowledge_orchestrator: audit emit failed: %s", exc)


# ── Factory ───────────────────────────────────────────────────────────────────

def build_knowledge_orchestrator(
    knowledge_service: "KnowledgeService | None" = None,
    audit_logger:      Any = None,
    rag_provider:      Any = None,
) -> KnowledgeOrchestrator:
    """
    Factory: build a KnowledgeOrchestrator.

    Args:
        knowledge_service: Existing KnowledgeService (builds default if None).
        audit_logger:      Optional AuditLogger.
        rag_provider:      Optional RAG provider (None = placeholder mode).

    Returns:
        KnowledgeOrchestrator ready for use.
    """
    if knowledge_service is None:
        from case_engine.knowledge import build_knowledge_service
        knowledge_service = build_knowledge_service(audit_logger=audit_logger)

    return KnowledgeOrchestrator(
        knowledge_service=knowledge_service,
        audit_logger=audit_logger,
        rag_provider=rag_provider,
    )
