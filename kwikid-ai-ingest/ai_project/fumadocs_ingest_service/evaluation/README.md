# Evaluation Framework

## 🎯 Purpose
This directory contains the scaffolding for evaluating the performance of the KwikID RAG system.

## 📁 Structure
- `gold_dataset.json`: Ground truth queries and expected results.
- `evaluate_retrieval.py`: Benchmark for vector search precision/recall.
- `evaluate_response.py`: Benchmark for LLM response quality.
- `metrics.py`: Implementation of evaluation metrics (Hit Rate, MRR, etc.).

## 🚀 Usage
1. Populate `gold_dataset.json` with representative queries.
2. Run retrieval evaluation:
   ```bash
   python evaluation/evaluate_retrieval.py
   ```
3. Run response evaluation:
   ```bash
   python evaluation/evaluate_response.py
   ```

## 📊 Metrics Tracked
- **Hit Rate @ K**: Was the correct chunk in the top K results?
- **MRR (Mean Reciprocal Rank)**: How high was the correct chunk ranked?
- **Faithfulness**: Is the response grounded in the retrieved context?
- **Relevance**: Does the response answer the user's query?
