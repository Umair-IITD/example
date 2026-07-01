"""
tests/test_retrieval_evaluation.py

Part 3: Retrieval evaluation tests for the KwikID knowledge pipeline.

Tests operate entirely on mock RetrievedChunk objects — NO live database required.
Each test simulates a realistic support query scenario and verifies that:
  - Scoring boost logic is applied correctly
  - quality_score gate passes for good articles
  - has_rca, knowledge_class, automation_label fields are correctly typed

Topics covered:
  1. "OTP not received on mobile"     → FAQ / TROUBLESHOOTING, VERIFIED_REPLY preferred
  2. "Video KYC session dropped"       → SOP / FAQ chunk
  3. "Document OCR failed"             → TROUBLESHOOTING chunk
  4. "Agent portal not loading"        → FAQ chunk
  5. "API callback not triggered"      → TROUBLESHOOTING chunk
"""
from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Minimal stub for RetrievedChunk to avoid importing the full RAG stack
# ---------------------------------------------------------------------------

@dataclass
class _MockRetrievedChunk:
    chunk_id:         str
    ticket_id:        Optional[str]
    sop_id:           Optional[str]
    chunk_type:       str
    content:          str
    similarity:       float
    boosted_score:    float
    source_table:     str
    has_rca:          bool  = False
    has_sop:          bool  = False
    automation_label: Optional[str] = None
    query_type:       Optional[str] = None
    extra_metadata:   dict = field(default_factory=dict)
    knowledge_class:  Optional[str] = None
    quality_score:    Optional[float] = None
    image_metadata:   list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Scoring helpers (mirrors the boosting logic in match_all_b1_sources RPC)
# ---------------------------------------------------------------------------

_QUALITY_GATE = 0.55


def _knowledge_boosted_score(similarity: float, quality_score: float) -> float:
    """
    Replicate Branch 3 boost: +0.08 base + quality*0.05 bonus.
    Capped at 1.0 (matches LEAST(1.0, ...) in SQL).
    """
    return min(1.0, similarity + 0.08 + (quality_score * 0.05))


def _sop_boosted_score(similarity: float) -> float:
    """Branch 2: +0.15 boost."""
    return min(1.0, similarity + 0.15)


def _ticket_boosted_score(similarity: float) -> float:
    """Branch 1: no boost."""
    return similarity


def _passes_quality_gate(quality_score: Optional[float]) -> bool:
    if quality_score is None:
        return False
    return quality_score >= _QUALITY_GATE


def _rank_chunks(chunks: list[_MockRetrievedChunk]) -> list[_MockRetrievedChunk]:
    """Sort by boosted_score descending (mirrors ORDER BY boosted_score DESC)."""
    return sorted(chunks, key=lambda c: c.boosted_score, reverse=True)


# ---------------------------------------------------------------------------
# Helper: build knowledge chunk at a given similarity and quality
# ---------------------------------------------------------------------------

def _make_knowledge_chunk(
    chunk_id: str,
    content: str,
    knowledge_class: str,
    similarity: float,
    quality_score: float,
    chunk_type: str = "FAQ_ANSWER",
    has_rca: bool = False,
) -> _MockRetrievedChunk:
    boosted = _knowledge_boosted_score(similarity, quality_score)
    return _MockRetrievedChunk(
        chunk_id        = chunk_id,
        ticket_id       = None,
        sop_id          = f"so_{chunk_id}",
        chunk_type      = chunk_type,
        content         = content,
        similarity      = similarity,
        boosted_score   = boosted,
        source_table    = "rag_knowledge_chunks",
        knowledge_class = knowledge_class,
        quality_score   = quality_score,
        has_rca         = has_rca,
        automation_label= None,
    )


def _make_sop_chunk(
    chunk_id: str,
    content: str,
    similarity: float,
) -> _MockRetrievedChunk:
    return _MockRetrievedChunk(
        chunk_id      = chunk_id,
        ticket_id     = None,
        sop_id        = f"sop_{chunk_id}",
        chunk_type    = "SOP_PROCEDURE",
        content       = content,
        similarity    = similarity,
        boosted_score = _sop_boosted_score(similarity),
        source_table  = "rag_sop_chunks",
        has_sop       = True,
    )


def _make_ticket_chunk(
    chunk_id: str,
    content: str,
    similarity: float,
    knowledge_class: Optional[str] = None,
    automation_label: Optional[str] = "AUTO_REPLY",
    has_rca: bool = False,
) -> _MockRetrievedChunk:
    return _MockRetrievedChunk(
        chunk_id         = chunk_id,
        ticket_id        = f"tk_{chunk_id}",
        sop_id           = None,
        chunk_type       = "RESOLUTION_RCA" if has_rca else "QUERY_BODY",
        content          = content,
        similarity       = similarity,
        boosted_score    = _ticket_boosted_score(similarity),
        source_table     = "rag_ticket_chunks",
        has_rca          = has_rca,
        knowledge_class  = knowledge_class,
        automation_label = automation_label,
    )


