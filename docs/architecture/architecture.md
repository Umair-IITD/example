# KwikID AI Ingest — Architecture Document

> **READ-ONLY analysis** — No code was modified.

---

## 1. System Architecture Overview

```mermaid
graph TB
    subgraph External["External Systems"]
        FD["Freshdesk API"]
        BB["Bitbucket (Fumadocs)"]
        OAI["OpenAI API"]
        OLL["Ollama (Local)"]
        N8N["n8n Workflow Engine"]
        TG["Telegram"]
        AS["Asana"]
    end

    subgraph Supabase["Supabase (PostgreSQL + pgvector)"]
        DOCS["public.documents\n(vector store)"]
        CM["public.chat_messages\n(conversation history)"]
        SCS["public.support_conversation_state\n(automation state)"]
        KC["public.knowledge_cards\n(LEGACY)"]
    end

    subgraph FastAPI["FastAPI Service (Port 8000)"]
        MAIN["main.py\n(Endpoints)"]
        ING["ingest.py\n(Pipeline)"]
        QRY["query.py\n(Search)"]
        CHAT["chat.py\n(RAG Chat)"]
        TRN["train.py\n(Teach-the-AI)"]
        SUG["suggestions.py"]
        EMB["embeddings.py\n(Embedding Client)"]
        VS["vector_store.py\n(DB Operations)"]
        CHK["chunker.py\n(Chunking)"]
        GS["git_sync.py"]
        PMD["parser_md.py"]
        PJ["parser_json.py"]
        PE["parser_excel.py"]
        PF["parser_freshdesk.py"]
    end

    MAIN --> ING
    MAIN --> QRY
    MAIN --> CHAT
    MAIN --> TRN
    MAIN --> SUG

    ING --> GS
    ING --> PMD
    ING --> PJ
    ING --> PE
    ING --> PF
    ING --> CHK
    ING --> EMB
    ING --> VS

    QRY --> EMB
    QRY --> VS

    CHAT --> QRY
    CHAT --> OAI

    TRN --> CHAT
    TRN --> CHK
    TRN --> EMB
    TRN --> VS

    SUG --> DOCS

    GS --> BB
    PF --> FD
    EMB --> OAI
    EMB --> OLL
    VS --> DOCS
    CHAT --> CM

    N8N --> MAIN
    N8N --> FD
    N8N --> DOCS
    N8N --> OAI
    N8N --> SCS
    N8N --> AS
    TG --> N8N
    FD --> N8N
```

---

## 2. Component Dependency Graph

```mermaid
graph LR
    main["main.py"] --> config["config.py"]
    main --> ingest["ingest.py"]
    main --> query["query.py"]
    main --> chat["chat.py"]
    main --> train["train.py"]
    main --> suggestions["suggestions.py"]

    ingest --> git_sync["git_sync.py"]
    ingest --> parser_md["parser_md.py"]
    ingest --> parser_json["parser_json.py"]
    ingest --> parser_excel["parser_excel.py"]
    ingest --> parser_freshdesk["parser_freshdesk.py"]
    ingest --> chunker["chunker.py"]
    ingest --> embeddings["embeddings.py"]
    ingest --> vector_store["vector_store.py"]
    ingest --> config

    query --> embeddings
    query --> vector_store
    query --> config

    chat --> query
    chat --> config

    train --> chat
    train --> chunker
    train --> embeddings
    train --> vector_store
    train --> config

    suggestions --> config

    parser_md --> chunker
    parser_json --> chunker
    parser_excel --> chunker
    parser_freshdesk --> chunker

    embedder["embedder.py"] -.-> embeddings
    uploader["uploader.py"] -.-> vector_store

    style embedder stroke-dasharray: 5 5
    style uploader stroke-dasharray: 5 5
```

---

## 3. Ingestion Pipeline — Sequence Diagram

```mermaid
sequenceDiagram
    participant Client
    participant API as FastAPI /ingest
    participant Git as git_sync
    participant Parsers as Parsers
    participant Validator as Validator
    participant Chunker as chunker.py
    participant Embedder as EmbeddingClient
    participant Store as VectorStore
    participant Supabase as Supabase DB

    Client->>API: POST /ingest {file_path, freshdesk_*, ...}

    alt Not Freshdesk-only
        API->>Git: sync_repo(url, branch, local_path)
        Git-->>API: repo_path, commit_sha
    end

    API->>Parsers: Parse sources based on file extension
    Note over Parsers: parser_md → Markdown<br/>parser_json → JSON/SO<br/>parser_excel → Excel/CSV<br/>parser_freshdesk → Freshdesk API

    Parsers-->>API: SourceDocument[]

    API->>Validator: _validate_documents(docs)
    Note over Validator: Min chars check<br/>Charset check<br/>Metadata check<br/>Dedup by content hash
    Validator-->>API: valid[], quarantined[], duplicates[]

    loop For each valid document
        API->>Chunker: chunk_document(doc, min/target/max/overlap)
        Chunker-->>API: Chunk[]
    end

    loop Batches of 64 chunks
        API->>Embedder: embed_texts(chunk_contents)
        Embedder->>Supabase: POST /embeddings (or Ollama /api/embed)
        Supabase-->>Embedder: float[][]
        Embedder-->>API: vectors

        API->>Store: upsert_chunks(chunks + vectors)
        Store->>Supabase: UPSERT into documents
        Note over Store: Deterministic UUID5 IDs<br/>Content hash skip<br/>Bulk → per-row fallback
        Supabase-->>Store: result
        Store-->>API: StoreResult
    end

    API-->>Client: IngestResult {stats, errors, report_path}
```

