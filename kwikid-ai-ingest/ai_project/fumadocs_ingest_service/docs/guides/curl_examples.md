# Curl Examples (Ingestion Service + Ollama + Chatbot)

Endpoints covered in this document:

- `GET /health` — liveness
- `GET /ready` — deep readiness probe (Supabase + embedding provider)
- `GET /freshdesk/filter-options` — statuses/priorities metadata for UI
- `POST /ingest` — multi-source ingestion (md, mdx, json, xlsx, freshdesk)
- `POST /query` — vector search with rerank + per-source thresholds + confidence gate
- `POST /chat` — ChatGPT-powered RAG chatbot (grounded on same `documents` corpus)

## Health check

```bash
curl -sS http://localhost:8000/health
```

## Deep readiness probe

```bash
curl -sS http://localhost:8000/ready
```

Response includes `{ "status": "ready" | "not_ready", "checks": { "supabase": {...}, "embeddings": {...} } }`.

## Freshdesk filter options (UI helper)

```bash
curl -sS http://localhost:8000/freshdesk/filter-options
```

## Trigger ingestion (re-index)

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false}'
```

## Trigger ingestion (Freshdesk enabled in .env)

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false, "freshdesk_updated_since":"2026-03-01T00:00:00Z"}'
```

## Trigger ingestion (all docs from repo)

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false}'
```

## Trigger ingestion (single file from repo)

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false, "file_path": "content/docs/cli/create-fumadocs-app.mdx"}'
```

## Trigger ingestion (single markdown by absolute path, including `.markdown`)

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false, "file_path": "/abs/path/to/guide.markdown"}'
```

## Trigger ingestion (single JSON file from `data/kwikid`)

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false, "file_path": "posts2votes.json"}'
```

## Trigger ingestion (single JSON by absolute path)

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false, "file_path": "/abs/path/to/posts.json"}'
```

## Trigger ingestion (single JSON file from nested path under `data/kwikid`)

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false, "file_path": "stack-overflow/posts.json"}'
```

## Trigger ingestion (single `.xlsx` under `EXCEL_DATA_PATH`)

**Setup:** Set `EXCEL_DATA_PATH` in `.env` (default `./data/excel`). Put the workbook under that directory. A request with `file_path` ending in `.xlsx` ingests **only** that file (markdown and JSON are not loaded for that call).

**`file_path`:** Use the path relative to `EXCEL_DATA_PATH`, with forward slashes, or the workbook file name if it sits in the root of that folder.

**Response:** Besides `files_scanned`, `chunks_*`, etc., the JSON may include an `excel` object:

- `file` — path of the workbook relative to `EXCEL_DATA_PATH`
- `sheet` — sheet name used
- `column_count` — number of header columns
- `data_row_count` — non-empty data rows ingested
- `skipped_empty_rows` — blank rows skipped
- `headers_normalized` — list of column labels after normalization
- `warnings` — e.g. missing file, unknown sheet, duplicate headers

### Example: workbook in `data/excel/` root

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false, "file_path": "Support VideoKYC(1-9).xlsx"}'
```

### Example: optional sheet name (defaults to active sheet)

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false, "file_path": "Support VideoKYC(1-9).xlsx", "excel_sheet": "Sheet1"}'
```

### Example: nested path under `EXCEL_DATA_PATH`

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false, "file_path": "exports/support/cases.xlsx"}'
```

### Example: absolute `.xlsx` path

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false, "file_path": "/abs/path/to/cases.xlsx"}'
```

## Dashboard upload note

When using the UI upload flow, files are written under the service data area (for example `data/ui_uploads/...`) and the returned absolute `file_path` can be sent directly to `/ingest`.

### Windows PowerShell (Excel ingest)

```powershell
curl.exe -sS -X POST "http://localhost:8000/ingest" `
  -H "Content-Type: application/json" `
  -d "{\"full_reindex\": false, \"file_path\": \"Support VideoKYC(1-9).xlsx\"}"
```

