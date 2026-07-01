# Handoff

## State
Sprint 2.36 (Knowledge Layer Final Certification) IN PROGRESS.

OCR verification COMPLETE — all 13 sample images returned text, 100% success rate:
- 5 posts tested: ids [76, 82, 125, 150, 170]
- Image classes hit: FLOWCHART (10), TABLE (1), CONFIG_SCREEN (1)
- OCR confidence range: 0.92–0.98
- OCR text NOT in embed_text: 13/13 PASS

Engine caching fix applied to `rag_engine/ingestion/parsers/stackoverflow_parser.py`:
- Added `_ocr_engine: Optional["RapidOCR"] = None` class var
- `_run_ocr()` now lazy-inits and reuses the engine (was re-creating per image)
- Speedup: 8.9h → ~4.9h for full corpus

Real data path (NOT ./stackoverflow/): `C:/Users/Umair.Alam/Desktop/kwikid_support_system/stackoverflow/`
Ingest command: `python scripts/ingest_knowledge.py --source C:/Users/Umair.Alam/Desktop/kwikid_support_system/stackoverflow`

## Next — UNBLOCKED after user applies B3_006 in Supabase SQL editor

Still need user to run 4 SQL blocks (see previous chat):
1. Backup SOPs → 2. Soft-disable fumadocs SOPs → 3. Apply B3_006 → 4. Reset content_hash to NULL

After B3_006 confirmed:
5. Live ingestion (with OCR): `python scripts/ingest_knowledge.py --source C:/Users/Umair.Alam/Desktop/kwikid_support_system/stackoverflow`
6. Post-migration SQL checks
7. Retrieval benchmark: `python tests/benchmark_knowledge_retrieval.py`
8. Manual spot checks
9. Hard-delete legacy SOPs (IRREVERSIBLE)
10. Issue GO/NO-GO

## Context
- Live ingestion expected runtime: ~4.9h OCR + ~7min embeddings (1,313 chunks × OpenAI API)
- 362 articles in DB have content_hash set → will SKIP without step 4 (reset content_hash)
- 3 fumadocs SOPs active: Account Lockout, OTP Delivery Failure, Video KYC Session Failure
- Backup tables MISSING (must create in step 1 before touching rag_sop_library)
- SUPABASE_KEY is service_role — never expose to browser
- `exclude_escalation=True` in rag_adapter.py — CRITICAL security fix, do not revert
