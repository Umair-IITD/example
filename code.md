# KwikID AI Ingest — Code Reference Document

> **READ-ONLY analysis** — No code was modified.

---

## 1. Repository Structure

```
kwikid-ai-ingest/
├── .gitignore
├── supabasesuccess.py                    # Standalone Supabase vector DB validation script
└── ai_project/
    └── fumadocs_ingest_service/          # Main project root
        ├── .github/workflows/ci.yml      # GitHub Actions CI
        ├── Dockerfile                    # Python 3.11-slim container
        ├── docker-compose.yml            # Single-service compose
        ├── requirements.txt              # Python dependencies
        ├── .env.example                  # Environment template
        ├── README.md                     # Project documentation
        ├── recreate-container.ps1        # PowerShell container restart
        ├── curl_examples.md              # API usage examples
        │
        ├── app/                          # ★ Core application package
        │   ├── __init__.py
        │   ├── main.py                   # FastAPI application + endpoints
        │   ├── config.py                 # Settings dataclass + env loading
        │   ├── ingest.py                 # Ingestion pipeline orchestrator
        │   ├── query.py                  # Vector search + reranking
        │   ├── chat.py                   # RAG chat engine + LLM client
        │   ├── train.py                  # Teach-the-AI knowledge capture
        │   ├── suggestions.py            # Chat starter suggestions
        │   ├── chunker.py                # Document chunking strategies
        │   ├── embeddings.py             # Embedding client (OpenAI/Ollama)
        │   ├── vector_store.py           # Supabase pgvector operations
        │   ├── git_sync.py               # Git clone/pull helper
        │   ├── parser_md.py              # Markdown parser
        │   ├── parser_json.py            # JSON/StackOverflow parser
        │   ├── parser_excel.py           # Excel/CSV parser
        │   ├── parser_freshdesk.py       # Freshdesk API parser
        │   ├── embedder.py               # Re-export alias for embeddings
        │   └── uploader.py               # Re-export alias for vector_store
        │
        ├── sql/                          # Database schema definitions
        │   ├── current_schema.sql         # Full DB schema dump (162KB)
        │   ├── match_documents_documents.sql  # Vector search RPC
        │   ├── chat_messages.sql          # Chat history table
        │   ├── kb_chunks.sql              # Legacy vector table
        │   ├── knowledge_cards.sql        # Legacy cards table
        │   └── support_conversation_state.sql  # n8n state table
        │
        ├── scripts/                      # Utility/analysis scripts
        │   ├── evaluate_rag.py
        │   ├── export_supabase_schema.py
        │   ├── export_video_ticket_conversations.py
        │   ├── freshdesk_export_ticket_fields.py
        │   ├── freshdesk_ticket_fields_probe.py
        │   ├── generate_video_issues_report.py
        │   ├── ingest_freshdesk_ticket_ids.py
        │   ├── list_freshdesk_contacts_by_email_domain.py
        │   └── rag_eval_dataset.example.json
        │
        ├── tests/
        │   └── test_ingest_context_improvements.py
        │
        ├── n8n/
        │   └── environment.example        # n8n environment template
        │
        ├── postman/
        │   └── fumadocs_ingest_service_e2e.postman_collection.json
        │
        ├── tmp_workflow_node_scripts/     # n8n JavaScript node code
        │   ├── Build_Supabase_Context.js
        │   ├── Build_TicketplusConversation_Text.js
        │   ├── Prepare_First_Response_Prompt.js
        │   └── Collect_Final_Response.js
        │
        └── [patch scripts]               # Workflow JSON modifiers
            ├── fix_minimal_reply_issue.py
            ├── fix_public_body.py
            ├── patch_formatter_labels.py
            ├── reapply_formatter_safeguards.py
            ├── refine_public_reply_professional.py
            ├── remove_result_tags_public_reply.py
            ├── update_n8n_workflow.py
            ├── update_prompt_grounding.py
            └── validate_workflow_changes.py
```

---

## 2. Core Application Modules

### 2.1 `app/main.py` — FastAPI Application

**Purpose**: Entry point for the REST API. Defines request/response models, endpoint handlers, and CORS configuration.

**Key Classes**:
- `IngestRequest` — Pydantic model with ~14 fields covering all ingestion sources and Freshdesk filters
- `QueryRequest` — search parameters including per-source-type thresholds, tenant/scope, date ranges
- `ChatRequest` — query + session + history settings for conversational RAG
- `TrainChatRequest` — knowledge capture with draft card state
- `CommitCardRequest` — finalize and persist a knowledge card
- `DraftCardModel` — card draft structure (title, summary, content, tags, tenant, scope, suggested_questions)

