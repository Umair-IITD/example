# SOP Ingestion Implementation

**Date**: 2026-05-18  
**Status**: Implemented — ready for live ingestion  
**Files created**:
- `rag_engine/ingestion/sop_pipeline.py`
- `scripts/ingest_sop.py`
- `data/sop/otp_delivery_failure_resolution.md` (global SOP)
- `data/sop/video_kyc_session_failure.md` (unity_bank SOP)
- `data/sop/account_lockout_resolution.md` (global SOP)

---

## 1. Architecture Overview

SOPs integrate with the existing B1 retrieval system without any schema changes. The database tables (`rag_sop_library`, `rag_sop_chunks`) and the `match_all_b1_sources` RPC were designed for SOP retrieval from the start (B1_002 and B1_005 migrations).

```
data/sop/*.md
      │
      ▼
SopDocumentBuilder.build_from_markdown()
      │  (frontmatter: title, query_type, clients)
      │  (sections: split on H2/H3 headings)
      ▼
SopIngestionPipeline._process_sop()
      │
      ├─ _fetch_existing_sop()          # dedup by content_hash
      │     unchanged → skip
      │     changed   → bump version
      │
      ├─ rag_sop_library.upsert()       # library record
      ├─ _delete_sop_chunks()           # on update: remove old sections
      ├─ _build_chunk_records()         # one chunk per H2/H3 section
      ├─ embed_batch()                  # OpenAI text-embedding-3-small
      └─ rag_sop_chunks.upsert()        # embedded chunks
```

At retrieval time, `match_all_b1_sources` already includes SOPs:
```sql
-- SOP chunks boosted +0.15 similarity
SELECT sc.id, 'rag_sop_chunks' AS source_table, ...
       LEAST(1.0, similarity + 0.15) AS boosted_score
FROM rag_sop_chunks sc
JOIN rag_sop_library sl ON sl.sop_id = sc.sop_id
WHERE sl.is_active = TRUE AND ...
```

No code change was needed in `TicketRetriever` for SOP retrieval — the RPC already handles it.

---

## 2. SOP Document Format

SOP files are Markdown with optional YAML frontmatter:

```markdown
---
title: "OTP Delivery Failure - Resolution Procedure"
query_type: "OTP_ISSUE"
issue_area: "Authentication"
clients: ""           # empty = global; "unity_bank" = tenant-specific
---

## Step 1: Triage

...content...

## Step 2: Verify Mobile Number

...content...
```

**Frontmatter fields:**

| Field | Required | Example | Notes |
|---|---|---|---|
| `title` | No (defaults from filename) | `"OTP Resolution"` | Human-readable SOP name |
| `query_type` | No | `"OTP_ISSUE"` | Maps to Freshdesk taxonomy |
| `issue_area` | No | `"Authentication"` | Sub-category |
| `clients` | No | `"unity_bank"` | Comma-separated; empty=global |

**Chunking behavior:**
- Content before the first H2/H3 heading → one chunk (no heading)
- Each H2/H3 section → one chunk with that heading as `chunk_heading`
- Token safety: sections >7,000 tokens are truncated with a warning (should not occur in practice)

---

## 3. Tenant Scope Design

SOPs can be scoped to specific tenants or apply globally:

| clients field | DB value | Retrieval behavior |
|---|---|---|
| (empty or absent) | `clients = '{}'` | Retrieved by all tenants (global SOP) |
| `clients: unity_bank` | `clients = '{unity_bank}'` | Only retrieved for unity_bank queries |
| `clients: unity_bank,rbl_bank` | `clients = '{unity_bank,rbl_bank}'` | Only for those two tenants |

The retrieval filter in SQL:
```sql
AND (sc.clients = '{}' OR p_client = ANY(sc.clients))
```

Empty array = global (no tenant filter applied). This is the correct design for standard operating procedures that apply to all supported tenants.

**Sample SOPs created:**
| File | Scope | query_type |
|---|---|---|
| `otp_delivery_failure_resolution.md` | Global | `OTP_ISSUE` |
| `video_kyc_session_failure.md` | `unity_bank` | `VIDEO_KYC` |
| `account_lockout_resolution.md` | Global | `AUTH_ISSUE` |

---

## 4. Idempotency and Deduplication

**New SOP**: `sop_id` not found in `rag_sop_library` → insert library record + embed all sections.

**Unchanged SOP**: `content_hash` matches existing record → skip entirely. Zero DB writes.

**Updated SOP**: `content_hash` differs → bump version, delete all old chunks for the `sop_id`, re-embed and insert new chunks with the new `sop_version`.

The version bump is critical because SOP chunk IDs are deterministic:
```python
uuid5(NAMESPACE_URL, f"sop_chunk:{sop_id}:{chunk_index}:{sop_version}")
```
A version 1 → version 2 bump produces entirely new chunk IDs, so old and new chunks cannot be confused. The old chunks are explicitly deleted to prevent stale sections from appearing in retrieval alongside updated ones.

**Deactivation (soft delete):**
```python
pipeline.deactivate_sop(sop_id)   # sets is_active=False
```
The retrieval RPC filters `WHERE sl.is_active = TRUE`, so deactivated SOPs never appear in results. The chunks remain in the DB for potential re-activation or audit.

