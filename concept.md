# KwikID AI Ingest — Complete System Concept Document

> **Repository**: `kwikid-think360/kwikid-ai-ingest`
> **Organization**: Think360.ai
> **Product**: KwikID — AI-driven KYC & Onboarding Platform
> **Analysis Date**: 2026-05-12 (Read-Only)

---

## 1. Executive Summary

This repository implements an **AI-powered knowledge ingestion and retrieval service** designed to automate and augment Think360's customer support operations for the KwikID product. The system ingests knowledge from multiple heterogeneous sources — internal documentation (Markdown), StackOverflow Teams Q&A (JSON), Excel-based support case logs, and live Freshdesk support tickets — into a unified **Supabase pgvector** vector database. It exposes a **RAG (Retrieval-Augmented Generation)** pipeline via a FastAPI REST service, enabling AI-assisted ticket resolution through both a dashboard chatbot and an automated n8n workflow that can draft and post responses directly into Freshdesk.

The system represents Think360's strategic initiative to evolve from a **fully manual support workflow** (human reads ticket → investigates → responds) to an **AI-assisted / semi-automated** pipeline where AI drafts initial responses grounded in organizational knowledge, and human agents review/approve them.

---

## 2. Business Context & Problem Statement

### Current Pain Points (Pre-AI)
```
Client raises ticket → Freshdesk → Human support manually reviews
→ Manually investigates logs/SOPs → Manually responds → Manually resolves
```

This process suffers from:
- **Slow response times** — every ticket requires manual investigation
- **Knowledge silos** — resolutions exist across docs, StackOverflow, Excel logs, and tribal knowledge
- **Inconsistent quality** — responses depend on individual agent expertise
- **No knowledge reuse** — similar tickets get resolved independently each time
- **Scale limitations** — support capacity is linearly tied to headcount

### Intended AI-Assisted Workflow
```
Ticket arrives (Freshdesk/Telegram)
  → n8n workflow triggers
    → Extracts ticket text + conversations
    → Sends to Ingest Service /query endpoint
    → Retrieves relevant knowledge chunks from vector DB
    → LLM generates grounded response
    → Posts draft reply to Freshdesk (public or private note)
    → Human reviews / approves / modifies
```

---

## 3. High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          KNOWLEDGE SOURCES                               │
│                                                                          │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │ Markdown     │  │ StackOverflow│  │  Excel   │  │   Freshdesk      │ │
│  │ (Fumadocs    │  │ Teams JSON   │  │  Support │  │   Tickets API    │ │
│  │  Bitbucket)  │  │              │  │  Cases   │  │   (live pull)    │ │
│  └──────┬───────┘  └──────┬───────┘  └─────┬────┘  └───────┬──────────┘ │
│         │                 │                │                │            │
│         └────────────────┬┘────────────────┘────────────────┘            │
│                          │                                               │
│                    ┌─────▼─────┐                                        │
│                    │  PARSERS  │  parser_md, parser_json,               │
│                    │           │  parser_excel, parser_freshdesk        │
│                    └─────┬─────┘                                        │
│                          │                                               │
│                    ┌─────▼─────┐                                        │
│                    │ VALIDATOR │  dedup, charset check,                  │
│                    │           │  metadata standardization               │
│                    └─────┬─────┘                                        │
│                          │                                               │
│                    ┌─────▼─────┐                                        │
│                    │  CHUNKER  │  heading_aware, paragraph,             │
│                    │           │  record_aware strategies                │
│                    └─────┬─────┘                                        │
│                          │                                               │
│                    ┌─────▼─────────┐                                    │
│                    │  EMBEDDINGS   │  OpenAI / Ollama                   │
│                    │  CLIENT       │  text-embedding-ada-002            │
│                    └─────┬─────────┘  or nomic-embed-text              │
│                          │                                               │
│                    ┌─────▼─────────┐                                    │
│                    │ VECTOR STORE  │  Supabase pgvector                 │
│                    │ (documents)   │  cosine similarity                 │
│                    └─────┬─────────┘                                    │
│                          │                                               │
└──────────────────────────┼──────────────────────────────────────────────┘
                           │
           ┌───────────────┼─────────────────────┐
           │               │                     │
     ┌─────▼──────┐  ┌────▼─────┐  ┌────────────▼───────────┐
     │  /query    │  │  /chat   │  │  /train/chat + commit  │
     │  API       │  │  API     │  │  (Teach-the-AI)        │
     │            │  │          │  │                        │
     │ Vector     │  │ RAG +    │  │ Knowledge card         │
     │ similarity │  │ GPT-4o   │  │ capture via LLM        │
     │ search +   │  │ mini     │  │ conversation           │
     │ rerank     │  │          │  │                        │
     └────────────┘  └────┬─────┘  └────────────────────────┘
                          │
                    ┌─────▼─────────┐
                    │   n8n         │
                    │   WORKFLOW    │
                    │               │
                    │ Freshdesk     │
                    │ webhook →     │
                    │ parse ticket →│
                    │ RAG query →   │
                    │ LLM draft →   │
                    │ Freshdesk     │
                    │ reply         │
                    └───────────────┘