**Key Design Decisions**:
- CORS is restricted to localhost:3000 — implies a React/Next.js frontend on the same machine
- Each endpoint constructs fresh `Settings` via `get_settings()` (no singleton caching)
- Query/Chat endpoints compute a SHA-256 hash of the query text for structured logging

### 2.2 `app/config.py` — Configuration

**Purpose**: Loads all configuration from environment variables with validation.

**Architecture**: Single frozen `Settings` dataclass with 60+ fields, categorized:

```python
# Embedding settings
embedding_provider: str       # "ollama" or "openai"
embedding_model: str           # "nomic-embed-text" or OpenAI model
embedding_dimensions: int      # 1536
embedding_batch_size: int      # 64

# Chunking settings
min_chunk_words: int           # 200
target_chunk_words: int        # 350
max_chunk_words: int           # 500
chunk_overlap_words: int       # 50
chunk_strategy: str            # "auto" | "paragraph" | "heading_aware" | "record_aware"

# Freshdesk settings
freshdesk_enabled: bool
freshdesk_domain: str
freshdesk_min_wait_on_429_s: float  # 25.0 — aggressive rate limit respect

# Chat/LLM settings
chat_model: str                # "gpt-4o-mini"
chat_temperature: float        # 0.2 — low for factual responses
chat_max_output_tokens: int    # 800
```

**Important**: `get_settings()` is called per-request (not cached). This means every request re-reads `.env` and re-validates.

### 2.3 `app/ingest.py` — Ingestion Pipeline

**Purpose**: Orchestrates the complete knowledge ingestion workflow.

**Key Function**: `run_ingest(settings, options)` (752 lines)

**Critical Flow**:
```python
# 1. Determine git sync strategy
if is_freshdesk_only:
    commit_sha = "freshdesk-only"
elif _local_ingest_path_skips_git_sync(file_path):
    commit_sha = "local-file"
else:
    repo_path, commit_sha = sync_repo(...)  # Git clone/pull

# 2. Parse sources
markdown_docs, json_docs, excel_docs, excel_summary, parse_errors = _prepare_source_documents(...)
freshdesk_docs = _parse_freshdesk_documents(settings, request, errors)

# 3. Combine and validate
documents = [*markdown_docs, *json_docs, *freshdesk_docs, *excel_docs]
normalized_docs, quarantined_docs, duplicate_docs = _validate_documents(...)

# 4. Chunk
for doc in normalized_docs:
    all_chunks.extend(chunk_document(doc, ...))

# 5. Embed + Upsert in batches
for batch in _batched(all_chunks, settings.embedding_batch_size):
    vectors = embeddings.embed_texts([item.content for item in batch])
    result = store.upsert_chunks(chunk_vectors=zip(batch, vectors))
```

**Validation Pipeline** (`_validate_documents`):
- Minimum document character length (configurable, default 60)
- Charset sanity check (≥90% printable characters)
- Required metadata check per source type
- Content-hash-based deduplication (both within-run and cross-source)
- Quarantine report written as JSON for debugging

**Retry Logic**: 5 attempts for transient upsert errors with exponential backoff (12s × 2^attempt, capped at 90s). Transient detection via string matching: "timeout", "rate limit", "connection reset", "cloudflare", "522".

### 2.4 `app/query.py` — Search Engine

**Purpose**: Vector similarity search with reranking and balanced source selection.

**Key Function**: `run_query(settings, query_text, ...)` → `QueryResult`

**Reranking** (`_rerank_score`):
```python
def _rerank_score(query_text: str, candidate_text: str) -> float:
    q = _tokenize(query_text)  # lowercase word tokens
    c = _tokenize(candidate_text)
    overlap = len(q & c)
    return overlap / max(1, len(q))
```
This is a simple bag-of-words Jaccard-like overlap. Not a learned reranker.

**Sort Key** (compound):
```python
def _sort_key(item):
    return (rerank_score, vector_score, recency_score)
```

**Balanced Selection** (`_balanced_top_matches`): Round-robin across source types ordered by best score, ensuring diverse results.

**Important**: `query.py` imports `EmbeddingClient` from `app.embedder` and `VectorStore` from `app.uploader` — these are re-export aliases, creating a minor indirection layer.

### 2.5 `app/chat.py` — RAG Chat Engine

**Purpose**: Conversational RAG with GPT-4o-mini, grounding enforcement, and persistent history.

