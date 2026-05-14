from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.query import run_query


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate retrieval quality for RAG workflow.")
    parser.add_argument("--dataset", required=True, help="Path to evaluation JSON file")
    parser.add_argument("--k", type=int, default=5, help="Top-k retrieval size")
    parser.add_argument("--min-precision", type=float, default=0.2, help="Fail if precision@k is below this")
    parser.add_argument("--min-recall", type=float, default=0.4, help="Fail if recall@k is below this")
    return parser.parse_args()


def _precision_at_k(hit_count: int, k: int) -> float:
    return hit_count / max(1, k)


def _recall_at_k(hit_count: int, expected_count: int) -> float:
    return hit_count / max(1, expected_count)


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Dataset must be a JSON array")
    return [item for item in payload if isinstance(item, dict)]


def main() -> int:
    args = _parse_args()
    settings = get_settings()
    rows = _load_dataset(Path(args.dataset))
    if not rows:
        print("No evaluation rows found.")
        return 1

    precision_scores: list[float] = []
    recall_scores: list[float] = []
    abstain_expected = 0
    abstain_correct = 0

    for row in rows:
        query_text = str(row.get("query_text") or "").strip()
        if not query_text:
            continue
        expected_sources = {str(v) for v in (row.get("expected_source_ids") or []) if str(v).strip()}
        should_abstain = bool(row.get("should_abstain", False))
        result = run_query(settings, query_text, match_count=args.k, match_threshold=0.0)
        returned_sources = {
            str((match.get("metadata") or {}).get("source_id") or (match.get("metadata") or {}).get("source_path_or_id") or "")
            for match in result.matches
        }
        hit_count = len(returned_sources & expected_sources) if expected_sources else 0
        precision_scores.append(_precision_at_k(hit_count, args.k))
        recall_scores.append(_recall_at_k(hit_count, len(expected_sources)))
        if should_abstain:
            abstain_expected += 1
            if result.insufficient_context:
                abstain_correct += 1

    avg_precision = sum(precision_scores) / max(1, len(precision_scores))
    avg_recall = sum(recall_scores) / max(1, len(recall_scores))
    abstain_accuracy = abstain_correct / max(1, abstain_expected)

    summary = {
        "queries_evaluated": len(precision_scores),
        "precision_at_k": round(avg_precision, 4),
        "recall_at_k": round(avg_recall, 4),
        "abstain_accuracy": round(abstain_accuracy, 4),
        "thresholds": {"min_precision": args.min_precision, "min_recall": args.min_recall},
    }
    print(json.dumps(summary, indent=2))

    if avg_precision < args.min_precision or avg_recall < args.min_recall:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
