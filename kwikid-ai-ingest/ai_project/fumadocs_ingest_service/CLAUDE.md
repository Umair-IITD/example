# KwikID AI Ingest — Project Rules

Enterprise support-agent automation for Freshdesk tickets. Python 3.11 / FastAPI.
Multi-tenant (Unity Bank first; BOB, RBL, others rolling in). Every sprint is
certified with a `sprint-X-Y-Z.md` report at the project root.

## Permanent rules (never violate)

- **NLU/NLG Split:** NLU (Natural Language Understanding) strictly happens at ingestion (`case_engine/nlp_router.py`) to extract intent/entities. NLG (Natural Language Generation) strictly happens at output (`intelligence/orchestrator.py`, `case_engine/investigation/observation.py`, `freshdesk/responses.py`) to generate notes/replies. Do not mix these boundaries.
- **Investigation Before Action:** L1 NEVER proposes or executes an action before collecting evidence. The OTP playbook (v3.0) is investigation-only and intentionally lacks EXECUTE steps.
- **Security fix — `exclude_escalation=True` in `case_engine/knowledge/rag_adapter.py:83`.** NEVER revert. Sprint 2.47 permanent.
- **`FreshdeskResponseService` is the sole approved Freshdesk write path.** Workflow / handler / adapter code must never call `FreshdeskClient.add_private_note` / `add_public_reply` / `update_ticket` directly. Blueprint SOB §26, §29. Sprint 2.28 + 2.48.
- **`ClosureFieldGuard` gates every PUT that sets `status=4` (Resolved) or `status=5` (Closed).** Freshdesk returns HTTP 422 if `cf_clients`, `ticket_type`, `cf_sop_status`, `cf_resolution_classification` are not all populated. SOT `Freshdesk_discovery/ticket_lifecycle.md` §6. Sprint 2.48.
- **`ReplySafetyGate` gates every `POST /reply` intent.** Confidence ≥ threshold, impact not in force-escalation set, body non-empty, not duplicate, kill-switch clear. If blocked, post the reply as a draft note via `build_draft_reply_note`. Sprint 2.48.
- **`InvestigationOrchestrator` (Sprint 2.46) is the sole production investigation entry point.** No parallel orchestrators.
- **`cf_clients` and `cf_environment` are READ-ONLY for the AI.** Set by Dispatch'r rules or humans. Never overwrite.
- **`ticket_type` writes are restricted to the 8 SOT-approved values** in `freshdesk.ALLOWED_TICKET_TYPES`. Read tolerance is broad; write tolerance is strict.
- **PII discipline:** log email domains only, never full addresses. Never log ticket body content. Never log API keys or credentials. `NLPSignal.raw_text` is NEVER logged.
- **10-second webhook budget:** the receiver must return 200 OK synchronously; all reasoning runs in `BackgroundTasks`.
- **Idempotency required for every Freshdesk write.** Handlers use `WebhookIdempotencyStore`; replies use `ReplySafetyGate` internal cache.
- **asyncio.to_thread() for all blocking I/O:** FastAPI runs on an async event loop. Supabase, Unity API, and Freshdesk calls MUST be wrapped in `asyncio.to_thread()`.

## Project layout

- `freshdesk/` — Freshdesk client, webhook verifier, handlers, idempotency, conversation state, response service, closure guard, HTML templates, safety gate.
- `case_engine/` — Reasoning: `nlp_router.py` (NLU), `investigation/` (planner, collector, root_cause, observation (NLG), pipeline), `workflows/` (playbooks, engine), `action_gateway*`, `knowledge/`, `ticket_orchestration/`, `tenant/`.
- `intelligence/` — LLM reasoning orchestrator (NLG for conclusions).
- `api/routes/` — FastAPI routes including `webhooks/freshdesk.py`.
- `webhook/` — Shared webhook processing abstractions.
- `retrieval/` — RAG hybrid retrieval (RRF + BM25 + FTS + pgvector).
- `tests/` — 200+ `test_sprint*.py` files, section-based (A, B, C…).

## Sprint workflow

1. Read the Source_Of_Truth doc for the layer being touched. Absolute path: `C:/Users/Umair.Alam/Desktop/kwikid_support_system/Source_Of_Truth/`. Use `/sot-check` skill.
2. Implement the sprint using the existing package boundaries (do not duplicate).
3. Write section-based integration tests as `tests/test_sprint<N>_<topic>.py`.
4. Run `/regression` — verifies Sprint 2.28.x + 2.46 + 2.47 + 2.48 + newest scope.
5. Write `sprint-X-Y-Z.md` via `/sprint-cert` — Blueprint reconciliation, files, tests, bugs, permanent certification.
6. Update `.remember/remember.md` via `/remember`.

## Testing conventions

- Test files: `tests/test_sprint<N>_<topic>.py` — one per sprint / topic.
- Sections labelled `TestX_<Name>` where X = A, B, C, …
- Tests are numbered `test_XN_<name>` (e.g. `test_A1_required_closure_fields`).
- Use `httpx.MockTransport` for outbound HTTP; never touch the network in tests.
- For naive-datetime tests, use `datetime.now(tz=timezone.utc).replace(tzinfo=None)` — never `datetime.now()`. Dev host is IST (+05:30) and `datetime.now()` fools the verifier's clock-skew guard.
- Fixtures set env vars up front: `RAG_API_KEY`, `OPENAI_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`, `AUDIT_BACKEND=inmemory`, `FRESHDESK_WEBHOOK_ENFORCE_HMAC=false`.
- **Mocking NLPRouter**: Never call the real OpenAI API in tests. Use `MagicMock(spec=NLPRouter)`.

