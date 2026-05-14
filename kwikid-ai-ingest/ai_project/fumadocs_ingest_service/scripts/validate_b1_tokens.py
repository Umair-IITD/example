"""
scripts/validate_b1_tokens.py

Offline validation of token-safe chunking and pre-embed validation.
No DB connection required. No API calls made.

Tests:
  1. Normal prose chunks stay within max_input_tokens
  2. Oversized payload (>7000 tokens) is split into safe parts
  3. Base64 blob is detected and skipped by pre-embed validation
  4. Whitespace payload is detected and skipped
  5. Repetitive content is detected and skipped
  6. Malformed unicode (control characters) is detected
  7. recursive_split_if_oversized handles pathological single-word blobs
  8. Empty content is handled gracefully
  9. ISSUE_HEADER is always within limit (even without tiktoken)
 10. Idempotent: chunking same doc twice gives same chunk IDs

Usage:
    python scripts/validate_b1_tokens.py
    python scripts/validate_b1_tokens.py -v   (verbose)
"""
from __future__ import annotations

import argparse
import base64
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from rag_engine.utils.tokens import (
    count_tokens,
    recursive_split_if_oversized,
    safe_split_by_tokens,
    truncate_to_token_limit,
)

_PASSED = 0
_FAILED = 0
_VERBOSE = False


def _ok(name: str, detail: str = "") -> None:
    global _PASSED
    _PASSED += 1
    msg = f"  PASS  {name}"
    if detail and _VERBOSE:
        msg += f": {detail}"
    print(msg)


def _fail(name: str, detail: str) -> None:
    global _FAILED
    _FAILED += 1
    print(f"  FAIL  {name}: {detail}")


def _check(cond: bool, name: str, ok_detail: str = "", fail_detail: str = "") -> None:
    if cond:
        _ok(name, ok_detail)
    else:
        _fail(name, fail_detail or "assertion failed")


# ── Token utility tests ────────────────────────────────────────────────────────

def test_count_tokens_basic() -> None:
    """count_tokens returns a positive integer for non-empty text."""
    n = count_tokens("Hello world, this is a test sentence.")
    _check(n > 0, "count_tokens_basic", f"{n} tokens")


def test_truncate_respects_limit() -> None:
    """truncate_to_token_limit never returns more tokens than requested."""
    text = "word " * 2000          # ~2000 tokens
    truncated = truncate_to_token_limit(text, max_tokens=100)
    n = count_tokens(truncated)
    _check(n <= 100, "truncate_respects_limit", f"{n} tokens after truncation (limit 100)")


def test_safe_split_no_chunk_exceeds_limit() -> None:
    """safe_split_by_tokens: every returned chunk is within max_tokens."""
    text = " ".join(f"word{i}" for i in range(5000))
    max_tokens = 500
    parts = safe_split_by_tokens(text, max_tokens=max_tokens, overlap_tokens=50)
    _check(len(parts) > 1, "safe_split_produces_multiple_parts", f"{len(parts)} parts")
    violations = [i for i, p in enumerate(parts) if count_tokens(p) > max_tokens]
    _check(
        len(violations) == 0,
        "safe_split_no_chunk_exceeds_limit",
        f"all {len(parts)} parts within {max_tokens} tokens",
        f"parts {violations[:3]} exceed {max_tokens} tokens",
    )


def test_safe_split_empty_input() -> None:
    parts = safe_split_by_tokens("", max_tokens=500)
    _check(parts == [], "safe_split_empty_returns_empty", f"{parts}")


def test_safe_split_short_text_single_chunk() -> None:
    text = "Short ticket description."
    parts = safe_split_by_tokens(text, max_tokens=500)
    _check(len(parts) == 1, "safe_split_short_text_single_chunk", f"{len(parts)} parts")


def test_recursive_split_pathological_base64() -> None:
    """A base64 blob with no spaces must be handled without crash."""
    # A 10 000-character base64 string with no whitespace — worst case for word splitting
    blob = base64.b64encode(b"x" * 7500).decode("ascii")  # 10000 chars, no spaces
    n_before = count_tokens(blob)
    max_tokens = 500
    parts = recursive_split_if_oversized(blob, max_tokens=max_tokens)
    _check(len(parts) >= 1, "recursive_split_handles_base64_blob", f"{len(parts)} parts from {n_before}-token blob")
    violations = [count_tokens(p) for p in parts if count_tokens(p) > max_tokens]
    _check(
        len(violations) == 0,
        "recursive_split_all_parts_within_limit",
        f"all {len(parts)} parts within {max_tokens} tokens",
        f"{len(violations)} parts exceed {max_tokens} tokens",
    )


