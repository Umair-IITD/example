"""
tests/test_sprint2275_knowledge_orchestrator.py

Sprint 2.27.5 Phase 2: KnowledgeOrchestrator + UnifiedKnowledgeBundle test suite.

Tests: orchestration pipeline, backward compatibility, error safety, bundle models.
"""
import pytest
from unittest.mock import MagicMock


# ── RAGEvidence ───────────────────────────────────────────────────────────────

class TestRAGEvidence:
    def test_placeholder_evidence(self):
        from case_engine.knowledge import RAGEvidence
        ev = RAGEvidence.placeholder_evidence(query="vkyc failure")
        assert ev.placeholder is True
        assert ev.source == "placeholder"
        assert ev.retrieval_confidence == 0.0
        assert ev.chunks == ()
        assert ev.query == "vkyc failure"

    def test_to_dict(self):
        from case_engine.knowledge import RAGEvidence
        ev = RAGEvidence.placeholder_evidence(query="test")
        d = ev.to_dict()
        assert "evidence_id" in d
        assert d["placeholder"] is True
        assert d["source"] == "placeholder"

    def test_from_dict_roundtrip(self):
        from case_engine.knowledge import RAGEvidence
        ev = RAGEvidence.placeholder_evidence(query="q")
        d = ev.to_dict()
        ev2 = RAGEvidence.from_dict(d)
        assert ev2.evidence_id == ev.evidence_id
        assert ev2.placeholder is True

    def test_immutable(self):
        from case_engine.knowledge import RAGEvidence
        ev = RAGEvidence.placeholder_evidence()
        with pytest.raises((AttributeError, TypeError)):
            ev.placeholder = False  # type: ignore[misc]


# ── SOPEvidence ───────────────────────────────────────────────────────────────

class TestSOPEvidence:
    def _make_knowledge_dict(self, sop_match_found=True):
        return {
            "result_id": "r1",
            "topic": "VKYC_Session_Failure",
            "sop_match_found": sop_match_found,
            "sop_match": {
                "entry": {"entry_id": "sop-1", "title": "VKYC SOP"},
                "relevance_score": 0.85,
            } if sop_match_found else None,
            "recommendation": {
                "recommended_action": "RETRY_SESSION",
                "confidence": 0.80,
                "escalation_required": False,
                "sop_steps": ["Step 1", "Step 2"],
            },
        }

    def test_from_knowledge_dict_with_match(self):
        from case_engine.knowledge.unified_bundle import SOPEvidence
        d = self._make_knowledge_dict(sop_match_found=True)
        ev = SOPEvidence.from_knowledge_dict(d)
        assert ev.sop_match_found is True
        assert ev.sop_entry_id == "sop-1"
        assert ev.sop_title == "VKYC SOP"
        assert ev.relevance_score == 0.85
        assert ev.recommended_action == "RETRY_SESSION"
        assert "Step 1" in ev.sop_steps

    def test_from_knowledge_dict_no_match(self):
        from case_engine.knowledge.unified_bundle import SOPEvidence
        d = self._make_knowledge_dict(sop_match_found=False)
        ev = SOPEvidence.from_knowledge_dict(d)
        assert ev.sop_match_found is False
        assert ev.sop_entry_id is None
        assert ev.relevance_score == 0.0

    def test_to_dict(self):
        from case_engine.knowledge.unified_bundle import SOPEvidence
        d = self._make_knowledge_dict()
        ev = SOPEvidence.from_knowledge_dict(d)
        out = ev.to_dict()
        assert out["sop_match_found"] is True
        assert out["sop_entry_id"] == "sop-1"
        assert isinstance(out["sop_steps"], list)


# ── UnifiedKnowledgeBundle ────────────────────────────────────────────────────

class TestUnifiedKnowledgeBundle:
    def _make_bundle(self):
        from case_engine.knowledge import UnifiedKnowledgeBundle
        return UnifiedKnowledgeBundle.from_knowledge_result(
            knowledge_dict={
                "result_id": "r1",
                "topic": "VKYC_Session_Failure",
                "sop_match_found": True,
                "sop_match": {"entry": {"entry_id": "sop-1", "title": "VKYC SOP"}, "relevance_score": 0.9},
                "recommendation": {
                    "recommended_action": "RETRY_SESSION",
                    "confidence": 0.82,
                    "escalation_required": False,
                    "sop_steps": ["Check logs", "Retry"],
                },
                "search_result": {"matches": [], "total_found": 1},
                "completed_at": "2025-01-01T00:00:00+00:00",
            },
            topic="VKYC_Session_Failure",
        )

    def test_bundle_created(self):
        bundle = self._make_bundle()
        assert bundle.bundle_id is not None
        assert bundle.topic == "VKYC_Session_Failure"
        assert bundle.sop_evidence.sop_match_found is True
        assert bundle.rag_evidence.placeholder is True

    def test_to_dict_backward_compat(self):
        bundle = self._make_bundle()
        d = bundle.to_dict()
        # Must include original KnowledgeService fields
        assert "result_id" in d
        assert "topic" in d
        assert "sop_match_found" in d
        # Plus new unified fields
        assert "bundle_id" in d
        assert d["is_unified_bundle"] is True
        assert "unified_confidence" in d

    def test_overall_confidence_from_recommendation(self):
        bundle = self._make_bundle()
        assert bundle.overall_confidence == pytest.approx(0.82)

    def test_citations_built(self):
        bundle = self._make_bundle()
        assert any("sop-1" in c for c in bundle.citations)

    def test_from_dict_roundtrip(self):
        from case_engine.knowledge import UnifiedKnowledgeBundle
        bundle = self._make_bundle()
        d = bundle.to_dict()
        bundle2 = UnifiedKnowledgeBundle.from_dict(d)
        assert bundle2.bundle_id == bundle.bundle_id
        assert bundle2.topic == bundle.topic
        assert bundle2.overall_confidence == pytest.approx(bundle.overall_confidence)

    def test_error_bundle_never_raises(self):
        from case_engine.knowledge.orchestrator import KnowledgeOrchestrator
        from case_engine.knowledge import build_knowledge_service
        ks = build_knowledge_service()
        orc = KnowledgeOrchestrator(knowledge_service=ks)
        bundle = orc._error_bundle("VKYC_Session_Failure", "test error")
        assert bundle.escalation_required is True
        assert bundle.recommended_action == "ESCALATE"

    def test_immutable(self):
        bundle = self._make_bundle()
        with pytest.raises((AttributeError, TypeError)):
            bundle.bundle_id = "hacked"  # type: ignore[misc]


