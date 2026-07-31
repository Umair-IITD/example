# Project Map — KwikID AI Ingest

## 1. Directory Structure

```
/
├── app/                  # ★ Core FastAPI application
│   ├── main.py           # API Entry point
│   ├── ingest.py         # Ingestion orchestration
│   ├── query.py          # Vector search & reranking
│   ├── chat.py           # RAG Chat engine
│   ├── train.py          # "Teach the AI" flow
│   └── ...               # Parsers, Chunker, Embeddings, VectorStore
├── debug/                # [NEW] Targeted debugging artifacts
├── docs_internal/        # [NEW] Internal engineering documentation
├── evaluation/           # [NEW] Evaluation datasets and scripts
├── experiments/          # [NEW] Isolated experimentation playground
├── logs/                 # [NEW] Structured application logs
├── observability/        # [NEW] Tracing and logging infrastructure
├── query_router/         # [NEW] Future query classification & routing
├── scripts_dev/          # [NEW] Local development utilities
├── sql/                  # Supabase schema definitions
├── tests_local/          # [NEW] Local-only test suites
├── tmp_workflow_node_scripts/ # n8n workflow JavaScript code
└── traces/               # [NEW] Structured JSON execution traces
```

## 2. Core Flows

### Ingestion Flow
1. **Trigger**: `POST /ingest`
2. **Sync**: Git pull from documentation repo (Bitbucket).
3. **Parse**: Source-specific parsing (MD, JSON, Excel, Freshdesk).
4. **Validate**: Deduplication and content sanity checks.
5. **Chunk**: Strategic splitting of documents.
6. **Embed**: Vector generation (OpenAI/Ollama).
7. **Upsert**: Idempotent write to Supabase pgvector.

### Retrieval (RAG) Flow
1. **Query**: User input → `POST /query` or `POST /chat`.
2. **Search**: Vector similarity search in Supabase.
3. **Rerank**: Lexical overlap and balanced source selection.
4. **Context**: Construction of grounded context blocks for LLM.

### Chat Flow
1. **History**: Retrieve session history from `chat_messages`.
2. **Prompt**: Combine System Prompt + History + RAG Context.
3. **LLM**: GPT-4o-mini generation in JSON mode.
4. **Response**: Return answer with citations and confidence.

## 3. Important Services
- **FastAPI**: Main application server.
- **Supabase**: Vector storage and relational data.
- **n8n**: External workflow automation (Freshdesk/Telegram).
- **OpenAI**: LLM and Embedding provider.
```
