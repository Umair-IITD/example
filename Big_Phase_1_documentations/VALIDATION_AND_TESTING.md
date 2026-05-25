# Validation and Testing

## Overview

The project uses a multi-layer offline validation strategy. All validation suites run without live API calls (no Supabase, no OpenAI), making them suitable for CI.

## Validation Suites

### 1. Response Governance (`validate_response_governance.py`) — 48/48

Tests the correctness of the governance engine, SOP parser, and threshold logic.

**Test groups:**

| Group | Count | Tests |
|-------|-------|-------|
| Threshold sanity | 6 | WORKFLOW_EXACT_SIMILARITY > WORKFLOW_RELATED_SIMILARITY, valid ranges |
| Scenario governance | 14 | exact_match → no requires_human; no_match → requires_human; knowledge-only → related_match; etc. |
| Branch completeness | 14 | Downgrade logic; absent branch counting; confidence transitions |
| SOP parser | 14 | P01–P14: empty input, each flag pattern, regex edge cases, cross-flag isolation |

Run: `python scripts/validate_response_governance.py`

### 2. Token Chunking (`validate_b1_tokens.py`) — 19/19

Tests token-aware chunking correctness.

**Tests include:**
- Empty input → no chunks
- Single-sentence input → single chunk
- Token count accuracy (within ±5% of tiktoken)
- Overlap preservation across adjacent chunks
- Hard cap enforcement (EMBEDDING_MAX_INPUT_TOKENS = 7000)
- Idempotency (same input → same chunks)
- Strategy variants: paragraph, heading_aware, auto
- Orphan handling
- Multi-language content

Run: `python scripts/validate_b1_tokens.py`

### 3. Generation Pipeline (`validate_b2_generation.py`) — 41/41

Tests the generation pipeline without live LLM calls (uses mock LLM).

**Tests include:**
- ContextAssembler: SOP/knowledge/general chunk ordering
- SOP header annotation with branch flags
- Truncation at CHAT_CONTEXT_CHUNK_MAX_CHARS
- Prompt construction: RESPONSE MODE per match type
- Branch mandate injection per SopDocumentFlag
- Confidence scoring: score → tier transitions
- Automation gate: all 4 conditions required
- History formatting: alternating role messages
- Sanitization: diagnostic field removal from answer
- Mock LLM failure → graceful error response
- Citation extraction from chunks

Run: `python scripts/validate_b2_generation.py`

### 4. Knowledge Pipeline (`validate_b3_knowledge.py`) — 46/46

Tests B3 knowledge article classification and pipeline.

**Tests include:**
- Quality score computation: vote count, acceptance, length
- Quality gate: score < 0.55 → excluded
- Chunk quality bonus capping
- Multi-tenant article routing
- SO Teams JSON parsing: articles, comments, tags
- Deduplication: same content → skip
- Pipeline integration: parse → classify → chunk → (mock) upsert

Run: `python scripts/validate_b3_knowledge.py`

### 5. Security (`validate_b2_5_security.py`) — 18/20 offline, 20/20 with correct .env

Tests API security without live requests (except S19/S20 which require a running service).

**Tests include:**
- S01: Missing API key → 401
- S02: Valid key → 200
- S03: Wrong key → 401
- S04: Multiple valid keys (rotation)
- S05: Rate limit enforcement
- S06: /health → 200 (no auth)
- S07: /ready → 200 (no auth)
- S08: Webhook HMAC: valid signature → 200
- S09: Webhook HMAC: invalid signature → 403
- S10: Webhook HMAC: missing secret with ENFORCE_HMAC=true → startup failure
- S11–S18: Constant-time comparison, key hint redaction, audit logging
- S19: /docs → 404 (docs disabled) — **requires local .env FASTAPI_DOCS_ENABLED=false**
- S20: /openapi.json → 404 — **requires local .env FASTAPI_DOCS_ENABLED=false**

S19/S20 fail locally because `FASTAPI_DOCS_ENABLED=true` in the developer's local `.env`. They pass in production where the default is `false`.

Run: `python scripts/validate_b2_5_security.py`

### 6. Infrastructure (`validate_b1_infrastructure.py`)

Tests infrastructure components without live connections:
- Config loading and validation
- Env var parsing (boolean, int, float)
- Settings validation: range checks, required fields
- Chunker initialization
- Parser initialization

Run: `python scripts/validate_b1_infrastructure.py`

## Unit Tests (`tests/`)

The `tests/` directory contains pytest-based unit tests. They require environment variables for live Supabase + OpenAI connections.

Current test files:
- `test_ingest_context_improvements.py` — Ingest context handling
- `test_b1_embedding_resilience.py` — Circuit breaker, retry logic
- `test_b1_token_chunking.py` — Token chunking edge cases

Run: `pytest tests/ -q --tb=short`

**Status**: Non-blocking in CI until secrets are wired (marked with `|| true`).

## Compile Check

Quick syntax validation across all Python modules:

```bash
python -m compileall -q app/ rag_engine/ retrieval/ dataset_pipeline/ scripts/ observability/ query_router/
```

Exit code 0 = no syntax errors. Runs in ~2 seconds.

## FastAPI Import Check

Validates that the FastAPI application initializes without errors (env-dependent startup errors caught):

```bash
python -c "from app.main import app; print('FastAPI app import: OK')"
```

## Docker Compose Validation

```bash
docker compose config --quiet
```

Validates the docker-compose.yml structure without starting containers.

## Live Validation Scripts (Require Supabase + OpenAI)

These require a configured `.env` with live credentials:

| Script | Purpose |
|--------|---------|
| `scripts/validate_b1_db_integrity.py` | Database integrity: index counts, orphaned chunks, embedding dimensions |
| `scripts/validate_b1_retrieval.py` | Retrieval quality: precision, recall, latency on gold dataset |
| `scripts/evaluate_rag.py` | End-to-end RAG evaluation against expected outputs |
| `scripts/evaluate_retrieval.py` | Retrieval-only evaluation with metrics |

## Adding Tests

To add a new governance test:
1. Open `scripts/validate_response_governance.py`
2. Add a test function in the appropriate section
3. Update the test count in `main()`

To add a new unit test:
1. Create `tests/test_<feature>.py`
2. Use `pytest` fixtures from `tests/conftest.py` (if it exists)
3. Mark live-dependency tests with `@pytest.mark.live`

## Test Philosophy

- **Offline by default**: All CI tests must run without live API calls
- **Deterministic**: Same input = same output, no flakiness
- **Fast**: Each suite completes in < 5 seconds
- **Self-documenting**: Test names encode what they verify (P01 = SOP parser test 1)
- **No mocking of internals**: Mock only external services (LLM, DB) not internal logic
