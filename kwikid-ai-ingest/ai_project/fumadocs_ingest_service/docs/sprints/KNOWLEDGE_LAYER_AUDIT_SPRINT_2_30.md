# Knowledge Layer Audit — Sprint 2.30

**Date:** 2026-06-25
**Scope:** rag_engine RAG/knowledge pipeline audit — Tasks A through D

---

## 1. Hybrid RAG Status

**Status: ACTIVE code, WIRED but GATED by environment flag. NOT dead code.**

### Evidence

`rag_engine/retrieval/hybrid_ticket_retriever.py` is a fully implemented class (680 lines) that is:

1. **Imported at runtime** in `app/main.py` lines 2006–2013:
   ```python
   if _B1_HYBRID_ENABLED:
       from rag_engine.retrieval.hybrid_ticket_retriever import HybridTicketRetriever
       retriever = HybridTicketRetriever(...)
   else:
       retriever = TicketRetriever(...)
   ```
   The gate `_B1_HYBRID_ENABLED` reads `B1_HYBRID_RETRIEVAL_ENABLED` (default `false`). In `.env.example`, this is set to `true`, meaning it IS active in the configured environment.

2. **Used in evaluation scripts**: `scripts/evaluate_retrieval.py` lines 346–354 also instantiates it.

3. **Full RRF pipeline implemented**: The class executes parallel FTS + semantic threads, RRF fusion, BM25 reranking, SOP guarantee, ISSUE_HEADER capping — no placeholder stubs.

### Relationship to KnowledgeOrchestrator

`case_engine/knowledge/orchestrator.py` has a **separate** `_query_rag()` method that accepts an injected `rag_provider`. This is **not** connected to `HybridTicketRetriever`:

- `rag_provider=None` (the current default) → returns `RAGEvidence.placeholder_evidence()`
- `rag_provider=<real_provider>` (Sprint 2.28 planned path) → calls `rag_provider.retrieve()`

**Conclusion**: `HybridTicketRetriever` serves the `/rag/chat` HTTP endpoint (B1 RAG pipeline). The `KnowledgeOrchestrator` (case engine ticket workflow) uses a separate RAG injection slot that remains a placeholder in Sprint 2.27.5 and is scheduled for Sprint 2.28 wiring. The two paths are architecturally distinct. Neither is dead code — they serve different consumers.

---

## 2. Metrics Added

### File Modified
`rag_engine/observability/metrics_collector.py`

### New Metric Name Constants (knowledge_ prefix — no conflict with freshdesk_ prefix)

| Constant | Value | Type |
|---|---|---|
| `KNOWLEDGE_EMBEDDING_LATENCY_MS` | `"knowledge_embedding_latency_ms"` | Latency histogram |
| `KNOWLEDGE_RETRIEVAL_HIT_RATE` | `"knowledge_retrieval_hit_rate"` | Derived ratio |
| `KNOWLEDGE_COMPLETENESS_SCORE_LOW` | `"knowledge_completeness_score_low"` | Counter (score < 0.5) |
| `KNOWLEDGE_COMPLETENESS_SCORE_MED` | `"knowledge_completeness_score_medium"` | Counter (0.5 ≤ score < 0.8) |
| `KNOWLEDGE_COMPLETENESS_SCORE_HIGH` | `"knowledge_completeness_score_high"` | Counter (score ≥ 0.8) |
| `KNOWLEDGE_QUALITY_REJECTION_RATE` | `"knowledge_quality_rejection_rate"` | Derived ratio |
| `KNOWLEDGE_ARTICLES_PROCESSED` | `"knowledge_articles_processed_total"` | Counter |
| `KNOWLEDGE_ARTICLES_REJECTED` | `"knowledge_articles_rejected_total"` | Counter |
| `KNOWLEDGE_RETRIEVAL_REQUESTS` | `"knowledge_retrieval_requests_total"` | Counter |
| `KNOWLEDGE_RETRIEVAL_HITS` | `"knowledge_retrieval_hits_total"` | Counter |

### New Methods Added to MetricsCollector

- `record_knowledge_embedding_latency(latency_ms)` — delegates to `record_latency(KNOWLEDGE_EMBEDDING_LATENCY_MS, ...)`
- `record_knowledge_article_processed(*, rejected=False)` — increments processed + optional rejected counters
- `record_knowledge_completeness_score(score)` — bins score into low/medium/high bucket counter
- `record_knowledge_retrieval(*, hit)` — tracks request total and hit count
- `knowledge_quality_rejection_rate()` — computed ratio: rejected / processed
- `knowledge_retrieval_hit_rate()` — computed ratio: hits / requests

