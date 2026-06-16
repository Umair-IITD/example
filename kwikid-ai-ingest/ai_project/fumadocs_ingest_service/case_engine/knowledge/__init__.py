"""
case_engine/knowledge

Sprint 2.20: Knowledge Layer public API.
Sprint 2.27.5: Added KnowledgeOrchestrator + UnifiedKnowledgeBundle.

Canonical import path for callers:
  from case_engine.knowledge import KnowledgeService, build_knowledge_service
  from case_engine.knowledge import KnowledgeOrchestrator, build_knowledge_orchestrator

Per blueprint flow_diagram.mermaid — Knowledge Layer:
  STACK → INGEST → INDEX → HYBRIDRAG
  ROOTCAUSE → HYBRIDRAG → REASONING  (orchestrated by KnowledgeOrchestrator)
"""
from case_engine.knowledge.importer import StackOverflowImporter
from case_engine.knowledge.matcher import SOPMatcher
from case_engine.knowledge.models import (
    KnowledgeEntry,
    KnowledgeEntryStatus,
    KnowledgeEntryType,
    KnowledgeResult,
    KnowledgeSearchQuery,
    KnowledgeSearchResult,
    ResolutionRecommendation,
    SOPMatch,
)
from case_engine.knowledge.orchestrator import KnowledgeOrchestrator, build_knowledge_orchestrator
from case_engine.knowledge.recommendation import ResolutionRecommendationEngine
from case_engine.knowledge.repository import InMemoryKnowledgeRepository, KnowledgeRepository
from case_engine.knowledge.retriever import HybridRetriever
from case_engine.knowledge.service import KnowledgeService
from case_engine.knowledge.unified_bundle import (
    RAGEvidence,
    SOPEvidence,
    UnifiedKnowledgeBundle,
)

__all__ = [
    # Service
    "KnowledgeService",
    "build_knowledge_service",
    # Orchestrator (Sprint 2.27.5)
    "KnowledgeOrchestrator",
    "build_knowledge_orchestrator",
    # Unified bundle models (Sprint 2.27.5)
    "UnifiedKnowledgeBundle",
    "SOPEvidence",
    "RAGEvidence",
    # Components
    "StackOverflowImporter",
    "InMemoryKnowledgeRepository",
    "HybridRetriever",
    "SOPMatcher",
    "ResolutionRecommendationEngine",
    # Models
    "KnowledgeEntry",
    "KnowledgeEntryType",
    "KnowledgeEntryStatus",
    "KnowledgeSearchQuery",
    "SOPMatch",
    "KnowledgeSearchResult",
    "ResolutionRecommendation",
    "KnowledgeResult",
    # Repository protocol
    "KnowledgeRepository",
]


def build_knowledge_service(
    repository: "KnowledgeRepository | None" = None,
    audit_logger: object | None = None,
    seed_entries: "list[KnowledgeEntry] | None" = None,
) -> KnowledgeService:
    """
    Factory: build a fully wired KnowledgeService.

    Args:
        repository:   KnowledgeRepository to use (defaults to new InMemoryKnowledgeRepository).
        audit_logger: AuditLogger instance (optional; pass None in tests).
        seed_entries: KnowledgeEntry list to pre-populate the repository.
                      Useful for testing and development.

    Returns:
        Ready-to-use KnowledgeService.
    """
    if repository is None:
        repository = InMemoryKnowledgeRepository()

    if seed_entries:
        repository.bulk_store(seed_entries)

    retriever  = HybridRetriever()
    matcher    = SOPMatcher(retriever, repository)
    rec_engine = ResolutionRecommendationEngine()

    return KnowledgeService(
        matcher=matcher,
        recommendation_engine=rec_engine,
        audit_logger=audit_logger,  # type: ignore[arg-type]
    )
