"""rag_engine/retrieval/bm25.py — Shared BM25 scoring utilities.

Sprint 2.60: Extracted from HybridTicketRetriever._bm25_rerank_inplace and
extended with bm25_score_list() for the Loki log relevance extractor.
"""
from __future__ import annotations
import math
import re

_WORD_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def bm25_rerank_inplace(query: str, rows: list[dict]) -> None:
    """In-place BM25 reranking. Mutates row['_bm25_score']. Same as the original @staticmethod."""
    q_tokens = tokenize(query)
    if not q_tokens or not rows:
        return
    n = len(rows)
    contents = [str(r.get("content", "")) for r in rows]
    doc_token_sets = [tokenize(c) for c in contents]
    doc_lengths = [len(dt) for dt in doc_token_sets]
    avg_dl = sum(doc_lengths) / n if n else 1.0
    df = {t: sum(1 for dt in doc_token_sets if t in dt) for t in q_tokens}
    k1, b = 1.5, 0.75
    for row, content, dl in zip(rows, contents, doc_lengths):
        content_lower = content.lower()
        score = 0.0
        for term in q_tokens:
            tf = content_lower.count(term)
            if tf == 0:
                continue
            idf = math.log((n - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5) + 1.0)
            tf_norm = tf * (k1 + 1.0) / (tf + k1 * (1.0 - b + b * dl / max(1.0, avg_dl)))
            score += idf * tf_norm
        row["_bm25_score"] = round(score, 6)


def bm25_score_list(query: str, contents: list[str]) -> list[float]:
    """Returns parallel list of BM25 scores. Used by log relevance extractor."""
    q_tokens = tokenize(query)
    n = len(contents)
    if not q_tokens or not n:
        return [0.0] * n
    doc_token_sets = [tokenize(c) for c in contents]
    doc_lengths = [len(dt) for dt in doc_token_sets]
    avg_dl = sum(doc_lengths) / n if n else 1.0
    df = {t: sum(1 for dt in doc_token_sets if t in dt) for t in q_tokens}
    k1, b = 1.5, 0.75
    scores: list[float] = []
    for content, dl in zip(contents, doc_lengths):
        content_lower = content.lower()
        score = 0.0
        for term in q_tokens:
            tf = content_lower.count(term)
            if tf == 0:
                continue
            idf = math.log((n - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5) + 1.0)
            tf_norm = tf * (k1 + 1.0) / (tf + k1 * (1.0 - b + b * dl / max(1.0, avg_dl)))
            score += idf * tf_norm
        scores.append(round(score, 6))
    return scores
