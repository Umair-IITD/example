# Database and Supabase

## Supabase Configuration

Supabase is used as the primary data store for:
- Vector embeddings (pgvector extension)
- Document metadata
- Chat history
- Review queue
- Session state (optional, for n8n workflow)

**Connection**: Service-role key (bypasses RLS) via Python `supabase-py` client.

```python
# rag_engine/database/supabase_client.py
client = create_client(SUPABASE_URL, SUPABASE_KEY)
```

**Security**: The service-role key is never exposed to browser clients or logged. See SECURITY_REVIEW.md.

## Tables

### `documents` (Legacy)

Primary vector store for the `/query` and `/ingest` endpoints.

```sql
CREATE TABLE documents (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    content         text NOT NULL,
    embedding       vector(1536),          -- OpenAI text-embedding-3-small
    metadata        jsonb DEFAULT '{}',
    source_type     text,                  -- 'md', 'json', 'excel', 'freshdesk', 'sop'
    source_id       text,                  -- original file path or ticket ID
    index_version   text DEFAULT 'v1',     -- version tag for live migration
    created_at      timestamptz DEFAULT now(),
    updated_at      timestamptz DEFAULT now()
);

CREATE INDEX ON documents USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX ON documents (source_type, index_version);
```

### `rag_sop_chunks`

Dedicated table for SOP procedure chunks with boosted retrieval.

```sql
CREATE TABLE rag_sop_chunks (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sop_id          text NOT NULL,         -- filename or logical SOP identifier
    chunk_index     int NOT NULL,
    content         text NOT NULL,
    embedding       vector(1536),
    content_hash    text,                  -- SHA-256 for deduplication
    metadata        jsonb DEFAULT '{}',
    index_version   text DEFAULT 'v2',
    created_at      timestamptz DEFAULT now()
);
```

The +0.15 similarity boost is applied at query time in the `match_all_b1_sources` RPC, not stored as a column.

### `rag_knowledge_articles`

B3 knowledge base articles from Stack Overflow Teams.

```sql
CREATE TABLE rag_knowledge_articles (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id     text UNIQUE,           -- SO post ID
    title           text NOT NULL,
    content         text NOT NULL,
    quality_score   float NOT NULL,        -- 0.0–1.0, computed by KnowledgeClassifier
    client          text,                  -- tenant identifier
    tags            text[],
    created_at      timestamptz DEFAULT now()
);
```

### `rag_knowledge_chunks`

Chunked embeddings of knowledge articles.

```sql
CREATE TABLE rag_knowledge_chunks (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id      uuid REFERENCES rag_knowledge_articles(id),
    chunk_index     int NOT NULL,
    content         text NOT NULL,
    embedding       vector(1536),
    quality_score   float,
    content_hash    text,
    index_version   text DEFAULT 'v2',
    created_at      timestamptz DEFAULT now()
);
```

### `chat_messages`

Per-session conversation history.

```sql
CREATE TABLE chat_messages (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  text NOT NULL,
    role        text NOT NULL CHECK (role IN ('user', 'assistant')),
    content     text NOT NULL,
    created_at  timestamptz DEFAULT now()
);

CREATE INDEX ON chat_messages (session_id, created_at DESC);
```

### `rag_review_queue`

Human review queue for low-confidence responses.

```sql
CREATE TABLE rag_review_queue (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      text,
    ticket_id       text,
    query_text      text NOT NULL,
    response        text NOT NULL,
    confidence      text NOT NULL,
    requires_human  boolean NOT NULL,
    status          text DEFAULT 'pending',  -- pending / reviewed / approved / rejected
    reviewer_notes  text,
    created_at      timestamptz DEFAULT now(),
    reviewed_at     timestamptz
);
```

### `support_conversation_state` (n8n)

Optional session state table used by the n8n workflow for multi-turn conversations.

```sql
-- See: sql/support_conversation_state.sql
CREATE TABLE support_conversation_state (
    session_id      text PRIMARY KEY,
    ticket_id       text,
    state           jsonb DEFAULT '{}',
    created_at      timestamptz DEFAULT now(),
    updated_at      timestamptz DEFAULT now()
);
```

## RPC Functions

### `match_all_b1_sources`

Primary retrieval RPC — hybrid semantic search across all source tables.

```sql
CREATE OR REPLACE FUNCTION match_all_b1_sources(
    query_embedding vector(1536),
    match_count     int DEFAULT 10,
    match_threshold float DEFAULT 0.0,
    p_index_version text DEFAULT 'v2',
    p_client        text DEFAULT NULL
)
RETURNS TABLE (
    id              uuid,
    content         text,
    similarity      float,
    boosted_score   float,     -- similarity + 0.15 for SOP chunks
    chunk_type      text,
    source_type     text,
    source_table    text,
    metadata        jsonb
) ...
```

### `match_all_b1_sources_fts`

FTS retrieval RPC — keyword search across all source tables.

Requires migrations: `B1_007_fts_setup.sql`, `B1_008_fts_rpc.sql`.

## Indexes

```sql
-- Vector similarity (HNSW) — O(log n) approximate nearest-neighbor
CREATE INDEX ON documents USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Full-text search (GIN) — fast keyword lookup
CREATE INDEX ON documents USING GIN (to_tsvector('english', content));
CREATE INDEX ON rag_sop_chunks USING GIN (to_tsvector('english', content));

-- Filter indexes
CREATE INDEX ON documents (source_type, index_version);
CREATE INDEX ON rag_knowledge_chunks (article_id, index_version);
```

## Index Versioning

Every chunk row has an `index_version` column. This enables zero-downtime index migration:

```
ACTIVE_INDEX_VERSION=v2   ← Read by /rag/chat (retrieval)
B1_INDEX_VERSION=v2       ← Written by ingestion
WRITE_INDEX_VERSION=v2    ← Written by /ingest endpoint
```

Migration procedure:
1. Ingest new data with `B1_INDEX_VERSION=v2`
2. Run `scripts/reingest_v2.py` for full historical re-ingestion if needed
3. Validate v2 quality with `scripts/validate_b1_db_integrity.py`
4. Set `ACTIVE_INDEX_VERSION=v2`, restart service
5. Old v1 chunks remain but are ignored by retrieval
6. Clean up: `DELETE FROM documents WHERE index_version='v1'` (after confidence period)

## Row Level Security (RLS)

RLS policies are configured in Supabase for multi-tenant isolation:
- Anon role: SELECT only on public data
- Authenticated role: Full access to own-tenant data
- Service role: Bypasses RLS (used by backend for ingestion and retrieval)

**Important**: The service-role key (`SUPABASE_KEY`) is a security boundary. It must never be:
- Logged
- Committed to git
- Returned in API responses
- Used in browser-side code

## Connection Management

The `supabase-py` client is not thread-safe by default. In the multi-threaded FastAPI context:
- A new client instance is created per request for ingestion operations
- The RAG engine uses a module-level client with connection pooling

Supabase uses PostgREST as the API layer — HTTP/1.1 connections with connection reuse. Under high load, consider adding PgBouncer or Supabase's connection pooler.