**System Prompt** (critical — ~60 lines):
```
You are KwikID Support AI, assisting human support agents.

## Evidence hierarchy (strict)
1) AUTHORITATIVE: The "Retrieved context chunks" section in the CURRENT user message.
2) SECONDARY: The "Diagnostics" JSON in the CURRENT user message.
3) NOT AUTHORITATIVE: Prior conversation turns.

## Grounding rules
- Answer ONLY from the retrieved chunks for factual content.
- Do not invent: product behavior, SLAs, policies, IDs, dates, URLs...
- If chunks conflict, summarize both positions...

## Output format (mandatory)
Return STRICT JSON only: { answer, confidence, citations, follow_up_question }
```

**ChatGPTClient**: Custom HTTP client using `httpx` (not the openai Python SDK). Implements:
- Retry with exponential backoff + Retry-After header support
- Retryable status detection (408, 409, 429, 500, 502, 503, 504)
- JSON response format enforcement

**ChatHistoryStore**: Persists to `public.chat_messages` via Supabase client. Fetches `limit * 2` rows and slices the last `limit * 2` for window control.

### 2.6 `app/train.py` — Knowledge Capture

**Purpose**: "Teach-the-AI" feature — subject matter experts create knowledge cards via conversational LLM.

**Train System Prompt**:
```
You are a knowledge-capture assistant for the KwikID product.
Your job is to help a subject-matter expert teach you reusable knowledge
(policies, SOPs, product rules, ticket resolutions, pricing logic, etc.)
...
Progressively refine a single "draft" across turns.
Set confidence = "high" only when title, content, tags, tenant,
and access_scope are all populated and content >= 40 words.
```

**Commit Flow** (`commit_knowledge_card`):
1. Validates title and minimum content (40 words)
2. Creates `SourceDocument` with `source_type="manual"`
3. Chunks using paragraph strategy
4. Enriches chunk 0 (head) with card summary, suggested questions, full content
5. Embeds all chunks
6. Upserts to `documents` table with `repo="manual:chat_train"`
7. On failure: cleans up partial writes by deleting by `card_id`

### 2.7 `app/parser_freshdesk.py` — Freshdesk Integration

**Purpose**: Full Freshdesk REST API integration for ticket ingestion. The largest module (1082 lines).

**Key Capabilities**:
- Paginated ticket listing with `updated_since` filtering
- Individual ticket detail fetching
- Conversation thread fetching (public replies + private notes)
- HTML → plain text conversion (script/style stripping, block break preservation)
- Image URL extraction from HTML (img src, srcset, markdown syntax, href)
- Structured description parsing (string, HTML, JSON dict)
- Rate limiting: configurable spacing between requests (default 3.5s)
- 429 handling: minimum 25s wait on rate limit responses
- Rich filtering: type, status, priority, requester_id, responder_id, group_id, date range

**Ticket Document Structure**:
```
Subject: <subject>
Ticket Link: <url>
Description: <html-to-text body>
Structured description (JSON): <if present>
Image and media URLs: <extracted URLs>
Conversation and notes: <all conversations with headers>
Ticket fields (JSON): <full ticket payload>
Ticket conversations and notes (JSON): <full conversations payload>
Tags: <tags>
Status: <human-readable>
Priority: <human-readable>
Type: <type>
```

### 2.8 `app/vector_store.py` — Supabase pgvector

**Key Methods**:

```python
# Deterministic ID computation
deterministic_id = uuid5(NAMESPACE_URL, f"{repo}:{source_type}:{source_id}:{chunk_index}")

# Upsert with fallback
def _upsert_payloads_with_fallback(self, payloads):
    try:
        self._client.table(TABLE).upsert(payloads).execute()  # Bulk
    except:
        for row in payloads:  # Per-row fallback with 3 retries
            for attempt in range(1, 4):
                try: upsert([row]); break
                except: sleep(min(8.0, 1.5 * attempt))

# Match with RPC fallback
def match_documents(self, query_embedding, ...):
    try:
        self._client.rpc("match_documents", {...}).execute()
    except:
        if "kb_chunks" in error:
            return self._match_documents_locally(...)  # Client-side cosine
```

**Local Fallback**: When the `match_documents` RPC references the old `kb_chunks` table, the code pages through all rows (up to `local_fallback_max_rows=5000`) and computes cosine similarity in Python. This is a critical performance concern.

### 2.9 `app/chunker.py` — Document Chunking

**Data Models**:
```python
@dataclass
class SourceDocument:
    source_type: str    # "md", "json", "excel", "freshdesk", "manual"
    source_id: str
    content: str
    title: str
    heading: str | None
    tags: list[str]
    creation_date: str | None
    metadata: dict[str, object]

@dataclass
class Chunk:
    chunk_id: str       # "{source_type}:{source_id}:{chunk_index}:{hash[:12]}"
    # ... inherits source fields + chunk_index, word_count, content_hash
```

