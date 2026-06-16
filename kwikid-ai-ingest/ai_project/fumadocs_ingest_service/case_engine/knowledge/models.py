"""
case_engine/knowledge/models.py

Sprint 2.20: Knowledge Layer domain models.

Per blueprint flow_diagram.mermaid (Knowledge Layer):
  STACK → INGEST → INDEX → HYBRIDRAG → REASONING

Per blueprint Section 31 (Knowledge System):
  Investigation → Root Cause → Knowledge Search → Relevant SOP → Recommended Action

Model hierarchy:
  KnowledgeEntry        — one item from the knowledge base (SOP, FAQ, runbook, etc.)
  KnowledgeSearchQuery  — input to the HybridRetriever
  SOPMatch              — one matching entry with relevance score
  KnowledgeSearchResult — full result of one retrieval call
  ResolutionRecommendation — synthesised resolution guidance
  KnowledgeResult       — complete output of one KNOWLEDGE_LOOKUP step

All models:
  - Immutable (frozen=True) where correctness requires it
  - JSON-serializable via to_dict()
  - Never raise on construction or serialization
  - No LLM, no async, no side effects
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Entry type / status enums ─────────────────────────────────────────────────

class KnowledgeEntryType(str, Enum):
    """Category of a knowledge base entry."""
    SOP                  = "SOP"
    FAQ                  = "FAQ"
    RUNBOOK              = "RUNBOOK"
    KNOWN_ISSUE          = "KNOWN_ISSUE"
    ENGINEERING_FIX      = "ENGINEERING_FIX"
    HISTORICAL_RESOLUTION = "HISTORICAL_RESOLUTION"


class KnowledgeEntryStatus(str, Enum):
    """Lifecycle status of a knowledge base entry."""
    ACTIVE     = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    ARCHIVED   = "ARCHIVED"


# ── KnowledgeEntry ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class KnowledgeEntry:
    """
    A single item in the knowledge base.

    Sourced from StackOverflow Teams export, manually authored SOPs,
    or engineering runbooks.

    Immutable after construction — shared safely across requests.

    entry_id               : stable UUID for this entry
    title                  : question title / SOP heading
    body                   : answer body / SOP content (plain text)
    entry_type             : KnowledgeEntryType
    tags                   : raw tag strings from source (e.g. "vkyc", "session_reset")
    topic_keys             : TopicKey values this entry is relevant to
    root_cause_categories  : RootCauseCategory values this entry addresses
    recommended_actions    : RecommendedAction values this entry supports
    resolution_steps       : ordered step-by-step procedure (empty if not structured)
    source                 : "stackoverflow_teams" | "manual" | "runbook"
    source_id              : original ID from source system (SO question/answer ID)
    accepted_answer        : True if this is the accepted SO answer
    score                  : SO vote score (0 if not from SO)
    created_at             : ISO timestamp
    status                 : KnowledgeEntryStatus
    """
    entry_id:              str
    title:                 str
    body:                  str
    entry_type:            KnowledgeEntryType
    tags:                  tuple[str, ...]
    topic_keys:            tuple[str, ...]
    root_cause_categories: tuple[str, ...]
    recommended_actions:   tuple[str, ...]
    resolution_steps:      tuple[str, ...]
    source:                str
    source_id:             str | None
    accepted_answer:       bool
    score:                 int
    created_at:            str
    status:                KnowledgeEntryStatus = KnowledgeEntryStatus.ACTIVE

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id":              self.entry_id,
            "title":                 self.title,
            "body":                  self.body,
            "entry_type":            self.entry_type.value,
            "tags":                  list(self.tags),
            "topic_keys":            list(self.topic_keys),
            "root_cause_categories": list(self.root_cause_categories),
            "recommended_actions":   list(self.recommended_actions),
            "resolution_steps":      list(self.resolution_steps),
            "source":                self.source,
            "source_id":             self.source_id,
            "accepted_answer":       self.accepted_answer,
            "score":                 self.score,
            "created_at":            self.created_at,
            "status":                self.status.value,
        }


# ── KnowledgeSearchQuery ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class KnowledgeSearchQuery:
    """
    Input to HybridRetriever.

    Built by KnowledgeService from the active investigation result and topic.
    Immutable — constructed once per knowledge lookup step.
    """
    query_id:            str
    topic:               str
    root_cause_category: str | None   # RootCauseCategory.value or None
    recommended_action:  str | None   # RecommendedAction.value or None
    keywords:            tuple[str, ...]
    created_at:          str

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id":            self.query_id,
            "topic":               self.topic,
            "root_cause_category": self.root_cause_category,
            "recommended_action":  self.recommended_action,
            "keywords":            list(self.keywords),
            "created_at":          self.created_at,
        }


# ── SOPMatch ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SOPMatch:
    """
    One matching KnowledgeEntry returned by HybridRetriever.

    relevance_score : 0.0–1.0; higher is more relevant
    match_reason    : human-readable explanation
    matched_on      : which fields drove the match
    """
    match_id:        str
    entry:           KnowledgeEntry
    relevance_score: float
    match_reason:    str
    matched_on:      tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_id":        self.match_id,
            "entry":           self.entry.to_dict(),
            "relevance_score": self.relevance_score,
            "match_reason":    self.match_reason,
            "matched_on":      list(self.matched_on),
        }


# ── KnowledgeSearchResult ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class KnowledgeSearchResult:
    """
    Complete output of one HybridRetriever call.

    top_match   : highest relevance_score SOPMatch, or None if no entries
    total_found : total candidate entries before top_k truncation
    """
    result_id:   str
    query:       KnowledgeSearchQuery
    matches:     tuple[SOPMatch, ...]
    top_match:   SOPMatch | None
    total_found: int
    searched_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id":   self.result_id,
            "query":       self.query.to_dict(),
            "matches":     [m.to_dict() for m in self.matches],
            "top_match":   self.top_match.to_dict() if self.top_match else None,
            "total_found": self.total_found,
            "searched_at": self.searched_at,
        }


# ── ResolutionRecommendation ──────────────────────────────────────────────────

@dataclass(frozen=True)
class ResolutionRecommendation:
    """
    Synthesised resolution guidance.

    Produced by ResolutionRecommendationEngine from:
      - InvestigationResult (root cause, recommended action)
      - SOPMatch (knowledge base evidence)

    Per blueprint Section 29, Principle 3: Reasoning before execution.
    Never invents recommendations not supported by evidence.

    sop_steps           : ordered resolution steps from the matched SOP
    escalation_required : True if root_cause.escalate OR no SOP match with confidence
    source_entry_ids    : KnowledgeEntry IDs that support this recommendation
    """
    recommendation_id:  str
    topic:              str
    root_cause_category: str
    recommended_action: str
    confidence:         float
    explanation:        str
    sop_steps:          tuple[str, ...]
    escalation_required: bool
    source_entry_ids:   tuple[str, ...]
    created_at:         str

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation_id":  self.recommendation_id,
            "topic":              self.topic,
            "root_cause_category": self.root_cause_category,
            "recommended_action": self.recommended_action,
            "confidence":         self.confidence,
            "explanation":        self.explanation,
            "sop_steps":          list(self.sop_steps),
            "escalation_required": self.escalation_required,
            "source_entry_ids":   list(self.source_entry_ids),
            "created_at":         self.created_at,
        }


# ── KnowledgeResult ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class KnowledgeResult:
    """
    Complete output of one KNOWLEDGE_LOOKUP workflow step execution.

    Stored in WorkflowExecutionResult.knowledge_result (JSONB-serializable).
    Round-trips safely through to_dict() / from_dict().

    sop_match      : best match found, or None if repository had no relevant entries
    sop_match_found: True if at least one match was found
    """
    result_id:      str
    topic:          str
    search_result:  KnowledgeSearchResult
    sop_match:      SOPMatch | None
    recommendation: ResolutionRecommendation
    sop_match_found: bool
    completed_at:   str

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id":      self.result_id,
            "topic":          self.topic,
            "search_result":  self.search_result.to_dict(),
            "sop_match":      self.sop_match.to_dict() if self.sop_match else None,
            "recommendation": self.recommendation.to_dict(),
            "sop_match_found": self.sop_match_found,
            "completed_at":   self.completed_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "KnowledgeResult":
        """Restore from persisted JSONB dict. Used in WorkflowExecutionResult.from_dict()."""
        # Minimal restoration — preserves the dicts without deep reconstruction
        # (full object graph reconstruction not needed for audit/API read paths)
        return _KnowledgeResultProxy(d)  # type: ignore[return-value]


class _KnowledgeResultProxy:
    """
    Lightweight proxy for KnowledgeResult restored from JSONB.

    Preserves the raw dict for audit/API read paths without requiring
    deep reconstruction of the full object graph. Exposes to_dict() for
    re-serialization.
    """
    def __init__(self, d: dict[str, Any]) -> None:
        self._d = d
        self.result_id       = d.get("result_id", "")
        self.topic           = d.get("topic", "")
        self.sop_match_found = d.get("sop_match_found", False)
        self.completed_at    = d.get("completed_at", "")

    def to_dict(self) -> dict[str, Any]:
        return dict(self._d)
