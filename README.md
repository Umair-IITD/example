# Fuma Docs + StackOverflow Ingestion Service

This service builds a unified semantic knowledge base from:

- Markdown/MDX documentation from a synced repository
- StackOverflow-style JSON datasets under `data/kwikid`
- Excel workbooks (`.xlsx`) under `EXCEL_DATA_PATH` (default `./data/excel`)
- Freshdesk tickets from `/api/v2/tickets`

It parses source files, chunks content (200-500 words), generates embeddings (OpenAI or Ollama), stores vectors in Supabase `public.documents`, and serves a **ChatGPT-powered RAG chatbot** via `POST /chat` that is grounded on the same corpus.

## What This Supports

- Multi-source ingestion (`.md`, `.mdx`, `.markdown`, `.json`, `.xlsx`)
- Excel row ingestion: one semantic document per data row, all columns serialized into `content`, `source_type=excel`, optional `excel_sheet` on `/ingest`
- Freshdesk API ingestion (`source_type=freshdesk`)
- Smart JSON graph parsing for dependent files in `data/kwikid`
- Unified chunking for markdown, JSON-, Excel-, and Freshdesk-derived content
- Strategy-aware chunking (`auto`, `paragraph`, `heading_aware`, `record_aware`) with max-char and orphan-chunk guardrails
- Embedding provider switch via `.env`
- Upsert into one table: `documents`
- Semantic query API over mixed sources with metadata filtering, reranking, and safe no-answer gating
- **Chatbot API** (`POST /chat`) that reuses the same retrieval pipeline, sends a source-tagged context block to OpenAI Chat Completions (strict JSON output), and persists conversation turns to `public.chat_messages`

## Project Structure

- `app/main.py` - FastAPI endpoints (`/health`, `/ready`, `/freshdesk/filter-options`, `/ingest`, `/query`, `/chat`)
- `app/ingest.py` - End-to-end ingest pipeline orchestration
- `app/parser_md.py` - Markdown parser
- `app/parser_json.py` - StackOverflow JSON parser with cross-file enrichment
- `app/parser_freshdesk.py` - Freshdesk ticket parser
- `app/parser_excel.py` - Excel (`.xlsx`) row parser
- `app/chunker.py` - Unified chunking logic
- `app/embeddings.py` / `app/embedder.py` - Embedding client
- `app/vector_store.py` / `app/uploader.py` - Supabase persistence and retrieval
- `app/query.py` - Query embedding + vector search + rerank + confidence gate
- `app/chat.py` - ChatGPT client, context builder, strict-JSON prompt, and `chat_messages` history store
- `curl_examples.md` - Curl quick-reference (includes `/chat` examples)
- `sql/match_documents_documents.sql` - `match_documents` function aligned to `documents`
- `sql/chat_messages.sql` - `public.chat_messages` table migration for chatbot history

## Required SOPs

### SOP 1: One-Time Setup

```bash
cd /path/to/fumadocs_ingest_service
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

For Windows PowerShell:

```powershell
cd C:\path\to\fumadocs_ingest_service
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

### SOP 2: Environment Configuration

Mandatory:

- `SUPABASE_URL`
- `SUPABASE_KEY`
- `TABLE_NAME=documents`
- `JSON_DATA_PATH=./data/kwikid`
- `EXCEL_DATA_PATH=./data/excel` (root searched for `.xlsx` when `file_path` ends with `.xlsx`)
- `WRITE_INDEX_VERSION=v1` and `ACTIVE_INDEX_VERSION=v1` (blue/green style index switching)

Freshdesk controls:

```env
FRESHDESK_ENABLED=false
FRESHDESK_DOMAIN=your-subdomain.freshdesk.com
FRESHDESK_API_KEY=
FRESHDESK_UPDATED_SINCE=
FRESHDESK_PAGE_SIZE=100
FRESHDESK_MAX_PAGES=100
FRESHDESK_MAX_RETRIES=4
FRESHDESK_RETRY_BASE_DELAY_S=1.0
```

