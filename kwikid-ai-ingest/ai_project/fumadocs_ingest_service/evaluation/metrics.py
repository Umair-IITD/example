from typing import List, Dict

def calculate_hit_rate(results: List[Dict], k: int = 5) -> float:
    hits = 0
    for res in results:
        actual_top_k = res["actual"][:k]
        expected = set(res["expected"])
        if any(idx in expected for idx in actual_top_k):
            hits += 1
    return hits / len(results) if results else 0.0

def calculate_mrr(results: List[Dict]) -> float:
    rr_sum = 0.0
    for res in results:
        actual = res["actual"]
        expected = set(res["expected"])
        for i, idx in enumerate(actual):
            if idx in expected:
                rr_sum += 1.0 / (i + 1)
                break
    return rr_sum / len(results) if results else 0.0
