# Phase B1 — Full Architecture Analysis

**Date**: 2026-05-18  
**Author**: Lead RAG Systems Engineer  
**Status**: Baseline analysis for B1 completion pass  

---

## 1. System Overview

KwikID AI Ingest is a multi-tenant, production-grade RAG (Retrieval-Augmented Generation) system for Think360.ai's KwikID KYC support desk. The system automatically drafts responses to support tickets by retrieving relevant past resolutions and standard operating procedures.

```
Freshdesk tickets / SOPs
         │
         ▼
   [Ingestion Pipeline]
   rag_ticket_documents
   rag_ticket_chunks (embedded)
   rag_sop_library / rag_sop_chunks
         │
         ▼
   [TicketRetriever] — match_all_b1_sources RPC
         │
         ▼
   [Chat Generator] — gpt-4o-mini (Phase B2, gated)
         │
         ▼
   Freshdesk webhook draft reply (Phase B3, gated)
```

---

## 2. Architecture Map

### 2.1 Core Directories

| Path | Role |
|---|---|
| `rag_engine/ingestion/` | Pipeline orchestrators (ticket + SOP) |
| `rag_engine/chunking/` | Token-safe chunking (ticket + SOP) |
| `rag_engine/embedding/` | OpenAI provider, batch processor, validation |
| `rag_engine/retrieval/` | TicketRetriever, reranking hook |
| `rag_engine/generation/` | Chat generator, context assembler (Phase B2) |
| `rag_engine/document_builder/` | Document builders for tickets and SOPs |
| `rag_engine/schemas/` | Pydantic/dataclass schemas |
| `rag_engine/config/` | RagEngineSettings (env-based) |
| `sql/b1_migrations/` | PostgreSQL DDL + RPC functions |
| `scripts/` | Validation + ingestion CLI scripts |
| `app/` | FastAPI entrypoints (legacy + B1) |
| `data/processed/` | Preprocessed parquet dataset |
| `data/sop/` | SOP markdown source files |
| `data/reports/` | Auto-generated validation reports |

### 2.2 Database Tables (Phase B1)

| Table | Rows (post-ingestion) | Purpose |
|---|---|---|
| `rag_ticket_documents` | ~1,826 | Parent document records (dedup + metadata) |
| `rag_ticket_chunks` | ~5,817 | Embedded chunks (3 per ticket avg) |
| `rag_sop_library` | 0 → 3+ | SOP master records |
| `rag_sop_chunks` | 0 → 30+ | SOP sections (embedded, boosted) |
| `rag_ingestion_logs` | 1+ | Run-level metrics |
| `rag_ingestion_errors` | varies | Per-chunk error records |
| `rag_feedback_logs` | 0 | User feedback (Phase B2 feature) |

### 2.3 Multi-Tenant Isolation (3 Layers)

| Layer | Where | Mechanism |
|---|---|---|
| Application | `ticket_retriever.py:128` | `ValueError` if `client=""` |
| RPC | `match_all_b1_sources` SQL | `WHERE rtc.client = p_client` (NOT NULL) |
| Row-Level Security | Supabase policy | `ENABLE ROW LEVEL SECURITY` on both tables |

ESCALATION exclusion is applied at ingestion (pipeline._process_row) and at the RPC layer (`AND rtc.automation_label != 'ESCALATION'`). The application layer never needs to filter ESCALATION because it cannot enter the corpus.

---

## 3. Ingestion Workflow Reconstruction

### 3.1 Ticket Ingestion

```
unified_cleaned_dataset.parquet (3,635 AI-usable rows)
  │
  ├─ DatasetSchemaMapper.map()        # Normalize field names + automation_label
  ├─ TicketSourceRow.model_validate() # Pydantic schema validation
  ├─ automation_label == ESCALATION?  # → skip (never enters RAG corpus)
  │
  ├─ TicketDocumentBuilder.build()    # → RagTicketDocument
  │     has_rca, has_sop, rca_quality_score, content sections
  │
  ├─ TicketChunker.chunk()            # → 2–3 TicketChunk per document
  │     ISSUE_HEADER  (metadata-rich, ~100-200 tokens)
  │     QUERY_BODY    (customer complaint, token-split if >1200 tokens)
  │     RESOLUTION_RCA (resolution+RCA, token-split if >1200 tokens)
  │
  ├─ rag_ticket_documents.upsert()    # Parent table first (FK constraint)
  ├─ DeduplicationChecker             # classify_chunks: INSERT | UPDATE | SKIP
  └─ BatchEmbeddingProcessor          # embed + upsert (streaming, circuit breaker)
```

### 3.2 SOP Ingestion (newly implemented)

```
data/sop/*.md  (YAML frontmatter + markdown)
  │
  ├─ SopDocumentBuilder.build_from_markdown()   # → RagSopDocument
  │     title, query_type, clients, sections[]
  │
  ├─ _fetch_existing_sop()                       # dedup by content_hash
  │     unchanged → skip
  │     changed   → bump version, delete old chunks
  │
  ├─ rag_sop_library.upsert()                    # Library record first
  ├─ _build_chunk_records()                      # One chunk per H2/H3 section
  └─ embed_batch() + rag_sop_chunks.upsert()     # Embed + insert
```

### 3.3 Retrieval Workflow

```
RetrievalRequest(query_text, client, top_k=10, ...)
  │
  ├─ embed_single(query_text)                    # OpenAI API → 1536-dim vector
  ├─ match_all_b1_sources RPC                    # pgvector cosine + SOP boost
  │     WHERE client = p_client                  # tenant isolation
  │     AND automation_label != ESCALATION       # safety filter
  │     ORDER BY boosted_score DESC              # SOPs rank higher
  │
  ├─ [NEW] chunk_type filter                     # if request.chunk_types set
  ├─ NullReranker.rerank()                       # passthrough (hook for B1.5)
  └─ RetrievalResponse(chunks, latencies, ...)
```

