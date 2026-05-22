# Environment Variables Reference

Complete reference for all environment variables. See `.env.example` for production-safe defaults with inline documentation.

## Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `SUPABASE_URL` | Supabase project URL | `https://abc123.supabase.co` |
| `SUPABASE_KEY` | Service-role key (bypasses RLS) | `eyJhbGci...` |
| `OPENAI_API_KEY` | OpenAI API key (embedding) | `sk-...` |
| `RAG_API_KEY` | API authentication key for this service | (generate with `secrets.token_urlsafe(32)`) |
| `EMBEDDING_PROVIDER` | Embedding backend: `openai` or `ollama` | `openai` |

## Server

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | Listen port |
| `LOG_LEVEL` | `info` | Log verbosity: debug / info / warning / error |
| `CORS_ALLOWED_ORIGINS` | localhost only | Comma-separated allowed origins |

## Embedding

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDING_PROVIDER` | — | **Required**: `openai` or `ollama` |
| `EMBEDDING_API_KEY` | — | API key (falls back to OPENAI_API_KEY) |
| `OPENAI_API_KEY` | — | OpenAI key (used if EMBEDDING_API_KEY unset) |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model name |
| `EMBEDDING_BASE_URL` | `https://api.openai.com/v1` | API base URL |
| `EMBEDDING_DIMENSIONS` | `1536` | Embedding vector dimensions |
| `EMBEDDING_BATCH_SIZE` | `64` | Chunks per API call |
| `EMBEDDING_TIMEOUT_S` | `60` | Request timeout |
| `EMBEDDING_MAX_RETRIES` | `3` | Max retry attempts |
| `EMBEDDING_RETRY_BASE_DELAY_S` | `0.8` | Retry base delay (exponential backoff) |

## B1 Embedding Resilience

| Variable | Default | Description |
|----------|---------|-------------|
| `B1_EMBEDDING_CONNECT_TIMEOUT_S` | `10` | DNS + TCP connect timeout |
| `B1_EMBEDDING_READ_TIMEOUT_S` | `90` | Response read timeout |
| `B1_EMBEDDING_WRITE_TIMEOUT_S` | `30` | Request write timeout |
| `B1_EMBEDDING_MAX_RETRIES` | `6` | Max embedding retries |
| `B1_EMBEDDING_CB_THRESHOLD` | `3` | Circuit breaker trip threshold (consecutive failures) |
| `B1_EMBEDDING_CB_COOLDOWN_S` | `60` | Circuit breaker cooldown period |

## Chunking

| Variable | Default | Description |
|----------|---------|-------------|
| `CHUNK_TARGET_TOKENS` | `1200` | Target tokens per chunk |
| `CHUNK_OVERLAP_TOKENS` | `150` | Token overlap between adjacent chunks |
| `EMBEDDING_MAX_INPUT_TOKENS` | `7000` | Hard max per chunk (OpenAI limit: 8192) |
| `CHUNK_STRATEGY` | `auto` | Strategy: auto / paragraph / heading_aware / record_aware |
| `CHUNK_MAX_CHARS` | `6000` | Hard char cap per chunk |
| `MIN_CHUNK_WORDS` | `200` | Min words for a chunk (word-based fallback) |
| `TARGET_CHUNK_WORDS` | `350` | Target words (word-based fallback) |
| `MAX_CHUNK_WORDS` | `500` | Max words (word-based fallback) |
| `CHUNK_OVERLAP_WORDS` | `50` | Word overlap (word-based fallback) |
| `CHUNK_MIN_ORPHAN_WORDS` | `50` | Min words for orphan chunk (else merged) |
| `MIN_DOCUMENT_CHARS` | `60` | Skip documents shorter than this |

## Index Versioning

| Variable | Default | Description |
|----------|---------|-------------|
| `B1_INDEX_VERSION` | `v2` | Written by ingestion scripts |
| `ACTIVE_INDEX_VERSION` | `v2` | **LIVE RETRIEVAL**: flip to v2 after migration |
| `WRITE_INDEX_VERSION` | `v2` | Written by /ingest endpoint |

## Supabase Tables

| Variable | Default | Description |
|----------|---------|-------------|
| `TABLE_NAME` | `documents` | Legacy document table |
| `B3_KNOWLEDGE_ARTICLES_TABLE` | `rag_knowledge_articles` | B3 articles table |
| `B3_KNOWLEDGE_CHUNKS_TABLE` | `rag_knowledge_chunks` | B3 chunks table |
| `B3_REVIEW_QUEUE_TABLE` | `rag_review_queue` | Human review queue |
| `CHAT_HISTORY_TABLE` | `chat_messages` | Conversation history |

## API Authentication

| Variable | Default | Description |
|----------|---------|-------------|
| `RAG_API_KEY` | — | **Required**: single API key |
| `RAG_API_KEYS` | — | Comma-separated keys (for rotation) |
| `RAG_CHAT_RATE_LIMIT` | `20` | Max requests per 60s per IP for /rag/chat |

## Retrieval