### Conflict Verification
All new names start with `knowledge_`. Freshdesk metrics (`freshdesk/metrics.py`) all start with `freshdesk_`. Zero collision. The Prometheus metrics in `observability/metrics.py` already had separate knowledge layer counters (WORK ITEM 7) — the new `rag_engine` MetricsCollector additions are the *in-process ingestion-run* tracker which is a different system.

---

## 3. Test File Created

**File:** `tests/test_knowledge_retrieval_evaluation.py`

All tests are deterministic and require no external API calls, no Supabase, no network. File uses `unittest.mock` and `tempfile.TemporaryDirectory` for filesystem isolation.

### Test Classes and Methods

#### `TestParserRoundTrip` (7 tests)
- `test_article_id_is_so_prefixed` — `article_id == "so_1537"` for post id 1537
- `test_article_type_qa_pair_when_answer_present` — article_type=="qa_pair" when answer present
- `test_image_count_zero_when_no_images` — all image counters are 0, status=="no_images"
- `test_completeness_score_between_0_and_1` — score in [0.0, 1.0]
- `test_safety_classification_populated` — classification is one of the 3 valid values
- `test_source_is_stackoverflow_for_teams` — source field
- `test_accepted_answer_selected_over_higher_score` — accepted answer wins over high-score rival
- `test_sensitive_operations_classification` — "production secret" → "sensitive_operations"

#### `TestQualityValidatorChecks` (11 tests — 1 per check + 1 positive)
- `test_check_1_missing_question` → `MISSING_QUESTION` error
- `test_check_2_missing_answer` → `MISSING_ANSWER` error (qa_pair, no answer)
- `test_check_3_empty_sop` → `EMPTY_SOP` error (combined < 100 chars)
- `test_check_4_parser_corruption` → `PARSER_CORRUPTION` error (â€ artifacts)
- `test_check_5_broken_markdown` → `BROKEN_MARKDOWN` warning (unclosed fence)
- `test_check_6_broken_canonical_url` → `BROKEN_CANONICAL_URL` warning
- `test_check_7_duplicate_content` → `DUPLICATE_CONTENT` error (second validate call)
- `test_check_8_image_manifest_missing` → `IMAGE_MANIFEST_MISSING` warning
- `test_check_9_invalid_completeness_score` → `INVALID_COMPLETENESS_SCORE` error (score=1.5)
- `test_check_10_repetitive_content` → `REPETITIVE_CONTENT` warning (>70% duplicate tokens)
- `test_valid_article_passes_all_checks` — clean article has zero errors

#### `TestImageGrounding` (5 tests)
- `test_manifest_missing_fires_for_unresolved_ref`
- `test_asset_missing_fires_when_manifest_found_but_no_local_file`
- `test_no_image_warnings_when_both_resolved`
- `test_both_warnings_fire_for_mixed_refs`
- `test_image_grounding_status_in_parser` — parser end-to-end with images.json missing entry

#### `TestDuplicateDetection` (4 tests)
- `test_first_pass_no_duplicate`
- `test_second_pass_same_content_triggers_duplicate`
- `test_reset_run_clears_seen_hashes`
- `test_distinct_content_no_duplicate`

#### `TestCompletenessFloor` (4 tests)
- `test_parser_sets_manual_review_for_low_score` — end-to-end parser with no-tag, no-answer post
- `test_stub_article_low_score_sets_manual_review` — completeness_score=0.3 → manual_review_required=True
- `test_high_completeness_score_no_manual_review` — score=1.0 → manual_review_required=False
- `test_threshold_boundary_0_80` — score=0.80 → manual_review_required=False (boundary is exclusive)

#### `TestKnowledgeMetricsCollector` (10 tests)
- `test_record_embedding_latency_stored`
- `test_quality_rejection_rate_zero_when_no_articles`
- `test_quality_rejection_rate_correct_after_processing`
- `test_retrieval_hit_rate_zero_when_no_requests`
- `test_retrieval_hit_rate_correct`
- `test_completeness_score_bucketing_low`
- `test_completeness_score_bucketing_medium`
- `test_completeness_score_bucketing_high`
- `test_summary_includes_knowledge_rates`
- `test_knowledge_metric_names_do_not_conflict_with_freshdesk_prefix`

#### `TestHybridRetrieverInterface` (7 tests)
- `test_retriever_instantiates_without_error`
- `test_retrieve_returns_response_object_on_empty_results`
- `test_retrieve_uses_embedding_provider` — embed_single called exactly once
- `test_retrieve_raises_on_missing_client` — ValueError on empty client
- `test_rrf_fusion_semantic_only_when_no_keyword_results`
- `test_rrf_fusion_hybrid_mode_when_both_results_present`
- `test_retrieval_metadata_contains_required_keys` — checks 6 required keys in metadata dict

