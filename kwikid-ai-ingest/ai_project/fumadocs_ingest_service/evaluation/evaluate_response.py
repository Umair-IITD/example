import json
import os
import requests

# Configuration
INGEST_SERVICE_URL = os.getenv("INGEST_SERVICE_BASE_URL", "http://localhost:8000")
GOLD_DATASET_PATH = "evaluation/gold_dataset.json"

def run_response_benchmark():
    if not os.path.exists(GOLD_DATASET_PATH):
        print(f"Gold dataset not found at {GOLD_DATASET_PATH}")
        return

    with open(GOLD_DATASET_PATH, "r") as f:
        gold_data = json.load(f)

    for item in gold_data:
        query = item["query"]
        expected_answer = item.get("expected_answer")
        
        # Call the /chat endpoint
        try:
            response = requests.post(f"{INGEST_SERVICE_URL}/chat", json={"query_text": query})
            response.raise_for_status()
            actual_answer = response.json().get("answer")
            
            print(f"Query: {query}")
            print(f"Expected: {expected_answer}")
            print(f"Actual: {actual_answer}")
            print("-" * 20)
        except Exception as e:
            print(f"Error chatting for '{query}': {e}")

if __name__ == "__main__":
    run_response_benchmark()
