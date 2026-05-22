# System Architecture

## High-Level Overview

KwikID AI Ingest is a production-grade Retrieval-Augmented Generation (RAG) system for AI-assisted customer support. It transforms Think360's KwikID KYC platform's support from fully manual to AI-assisted.

```
                         ┌─────────────────────────────────────────────┐
                         │           Knowledge Sources                  │
                         │  Fumadocs (Git) │ JSON │ Excel │ SOPs       │
                         │  Freshdesk Tickets │ StackOverflow Teams    │
                         └──────────────────┬──────────────────────────┘
                                            │ Ingestion Pipeline
                                            ▼
                         ┌─────────────────────────────────────────────┐
                         │         Supabase Vector Store                │
                         │   pgvector (1536-dim, HNSW index)           │
                         │   FTS (tsvector for keyword search)         │
                         │   Tables: documents, rag_sop_chunks,        │
                         │           rag_knowledge_articles/chunks,     │
                         │           chat_messages, rag_review_queue    │
                         └──────────────────┬──────────────────────────┘
                                            │ Retrieval
                                            ▼
┌────────────────────┐   ┌─────────────────────────────────────────────┐
│   n8n / Client     │   │          FastAPI RAG Service (Port 8000)    │
│                    │──▶│                                             │
│  Freshdesk Webhook │   │  POST /rag/chat ──▶ HybridTicketRetriever  │
│  Telegram Bot      │   │                      ▼ (semantic + FTS + RRF)
│  Direct API        │   │                  ContextAssembler           │
│                    │   │                      ▼                      │
│                    │◀──│                  SopStructureParser          │
│                    │   │                      ▼                      │
│                    │   │                  PromptBuilder               │
│                    │   │                      ▼                      │
│                    │   │                  LLM (GPT-4o-mini)          │
│                    │   │                      ▼                      │
│                    │   │                  GovernanceChecker          │
│                    │   │                      ▼                      │
│                    │   │                  GenerationResult           │
└────────────────────┘   └─────────────────────────────────────────────┘
```

## Core Components

### 1. FastAPI Service (`app/main.py`)

The REST API layer. All routes except `/health`, `/ready`, and `/freshdesk/webhook` require `X-API-Key` authentication.

**Primary endpoints:**

| Endpoint | Purpose |
|----------|---------|
| `POST /rag/chat` | Primary RAG endpoint — full retrieval + generation pipeline |
| `POST /freshdesk/webhook` | Inbound Freshdesk webhook (HMAC-validated) |
| `POST /ingest` | Document ingestion trigger (fumadocs, JSON, Excel) |
| `POST /feedback` | Thumbs up/down feedback ingestion |
| `GET /health` | Health check (no auth) |
| `GET /ready` | Dependency readiness check — Supabase + embeddings (no auth, safe response) |
| `GET /metrics` | Prometheus metrics (if enabled) |
| `POST /query` | Legacy retrieval-only endpoint (no generation) |

### 2. Retrieval Pipeline

**Phase B1 — Hybrid Retrieval** (`rag_engine/retrieval/hybrid_ticket_retriever.py`):

```
Query Text
    │
    ├──▶ Semantic Search (pgvector)
    │    match_all_b1_sources() RPC
    │    • Embedding: text-embedding-3-small (1536-dim)
    │    • HNSW index for approximate nearest-neighbor
    │    • SOP chunks: +0.15 boosted_score bonus
    │    • Top-K: RETRIEVAL_SEMANTIC_TOP_K (default: 20)
    │
    ├──▶ Keyword Search (PostgreSQL FTS)
    │    match_all_b1_sources_fts() RPC
    │    • tsvector full-text search on content
    │    • Top-K: RETRIEVAL_KEYWORD_TOP_K (default: 20)
    │    • Requires B1_007_fts_setup.sql migration
    │
    └──▶ RRF Fusion (Reciprocal Rank Fusion)
         • Merges semantic + keyword ranked lists
         • RRF score = Σ 1/(k + rank_i), k=60
         • Adaptive k available (RETRIEVAL_ADAPTIVE_RRF_ENABLED)
         │
         └──▶ Reranking (BM25 or CrossEncoder)
              • BM25: term-frequency reranking (default)
              • CrossEncoder: ms-marco-MiniLM-L-6-v2 (optional)
              │
              └──▶ Final Top-K = RETRIEVAL_FINAL_TOP_K (default: 5)
```

### 3. SOP Parser (`rag_engine/sop/sop_parser.py`)

Structure-aware SOP document analysis using 7 compiled regex patterns. Runs at inference time on retrieved chunk content — no database persistence required.

**Detected flags:**
- `has_escalation_branches` — escalation paths, supervisor routing, account takeover signals
- `has_denial_branches` — conditional prohibitions ("do not", "never", "must not" patterns)
- `has_security_freeze` — phrases indicating ops-level interventions
- `has_post_resolution` — post-unlock checklists, logging requirements
- `has_mandatory_warnings` — absolute prohibitions ("NEVER unlock...", "Mandatory: ...")

