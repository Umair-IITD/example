# KwikID AI Ingest — Glossary

> **Purpose**: Domain-specific reference for onboarding engineers. Covers all technical terms, acronyms, component names, and domain concepts used in this repository.

---

## A

### Access Scope
A metadata field (`access_scope`) attached to documents and knowledge cards that controls visibility within the retrieval pipeline. Intended for role-based filtering (e.g., "support", "engineering", "public"). Currently configured but not enforced.

**Used in**: `config.py`, `train.py`, `suggestions.py`, `query.py`

---

## B

### Balanced Selection
The query engine's strategy for ensuring result diversity across source types. Instead of returning all results from the highest-scoring source, it round-robin picks from each source type (md, json, freshdesk, excel) ordered by best score.

**Implemented in**: `query.py` → `_balanced_top_matches()`

---

## C

### Card Head
The representative chunk (chunk_index == 0) of a knowledge card in the `documents` table. Contains enriched metadata including card summary, suggested questions, and full content. Used for listing and detail APIs.

**Implemented in**: `train.py` → `_card_head_metadata_filter()`

### Chat History Store
A Supabase-backed persistence layer for conversation turns. Stores user and assistant messages with metadata (confidence, citations, mode) keyed by `session_id`.

**Table**: `public.chat_messages`
**Implemented in**: `chat.py` → `ChatHistoryStore` class

### Chunk
A segment of a `SourceDocument` created by the chunking process. Each chunk becomes one row in the `documents` vector table with its own embedding. Chunks are sized between `min_chunk_words` (200) and `max_chunk_words` (500) with overlap for continuity.

**Data class**: `chunker.py` → `Chunk`
**Fields**: `chunk_id`, `source_type`, `source_id`, `title`, `heading`, `tags`, `chunk_index`, `content`, `word_count`, `content_hash`, `metadata`

### Chunking Strategy
The method used to split a `SourceDocument` into chunks:
- **`heading_aware`**: Splits on markdown headings (`#`). Used for `.md/.mdx` files.
- **`record_aware`**: Splits on double newlines. Used for JSON, Excel, Freshdesk records.
- **`paragraph`**: Generic paragraph splitting. Default fallback.
- **`auto`**: Automatically selects strategy based on `source_type`.

**Configured via**: `CHUNK_STRATEGY` environment variable

### Confidence Score
A measure of the AI system's trust in its response:
- **In chat**: "high" (chunks directly answer), "medium" (partial match), "low" (weak match). Returned in the JSON response.
- **In n8n workflow**: Numeric composite score combining similarity, rerank, and lexical overlap. Compared against category-specific thresholds.