```

---

## 4. System Components

### 4.1 FastAPI Service (`app/main.py`)
The central REST API layer. Exposes endpoints:

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | Liveness probe |
| `/ready` | GET | Readiness probe (checks Supabase + Embeddings) |
| `/ingest` | POST | Trigger knowledge ingestion pipeline |
| `/query` | POST | Vector similarity search with reranking |
| `/chat` | POST | RAG-powered conversational AI |
| `/chat/suggestions` | GET | Starter questions for empty chat state |
| `/train/chat` | POST | Knowledge capture conversation |
| `/train/commit` | POST | Commit a knowledge card to the vector DB |
| `/train/cards` | GET | List knowledge cards |
| `/train/cards/{id}` | GET/DELETE | Get or delete a knowledge card |
| `/freshdesk/filter-options` | GET | Status/priority filter options |

### 4.2 Ingestion Pipeline (`app/ingest.py`)
Orchestrates the full ingest workflow:
1. **Git Sync** — clones/pulls the Fumadocs documentation repo from Bitbucket
2. **Source Parsing** — dispatches to appropriate parsers based on file type
3. **Validation** — deduplication, charset checks, minimum length, required metadata
4. **Chunking** — splits documents into embedding-ready chunks
5. **Embedding** — generates vectors via OpenAI or Ollama
6. **Upsert** — writes to Supabase with deterministic IDs (idempotent)

### 4.3 Parsers
- **`parser_md.py`** — Markdown/MDX from the Fumadocs repo; strips frontmatter, extracts titles
- **`parser_json.py`** — StackOverflow Teams Q&A exports and generic JSON; handles posts/answers/comments/votes graph
- **`parser_excel.py`** — .xlsx/.xls/.csv support case logs; row-by-row with semantic header extraction
- **`parser_freshdesk.py`** — Live Freshdesk API integration; fetches tickets + conversations with full retry/rate-limiting logic

### 4.4 Chunker (`app/chunker.py`)
Splits `SourceDocument`s into `Chunk`s using configurable strategies:
- **`heading_aware`** — for Markdown (respects heading boundaries)
- **`record_aware`** — for JSON/Excel/Freshdesk (paragraph-based within records)
- **`paragraph`** — generic paragraph splitting
- Word count targets (200-500 words), overlap for continuity, orphan merging

### 4.5 Embeddings (`app/embeddings.py`)
Dual-provider embedding client:
- **OpenAI** — production path (`text-embedding-ada-002` / `text-embedding-3-small`)
- **Ollama** — local development path (`nomic-embed-text`)
- Handles batched embedding, context length overflow (progressive truncation), retry with backoff

### 4.6 Vector Store (`app/vector_store.py`)
Supabase pgvector abstraction:
- **Upsert** — deterministic UUID5 IDs for idempotent writes; content hash-based skip detection
- **Match** — server-side RPC (`match_documents`) for cosine similarity search
- **Local fallback** — when RPC fails (e.g., schema mismatch), falls back to client-side cosine computation
- **Bulk + per-row fallback** — tries bulk upsert first, falls back to row-by-row on timeout

### 4.7 Query Engine (`app/query.py`)
Search pipeline:
1. Embed query text
2. Fetch candidates (4x requested count for reranking headroom)
3. **Lexical reranking** — word overlap scoring between query and candidate content
4. **Per-source-type thresholds** — Freshdesk 0.30, MD/JSON/Excel 0.20
5. **Balanced selection** — round-robin across source types to ensure diversity
6. **Recency scoring** — optional boost for recent documents
7. **Insufficient context detection** — flags when best scores fall below minimums

### 4.8 Chat Engine (`app/chat.py`)
RAG conversational AI:
1. Retrieves relevant chunks via the query engine
2. Builds structured context block with metadata headers
3. Fetches conversation history from `chat_messages` table
4. Calls OpenAI Chat Completions API (GPT-4o-mini) with:
   - Detailed system prompt enforcing grounding rules, evidence hierarchy, citation requirements
   - JSON-mode response format
5. Normalizes output (confidence, citations, follow-up questions)
6. Persists conversation turns to Supabase

### 4.9 Train / Teach-the-AI (`app/train.py`)
Knowledge capture system:
- Human experts converse with an LLM to create "knowledge cards"
- Progressive draft refinement across turns
- On commit: chunks, embeds, and upserts as `source_type='manual'` documents
- Cards become part of the retrieval corpus alongside other sources
- Full CRUD API for managing committed cards

### 4.10 n8n Workflow Integration
External automation layer (not running within this service, but configured here):
- **Trigger**: Freshdesk webhook or Telegram message
- **Pipeline**: Normalize input → Extract ticket text + conversations → Parse issue (LLM) → Query Supabase RAG → Build context with reranking → Generate response (LLM) → Post to Freshdesk
- **Decision logic**: Category-based confidence thresholds, missing signal detection, clarification routing
- Configured via exported JSON workflow file + JavaScript node scripts

---

## 5. Data Flow — Ingestion Lifecycle

```
1. API call → POST /ingest (with source filters)
2. Git sync (if needed) — clone/pull Fumadocs docs from Bitbucket
3. Route to parsers based on file_path extension or explicit Freshdesk flags:
   ├── .md/.mdx → parser_md → SourceDocument[]
   ├── .json/.zip → parser_json → SourceDocument[]
   ├── .xlsx/.xls/.csv → parser_excel → SourceDocument[]
   └── freshdesk_* params → parser_freshdesk → SourceDocument[]