- Set `FRESHDESK_ENABLED=true` only when both domain and API key are configured.
- `FRESHDESK_UPDATED_SINCE` is optional and supports incremental sync windows.

Chatbot controls (for `POST /chat`):

```env
# Leave blank to reuse OPENAI_API_KEY for chat; or set an explicit key here.
OPENAI_CHAT_API_KEY=
OPENAI_CHAT_MODEL=gpt-4o-mini
OPENAI_CHAT_BASE_URL=https://api.openai.com/v1
CHAT_TIMEOUT_S=60
CHAT_MAX_RETRIES=3
CHAT_RETRY_BASE_DELAY_S=0.8
CHAT_TEMPERATURE=0.2
CHAT_MAX_OUTPUT_TOKENS=800
CHAT_CONTEXT_CHUNK_MAX_CHARS=1400
CHAT_HISTORY_TURNS=6
CHAT_HISTORY_TABLE=chat_messages
```

- `OPENAI_CHAT_API_KEY` resolution order: `OPENAI_CHAT_API_KEY` → `OPENAI_API_KEY` → empty (fails fast at validation).
- `CHAT_CONTEXT_CHUNK_MAX_CHARS` trims each retrieved chunk before it is shown to the LLM (keeps token spend predictable).
- `CHAT_HISTORY_TURNS` caps how many recent `user`/`assistant` messages from the same session are replayed into the prompt.
- `CHAT_HISTORY_TABLE` must match the Supabase table created by `sql/chat_messages.sql`.

Robustness controls:

```env
CHUNK_STRATEGY=auto
CHUNK_MAX_CHARS=6000
CHUNK_MIN_ORPHAN_WORDS=50
MIN_DOCUMENT_CHARS=60
INGEST_REPORTS_PATH=./data/reports
PARSER_VERSION=v1
METADATA_TENANT=
METADATA_ACCESS_SCOPE=
WRITE_INDEX_VERSION=v1
ACTIVE_INDEX_VERSION=v1
RERANK_ENABLED=true
RERANK_CANDIDATE_MULTIPLIER=4
CONFIDENCE_MIN_SIMILARITY=0.2
CONFIDENCE_MIN_RERANK=0.05
QUERY_SOURCE_THRESHOLD_FRESHDESK=0.30
QUERY_SOURCE_THRESHOLD_MD=0.20
QUERY_SOURCE_THRESHOLD_JSON=0.20
QUERY_SOURCE_THRESHOLD_EXCEL=0.20
```

OpenAI embedding config:

```env
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-...
```

Ollama embedding config:

```env
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=nomic-embed-text
OLLAMA_BASE_URL=http://localhost:11434
EMBEDDING_API_KEY=
```

### SOP 3: Supabase Function Alignment (Important)

If query fails with `relation "public.kb_chunks" does not exist`, execute:

```sql
drop function if exists public.match_documents(public.vector, integer, double precision);
drop function if exists public.match_documents(public.vector, integer);

create or replace function public.match_documents(
    query_embedding public.vector(1536),
    match_count integer default 5,
    match_threshold double precision default 0.0
)
returns table (
    id uuid,
    content text,
    metadata jsonb,
    similarity double precision
)
language sql
stable
as $$
    select
        d.id,
        d.content,
        d.metadata,
        1 - (d.embedding <=> query_embedding) as similarity
    from public.documents as d
    where 1 - (d.embedding <=> query_embedding) >= match_threshold
    order by d.embedding <=> query_embedding
    limit match_count;
$$;
```

Same SQL is available in `sql/match_documents_documents.sql`.

### SOP 4: Run the Service

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### SOP 5: Ingest Data

`file_path` now supports either:

- a source-relative path (existing behavior), or
- an absolute file path on disk (used by dashboard uploads).

- Ingest all markdown + JSON:

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false}'
```

- Ingest one markdown file:

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false, "file_path": "content/docs/cli/create-fumadocs-app.mdx"}'
```

- Ingest one uploaded markdown file by absolute path:

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false, "file_path": "/abs/path/to/file.md"}'
```

- Ingest one JSON file from `data/kwikid`:

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false, "file_path": "posts.json"}'
```