| Variable | Default | Description |
|----------|---------|-------------|
| `B1_HYBRID_RETRIEVAL_ENABLED` | `true` | Enable hybrid retrieval |
| `HYBRID_RETRIEVAL_ENABLED` | `true` | Legacy switch (must also be true) |
| `RETRIEVAL_SEMANTIC_TOP_K` | `20` | Semantic candidates |
| `RETRIEVAL_KEYWORD_ENABLED` | `true` | Enable FTS keyword search |
| `RETRIEVAL_KEYWORD_TOP_K` | `20` | FTS candidates |
| `RETRIEVAL_RRF_K` | `60` | RRF smoothing factor |
| `RETRIEVAL_RERANK_ENABLED` | `true` | Enable reranking |
| `RETRIEVAL_RERANK_TOP_K` | `5` | Top chunks to rerank |
| `RETRIEVAL_FINAL_TOP_K` | `5` | Final result count |
| `RETRIEVAL_MIN_SIMILARITY` | `0.20` | Post-rerank minimum score |
| `B1_DEFAULT_TOP_K` | `10` | /rag/chat default top_k |
| `B1_DEFAULT_SIMILARITY_THRESHOLD` | `0.27` | /rag/chat default threshold |
| `B1_SOP_SIMILARITY_BOOST` | `0.15` | SOP score boost (in SQL) |
| `RERANK_ENABLED` | `true` | Enable legacy reranking |
| `RERANK_CANDIDATE_MULTIPLIER` | `4` | Retrieval multiplier for candidates |
| `CONFIDENCE_MIN_SIMILARITY` | `0.20` | Min similarity for confidence |
| `CONFIDENCE_MIN_RERANK` | `0.05` | Min rerank score for confidence |
| `QUERY_SOURCE_THRESHOLD_FRESHDESK` | `0.20` | Per-source threshold |
| `QUERY_SOURCE_THRESHOLD_MD` | `0.20` | Per-source threshold |
| `QUERY_SOURCE_THRESHOLD_JSON` | `0.20` | Per-source threshold |
| `QUERY_SOURCE_THRESHOLD_EXCEL` | `0.20` | Per-source threshold |

## Governance / Classification

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKFLOW_EXACT_SIMILARITY` | `0.55` | Boosted score threshold for exact_match |
| `WORKFLOW_RELATED_SIMILARITY` | `0.35` | Boosted score threshold for related_match |
| `ENABLE_QUERY_ROUTER` | `true` | Enable keyword-based query routing |

## Chat / Generation

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_CHAT_API_KEY` | — | OpenAI key for chat (falls back to OPENAI_API_KEY) |
| `OPENAI_CHAT_MODEL` | `gpt-4o-mini` | LLM model |
| `OPENAI_CHAT_BASE_URL` | `https://api.openai.com/v1` | LLM API base |
| `CHAT_TIMEOUT_S` | `60` | LLM request timeout |
| `CHAT_MAX_RETRIES` | `3` | LLM max retries |
| `CHAT_TEMPERATURE` | `0.2` | LLM temperature (low = deterministic) |
| `CHAT_MAX_OUTPUT_TOKENS` | `800` | Max response length |
| `CHAT_CONTEXT_CHUNK_MAX_CHARS` | `3500` | **CRITICAL**: max chars per chunk in context |
| `CHAT_HISTORY_TURNS` | `6` | Prior conversation turns to include |

## Freshdesk

| Variable | Default | Description |
|----------|---------|-------------|
| `FRESHDESK_ENABLED` | `false` | Enable Freshdesk ticket ingestion |
| `FRESHDESK_DOMAIN` | — | Your Freshdesk subdomain |
| `FRESHDESK_API_KEY` | — | Freshdesk API key |
| `FRESHDESK_WEBHOOK_ENABLED` | `false` | Enable inbound webhook |
| `FRESHDESK_WEBHOOK_SECRET` | — | HMAC secret (required in production) |
| `FRESHDESK_WEBHOOK_ENFORCE_HMAC` | `false` | **Set true in production** |
| `FRESHDESK_WEBHOOK_MIN_CONFIDENCE` | `low` | Minimum confidence for auto-reply |

## Redis

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_RATE_LIMIT_ENABLED` | `false` | Enable Redis rate limiting |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `REDIS_SOCKET_TIMEOUT_S` | `1.0` | Redis socket timeout |

## Observability

| Variable | Default | Description |
|----------|---------|-------------|
| `PROMETHEUS_ENABLED` | `false` | Enable /metrics endpoint |
| `FASTAPI_DOCS_ENABLED` | `false` | Expose /docs and /openapi.json |
| `DEBUG_TRACE` | `false` | Write request traces to TRACE_DIR |
| `DEBUG_RAG` | `false` | Include chunk content in API responses |
| `TRACE_DIR` | `./traces` | Trace file directory |
| `LOG_DIR` | `./logs` | Log file directory |
| `INGEST_REPORTS_PATH` | `./data/reports` | Ingestion report directory |

## B3 Knowledge Base

| Variable | Default | Description |
|----------|---------|-------------|
| `B3_KNOWLEDGE_SOURCE_DIR` | `../../../../data/raw/More_data` | SO Teams export directory |
| `B3_KNOWLEDGE_MIN_QUALITY_SCORE` | `0.55` | Quality gate for articles |
| `B3_KNOWLEDGE_SIMILARITY_BOOST` | `0.08` | Similarity boost for KB chunks |
| `B3_KNOWLEDGE_MIN_CHUNK_CHARS` | `100` | Min chars for KB chunk embedding |