# ===========================================================================
# Test 1: "OTP not received on mobile"
# Expected: FAQ / TROUBLESHOOTING knowledge chunk, VERIFIED_REPLY preferred
# ===========================================================================

class TestOTPNotReceived:
    """Query: 'OTP not received on mobile'"""

    def _build_candidates(self) -> list[_MockRetrievedChunk]:
        return [
            # VERIFIED_REPLY with high quality → should rank at top
            _make_knowledge_chunk(
                "otp_verified_reply",
                "If OTP is not received, first check if the mobile number is correct in the KYC portal. "
                "Then trigger a resend via the OTP retry button. If the issue persists escalate to telecom.",
                knowledge_class = "VERIFIED_REPLY",
                similarity      = 0.78,
                quality_score   = 0.85,
                chunk_type      = "FAQ_ANSWER",
            ),
            # FAQ chunk with moderate score
            _make_knowledge_chunk(
                "otp_faq",
                "OTP not received: ensure mobile number is correct and network is available.",
                knowledge_class = "FAQ",
                similarity      = 0.70,
                quality_score   = 0.72,
            ),
            # Ticket chunk with lower similarity (no boost)
            _make_ticket_chunk(
                "otp_ticket",
                "Customer reported OTP not received. Resolved by re-triggering OTP from portal.",
                similarity      = 0.65,
                has_rca         = False,
            ),
        ]

    def test_verified_reply_ranks_first(self) -> None:
        """VERIFIED_REPLY with highest boosted_score should lead after sorting."""
        chunks   = self._build_candidates()
        ranked   = _rank_chunks(chunks)
        assert ranked[0].chunk_id == "otp_verified_reply"
        assert ranked[0].knowledge_class == "VERIFIED_REPLY"

    def test_quality_gate_passes_for_all(self) -> None:
        """All knowledge chunks in this scenario should pass the quality gate."""
        chunks = self._build_candidates()
        knowledge_chunks = [c for c in chunks if c.source_table == "rag_knowledge_chunks"]
        for c in knowledge_chunks:
            assert _passes_quality_gate(c.quality_score), (
                f"chunk {c.chunk_id} quality_score={c.quality_score} below gate {_QUALITY_GATE}"
            )

    def test_boost_formula_applied(self) -> None:
        """Verify the numeric boost formula is applied to knowledge chunks."""
        chunk = _make_knowledge_chunk(
            "otp_score_check", "test content", "VERIFIED_REPLY",
            similarity=0.78, quality_score=0.85,
        )
        expected = min(1.0, 0.78 + 0.08 + 0.85 * 0.05)
        assert abs(chunk.boosted_score - expected) < 1e-9

    def test_fields_are_correctly_typed(self) -> None:
        """knowledge_class and automation_label fields are present and correctly typed."""
        chunks = self._build_candidates()
        for c in chunks:
            if c.source_table == "rag_knowledge_chunks":
                assert isinstance(c.knowledge_class, str)
                assert c.quality_score is not None
                assert isinstance(c.quality_score, float)
            if c.automation_label is not None:
                assert isinstance(c.automation_label, str)


# ===========================================================================
# Test 2: "Video KYC session dropped"
# Expected: SOP or FAQ chunk
# ===========================================================================

class TestVideoKYCSessionDropped:
    """Query: 'Video KYC session dropped'"""

    def _build_candidates(self) -> list[_MockRetrievedChunk]:
        return [
            _make_sop_chunk(
                "vkyc_sop",
                "Step 1: Verify network bandwidth (minimum 2Mbps required). "
                "Step 2: Ask customer to reconnect and restart the Video KYC session.",
                similarity=0.72,
            ),
            _make_knowledge_chunk(
                "vkyc_faq",
                "If the Video KYC session drops mid-way, the session can be resumed within 30 minutes. "
                "The agent should send a new session link from the KYC portal.",
                knowledge_class="FAQ",
                similarity=0.68,
                quality_score=0.80,
                chunk_type="FAQ_ANSWER",
            ),
        ]

    def test_sop_chunk_ranks_first(self) -> None:
        """SOP chunk with +0.15 boost should outrank knowledge FAQ chunk."""
        chunks = self._build_candidates()
        ranked = _rank_chunks(chunks)
        assert ranked[0].source_table == "rag_sop_chunks"
        assert ranked[0].has_sop is True

    def test_sop_boost_is_0_15(self) -> None:
        """SOP boosted_score = similarity + 0.15."""
        chunk = _make_sop_chunk("sop_test", "content", similarity=0.72)
        assert abs(chunk.boosted_score - (0.72 + 0.15)) < 1e-9

    def test_faq_knowledge_class(self) -> None:
        """FAQ knowledge chunk has knowledge_class='FAQ'."""
        chunks = self._build_candidates()
        faq_chunks = [c for c in chunks if c.chunk_id == "vkyc_faq"]
        assert len(faq_chunks) == 1
        assert faq_chunks[0].knowledge_class == "FAQ"
        assert faq_chunks[0].chunk_type == "FAQ_ANSWER"