- Ingest one uploaded JSON file by absolute path:

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false, "file_path": "/abs/path/to/file.json"}'
```

- Full reindex (upsert-only; no deletions):

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": true}'
```

This mode refreshes vectors through deterministic upserts and never deletes existing rows.
Ingestion now validates documents before chunking; invalid/low-quality rows are quarantined to `INGEST_REPORTS_PATH/quarantine_<run_id>.json`, and response includes accepted/quarantined/duplicate counters.

### SOP 5A: Excel (`.xlsx`) Ingest

Place workbooks under `EXCEL_DATA_PATH` (default `./data/excel`). When `file_path` ends with `.xlsx`, **only** that workbook is ingested (markdown and JSON are skipped for that request), similar to passing a single `.json` file. Absolute `.xlsx` file paths are also supported.

- **Rows**: Each non-empty data row becomes one source document. Row 1 is treated as the header row; headers are normalized (whitespace, stray non-breaking spaces).
- **Content**: Every column appears in the embedded text as `ColumnName:\nvalue`, separated by blank lines so the shared chunker can split long rows sensibly.
- **Metadata**: Includes `excel_file`, `sheet`, `excel_row`, `column_count`, and `spreadsheet_id` when an `ID` column exists.
- **API response**: The JSON body may include an `excel` object with `file`, `sheet`, `column_count`, `data_row_count`, `skipped_empty_rows`, `headers_normalized`, and `warnings` so you can verify the parse without reading server logs.
- **Optional body field**: `excel_sheet` — sheet name; omit to use the workbook’s active sheet.

Examples (Linux/macOS):

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false, "file_path": "Support VideoKYC(1-9).xlsx"}'
```

Nested path under `EXCEL_DATA_PATH` (use forward slashes):

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false, "file_path": "exports/support/cases.xlsx", "excel_sheet": "Sheet1"}'
```

CLI equivalent:

```bash
python -m app.ingest --file-path "Support VideoKYC(1-9).xlsx"
```

More examples: [`curl_examples.md`](curl_examples.md).

### SOP 5B: Freshdesk Ingest

- Enable Freshdesk in `.env`:

```env
FRESHDESK_ENABLED=true
FRESHDESK_DOMAIN=your-subdomain.freshdesk.com
FRESHDESK_API_KEY=your_api_key
```

- Freshdesk ingest (md + json + freshdesk):

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{
    "full_reindex": false,
    "freshdesk_updated_since": "2026-03-01T00:00:00Z"
  }'
```

- Incremental Freshdesk sync (example):

```env
FRESHDESK_UPDATED_SINCE=2026-03-01T00:00:00Z
```

Then run the same `/ingest` endpoint; ticket metadata will include `freshdesk_sync_cursor` and `ingest_run_ts`.

### SOP 5C: Freshdesk Runtime Filters (via API body)

Use `/ingest` body fields to control date range and ticket segmentation without hardcoding:

- `freshdesk_updated_since` (API lower bound)
- `freshdesk_updated_until` (local upper bound filter)
- `freshdesk_ticket_types` (example: `["Issues"]`)
- `freshdesk_requester_ids` (client/requester IDs)
- `freshdesk_responder_ids` (agent IDs)
- `freshdesk_group_ids`
- `freshdesk_statuses` (example: `["closed"]`)
- `freshdesk_priorities` (example: `["high","urgent"]`)

Example:

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{
    "full_reindex": false,
    "freshdesk_updated_since": "2026-03-01T00:00:00Z",
    "freshdesk_updated_until": "2026-03-27T23:59:59Z",
    "freshdesk_ticket_types": ["Issues"],
    "freshdesk_statuses": ["closed"]
  }'
```

### SOP 6: Query Data

Linux/macOS shell:

```bash
curl -sS -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query_text": "What are the points associated with my profile?",
    "match_count": 5,
    "match_threshold": 0.0,
    "source_thresholds": {
      "freshdesk": 0.30,
      "md": 0.20,
      "json": 0.20,
      "excel": 0.20
    },
    "source_types": ["md", "json"],
    "tenant": null,
    "access_scope": null,
    "updated_at_from": null,
    "updated_at_to": null
  }'
```

