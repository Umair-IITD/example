# Configuration Reference — KwikID AI Ingest Service

**Sprint:** 2.11
**Last Updated:** 2026-06-04

Complete reference for all environment variables. Copy `.env.example` to `.env` and fill in the required values before deployment.

---

## Required Variables (service fails to start without these)

| Variable | Example | Description |
|----------|---------|-------------|
| `SUPABASE_URL` | `https://abc.supabase.co` | Supabase project URL |
| `SUPABASE_KEY` | `eyJ...` | Supabase service-role key — **never expose to browser** |
| `RAG_API_KEY` | `sk-...` | Primary API key for service authentication |

---

## Authentication

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTH_ENABLED` | `false` | Enable API key authentication. Must be `true` in production. |
| `APPROVER_API_KEYS` | — | One `identity:key` pair per line for APPROVER role |
| `OPERATOR_API_KEYS` | — | One `identity:key` pair per line for OPERATOR role |
| `ADMIN_API_KEYS` | — | One `identity:key` pair per line for ADMIN role |
| `RAG_API_KEYS` | — | Comma-separated list (alternative multi-key format) |

---

## Server

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | Listen port |
| `LOG_LEVEL` | `info` | Python logging level |
| `CORS_ALLOWED_ORIGINS` | — | Comma-separated allowed origins |
| `FASTAPI_DOCS_ENABLED` | `false` | Enable /docs and /redoc (dev only) |

---

## Embedding

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDING_PROVIDER` | `openai` | Provider: `openai` or `ollama` |
| `OPENAI_API_KEY` | — | OpenAI API key (fallback if `EMBEDDING_API_KEY` unset) |
| `EMBEDDING_API_KEY` | — | Primary embedding API key |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model name |
| `EMBEDDING_DIMENSIONS` | `1536` | Embedding vector dimensions |
| `EMBEDDING_BASE_URL` | `https://api.openai.com/v1` | API base URL |
| `EMBEDDING_BATCH_SIZE` | `64` | Texts per embedding API call |
| `EMBEDDING_TIMEOUT_S` | `60` | Per-request timeout (seconds) |
| `EMBEDDING_MAX_RETRIES` | `3` | Maximum retry attempts |
| `EMBEDDING_RETRY_BASE_DELAY_S` | `0.8` | Initial retry delay (seconds) |

### Phase B1 Embedding Resilience

| Variable | Default | Description |
|----------|---------|-------------|
| `B1_EMBEDDING_CONNECT_TIMEOUT_S` | `10` | TCP connect timeout |
| `B1_EMBEDDING_READ_TIMEOUT_S` | `90` | Read timeout |
| `B1_EMBEDDING_WRITE_TIMEOUT_S` | `30` | Write timeout |
| `B1_EMBEDDING_POOL_TIMEOUT_S` | `10` | Connection pool acquire timeout |
| `B1_EMBEDDING_MAX_RETRIES` | `6` | Maximum retry attempts (Phase B1) |
| `B1_EMBEDDING_RETRY_BASE_DELAY_S` | `1.0` | Initial retry delay |
| `B1_EMBEDDING_RETRY_MAX_DELAY_S` | `60.0` | Maximum retry delay cap |
| `B1_EMBEDDING_CB_THRESHOLD` | `3` | Circuit breaker failure threshold |
| `B1_EMBEDDING_CB_COOLDOWN_S` | `60` | Circuit breaker cooldown (seconds) |

---

## Chat / Generation

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_CHAT_API_KEY` | — | OpenAI API key for chat completions |
| `OPENAI_CHAT_MODEL` | `gpt-4o-mini` | Chat completion model |
| `OPENAI_CHAT_BASE_URL` | `https://api.openai.com/v1` | Chat API base URL |
| `CHAT_TIMEOUT_S` | `60` | Chat request timeout |
| `CHAT_MAX_RETRIES` | `3` | Maximum retries |
| `CHAT_TEMPERATURE` | `0.2` | Sampling temperature |
| `CHAT_MAX_OUTPUT_TOKENS` | `800` | Maximum tokens in response |
| `CHAT_CONTEXT_CHUNK_MAX_CHARS` | `3500` | Max chars per chunk in context |
| `CHAT_HISTORY_TURNS` | `6` | Prior conversation turns in context |
| `CHAT_HISTORY_TABLE` | `chat_messages` | Supabase table for chat history |

---

## Chunking

| Variable | Default | Description |
|----------|---------|-------------|
| `CHUNK_TARGET_TOKENS` | `1200` | Target tokens per chunk |
| `CHUNK_OVERLAP_TOKENS` | `150` | Token overlap between chunks |
| `MIN_CHUNK_WORDS` | `200` | Minimum words per chunk |
| `MAX_CHUNK_WORDS` | `500` | Maximum words per chunk |
| `CHUNK_OVERLAP_WORDS` | `50` | Word overlap between chunks |
| `CHUNK_STRATEGY` | `auto` | Strategy: auto/paragraph/heading_aware/record_aware |
| `CHUNK_MAX_CHARS` | `6000` | Hard character cap per chunk |
| `CHUNK_MIN_ORPHAN_WORDS` | `50` | Min words for trailing chunk |
| `MIN_DOCUMENT_CHARS` | `60` | Documents shorter than this are skipped |
| `EMBEDDING_MAX_INPUT_TOKENS` | `7000` | Hard token limit before embedding |

---

## Retrieval