---

## 4. Current Implementation Status

### Phase B1 — Core Retrieval

| Component | Status | Notes |
|---|---|---|
| Ticket ingestion pipeline | COMPLETE | 5,817 chunks in DB |
| Three-chunk strategy | COMPLETE | ISSUE_HEADER + QUERY_BODY + RESOLUTION_RCA |
| Token-safe chunking | COMPLETE | tiktoken cl100k_base, 7000 token hard cap |
| SHA256 deduplication | COMPLETE | INSERT / UPDATE / SKIP classification |
| UUID5 deterministic IDs | COMPLETE | Idempotent re-runs |
| Streaming embed+upsert | COMPLETE | Resume-on-crash via dedup |
| Circuit breaker | COMPLETE | 3 failures → 60s cooldown |
| Pre-embed validation | COMPLETE | 7 checks (empty, oversized, repetitive, etc.) |
| Multi-tenant isolation | COMPLETE | 3-layer (app + RPC + RLS) |
| ESCALATION exclusion | COMPLETE | At ingestion + RPC level |
| Chunk-type filter | **FIXED** | Was silently ignored; now applied post-RPC |
| SOP ingestion | **IMPLEMENTED** | SopIngestionPipeline + CLI + 3 sample SOPs |
| SOP retrieval (+0.15 boost) | COMPLETE | Built into match_all_b1_sources SQL |
| NullReranker | COMPLETE | Hook ready for LexicalReranker (B1.5) |

### Phase B1.5 — Retrieval Tuning (planned)

| Component | Status |
|---|---|
| LexicalReranker | Scaffolded in `reranking_hook.py` |
| CrossEncoderReranker | Scaffolded |
| Hybrid retrieval (BM25 + pgvector) | Architecture documented; feature-flagged OFF |
| Retrieval precision evaluation | Gold dataset needed |

### Phase B2 — Chat Generation (gated)

| Component | Status |
|---|---|
| ContextAssembler | Implemented, gated |
| ChatGenerator + LLM client | Implemented, gated |
| PromptBuilder | Implemented, gated |
| /rag/chat endpoint | Disabled until B1 live-validated |

### Phase B3 — Freshdesk Webhook (gated)

| Component | Status |
|---|---|
| HMAC-SHA256 webhook verification | Implemented |
| freshdesk_webhook.py | Implemented |
| Feature flag: FRESHDESK_WEBHOOK_ENABLED | Off by default |

---

## 5. Technical Debt Inventory

| ID | Debt | Impact | Priority |
|---|---|---|---|
| TD-1 | No API authentication on FastAPI endpoints | HIGH security risk | P0 |
| TD-2 | No rate limiting on /rag/chat | Abuse vector | P0 |
| TD-3 | Synchronous ingestion blocks webhook for large batches | Webhook timeout risk | P1 |
| TD-4 | RCA quality low (avg 12.2 words in RESOLUTION_RCA) | Retrieval quality | P1 |
| TD-5 | match_all_b1_sources doesn't return automation_label | Reduced observability | P2 |
| TD-6 | SOP version dedup — old chunks not filtered by sop_version in RPC | Stale content on update | P2 (mitigated by delete in pipeline) |
| TD-7 | No evaluation harness for retrieval precision (Hit Rate, MRR) | Cannot tune without data | P2 |
| TD-8 | n8n root-level scripts (dead code) | Repo hygiene | P3 |
| TD-9 | Dead root-level retrieval/ and query_router/ modules | Repo hygiene | P3 |

---

## 6. Known Issues and Risks

| Risk | Severity | Status |
|---|---|---|
| No auth on API endpoints | CRITICAL | Open (TD-1) |
| B2 chat generation tested offline only | HIGH | Gated — OK until B1 validated |
| rbl_bank has 0 chunks — T7 only tests isolation by absence | MEDIUM | Acceptable short-term |
| Single data source (unity_bank) — model may be overfit | MEDIUM | Expand to rbl_bank as next tenant |
| PAN/Aadhaar numbers in ticket content | MEDIUM | Dataset pipeline anonymization present |
| `documents_video_realted.csv` not reviewed for PII | MEDIUM | Pre-git review required |

---

## 7. Ingestion Statistics (Post-Full Run)

| Metric | Value |
|---|---|
| Source rows loaded | 3,635 |
| ESCALATION tickets excluded | ~1,809 (49.8%) |
| Documents upserted | ~1,826 |
| Chunks created | ~5,817 |
| Avg chunks per document | 3.18 |
| Unity Bank chunks | 2,726 (46.8%) |
| Embedding cost (text-embedding-3-small) | ~$0.029 |

---

## 8. Ordered Next Steps for B1 Completion

1. **[DONE]** Fix T6 chunk_type filter in `ticket_retriever.py`
2. **[DONE]** Implement SOP ingestion pipeline
3. Run SOP ingestion: `python scripts/ingest_sop.py --sop-dir data/sop/`
4. Re-run retrieval validation: `python scripts/validate_b1_retrieval.py --live --report`
5. Verify 50/50 checks pass (T6 should now be fixed)
6. Implement API authentication on FastAPI endpoints (P0)
7. Implement rate limiting on /rag/chat (P0)
8. Ingest rbl_bank data to validate cross-tenant isolation (T7 definitive test)
9. Enable B2 chat generation layer and run end-to-end tests
10. Enable B3 Freshdesk webhook in staging environment
