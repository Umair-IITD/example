"""
query_router/models.py

Pydantic models for query classification and routing decisions.
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field

from query_router.taxonomy import IssueCategory


class ClassificationResult(BaseModel):
    """Result of classifying a support query into a category."""
    category: IssueCategory
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str | None = None
    # Secondary categories with scores (for multi-label awareness)
    secondary_categories: dict[str, float] = Field(default_factory=dict)
    # Which classifier produced this result
    classifier_used: str = "deterministic"


class RoutingDecision(BaseModel):
    """
    Complete routing decision including retrieval strategy adjustments.

    Consumers (retrieval layer, chat generator) use this to modify
    their behavior based on query category.
    """
    category: IssueCategory
    confidence: float = Field(ge=0.0, le=1.0)
    classifier_used: str

    # ── Retrieval strategy ─────────────────────────────────────────────────
    # Whether to prioritize SOP chunks over ticket chunks
    prioritize_sop: bool = False
    # Override for top_k (None = use caller's default)
    top_k_override: int | None = None
    # Override for similarity threshold (None = use caller's default)
    threshold_override: float | None = None
    # Whether retrieval should be skipped entirely (spam/conversational)
    skip_retrieval: bool = False
    # Whether to force human escalation regardless of confidence
    force_escalation: bool = False

    # ── Metadata ──────────────────────────────────────────────────────────
    reasoning: str | None = None
    routing_metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_classification(
        cls,
        result: ClassificationResult,
    ) -> "RoutingDecision":
        """Convert a ClassificationResult into a RoutingDecision with strategy defaults."""
        from query_router.thresholds import ROUTING_STRATEGIES
        strategy = ROUTING_STRATEGIES.get(result.category, {})

        return cls(
            category=result.category,
            confidence=result.confidence,
            classifier_used=result.classifier_used,
            prioritize_sop=strategy.get("prioritize_sop", False),
            top_k_override=strategy.get("top_k_override"),
            threshold_override=strategy.get("threshold_override"),
            skip_retrieval=strategy.get("skip_retrieval", False),
            force_escalation=strategy.get("force_escalation", False),
            reasoning=result.reasoning,
            routing_metadata={
                "secondary_categories": result.secondary_categories,
            },
        )
