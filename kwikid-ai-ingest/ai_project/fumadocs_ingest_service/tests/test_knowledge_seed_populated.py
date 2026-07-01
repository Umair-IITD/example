"""
tests/test_knowledge_seed_populated.py

Verifies that the knowledge repository is NOT empty after startup seeding and that
KNOWLEDGE_LOOKUP workflow steps will receive real results.

Tests:
  test_knowledge_not_empty          — repository has entries after build_knowledge_service()
                                      with the same seed path used in assembly.py
  test_vkyc_search_returns_results  — search("VKYC_Session_Failure") finds a match
  test_otp_search_returns_results   — search("OTP_Delivery_Failure") finds a match
  test_ocr_search_returns_results   — search("Document_OCR_Failure") finds a match
  test_portal_search_returns_results — search("Agent_Portal_Issue") finds a match
  test_callback_search_returns_results — search("API_Callback_Failure") finds a match
  test_all_five_topics_covered      — each topic appears in at least one seed entry
"""
from __future__ import annotations

import sys
import os

import pytest

# ---------------------------------------------------------------------------
# Import the same seed builder that assembly.py uses.
# We import it directly from runtime.assembly so the test exercises the exact
# production path — not a copy.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from runtime.assembly import _build_seed_knowledge_entries
from case_engine.knowledge import build_knowledge_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service():
    """Build a KnowledgeService seeded exactly as assembly.py does at startup."""
    entries = _build_seed_knowledge_entries()
    return build_knowledge_service(seed_entries=entries, audit_logger=None)


def _search_result(service, topic: str, root_cause: str | None = None, action: str | None = None):
    """Call service.search() with a minimal investigation_result dict."""
    investigation_result = None
    if root_cause or action:
        investigation_result = {
            "root_cause": {
                "category": root_cause,
                "recommended_action": action,
                "confidence": 0.8,
                "escalate": False,
            }
        }
    return service.search(topic, investigation_result)


# ---------------------------------------------------------------------------
# Phase 3 proof — repository is populated
# ---------------------------------------------------------------------------

def test_knowledge_not_empty():
    """Repository must have seed entries — InMemoryKnowledgeRepository must NOT start empty."""
    entries = _build_seed_knowledge_entries()
    assert len(entries) > 0, "seed entries list is empty — assembly.py will produce empty repository"


def test_seed_entry_count():
    """Exactly 7 seed entries are built (one per SOP scenario)."""
    entries = _build_seed_knowledge_entries()
    assert len(entries) == 7, f"Expected 7 seed entries, got {len(entries)}"


# ---------------------------------------------------------------------------
# Phase 4 — search returns results per topic
# ---------------------------------------------------------------------------

def test_vkyc_search_returns_results():
    """KNOWLEDGE_LOOKUP for VKYC_Session_Failure must return at least one match."""
    service = _make_service()
    result = _search_result(
        service,
        topic="VKYC_Session_Failure",
        root_cause="EXPIRED_SESSION",
        action="SESSION_RESET",
    )
    assert result["sop_match_found"] is True, (
        "Expected sop_match_found=True for VKYC_Session_Failure with EXPIRED_SESSION"
    )
    assert result["sop_match"] is not None
    assert result["sop_match"]["entry"]["topic_keys"] == ["VKYC_Session_Failure"], (
        f"Wrong topic on match: {result['sop_match']['entry']['topic_keys']}"
    )


def test_otp_search_returns_results():
    """KNOWLEDGE_LOOKUP for OTP_Delivery_Failure must return at least one match."""
    service = _make_service()
    result = _search_result(
        service,
        topic="OTP_Delivery_Failure",
        root_cause="SMS_DELIVERY_FAILURE",
        action="OTP_RESEND",
    )
    assert result["sop_match_found"] is True, (
        "Expected sop_match_found=True for OTP_Delivery_Failure with SMS_DELIVERY_FAILURE"
    )
    matched_topics = result["sop_match"]["entry"]["topic_keys"]
    assert "OTP_Delivery_Failure" in matched_topics, f"Wrong topic: {matched_topics}"


def test_ocr_search_returns_results():
    """KNOWLEDGE_LOOKUP for Document_OCR_Failure must return at least one match."""
    service = _make_service()
    result = _search_result(
        service,
        topic="Document_OCR_Failure",
        root_cause="DOCUMENT_FAILURE",
        action="RETRY",
    )
    assert result["sop_match_found"] is True, (
        "Expected sop_match_found=True for Document_OCR_Failure with DOCUMENT_FAILURE"
    )
    matched_topics = result["sop_match"]["entry"]["topic_keys"]
    assert "Document_OCR_Failure" in matched_topics, f"Wrong topic: {matched_topics}"


def test_portal_search_returns_results():
    """KNOWLEDGE_LOOKUP for Agent_Portal_Issue must return at least one match."""
    service = _make_service()
    result = _search_result(
        service,
        topic="Agent_Portal_Issue",
        root_cause="PORTAL_UNAVAILABLE",
        action="PORTAL_REFRESH",
    )
    assert result["sop_match_found"] is True, (
        "Expected sop_match_found=True for Agent_Portal_Issue with PORTAL_UNAVAILABLE"
    )
    matched_topics = result["sop_match"]["entry"]["topic_keys"]
    assert "Agent_Portal_Issue" in matched_topics, f"Wrong topic: {matched_topics}"


def test_callback_search_returns_results():
    """KNOWLEDGE_LOOKUP for API_Callback_Failure must return at least one match."""
    service = _make_service()
    result = _search_result(
        service,
        topic="API_Callback_Failure",
        root_cause="CALLBACK_FAILURE",
        action="CALLBACK_RETRY",
    )
    assert result["sop_match_found"] is True, (
        "Expected sop_match_found=True for API_Callback_Failure with CALLBACK_FAILURE"
    )
    matched_topics = result["sop_match"]["entry"]["topic_keys"]
    assert "API_Callback_Failure" in matched_topics, f"Wrong topic: {matched_topics}"


def test_all_five_topics_covered():
    """Every required topic must have at least one entry in the seed list."""
    required_topics = {
        "VKYC_Session_Failure",
        "OTP_Delivery_Failure",
        "Document_OCR_Failure",
        "Agent_Portal_Issue",
        "API_Callback_Failure",
    }
    entries = _build_seed_knowledge_entries()
    covered = set()
    for entry in entries:
        for topic in entry.topic_keys:
            covered.add(topic)

    missing = required_topics - covered
    assert not missing, f"These topics have no seed entries: {missing}"


def test_search_without_investigation_result():
    """
    search() must work even when investigation_result is None
    (topic-only match via keyword overlap).
    """
    service = _make_service()
    result = service.search("VKYC_Session_Failure", investigation_result=None)
    # No root-cause hint — topic-key match (+0.30) and keyword overlap may fire
    # At minimum the call must not raise and must return a dict
    assert isinstance(result, dict)
    assert "sop_match_found" in result
    assert "recommendation" in result