# ── KnowledgeOrchestrator ─────────────────────────────────────────────────────

class TestKnowledgeOrchestrator:
    def _make_orchestrator(self, with_audit=False):
        from case_engine.knowledge import build_knowledge_orchestrator
        audit = MagicMock() if with_audit else None
        return build_knowledge_orchestrator(audit_logger=audit), audit

    def test_orchestrate_returns_bundle(self):
        orc, _ = self._make_orchestrator()
        bundle = orc.orchestrate("VKYC_Session_Failure", investigation_result=None)
        assert bundle.bundle_id is not None
        assert bundle.rag_evidence.placeholder is True

    def test_search_backward_compat(self):
        orc, _ = self._make_orchestrator()
        result = orc.search("VKYC_Session_Failure", investigation_result=None)
        assert isinstance(result, dict)
        assert "bundle_id" in result
        assert result["is_unified_bundle"] is True

    def test_orchestrate_with_investigation_result(self):
        orc, _ = self._make_orchestrator()
        inv = {"root_cause": {"category": "session_timeout", "recommended_action": "RETRY"}}
        bundle = orc.orchestrate("VKYC_Session_Failure", investigation_result=inv)
        assert bundle.topic == "VKYC_Session_Failure"

    def test_orchestrate_never_raises(self):
        from case_engine.knowledge import KnowledgeOrchestrator
        broken_ks = MagicMock()
        broken_ks.search.side_effect = RuntimeError("knowledge service down")
        orc = KnowledgeOrchestrator(knowledge_service=broken_ks)
        bundle = orc.orchestrate("VKYC_Session_Failure", investigation_result=None)
        assert bundle.escalation_required is True

    def test_orchestrate_with_real_rag_provider(self):
        from case_engine.knowledge import build_knowledge_orchestrator
        mock_rag = MagicMock()
        mock_rag.retrieve.return_value = {
            "chunks": [{"text": "chunk1", "score": 0.9}],
            "confidence": 0.85,
        }
        orc = build_knowledge_orchestrator(rag_provider=mock_rag)
        bundle = orc.orchestrate("VKYC_Session_Failure", investigation_result=None)
        # Rag provider called
        mock_rag.retrieve.assert_called_once()
        assert bundle.rag_evidence.placeholder is False

    def test_orchestrate_rag_failure_uses_placeholder(self):
        from case_engine.knowledge import build_knowledge_orchestrator
        mock_rag = MagicMock()
        mock_rag.retrieve.side_effect = RuntimeError("rag down")
        orc = build_knowledge_orchestrator(rag_provider=mock_rag)
        bundle = orc.orchestrate("VKYC_Session_Failure", investigation_result=None)
        # Fallback to placeholder when real RAG fails
        assert bundle.rag_evidence.placeholder is True

    def test_audit_emitted_on_orchestrate(self):
        orc, mock_audit = self._make_orchestrator(with_audit=True)
        from case_engine.models import Case
        case = Case(ticket_id="fd-001", client="unity_bank")
        orc.orchestrate("VKYC_Session_Failure", investigation_result=None, case=case)
        mock_audit.log_knowledge_orchestration_completed.assert_called_once()

    def test_audit_failure_does_not_crash(self):
        from case_engine.knowledge import build_knowledge_orchestrator
        mock_audit = MagicMock()
        mock_audit.log_knowledge_orchestration_completed.side_effect = RuntimeError("audit down")
        from case_engine.models import Case
        case = Case(ticket_id="fd-001", client="unity_bank")
        orc = build_knowledge_orchestrator(audit_logger=mock_audit)
        bundle = orc.orchestrate("VKYC_Session_Failure", investigation_result=None, case=case)
        assert bundle is not None


# ── Factory ───────────────────────────────────────────────────────────────────

class TestKnowledgeOrchestratorFactory:
    def test_build_with_no_args(self):
        from case_engine.knowledge import build_knowledge_orchestrator
        orc = build_knowledge_orchestrator()
        assert orc is not None

    def test_build_with_existing_knowledge_service(self):
        from case_engine.knowledge import build_knowledge_orchestrator, build_knowledge_service
        ks = build_knowledge_service()
        orc = build_knowledge_orchestrator(knowledge_service=ks)
        assert orc._knowledge_service is ks

    def test_build_with_rag_provider(self):
        from case_engine.knowledge import build_knowledge_orchestrator
        mock_rag = MagicMock()
        orc = build_knowledge_orchestrator(rag_provider=mock_rag)
        assert orc._rag_provider is mock_rag
