# Handoff

## State
Knowledge Layer v2.1 — CERTIFIED AND FROZEN (2026-07-03). Sprint 2.37 engineering guardrails COMPLETE.
Branch: major-architecture-change. Git: 133c06852b19c6955fa5ea1af47285fcc85b91a1
All Sprint 2.37 tests: 64/64 PASS (45 golden knowledge + 19 golden retrieval).

## Artefacts (all new in Sprint 2.37, no production code changed)
- `scripts/generate_freeze_manifest.py` → `data/freeze/knowledge_v2_1_freeze_manifest.json`
- `scripts/corpus_fingerprint.py --generate|--verify` → `data/freeze/knowledge_v2_1_fingerprints.json`
- `tests/test_knowledge_golden.py` — 45 tests, 7 article categories
- `tests/test_golden_retrieval.py` — 19 tests, content-based retrieval assertions
- `docs/KNOWLEDGE_LAYER_FREEZE.md` — canonical freeze spec

## Next
1. Tasks #10-#14: async FastAPI handlers, Prometheus metrics, Docker hardening, Redis config, SQL migrations B1_007/B1_008
2. Task #15: reingestion CLI (scripts/reingest_v2.py)
3. Task #16: security audit (log redaction, webhook enforcement, Docker security)

## Context
- `exclude_escalation=True` in `case_engine/knowledge/rag_adapter.py:83` — NEVER revert (security fix)
- `index_version='v2'` required in every `RetrievalRequest` for knowledge chunks (default 'v1' excludes them)
- `scripts/certify_freeze.py` is the canonical freeze verifier; run it before ANY future knowledge layer change
- `scripts/corpus_fingerprint.py --verify` confirms corpus unchanged since freeze