---

## 4. RAG Chat — Sequence Diagram

```mermaid
sequenceDiagram
    participant User
    participant API as FastAPI /chat
    participant Query as query.py
    participant Embed as EmbeddingClient
    participant VS as VectorStore
    participant Supabase as Supabase DB
    participant LLM as OpenAI GPT-4o-mini
    participant History as ChatHistoryStore

    User->>API: POST /chat {query_text, session_id}

    API->>Query: run_query(query_text, match_count*4)
    Query->>Embed: embed_texts([query_text])
    Embed->>LLM: POST /embeddings
    LLM-->>Embed: query_vector

    Query->>VS: match_documents(query_vector, count*4)
    VS->>Supabase: RPC match_documents
    alt RPC Success
        Supabase-->>VS: candidates with similarity
    else RPC Failure (schema mismatch)
        VS->>Supabase: SELECT * FROM documents (paginated)
        Note over VS: Client-side cosine similarity<br/>(up to 5000 rows)
        Supabase-->>VS: all rows
    end
    VS-->>Query: raw matches

    Note over Query: Lexical reranking<br/>Per-source thresholds<br/>Balanced selection<br/>Insufficient context detection
    Query-->>API: QueryResult {matches, confidence}

    API->>History: fetch_recent_turns(session_id, limit)
    History->>Supabase: SELECT from chat_messages
    Supabase-->>History: history turns
    History-->>API: [{role, content}]

    Note over API: Build context block<br/>+ system prompt<br/>+ diagnostics JSON

    API->>LLM: POST /chat/completions (JSON mode)
    Note over LLM: System: grounding rules<br/>User: context + query + diagnostics<br/>History: last N turns
    LLM-->>API: {answer, confidence, citations, follow_up}

    API->>History: append(user message)
    API->>History: append(assistant message)
    History->>Supabase: INSERT into chat_messages

    API-->>User: ChatResult {answer, confidence, citations, ...}
```

---

## 5. n8n Workflow — Sequence Diagram

```mermaid
sequenceDiagram
    participant FD as Freshdesk Webhook
    participant N8N as n8n Workflow
    participant Normalize as Normalize Input
    participant FDConv as Freshdesk Conversations
    participant BuildText as Build Ticket+Conv Text
    participant ParseIssue as Parse Issue (LLM)
    participant RAG as Ingest Service /query
    participant BuildCtx as Build Supabase Context
    participant PrepPrompt as Prepare First Response Prompt
    participant LLM as OpenAI (First Response)
    participant Collect as Collect Final Response
    participant Reply as Freshdesk Public Reply

    FD->>N8N: Ticket created/updated webhook
    N8N->>Normalize: Extract ticketId, subject, body, source

    Normalize->>FDConv: GET /api/v2/tickets/{id}/conversations
    FDConv-->>BuildText: conversation threads

    BuildText->>BuildText: Combine subject + body + conversations → analysisText

    BuildText->>ParseIssue: Analyze with LLM
    Note over ParseIssue: Extract: exactIssue, issueCategory,<br/>searchQuery, structuredSignals,<br/>missingFromTicket

    ParseIssue->>RAG: POST /query {search_query}
    RAG-->>BuildCtx: Matched documents + scores

    Note over BuildCtx: Lexical reranking<br/>Source-type weighting<br/>Confidence scoring<br/>Category thresholds<br/>Missing signal detection<br/>Clarification routing

    BuildCtx->>PrepPrompt: Build LLM prompt
    Note over PrepPrompt: Ticket text + RAG context +<br/>missing hints + output format rules

    PrepPrompt->>LLM: Generate first response
    LLM-->>Collect: Raw LLM output

    Collect->>Reply: Format for Freshdesk
    Note over Reply: Clean prefixes<br/>Enforce sections (greeting, ack, reasons, fixes, next, closing)<br/>Add signature<br/>HTML wrapping<br/>Length limiting (6000 chars)

    Reply->>FD: POST /api/v2/tickets/{id}/reply
```

---

## 6. Data Model — Entity Relationships