**`SopDocumentFlags`** is a frozen dataclass — immutable, thread-safe, JSON-serializable.

### 4. Context Assembler (`rag_engine/generation/context_assembler.py`)

Assembles the top-K retrieved chunks into a structured LLM context string. For SOP chunks, it runs the parser and injects structural annotations into the chunk header:

```
##1 [SOP | sop_id=account_lockout | score=0.742 | AUTHORITATIVE | escalation:YES | mandatory:YES]
```

This guides the LLM to address each flagged branch explicitly.

### 5. Generation / Governance (`rag_engine/generation/chat_generator.py`)

```
GenerationRequest
    │
    ├──▶ Retrieval (HybridTicketRetriever or TicketRetriever)
    │
    ├──▶ Context Assembly (ContextAssembler)
    │    • Assembles top-K chunks into structured context
    │    • Annotates SOP structural flags
    │    • Truncates per CHAT_CONTEXT_CHUNK_MAX_CHARS
    │
    ├──▶ Workflow Classification
    │    • max(best_sop_boosted, raw_best_similarity)
    │    • exact_match: ≥ 0.55 boosted score
    │    • related_match: ≥ 0.35 boosted score
    │    • weak_match / no_match: below threshold
    │
    ├──▶ Prompt Construction (PromptBuilder)
    │    • RESPONSE MODE injected based on workflow_match_type
    │    • SOP branch mandate: per-flag instructions for LLM
    │    • Chat history included (last N turns)
    │
    ├──▶ LLM Call (GPT-4o-mini via OpenAI API)
    │    • Temperature: 0.2 (low — deterministic for support)
    │    • Max tokens: 800
    │    • Timeout: 60s, retries: 3
    │
    ├──▶ Branch Completeness Check
    │    • Post-LLM validation against SopDocumentFlags
    │    • Downgrades confidence high→medium if ≥2 branches absent
    │
    ├──▶ Automation Safety Gate
    │    • ALL must be true: not requires_human
    │                         AND confidence=="high"
    │                         AND exact_match
    │                         AND retrieval_confidence=="high"
    │
    └──▶ GenerationResult
         • answer, confidence, confidence_score
         • requires_human, citations, follow_up_question
         • insufficient_context, chunks, diagnostics
```

### 6. Multi-Tenant Isolation

Three defense layers:

1. **Application layer**: `ValueError` raised if `client` parameter is missing for tenant-isolated endpoints
2. **SQL RPC layer**: `WHERE index_version = ? AND client = ?` in Supabase RPC functions
3. **Supabase RLS**: Row Level Security policies on all tables (service-role key bypasses for ingestion)

### 7. Security Architecture

```
Request → Middleware (app/security.py)
           │
           ├── IP extraction (X-Forwarded-For aware)
           ├── API key validation (constant-time: hmac.compare_digest)
           │   • Checks all valid keys with bitwise-OR (no timing oracle)
           │   • Unprotected paths: /health, /ready, /freshdesk/webhook
           ├── Rate limiting (sliding window per IP)
           │   • /rag/chat: RAG_CHAT_RATE_LIMIT req/min
           │   • Redis (distributed) or in-process (fallback)
           └── Audit logging (structured JSON: method, path, client_ip, key_hint)
```

## Database Schema (Supabase)

### Core Tables

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `documents` | Legacy document store for /query | id, content, embedding, metadata, source_type, index_version |
| `rag_sop_chunks` | SOP procedure chunks | id, sop_id, content, embedding, boosted_score, index_version |
| `rag_knowledge_articles` | B3 knowledge base articles | id, title, content, quality_score, client |
| `rag_knowledge_chunks` | Embedded KB article chunks | id, article_id, content, embedding, quality_score |
| `chat_messages` | Conversation history | id, session_id, role, content, created_at |
| `rag_review_queue` | Human review queue for low-confidence responses | id, ticket_id, response, confidence, status |

### RPC Functions

| Function | Purpose |
|----------|---------|
| `match_all_b1_sources` | Hybrid semantic search with SOP boost |
| `match_all_b1_sources_fts` | Full-text search across all B1 sources |

## Deployment Architecture

```
Docker Compose (root docker-compose.yml)
│
├── kwikid-ingest service
│   ├── Image: multi-stage build (builder → runtime)
│   ├── User: appuser (UID 1001, non-root)
│   ├── Port: 8000
│   ├── Volumes:
│   │   ├── ./logs → /app/logs
│   │   ├── ./traces → /app/traces
│   │   └── ./data/reports → /app/data/reports
│   └── Health check: GET /health
│
└── redis (optional)
    ├── Image: redis:7-alpine
    └── Port: 6379 (internal only)
```
