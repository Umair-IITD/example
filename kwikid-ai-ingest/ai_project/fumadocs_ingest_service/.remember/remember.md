# Handoff

## State
Sprint 2.53 Wave 4A (Intelligence Runtime Wiring) — CERTIFIED. Branch: `major-architecture-change`.
Runtime now executes full Blueprint L1 pipeline end-to-end: Evidence → Knowledge → Prompt → LLM → Reasoning → Observation → Reply Draft → Action Proposal, without interruption. 1238/1238 tests pass (36 new Wave 4A tests + 1202 regression, zero failures).
Files changed: `intelligence/orchestrator.py`, `case_engine/runtime/support_agent_runtime.py`, `runtime/assembly.py`, `app/main.py`, `.env.example`, `pytest.ini` (new), `tests/test_sprint253_wave4a_wiring.py` (new).
Cert: `sprint-2-5-3-wave-4a.md` at project root.
Also installed pytest-asyncio 1.4.0 (was missing — Sprint 2.53 async tests were silently skipped before).

## Next
1. **Sprint 2.54 — Action Gateway wiring.** Route `IntelligenceResult.action_proposals` into the existing Sprint 2.5 ActionGateway (risk classification → SAFE auto-execute vs REVERSIBLE/HIGH approval routing). Never bypass. Wave 4A produces proposals; execution is Wave 4B/Sprint 2.54.
2. **20 manual ops actions** from `production-readiness-review.md §8` still outstanding — unchanged.
3. Optional: Add `intelligence_orchestrator` to `app/startup_validator.py` 9-check validator for STARTUP_READY reporting.
4. Optional: Move `_run_intelligence`'s `_intelligence._client = None` reset into `IntelligenceOrchestrator.close()` semantics (cleaner API).

## Context
- **Sync/async bridge:** SupportAgentRuntime is sync (called from BackgroundTasks via anyio thread pool). `orchestrate()` is async. Bridge: reset `_client` to None before every call → `asyncio.run(orchestrate())` → close in `finally`. httpx.AsyncClient CANNOT be shared across `asyncio.run()` calls (event-loop-bound). This pattern MUST be preserved.
- **Two knowledge sources** both flow to `LLMContext.retrieved_chunks`: `workflow_context.knowledge_result.chunks` (Sprint 2.30.1 KnowledgeOrchestrator) + `investigation.knowledge_entries` (Sprint 2.46 InvestigationOrchestrator). `_to_retrieved_chunk()` normalizes both.
- **14 sprint-required boundary tags** all fire: ENTER/EXIT_INTELLIGENCE (in SupportAgentRuntime), ENTER/EXIT_PROMPT+LLM+REASONING+OBSERVATION+REPLY+ACTION_PROPOSAL (in intelligence/orchestrator.py). Complement (not replace) the 22 canonical TRACE_XX tags — both sets emit at WARNING.
- **Response override:** if `IntelligenceResult.customer_reply` is present, `SupportAgentRuntime._response_from_intelligence()` produces a response_draft dict shaped like the legacy `ResponseGenerationService` output. Falls back to legacy templating on None/missing/empty. Downstream `FreshdeskResponseService` + `ReplySafetyGate` + `ClosureFieldGuard` unchanged.
- **L2 escalation:** reasoning.outcome=ESCALATE forces `needs_l2=True` in the L2CHECK step. Blueprint §13 Reasoning Engine "Recommended Escalation" now honored.
- **PII:** context_builder masks phone (`*****2923`) + email (`***@domain`); intelligence trace sanitizer redacts @, JWT prefix `eyj`, bearer, api_key, token=, pan, aadhaar, values >128 chars. `test_B4` guards this.
- **Never-raises:** `_run_intelligence` catches every exception → returns None → emits EXIT_INTELLIGENCE with status=ERROR:<name>. Verified by `test_G2`. Legacy path is used when intelligence returns None.
- **pytest.ini added** — `asyncio_mode=auto` so pytest-asyncio backend runs without per-invocation flag. Suppresses two known pydantic v2 deprecation warnings.
- Permanent rules still active: exclude_escalation=True (Sprint 2.47), sole ResponseService write path (2.28+2.48), ClosureFieldGuard on status=4/5 (2.48), ReplySafetyGate on /reply (2.48), InvestigationOrchestrator sole investigation entry (2.46), NEW: IntelligenceOrchestrator sole LLM-reasoning entry (Wave 4A).
