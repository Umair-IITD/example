"""
case_engine/knowledge/service.py

Sprint 2.20: KnowledgeService — orchestrates the Knowledge Layer.

Per blueprint flow_diagram.mermaid:
  ROOTCAUSE → HYBRIDRAG → REASONING → GUARDRAILS → ACTIONPROPOSAL

Per blueprint Section 31 (Knowledge Retrieval Flow):
  Investigation → Root Cause → Knowledge Search → Relevant SOP → Recommended Action

Pipeline:
  investigation_result dict
  → extract topic, root_cause_category, recommended_action, confidence, escalate
  → build KnowledgeSearchQuery
  → SOPMatcher.match() → (SOPMatch | None, KnowledgeSearchResult)
  → ResolutionRecommendationEngine.recommend()
  → KnowledgeResult

Public API:
  service = KnowledgeService(matcher, recommendation_engine, audit_logger=None)
  result_dict = service.search(topic, investigation_result, case=None)

Design invariants:
  - Never raises — all exceptions produce a safe KnowledgeResult with escalation
  - Audit events emitted: KNOWLEDGE_SEARCH_STARTED, KNOWLEDGE_SEARCH_COMPLETED
    and SOP_MATCH_FOUND or SOP_MATCH_NOT_FOUND
  - No LLM — fully deterministic
  - Case argument is optional (allows admin invocations without live Case)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from case_engine.knowledge.matcher import SOPMatcher
from case_engine.knowledge.models import (
    KnowledgeResult,
    KnowledgeSearchQuery,
    KnowledgeSearchResult,
    ResolutionRecommendation,
    SOPMatch,
)
from case_engine.knowledge.recommendation import ResolutionRecommendationEngine

if TYPE_CHECKING:
    from case_engine.audit import AuditLogger
    from case_engine.models import Case

LOGGER = logging.getLogger(__name__)


class KnowledgeService:
    """
    Orchestrates the Knowledge Layer pipeline.

    Stateless per call. One instance is safe to share across requests.
    """

    def __init__(
        self,
        matcher: SOPMatcher,
        recommendation_engine: ResolutionRecommendationEngine,
        audit_logger: "AuditLogger | None" = None,
    ) -> None:
        self._matcher         = matcher
        self._rec_engine      = recommendation_engine
        self._audit           = audit_logger

    def search(
        self,
        topic: str,
        investigation_result: dict[str, Any] | None,
        case: "Case | None" = None,
    ) -> dict[str, Any]:
        """
        Run the knowledge search pipeline and return a JSONB-safe dict.

        Args:
            topic:               Ticket topic (e.g. "VKYC_Session_Failure").
            investigation_result: Serialised InvestigationResult dict from INVESTIGATE step.
                                  May be None if called without prior investigation.
            case:                Live Case object (optional — used for audit logging only).

        Returns:
            KnowledgeResult.to_dict() — never raises.
        """
        try:
            result = self._search(topic, investigation_result, case)
        except Exception as exc:
            LOGGER.exception(
                "knowledge_service.fatal_error topic=%s", topic
            )
            result = self._error_result(topic, str(exc))

        return result.to_dict()

    # ── Private ────────────────────────────────────────────────────────────────

    def _search(
        self,
        topic: str,
        investigation_result: dict[str, Any] | None,
        case: "Case | None",
    ) -> KnowledgeResult:
        # Extract root cause data from investigation result
        root_cause_dict = (investigation_result or {}).get("root_cause") or {}
        root_cause_category  = root_cause_dict.get("category") or None
        recommended_action   = root_cause_dict.get("recommended_action") or None
        inv_confidence       = float(root_cause_dict.get("confidence") or 0.0)
        inv_escalate         = bool(root_cause_dict.get("escalate") or False)

        if case is not None and self._audit is not None:
            try:
                self._audit.log_knowledge_search_started(
                    case,
                    topic=topic,
                    root_cause_category=root_cause_category,
                )
            except Exception:
                pass

        # SOPMatcher: query → retrieve → match
        sop_match, search_result = self._matcher.match(
            topic=topic,
            root_cause_category=root_cause_category,
            recommended_action=recommended_action,
        )

        # ResolutionRecommendation: synthesise from evidence
        recommendation = self._rec_engine.recommend(
            topic=topic,
            root_cause_category=root_cause_category or "UNKNOWN",
            recommended_action=recommended_action or "MANUAL_REVIEW",
            investigation_confidence=inv_confidence,
            investigation_escalate=inv_escalate,
            sop_match=sop_match,
        )

        result = KnowledgeResult(
            result_id=str(uuid.uuid4()),
            topic=topic,
            search_result=search_result,
            sop_match=sop_match,
            recommendation=recommendation,
            sop_match_found=sop_match is not None,
            completed_at=datetime.now(tz=timezone.utc).isoformat(),
        )

        if case is not None and self._audit is not None:
            try:
                if sop_match is not None:
                    self._audit.log_sop_match_found(
                        case,
                        topic=topic,
                        entry_id=sop_match.entry.entry_id,
                        entry_title=sop_match.entry.title,
                        relevance_score=sop_match.relevance_score,
                    )
                else:
                    self._audit.log_sop_match_not_found(
                        case,
                        topic=topic,
                        root_cause_category=root_cause_category,
                    )
                self._audit.log_knowledge_search_completed(
                    case,
                    topic=topic,
                    result_id=result.result_id,
                    sop_match_found=result.sop_match_found,
                    top_score=sop_match.relevance_score if sop_match else 0.0,
                )
            except Exception:
                pass

        LOGGER.info(
            "knowledge_service.complete topic=%s result_id=%s sop_match=%s"
            " recommendation=%s confidence=%.2f escalate=%s",
            topic,
            result.result_id,
            result.sop_match_found,
            recommendation.recommended_action,
            recommendation.confidence,
            recommendation.escalation_required,
        )
        return result

    def _error_result(self, topic: str, error_msg: str) -> KnowledgeResult:
        """Build a safe error-state KnowledgeResult when the pipeline crashes."""
        now = datetime.now(tz=timezone.utc).isoformat()
        query = KnowledgeSearchQuery(
            query_id=str(uuid.uuid4()),
            topic=topic,
            root_cause_category=None,
            recommended_action=None,
            keywords=(),
            created_at=now,
        )
        search_result = KnowledgeSearchResult(
            result_id=str(uuid.uuid4()),
            query=query,
            matches=(),
            top_match=None,
            total_found=0,
            searched_at=now,
        )
        recommendation = ResolutionRecommendation(
            recommendation_id=str(uuid.uuid4()),
            topic=topic,
            root_cause_category="UNKNOWN",
            recommended_action="ESCALATE",
            confidence=0.0,
            explanation=f"Knowledge service error: {error_msg}",
            sop_steps=(),
            escalation_required=True,
            source_entry_ids=(),
            created_at=now,
        )
        return KnowledgeResult(
            result_id=str(uuid.uuid4()),
            topic=topic,
            search_result=search_result,
            sop_match=None,
            recommendation=recommendation,
            sop_match_found=False,
            completed_at=now,
        )