```mermaid
erDiagram
    DOCUMENTS {
        uuid id PK "UUID5(repo:type:source_id:chunk_index)"
        text content "Chunk text"
        vector embedding "1536-dim vector"
        jsonb metadata "Rich structured metadata"
        timestamptz created_at
    }

    CHAT_MESSAGES {
        uuid id PK "auto-generated"
        uuid session_id FK "Groups conversation"
        text role "user|assistant|system"
        text content "Message text"
        jsonb metadata "Mode, confidence, draft, etc."
        timestamptz created_at
    }

    SUPPORT_CONVERSATION_STATE {
        uuid id PK "auto-generated"
        text channel "telegram|freshdesk"
        text external_id "Ticket or chat ID"
        text issue_key
        text issue_category
        jsonb collected_fields
        jsonb pending_questions
        timestamptz updated_at
    }

    KNOWLEDGE_CARDS {
        uuid id PK "auto-generated"
        uuid session_id
        text title
        text content
        text status "draft|committed|archived"
        text tenant
        text access_scope
        jsonb suggested_questions
    }

    DOCUMENTS ||--o{ CHAT_MESSAGES : "context source"
    DOCUMENTS ||--o{ KNOWLEDGE_CARDS : "committed as chunks"
    SUPPORT_CONVERSATION_STATE ||--o{ DOCUMENTS : "queries against"
```

---

## 7. Deployment Architecture

```mermaid
graph TB
    subgraph Docker["Docker Host"]
        subgraph Compose["docker-compose.yml"]
            API["fumadocs-api\nPython 3.11-slim\nuvicorn + FastAPI\nPort 8000"]
        end
        subgraph Optional["Optional (commented out)"]
            OLLAMA["ollama\nLocal embeddings\nPort 11434"]
        end
    end

    subgraph Cloud["Cloud Services"]
        SB["Supabase\n(PostgreSQL + pgvector)"]
        OAPI["OpenAI API"]
        FD["Freshdesk"]
        BB["Bitbucket"]
    end

    subgraph Separate["Separate Infrastructure"]
        N8N2["n8n\n(Self-hosted)"]
        DASH["Dashboard Frontend\n(localhost:3000)"]
    end

    API --> SB
    API --> OAPI
    API --> FD
    API --> BB
    API -.-> OLLAMA
    N8N2 --> API
    N8N2 --> FD
    N8N2 --> SB
    DASH --> API

    subgraph CI["GitHub Actions"]
        GHA["ci.yml\npytest only"]
    end
```

---

## 8. Embedding Dimension & Index Strategy

```mermaid
graph LR
    subgraph Embedding["Embedding Layer"]
        direction TB
        INPUT["Input Text\n(max 7000 chars)"]
        OPENAI["OpenAI API\ntext-embedding-ada-002\n→ 1536 dimensions"]
        OLLAMA2["Ollama\nnomic-embed-text\n→ 768 dimensions"]
    end

    subgraph Storage["Vector Storage"]
        direction TB
        VEC["vector(1536)"]
        IDX["HNSW Index\nvector_cosine_ops\nm=16, ef_construction=64"]
        RPC["match_documents RPC\n1 - (embedding <=> query)\ncosine distance"]
    end

    INPUT --> OPENAI
    INPUT --> OLLAMA2
    OPENAI --> VEC
    OLLAMA2 --> VEC
    VEC --> IDX
    IDX --> RPC
```

> [!WARNING]
> The database schema defines `vector(1536)` but the Ollama default model (`nomic-embed-text`) produces 768-dim vectors. This dimension mismatch would cause runtime errors if Ollama is used without schema adjustment.

---

## 9. Configuration Hierarchy

```mermaid
graph TD
    ENV[".env file\n(source of truth)"]
    ENVEX[".env.example\n(template)"]
    CONFIG["app/config.py\nSettings dataclass\n(validation + defaults)"]
    MAIN["app/main.py\nget_settings()\n(per-request instantiation)"]
    DOCKER["docker-compose.yml\nenv_file: .env"]
    N8NENV["n8n/environment.example\n(separate config)"]
    N8N3["n8n instance\n(separate runtime)"]

    ENVEX -.->|template| ENV
    ENV --> CONFIG
    ENV --> DOCKER
    CONFIG --> MAIN
    N8NENV -.->|template| N8N3
```

---

## 10. Security Architecture (As-Is)

```mermaid
graph TB
    subgraph Public["No Authentication"]
        CLIENT["Any HTTP Client"]
        ALL["All API Endpoints\n/ingest, /query, /chat, /train/*"]
    end

    subgraph Service["Service Role Access"]
        API2["FastAPI Backend"]
        SB2["Supabase\n(service_role key bypasses RLS)"]
    end

    subgraph RLS["Row Level Security (enabled, no policies)"]
        DOCS2["documents"]
        CM2["chat_messages"]
        KC2["knowledge_cards"]
        SCS2["support_conversation_state"]
    end

    CLIENT -->|"No auth"| ALL
    ALL --> API2
    API2 -->|"service_role key"| SB2
    SB2 --> RLS
```

> [!CAUTION]
> All FastAPI endpoints are publicly accessible with no authentication. The Supabase service role key (which bypasses all RLS) is used for all database operations. The `supabasesuccess.py` script at the repo root contains a hardcoded service role key.