---

## 5. SOP Boost Mechanism

The +0.15 similarity boost is applied in `match_all_b1_sources`:

```sql
LEAST(1.0, (1 - (sc.embedding <=> p_query_embedding)) + 0.15)::FLOAT AS boosted_score
```

- Semantic similarity of 0.50 → boosted_score 0.65
- Semantic similarity of 0.90 → boosted_score 1.00 (capped at 1.0)
- Results are ordered `ORDER BY boosted_score DESC`

This ensures SOPs rank above ticket chunks with comparable semantic similarity. The boost does not affect the raw `similarity` field — only `boosted_score` is inflated. The `RetrievedChunk` schema carries both values for transparency.

**Retrieval verification (T5):**
```python
sop_results = [c for c in resp.chunks if c.source_table == "rag_sop_chunks"]
boost_applied = all(abs(c.boosted_score - c.similarity) >= 0.10 for c in sop_results)
# Should be True after SOP ingestion
```

---

## 6. Pipeline Design Decisions

### 6.1 Why NOT use BatchEmbeddingProcessor?

`BatchEmbeddingProcessor` takes `list[TicketChunk]` objects and mutates them in-place. SOP chunks are `dict` records, not `TicketChunk` instances. Rather than duck-typing or creating a wrapper, the SOP pipeline uses `embed_batch()` directly from the `EmbeddingProvider` protocol.

This is simpler and correct: SOP corpus is small (~10–50 SOPs, ~100–500 chunks). The full circuit-breaker overhead of `BatchEmbeddingProcessor` is not needed. If a single batch fails, it is logged and skipped; the next run will retry (since the SOP chunk hash will still differ from the library record).

### 6.2 Why delete old chunks on update?

The `rag_sop_chunks` UNIQUE constraint is on `(sop_id, chunk_index, sop_version)`. When a SOP is updated:
- New version = old version + 1
- New chunk IDs are different from old chunk IDs
- Upserting new chunks would NOT replace old chunks (different IDs, different sop_version)

Without deletion, both old and new sections would be in `rag_sop_chunks`. The retrieval RPC joins on `sop_id` with no `sop_version` filter — both old and new sections would be retrieved together, creating duplicate or contradictory content.

Deleting old chunks before inserting new ones keeps the table clean. This is the correct production behavior.

### 6.3 Why no SOP chunker class?

The ticket pipeline has a `TicketChunker` class because the chunking logic is complex (token-aware splitting, multiple chunk types per document, UUID5 generation requiring the document ID). SOP sections are already split by heading in `SopDocumentBuilder._split_sections()`. The SOP pipeline's `_build_chunk_records()` method is a thin wrapper that adds token safety and ID generation — not complex enough to warrant a separate class.

### 6.4 Why truncate SOP sections instead of splitting?

SOP sections are procedural step groups. Splitting a section mid-step would produce a semantically incomplete chunk. Truncation with a warning is the correct behavior — the SOP author should be notified to shorten the section. In practice, step-group sections are rarely > 500 words.

---

## 7. Operational Notes

### 7.1 Running SOP Ingestion

```bash
# First ingestion
python scripts/ingest_sop.py

# After modifying SOPs (content-hash-changed SOPs will be updated)
python scripts/ingest_sop.py

# Dry run before production
python scripts/ingest_sop.py --dry-run

# List current SOPs
python scripts/ingest_sop.py --list

# Ingest from a specific directory
python scripts/ingest_sop.py --sop-dir /path/to/sop/files/

# Tenant-specific default clients (for SOPs without frontmatter clients field)
python scripts/ingest_sop.py --clients unity_bank,rbl_bank
```

### 7.2 Validating SOP Ingestion

After `ingest_sop.py`, run:
```bash
python scripts/validate_b1_retrieval.py --live --report
```

Check T5 in the report:
```
T5 — SOP-heavy retrieval (SOP boost)
  [OK]  SOP chunks available: 30+
  [OK]  SOP boost applied: N SOP chunks boosted
```

### 7.3 Adding New SOPs

1. Create a `.md` file in `data/sop/` with appropriate frontmatter
2. Run `python scripts/ingest_sop.py`
3. The new SOP is live immediately (no restart required)

### 7.4 Updating Existing SOPs

1. Edit the `.md` file (change content in any section)
2. Run `python scripts/ingest_sop.py`
3. The pipeline detects the `content_hash` change, bumps the version, and re-embeds

---

## 8. Expected Retrieval Impact

After SOP ingestion with the three sample files:

| Query | Expected result |
|---|---|
| "OTP not received on mobile" | OTP SOP chunks rank in top-3 (global) |
| "video KYC call dropping" | Video KYC SOP chunks rank in top-3 (unity_bank) |
| "account locked after wrong password" | Account lockout SOP chunks rank in top-3 (global) |
| "standard operating procedure for verification" | All relevant SOPs surface |

The +0.15 boost ensures SOPs beat ticket chunks at comparable semantic similarity. An SOP at 0.45 similarity beats a ticket chunk at 0.59 similarity (boosted SOP = 0.60 > 0.59).
