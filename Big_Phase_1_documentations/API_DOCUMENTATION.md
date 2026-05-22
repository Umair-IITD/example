# API Documentation

## Authentication

All endpoints except `/health`, `/ready`, and `/freshdesk/webhook` require an `X-API-Key` header.

```
X-API-Key: <your-api-key>
```

Keys are configured via `RAG_API_KEY` or `RAG_API_KEYS` (comma-separated for rotation) in `.env`.

Key validation uses constant-time comparison (`hmac.compare_digest`) to prevent timing attacks.

---

## Primary Endpoint: POST /rag/chat

The main production endpoint. Full RAG pipeline: retrieval → context assembly → SOP parsing → LLM generation → governance.

### Request

```json
{
  "query_text": "Customer cannot unlock their KwikID account after OTP verification failed",
  "client": "kwikid",
  "session_id": "sess_abc123",
  "top_k": 5,
  "similarity_threshold": 0.20,
  "persist_history": true,
  "history_turns": 6
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `query_text` | string | Yes | — | The support query or issue description |
| `client` | string | Yes | — | Tenant/client identifier for isolation |
| `session_id` | string | No | auto-generated | Session ID for history persistence |
| `top_k` | int | No | `B1_DEFAULT_TOP_K` (10) | Max chunks to retrieve |
| `similarity_threshold` | float | No | `B1_DEFAULT_SIMILARITY_THRESHOLD` (0.27) | Minimum similarity score |
| `persist_history` | bool | No | false | Whether to save this turn to chat_messages |
| `history_turns` | int | No | `CHAT_HISTORY_TURNS` (6) | How many prior turns to include |

### Response

```json
{
  "session_id": "sess_abc123",
  "message_id": "msg_789xyz",
  "answer": "To resolve an account lockout after OTP failure, follow these steps...",
  "confidence": "high",
  "confidence_score": 0.847,
  "requires_human": false,
  "citations": [
    {
      "sop_id": "account_lockout_resolution",
      "title": "Account Lockout Resolution SOP",
      "score": 0.742
    }
  ],
  "follow_up_question": "Has the customer verified their registered mobile number is correct?",
  "insufficient_context": false,
  "chunks": [
    {
      "id": "chunk_abc",
      "content": "...",
      "chunk_type": "sop",
      "source_type": "sop",
      "similarity": 0.592,
      "boosted_score": 0.742,
      "rerank_score": 0.681
    }
  ],
  "diagnostics": {
    "workflow_match_type": "exact_match",
    "retrieval_strategy": "hybrid",
    "sop_branch_flags": {
      "has_escalation_branches": true,
      "has_denial_branches": true,
      "has_security_freeze": false,
      "has_post_resolution": true,
      "has_mandatory_warnings": false
    },
    "query_route": "SOP",
    "routing_confidence": 0.92,
    "retrieval_strategy": "SOP_FIRST"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `answer` | string | Generated response grounded in retrieved knowledge |
| `confidence` | string | `high` / `medium` / `low` / `none` |
| `confidence_score` | float | Numerical confidence (0.0–1.0) |
| `requires_human` | bool | `true` if escalation is required |
| `citations` | array | Source documents used to generate the answer |
| `follow_up_question` | string | Suggested clarifying question (may be empty) |
| `insufficient_context` | bool | `true` if no relevant context was retrieved |
| `chunks` | array | Raw retrieved chunks (for debugging; redacted in production if `DEBUG_RAG=false`) |
| `diagnostics` | object | Classification, routing, and retrieval metadata |

### Rate Limiting

- Limit: `RAG_CHAT_RATE_LIMIT` requests per 60 seconds per IP (default: 20)
- Response on limit: `HTTP 429` with `{"error": "rate_limited"}`

### Error Responses

| Status | Condition |
|--------|-----------|
| 400 | Invalid request (`client` missing, malformed JSON) |
| 401 | Missing or invalid `X-API-Key` |
| 429 | Rate limit exceeded |
| 502 | Upstream error (Supabase or OpenAI unavailable) |

---

## POST /freshdesk/webhook

Inbound Freshdesk webhook handler. Receives ticket events, performs RAG lookup, and optionally posts a reply.

**Authentication**: HMAC-SHA256 signature validation (not API key). Enabled when `FRESHDESK_WEBHOOK_ENABLED=true`.

### Request (Freshdesk ticket payload)

```json
{
  "freshdesk_webhook": {
    "ticket_id": 12345,
    "ticket_subject": "Account lockout issue",
    "ticket_description": "Customer unable to login after KYC verification",
    "ticket_tags": ["client:kwikid", "priority:high"]
  }
}
```

### Response

```json
{
  "status": "processed",
  "ticket_id": 12345,
  "confidence": "high",
  "requires_human": false,
  "reply_posted": true
}
```

---

## POST /ingest

Triggers document ingestion from configured sources.

**Authentication**: `X-API-Key` required.

### Request

```json
{
  "repo_url": "git@bitbucket.org:team360noscope/kwikid-docs-internal.git",
  "ref": "main",
  "full_reindex": false,
  "file_path": "docs/account-management/lockout.md"
}
```

All fields are optional; defaults from `.env` are used when omitted.

---

## POST /feedback

Ingest thumbs up/down feedback for a response.

**Authentication**: `X-API-Key` required.

### Request

```json
{
  "session_id": "sess_abc123",
  "message_id": "msg_789xyz",
  "rating": "thumbs_up",
  "comment": "Accurate and followed SOP correctly"
}
```

---

## GET /health

No authentication required. Always returns `200 OK` if the service process is alive.

```json
{"status": "ok"}
```

---

## GET /ready

No authentication required. Checks Supabase connectivity and embedding provider availability.

```json
{
  "status": "ready",
  "checks": {
    "supabase": {"ok": true},
    "embeddings": {"ok": true}
  }
}
```

**Security note**: Errors are returned as `"error": "dependency_check_failed"` — no internal details are exposed to callers.

---

## GET /metrics

Returns Prometheus text-format metrics. Requires `PROMETHEUS_ENABLED=true`.

**Authentication**: `X-API-Key` required.

```
# HELP http_requests_total Total HTTP requests
# TYPE http_requests_total counter
http_requests_total{method="POST",path="/rag/chat",status="200"} 1247
```

---

## Legacy Endpoints

These endpoints are maintained for backward compatibility. New integrations should use `/rag/chat`.

### POST /query

Retrieval-only — returns raw chunks without LLM generation.

```json
{
  "query_text": "account lockout",
  "match_count": 5,
  "match_threshold": 0.20,
  "source_thresholds": {"freshdesk": 0.30, "md": 0.20},
  "source_types": ["md", "sop"],
  "tenant": "kwikid"
}
```

### POST /chat

Legacy RAG chat using the old retrieval stack. Replaced by `/rag/chat`.

---

## n8n Integration

The n8n workflow (`n8n/kwikid_support_workflow.json`) calls `/rag/chat`. Required n8n environment variables:

```
INGEST_SERVICE_BASE_URL=https://your-service-url
RAG_API_KEY=<your-api-key>          # Must match service RAG_API_KEY
RAG_MATCH_COUNT=5
RAG_MATCH_THRESHOLD=0.20
SOP_MIN_SIMILARITY=0.20
RAG_DEFAULT_CLIENT=kwikid
```

The workflow sends `X-API-Key: {{ $env.RAG_API_KEY }}` with every request to `/rag/chat`.