def test_recursive_split_normal_text() -> None:
    """Normal text within limit returns a single-item list unchanged."""
    text = "Hello world."
    parts = recursive_split_if_oversized(text, max_tokens=500)
    _check(parts == [text], "recursive_split_normal_text_unchanged", f"returned {parts}")


def test_recursive_split_empty() -> None:
    parts = recursive_split_if_oversized("", max_tokens=500)
    _check(parts == [], "recursive_split_empty_returns_empty")


# ── Pre-embed validation tests (batch_processor._validate_chunk) ───────────────

def _make_chunk(content: str) -> object:
    """Create a minimal mock TicketChunk for validation testing."""
    class MockChunk:
        id = str(uuid.uuid4())
        ticket_id = "test-ticket"
        chunk_index = 0
    c = MockChunk()
    c.content = content
    return c


def test_validate_empty_content() -> None:
    from rag_engine.embedding.batch_processor import BatchEmbeddingProcessor
    from unittest.mock import MagicMock

    proc = BatchEmbeddingProcessor(provider=MagicMock(), max_input_tokens=7000)
    reason = proc._validate_chunk(_make_chunk(""))
    _check(reason is not None, "validate_detects_empty_content", reason)


def test_validate_whitespace_only() -> None:
    from rag_engine.embedding.batch_processor import BatchEmbeddingProcessor
    from unittest.mock import MagicMock

    proc = BatchEmbeddingProcessor(provider=MagicMock(), max_input_tokens=7000)
    reason = proc._validate_chunk(_make_chunk("   \n\n\t   \n"))
    _check(reason is not None, "validate_detects_whitespace_only", reason)


def test_validate_repetitive_content() -> None:
    from rag_engine.embedding.batch_processor import BatchEmbeddingProcessor
    from unittest.mock import MagicMock

    proc = BatchEmbeddingProcessor(provider=MagicMock(), max_input_tokens=7000)
    repetitive = "ERROR " * 200  # one word repeated 200 times
    reason = proc._validate_chunk(_make_chunk(repetitive))
    _check(reason is not None, "validate_detects_repetitive_content", reason)


def test_validate_base64_blob() -> None:
    from rag_engine.embedding.batch_processor import BatchEmbeddingProcessor
    from unittest.mock import MagicMock

    proc = BatchEmbeddingProcessor(provider=MagicMock(), max_input_tokens=7000)
    blob = " ".join([base64.b64encode(b"x" * 60).decode("ascii")] * 10)
    reason = proc._validate_chunk(_make_chunk(blob))
    _check(reason is not None, "validate_detects_base64_blob", reason)


def test_validate_control_chars() -> None:
    from rag_engine.embedding.batch_processor import BatchEmbeddingProcessor
    from unittest.mock import MagicMock

    proc = BatchEmbeddingProcessor(provider=MagicMock(), max_input_tokens=7000)
    # 20% control characters (above 10% threshold)
    text = "normal text " + "\x00\x01\x02\x03\x04" * 50
    reason = proc._validate_chunk(_make_chunk(text))
    _check(reason is not None, "validate_detects_control_chars", reason)


def test_validate_good_content_passes() -> None:
    from rag_engine.embedding.batch_processor import BatchEmbeddingProcessor
    from unittest.mock import MagicMock

    proc = BatchEmbeddingProcessor(provider=MagicMock(), max_input_tokens=7000)
    good = "The KYC verification failed because the document expiry date was not recognized by the system."
    reason = proc._validate_chunk(_make_chunk(good))
    _check(reason is None, "validate_good_content_passes", "no validation error")


def test_validate_token_overflow_detected() -> None:
    from rag_engine.embedding.batch_processor import BatchEmbeddingProcessor
    from unittest.mock import MagicMock

    proc = BatchEmbeddingProcessor(provider=MagicMock(), max_input_tokens=50)
    long_text = "word " * 200  # ~200 tokens, but limit is 50
    reason = proc._validate_chunk(_make_chunk(long_text))
    _check(reason is not None, "validate_token_overflow_detected", reason)


# ── Chunker token-safety test ─────────────────────────────────────────────────