Windows PowerShell:

```powershell
curl.exe -sS -X POST "http://localhost:8000/query" `
  -H "Content-Type: application/json" `
  -d "{\"query_text\":\"What are the points associated with my profile?\",\"match_count\":5,\"match_threshold\":0.0,\"source_thresholds\":{\"freshdesk\":0.30,\"md\":0.20,\"json\":0.20,\"excel\":0.20},\"source_types\":[\"md\",\"json\"]}"
```

Query response now includes:

- `matches`: source-balanced ranked chunks with `vector_score`, `rerank_score`, `final_rank`
- `matches_by_source_type`: grouped ranked chunks per source type (`freshdesk`, `md`, `json`, `excel`)
- `insufficient_context`: confidence gate output
- `clarification`: fallback suggestion when confidence is low
- `diagnostics`: retrieval/ranking telemetry

### SOP 7: Chatbot (`POST /chat`)

End-to-end RAG chatbot that layers ChatGPT on top of `/query`. Used by the dashboard `/chat` page.

**1. Create the history table (one-time).** Paste this into Supabase Dashboard → SQL Editor → Run:

```sql
create table if not exists public.chat_messages (
    id uuid primary key default gen_random_uuid(),
    session_id uuid not null,
    role text not null check (role in ('user', 'assistant', 'system')),
    content text not null,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists chat_messages_session_created_at_idx
    on public.chat_messages (session_id, created_at asc);
create index if not exists chat_messages_created_at_idx
    on public.chat_messages (created_at desc);
create index if not exists chat_messages_metadata_idx
    on public.chat_messages using gin (metadata);

alter table public.chat_messages enable row level security;

notify pgrst, 'reload schema';
```

The same SQL is in `sql/chat_messages.sql`. The final `notify pgrst` line forces PostgREST to refresh its schema cache so the `PGRST205 "Could not find the table 'public.chat_messages'"` warning clears immediately without a backend restart.

**2. Configure OpenAI chat env (see "Chatbot controls" above).** The backend reuses `OPENAI_API_KEY` when `OPENAI_CHAT_API_KEY` is blank, so the default setup needs zero extra secrets.

**3. Restart the FastAPI service** so the `/chat` route is registered:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**Request body:**

```json
{
  "query_text": "How do I resolve VKYC black-screen issues?",
  "session_id": null,
  "match_count": 5,
  "match_threshold": 0.0,
  "source_thresholds": { "freshdesk": 0.30, "md": 0.20, "json": 0.20, "excel": 0.20 },
  "source_types": ["freshdesk", "excel"],
  "tenant": null,
  "access_scope": null,
  "updated_at_from": null,
  "updated_at_to": null,
  "history_turns": 6,
  "persist_history": true
}
```

Field semantics:

- `session_id` — UUID string. If omitted/null, the backend creates one and returns it in the response. Reuse it for follow-ups so history is threaded.
- `history_turns` — recent `user`+`assistant` turns from the same session to include (default `CHAT_HISTORY_TURNS=6`). Pass `0` for stateless Q&A.
- `persist_history` — set `false` to skip writing to `public.chat_messages` (use for health checks / ephemeral sessions).
- All other retrieval fields behave identically to `POST /query`.

**Response body:**

```json
{
  "session_id": "b7e5...",
  "message_id": "d1f2...",
  "answer": "Final answer grounded in context chunks.",
  "confidence": "high",
  "citations": [
    { "source_type": "freshdesk", "source_id": "12345", "title": "VKYC black screen", "chunk_index": 0 }
  ],
  "follow_up_question": null,
  "insufficient_context": false,
  "matches": [ /* top-k chunks with similarity + rerank_score */ ],
  "diagnostics": { /* candidate_count, best_similarity, applied_source_thresholds, ... */ }
}
```

**Prompt contract (implemented in `app/chat.py`):**