**Strategy Selection** (auto mode):
- `md` → `heading_aware` (splits on `#` headings)
- `json`, `excel`, `freshdesk` → `record_aware` (paragraph-based)
- default → `paragraph`

**Orphan Merging**: If the last chunk has fewer than `min_orphan_words` (50), it's merged back into the previous chunk.

---

## 3. n8n Workflow Scripts

### 3.1 `Build_Supabase_Context.js`

**Role**: The most sophisticated piece of automation logic. Takes parsed issue output and RAG query results, and:

1. **Normalizes matches** — handles multiple possible payload shapes
2. **Lexical reranking** — token overlap + phrase matching against focus phrases extracted from the parsed issue
3. **Source-type weighting** — configurable via env vars (`RAG_WEIGHT_MD`, `RAG_WEIGHT_FRESHDESK`, etc.)
4. **Composite scoring** — `rerankScore = effectiveScore * 0.65 + overlapScore * 0.25 + phraseScore * 0.10`
5. **Confidence gating** — category-specific thresholds (auth: 0.32, video_not_available: 0.30, network: 0.28, other: 0.24)
6. **Missing signal detection** — checks for sessionId, phoneNumber, appVersion based on issue category
7. **Clarification routing** — determines if AI should ask for more info vs. provide a resolution

### 3.2 `Prepare_First_Response_Prompt.js`

**Role**: Constructs the LLM prompt for generating the first Freshdesk response.

Key rules enforced:
- "Do NOT suggest server-side, backend, database, deployment, infra, restart, config, or terminal actions"
- "End every customer response with exactly: 'Thanks & Regards' then 'kwikid AI support'"
- Output format: acknowledgement → diagnosis → steps → needed details → closing signature

### 3.3 `Build_TicketplusConversation_Text.js`

**Role**: Combines ticket subject + body + conversation thread into a single `analysisText` string for LLM processing.

### 3.4 `Collect_Final_Response.js`

**Role**: Extracts the LLM response from the "First Response (OpenAPI + RAG)" node output.

---

## 4. SQL Schema Definitions

### `match_documents_documents.sql` — Vector Search RPC
```sql
create or replace function public.match_documents(
    query_embedding public.vector(1536),
    match_count integer default 5,
    match_threshold double precision default 0.0
)
returns table (id uuid, content text, metadata jsonb, similarity double precision)
language sql stable as $$
    select d.id, d.content, d.metadata,
           1 - (d.embedding <=> query_embedding) as similarity
    from public.documents as d
    where 1 - (d.embedding <=> query_embedding) >= match_threshold
    order by d.embedding <=> query_embedding
    limit match_count;
$$;
```

### `support_conversation_state.sql` — Automation State
```sql
create table if not exists public.support_conversation_state (
    id uuid primary key default gen_random_uuid(),
    channel text not null check (channel in ('telegram', 'freshdesk')),
    external_id text not null,
    issue_key text,
    issue_category text,
    collected_fields jsonb not null default '{}'::jsonb,
    pending_questions jsonb not null default '[]'::jsonb,
    updated_at timestamptz not null default now(),
    unique (channel, external_id)
);
```

---

## 5. Configuration & Deployment

### Dockerfile
```dockerfile
FROM python:3.11-slim
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```
Note: `--reload` in production CMD is a development concern.

### Dependencies (`requirements.txt`)
```
fastapi==0.116.1, uvicorn==0.35.0, python-dotenv==1.1.1,
httpx==0.28.1, pydantic==2.11.9, supabase==2.18.1,
openpyxl==3.1.5, pandas, xlrd, pytest==8.4.2
```

### CI/CD (`.github/workflows/ci.yml`)
Minimal: Python 3.11 → install deps → `pytest -q`. No linting, type checking, or deployment.

---

## 6. Key Entry Points for Future Modification

| Area | Files | Notes |
|---|---|---|
| Add new source type | `parser_*.py`, `ingest.py`, `chunker.py`, `config.py` | Follow parser pattern, register in `_prepare_source_documents` |
| Improve reranking | `query.py` (_rerank_score), `Build_Supabase_Context.js` | Two implementations to keep in sync |
| Modify chat behavior | `chat.py` (SYSTEM_PROMPT), `train.py` (TRAIN_SYSTEM_PROMPT) | Prompt engineering area |
| Add authentication | `main.py` | Need middleware or FastAPI dependency injection |
| Scale ingestion | `ingest.py` | Currently synchronous; needs async/queue for large volumes |
| n8n workflow changes | `tmp_workflow_node_scripts/*.js`, `update_n8n_workflow.py` | Patch scripts modify the exported JSON |
| Database schema | `sql/*.sql` | Applied manually via Supabase SQL editor |
