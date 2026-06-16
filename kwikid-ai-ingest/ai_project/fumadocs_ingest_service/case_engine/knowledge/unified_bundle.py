"""
case_engine/knowledge/unified_bundle.py

Sprint 2.27.5: UnifiedKnowledgeBundle — converged knowledge output.

Per blueprint flow_diagram.mermaid:
  ROOTCAUSE --> HYBRIDRAG --> REASONING

KnowledgeOrchestrator produces a UnifiedKnowledgeBundle that combines:
  - SOP evidence (from KnowledgeService / SOP Repository)
  - RAG evidence (from HybridRAG — placeholder for Sprint 2.28)

The ReasoningService consumes UnifiedKnowledgeBundle instead of two
separate knowledge dicts. This is the convergence point.

Backward compatibility:
  - to_dict() produces ALL fields that existing KnowledgeService.search()
    dict format includes, PLUS the new unified fields.
  - Existing code that reads `knowledge_result["sop_match_found"]` etc. continues to work.

Design:
  - Frozen dataclasses (immutable)
  - JSON-serializable via to_dict()
  - from_knowledge_result() builds from existing KnowledgeService output
  - Supports future LLM-based re-ranking (rag_evidence.placeholder=True until Sprint 2.28)
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── RAGEvidence ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RAGEvidence:
    """
    Evidence from the HybridRAG system (Phase 1 RAG layer).

    Sprint 2.27.5: placeholder=True — the real HybridRAG integration is Sprint 2.28.
    The architecture is wired; execution is deferred.

    In Sprint 2.28:
      - placeholder becomes False
      - chunks are populated from pgvector + FTS retrieval
      - retrieval_confidence reflects actual semantic similarity
    """
    evidence_id:           str
    query:                 str
    chunks:                tuple[dict[str, Any], ...]
    source:                str           # "hybrid_rag", "semantic", "keyword", "placeholder"
    retrieval_confidence:  float         # 0.0 for placeholder
    retrieved_at:          str
    placeholder:           bool = True   # True until Sprint 2.28 real integration

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id":          self.evidence_id,
            "query":                self.query,
            "chunks":               list(self.chunks),
            "source":               self.source,
            "retrieval_confidence": self.retrieval_confidence,
            "retrieved_at":         self.retrieved_at,
            "placeholder":          self.placeholder,
        }

    @classmethod
    def placeholder_evidence(cls, query: str = "") -> "RAGEvidence":
        """Build a no-op placeholder for Sprint 2.27.5."""
        return cls(
            evidence_id=_new_id(),
            query=query,
            chunks=(),
            source="placeholder",
            retrieval_confidence=0.0,
            retrieved_at=_now_iso(),
            placeholder=True,
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RAGEvidence":
        return cls(
            evidence_id=d.get("evidence_id", _new_id()),
            query=d.get("query", ""),
            chunks=tuple(d.get("chunks", [])),
            source=d.get("source", "placeholder"),
            retrieval_confidence=float(d.get("retrieval_confidence", 0.0)),
            retrieved_at=d.get("retrieved_at", _now_iso()),
            placeholder=bool(d.get("placeholder", True)),
        )


# ── SOPEvidence ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SOPEvidence:
    """
    Evidence from the SOP knowledge base (case_engine/knowledge/).

    Populated by KnowledgeService.search() → SOPMatcher.match().
    Always available (deterministic, no external calls).
    """
    evidence_id:        str
    sop_match_found:    bool
    sop_entry_id:       str | None
    sop_title:          str | None
    relevance_score:    float
    recommended_action: str | None
    sop_steps:          tuple[str, ...]
    source:             str = "sop_knowledge_base"

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id":        self.evidence_id,
            "sop_match_found":    self.sop_match_found,
            "sop_entry_id":       self.sop_entry_id,
            "sop_title":          self.sop_title,
            "relevance_score":    self.relevance_score,
            "recommended_action": self.recommended_action,
            "sop_steps":          list(self.sop_steps),
            "source":             self.source,
        }

    @classmethod
    def from_knowledge_dict(cls, d: dict[str, Any]) -> "SOPEvidence":
        """Build SOPEvidence from KnowledgeService.search() output dict."""
        sop_match = d.get("sop_match") or {}
        entry     = sop_match.get("entry") or {}

        rec   = d.get("recommendation") or {}
        steps = rec.get("sop_steps") or []
        if isinstance(steps, (list, tuple)):
            sop_steps = tuple(str(s) for s in steps)
        else:
            sop_steps = ()

        return cls(
            evidence_id=_new_id(),
            sop_match_found=bool(d.get("sop_match_found", False)),
            sop_entry_id=entry.get("entry_id") if entry else None,
            sop_title=entry.get("title") if entry else None,
            relevance_score=float(sop_match.get("relevance_score", 0.0)) if sop_match else 0.0,
            recommended_action=rec.get("recommended_action"),
            sop_steps=sop_steps,
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SOPEvidence":
        return cls(
            evidence_id=d.get("evidence_id", _new_id()),
            sop_match_found=bool(d.get("sop_match_found", False)),
            sop_entry_id=d.get("sop_entry_id"),
            sop_title=d.get("sop_title"),
            relevance_score=float(d.get("relevance_score", 0.0)),
            recommended_action=d.get("recommended_action"),
            sop_steps=tuple(d.get("sop_steps", [])),
            source=d.get("source", "sop_knowledge_base"),
        )


# ── UnifiedKnowledgeBundle ────────────────────────────────────────────────────

@dataclass(frozen=True)
class UnifiedKnowledgeBundle:
    """
    Unified output of the KnowledgeOrchestrator.

    This is the single knowledge artifact that the ReasoningEngine consumes.
    It merges SOP evidence and RAG evidence into a ranked, correlated bundle.

    Per blueprint flow_diagram.mermaid:
      HYBRIDRAG → REASONING
      (KnowledgeOrchestrator produces this bundle, ReasoningEngine reads it)

    Backward compatibility:
      to_dict() includes all fields from KnowledgeService.search() output PLUS
      the new unified fields. Existing code using `knowledge_result[key]` works
      without changes.

    Sprint 2.27.5:
      - rag_evidence.placeholder=True (no real RAG calls yet)
      - Sprint 2.28 replaces placeholder with real HybridRAG

    Fields:
      bundle_id            — UUID for this specific knowledge bundle
      topic                — ticket topic (e.g. "VKYC_Session_Failure")
      sop_evidence         — evidence from SOP knowledge base
      rag_evidence         — evidence from HybridRAG (placeholder in 2.27.5)
      citations            — authoritative citation strings for response generation
      overall_confidence   — merged confidence score (0.0–1.0)
      recommended_action   — primary recommended action string
      escalation_required  — True if knowledge layer recommends escalation
      correlation_metadata — evidence correlation details (for LLM context in 2.28)
      created_at           — ISO timestamp
      raw_knowledge_dict   — original KnowledgeService.search() output (for compat)
    """
    bundle_id:            str
    topic:                str
    sop_evidence:         SOPEvidence
    rag_evidence:         RAGEvidence
    citations:            tuple[str, ...]
    overall_confidence:   float
    recommended_action:   str | None
    escalation_required:  bool
    correlation_metadata: dict[str, Any]
    created_at:           str
    raw_knowledge_dict:   dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Produce a JSON-safe dict.

        Includes all original KnowledgeService.search() fields for backward
        compatibility, plus new unified fields under "unified_*" keys.
        """
        base = dict(self.raw_knowledge_dict)
        base.update({
            # New unified fields
            "bundle_id":            self.bundle_id,
            "unified_topic":        self.topic,
            "unified_confidence":   self.overall_confidence,
            "unified_recommended":  self.recommended_action,
            "unified_escalate":     self.escalation_required,
            "sop_evidence":         self.sop_evidence.to_dict(),
            "rag_evidence":         self.rag_evidence.to_dict(),
            "citations":            list(self.citations),
            "correlation_metadata": dict(self.correlation_metadata),
            "created_at":           self.created_at,
            "is_unified_bundle":    True,
        })
        return base

    @classmethod
    def from_knowledge_result(
        cls,
        knowledge_dict:  dict[str, Any],
        rag_evidence:    RAGEvidence | None = None,
        topic:           str = "",
    ) -> "UnifiedKnowledgeBundle":
        """
        Build a UnifiedKnowledgeBundle from a KnowledgeService.search() output dict.

        This is the convergence point: existing KnowledgeService output is
        wrapped into the unified format with a placeholder RAGEvidence.

        Args:
            knowledge_dict: Output from KnowledgeService.search().
            rag_evidence:   Optional RAGEvidence (placeholder if None).
            topic:          Ticket topic string (fallback from knowledge_dict).

        Returns:
            UnifiedKnowledgeBundle — never raises.
        """
        sop_evidence = SOPEvidence.from_knowledge_dict(knowledge_dict)
        if rag_evidence is None:
            rag_evidence = RAGEvidence.placeholder_evidence(query=topic or knowledge_dict.get("topic", ""))

        rec = knowledge_dict.get("recommendation") or {}
        overall_confidence = float(rec.get("confidence", 0.0))
        recommended_action = rec.get("recommended_action")
        escalation_required = bool(rec.get("escalation_required", False))

        # Build citations from SOP match
        citations: list[str] = []
        sop_match = knowledge_dict.get("sop_match") or {}
        entry     = sop_match.get("entry") or {}
        if entry.get("entry_id"):
            citations.append(f"SOP:{entry['entry_id']}")
        if entry.get("title"):
            citations.append(f"SOP:{entry['title']}")

        resolved_topic = topic or knowledge_dict.get("topic", "UNKNOWN")

        return cls(
            bundle_id=_new_id(),
            topic=resolved_topic,
            sop_evidence=sop_evidence,
            rag_evidence=rag_evidence,
            citations=tuple(citations),
            overall_confidence=overall_confidence,
            recommended_action=recommended_action,
            escalation_required=escalation_required,
            correlation_metadata={
                "sop_weight": 1.0,
                "rag_weight": 0.0,  # Sprint 2.28: increase when real RAG is wired
                "merge_strategy": "sop_primary",
                "rag_placeholder": rag_evidence.placeholder,
            },
            created_at=_now_iso(),
            raw_knowledge_dict=dict(knowledge_dict),
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "UnifiedKnowledgeBundle":
        """Deserialize from a to_dict() output."""
        sop_ev  = SOPEvidence.from_dict(d.get("sop_evidence") or {})
        rag_ev  = RAGEvidence.from_dict(d.get("rag_evidence") or {})
        raw     = {k: v for k, v in d.items()
                   if k not in ("sop_evidence", "rag_evidence", "citations",
                                "correlation_metadata", "bundle_id", "is_unified_bundle")}
        return cls(
            bundle_id=d.get("bundle_id", _new_id()),
            topic=d.get("unified_topic", d.get("topic", "UNKNOWN")),
            sop_evidence=sop_ev,
            rag_evidence=rag_ev,
            citations=tuple(d.get("citations", [])),
            overall_confidence=float(d.get("unified_confidence", 0.0)),
            recommended_action=d.get("unified_recommended"),
            escalation_required=bool(d.get("unified_escalate", False)),
            correlation_metadata=dict(d.get("correlation_metadata", {})),
            created_at=d.get("created_at", _now_iso()),
            raw_knowledge_dict=raw,
        )