# ===========================================================================
# Test 3: "Document OCR failed"
# Expected: TROUBLESHOOTING chunk
# ===========================================================================

class TestDocumentOCRFailed:
    """Query: 'Document OCR failed'"""

    def _build_candidates(self) -> list[_MockRetrievedChunk]:
        return [
            _make_knowledge_chunk(
                "ocr_troubleshoot",
                "Document OCR failure: ensure the document image is not blurry and the file size is "
                "under 5MB. Retry the upload. If OCR fails repeatedly, use manual entry fallback.",
                knowledge_class = "TROUBLESHOOTING",
                similarity      = 0.75,
                quality_score   = 0.78,
                chunk_type      = "TROUBLESHOOTING",
            ),
            _make_ticket_chunk(
                "ocr_ticket_rca",
                "Root cause: OCR engine timed out due to oversized PDF. Resolution: compress PDF first.",
                similarity      = 0.60,
                has_rca         = True,
            ),
        ]

    def test_troubleshooting_chunk_present(self) -> None:
        """TROUBLESHOOTING knowledge chunk is retrieved."""
        chunks = self._build_candidates()
        troubleshoot = [c for c in chunks if c.knowledge_class == "TROUBLESHOOTING"]
        assert len(troubleshoot) >= 1
        assert troubleshoot[0].chunk_type == "TROUBLESHOOTING"

    def test_rca_ticket_present(self) -> None:
        """Ticket chunk with has_rca=True is present in candidates."""
        chunks = self._build_candidates()
        rca_chunks = [c for c in chunks if c.has_rca]
        assert len(rca_chunks) >= 1

    def test_quality_gate_passes(self) -> None:
        """TROUBLESHOOTING chunk passes the quality gate."""
        chunk = _make_knowledge_chunk(
            "ocr_test", "content", "TROUBLESHOOTING",
            similarity=0.75, quality_score=0.78,
        )
        assert _passes_quality_gate(chunk.quality_score)

    def test_low_quality_fails_gate(self) -> None:
        """A chunk with quality_score=0.40 does NOT pass the 0.55 gate."""
        assert not _passes_quality_gate(0.40)

    def test_rca_boost_signal(self) -> None:
        """has_rca=True ticket chunk provides a scoring signal for downstream consumers."""
        chunks = self._build_candidates()
        rca_chunk = next(c for c in chunks if c.has_rca)
        assert rca_chunk.has_rca is True
        # Ticket chunks have NO boost in the SQL RPC — boosted_score == similarity
        assert abs(rca_chunk.boosted_score - rca_chunk.similarity) < 1e-9


# ===========================================================================
# Test 4: "Agent portal not loading"
# Expected: FAQ chunk
# ===========================================================================

class TestAgentPortalNotLoading:
    """Query: 'Agent portal not loading'"""

    def _build_candidates(self) -> list[_MockRetrievedChunk]:
        return [
            _make_knowledge_chunk(
                "portal_faq_1",
                "If the agent portal is not loading, clear your browser cache and try again. "
                "Supported browsers: Chrome 90+, Firefox 88+.",
                knowledge_class = "FAQ",
                similarity      = 0.80,
                quality_score   = 0.90,
            ),
            _make_knowledge_chunk(
                "portal_faq_2",
                "Agent portal loading issues often occur after a session timeout. "
                "Log out and log back in to refresh the session.",
                knowledge_class = "FAQ",
                similarity      = 0.65,
                quality_score   = 0.70,
            ),
        ]

    def test_highest_quality_faq_ranks_first(self) -> None:
        """Higher quality_score contributes to boosted_score, ranking the better chunk first."""
        chunks  = self._build_candidates()
        ranked  = _rank_chunks(chunks)
        # portal_faq_1 has higher sim + quality → should rank first
        assert ranked[0].chunk_id == "portal_faq_1"

    def test_all_chunks_are_faq(self) -> None:
        """All retrieved chunks are classified as FAQ."""
        chunks = self._build_candidates()
        for c in chunks:
            assert c.knowledge_class == "FAQ"

    def test_automation_label_absent_for_knowledge_chunks(self) -> None:
        """Knowledge chunks do not carry automation_label."""
        chunks = self._build_candidates()
        for c in chunks:
            assert c.automation_label is None

    def test_image_metadata_field_present(self) -> None:
        """image_metadata field is present and is a list (even if empty)."""
        chunks = self._build_candidates()
        for c in chunks:
            assert isinstance(c.image_metadata, list)