4. Combine all documents
5. Validate: min chars, charset, required metadata, dedup by content hash
6. Quarantine invalid docs (JSON report written to disk)
7. Chunk each valid document → Chunk[]
8. Batch embedding (64 chunks per batch)
9. Upsert to Supabase pgvector (deterministic IDs, content hash skip)
10. Return IngestResult (stats, errors, quarantine report path)
```

---

## 6. Data Flow — Query/Chat Lifecycle

```
1. API call → POST /chat { query_text, session_id, filters... }
2. Embed query text → vector
3. Call match_documents RPC → candidates (4x match_count)
4. Rerank candidates: lexical overlap + recency
5. Apply per-source-type similarity thresholds
6. Balanced top-N selection across source types
7. Build context block (chunk metadata + truncated content)
8. Fetch chat history from Supabase chat_messages
9. Construct messages: system prompt + history + user prompt (with context + diagnostics)
10. Call OpenAI Chat Completions API (JSON mode)
11. Parse response → { answer, confidence, citations, follow_up_question }
12. Persist user + assistant messages to chat_messages
13. Return ChatResult
```

---

## 7. External Integrations

| Integration | Type | Purpose |
|---|---|---|
| **Supabase** (pgvector) | Database | Vector store (`documents`), chat history (`chat_messages`), support state (`support_conversation_state`) |
| **OpenAI API** | LLM | Chat completions (GPT-4o-mini), embeddings |
| **Ollama** | Local LLM | Development-mode embeddings (nomic-embed-text) |
| **Freshdesk API** | Ticketing | Fetch tickets/conversations for ingestion; post AI responses |
| **Bitbucket** | Git | Source for Fumadocs internal documentation |
| **n8n** | Workflow automation | Orchestrates end-to-end ticket → AI response → Freshdesk reply |
| **Telegram** | Messaging | Alternate input channel (via n8n workflow) |
| **Asana** | Project management | Referenced in n8n environment (task creation for escalations) |
| **StackOverflow Teams** | Knowledge base | Q&A content ingested via JSON exports |

---

## 8. Database Schema

### `public.documents` (primary vector table)
- `id` UUID — deterministic UUID5 based on repo:source_type:source_id:chunk_index
- `content` TEXT — chunk text
- `embedding` vector(1536) — embedding vector
- `metadata` JSONB — rich structured metadata (source_type, tags, tenant, access_scope, etc.)

### `public.chat_messages`
- Session-based conversation history for the dashboard chatbot
- Indexed by (session_id, created_at)
- Accessed via service_role (bypasses RLS)

### `public.support_conversation_state`
- Multi-turn support automation state for n8n workflows
- Tracks channel (telegram/freshdesk), collected fields, pending questions
- Distinct from vector documents and chat history

### `public.knowledge_cards` (LEGACY — no longer written)
- Historical card-level metadata table; superseded by metadata on `documents` rows

### `public.kb_chunks` (LEGACY — vestigial)
- Earlier schema that was replaced by `documents`; `match_documents` RPC was updated to target `documents`

---

## 9. Current Implementation Status

| Component | Status | Notes |
|---|---|---|
| Ingestion pipeline (Markdown) | ✅ Functional | Git sync + parsing + chunking + embedding |
| Ingestion pipeline (JSON) | ✅ Functional | StackOverflow + generic JSON support |
| Ingestion pipeline (Excel) | ✅ Functional | .xlsx/.xls/.csv with multi-sheet support |
| Ingestion pipeline (Freshdesk) | ✅ Functional | Full API integration with retry/rate limiting |
| Vector search (/query) | ✅ Functional | Reranking, balanced selection, per-source thresholds |
| RAG Chat (/chat) | ✅ Functional | GPT-4o-mini with grounding rules, history, citations |
| Teach-the-AI (/train) | ✅ Functional | Knowledge card capture, commit, list, delete |
| Chat suggestions | ✅ Functional | Pulls from train cards + document titles |
| n8n workflow | ⚠️ Semi-functional | Workflow JSON exists; multiple patch scripts indicate ongoing fixes |
| Freshdesk auto-reply | ⚠️ Semi-functional | Response formatting has known issues (multiple fix scripts) |
| Telegram integration | 🔲 Partially configured | Referenced in n8n environment but workflow details unclear |
| Asana integration | 🔲 Configured only | Environment variables present, implementation unclear |
| PII redaction | 🔲 TODO | Explicitly noted in train.py commit function |
| Authentication/authorization | 🔲 Not implemented | No API key validation on endpoints |

---

## 10. Design Philosophy & Assumptions

1. **Source-of-truth multiplexing**: The system treats multiple knowledge sources (docs, Q&A, tickets, manual cards) as a unified retrieval corpus, differentiated by `source_type` in metadata.

2. **Idempotent ingestion**: Deterministic UUID5 IDs and content-hash-based skip detection ensure re-running ingestion is safe and efficient.

3. **Graceful degradation**: Extensive fallback patterns — git sync failure doesn't block ingestion; RPC failure falls back to client-side search; bulk upsert failure falls back to per-row.

4. **Grounded AI**: The system prompt is explicitly designed to prevent hallucination — the LLM must cite retrieved chunks and cannot invent facts.

5. **Multi-tenant readiness**: `tenant` and `access_scope` metadata fields are threaded through the entire pipeline, though multi-tenancy doesn't appear to be actively used yet.

6. **Schema evolution tolerance**: The codebase handles multiple historical schema versions (kb_chunks → documents) with runtime detection and fallback.

---

## 11. Inferred Roadmap & Future Direction

Based on code artifacts, TODOs, and architectural patterns:

1. **PII redaction** — TODO noted in `train.py`; needed before exposing committed knowledge more broadly
2. **Multi-tenant isolation** — Infrastructure is in place but not enforced
3. **Richer reranking** — Current lexical overlap reranking is basic; the n8n workflow implements a more sophisticated version suggesting planned consolidation
4. **Full automation loop** — n8n workflow shows the path toward fully automated ticket responses with human review gates
5. **Telegram as input channel** — Support conversation state table supports telegram; n8n env references it
6. **OpenAPI-driven troubleshooting** — n8n env includes `KWIK_OPENAPI_URL` and `KWIK_FIX_API_URL`, suggesting planned integration with KwikID's own API for automated remediation
7. **Confidence-gated automation** — The n8n workflow has sophisticated confidence thresholds and clarification routing, indicating a vision for different automation levels based on AI confidence

---

## 12. Key Observations

1. **Dual reranking implementations**: The FastAPI service has a simple word-overlap reranker in `query.py`, while the n8n workflow in `Build_Supabase_Context.js` has a more sophisticated implementation with source-type weights, lexical phrase matching, and configurable thresholds. These are not synchronized.

2. **Extensive patch scripts**: Multiple Python scripts (`fix_minimal_reply_issue.py`, `fix_public_body.py`, `update_n8n_workflow.py`, etc.) modify the n8n workflow JSON programmatically, indicating rapid iteration on response quality with manual patching rather than version-controlled workflow development.

3. **Hardcoded developer paths**: Several patch scripts reference `C:\Users\Dyaneshwar.Shekade\Desktop\...`, indicating a single-developer workflow without team standardization.

4. **Security concern**: `supabasesuccess.py` contains a hardcoded Supabase service role key in version control.

5. **No authentication layer**: All FastAPI endpoints are publicly accessible — no API key, JWT, or other authentication mechanism.

---

## 13. Limitations

1. **No real-time ingestion**: Ingestion is triggered manually or via API call; there is no webhook-driven auto-ingestion when Freshdesk tickets are created/updated.
2. **Synchronous processing**: The entire ingestion pipeline runs synchronously in the request handler; large ingests block the API.
3. **No observability**: No metrics, tracing, or structured logging beyond Python's `logging` module.
4. **Single-node deployment**: Docker Compose with a single container; no horizontal scaling provisions.
5. **No caching**: Every query re-embeds the query text and hits Supabase; no query result caching.
6. **Reranking quality**: The word-overlap reranker is simplistic compared to cross-encoder or learned rerankers.
7. **No feedback loop**: No mechanism to capture whether AI-generated responses were accurate or helpful.
