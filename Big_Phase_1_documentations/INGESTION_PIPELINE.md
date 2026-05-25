# Ingestion Pipeline

## Overview

The ingestion pipeline processes multiple document sources, chunks them, embeds them, and upserts them into Supabase. It is designed for resilience: individual chunk failures do not abort the run, and duplicate content is detected via content hashing.

## Source Types

| Source | Trigger | Format | Table |
|--------|---------|--------|-------|
| Fumadocs (Git repo) | `/ingest` API call | Markdown/MDX/JSON | `documents` |
| JSON data | `/ingest` API call | Structured JSON | `documents` |
| Excel data | `/ingest` API call | .xlsx/.xls | `documents` |
| Freshdesk tickets | `/ingest` with `freshdesk_enabled=true` | API | `documents` |
| SOP files | `sop_pipeline.py` | Markdown | `rag_sop_chunks` |
| Knowledge articles | `knowledge_pipeline.py` | SO Teams JSON | `rag_knowledge_chunks` |

## Ingestion Flow (B1 Pipeline)

```
Source Documents
      │
      ├──▶ Parser (source-specific)
      │    ├── parser_md.py — Markdown/MDX
      │    ├── parser_json.py — JSON
      │    ├── parser_excel.py — Excel
      │    ├── parser_freshdesk.py — Freshdesk API
      │    └── stackoverflow_parser.py — SO Teams JSON export
      │
      ├──▶ Delta Tracker (delta_tracker.py)
      │    • Content hash comparison against last-known state
      │    • Only changed/new documents re-embedded (cost control)
      │
      ├──▶ Deduplication (deduplication.py)
      │    • SHA-256 hash of normalized content
      │    • Skips chunks already in database with identical hash
      │
      ├──▶ Chunker (chunker_v2.py / ticket_chunker.py)
      │    • Token-aware chunking: CHUNK_TARGET_TOKENS=1200
      │    • Hard cap: EMBEDDING_MAX_INPUT_TOKENS=7000
      │    • Overlap: CHUNK_OVERLAP_TOKENS=150
      │    • Fallback: word-based chunking if tiktoken unavailable
      │
      ├──▶ Embedding (batch_processor.py)
      │    • OpenAI text-embedding-3-small
      │    • Batch size: B1_EMBEDDING_BATCH_SIZE=64
      │    • Circuit breaker: pauses after B1_EMBEDDING_CB_THRESHOLD failures
      │    • Exponential backoff with ±30% jitter
      │    • Max retries: B1_EMBEDDING_MAX_RETRIES=6
      │
      ├──▶ Upsert (vector_store.py)
      │    • Batch upserts to Supabase (B1_UPSERT_BATCH_SIZE=50)
      │    • index_version tag: B1_INDEX_VERSION (default: v2)
      │    • ON CONFLICT: update existing records
      │
      └──▶ Ingestion Report
           • Run ID, duration, doc/chunk/embedding counts
           • Written to INGEST_REPORTS_PATH (./data/reports/)
           • JSON + Markdown summary formats
```

## Token-Aware Chunking

The chunker in `app/chunker_v2.py` is the production implementation:

