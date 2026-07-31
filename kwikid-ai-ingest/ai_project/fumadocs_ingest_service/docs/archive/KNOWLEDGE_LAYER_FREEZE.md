# Knowledge Layer v2.1 — Freeze Specification

**Status**: FROZEN  
**Certified**: 2026-07-03  
**Branch**: `major-architecture-change`  
**Git commit at freeze**: `133c06852b19c6955fa5ea1af47285fcc85b91a1`

---

## 1. What is Frozen

The Knowledge Layer v2.1 is a production-frozen corpus of 375 internal StackOverflow-for-Teams articles, chunked and embedded into Supabase for hybrid RAG retrieval.  No part of the pipeline that changes corpus content or embeddings may be modified without a new certification run.

Frozen artefacts:
- **`rag_knowledge_articles`** — 375 active rows
- **`rag_knowledge_chunks`** — 2,293 chunks (`index_version = 'v2'`)
- **`rag_knowledge_articles.image_metadata`** — 672 image records (OCR text inline)

Frozen code (do NOT modify without recertification):
- `rag_engine/ingestion/knowledge_pipeline.py`
- `rag_engine/chunking/knowledge_chunker.py`
- `rag_engine/parsers/stackoverflow_parser.py` (if present)
- `rag_engine/retrieval/ticket_retriever.py`
- `case_engine/knowledge/rag_adapter.py`

---

## 2. Architecture

### Ingest Path

```
stackoverflow/posts.json + images/ (802 Q + 913 A, 1,255 image GUIDs)
    │
    └─► StackOverflowParser.parse()
          _select_best_answer()       — accepted_answer | highest-score fallback
          _extract_image_references() — classify + optional OCR (rapidocr-onnxruntime)
          _inject_ocr_inline()        — replaces ![alt](CDN_url) with [IMAGE: ocr_text]
                                        AT ORIGINAL READING POSITION (not appended)
          → list[KnowledgeArticle]
    │
    └─► KnowledgePipeline.run()
          KnowledgeQualityValidator   — quality_score (0.0–1.0); floor = 0.55
          KnowledgeClassifier         — knowledge_class (VERIFIED_REPLY, FAQ, etc.)
          _redact_pii()               — redacts email, phone, secrets
          _build_embed_text_raw()     → "QUESTION: {title}\n...\nANSWER:\n{body}"
          _sanitize_for_embedding()   — strip residual HTML, CDN URLs, base64
          KnowledgeChunker.chunk()    — token-aware: 1200 tok target, 150 overlap
            CODE_SAMPLE emitted only for fences >= 60 chars (_MIN_CODE_CHARS)
            shorter fences folded into surrounding prose chunk
          ChunkQualityFilter          — rejects short/boilerplate/no-alpha chunks
          _batch_embed_and_upsert()   — text-embedding-3-small (1536 dims)
    │
    └─► Supabase
          rag_knowledge_articles  (upsert on article_id)
          rag_knowledge_chunks    (upsert on article_id + chunk_index + index_version)
```

### Retrieval Path

```
WorkflowEngine
    └─► KnowledgeOrchestrator._query_rag()
          HybridRAGProvider.retrieve(query, topic)     [rag_adapter.py]
            exclude_escalation=True    ← HARDCODED SECURITY INVARIANT (line 83)
            TicketRetriever.retrieve(RetrievalRequest(index_version='v2'))
              supabase.rpc("match_all_b1_sources")
                Branch 3: rag_knowledge_chunks
                  WHERE index_version = 'v2'
                  AND quality_score >= 0.55
                  boosted_score = similarity + 0.08 + quality_bonus (max 0.05)
              → list[RetrievedChunk]  (.source_table == "rag_knowledge_chunks")
```

---

## 3. Invariants (Never Change Without Recertification)

| Invariant | Value | Location |
|-----------|-------|----------|
| `exclude_escalation` | `True` (hardcoded) | `rag_adapter.py:83` |
| `index_version` in every `RetrievalRequest` | `'v2'` | `rag_settings.py` |
| DB upsert key | `(article_id, chunk_index, index_version)` | `knowledge_pipeline.py` |
| `_MIN_CODE_CHARS` | `60` | `knowledge_chunker.py` |
| `quality_score` floor | `0.55` | `rag_settings.knowledge_min_quality_score` |
| OCR injection position | inline at image position (not appended) | `stackoverflow_parser.py` |
| Embedding model | `text-embedding-3-small` | `rag_settings.embedding_model` |
| Embedding dimensions | `1536` | `rag_settings.embedding_dimensions` |
| Chunk target tokens | `1200` | `rag_settings.chunk_target_tokens` |
| Chunk overlap tokens | `150` | `rag_settings.chunk_overlap_tokens` |

---

## 4. Certification Procedure

Run before ANY change to frozen code or before re-ingestion:

```bash
python scripts/certify_freeze.py
```

Expected output: **24/24 checks PASS**.  If any check fails, the proposed change is blocked until root cause is identified and either fixed or the freeze cert is updated with justification.

### Checks (24 total)

**Section 1 — Corpus Integrity (7 checks)**
1. All source articles accounted for (375 active)
2. Zero null embeddings
3. Zero orphan chunks (no article parent)
4. Zero articles with zero chunks
5. Zero duplicate chunk_index within any article (DB key violation)
6. OCR image rate (>= 40% of imaged articles have OCR)
7. Accepted-answer articles have non-null `accepted_answer_id`