# ===========================================================================
# Test 5: "API callback not triggered"
# Expected: TROUBLESHOOTING chunk
# ===========================================================================

class TestAPICallbackNotTriggered:
    """Query: 'API callback not triggered'"""

    def _build_candidates(self) -> list[_MockRetrievedChunk]:
        return [
            _make_knowledge_chunk(
                "api_callback_troubleshoot",
                "API callback not firing: verify the callback URL is whitelisted in the KwikID portal. "
                "Check if the HTTPS certificate is valid. Inspect the webhook logs for 4xx/5xx errors.",
                knowledge_class = "TROUBLESHOOTING",
                similarity      = 0.82,
                quality_score   = 0.88,
                chunk_type      = "TROUBLESHOOTING",
            ),
            _make_knowledge_chunk(
                "api_callback_rca",
                "Root cause documented: callback was blocked by firewall rule on port 443. "
                "Resolution: add IP whitelist in network config.",
                knowledge_class = "RCA",
                similarity      = 0.70,
                quality_score   = 0.75,
                chunk_type      = "TROUBLESHOOTING",
                has_rca         = True,
            ),
        ]

    def test_troubleshooting_ranks_first(self) -> None:
        """Higher similarity + quality → TROUBLESHOOTING chunk ranks above RCA."""
        chunks = self._build_candidates()
        ranked = _rank_chunks(chunks)
        assert ranked[0].chunk_id == "api_callback_troubleshoot"

    def test_rca_chunk_present(self) -> None:
        """RCA knowledge chunk is surfaced alongside TROUBLESHOOTING."""
        chunks = self._build_candidates()
        rca_chunks = [c for c in chunks if c.knowledge_class == "RCA"]
        assert len(rca_chunks) >= 1
        assert rca_chunks[0].has_rca is True

    def test_knowledge_class_typed_correctly(self) -> None:
        """All knowledge_class values are strings from the expected set."""
        valid_classes = {"VERIFIED_REPLY", "TROUBLESHOOTING", "FAQ", "POLICY", "RCA", "ESCALATION"}
        chunks = self._build_candidates()
        for c in chunks:
            if c.knowledge_class is not None:
                assert c.knowledge_class in valid_classes, (
                    f"unexpected knowledge_class: {c.knowledge_class}"
                )

    def test_quality_score_range(self) -> None:
        """All quality scores are within [0.0, 1.0]."""
        chunks = self._build_candidates()
        for c in chunks:
            if c.quality_score is not None:
                assert 0.0 <= c.quality_score <= 1.0

    def test_boost_formula_knowledge_vs_sop(self) -> None:
        """Knowledge boost (+0.08 + quality*0.05) vs SOP boost (+0.15) at same similarity."""
        sim = 0.70
        quality = 0.88
        knowledge_score = _knowledge_boosted_score(sim, quality)
        sop_score       = _sop_boosted_score(sim)
        # knowledge_score = 0.70 + 0.08 + 0.88*0.05 = 0.824
        # sop_score       = 0.70 + 0.15              = 0.850
        # SOP still wins at this quality level unless knowledge quality >> 1.4
        assert sop_score > knowledge_score, (
            "SOP boost should exceed knowledge boost at equal similarity when quality < 1.4"
        )


# ===========================================================================
# Test 6: Quality gate edge cases
# ===========================================================================

class TestQualityGateEdgeCases:

    def test_exactly_at_gate_passes(self) -> None:
        """quality_score == 0.55 passes the gate (inclusive boundary)."""
        assert _passes_quality_gate(0.55)

    def test_below_gate_fails(self) -> None:
        """quality_score == 0.54 fails the gate."""
        assert not _passes_quality_gate(0.54)

    def test_none_quality_fails(self) -> None:
        """None quality_score fails the gate (ticket/SOP chunks have no quality_score)."""
        assert not _passes_quality_gate(None)

    def test_max_boost_capped_at_1(self) -> None:
        """boosted_score is capped at 1.0 regardless of similarity + bonus."""
        score = _knowledge_boosted_score(1.0, 1.0)
        assert score == 1.0

    def test_has_rca_true_is_boolean(self) -> None:
        """has_rca field is always a bool (not int/None)."""
        chunk = _make_ticket_chunk("x", "content", similarity=0.7, has_rca=True)
        assert isinstance(chunk.has_rca, bool)
        assert chunk.has_rca is True