1. **Token counting**: Uses `tiktoken` (OpenAI's tokenizer) with graceful fallback to word-based counting if `tiktoken` is unavailable.

2. **Strategy**: 
   - `auto`: Tries heading-aware, falls back to paragraph-based
   - `heading_aware`: Splits at H1/H2/H3 markdown headings first
   - `paragraph_based`: Splits at blank lines
   - `record_aware`: Treats each record (JSON object, Excel row) as an atomic unit

3. **Overlap**: Adjacent chunks share `CHUNK_OVERLAP_TOKENS` tokens to prevent context loss at split boundaries.

4. **Orphan handling**: Trailing chunks shorter than `CHUNK_MIN_ORPHAN_WORDS` are merged into the preceding chunk rather than embedded as fragments.

## Three-Part Ticket Chunking

Historical Freshdesk tickets use a specialized chunker (`rag_engine/chunking/ticket_chunker.py`):

```
Ticket
├── chunk_type=ISSUE_HEADER
│   Fields: subject, category, priority, product, created_at, tags
│   Purpose: Match "what kind of issue" queries
│
├── chunk_type=QUERY_BODY
│   Fields: Customer messages, agent questions (first N exchanges)
│   Min chars: B1_MIN_QUERY_BODY_CHARS=80
│   Purpose: Match "how the issue was described" queries
│
└── chunk_type=RESOLUTION_RCA
    Fields: Resolution summary, root cause, agent notes, final status
    Purpose: Match "how was it fixed" and "why did it happen" queries
```

## Embedding Resilience (Circuit Breaker)

The batch processor (`rag_engine/embedding/batch_processor.py`) implements a circuit breaker:

```
State: CLOSED (normal) → OPEN (failing) → HALF_OPEN (testing)

CLOSED: Process batches normally
    │ (B1_EMBEDDING_CB_THRESHOLD consecutive failures)
    ▼
OPEN: Reject all embedding requests, raise EmbeddingCircuitOpenError
    │ (after B1_EMBEDDING_CB_COOLDOWN_S seconds)
    ▼
HALF_OPEN: Attempt one test batch
    │ Success → CLOSED
    └ Failure → back to OPEN
```

Exponential backoff formula:
```python
delay = min(
    base_delay * (2 ** attempt) * (0.7 + random() * 0.6),  # ±30% jitter
    max_delay
)
```

## Knowledge Article Ingestion (Phase B3)

Knowledge articles from Stack Overflow Teams exports are ingested via `rag_engine/ingestion/knowledge_pipeline.py`:

1. **Parse**: `stackoverflow_parser.py` — reads `posts.json`, `comments.json`, `tags.json`
2. **Classify**: `knowledge_classifier.py` — computes `quality_score` based on:
   - Answer acceptance status
   - Vote count
   - Comment quality
   - Content length and structure
3. **Gate**: Articles below `B3_KNOWLEDGE_MIN_QUALITY_SCORE = 0.55` are excluded
4. **Chunk**: Standard token-aware chunking
5. **Upsert**: `rag_knowledge_articles` + `rag_knowledge_chunks` tables

## SOP Ingestion

SOPs are markdown files in `data/sop/`. Ingested via `rag_engine/ingestion/sop_pipeline.py`:
- Stored in `rag_sop_chunks` table with `sop_id` metadata
- The SQL RPC applies the +0.15 boost at query time (not stored as a column)
- `sop_builder.py` constructs structured SOP documents with section headers preserved

## Index Versioning

The `index_version` field on every chunk enables live index migration without downtime:

```
B1_INDEX_VERSION=v2       ← Written during ingestion
ACTIVE_INDEX_VERSION=v2   ← Read during retrieval
WRITE_INDEX_VERSION=v2    ← Written by /ingest endpoint
```

Migration workflow:
1. Ingest with `B1_INDEX_VERSION=v2` (new chunks tagged v2)
2. Validate v2 chunks quality
3. Set `ACTIVE_INDEX_VERSION=v2`, restart service
4. v1 chunks remain in DB but are not retrieved
5. Clean up v1 chunks when confident in v2

## Deduplication

Content deduplication uses SHA-256 hash of normalized content:
```python
content_hash = sha256(
    content.strip().lower().encode("utf-8")
).hexdigest()
```

On upsert, chunks with matching `(source_id, chunk_index, content_hash)` are skipped. This prevents re-embedding identical chunks on delta ingestion runs.

## Ingestion Reports

Each run produces:
- `data/reports/ingestion_{timestamp}_{run_id}.json` — machine-readable metrics
- `data/reports/preprocessing_report.md` — human-readable summary

These are gitignored (runtime artifacts). Key metrics tracked:
- `total_source_rows`, `documents_processed`, `documents_skipped`, `documents_failed`
- `chunks_created`, `chunks_skipped` (duplicates), `chunks_failed`
- `embeddings_generated`, `embedding_api_calls`, `embedding_tokens_used`
- `automation_label_counts`, `client_counts`
