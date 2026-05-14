import json
import os
import requests
from typing import List, Dict
from metrics import calculate_hit_rate, calculate_mrr

# Configuration
INGEST_SERVICE_URL = os.getenv("INGEST_SERVICE_BASE_URL", "http://localhost:8000")
GOLD_DATASET_PATH = "evaluation/gold_dataset.json"

def run_retrieval_benchmark():
    if not os.path.exists(GOLD_DATASET_PATH):
        print(f"Gold dataset not found at {GOLD_DATASET_PATH}")
        return

    with open(GOLD_DATASET_PATH, "r") as f:
        gold_data = json.load(f)

    results = []
    for item in gold_data:
        query = item["query"]
        expected_ids = item["expected_chunks"]
        
        # Call the /query endpoint
        try:
            response = requests.post(f"{INGEST_SERVICE_URL}/query", json={"query_text": query})
            response.raise_for_status()
            actual_matches = response.json().get("matches", [])
            actual_ids = [m["id"] for m in actual_matches]
            
            results.append({
                "query": query,
                "expected": expected_ids,
                "actual": actual_ids
            })
        except Exception as e:
            print(f"Error querying for '{query}': {e}")

    # Calculate metrics
    hit_rate = calculate_hit_rate(results, k=5)
    mrr = calculate_mrr(results)

    print(f"--- Retrieval Evaluation Results ---")
    print(f"Hit Rate @ 5: {hit_rate:.4f}")
    print(f"MRR: {mrr:.4f}")
    
    with open("evaluation/retrieval_metrics.json", "w") as f:
        json.dump({"hit_rate_at_5": hit_rate, "mrr": mrr}, f, indent=2)

if __name__ == "__main__":
    run_retrieval_benchmark()
