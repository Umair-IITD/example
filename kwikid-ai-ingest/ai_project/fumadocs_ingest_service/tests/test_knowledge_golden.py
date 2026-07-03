"""
tests/test_knowledge_golden.py

Sprint 2.37 — Golden Knowledge Regression Suite

Asserts that specific operational knowledge is present in rag_knowledge_chunks
for 7 representative article categories.  These tests MUST FAIL if future
re-ingestion silently removes or corrupts operational content.

Categories:
    IMAGE-HEAVY     so_890  — PAN Verification debug, 15 inline OCR images
    OCR-HEAVY       so_453  — RBL callback retrigger, 14 OCR images
    CODE-HEAVY      so_1488 — Unity callback checker script, 23 code chunks
    SOP-HEAVY       so_236  — CBI video recovery procedure, 51 numbered steps
    ACCEPTED-ANSWER so_24   — TCOOKP put booking, curl command accepted answer
    MULTI-ANSWER    so_1587 — CBI VKYC backend intermittent downtime, 11 chunks
    TABLE-HEAVY     so_1337 — Unity Admin Config, 408+ pipe chars

Run with:
    pytest tests/test_knowledge_golden.py -v -m integration
    pytest tests/test_knowledge_golden.py -v          # skips if no DB creds
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(SERVICE_ROOT))

from dotenv import load_dotenv
load_dotenv(SERVICE_ROOT / ".env")

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def supabase():
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_KEY", "")
    if not url or not key:
        pytest.skip("SUPABASE_URL / SUPABASE_KEY not set — skipping golden knowledge tests")
    from supabase import create_client
    return create_client(url, key)


def _fetch_article(sb, article_id: str) -> dict:
    r = sb.table("rag_knowledge_articles").select(
        "article_id,title,knowledge_class,quality_score,accepted_answer_id,image_metadata"
    ).eq("article_id", article_id).execute()
    assert r.data, f"Article {article_id} not found in rag_knowledge_articles"
    return r.data[0]


def _fetch_chunks(sb, article_id: str) -> list[dict]:
    r = sb.table("rag_knowledge_chunks").select(
        "chunk_type,chunk_index,content"
    ).eq("article_id", article_id).eq("index_version", "v2").order("chunk_index").execute()
    return r.data


def _all_content(chunks: list[dict]) -> str:
    return "\n".join(c.get("content", "") for c in chunks)


# ─── IMAGE-HEAVY: so_890 ─────────────────────────────────────────────────────

class TestImageHeavy:
    """so_890 — PAN Verification debug (15 images, all with OCR)."""

    ARTICLE_ID = "so_890"

    def test_article_exists(self, supabase):
        art = _fetch_article(supabase, self.ARTICLE_ID)
        assert art["article_id"] == self.ARTICLE_ID

    def test_has_fifteen_or_more_images(self, supabase):
        art = _fetch_article(supabase, self.ARTICLE_ID)
        images = art.get("image_metadata") or []
        assert len(images) >= 15, (
            f"Expected >= 15 images in so_890, got {len(images)}. "
            "OCR image data may have been stripped during re-ingestion."
        )

    def test_image_markers_in_chunks(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "[IMAGE:" in content, (
            "Expected inline [IMAGE: ...] OCR markers in so_890 chunks. "
            "Inline OCR injection may have regressed."
        )

    def test_ocr_marker_count_at_least_ten(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        count = content.count("[IMAGE:")
        assert count >= 10, (
            f"Expected >= 10 [IMAGE: markers in so_890 chunks, found {count}. "
            "OCR injection completeness may have regressed."
        )

    def test_systemctl_status_ocr_present(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "systemctlstatuskwikid" in content or "systemctl status" in content.lower() or "ubuntueip-172-29-11-111" in content, (
            "Expected systemctl status OCR content from so_890 terminal screenshot to be present. "
            "Specific OCR text may have been lost during re-ingestion."
        )

    def test_verify_pan_endpoint_present(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "verify_pan" in content, (
            "Expected 'verify_pan' endpoint text in so_890 chunks. "
            "Core operational content may have been lost."
        )


# ─── OCR-HEAVY: so_453 ───────────────────────────────────────────────────────

class TestOCRHeavy:
    """so_453 — RBL callback retrigger script (14 OCR images, 124 multi-answer separators)."""

    ARTICLE_ID = "so_453"

    def test_article_exists(self, supabase):
        art = _fetch_article(supabase, self.ARTICLE_ID)
        assert art["article_id"] == self.ARTICLE_ID

    def test_has_fourteen_or_more_ocr_images(self, supabase):
        art = _fetch_article(supabase, self.ARTICLE_ID)
        images = art.get("image_metadata") or []
        ocr_count = sum(1 for m in images if m.get("ocr_text"))
        assert ocr_count >= 14, (
            f"Expected >= 14 OCR images in so_453, got {ocr_count}. "
            "OCR processing may have regressed."
        )

    def test_image_markers_in_chunks(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "[IMAGE:" in content, (
            "Expected [IMAGE: ...] OCR markers in so_453 chunks."
        )

    def test_dynamodb_query_command_present(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "aws dynamodb query" in content, (
            "Expected 'aws dynamodb query' command in so_453 chunks. "
            "Critical RBL operational knowledge may have been lost."
        )

    def test_session_status_table_referenced(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "kwikid_vkyc_session" in content or "session_status" in content, (
            "Expected DynamoDB table name reference in so_453 chunks."
        )

    def test_callback_external_command_present(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "callback_external" in content or "product_code_specific_callback" in content, (
            "Expected callback trigger command in so_453 chunks. "
            "RBL callback retrigger procedure may have been lost."
        )

    def test_has_code_sample_chunks(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        code_chunks = [c for c in chunks if c["chunk_type"] in ("CODE_SAMPLE", "COMMAND")]
        assert len(code_chunks) >= 1, "Expected at least 1 CODE_SAMPLE/COMMAND chunk in so_453."


# ─── CODE-HEAVY: so_1488 ─────────────────────────────────────────────────────

class TestCodeHeavy:
    """so_1488 — Unity callback checker script (23 code chunks)."""

    ARTICLE_ID = "so_1488"

    def test_article_exists(self, supabase):
        art = _fetch_article(supabase, self.ARTICLE_ID)
        assert art["article_id"] == self.ARTICLE_ID

    def test_has_twenty_or_more_code_chunks(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        code_chunks = [c for c in chunks if c["chunk_type"] in ("CODE_SAMPLE", "COMMAND")]
        assert len(code_chunks) >= 20, (
            f"Expected >= 20 CODE_SAMPLE/COMMAND chunks in so_1488, got {len(code_chunks)}. "
            "Code chunking may have regressed or the code-heavy article changed."
        )

    def test_callback_script_name_present(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "check_callback_responses.py" in content, (
            "Expected script filename 'check_callback_responses.py' in so_1488 chunks. "
            "Critical operational script reference may have been lost."
        )

    def test_aws_configure_command_present(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "aws configure" in content, (
            "Expected 'aws configure' command in so_1488 chunks."
        )

    def test_script_creation_steps_present(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "/home/ubuntu/script" in content, (
            "Expected script directory '/home/ubuntu/script' in so_1488 chunks."
        )

    def test_chmod_step_present(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "chmod" in content and "check_callback_responses" in content, (
            "Expected chmod step for check_callback_responses.py in so_1488 chunks."
        )


# ─── SOP-HEAVY: so_236 ───────────────────────────────────────────────────────

class TestSOPHeavy:
    """so_236 — CBI Video recovery procedure (51 numbered steps)."""

    ARTICLE_ID = "so_236"

    def test_article_exists(self, supabase):
        art = _fetch_article(supabase, self.ARTICLE_ID)
        assert art["article_id"] == self.ARTICLE_ID

    def test_cbi_video_script_present(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "cbi_video.py" in content, (
            "Expected 'cbi_video.py' in so_236 chunks. "
            "CBI video recovery script reference may have been lost."
        )

    def test_python_run_command_present(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "python cbi_video.py" in content, (
            "Expected 'python cbi_video.py' run command in so_236 chunks. "
            "SOP execution step may have been lost."
        )

    def test_script_directory_present(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "/home/ubuntu/script" in content, (
            "Expected '/home/ubuntu/script' directory path in so_236 chunks."
        )

    def test_venv_activation_present(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "venv/bin/activate" in content or "source venv" in content, (
            "Expected venv activation step in so_236 chunks."
        )

    def test_open_code_file_step_present(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "vi cbi_video.py" in content, (
            "Expected 'vi cbi_video.py' edit step in so_236 chunks."
        )

    def test_multiple_chunks_present(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        assert len(chunks) >= 10, (
            f"Expected >= 10 chunks in so_236 (SOP article), got {len(chunks)}."
        )


# ─── ACCEPTED-ANSWER: so_24 ──────────────────────────────────────────────────

class TestAcceptedAnswer:
    """so_24 — TCOOKP put booking (accepted_answer_id=25, curl command)."""

    ARTICLE_ID = "so_24"

    def test_article_exists(self, supabase):
        art = _fetch_article(supabase, self.ARTICLE_ID)
        assert art["article_id"] == self.ARTICLE_ID

    def test_has_accepted_answer_id(self, supabase):
        art = _fetch_article(supabase, self.ARTICLE_ID)
        assert art.get("accepted_answer_id") is not None, (
            "Expected accepted_answer_id to be set on so_24. "
            "Accepted answer linkage may have been lost during re-ingestion."
        )

    def test_verified_answer_prefix_in_chunks(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "VERIFIED ANSWER:" in content or "VERIFIED_ANSWER" in content.upper(), (
            "Expected 'VERIFIED ANSWER:' prefix in so_24 chunks. "
            "Accepted answer highlighting may have been lost."
        )

    def test_put_booking_curl_command_present(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "booking/put/" in content, (
            "Expected 'booking/put/' curl endpoint in so_24 chunks. "
            "Critical TCOOKP operational command may have been lost."
        )

    def test_slot_get_api_present(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "slot/get/day" in content or "videokyc.thomascook.in" in content, (
            "Expected Thomas Cook VKYC slot API URL in so_24 chunks."
        )

    def test_has_code_sample_with_curl(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        code_chunks = [c for c in chunks if c["chunk_type"] in ("CODE_SAMPLE", "COMMAND")]
        assert code_chunks, "Expected at least 1 CODE_SAMPLE/COMMAND chunk in so_24."
        code_content = _all_content(code_chunks)
        assert "curl" in code_content, "Expected 'curl' in CODE_SAMPLE chunks of so_24."


# ─── MULTI-ANSWER: so_1587 ───────────────────────────────────────────────────

class TestMultiAnswer:
    """so_1587 — CBI VKYC/DKYC Agent Backend intermittent downtime (11 chunks, network SOP)."""

    ARTICLE_ID = "so_1587"

    def test_article_exists(self, supabase):
        art = _fetch_article(supabase, self.ARTICLE_ID)
        assert art["article_id"] == self.ARTICLE_ID

    def test_network_level_diagnosis_present(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "network level issue" in content, (
            "Expected 'network level issue' diagnosis in so_1587 chunks. "
            "CBI backend downtime SOP may have been lost."
        )

    def test_dns_resolution_step_present(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "DNS Resolution" in content or "nslookup" in content, (
            "Expected DNS resolution step in so_1587 chunks. "
            "Network diagnostic SOP may have been truncated."
        )

    def test_cbi_domain_referenced(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "centralbank.bank.in" in content or "vkyc.centralbank" in content, (
            "Expected CBI domain reference in so_1587 chunks. "
            "Client-specific operational knowledge may have been lost."
        )

    def test_health_endpoint_monitoring_present(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "agent/health" in content or "health" in content.lower(), (
            "Expected health endpoint reference in so_1587 chunks."
        )

    def test_multiple_chunks_multi_answer(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        assert len(chunks) >= 8, (
            f"Expected >= 8 chunks for multi-answer so_1587, got {len(chunks)}. "
            "Multi-answer article may have been collapsed during re-ingestion."
        )


# ─── TABLE-HEAVY: so_1337 ────────────────────────────────────────────────────

class TestTableHeavy:
    """so_1337 — Unity Admin Config (41 chunks, 408+ pipe chars, configuration tables)."""

    ARTICLE_ID = "so_1337"

    def test_article_exists(self, supabase):
        art = _fetch_article(supabase, self.ARTICLE_ID)
        assert art["article_id"] == self.ARTICLE_ID

    def test_unity_admin_portal_reference(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "Unity Bank Admin Portal" in content or "Unity Admin" in content, (
            "Expected 'Unity Bank Admin Portal' reference in so_1337 chunks."
        )

    def test_table_pipe_chars_present(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        pipe_count = content.count("|")
        assert pipe_count >= 200, (
            f"Expected >= 200 pipe chars (markdown tables) in so_1337 chunks, found {pipe_count}. "
            "Table content may have been stripped during re-ingestion."
        )

    def test_config_s3_command_present(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "aws s3 ls s3://kwikid-prod/CONFIG/ADMIN/" in content, (
            "Expected S3 config path command in so_1337 chunks. "
            "Unity Admin Config S3 operational knowledge may have been lost."
        )

    def test_configuration_guide_content_present(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        content = _all_content(chunks)
        assert "Configuration Guide" in content or "Admin Portal" in content, (
            "Expected configuration guide content in so_1337 chunks."
        )

    def test_has_many_chunks(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        assert len(chunks) >= 30, (
            f"Expected >= 30 chunks for table-heavy so_1337, got {len(chunks)}. "
            "Large config article may have been truncated."
        )

    def test_has_code_sample_chunks(self, supabase):
        chunks = _fetch_chunks(supabase, self.ARTICLE_ID)
        code_chunks = [c for c in chunks if c["chunk_type"] in ("CODE_SAMPLE", "COMMAND")]
        assert len(code_chunks) >= 1, "Expected at least 1 CODE_SAMPLE/COMMAND chunk in so_1337."