```text
You are KwikID Support AI. Answer ONLY using the provided context chunks.
Rules:
1) If context is insufficient or conflicting, say you are not fully sure and ask a targeted clarification question.
2) Do not invent product behavior, policies, IDs, dates, or links.
3) Prefer recent and source-specific evidence; when possible mention source_type and title.
4) Keep response concise and actionable for support agents.
5) If steps are requested, return numbered steps.
6) If multiple sources disagree, explicitly state the conflict.
Output STRICT JSON only: answer, confidence (high|medium|low), citations[], follow_up_question.
```

Behavior guarantees:

- Returns `insufficient_context=true` when the retrieval confidence gate trips, and the backend **demotes `high` → `medium`** so the LLM cannot claim certainty on weak evidence.
- Chat errors return `HTTP 400` (bad request), `HTTP 502` (OpenAI upstream error), and never cause the retrieval pipeline to fail silently.
- Conversation history errors (e.g. missing `chat_messages` table) are logged but **do not fail the chat response**.

### SOP 8: Teach the AI (`POST /train/*` and `source_type=manual`)

The dashboard `/train` page lets a subject-matter expert author reusable knowledge through conversation. The assistant captures what the user says into a structured "knowledge card", asks one focused follow-up each turn, and proposes 3-6 end-user questions the content would answer. Clicking **Commit to knowledge base** chunks the body, embeds it, and upserts into `public.documents` with `source_type="manual"`. Card-level fields (summary, suggested questions, session id, chunk count, etc.) are stored on each chunk’s `metadata`; the row with `metadata.chunk_index == 0` is the canonical **card head** for `GET /train/cards` and `GET /chat/suggestions`.

**1. Optional legacy table.** Older deployments may have run `sql/knowledge_cards.sql`. The FastAPI service **no longer reads or writes** `public.knowledge_cards`; you can leave the table empty or drop it after migrating any reporting off it.

**2. Optional threshold tuning.** Per-source similarity thresholds for the `/query` pipeline gain a `manual` default (0.20). Override with:

```env
QUERY_SOURCE_THRESHOLD_MANUAL=0.20
```

**3. New endpoints (all served by the same FastAPI app):**

- `POST /train/chat` — conversational knowledge capture.
- `POST /train/commit` — embed + upsert the reviewed draft into `documents` only (stable `card_id` in chunk metadata).
- `GET /train/cards` — list committed cards from document **heads** (filter by `tenant`, `access_scope`, `status`, `session_id`).
- `GET /train/cards/{id}` — fetch one card (metadata from its head chunk).
- `DELETE /train/cards/{id}` — delete all `documents` rows whose `metadata.card_id` matches (returns `status: "missing"` when nothing matched).
- `GET /chat/suggestions` — starter-question chips (from `metadata.suggested_questions` on train heads; falls back to `documents.metadata.title` when fewer than `limit`).

**Request body for `/train/chat`:**

```json
{
  "query_text": "Refunds requested within 7 days waive the processing fee.",
  "session_id": null,
  "draft": {
    "title": null,
    "summary": null,
    "content": "",
    "tags": [],
    "tenant": null,
    "access_scope": null,
    "suggested_questions": []
  },
  "tenant": "kwikid",
  "access_scope": "support",
  "history_turns": 6,
  "persist_history": true
}
```

- `draft` is cumulative: send back the merged draft you received from the previous turn so the LLM can iterate on it.
- Assistant always returns strict JSON; the server enforces `confidence=high` only when `title`, `tenant`, `access_scope`, `tags`, and a content body of at least 40 words are all populated.
- Turns are persisted to `public.chat_messages` with `metadata.mode="training"` (re-uses the existing chat history store).

**Response:**

```json
{
  "session_id": "d1b6...",
  "message_id": "a93e...",
  "assistant_reply": "Captured the refund rule. I still need the tenant.",
  "draft": { "title": "7-day refund fee waiver", "summary": "...", "content": "...", "tags": ["billing"], "tenant": null, "access_scope": null, "suggested_questions": ["..."] },
  "follow_up_question": "Which tenant does this policy apply to?",
  "confidence": "medium",
  "diagnostics": { "word_count": 52, "has_title": true, "has_tenant": false, "tag_count": 1, "suggested_question_count": 4 }
}
```

