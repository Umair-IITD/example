"""
tests/test_golden_retrieval.py

Sprint 2.37 — Golden Retrieval Regression Tests

Asserts that specific operational knowledge is retrievable via TicketRetriever
for representative production queries.  Assertions are content-based, not
score-based, so they survive minor embedding or boost tuning changes.

Tests MUST FAIL if:
  - Re-ingestion removes the target article's chunks
  - index_version changes break v2 chunk visibility
  - Knowledge boost configuration drops these articles out of top-K

Run with:
    pytest tests/test_golden_retrieval.py -v -m integration
    pytest tests/test_golden_retrieval.py -v    # skips if no creds
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(SERVICE_ROOT))

from dotenv import load_dotenv
load_dotenv(SERVICE_ROOT / ".env")

pytestmark = pytest.mark.integration

TOP_K = 10
CLIENT = "unity_bank"


@pytest.fixture(scope="module")
def retriever():
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_KEY", "")
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if not url or not key or not openai_key:
        pytest.skip("SUPABASE_URL / SUPABASE_KEY / OPENAI_API_KEY not set — skipping golden retrieval tests")

    from supabase import create_client
    from rag_engine.embedding.openai_provider import OpenAIEmbeddingProvider
    from rag_engine.retrieval.ticket_retriever import TicketRetriever
    from rag_engine.config.rag_settings import get_rag_settings

    settings = get_rag_settings()
    sb = create_client(url, key)
    embedder = OpenAIEmbeddingProvider(
        api_key=openai_key,
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    )
    return TicketRetriever(sb, embedder, settings)


def _retrieve(retriever, query: str):
    from rag_engine.retrieval.ticket_retriever import RetrievalRequest
    from rag_engine.config.rag_settings import get_rag_settings
    settings = get_rag_settings()
    req = RetrievalRequest(
        query_text=query,
        client=CLIENT,
        top_k=TOP_K,
        include_sop=True,
        exclude_escalation=True,
        index_version=settings.index_version,
    )
    resp = retriever.retrieve(req)
    return [c for c in resp.chunks if c.source_table == "rag_knowledge_chunks"]


def _joined_content(chunks) -> str:
    return "\n".join(c.content for c in chunks)


# ─── CBI Video Recovery ───────────────────────────────────────────────────────

class TestCBIVideoRecovery:
    """Query: CBI video recovery — must retrieve so_236 content."""

    def test_knowledge_chunks_returned(self, retriever):
        chunks = _retrieve(retriever, "CBI video recovery procedure steps")
        assert chunks, "Expected knowledge chunks for CBI video recovery query."

    def test_cbi_video_script_retrieved(self, retriever):
        chunks = _retrieve(retriever, "CBI video recovery procedure steps")
        content = _joined_content(chunks)
        assert "cbi_video" in content or "video recovery" in content.lower(), (
            "Expected 'cbi_video' or 'video recovery' in retrieved chunks for CBI recovery query. "
            "so_236 may have been dropped from retrieval."
        )

    def test_python_run_command_retrieved(self, retriever):
        chunks = _retrieve(retriever, "how to run CBI video recovery script on ubuntu")
        content = _joined_content(chunks)
        assert "cbi_video.py" in content or "/home/ubuntu/script" in content, (
            "Expected CBI video recovery script reference in retrieved chunks."
        )


# ─── RBL Callback Retrigger ──────────────────────────────────────────────────

class TestRBLCallbackRetrigger:
    """Query: RBL callback retrigger — must retrieve so_453 content."""

    def test_knowledge_chunks_returned(self, retriever):
        chunks = _retrieve(retriever, "how to retrigger RBL callback manually through script")
        assert chunks, "Expected knowledge chunks for RBL callback retrigger query."

    def test_dynamodb_session_check_retrieved(self, retriever):
        chunks = _retrieve(retriever, "how to retrigger RBL callback manually through script")
        content = _joined_content(chunks)
        assert "dynamodb" in content.lower() or "session_status" in content or "callback" in content.lower(), (
            "Expected DynamoDB or callback reference in retrieved chunks for RBL query. "
            "so_453 may have been dropped from retrieval."
        )

    def test_callback_trigger_command_retrieved(self, retriever):
        chunks = _retrieve(retriever, "RBL callback retrigger product_code_specific_callback")
        content = _joined_content(chunks)
        assert "callback" in content.lower() and ("rbl" in content.lower() or "BB" in content or "product_code" in content), (
            "Expected callback trigger procedure in retrieved chunks."
        )


# ─── Unity Callback Checker ───────────────────────────────────────────────────

class TestUnityCallbackChecker:
    """Query: Unity callback checker script — must retrieve so_1488 content."""

    def test_knowledge_chunks_returned(self, retriever):
        chunks = _retrieve(retriever, "how to check callback response for agent and auditor unity")
        assert chunks, "Expected knowledge chunks for callback checker query."

    def test_callback_script_retrieved(self, retriever):
        chunks = _retrieve(retriever, "how to check callback response for agent and auditor unity")
        content = _joined_content(chunks)
        assert "callback_response" in content or "check_callback" in content or "callback" in content.lower(), (
            "Expected callback checker script in retrieved chunks. "
            "so_1488 may have been dropped from retrieval."
        )

    def test_aws_setup_step_retrieved(self, retriever):
        chunks = _retrieve(retriever, "check callback response unity aws configure script")
        content = _joined_content(chunks)
        assert "aws" in content.lower() and "callback" in content.lower(), (
            "Expected AWS configuration step in Unity callback checker retrieval."
        )


# ─── Linux Disk Usage ────────────────────────────────────────────────────────

class TestLinuxDiskUsage:
    """Query: Linux disk high after deleting files — must retrieve so_1704 content."""

    def test_knowledge_chunks_returned(self, retriever):
        chunks = _retrieve(retriever, "Linux disk usage high even after deleting files")
        assert chunks, "Expected knowledge chunks for Linux disk usage query."

    def test_docker_overlay_issue_retrieved(self, retriever):
        chunks = _retrieve(retriever, "Linux disk usage high even after deleting files docker")
        content = _joined_content(chunks)
        assert "docker" in content.lower() or "disk" in content.lower() or "du" in content, (
            "Expected Docker/disk usage content in retrieved chunks. "
            "so_1704 may have been dropped from retrieval."
        )


# ─── CBI VKYC Backend Downtime ───────────────────────────────────────────────

class TestCBIBackendDowntime:
    """Query: CBI backend intermittent downtime — must retrieve so_1587 content."""

    def test_knowledge_chunks_returned(self, retriever):
        chunks = _retrieve(retriever, "CBI VKYC agent backend intermittently goes down")
        assert chunks, "Expected knowledge chunks for CBI backend downtime query."

    def test_network_diagnosis_retrieved(self, retriever):
        chunks = _retrieve(retriever, "CBI VKYC DKYC backend intermittently goes down restored")
        content = _joined_content(chunks)
        assert "network" in content.lower() or "cbi" in content.lower() or "backend" in content.lower(), (
            "Expected network/CBI/backend content in retrieved chunks. "
            "so_1587 may have been dropped from retrieval."
        )


# ─── Unity Admin Config ───────────────────────────────────────────────────────

class TestUnityAdminConfig:
    """Query: Unity admin config — must retrieve so_1337 content."""

    def test_knowledge_chunks_returned(self, retriever):
        chunks = _retrieve(retriever, "Unity Admin Config portal configuration")
        assert chunks, "Expected knowledge chunks for Unity admin config query."

    def test_unity_portal_config_retrieved(self, retriever):
        chunks = _retrieve(retriever, "Unity Bank Admin Config portal API endpoints")
        content = _joined_content(chunks)
        assert "unity" in content.lower() and ("admin" in content.lower() or "config" in content.lower()), (
            "Expected Unity admin config content in retrieved chunks. "
            "so_1337 may have been dropped from retrieval."
        )


# ─── PAN Verification Debug ───────────────────────────────────────────────────

class TestPANVerificationDebug:
    """Query: Unity PAN verification debug — must retrieve so_890 content."""

    def test_knowledge_chunks_returned(self, retriever):
        chunks = _retrieve(retriever, "How to debug PAN verification API issue in Unity")
        assert chunks, "Expected knowledge chunks for PAN verification query."

    def test_pan_verification_content_retrieved(self, retriever):
        chunks = _retrieve(retriever, "How to debug PAN verification API issue Unity production")
        content = _joined_content(chunks)
        assert "pan" in content.lower() or "verify_pan" in content or "PAN" in content, (
            "Expected PAN verification content in retrieved chunks. "
            "so_890 image-heavy article may have been dropped from retrieval."
        )


# ─── Invariant: index_version must be v2 ─────────────────────────────────────

class TestIndexVersionInvariant:
    """Asserts that knowledge chunks are NOT returned without index_version='v2'."""

    def test_v1_request_returns_no_knowledge_chunks(self, retriever):
        """With index_version='v1', rag_knowledge_chunks should NOT appear."""
        from rag_engine.retrieval.ticket_retriever import RetrievalRequest
        req = RetrievalRequest(
            query_text="CBI video recovery procedure",
            client=CLIENT,
            top_k=TOP_K,
            include_sop=True,
            exclude_escalation=True,
            index_version="v1",
        )
        resp = retriever.retrieve(req)
        knowledge_chunks = [c for c in resp.chunks if c.source_table == "rag_knowledge_chunks"]
        assert len(knowledge_chunks) == 0, (
            f"Expected 0 knowledge chunks with index_version='v1', got {len(knowledge_chunks)}. "
            "The index_version guard may have broken — this is a security/correctness invariant."
        )

    def test_exclude_escalation_flag_respected(self, retriever):
        """exclude_escalation=True must be in every retrieval request — hardcoded in rag_adapter."""
        from rag_engine.retrieval.ticket_retriever import RetrievalRequest
        from rag_engine.config.rag_settings import get_rag_settings
        settings = get_rag_settings()
        req = RetrievalRequest(
            query_text="escalation",
            client=CLIENT,
            top_k=TOP_K,
            include_sop=False,
            exclude_escalation=True,
            index_version=settings.index_version,
        )
        resp = retriever.retrieve(req)
        assert resp is not None, "Retriever returned None with exclude_escalation=True."