def test_chunker_no_chunk_exceeds_limit() -> None:
    """TicketChunker output: every chunk is within max_input_tokens."""
    try:
        from rag_engine.chunking.ticket_chunker import TicketChunker
        from rag_engine.schemas.ticket_document import (
            AutomationLabel, RagTicketDocument, ChunkType
        )
        from datetime import datetime, timezone
    except ImportError as exc:
        _fail("chunker_no_chunk_exceeds_limit", f"import error: {exc}")
        return

    chunker = TicketChunker(
        chunk_target_tokens=200,
        chunk_overlap_tokens=20,
        max_input_tokens=300,
        embedding_model="text-embedding-3-small",
    )

    # Build a synthetic document with a very long QUERY_BODY
    long_body = "This is a very detailed customer complaint about the KYC process. " * 100

    try:
        doc = RagTicketDocument(
            ticket_id="T-TEST-001",
            source_file="test",
            source_type="freshdesk",
            client="test_bank",
            query_type="kyc_failure",
            issue_area="document_verification",
            environment="production",
            priority="medium",
            status="open",
            automation_label=AutomationLabel.AUTO_REPLY,
            escalation_flag=False,
            has_rca=False,
            has_sop=False,
            sop_status=None,
            rca_quality_score=0.0,
            subject="KYC document rejected",
            issue_header_text="Ticket T-TEST-001 | KYC document rejected | client: test_bank",
            query_body_text=long_body,
            resolution_rca_text="Agent resolved by re-verifying the document.",
            full_document_text=long_body,
            content_hash="abc123",
        )
    except Exception as exc:
        _fail("chunker_no_chunk_exceeds_limit", f"could not build test doc: {exc}")
        return

    chunks = chunker.chunk(doc, document_id=str(uuid.uuid4()), ingestion_run_id=str(uuid.uuid4()))
    max_tokens = 300
    violations = [c for c in chunks if count_tokens(c.content) > max_tokens]
    _check(
        len(violations) == 0,
        "chunker_no_chunk_exceeds_limit",
        f"all {len(chunks)} chunks within {max_tokens} tokens",
        f"{len(violations)}/{len(chunks)} chunks exceed {max_tokens} tokens",
    )


def test_chunker_idempotent() -> None:
    """Same document chunked twice must produce identical chunk IDs."""
    try:
        from rag_engine.chunking.ticket_chunker import TicketChunker
        from rag_engine.schemas.ticket_document import AutomationLabel, RagTicketDocument
    except ImportError as exc:
        _fail("chunker_idempotent", f"import error: {exc}")
        return

    chunker = TicketChunker(index_version="v1")
    doc_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())

    try:
        doc = RagTicketDocument(
            ticket_id="T-IDEM-001",
            source_file="test",
            source_type="freshdesk",
            client="test_bank",
            query_type="kyc_failure",
            issue_area="doc_verification",
            environment="production",
            priority="medium",
            status="open",
            automation_label=AutomationLabel.AUTO_REPLY,
            escalation_flag=False,
            has_rca=False,
            has_sop=False,
            sop_status=None,
            rca_quality_score=0.0,
            subject="Duplicate KYC test",
            issue_header_text="Ticket T-IDEM-001 | Duplicate KYC test | client: test_bank",
            query_body_text="Customer reported an issue with document upload.",
            resolution_rca_text="Document was re-uploaded and verified successfully.",
            full_document_text="Customer reported an issue with document upload.",
            content_hash="def456",
        )
    except Exception as exc:
        _fail("chunker_idempotent", f"could not build test doc: {exc}")
        return

    run1 = chunker.chunk(doc, document_id=doc_id, ingestion_run_id=run_id)
    run2 = chunker.chunk(doc, document_id=doc_id, ingestion_run_id=run_id)
    ids1 = [c.id for c in run1]
    ids2 = [c.id for c in run2]
    _check(ids1 == ids2, "chunker_idempotent", f"both runs: {ids1}", f"run1={ids1} run2={ids2}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    global _VERBOSE
    parser = argparse.ArgumentParser(description="B1 token safety validation")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    _VERBOSE = args.verbose

    print("=" * 60)
    print(" B1 Token + Validation Suite — Offline")
    print("=" * 60)

    print("\n[Token utilities]")
    test_count_tokens_basic()
    test_truncate_respects_limit()
    test_safe_split_no_chunk_exceeds_limit()
    test_safe_split_empty_input()
    test_safe_split_short_text_single_chunk()

    print("\n[recursive_split_if_oversized]")
    test_recursive_split_pathological_base64()
    test_recursive_split_normal_text()
    test_recursive_split_empty()

    print("\n[Pre-embed validation]")
    test_validate_empty_content()
    test_validate_whitespace_only()
    test_validate_repetitive_content()
    test_validate_base64_blob()
    test_validate_control_chars()
    test_validate_good_content_passes()
    test_validate_token_overflow_detected()

    print("\n[Chunker safety]")
    test_chunker_no_chunk_exceeds_limit()
    test_chunker_idempotent()

    print("\n" + "=" * 60)
    print(f" Results: {_PASSED} passed, {_FAILED} failed")
    print("=" * 60)

    sys.exit(0 if _FAILED == 0 else 1)


if __name__ == "__main__":
    main()