**Commit:**

```bash
curl -sS -X POST "http://localhost:8000/train/commit" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "REPLACE_WITH_SESSION_ID",
    "draft": {
      "title": "7-day refund fee waiver",
      "summary": "Waive processing fee on refunds requested within 7 days.",
      "content": "...full canonical knowledge body (>= 40 words)...",
      "tags": ["billing", "refunds"],
      "tenant": "kwikid",
      "access_scope": "support",
      "suggested_questions": ["What is the refund window?", "Does the 7-day rule apply to failed transactions?"]
    }
  }'
```

Response includes `card_id`, `chunk_ids`, and standard `chunks_created / chunks_updated / chunks_skipped` counters.

**Retrieve manual content via `/query` or `/chat`:**

```bash
curl -sS -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -d '{"query_text":"What is the refund window?","source_types":["manual"],"match_count":5}'
```

`source_type="manual"` behaves like any other source: it can be included or excluded via `source_types`, and per-source similarity thresholds are honored via `source_thresholds.manual`.

**Suggestions:**

```bash
curl -sS "http://localhost:8000/chat/suggestions?limit=6&tenant=kwikid"
```

Returns `{"suggestions": ["...", "..."]}`. The `/chat` empty state in the dashboard renders these as clickable chips that auto-submit.

**Delete / undo a committed card:**

```bash
curl -sS -X DELETE "http://localhost:8000/train/cards/REPLACE_WITH_CARD_ID"
```

Removes every `public.documents` row whose `metadata.card_id` matches and marks the card `status="archived"`. Its `suggested_questions` immediately disappear from `/chat/suggestions`.

## All Curl Commands (Quick Reference)

### Health

```bash
curl -sS "http://localhost:8000/health"
```

### Ingest (all)

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false}'
```

### Ingest (Freshdesk enabled in env)

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false, "freshdesk_updated_since":"2026-03-01T00:00:00Z"}'
```

### Ingest (Freshdesk filtered by date + type + status)

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex":false,"freshdesk_updated_since":"2026-03-01T00:00:00Z","freshdesk_updated_until":"2026-03-27T23:59:59Z","freshdesk_ticket_types":["Issues"],"freshdesk_statuses":["closed"]}'
```

### Ingest (single markdown)

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false, "file_path": "content/docs/product/async-services/celery-worker/index.md"}'
```

### Ingest (single JSON)

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false, "file_path": "posts.json"}'
```

### Ingest (single Excel under `EXCEL_DATA_PATH`)

```bash
curl -sS -X POST "http://localhost:8000/ingest" \
  -H "Content-Type: application/json" \
  -d '{"full_reindex": false, "file_path": "Support VideoKYC(1-9).xlsx"}'
```

### Query (JSON-focused)

```bash
curl -sS -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -d '{"query_text":"What are the points associated with my profile?","match_count":5,"match_threshold":0.0,"source_thresholds":{"json":0.18,"freshdesk":0.30},"source_types":["json"]}'
```

### Query (Markdown-focused)

```bash
curl -sS -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -d '{"query_text":"How do I configure celery worker async services?","match_count":5,"match_threshold":0.0,"source_thresholds":{"md":0.18,"freshdesk":0.30},"source_types":["md"]}'
```

### Query (mixed)

```bash
curl -sS -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -d '{"query_text":"How can I track user comments and answers in docs and Q&A?","match_count":8,"match_threshold":0.0,"source_thresholds":{"freshdesk":0.30,"md":0.20,"json":0.20,"excel":0.20}}'
```

### Query (Freshdesk-focused)

```bash
curl -sS -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -d '{"query_text":"What is the latest status and priority for PlayStation support tickets?","match_count":5,"match_threshold":0.0,"source_thresholds":{"freshdesk":0.35},"source_types":["freshdesk"]}'
```

### Query (Excel / support-knowledge focused)

```bash
curl -sS -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -d '{"query_text":"VKYC customer video black screen resolution steps","match_count":5,"match_threshold":0.0,"source_thresholds":{"excel":0.20,"freshdesk":0.30},"source_types":["excel"]}'
```

### Chat (stateless, single-turn)

```bash
curl -sS -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "query_text": "What are the points associated with my profile?",
    "match_count": 5,
    "match_threshold": 0.0,
    "persist_history": false
  }'