## Full re-index (upsert-only; no deletions)

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": true}'
```

`full_reindex=true` performs a full refresh via upsert and does not delete existing rows.

## Ollama: list models

```bash
curl -sS http://localhost:11434/api/tags
```

## Ollama: embedding probe (newer API)

```bash
curl -sS -X POST http://localhost:11434/api/embed \
  -H "Content-Type: application/json" \
  -d '{"model":"nomic-embed-text","input":["hello world"]}'
```

## Ollama: embedding probe (older route, fallback)

```bash
curl -sS -X POST http://localhost:11434/api/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model":"nomic-embed-text","prompt":"hello world"}'
```

## Query docs via vector search

```bash
curl -sS -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query_text": "How do I create a new Fuma docs app?",
    "match_count": 5,
    "match_threshold": 0.0,
    "source_thresholds": {
      "freshdesk": 0.30,
      "md": 0.20,
      "json": 0.20,
      "excel": 0.20
    }
  }'
```

## Query JSON-oriented content (StackOverflow-style)

```bash
curl -sS -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query_text": "What are the points associated with my profile?",
    "match_count": 5,
    "match_threshold": 0.0,
    "source_thresholds": {
      "json": 0.18,
      "freshdesk": 0.30
    },
    "source_types": ["json"]
  }'
```

## Query Freshdesk-oriented content

```bash
curl -sS -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query_text": "Show latest PlayStation ticket status and priority updates",
    "match_count": 5,
    "match_threshold": 0.0,
    "source_thresholds": {
      "freshdesk": 0.35
    },
    "source_types": ["freshdesk"]
  }'
```

## Query Excel / support-knowledge content (after ingesting `.xlsx`)

Use natural language that matches your sheet (e.g. VKYC, bank names, issue types). Tune `match_threshold` between `0.0` and `1.0` (e.g. `0.0` for broad recall, `0.2`+ for stricter matches) if you get no or weak hits. Use `source_thresholds` to make one source stricter (for example, Freshdesk) without reducing recall for other source types.

```bash
curl -sS -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query_text": "VKYC customer video black screen agent cannot see video",
    "match_count": 5,
    "match_threshold": 0.0,
    "source_thresholds": {
      "excel": 0.20,
      "freshdesk": 0.30
    },
    "source_types": ["excel", "freshdesk"]
  }'
```

## Chatbot (`POST /chat`)

RAG chatbot that reuses `/query` retrieval, grounds ChatGPT on the top-k chunks, and persists history in `public.chat_messages`.

**Prerequisites:**

1. Run `sql/chat_messages.sql` in Supabase (SQL Editor) — creates `public.chat_messages` + indexes + RLS.
2. Set `OPENAI_CHAT_API_KEY` in `.env` (or leave blank to reuse `OPENAI_API_KEY`).
3. Set `OPENAI_CHAT_MODEL=gpt-4o-mini` (any Chat Completions model works).
4. Restart uvicorn so the `/chat` route is registered.

### Stateless single-turn chat

```bash
curl -sS -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "query_text": "How do I create a new Fuma docs app?",
    "match_count": 5,
    "match_threshold": 0.0,
    "persist_history": false
  }'
```

### Multi-turn chat with session

```bash
# Turn 1 — response will include session_id and message_id
curl -sS -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{"query_text":"How do I resolve a VKYC black-screen ticket?","match_count":5}'

# Turn 2 — pass session_id from Turn 1 to thread context
curl -sS -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "query_text": "Give me the numbered steps only.",
    "session_id": "REPLACE_WITH_PREVIOUS_SESSION_ID",
    "history_turns": 6
  }'
```

### Scoped chat (Freshdesk tickets within a date window)

```bash
curl -sS -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "query_text": "Latest closed PlayStation support tickets in March",
    "source_types": ["freshdesk"],
    "source_thresholds": {"freshdesk": 0.35},
    "match_count": 8,
    "updated_at_from": "2026-03-01T00:00:00Z",
    "updated_at_to": "2026-03-31T23:59:59Z"
  }'
