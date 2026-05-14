"""
rag_engine/cli/sample_retrieval.py

Quick retrieval example — run after ingestion is complete.
Shows how Phase B2 (chat) will call the retrieval layer.

Usage:
    python -m rag_engine.cli.sample_retrieval

Requires: populated rag_ticket_chunks, SUPABASE_URL + SUPABASE_KEY + EMBEDDING_API_KEY in .env
"""
from __future__ import annotations

import json
import os
import sys

# Add service root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()

    from app.config import get_settings
    from rag_engine.database.supabase_client import build_supabase_client_from_settings
    from rag_engine.embedding.openai_provider import OpenAIEmbeddingProvider
    from rag_engine.retrieval.ticket_retriever import RetrievalRequest, TicketRetriever

    app_settings = get_settings()
    supabase_client = build_supabase_client_from_settings()
    embedder = OpenAIEmbeddingProvider(
        api_key=app_settings.embedding_api_key,
        model=app_settings.embedding_model,
        dimensions=app_settings.embedding_dimensions,
    )

    retriever = TicketRetriever(
        supabase_client=supabase_client,
        embedding_provider=embedder,
    )

    # === Sample Queries ===
    sample_queries = [
        {
            "query": "Customer is unable to receive OTP on mobile number",
            "client": "unity_bank",
            "label": "OTP retrieval example",
        },
        {
            "query": "Video KYC session not connecting, camera not working",
            "client": "unity_bank",
            "label": "Video connectivity retrieval example",
        },
        {
            "query": "Aadhaar verification failed during VKYC process",
            "client": "rbl_bank",
            "label": "Aadhaar verification (RBL Bank)",
        },
    ]

    for sample in sample_queries:
        print(f"\n{'='*70}")
        print(f"Query: {sample['query']}")
        print(f"Client: {sample['client']} | {sample['label']}")
        print(f"{'='*70}")

        try:
            response = retriever.retrieve(
                RetrievalRequest(
                    query_text=sample["query"],
                    client=sample["client"],
                    top_k=5,
                    include_sop=True,
                )
            )
            print(f"Retrieved {len(response.chunks)} chunks in {response.total_latency_ms:.0f}ms")
            print(f"Has SOP context: {response.has_sop_context}")
            print(f"Has RCA context: {response.has_rca_context}")
            print()

            for i, chunk in enumerate(response.chunks, 1):
                source_icon = "📋" if chunk.source_table == "rag_sop_chunks" else "🎫"
                rca_icon = " [RCA]" if chunk.has_rca else ""
                sop_icon = " [SOP]" if chunk.has_sop else ""
                print(f"  [{i}] {source_icon} {chunk.chunk_type} | sim={chunk.similarity:.3f}{rca_icon}{sop_icon}")
                print(f"       {chunk.content[:120].strip()}...")
                print()

        except Exception as exc:
            print(f"  ERROR: {exc}")
            print(f"  Make sure you have run the ingestion pipeline first:")
            print(f"    python -m rag_engine.cli.ingest_cli --mode full")


if __name__ == "__main__":
    main()