#### `TestValidationResultAPI` (3 tests)
- `test_is_valid_false_when_error_present`
- `test_is_valid_true_when_only_warnings`
- `test_errors_and_warnings_properties`

**Total: 51 tests**

---

## 4. Dead Code Findings

### Files Flagged (NOT deleted — flagged only per task spec)

| File | Status | Reason |
|---|---|---|
| `rag_engine/ingestion/delta_tracker.py` | Active | Imported by `rag_engine/ingestion/pipeline.py` and `__init__.py` |
| `rag_engine/ingestion/schema_mapper.py` | Active | Imported by `pipeline.py` and 3 validation scripts |
| `rag_engine/ingestion/deduplication.py` | Active | Imported by pipeline, scripts, and existing tests |
| `rag_engine/embedding/base.py` | Active (Protocol) | `pass` body is correct for Protocol methods — not a stub |
| `rag_engine/sop/sop_parser.py` | Active | Imported by `chat_generator.py` and `context_assembler.py` |
| `rag_engine/document_builder/sop_builder.py` | Active | Imported by `sop_pipeline.py` |
| `rag_engine/document_builder/ticket_builder.py` | Active | Imported by `pipeline.py` and tests |
| `rag_engine/feedback/feedback_loop.py` | Active | Imported by `app/main.py` and `app/feedback.py` |
| `rag_engine/feedback/review_queue.py` | Active | Imported by `app/main.py` |
| `case_engine/knowledge/importer.py` | Active | Imported by `__init__.py` and tests |
| `case_engine/knowledge/unified_bundle.py` | Active | Imported by `orchestrator.py` and tests |

### Notes

- `rag_engine/embedding/base.py`: The `pass` bodies on Protocol methods are correct Python — Protocol method bodies are intentional stubs (the `...` or `pass` is the contract, not a missing implementation). Not dead code.
- `case_engine/knowledge/retriever.py` (`HybridRetriever`): This is the *SOP-domain keyword retriever*, distinct from `rag_engine/retrieval/hybrid_ticket_retriever.py`. Both serve different layers; neither is dead code.
- `case_engine/knowledge/orchestrator.py`: The `_query_rag()` method returns a placeholder when `rag_provider=None`. This is **intentional scaffolding** for Sprint 2.28 injection — it is not dead code, it is a wired extension point.

### Files Deleted

**None.** No file qualifies for deletion — all rag_engine and case_engine/knowledge files have at least one active caller or are correctly structured abstractions.

---

## 5. Overall Assessment: Is the Knowledge Pipeline Production-Ready?

**Verdict: CONDITIONALLY READY — production-capable for ingestion; retrieval orchestration has one open gap**

### Strengths

1. **Parser**: `StackOverflowParser` is fully implemented with image grounding, safety classification, 11-dimension completeness scoring, and defensive parsing. Zero placeholder methods.

2. **Quality Validator**: All 10 validation checks are deterministic, implemented, and have test coverage in the new suite.

3. **Ingestion Pipeline**: `KnowledgePipeline` implements parse → validate → classify → PII-redact → chunk → embed → upsert with idempotency (SHA256 dedup) and dry-run mode.

4. **Hybrid Retriever**: `HybridTicketRetriever` is fully implemented and wired to the `/rag/chat` endpoint via `B1_HYBRID_RETRIEVAL_ENABLED`. RRF fusion, adaptive k, BM25 reranking, SOP guarantee, ISSUE_HEADER cap — all implemented.

5. **Metrics**: Prometheus metrics for knowledge ingestion are registered in `observability/metrics.py` (WORK ITEM 7). The new `rag_engine` MetricsCollector now also tracks embedding latency, hit rate, completeness distribution, and rejection rate for ingestion-run reporting.

### Open Gap

**`KnowledgeOrchestrator._query_rag()` returns placeholder evidence** when `rag_provider=None` (the current default). This means the ticket workflow's KNOWLEDGE_LOOKUP step does NOT yet query the B3 RAG knowledge chunks. Per the code comments, Sprint 2.28 must inject a real `rag_provider` (wrapping `HybridTicketRetriever` or `TicketRetriever`) to activate this path.

Until that injection is wired, the KwikID ticket case engine resolves knowledge from the SOP matcher only, not from the StackOverflow Q&A knowledge base in `rag_knowledge_chunks`.

### Recommendation

Set `B1_HYBRID_RETRIEVAL_ENABLED=true` (already set in `.env.example`) and complete the Sprint 2.28 `rag_provider` injection into `KnowledgeOrchestrator` to close the final gap.