| Variable | Default | Description |
|----------|---------|-------------|
| `B1_HYBRID_RETRIEVAL_ENABLED` | `true` | Enable hybrid (semantic + keyword) retrieval |
| `RETRIEVAL_SEMANTIC_TOP_K` | `20` | Candidate count from semantic search |
| `RETRIEVAL_KEYWORD_ENABLED` | `true` | Enable FTS keyword search |
| `RETRIEVAL_KEYWORD_TOP_K` | `20` | Candidate count from keyword search |
| `RETRIEVAL_RRF_K` | `60` | RRF smoothing constant |
| `RETRIEVAL_RERANK_ENABLED` | `true` | Enable BM25 reranking pass |
| `RETRIEVAL_RERANK_TOP_K` | `5` | Candidates after reranking |
| `RETRIEVAL_FINAL_TOP_K` | `5` | Final result count |
| `RETRIEVAL_MIN_SIMILARITY` | `0.2` | Minimum similarity score |
| `B1_DEFAULT_TOP_K` | `10` | Default retrieval result count |
| `B1_DEFAULT_SIMILARITY_THRESHOLD` | `0.27` | Default similarity threshold |
| `RERANK_CROSS_ENCODER_ENABLED` | `false` | Enable cross-encoder reranking |
| `RERANK_CROSS_ENCODER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Cross-encoder model |

---

## Freshdesk Webhook

| Variable | Default | Description |
|----------|---------|-------------|
| `FRESHDESK_WEBHOOK_ENABLED` | `false` | Enable inbound webhook |
| `FRESHDESK_WEBHOOK_SECRET` | — | HMAC secret — required if ENFORCE_HMAC=true |
| `FRESHDESK_WEBHOOK_ENFORCE_HMAC` | `false` | Reject unsigned webhook requests |
| `FRESHDESK_WEBHOOK_DEFAULT_CLIENT` | — | Default client tag for untagged tickets |
| `FRESHDESK_WEBHOOK_REPLY_AS_NOTE` | `true` | Post replies as private notes |
| `FRESHDESK_WEBHOOK_MIN_CONFIDENCE` | `low` | Minimum confidence for auto-reply |
| `FRESHDESK_WEBHOOK_TENANT_TAG_PREFIX` | `client:` | Tag prefix for tenant identification |

---

## Audit Backend

| Variable | Default | Description |
|----------|---------|-------------|
| `AUDIT_BACKEND` | `supabase` | Backend: `inmemory` (dev) or `supabase` (prod) |
| `AUDIT_QUERY_MAX_LIMIT` | `500` | Maximum events per API response |
| `AUDIT_RETRY_ENABLED` | `true` | Enable retry on failed Supabase writes |
| `AUDIT_MAX_RETRIES` | `4` | Total write attempts (1 = no retry) |
| `AUDIT_OUTBOX_MAX_SIZE` | `1000` | Max in-memory buffered events |

---

## Prometheus Metrics

| Variable | Default | Description |
|----------|---------|-------------|
| `PROMETHEUS_ENABLED` | `false` | Informational flag (endpoint always active) |

---

## Rate Limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `RAG_CHAT_RATE_LIMIT` | `20` | Requests per minute per IP for /rag/chat |
| `REDIS_RATE_LIMIT_ENABLED` | `false` | Use Redis for distributed rate limiting |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `REDIS_SOCKET_TIMEOUT_S` | `1.0` | Redis socket timeout |
| `REDIS_SOCKET_CONNECT_TIMEOUT_S` | `0.5` | Redis connect timeout |

---

## Index Versioning

| Variable | Default | Description |
|----------|---------|-------------|
| `B1_INDEX_VERSION` | `v1` | Version written by ingestion scripts |
| `ACTIVE_INDEX_VERSION` | `v1` | **Single source of truth for retrieval** |
| `WRITE_INDEX_VERSION` | `v1` | Version written by /ingest endpoint |

**To flip to v2 index:** Set `ACTIVE_INDEX_VERSION=v2` after validating reingest output, then restart.

---

## Observability

| Variable | Default | Description |
|----------|---------|-------------|
| `DEBUG_RAG` | `false` | Include chunk previews in /rag/chat — **NEVER in production** |
| `DEBUG_TRACE` | `false` | Write JSON trace files |
| `TRACE_DIR` | `./traces` | Trace output directory |
| `LOG_DIR` | `./logs` | Log output directory |
| `STRUCTURED_LOGGING_ENABLED` | `false` | Enable JSON log format |

---

## Sprint 2.11 New Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AUDIT_RETRY_ENABLED` | `true` | Enable retry on failed audit writes |
| `AUDIT_MAX_RETRIES` | `4` | Max audit write attempts |
| `AUDIT_OUTBOX_MAX_SIZE` | `1000` | Bounded in-memory outbox capacity |
| `STRUCTURED_LOGGING_ENABLED` | `false` | Structured JSON log output |

---

## Security Checklist (Production)

```
[ ] SUPABASE_KEY rotated (old key in git history — CRITICAL)
[ ] AUTH_ENABLED=true
[ ] ADMIN_API_KEYS set (strong, random keys)
[ ] FRESHDESK_WEBHOOK_ENFORCE_HMAC=true
[ ] FRESHDESK_WEBHOOK_SECRET set
[ ] DEBUG_RAG=false
[ ] FASTAPI_DOCS_ENABLED=false
[ ] AUDIT_BACKEND=supabase
[ ] S2_003 migration applied
[ ] ACTIVE_INDEX_VERSION matches ingested data
```