### Content Hash
SHA-256 hash of chunk text content. Used for two purposes:
1. **Deduplication** during ingestion (identical content across sources)
2. **Skip detection** during upsert (unchanged content doesn't need re-embedding)

### Cosine Distance
The distance metric used for vector similarity search in Supabase pgvector. Computed as `embedding <=> query_embedding`. Similarity = `1 - cosine_distance`. Higher similarity = more relevant.

---

## D

### Deterministic ID
UUID5 computed from `repo:source_type:source_id:chunk_index` using `uuid.NAMESPACE_URL`. Ensures the same document chunk always gets the same database row ID, making upserts idempotent.

**Implemented in**: `vector_store.py` → `_deterministic_id()`

### Documents Table
The primary Supabase table (`public.documents`) storing all vectorized knowledge. Contains columns: `id` (UUID), `content` (text), `embedding` (vector(1536)), `metadata` (JSONB).

---

## E

### EmbeddingClient
The abstraction layer for generating text embeddings. Supports two providers:
- **OpenAI**: Production path via `POST /embeddings`
- **Ollama**: Local development path via `POST /api/embed` (with legacy `/api/embeddings` fallback)

**Class**: `embeddings.py` → `EmbeddingClient`

### Evidence Hierarchy
The strict ordering of information sources that the chat AI must follow:
1. **AUTHORITATIVE**: Retrieved context chunks (from vector search)
2. **SECONDARY**: Diagnostics JSON (query metadata)
3. **NOT AUTHORITATIVE**: Prior conversation turns

Defined in the `SYSTEM_PROMPT` in `chat.py`.

---

## F

### Freshdesk
Think360's customer support ticketing platform. The system integrates with Freshdesk in two directions:
1. **Ingestion**: Pulls tickets + conversations via REST API for vectorization
2. **Response**: Posts AI-drafted replies back to tickets (via n8n workflow)

### Freshdesk Ticket Filters
Configurable filters for Freshdesk ticket ingestion:
- `ticket_types`: Question, Problem, Incident, etc.
- `statuses`: open, pending, resolved, closed
- `priorities`: low, medium, high, urgent
- `requester_ids`, `responder_ids`, `group_ids`: Numeric ID filters
- `updated_until`: Date-based upper bound

**Data class**: `parser_freshdesk.py` → `FreshdeskTicketFilters`

### Fumadocs
The internal documentation framework used by KwikID. Documentation is stored as Markdown/MDX files in a Bitbucket repository. "Fumadocs" appears to be the documentation platform name (possibly based on the Fumadocs open-source framework).

---

## G

### Git Sync
The process of cloning or pulling the latest documentation from Bitbucket before ingestion. Uses subprocess calls to `git clone`, `git fetch`, `git checkout`, `git pull`.

**Implemented in**: `git_sync.py` → `sync_repo()`

### Grounding
The principle that the AI must base all factual claims on retrieved context chunks, not its training data. Enforced through the system prompt with explicit rules against hallucination.

---

## H

### HNSW Index
Hierarchical Navigable Small World — the approximate nearest neighbor index used by pgvector for fast similarity search. Configured with `m=16, ef_construction=64` using `vector_cosine_ops`.

**Defined in**: `sql/kb_chunks.sql` (also applied to `documents` table)

---

## I

### Index Version
A versioning scheme for the vector store (`WRITE_INDEX_VERSION`, `ACTIVE_INDEX_VERSION`). Allows writing to a new index version while querying the old one during migration. Currently both set to `v1`.

### Ingest Report
A JSON file written to disk during ingestion containing details about quarantined documents, validation errors, and processing statistics. Stored under `INGEST_REPORTS_PATH`.

### Insufficient Context
A flag set by the query engine when the best similarity scores fall below the minimum confidence threshold. Signals to the chat engine (or n8n workflow) that it should ask clarifying questions rather than attempt an answer.

---

## K

### Knowledge Card
A unit of curated knowledge created through the "Teach-the-AI" feature. Contains:
- **title**: Topic name
- **summary**: Brief description
- **content**: Canonical help-center-style article text
- **tags**: Categorization labels
- **tenant/access_scope**: Visibility controls
- **suggested_questions**: Example queries this card answers

On commit, a card is chunked, embedded, and stored in `documents` with `source_type="manual"`.

### KwikID
Think360's AI-driven KYC (Know Your Customer) and identity verification/onboarding platform. The product that this support AI system serves.

---

## L

### Lexical Reranking
A post-retrieval scoring method that computes word overlap between the query and candidate documents. Used to complement vector similarity (which can miss exact term matches).

**Python implementation** (`query.py`): Simple Jaccard-like ratio
**JavaScript implementation** (`Build_Supabase_Context.js`): Weighted composite with phrase matching

### Local Fallback
When the `match_documents` Supabase RPC fails, the system downloads all rows from the `documents` table and computes cosine similarity in Python. Limited to `LOCAL_MATCH_FALLBACK_MAX_ROWS` (default 5000).

---

## M

### Manual Source Type
Documents created through the "Teach-the-AI" knowledge capture flow. Stored with `source_type="manual"` and `metadata.author="chat_train"` in the vector store.

### Match Documents
A Supabase PostgreSQL function (RPC) that performs cosine similarity search on the `documents` table. Takes a query embedding vector, match count, and minimum similarity threshold.

```sql
SELECT id, content, metadata, 1 - (embedding <=> query_embedding) AS similarity
FROM documents
WHERE similarity >= threshold
ORDER BY embedding <=> query_embedding
LIMIT count;
```

### Metadata
JSONB column on the `documents` table containing structured information about each chunk:
- Common fields: `source_type`, `title`, `tags`, `tenant`, `access_scope`
- Source-specific: `ticket_id`, `post_link`, `file_path`, `excel_row`, etc.
- System fields: `content_hash`, `repo`, `commit_sha`, `index_version`

---

## N

### n8n
An open-source workflow automation platform used to orchestrate the end-to-end support ticket response pipeline. Runs as a separate service (not part of this repository's Docker setup).

### Needs Clarification
A boolean flag computed by the n8n workflow's `Build_Supabase_Context.js` indicating whether the AI should ask for more information instead of providing a resolution. Triggered by: insufficient context, missing critical signals, low confidence, informational-only tickets.

---

## O

### Ollama
An open-source local LLM server. Used as the development-mode embedding provider (`nomic-embed-text` model). Optional service in docker-compose (commented out).

### Orphan Chunk
A final chunk with very few words (< `min_orphan_words`, default 50). The chunker automatically merges orphan chunks back into the previous chunk to avoid creating trivially small embeddings.

### Overlap Words
The number of words carried from the end of one chunk into the beginning of the next (default 50). Provides context continuity for retrieval.

---

## P

### Parser
A module that converts a specific data format into `SourceDocument` objects:
- `parser_md.py`: Markdown/MDX
- `parser_json.py`: JSON (StackOverflow Teams format + generic)
- `parser_excel.py`: .xlsx/.xls/.csv
- `parser_freshdesk.py`: Freshdesk REST API

### pgvector
A PostgreSQL extension that adds vector data types and similarity search operators. Used via Supabase for storing and querying document embeddings.

---

## Q

### Quarantine
Documents that fail validation during ingestion are "quarantined" — excluded from embedding/upsert and logged to a JSON report with the failure reason (too_short, charset_sanity, missing_metadata, duplicate_content).

### Query Hash
SHA-256 hash of the query text, included in responses and logs for traceability and debugging.

---

## R

### RAG (Retrieval-Augmented Generation)
The core architectural pattern: retrieve relevant knowledge chunks from the vector store, then augment the LLM's prompt with these chunks to generate grounded responses. Prevents hallucination by constraining the AI to organizational knowledge.

### Rerank Candidate Multiplier
A factor (default 4) applied to `match_count` when fetching vector search candidates. If the user requests 5 results, the system fetches 20 candidates, then reranks and filters down to the best 5.

### RLS (Row Level Security)
Supabase/PostgreSQL security feature that restricts row-level access. Enabled on all tables but no policies are defined — the service uses `service_role` which bypasses RLS entirely.

---

## S

### Service Role Key
A Supabase API key that bypasses all Row Level Security policies. Used by the FastAPI backend for all database operations. Should be kept secret and never exposed to clients.

### Session ID
A UUID that groups conversation turns together. Used in:
- **Chat**: Links user and assistant messages in `chat_messages`
- **Train**: Links training conversation turns
- **Support state**: Links multi-turn support automation in `support_conversation_state`

### Source Document
The intermediate representation of a parsed knowledge source before chunking. Contains the full document text, title, heading, tags, creation date, and rich metadata.

**Data class**: `chunker.py` → `SourceDocument`

### Source Type
A classification label for knowledge sources:
- `md`: Markdown documentation
- `json`: JSON/StackOverflow data
- `excel`: Excel/CSV support logs
- `freshdesk`: Freshdesk support tickets
- `manual`: Teach-the-AI knowledge cards

### StackOverflow Teams
Think360's internal Q&A platform (hosted on StackOverflow Teams). Knowledge is ingested via JSON exports containing posts, answers, comments, votes, and user data.

### StoreResult
The return value from `VectorStore.upsert_chunks()`:
- `created`: Number of new rows inserted
- `updated`: Number of existing rows with changed content
- `skipped`: Number of rows with unchanged content (no re-embedding needed)
- `errors`: List of error messages for failed rows

### Support Conversation State
A Supabase table (`public.support_conversation_state`) that tracks multi-turn support automation state for n8n workflows. Tracks channel, external ID, issue category, collected fields, and pending questions.

---

## T

### Teach-the-AI
The knowledge capture feature (`/train/*` endpoints) where subject matter experts create "knowledge cards" through a conversational LLM interface. Cards are committed to the vector store and become part of the retrieval corpus.

### Tenant
A metadata field (`metadata.tenant`) for multi-tenant isolation. Threaded through ingestion, querying, and training. Currently configured but not enforced as a hard isolation boundary.

---

## U

### Upsert
The database operation that inserts a new row or updates an existing one (based on the deterministic UUID). The vector store uses Supabase's `.upsert()` method with `on_conflict="id"`.

---

## V

### Vector Store
The Supabase pgvector-backed storage and retrieval layer. Wraps CRUD operations on the `documents` table with deterministic IDs, content hash tracking, and RPC-based similarity search.

**Class**: `vector_store.py` → `VectorStore`

---

## W

### Write Index Version
The version tag (`WRITE_INDEX_VERSION`) stamped on new document rows during ingestion. Allows blue-green deployment of new index versions while the `ACTIVE_INDEX_VERSION` controls which version is queried.