## Pre-existing failures (do not count as regressions)

- `tests/test_sprint2281_ticket_created_handler.py` — 2 tests (Sprint 2.30.1 interface drift).
- ~129 unrelated failures across sprint216/219/224/228x/229x/2292/golden/stackoverflow tests.

## Certification report structure (`sprint-X-Y-Z.md`)

Every sprint report follows this outline: Blueprint Reconciliation Table, Architecture Reconciliation Table, Dependency Graph, Files Created, Files Modified, Test Counts, Regression Counts, Execution Timing, Bugs Found During Loop Engineering, Fixes Applied, Permanent Certification block.

## Slash commands (local)

- `/sprint-cert` — start a new sprint certification report from the house template.
- `/sot-check` — pull the relevant SOT doc for the layer being touched.
- `/regression` — run the standard regression suite and report deltas.
- `/remember` — save session handoff to `.remember/remember.md`.
- `/graph-engineer <task>` — **Graph Engineering** orchestrator: 9-node directed agent graph that plans, implements, reviews, and synthesizes code changes. Replaces standard Loop Engineering for complex multi-layer tasks. Command file: `.claude/commands/graph-engineer.md`.

## Subagents (dispatch via Agent tool)

- **`sprint-auditor`** — Reads the relevant SOT doc + `git diff HEAD` and reports SOT drift, missing requirements, untested paths, and a Ready/Blocked verdict. Dispatch BEFORE writing any `sprint-X-Y-Z.md`.
- **`freshdesk-safety-reviewer`** — Reviews any diff touching `freshdesk/` for the three permanent safety rules. Dispatch PROACTIVELY on any Freshdesk-touching diff.
- **`architecture-drift-corrector`** — Reviews `git diff HEAD` against `SUPPORT_OPERATIONS_BLUEPRINT.md` and `flow_diagram.mermaid`. If runtime code violates the blueprint (e.g., skipping investigation, bypassing NLU/NLG split, direct DB writes), it automatically corrects the code to align with the Blueprint. Dispatch after any major pipeline change.
- **`enterprise-code-reviewer`** — Reviews Python code for enterprise standards: `asyncio.to_thread()` usage on blocking I/O, PII masking in logs, proper error handling, no hardcoded credentials, strict type hinting, and test coverage completeness. Dispatch before finalizing any sprint.
- **`llm-behavior-validator`** — Inspects all LLM prompts and response parsers in `case_engine/nlp_router.py` and `intelligence/`. Ensures NLU strictly outputs structured JSON (no conversational text) and NLG strictly uses verified evidence (no hallucinated tool outputs). Dispatch when modifying any LLM prompt.
- **`graph-planner`** — Graph Engineering only. Breaks a task into a concrete numbered plan with file paths and Blueprint refs. Dispatched by `/graph-engineer` Node 2. Read-only.
- **`graph-worker`** — Graph Engineering only. Executes the approved plan: writes/modifies code and tests. Dispatched by `/graph-engineer` Node 4. Enforces all permanent CLAUDE.md rules during implementation.

## MCP servers (usage playbook)

- **`sentry`** (org `think360-n0`, project `python-fastapi`, region `us.sentry.io`) — **Use every sprint.** Before writing a cert report, run `search_issues(query="is:unresolved", period="7d")`. If any hits touch the sprint scope, block certification until triaged.
- **`context7`** — Anytime touching FastAPI / httpx / Pydantic / Supabase-py APIs.
- **`supabase`** — pgvector / FTS query prototyping, RLS review.
- **`asana`** — Phase 3 escalation work.
- **`github`** — PR review, CI status, issue triage.

## Hooks active (`.claude/settings.json`)

- `PreToolUse` on Edit/Write asks confirmation before modifying `exclude_escalation` in `case_engine/knowledge/rag_adapter.py`.
- `PostToolUse` on `tests/test_sprint*.py` edits prints a reminder to run that test file.

## Certification workflow (canonical order)

1. `/sot-check <layer>` — pull the relevant SOT.
2. Implement inside existing package boundaries. Prefer refactor over duplication.
3. Write `tests/test_sprint<N>_<topic>.py` (section-based A, B, C…).
4. `/regression` — verify Sprint 2.28.x + 2.46 + 2.47 + 2.48 + prior + newest scope.
5. Dispatch **`freshdesk-safety-reviewer`** if the sprint touched any Freshdesk write path.
6. Dispatch **`architecture-drift-corrector`** to verify Blueprint alignment.
7. Dispatch **`enterprise-code-reviewer`** to verify code quality.
8. Query **Sentry** for open issues in scope. Block cert if any relevant open issue exists.
9. Dispatch **`sprint-auditor`** with the sprint number for final Ready/Blocked verdict.
10. `/sprint-cert <X.Y.Z>` — write the certification report.
11. `/remember` — update handoff.

## External state pointers

- SOT docs: `C:/Users/Umair.Alam/Desktop/kwikid_support_system/Source_Of_Truth/` (one level ABOVE this repo).
- Auto-memory: `C:/Users/Umair.Alam/.claude/projects/C--Users-Umair-Alam-Desktop-kwikid-support-system/memory/`.
- Handoff note: `.remember/remember.md` at repo root.
- Sentry: https://think360-n0.sentry.io — project `python-fastapi`.