**Section 2 — Retrieval Integrity (9 checks)**
1–6. Six representative queries each return >= 1 knowledge chunk  
7. Image-heavy articles produce `[IMAGE:]` markers retrievable from chunks  
8. Code-heavy articles produce CODE_SAMPLE chunks  
9. All 2,293 chunks have non-null embeddings

**Section 3 — Knowledge Integrity (5 checks)**
1. Q/A body faithfulness: question body present for 90%+ of sampled articles  
2. OCR fidelity: articles with OCR have `[IMAGE:]` markers in chunks  
3. Code fence fidelity: articles with meaningful (>= 60 char) fences have CODE_SAMPLE chunks  
4. Table fidelity: table-heavy articles have pipe chars in chunk content  
5. SOP fidelity: numbered-step articles preserve step structure

**Section 4 — Production Integrity (3 checks)**
1. Benchmark subprocess exits 0  
2. VERDICT contains "PASS"  
3. Hit rate >= 60% (actual: 100%, 30/30)

---

## 5. Rebuild Procedure

If the corpus must be rebuilt (new articles, parser fix, embedding model change):

1. **Freeze the current state**: run `scripts/certify_freeze.py` — confirm 24/24 PASS baseline.
2. **Branch**: create `knowledge-v2.2-rebuild` from `main`.
3. **Make changes** in the new branch only.
4. **Re-ingest**: `python scripts/ingest_knowledge.py` (dry-run first with `--dry-run`).
5. **Generate new fingerprints**: `python scripts/corpus_fingerprint.py --generate`.
6. **Recertify**: `python scripts/certify_freeze.py` — must get 24/24 PASS.
7. **Update manifest**: `python scripts/generate_freeze_manifest.py`.
8. **Update golden tests**: run `scripts/_discover_golden.py`, update fixtures in `tests/test_knowledge_golden.py`.
9. **Bump version**: update all references from `v2.1` to `v2.2`.
10. **PR**: require CTO sign-off before merging.

---

## 6. What MAY Change Without Recertification

- API layer changes (`api/`, `app/`, `webhook/`, `freshdesk/`)
- Case engine logic (`case_engine/`) — but NOT `case_engine/knowledge/rag_adapter.py`
- Test files, scripts, docs
- `requirements.txt`, `.env.example`, Docker config
- Metrics, logging, observability code (read-only DB access)

---

## 7. What MUST NOT Change Without Recertification

- Any file in the ingest path (see Section 2 above)
- `rag_adapter.py` — especially `exclude_escalation=True` and `index_version`
- `rag_settings.py` embedding config (model, dimensions, token targets)
- DB migration that modifies `rag_knowledge_articles` or `rag_knowledge_chunks` schema
- The `match_all_b1_sources` RPC function

---

## 8. Versioning Policy

| Version | Status | Articles | Chunks | Certified |
|---------|--------|----------|--------|-----------|
| v2.0    | Superseded | 375 | 2,293 | 2026-07-01 |
| v2.1    | **CURRENT / FROZEN** | 375 | 2,293 | 2026-07-03 |

Version is incremented when:
- Corpus article count changes
- Embedding model changes
- Parser or chunker logic changes that alter stored content
- Quality threshold or boost parameters change

The `index_version` DB field (`'v2'`) is separate from the manifest version and is only bumped when a full re-embedding is done.

---

## 9. Freeze Artefacts

| File | Purpose |
|------|---------|
| `scripts/certify_freeze.py` | 24-check read-only certifier |
| `scripts/generate_freeze_manifest.py` | Writes `data/freeze/knowledge_v2_1_freeze_manifest.json` |
| `scripts/corpus_fingerprint.py` | SHA-256 per-article fingerprint generator/verifier |
| `data/freeze/knowledge_v2_1_freeze_manifest.json` | Machine-readable freeze record |
| `data/freeze/knowledge_v2_1_fingerprints.json` | SHA-256 corpus fingerprints |
| `tests/test_knowledge_golden.py` | 7-category DB content regression suite |
| `tests/test_golden_retrieval.py` | Content-based retrieval regression suite |
| `docs/KNOWLEDGE_LAYER_CERTIFICATION_REPORT.txt` | Sprint 2.34 audit report |

---

## 10. Golden Test Article Map

| Category | Article ID | Title (truncated) | Key Assertion |
|----------|------------|-------------------|---------------|
| IMAGE-HEAVY | `so_890` | PAN Verification API Debug (Unity) | >= 15 images, `[IMAGE:]` markers |
| OCR-HEAVY | `so_453` | RBL Callback Retrigger Script | >= 14 OCR images, `aws dynamodb query` |
| CODE-HEAVY | `so_1488` | Unity Callback Checker | >= 20 CODE_SAMPLE chunks, `check_callback_responses.py` |
| SOP-HEAVY | `so_236` | CBI Video Recovery | `python cbi_video.py`, venv activation |
| ACCEPTED-ANSWER | `so_24` | TCOOKP Put Booking | `VERIFIED ANSWER:`, `booking/put/` curl |
| MULTI-ANSWER | `so_1587` | CBI VKYC Backend Downtime | `network level issue`, `nslookup` DNS step |
| TABLE-HEAVY | `so_1337` | Unity Admin Config | >= 200 pipe chars, `aws s3 ls s3://kwikid-prod/CONFIG/ADMIN/` |
