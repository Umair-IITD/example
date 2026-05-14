"""
rag_engine — Phase B1: RAG Architecture + Vector Ingestion System

This package implements the ticket-specific ingestion and retrieval stack
for the KwikID AI Support System. It is designed to sit alongside the
existing `app/` module and extend it with:

  - Structured ticket document construction (document_builder/)
  - Section-aware semantic chunking (chunking/)
  - Modular embedding pipeline with provider abstraction (embedding/)
  - Full + delta ingestion orchestration (ingestion/)
  - Tenant-isolated retrieval APIs (retrieval/)
  - Feedback learning loop (feedback/)
  - Structured observability (observability/)
  - Ingestion CLI (cli/)

Existing infrastructure reused:
  - app.config.get_settings()       → Supabase URL/key, embedding config
  - app.vector_store.VectorStore    → upsert + match_documents (existing table)
  - app.chunker.Chunk               → chunk model (extended, not replaced)
  - retrieval.*                     → hybrid search, RRF, reranker
"""

__version__ = "1.0.0"
__phase__ = "B1"
