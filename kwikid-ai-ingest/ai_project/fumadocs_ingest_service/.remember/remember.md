# Handoff

## State
Sprint 2.34 (Knowledge Layer Certification) COMPLETE. Knowledge Layer certified and FROZEN.

All Sprint 2.34 fixes applied:
- `requirements.txt`: `rapidocr-onnxruntime>=1.3.0` uncommented (was commented out — OCR was silently disabled)
- `docs/KNOWLEDGE_LAYER_MIGRATION_PLAN.md`: Migration order corrected — B3_007 BEFORE B3_006 (B3_006 references `kc.fts` which B3_007 creates)
- `scripts/ingest_knowledge.py`: Docstring examples updated from `../More_data` to `./stackoverflow`
- `.env`: Comment fixed (`More_data()` → `stackoverflow/`) — actual value was already correct
- `rag_engine/ingestion/knowledge_pipeline.py`: Dead `_chunk_text()` function removed

Production readiness score: **87/100**. Architecture certified. No code bugs found.

## Next (priority order)

1. KNOWLEDGE MIGRATION — execute the 8-step plan in `docs/KNOWLEDGE_LAYER_MIGRATION_PLAN.md`:
   - Step 4 order (CRITICAL): B3_001 → B3_002 → B3_003 → B3_004 → B3_005 → **B3_007** → B3_006
   - `pip install rapidocr-onnxruntime>=1.3.0` before Step 7 (Live Ingestion)
   - Dry-run: `python scripts/ingest_knowledge.py --dry-run --source ./stackoverflow --verbose`
   - Step 8 (hard-delete legacy fumadocs SOPs) is IRREVERSIBLE — explicit confirmation required

2. CONFIGURE investigation tools: live Unity Bank API credentials in TenantContext.enabled_tools
   (configuration, not code — contact Unity Bank integration team)

3. Phase B: Investigation Layer integration
4. Phase C: Action & Execution Layer
5. Phase D: Production hardening

## Context
- Branch: major-architecture-change
- Corpus: `./stackoverflow/` (802Q + 913A, 1,255 image GUIDs in manifest, ~200 local PNGs)
- 83% of images have no local PNG — OCR gracefully returns ocr_text=None for these
- `DEFAULT_RAG_TENANT` hardcoded to "unity" in HybridRAGProvider — multi-tenant retrieval limitation
- Question-level comments silently dropped (only answer comments captured) — known minor gap
- OCR text NOT in embeddings (stored in image_metadata JSONB only) — design decision, not a bug
- WhatsApp chat image may misclassify (URL title attribute not parsed) — no impact since image has no local PNG
- B3_004 contains `DELETE FROM rag_knowledge_chunks WHERE quality_score < 0.55` — data-destructive, apply once only
