from __future__ import annotations

import json
from pathlib import Path

from app.parser_json import parse_stackoverflow_json_files
from rag_engine.ingestion.knowledge_pipeline import _build_embed_text_raw, _redact_pii
from rag_engine.ingestion.knowledge_classifier import KnowledgeClass
from rag_engine.ingestion.parsers.stackoverflow_parser import StackOverflowParser


def _write_export(tmp_path: Path) -> None:
    question_body = (
        "## Steps\\n"
        "1. Open admin portal\\n"
        "2. Restart service\\n\\n"
        "![server](https://stackoverflowteams.com/c/kwikid/images/s/"
        "46791add-fd95-4bf4-9785-9b7d7ecce7c3.png)"
    )
    posts = [
        {
            "id": 1537,
            "postType": "question",
            "title": "All Clients | Server Up - SOP and Steps",
            "bodyMarkdown": question_body,
            "score": 5,
            "viewCount": 10,
            "acceptedAnswerId": 2001,
            "tags": ["server_down", "all_clients"],
            "creationDate": "2026-01-01T00:00:00Z",
        },
        {
            "id": 2001,
            "postType": "answer",
            "parentId": 1537,
            "bodyMarkdown": "Use runbook and verify heartbeat.",
            "score": 9,
            "creationDate": "2026-01-01T01:00:00Z",
        },
        {
            "id": 2002,
            "postType": "answer",
            "parentId": 1537,
            "bodyMarkdown": "Lower-priority fallback answer.",
            "score": 20,
            "creationDate": "2026-01-01T01:10:00Z",
        },
    ]
    (tmp_path / "posts.json").write_text(json.dumps(posts), encoding="utf-8")
    (tmp_path / "comments.json").write_text("[]", encoding="utf-8")
    (tmp_path / "posts2votes.json").write_text("[]", encoding="utf-8")
    (tmp_path / "tags.json").write_text("[]", encoding="utf-8")
    (tmp_path / "images.json").write_text(
        json.dumps([{"id": 99, "imageGuid": "46791add-fd95-4bf4-9785-9b7d7ecce7c3"}]),
        encoding="utf-8",
    )


def test_stackoverflow_parser_preserves_markdown_images_and_accepted_answer(tmp_path: Path) -> None:
    _write_export(tmp_path)
    parser = StackOverflowParser()
    articles = parser.parse(tmp_path)
    assert len(articles) == 1
    article = articles[0]
    assert article.canonical_url.endswith("/questions/1537")
    assert "![server]" in article.question_markdown
    assert article.answer_post_id == 2001  # accepted answer beats higher score
    assert len(article.image_references) == 1
    assert article.image_references[0].manifest_found is True
    assert article.completeness_score > 0.80


def test_parser_json_uses_canonical_stackoverflow_path(tmp_path: Path) -> None:
    _write_export(tmp_path)
    docs = parse_stackoverflow_json_files(tmp_path)
    assert len(docs) == 1
    assert "Image References:" in docs[0].content
    assert "questions/1537" in docs[0].content


def test_redaction_hardening_masks_credentials() -> None:
    text = (
        "password=hello123 bearer abcdefghijklmnopqrstuvwxyz123456 "
        "postgres://user:secret@db.internal:5432/app ssh root@10.0.0.2:/srv/api "
        "AKIA1234567890ABCDEF"
    )
    redacted, n = _redact_pii(text)
    assert n > 0
    assert "hello123" not in redacted
    assert "postgres://" not in redacted
    assert "AKIA1234567890ABCDEF" not in redacted


def test_embed_text_contains_tags_url_and_images() -> None:
    text = _build_embed_text_raw(
        title="Server Down SOP",
        question_body="step 1",
        answer_body="step 2",
        knowledge_class=KnowledgeClass.SOP,
        canonical_url="https://stackoverflowteams.com/c/kwikid/questions/42",
        tags_raw=["server_down", "all_clients"],
        completeness_score=0.857,
        image_refs=["https://stackoverflowteams.com/c/kwikid/images/s/a.png"],
    )
    assert "CANONICAL_URL:" in text
    assert "TAGS:" in text
    assert "IMAGE_REFERENCES:" in text


def test_retrieval_acceptance_for_critical_sops() -> None:
    records = [
        ("Server Down", "all clients server down incident restart service"),
        ("Session Timeout", "session timeout re-login and refresh token"),
        ("OTP Failure", "otp failure sms retry delivery"),
        ("VKYC Failure", "vkyc verification failed manual retry"),
        ("Payment Pending", "payment pending callback reconcile"),
        ("KYC Rejected", "kyc rejected document mismatch"),
        ("Login Failure", "login failure reset password"),
        ("Video Issue", "video kyc camera permission denied"),
        ("Bank Switch", "bank switch reroute user journey"),
        ("Transaction Reversal", "transaction reversal settlement rollback"),
    ]

    def score(query: str, text: str) -> int:
        q = set(query.lower().split())
        t = set(text.lower().split())
        return len(q & t)

    for title, body in records:
        query = title.lower()
        ranked = sorted(records, key=lambda row: score(query, f"{row[0]} {row[1]}"), reverse=True)
        assert ranked[0][0] == title