```

### Chat (multi-turn with session)

First call creates the session; the response includes `session_id` — reuse it for follow-ups.

```bash
# Turn 1
curl -sS -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{"query_text":"How do I resolve a VKYC black-screen ticket?","match_count":5}'

# Turn 2 (use session_id from turn 1 response)
curl -sS -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "query_text": "Give me the numbered steps only.",
    "session_id": "REPLACE_WITH_PREVIOUS_SESSION_ID",
    "history_turns": 6
  }'
```

### Chat (scoped to Freshdesk with tight thresholds)

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

### Chat (Windows PowerShell)

```powershell
curl.exe -sS -X POST "http://localhost:8000/chat" `
  -H "Content-Type: application/json" `
  -d "{\"query_text\":\"What are the points associated with my profile?\",\"match_count\":5,\"persist_history\":false}"
```

### OpenAI direct embedding API

```bash
curl -sS -X POST "https://api.openai.com/v1/embeddings" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"text-embedding-3-small","input":"What are the points associated with my profile?"}'
```

### OpenAI direct chat completion (used by `/chat`)

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

### Ollama health/model check

```bash
curl -sS "http://localhost:11434/api/tags"
```

## Smart JSON Parsing Notes (`data/kwikid`)

The parser treats kwikid JSON as a connected dataset:

- `posts.json`: base question/answer graph
- `comments.json`: attached to posts/answers by `postId`
- `posts2votes.json`: vote summaries by `postId`
- `users.json`: owner enrichment by `ownerUserId`

For each question, it builds a semantic document from:

- Question title/body
- Canonical post link (`post_link`) for direct source navigation (fallback format: `https://stackoverflowteams.com/c/kwikid/questions/{post_id}`)
- Accepted or best answer
- Additional related answers
- Question comments + answer comments
- Vote summary

This improves retrieval quality over per-file isolated parsing.

## Verification Checklist

- `GET /health` returns `{"status":"ok"}`
- `POST /ingest` returns no fatal errors
- Supabase `documents` row count increases/updates
- JSON rows contain metadata like `source_type`, `post_id`, `post_link`, `answer_count`
- Markdown rows contain metadata like `source_type`, `file_path`, `heading`
- Freshdesk rows contain metadata like `source_type=freshdesk`, `ticket_id`, `ticket_url`, `ticket_link`, `post_link`, `status`, `priority`, `image_urls`, `image_url_count`, `conversation_count`, `has_private_notes`; ingest resolves each ticket via `/api/v2/tickets/{id}` and `/api/v2/tickets/{id}/conversations`, and chunk text includes `Ticket Link: ...`, full ticket description, conversations/replies, private notes, ticket fields JSON, and a dedicated **Image and media URLs** section for retrieval
- Excel-derived rows contain metadata like `source_type=excel`, `source_id`, `excel_file`, `sheet`, `excel_row`, `column_count`, and optionally `spreadsheet_id`
- `POST /query` returns top-k matches from ingested sources (markdown, JSON, Excel, Freshdesk)
- `POST /query` returns confidence-gated output and diagnostics; low-confidence requests return `insufficient_context=true`
- `POST /query` accepts optional `source_thresholds` for per-source similarity filtering and returns `matches_by_source_type`
- `POST /chat` returns `{ session_id, message_id, answer, confidence, citations[], follow_up_question, insufficient_context, matches, diagnostics }`
- `POST /chat` persists `user` + `assistant` turns to `public.chat_messages` when `persist_history=true`
- `POST /chat` never returns `confidence="high"` when the retrieval gate reports `insufficient_context=true` (auto-demoted to `medium`)

## Troubleshooting SOP

