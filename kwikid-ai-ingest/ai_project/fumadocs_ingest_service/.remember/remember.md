# Handoff

## State
Sprint 2.64 CERTIFIED (22/22 tests, 462/462 combined 2.48+2.60+2.61+2.62+2.63+2.63.1+2.63.2+2.64, 0 regressions).
All sprint changes committed on `major-architecture-change` (commit 4a909ca). sentry-sdk[fastapi] added to requirements.txt and installed.
Full regression: 1267 passed, 2 pre-existing failures (test_sprint2281 interface drift), 0 errors.

## Next
1. **§4.4 admin action** — create `ai.support@getkwikid.com` Freshdesk agent account. Last remaining production gate. Then set `SUPPORT_AGENT_MODE=PRODUCTION`.
2. Post-deploy verification: test ticket through Freshdesk → confirm observation note posted; complete Asana task → confirm customer reply + status=4.
3. Optional: merge `major-architecture-change` → `main` when Umair confirms post-deploy verification passes.

## Context
Pre-existing test failures (not regressions): test_sprint2281 (2 tests, Sprint 2.30.1 interface drift).
`observation.py` (Sprint 2.18) is shadowed by `observation/` package (Sprint 2.44) — edits to observation.py only take effect via `importlib` direct load.
handlers.py:423 step_results type guard added (list vs dict) — post-cert defensive fix committed in 4a909ca.

## Docs reorg (Umair, 2026-07-31 — no code changes)
All ~145 loose root-level and `docs/`/`docs_internal/` markdown files were moved (not edited) into `docs/{guides,project_status,sprints,architecture,architecture/internal,archive}/`. Zero `.py`/`.env`/config files touched except: 4 orphaned hardcoded-Windows-path debug scripts (`check_db.py`, `rehearsal_stage2_corpus.py`, `test_chunking.py`, `test_sop.py` — confirmed unreferenced anywhere) moved to `docs/archive/legacy_scripts/`, and one real test (`test_investigate_webhook.py`) moved from root into `tests/` (still auto-discovered by `pytest -q`, no testpaths restriction in pytest.ini/ci.yml). `ontology.json` deliberately left at root — `case_engine/nlp_router.py` loads it via `Path(__file__).parent.parent`. `CLAUDE.md`, `.env*`, `.remember/`, all config files untouched at root. `docs_internal/` and the nested `Big_Phase_2_documentations/` are now empty (files moved out) but couldn't be rmdir'd (Cowork delete-protection on this mounted folder) — safe to delete manually. If you go looking for any doc by its old root-level path, check `docs/<category>/` first.