```

### Chat with tenant/access_scope metadata filters

```bash
curl -sS -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "query_text": "Summarize open SOP changes for L1 team",
    "tenant": "kwikid",
    "access_scope": "support",
    "source_types": ["md", "excel"],
    "match_count": 6
  }'
```

### Chat (Windows PowerShell)

```powershell
curl.exe -sS -X POST "http://localhost:8000/chat" `
  -H "Content-Type: application/json" `
  -d "{\"query_text\":\"How do I create a new Fuma docs app?\",\"match_count\":5,\"persist_history\":false}"
```

### Chat response shape

```json
{
  "session_id": "b7e5c4aa-...",
  "message_id": "d1f2f6ba-...",
  "answer": "1. Open the ticket...\n2. Verify...",
  "confidence": "high",
  "citations": [
    { "source_type": "freshdesk", "source_id": "12345", "title": "VKYC black screen", "chunk_index": 0 }
  ],
  "follow_up_question": null,
  "insufficient_context": false,
  "matches": [
    {
      "id": "....",
      "content": "...",
      "similarity": 0.62,
      "vector_score": 0.62,
      "rerank_score": 0.44,
      "final_rank": 1,
      "metadata": { "source_type": "freshdesk", "title": "...", "chunk_index": 0 }
    }
  ],
  "diagnostics": {
    "candidate_count": 20,
    "returned_count": 5,
    "best_similarity": 0.62,
    "best_rerank_score": 0.44,
    "applied_source_thresholds": { "freshdesk": 0.35, "__fallback__": 0.0 }
  }
}
```

Behavior notes:

- `confidence` is `high|medium|low`. If `insufficient_context=true`, the backend demotes `high` → `medium` so the LLM cannot claim certainty on weak evidence.
- `citations` reference specific chunks; the UI renders them as chips.
- `session_id` + `message_id` are UUID strings. Store `session_id` on the client to thread follow-ups.
- Errors: `400` for validation (`bad_request`), `502` for OpenAI upstream failure (`upstream_error`).
- Chat history write failures (e.g. missing `chat_messages` table) are logged but do **not** fail the chat response.

### Chat: sanity-check OpenAI credentials directly

```bash
curl -sS -X POST "https://api.openai.com/v1/chat/completions" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "response_format": {"type": "json_object"},
    "messages": [
      {"role": "system", "content": "Reply strictly as JSON {\"ok\": true}."},
      {"role": "user", "content": "ping"}
    ]
  }'
```

## Query response shape (source-aware)

`POST /query` now returns both a global ranked list (`matches`) and grouped results (`matches_by_source_type`).

```json
{
  "matches": [
    {
      "id": "....",
      "similarity": 0.62,
      "vector_score": 0.62,
      "rerank_score": 0.44,
      "final_rank": 1
    }
  ],
  "matches_by_source_type": {
    "md": [{ "id": "....", "similarity": 0.62 }],
    "json": [{ "id": "....", "similarity": 0.58 }],
    "freshdesk": [{ "id": "....", "similarity": 0.54 }]
  },
  "insufficient_context": false,
  "clarification": null,
  "diagnostics": {
    "applied_source_thresholds": {
      "freshdesk": 0.30,
      "md": 0.20,
      "json": 0.20,
      "excel": 0.20,
      "__fallback__": 0.0
    }
  }
}
```

## RAG coverage for n8n / support automation

Point the n8n **Supabase Query** node at this service (`INGEST_SERVICE_BASE_URL`, e.g. `http://localhost:8000` in dev). Ingest sources that match real tickets so `/query` returns useful chunks:

- `JSON_DATA_PATH` (e.g. `./data/kwikid`) — StackOverflow-style JSON graphs
- `EXCEL_DATA_PATH` (e.g. `./data/excel`) — support SOP workbooks (Video KYC, etc.)
- Docs repo + optional Freshdesk ingest (see main README)

Run `sql/support_conversation_state.sql` in Supabase before using the workflow **Upsert Support Session** node. n8n environment variables: `n8n/environment.example`. Keep the exported workflow JSON updated directly.