- **500 on `/query` with `kb_chunks` error**
  - Apply `sql/match_documents_documents.sql`
- **`/query` returns empty matches**
  - Use `match_threshold: 0.0` during testing
  - Lower `QUERY_SOURCE_THRESHOLD_*` or request `source_thresholds` when one source is over-filtered
  - Ensure ingestion has populated rows in `documents`
- **OpenAI errors**
  - Verify `OPENAI_API_KEY`, model name, and outbound network
- **Freshdesk auth errors (401/403)**
  - Verify `FRESHDESK_DOMAIN` and `FRESHDESK_API_KEY`
  - Confirm API key belongs to an active Freshdesk agent account
- **Freshdesk rate limit (429)**
  - Reduce `FRESHDESK_PAGE_SIZE`
  - Increase `FRESHDESK_RETRY_BASE_DELAY_S` or lower run frequency
- **PowerShell curl parsing issues**
  - Use `curl.exe` with escaped JSON as shown above
- **Excel ingest: `excel.warnings` or zero `data_row_count`**
  - Confirm `file_path` is either valid relative to `EXCEL_DATA_PATH` or a valid absolute `.xlsx` path (see SOP 5A)
  - Install deps: `pip install -r requirements.txt` (requires `openpyxl`)
- **Markdown/JSON absolute-path ingest returns zero files**
  - Verify backend was restarted after parser changes
  - Ensure `file_path` points to an existing readable file with supported extension
- **`/chat` returns 404**
  - Backend was started before `/chat` was added. Restart uvicorn so the new route is loaded.
- **`/chat` returns 502 `upstream_error`**
  - OpenAI Chat Completions request failed. Check `OPENAI_CHAT_API_KEY` (or `OPENAI_API_KEY` fallback), `OPENAI_CHAT_MODEL`, outbound network, and quota/billing. Inspect backend logs for the HTTP status from OpenAI.
- **`/chat` logs `PGRST205 "Could not find the table 'public.chat_messages'"`**
  - Run `sql/chat_messages.sql` in the Supabase SQL editor (SOP 7). The chat answer still succeeds — only history persistence is affected.
- **`/chat` returns but answer is empty or `confidence=low`**
  - Retrieval did not find strong context. Lower `match_threshold`, remove `source_types` filter, or broaden `source_thresholds`.
- **`/chat` ignores earlier turns**
  - Pass the `session_id` returned from the first call on subsequent calls, or increase `history_turns`.

## Scheduled Ingestion (Optional)

Linux cron every 6 hours:

```cron
0 */6 * * * cd /path/to/fumadocs_ingest_service && /path/to/fumadocs_ingest_service/.venv/bin/python -m app.ingest >> /var/log/fumadocs_ingest.log 2>&1
```

## Tools For QA

- List Freshdesk contact (requester) IDs by email domain (primary email only):
  - `python scripts/list_freshdesk_contacts_by_email_domain.py --domain-suffix unitybank.co.in`
  - JSON IDs only (for `freshdesk_requester_ids`): add `--ids-only`
- Create a Freshdesk AI test ticket assigned to L2 + an agent, with random RAG context in description:
  - `python scripts/create_freshdesk_test_ticket.py --group-name "L2" --agent-name "Dnyaneshwar Shekade" --requester-email "unity.test@unitybank.co.in"`
  - Optional: force RAG seed query with `--rag-query "Unity pending status link issue"`
- Ticket-field discovery and filter dry-runs:
  - `python scripts/freshdesk_ticket_fields_probe.py --updated-since "2026-03-01T00:00:00Z" --updated-until "2026-03-27T23:59:59Z" --status closed --max-pages 5`
- Postman end-to-end collection:
  - `postman/fumadocs_ingest_service_e2e.postman_collection.json`
- Retrieval evaluation harness:
  - Copy `scripts/rag_eval_dataset.example.json` to your own dataset and fill expected source IDs
  - Run `python scripts/evaluate_rag.py --dataset scripts/rag_eval_dataset.example.json --k 5 --min-precision 0.2 --min-recall 0.4`
