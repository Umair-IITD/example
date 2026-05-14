# KwikID AI Ingest — Workflow Document

> **READ-ONLY analysis** — No code was modified.

---

## 1. Workflow Index

| # | Workflow | Trigger | Key Files |
|---|---|---|---|
| 1 | [Knowledge Ingestion](#2-knowledge-ingestion-workflow) | `POST /ingest` | `ingest.py`, all parsers, `chunker.py`, `embeddings.py`, `vector_store.py` |
| 2 | [RAG Query](#3-rag-query-workflow) | `POST /query` | `query.py`, `embeddings.py`, `vector_store.py` |
| 3 | [RAG Chat](#4-rag-chat-workflow) | `POST /chat` | `chat.py`, `query.py`, `embeddings.py`, `vector_store.py` |
| 4 | [Knowledge Card Training](#5-knowledge-card-training-workflow) | `POST /train/chat` + `POST /train/commit` | `train.py`, `chat.py`, `chunker.py`, `embeddings.py`, `vector_store.py` |
| 5 | [n8n Automated Support](#6-n8n-automated-support-workflow) | Freshdesk webhook / Telegram message | `tmp_workflow_node_scripts/*.js`, n8n JSON workflow |
| 6 | [Freshdesk Ticket Ingestion](#7-freshdesk-ticket-ingestion-workflow) | `POST /ingest` with Freshdesk params | `parser_freshdesk.py`, `ingest.py` |

---

## 2. Knowledge Ingestion Workflow

### Trigger
HTTP `POST /ingest` with body specifying sources to ingest.

### Request Parameters
```json
{
  "file_path": "optional — restrict to single file (md/json/excel)",
  "docs_glob": "optional — glob pattern for markdown files",
  "freshdesk_enabled": true,
  "freshdesk_updated_since": "2026-01-01T00:00:00Z",
  "freshdesk_ticket_ids": [12345, 67890],
  "freshdesk_filters": {
    "statuses": ["open", "pending"],
    "priorities": ["high", "urgent"],
    "ticket_types": ["Question"],
    "group_ids": [1234]
  },
  "sheet_name": "optional — Excel sheet name",
  "ingest_all_sheets": false
}
```

### Step-by-Step Execution

#### Step 1 — Settings Resolution
- [main.py](file:///c:/Users/HP/Desktop/Think360/kwikid-ai-ingest/ai_project/fumadocs_ingest_service/app/main.py) constructs `Settings` from environment
- Validates request model against `IngestRequest` Pydantic schema

#### Step 2 — Git Synchronization
- [ingest.py](file:///c:/Users/HP/Desktop/Think360/kwikid-ai-ingest/ai_project/fumadocs_ingest_service/app/ingest.py) calls [git_sync.py](file:///c:/Users/HP/Desktop/Think360/kwikid-ai-ingest/ai_project/fumadocs_ingest_service/app/git_sync.py) `sync_repo()`
- If repo doesn't exist locally: `git clone --branch <branch> --single-branch <url> <path>`
- If repo exists: `git fetch origin` → `git checkout <branch>` → `git pull --ff-only origin <branch>`
- Returns `(repo_path, commit_sha)`
- **Skip conditions**: Freshdesk-only ingestion, or absolute file paths (local uploads)
- **Timeout**: Configurable (default 120s per git command)

#### Step 3 — Source Parsing
Routes to parsers based on input:

**Markdown** ([parser_md.py](file:///c:/Users/HP/Desktop/Think360/kwikid-ai-ingest/ai_project/fumadocs_ingest_service/app/parser_md.py)):
1. Walk repo tree matching glob patterns (default `**/*.md`, `**/*.mdx`)
2. Read file → strip YAML frontmatter → normalize whitespace
3. Extract title from first `# ` heading
4. Extract heading from first `## ` heading
5. Emit `SourceDocument(source_type="md")`

**JSON** ([parser_json.py](file:///c:/Users/HP/Desktop/Think360/kwikid-ai-ingest/ai_project/fumadocs_ingest_service/app/parser_json.py)):
1. Load JSON files (supports ZIP extraction)
2. Try StackOverflow Teams posts.json format first:
   - Build Q&A graph (questions ↔ answers ↔ comments ↔ votes)
   - Cross-reference users
   - Select primary answer (accepted or highest score)
   - Include up to 2 related answers and 5 comments each
3. If not StackOverflow format: classify records as questions/answers/comments
4. Final fallback: generic JSON records (key-value linearization with priority ordering)
5. Emit `SourceDocument(source_type="json")`

**Excel** ([parser_excel.py](file:///c:/Users/HP/Desktop/Think360/kwikid-ai-ingest/ai_project/fumadocs_ingest_service/app/parser_excel.py)):
1. Locate file by path (absolute or relative to excel root)
2. Route by extension: `.xlsx` → openpyxl, `.xls` → pandas, `.csv` → csv reader
3. Normalize headers, handle duplicates
4. Per-row: extract semantic labels (id, bank, issue type, app type)
5. Build row content as `header: value` pairs
6. Emit `SourceDocument(source_type="excel")` per row

**Freshdesk** ([parser_freshdesk.py](file:///c:/Users/HP/Desktop/Think360/kwikid-ai-ingest/ai_project/fumadocs_ingest_service/app/parser_freshdesk.py)):
1. See [Freshdesk Ticket Ingestion Workflow](#7-freshdesk-ticket-ingestion-workflow)

#### Step 4 — Validation
Function `_validate_documents()` in [ingest.py](file:///c:/Users/HP/Desktop/Think360/kwikid-ai-ingest/ai_project/fumadocs_ingest_service/app/ingest.py):

```
For each SourceDocument:
├── Check content length ≥ min_document_chars (60)
│   └── If too short → quarantine("too_short")
├── Check charset (≥90% printable ASCII/common)
│   └── If garbled → quarantine("charset_sanity")
├── Check required metadata per source_type
│   └── If missing → quarantine("missing_metadata")
├── Compute content SHA-256 hash
│   └── If duplicate within current batch → quarantine("duplicate_content")
│   └── If duplicate across sources → quarantine("cross_source_duplicate")
└── Add tenant + access_scope metadata if configured
```

Quarantined documents are written to a JSON report file under `INGEST_REPORTS_PATH`.

#### Step 5 — Chunking
Function `chunk_document()` in [chunker.py](file:///c:/Users/HP/Desktop/Think360/kwikid-ai-ingest/ai_project/fumadocs_ingest_service/app/chunker.py):

```
Strategy selection (auto mode):
  md → heading_aware (split on # headings)
  json/excel/freshdesk → record_aware (split on double newlines)
  other → paragraph

For each paragraph/section:
├── Accumulate until target_words (350) reached
├── Flush when min_words (200) met AND next para would exceed target
├── Hard flush at max_words (500)
├── Split oversized chunks by word limits (max_words AND max_chars=6000)
├── Carry overlap_words (50) into next chunk for continuity
└── Merge orphan last chunk (<50 words) back into previous

Each Chunk gets:
  chunk_id = "{source_type}:{source_id}:{chunk_index}:{content_hash[:12]}"
  content_hash = SHA-256 of chunk text
```

#### Step 6 — Embedding
Using [embeddings.py](file:///c:/Users/HP/Desktop/Think360/kwikid-ai-ingest/ai_project/fumadocs_ingest_service/app/embeddings.py) `EmbeddingClient`:

```
Batch size: 64 chunks per API call

For each batch:
├── Trim text to 7000 chars max
├── If provider == "openai":
│   └── POST {base_url}/embeddings {model, input: texts}
├── If provider == "ollama":
│   ├── Try batch: POST /api/embed {model, input: texts}
│   ├── If 400 (batch not supported): embed one-by-one
│   ├── If 404 (/api/embed not found): fall back to /api/embeddings (legacy)
│   └── If context_length error: progressively halve text until it fits
└── Retry logic: 3 retries, exponential backoff + jitter, honor Retry-After
```

#### Step 7 — Upsert to Vector Store
Using [vector_store.py](file:///c:/Users/HP/Desktop/Think360/kwikid-ai-ingest/ai_project/fumadocs_ingest_service/app/vector_store.py) `VectorStore.upsert_chunks()`:

```
For each (chunk, vector) pair:
├── Compute deterministic ID: UUID5(NAMESPACE_URL, "{repo}:{source_type}:{source_id}:{chunk_index}")
├── Build payload: {id, content, embedding, metadata: {source_type, title, tags, ...}}
├── Check existing row by ID
│   ├── If exists AND content_hash matches → skip (no change)
│   ├── If exists AND content_hash differs → update
│   └── If new → create
├── Batch upsert (try all payloads at once)
│   └── On timeout/error → fall back to per-row upsert with 3 retries each
└── Return StoreResult(created, updated, skipped, errors)
```

**Retry logic for transient errors** (in `ingest.py`):
```
5 attempts total, exponential backoff:
  wait = min(90, 12 * 2^attempt) + jitter
Transient errors detected by: "timeout", "rate limit", "connection reset",
  "cloudflare", "522", "503", "504"
```

#### Step 8 — Response
```json
{
  "status": "completed",
  "repo": "kwikid-docs-internal",
  "commit_sha": "abc123...",
  "markdown_docs": 42,
  "json_docs": 150,
  "excel_docs": 500,
  "freshdesk_docs": 200,
  "total_chunks": 1847,
  "chunks_created": 1200,
  "chunks_updated": 47,
  "chunks_skipped": 600,
  "quarantined": 15,
  "errors": [],
  "report_path": "./data/reports/ingest_2026-05-12T06:00:00.json"
}
```

---

## 3. RAG Query Workflow

### Trigger
HTTP `POST /query`

### Request
```json
{
  "query_text": "How to fix video not available error?",
  "match_count": 5,
  "match_threshold": 0.2,
  "source_types": ["md", "json", "freshdesk"],
  "tenant": "kwikid",
  "access_scope": "support",
  "rerank_enabled": true,
  "rerank_candidate_multiplier": 4,
  "query_source_threshold_freshdesk": 0.30,
  "query_source_threshold_md": 0.20
}
```

### Step-by-Step Execution

#### Step 1 — Query Embedding
```
embed_texts([query_text]) → query_vector (1536-dim)
```

#### Step 2 — Vector Search
```
Call VectorStore.match_documents(
    query_embedding=query_vector,
    match_count=match_count * rerank_candidate_multiplier,  # 5 × 4 = 20
    match_threshold=confidence_min_similarity  # 0.2
)
```
- Primary: Supabase RPC `match_documents` (server-side cosine distance)
- Fallback: Client-side cosine similarity if RPC fails

#### Step 3 — Post-Filtering
```
For each candidate:
├── Apply source_type filter (if specified)
├── Apply tenant filter (metadata.tenant match)
├── Apply access_scope filter
├── Apply per-source-type similarity threshold
│   freshdesk: 0.30, md: 0.20, json: 0.20, excel: 0.20
└── Apply date range filter (if min_date/max_date specified)
```

#### Step 4 — Reranking
```
For each surviving candidate:
├── Compute lexical overlap: |query_tokens ∩ content_tokens| / |query_tokens|
├── Compute recency score (optional)
└── Sort by (rerank_score DESC, similarity DESC, recency DESC)
```

#### Step 5 — Balanced Selection
```
Group candidates by source_type
Sort groups by best score
Round-robin pick until match_count reached
→ Ensures diversity across md, json, freshdesk, excel
```

#### Step 6 — Insufficient Context Detection
```
If best_similarity < confidence_min_similarity (0.2):
    insufficient_context = true
    clarification = "The retrieved documents do not closely match your query..."
```

#### Step 7 — Response
```json
{
  "matches": [
    {
      "id": "uuid",
      "content": "chunk text...",
      "metadata": {"source_type": "freshdesk", "ticket_id": "12345", ...},
      "similarity": 0.87,
      "rerank_score": 0.72
    }
  ],
  "query_hash": "sha256...",
  "match_count": 5,
  "insufficient_context": false,
  "diagnostics": {"candidate_count": 20, "post_filter_count": 15, ...}
}
```

---

## 4. RAG Chat Workflow

### Trigger
HTTP `POST /chat`

### Request
```json
{
  "query_text": "Why is the user getting OTP failure?",
  "session_id": "uuid — optional, auto-generated if absent",
  "match_count": 5,
  "tenant": "kwikid",
  "history_turns": 6,
  "chat_context_chunk_max_chars": 1400
}
```

### Step-by-Step Execution

#### Step 1 — Retrieve Context
Calls `run_query()` (see [RAG Query Workflow](#3-rag-query-workflow) above) to get relevant chunks.

#### Step 2 — Build Context Block
```
For each match (up to match_count):
    [Source {i+1}]
    source_type: freshdesk
    title: OTP delivery failure for ICICI Bank
    similarity: 0.87
    ticket_url: https://kwikid.freshdesk.com/a/tickets/12345
    ---
    {truncated content, max 1400 chars per chunk}
```

#### Step 3 — Fetch Conversation History
```
ChatHistoryStore.fetch_recent_turns(session_id, limit=6)
→ Returns [{role: "user", content: "..."}, {role: "assistant", content: "..."}]
```

#### Step 4 — Construct LLM Messages
```
messages = [
    {role: "system", content: SYSTEM_PROMPT},  # Grounding rules, output format
    ...history_turns,                           # Previous conversation
    {role: "user", content: f"""
        Retrieved context chunks:
        {context_block}

        Diagnostics:
        {json_diagnostics}

        User question:
        {query_text}
    """}
]
```

**System Prompt Key Rules**:
- Evidence hierarchy: retrieved chunks > diagnostics > conversation history
- Must cite sources: `[Source 1]`, `[Source 3]`
- Cannot invent: product behavior, SLAs, policies, IDs, dates, URLs
- Output format: strict JSON `{answer, confidence, citations, follow_up_question}`
- Confidence values: "high" (chunks directly answer), "medium" (partial), "low" (weak match)

#### Step 5 — LLM Call
```
POST {chat_base_url}/chat/completions
{
    model: "gpt-4o-mini",
    messages: [...],
    temperature: 0.2,
    max_tokens: 800,
    response_format: {type: "json_object"}
}
```

#### Step 6 — Response Parsing
```python
raw_json = parse LLM response
answer = raw_json["answer"]
confidence = raw_json["confidence"]  # "high" | "medium" | "low"
citations = raw_json["citations"]    # ["Source 1", "Source 3"]
follow_up = raw_json["follow_up_question"]  # or null
```

#### Step 7 — History Persistence
```
ChatHistoryStore.append(session_id, "user", query_text, message_id=uuid)
ChatHistoryStore.append(session_id, "assistant", answer, message_id=uuid,
    metadata={confidence, citations, follow_up, diagnostics})
```

#### Step 8 — Response
```json
{
  "session_id": "uuid",
  "message_id": "uuid",
  "answer": "Based on the support ticket history...",
  "confidence": "high",
  "citations": ["Source 1", "Source 3"],
  "follow_up_question": null,
  "diagnostics": {
    "query_hash": "sha256...",
    "context_chunks_used": 5,
    "insufficient_context": false,
    "chat_model": "gpt-4o-mini"
  }
}
```

---

## 5. Knowledge Card Training Workflow

### Phase 1 — Conversational Drafting

#### Trigger
HTTP `POST /train/chat`

#### Request
```json
{
  "query_text": "I want to teach you about OTP retry limits",
  "session_id": "uuid — optional",
  "previous_draft": null,
  "tenant": "kwikid",
  "access_scope": "support"
}
```

#### Execution
1. Construct user prompt with previous draft JSON + new user message
2. Call LLM with `TRAIN_SYSTEM_PROMPT` (knowledge-capture rules)
3. LLM returns structured JSON:
   ```json
   {
     "assistant_reply": "Great, let me capture that...",
     "draft": {
       "title": "OTP Retry Limits",
       "summary": "Defines retry limits for OTP delivery...",
       "content": "OTP delivery is limited to 5 retries per...",
       "tags": ["otp", "authentication", "limits"],
       "tenant": "kwikid",
       "access_scope": "support",
       "suggested_questions": ["What is the OTP retry limit?", ...]
     },
     "follow_up_question": "What happens after the limit is reached?",
     "confidence": "medium"
   }
   ```
4. Merge new draft with prior draft (non-null fields overwrite)
5. Apply confidence guardrail: downgrade "high" to "medium" if title/tenant/scope/tags missing or content < 40 words
6. Persist user + assistant messages to chat history
7. Return `TrainChatResult`

### Phase 2 — Commit

#### Trigger
HTTP `POST /train/commit`

#### Request
```json
{
  "session_id": "uuid",
  "draft": {
    "title": "OTP Retry Limits",
    "content": "OTP delivery is limited to 5 retries per session...",
    "tags": ["otp", "authentication"],
    "tenant": "kwikid",
    "access_scope": "support",
    "suggested_questions": ["What is the OTP retry limit?"]
  }
}
```

#### Execution
1. Validate: title required, content ≥ 40 words
2. Generate `card_id` (UUID4)
3. Create `SourceDocument(source_type="manual", source_id=card_id)`
4. Chunk document (paragraph strategy)
5. Enrich chunk 0 (head) with:
   - `card_summary`, `suggested_questions`, `manual_card_full_content`, `manual_chunk_count`
6. Embed all chunks
7. Upsert to `documents` table with `repo="manual:chat_train"`
8. On failure: delete any partially written chunks by `card_id`
9. Return `CommitResult` with chunk IDs

---

## 6. n8n Automated Support Workflow

### Trigger
Freshdesk webhook (ticket created/updated) or Telegram message

### End-to-End Pipeline

#### Node 1 — Normalize Input
Extracts from webhook payload:
- `ticketId`, `ticketSubject`, `ticketBody`, `source` (freshdesk/telegram)
- `ccEmails`, `replyVisibility` (public/private)

#### Node 2 — Fetch Freshdesk Conversations
```
GET {FRESHDESK_BASE_URL}/api/v2/tickets/{ticketId}/conversations
Authorization: Basic {FRESHDESK_BASIC_AUTH}
```

#### Node 3 — Build Ticket + Conversation Text
[Build_TicketplusConversation_Text.js](file:///c:/Users/HP/Desktop/Think360/kwikid-ai-ingest/ai_project/fumadocs_ingest_service/tmp_workflow_node_scripts/Build_TicketplusConversation_Text.js):
```
analysisText = "Subject: {subject}\n\nTicket body: {body}\n\nConversation thread:\n{conversations}"
```

#### Node 4 — Parse Issue (LLM)
LLM analyzes `analysisText` to extract structured signals:
- `exactIssue` — one-line issue description
- `issueCategory` — auth, video_not_available, network, other
- `searchQuery` — optimized RAG search query
- `issueSummary` — brief summary
- `structuredSignals` — { sessionId, phoneNumber, pan, appVersion, platform }
- `missingFromTicket` — list of missing critical fields

#### Node 5 — RAG Query
```
POST {INGEST_SERVICE_BASE_URL}/query
{
    query_text: searchQuery,
    match_count: RAG_MATCH_COUNT (8),
    match_threshold: RAG_MATCH_THRESHOLD (0.2)
}
```

#### Node 6 — Build Supabase Context
[Build_Supabase_Context.js](file:///c:/Users/HP/Desktop/Think360/kwikid-ai-ingest/ai_project/fumadocs_ingest_service/tmp_workflow_node_scripts/Build_Supabase_Context.js):

**Sophisticated reranking**:
```javascript
rerankScore = (effectiveScore * 0.65) + (overlapScore * 0.25) + (phraseScore * 0.10)
// where effectiveScore = similarity * source_type_weight
```

**Confidence gating**:
```javascript
categoryThreshold = {
    auth: 0.32,
    video_not_available: 0.30,
    network: 0.28,
    other: 0.24
}
needsClarification = insufficient_context || missingSignals || !hasActionableEvidence
```

**Missing signal detection**:
- `video_not_available` → requires `sessionId`
- `auth` → requires `phoneNumber` or `pan`
- `network` → requires `appVersion` or `platform`

#### Node 7 — Prepare First Response Prompt
[Prepare_First_Response_Prompt.js](file:///c:/Users/HP/Desktop/Think360/kwikid-ai-ingest/ai_project/fumadocs_ingest_service/tmp_workflow_node_scripts/Prepare_First_Response_Prompt.js):

Builds LLM prompt with:
- Ticket text + RAG context + missing hints
- Output format: acknowledgement → diagnosis → steps → needed details → closing
- Constraints: no backend steps to customer, no subject line repetition

#### Node 8 — First Response (LLM)
Calls OpenAI to generate draft customer response.

#### Node 9 — Collect Final Response
[Collect_Final_Response.js](file:///c:/Users/HP/Desktop/Think360/kwikid-ai-ingest/ai_project/fumadocs_ingest_service/tmp_workflow_node_scripts/Collect_Final_Response.js):
Extracts LLM output text.

#### Node 10 — Freshdesk Public Reply
Formats and posts response via Freshdesk API:
```
POST {FRESHDESK_BASE_URL}/api/v2/tickets/{ticketId}/reply
{
    body: "<div><p>{formatted_response}</p></div>",
    cc_emails: [...] // if public reply
}
```

**Response formatting safeguards**:
- Fallback template if LLM output is empty
- Enforce mandatory sections: greeting, acknowledgement, reasons, fixes, next action, signature
- Clean noisy LLM prefixes (Subject:, Ticket Subject:)
- Hard limit: 6000 chars
- Mandatory signature: "Thanks & Regards\nkwikid AI support"

---

## 7. Freshdesk Ticket Ingestion Workflow

### Two Modes

#### Mode A — By Ticket IDs
When `freshdesk_ticket_ids` is provided:
```
For each ticket_id:
├── GET /api/v2/tickets/{id} (ticket details)
├── Wait 3.5s (rate limiting)
├── GET /api/v2/tickets/{id}/conversations (all pages)
├── Apply filters if apply_filters_with_ticket_ids=true
├── Build SourceDocument from ticket + conversations
└── Emit to ingestion pipeline
```

#### Mode B — Paginated Listing
When no ticket_ids:
```
For page in 1..max_pages (100):
├── GET /api/v2/tickets?page={page}&per_page=100&updated_since={date}
├── Apply client-side filters:
│   ├── updated_until (date range)
│   ├── ticket_types (Question, Problem, etc.)
│   ├── requester_ids / responder_ids / group_ids
│   ├── statuses (open, pending, resolved, closed)
│   └── priorities (low, medium, high, urgent)
├── For each passing ticket:
│   ├── GET /api/v2/tickets/{id} (full details)
│   ├── GET /api/v2/tickets/{id}/conversations
│   └── Build SourceDocument
├── Wait 3.5s between requests
└── Stop when page returns fewer than page_size results
```

### Rate Limiting & Retry
```
Freshdesk returns 429 → wait at least 25s
Any retryable error (408, 429, 500, 502, 503, 504):
├── Honor Retry-After header (delta-seconds or HTTP-date)
├── Exponential backoff: base_delay * 2^attempt
├── Add jitter: random(0, min(1.0, base_delay))
└── Max 4 retries
```

### Ticket Document Construction
```
Subject: {subject}
Ticket Link: https://{domain}/a/tickets/{id}

Description: {HTML-to-plain-text body}
  └── Handles: <script>/<style> removal, block break preservation,
      HTML entity unescaping, whitespace normalization

Structured description (JSON): {if dict/string present}

Image and media URLs:
  - Extracted from: <img src>, <img srcset>, markdown ![](url), <a href> images
  - Excludes data: URIs

Conversation and notes:
  Conversation 1 | id=123 | incoming | public_reply | channel=email | created_at=...
  {body text}
  Attachments:
  - {url}

Ticket fields (JSON): {full ticket payload}
Ticket conversations and notes (JSON): {full conversations payload}

Tags: tag1, tag2
Status: open
Priority: high
Type: Question
```

### Metadata Attached
```json
{
  "source_type": "freshdesk",
  "ticket_id": "12345",
  "ticket_url": "https://kwikid.freshdesk.com/a/tickets/12345",
  "status": "open",
  "priority": "high",
  "requester_id": 1234,
  "responder_id": 5678,
  "group_id": 42,
  "tags": ["otp", "auth"],
  "created_at": "2026-01-15T10:00:00Z",
  "updated_at": "2026-05-10T14:30:00Z",
  "type": "Question",
  "image_urls": ["https://..."],
  "conversation_count": 5,
  "has_private_notes": true,
  "ingest_run_ts": "2026-05-12T06:00:00Z"
}
```
