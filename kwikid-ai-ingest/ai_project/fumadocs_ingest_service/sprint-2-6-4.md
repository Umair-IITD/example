# Sprint 2.64 Certification Report
## Final Go-Live Audit — ReplySafetyGate Wiring Verification + Knowledge Layer SOP Fix + Master E2E Test Suite

**Date:** 2026-07-31  
**Branch:** `major-architecture-change`  
**Certifier:** Claude Code (claude-sonnet-4-6)  
**Pipeline:** 6-node Graph Engineering (/graph-engineer) — Final Go-Live Audit  

---

## §1 — Blueprint Reconciliation Table

| Blueprint Req | §Ref | Implementation | Tests | Status |
|---|---|---|---|---|
| ReplySafetyGate must gate every customer reply | CLAUDE.md / §20A-20B | `_gated_customer_reply()` in freshdesk.py (Sites 1-3) + inline gate in asana.py (Site 4) | TestC_ReplySafetyGateWiring (4), TestK/TestM in test_sprint2631 (10) | ✅ |
| ReplySafetyGate instantiated in app.state at startup | §20A-20B | `app/main.py` — `ReplySafetyGate(kill_switch=...)` wired as shared instance | TestM_E2EGateWiring (4) | ✅ |
| Fail-CLOSED if gate absent: draft note instead of auto-send | CLAUDE.md safety | `_gated_customer_reply()`: posts `build_draft_reply_note` when `safety_gate is None` | TestC1, TestD4, TestK1 | ✅ |
| Confidence threaded from LLM → handlers → route layer | §20A | `HandlerResult.response_confidence`; extracted from `response_draft["confidence"]` | TestK3, TestM2 | ✅ |
| Kill switch env var: REPLY_SAFETY_KILL_SWITCH | ops requirement | `.env.example` documented; `ReplySafetyGate(kill_switch=True)` blocks all sends | TestC4, TestD3, TestJ2 | ✅ |
| GUARD→GATE→SEND→CLOSE sequence in Asana closure | flow_diagram.mermaid §20B | `_handle_task_completed()`: ClosureFieldGuard → ReplySafetyGate → send_customer_reply → update_ticket_fields | TestD1–D5 | ✅ |
| KnowledgeService SOP content reaches LLM context | §14/§16 | `_run_intelligence()` fallback: `search_result.matches[:3].entry` → `_to_retrieved_chunk()` → `LLMContext.retrieved_chunks` | TestA1–A5 | ✅ |
| PII redacted BEFORE BM25 scoring (ordering invariant) | §35 / Sprint 2.60 | `parse_line()` calls `_redact_and_truncate(message)` then BM25 — `content` is always redacted | TestB1–B4 | ✅ |
| "1234" (PII value) not in curated log excerpt | §35 PII discipline | `_PII_KEY_PATTERN` matches `"aadhaar_number":"<value>"` → replaced with `[REDACTED]` | TestB4 | ✅ |
| FreshdeskResponseService sole write path | CLAUDE.md permanent rule | No direct FreshdeskClient calls in freshdesk.py / asana.py; all writes via `response_service.*()` | TestE2–E4 | ✅ |
| BackgroundTasks: route returns 200 synchronously | 10-sec webhook budget | `background_tasks.add_task()` for all processing; route returns immediately | TestE1 | ✅ |
| Idempotency: Freshdesk + Asana | §26 | `WebhookIdempotencyStore` (Freshdesk) + `AsanaEventIdempotencyStore` (Asana) | TestD5 | ✅ |
| L2 ticket lifecycle: observation → escalation → webhook closure | §20A-20B full loop | TestE2 (observation note), TestE3 (Asana link update), TestD1 (webhook closure) | TestE2, TestE3, TestD1 | ✅ |

---

## §2 — Architecture Reconciliation Table

| Prior Sprint Concern | Drift? | Evidence |
|---|---|---|
| ReplySafetyGate wired to all 4 autonomous reply sites | No drift — **was drift, now fixed** | `_gated_customer_reply()` covers Sites 1-3 in freshdesk.py; inline gate covers Site 4 in asana.py |
| KnowledgeService SOP content in LLM reasoning prompt | No drift — **was silent bug, now fixed** | `support_agent_runtime.py` now extracts from `search_result.matches[*].entry`; LLMContext.retrieved_chunks populated |
| FreshdeskResponseService sole write path (CLAUDE.md) | No drift | Verified by grep: no direct FreshdeskClient write calls outside response_service.py |
| ClosureFieldGuard gates every status=4 PUT | No drift | Verified in asana.py `_handle_task_completed()` and freshdesk/handlers.py |
| PII-before-BM25 ordering constraint (Sprint 2.60) | No drift | `parse_line()` in loki/relevance.py: redact+truncate THEN score |
| BackgroundTasks for all webhook processing | No drift | Freshdesk + Asana routes both use `background_tasks.add_task()` |
| NLU/NLG split (CLAUDE.md) | No drift | NLU: nlp_router.py; NLG: intelligence/orchestrator.py + observation generator |
| exclude_escalation=True in rag_adapter.py:83 | Not touched | Permanent security rule intact |
| cf_clients and cf_environment READ-ONLY | No drift | Not written anywhere in pipeline code |
| KNOWLEDGE_LOOKUP step fires KnowledgeService | No drift | WorkflowEngine._exec_knowledge_lookup() confirmed wired; result stored in workflow_context.knowledge_result |

---

## §3 — Dependency Graph

```
Sprint 2.63.1 additions:
  freshdesk/safety_gate.py  [unchanged — already correct]
  freshdesk/handlers.py  [HandlerResult.response_confidence; confidence extraction at 3 sites]
  api/routes/webhooks/freshdesk.py  [_get_safety_gate(); _gated_customer_reply(); Sites 1-3 wired]
  api/routes/webhooks/asana.py  [_get_safety_gate(); inline gate; Site 4 wired]
  app/main.py  [ReplySafetyGate(kill_switch=...) wired as app.state.reply_safety_gate]
    ↑ REPLY_SAFETY_KILL_SWITCH env var (documented in .env.example)

Sprint 2.64 fix:
  case_engine/runtime/support_agent_runtime.py::_run_intelligence()
    ├── _extract_workflow_knowledge(workflow_result)  → wf_knowledge dict
    ├── wf_knowledge["search_result"]["matches"][:3]  → top-3 SOP matches
    ├── match["entry"]  → {title, body, topic, ...}
    ├── _to_retrieved_chunk(merged, RetrievedChunk)  → RetrievedChunk
    └── LLMContext.retrieved_chunks  → prompt_builder → "RELEVANT KNOWLEDGE" section → LLM

No new packages. No circular imports introduced.
```

---

## §4 — Files Created

| File | Lines | Purpose |
|---|---|---|
| `tests/test_sprint264_master_e2e_validation.py` | 863 | Master E2E test suite: Knowledge Layer SOP extraction (A), PII redaction ordering (B), ReplySafetyGate wiring (C), Asana closure chain (D), full lifecycle (E) |
| `tests/test_sprint2631_reply_safety_gate_wiring.py` | ~350 | Sprint 2.63.1 unit + E2E: `_gated_customer_reply()` direct coverage (K) + real FastAPI Request pipeline (M) |

---

## §5 — Files Modified

| File | Change | Reason |
|---|---|---|
| `case_engine/runtime/support_agent_runtime.py` | +12 lines: fallback extraction from `search_result.matches[:3]` | **Bug fix**: `KnowledgeResult.to_dict()` has no `chunks` key; SOP content lives at `search_result.matches[*].entry.body` |
| `freshdesk/handlers.py` | `HandlerResult.response_confidence: float | None`; 3 extraction points from `response_draft["confidence"]` | Sprint 2.63.1: thread confidence to route layer for ReplySafetyGate |
| `api/routes/webhooks/freshdesk.py` | `_get_safety_gate()` accessor; `_gated_customer_reply()` funnel (Sites 1-3) | Sprint 2.63.1: wire gate to 3 Freshdesk reply sites |
| `api/routes/webhooks/asana.py` | `_get_safety_gate()` accessor; inline GUARD→GATE check (Site 4) | Sprint 2.63.1: wire gate to Asana resolution reply site |
| `app/main.py` | `ReplySafetyGate(kill_switch=...)` → `app.state.reply_safety_gate` | Sprint 2.63.1: shared instance with kill-switch env var |
| `.env.example` | `REPLY_SAFETY_KILL_SWITCH=false` documented | Sprint 2.63.1: emergency stop for all 4 reply sites |
| `Source_Of_Truth/.../SUPPORT_OPERATIONS_BLUEPRINT.md` | Bumped to v1.6; §20B rewritten with GUARD/GATE/MANUALNOTE sequence | Sprint 2.63.1: SOT matches implementation |
| `Source_Of_Truth/.../flow_diagram.mermaid` | GUARD/GATE nodes added to async-resolution block; §20A edge annotated | Sprint 2.63.1: flow diagram reflects 2-step safety chain |
| `tests/test_sprint263_asana_webhook_receiver.py` | Section J added (4 tests: no-gate fails closed, kill-switch, duplicate, allowed path); TestG1 updated to pass ReplySafetyGate | Sprint 2.63.1: Asana resolution-reply gate coverage |

---

## §6 — Test Counts

| Suite | Section | Tests | Pass | Notes |
|---|---|---|---|---|
| test_sprint264_master_e2e_validation.py | A Knowledge Layer | 5 | 5 | SOP extraction from search_result.matches |
| test_sprint264_master_e2e_validation.py | B PII Redaction | 4 | 4 | aadhaar/pan/non-PII/ordering |
| test_sprint264_master_e2e_validation.py | C ReplySafetyGate | 4 | 4 | no-gate/allow/block/kill-switch |
| test_sprint264_master_e2e_validation.py | D Asana Closure Chain | 5 | 5 | happy path/guard block/gate block/no-gate/idempotency |
| test_sprint264_master_e2e_validation.py | E Full Lifecycle | 4 | 4 | 200 enqueue/observation note/escalation link/updated resume |
| **Sprint 2.64 subtotal** | | **22** | **22** | 4.86s |
| test_sprint2631_reply_safety_gate_wiring.py | K+M | 10 | 10 | Sprint 2.63.1 gate unit + E2E |
| test_sprint263_asana_webhook_receiver.py | J (new) | 4 | 4 | Sprint 2.63.1 Asana gate |
| **Sprint 2.63.1 subtotal** | | **14** | **14** | |
| **Sprint 2.64 scope total** | | **36** | **36** | 0 failures |

---

## §7 — Regression Counts

| Suite | Tests | Pass | Fail | Run |
|---|---|---|---|---|
| test_sprint248_freshdesk_integration.py | (included below) | | | |
| test_sprint260_loki_integration.py | (included below) | | | |
| test_sprint261_l1_final_e2e.py | (included below) | | | |
| test_sprint262_l1_final_validation.py | (included below) | | | |
| test_sprint263_asana_webhook_receiver.py | (included below) | | | |
| test_sprint2631_reply_safety_gate_wiring.py | (included below) | | | |
| test_sprint2632_asana_ticket_content.py | (included below) | | | |
| test_sprint264_master_e2e_validation.py | (included below) | | | |
| **Combined (2.48+2.60+2.61+2.62+2.63+2.63.1+2.63.2+2.64)** | **462** | **462** | **0** | **37.20s** |

Pre-existing known failures (none in this scope): `test_sprint253`, `test_sprint256` (version 1.1.0 vs 1.0.0), `test_sprint2281` (2 tests — Sprint 2.30.1 interface drift), ~5 orchestrator-fixture-signature-drift failures. None triggered in this regression run.

---

## §8 — Execution Timing

| Run | Command | Wall-clock |
|---|---|---|
| Sprint 2.64 scope only | `pytest tests/test_sprint264_master_e2e_validation.py` | 4.86s |
| Full regression (2.48–2.64) | 8-file combined run | 37.20s |

---

## §9 — Bugs Found During Loop Engineering

### Bug 1: ReplySafetyGate never instantiated or invoked (Sprint 2.63.1 — Cowork discovery)

**Severity**: Critical — autonomous customer replies were sent without confidence check, kill-switch, or duplicate detection.

**Discovery**: Independent audit of Sprint 2.63 diff by Umair during a Cowork session. Confirmed by source review: `freshdesk/safety_gate.py` (Sprint 2.48) was fully implemented but never instantiated in `app/main.py` and never called at any of the 4 autonomous `send_customer_reply()` sites.

**Root cause**:
1. Site 1 (escalation reply, freshdesk.py): gate never wired.
2. Sites 2-3 (orchestrator response_draft, freshdesk.py): `freshdesk/handlers.py` extracted only `body_html`/`body_text` from `response_draft`, discarding the `confidence` key that `support_agent_runtime.py` already populated — so even if a gate had been called, confidence was unavailable.
3. Site 4 (Asana resolution reply, asana.py): Sprint 2.63 added this site without the gate.

**Impact**: All 4 reply sites have now been gated. Fail-CLOSED design: absent gate → draft note, not unprotected send.

---

### Bug 2: KnowledgeResult SOP content never reaching LLM (Sprint 2.64 discovery)

**Severity**: High — LLM was reasoning without SOP guidance for every ticket; "RELEVANT KNOWLEDGE" block in the prompt was always "(no SOP or knowledge chunks retrieved)".

**Discovery**: Line-by-line audit of `support_agent_runtime.py::_run_intelligence()` during this sprint's Go-Live Audit.

**Root cause**: `KnowledgeResult.to_dict()` (case_engine/knowledge/models.py) serializes SOP content at `search_result.matches[*].entry.body` — not under a `chunks` key. `_run_intelligence()` looked only at:
```python
for entry in (wf_knowledge.get("chunks") or []):  # key never exists
```
and:
```python
for entry in (investigation.get("knowledge_entries") or []):  # also always empty
```
Both loops iterated zero elements. `LLMContext.retrieved_chunks` was always `[]`.

**Impact**: Every LLM call since Sprint 2.53 Wave 4A wiring (when KnowledgeService was integrated) received no SOP content. The fix extracts from the correct path and resolves this silently.

---

### Bug 3: Test B4 wrong Loki line format and wrong API signature

**Severity**: Low — test-only issue.

**Root cause**: Test used JSON object format (`{"timestamp":...,"message":"..."}`) for Loki lines, but `_LINE_RE` expects `[ts] (meta) message` format. In the JSON format, `aadhaar_number` appeared inside an escaped JSON string where the PII pattern's `"` delimiter couldn't match `\"`. Also used `query=` and `budget_chars=` kwargs when the actual params are positional `ticket_query` and `char_budget`.

---

## §10 — Fixes Applied

### Fix 1: Sprint 2.63.1 — ReplySafetyGate wiring (Cowork session)

**Files**: `freshdesk/handlers.py`, `api/routes/webhooks/freshdesk.py`, `api/routes/webhooks/asana.py`, `app/main.py`, `.env.example`

**Summary**:
- `app/main.py`: `ReplySafetyGate(kill_switch=REPLY_SAFETY_KILL_SWITCH)` instantiated once as `app.state.reply_safety_gate` (shared for duplicate-hash dedup correctness). Graceful degradation to `None` on exception (logged); `None` → fail-CLOSED at all sites.
- `freshdesk/handlers.py`: `HandlerResult.response_confidence: float | None`; both handlers extract `confidence` from `response_draft`; clarification NLP slot prompt assigns `confidence=1.0` (deterministic).
- `api/routes/webhooks/freshdesk.py`: `_get_safety_gate(request)` accessor + `_gated_customer_reply()` single funnel. Site 1 passes `confidence=1.0` (fixed template). Sites 2-3 pass `result.response_confidence` (may be `None` → gate blocks). None gate → post draft note + return, no unprotected send.
- `api/routes/webhooks/asana.py`: `_get_safety_gate(request)` accessor. `_handle_task_completed()` gains `safety_gate` param; inline GUARD→GATE sequence: ClosureFieldGuard first, ReplySafetyGate second with `confidence=1.0` (fixed template). Gate-blocked → draft note, ticket stays open.

### Fix 2: Sprint 2.64 — KnowledgeResult SOP extraction fallback

**File**: `case_engine/runtime/support_agent_runtime.py` (+12 lines at ~line 1040)

```python
# KnowledgeResult.to_dict() stores SOP content in
# search_result.matches[*].entry — not under a "chunks" key. Extract
# top-3 matches as RetrievedChunks so the LLM receives SOP body text.
if not knowledge_chunks:
    for match in (wf_knowledge.get("search_result") or {}).get("matches", [])[:3]:
        if isinstance(match, dict):
            entry = match.get("entry") or {}
            merged = {**entry, "score": match.get("relevance_score", 0.0)}
            chunk = _to_retrieved_chunk(merged, RetrievedChunk)
            if chunk is not None:
                knowledge_chunks.append(chunk)
```

### Fix 3: Sprint 2.64 — Test B4 Loki line format and API signature

**File**: `tests/test_sprint264_master_e2e_validation.py`

Changed raw_lines to `[ts] (meta) message` format so the message field is unescaped and the PII pattern matches. Changed `query=` → positional `ticket_query`; `budget_chars=` → `char_budget=`. Changed return value handling to `lines, _stats = extract_relevant_lines(...)`.

---

## §11 — Permanent Certification

```
╔══════════════════════════════════════════════════════════════════════════╗
║        SPRINT 2.64 — FINAL GO-LIVE AUDIT — PERMANENTLY CERTIFIED       ║
╠══════════════════════════════════════════════════════════════════════════╣
║  ✅ ReplySafetyGate wired to all 4 autonomous customer reply sites      ║
║  ✅ Fail-CLOSED: absent gate → draft note, not unprotected send         ║
║  ✅ Confidence threaded: LLM → response_draft → HandlerResult → route   ║
║  ✅ Kill-switch: REPLY_SAFETY_KILL_SWITCH env var blocks all 4 sites    ║
║  ✅ GUARD→GATE→SEND→CLOSE sequence enforced in Asana closure path       ║
║  ✅ KnowledgeResult SOP extraction fixed: search_result.matches[*]      ║
║     .entry.body now reaches LLMContext.retrieved_chunks → LLM prompt    ║
║  ✅ PII-before-BM25 ordering invariant confirmed (parse_line verified)   ║
║  ✅ FreshdeskResponseService sole write path — no bypass found           ║
║  ✅ ClosureFieldGuard gates every status=4 PUT — confirmed               ║
║  ✅ BackgroundTasks for all webhook processing — 10-sec budget honored   ║
║  ✅ Idempotency: WebhookIdempotencyStore + AsanaEventIdempotencyStore    ║
║  ✅ L1+L2 full lifecycle test: ticket-created → obs → escalation →       ║
║     Asana webhook → customer reply → status=4 — all 22 tests pass       ║
║  ✅ Regression: 462/462 pass, 0 regressions across Sprints 2.48–2.64   ║
║  ✅ SOT (Blueprint v1.6 + flow_diagram.mermaid) matches implementation  ║
║  ✅ exclude_escalation=True in rag_adapter.py:83 — intact, unchanged    ║
╠══════════════════════════════════════════════════════════════════════════╣
║  Sprint 2.64 scope tests: 22/22 pass (4.86s)                           ║
║  Sprint 2.63.1 scope tests: 14/14 pass                                 ║
║  Combined regression: 462/462 pass (37.20s, 0 failures, 1 warning)     ║
║  Bugs found: 3 (2 critical/high production bugs fixed; 1 test-only)     ║
║  Files created: 2 (test files)                                          ║
║  Files modified: 9 (runtime, handlers, routes, main, env, SOT docs)    ║
╚══════════════════════════════════════════════════════════════════════════╝
```

---

## §12 — Manual Action Checklist

These items remain outstanding and block full PRODUCTION mode:

| # | Action | Owner | Status |
|---|---|---|---|
| §4.4 | Create `ai.support@getkwikid.com` Freshdesk agent account (dedicated AI agent identity) | Umair (admin action) | ⚠️ PENDING — only remaining code-external blocker |
| §20B.wh | Register Asana webhook: `python scripts/register_asana_webhook.py <live-url>/webhooks/asana/task-completed` | Umair (needs live server + ngrok/tunnel URL) | ⚠️ PENDING — requires live server |
| Deploy | Set `SUPPORT_AGENT_MODE=PRODUCTION` in production `.env` after §4.4 + webhook registration | Umair | ⚠️ PENDING — do NOT enable until both above are complete |
| Monitor | After deploy, monitor Sentry (`think360-n0`, project `python-fastapi`) for first 24h of live traffic | Umair | ⚠️ Post-deploy |
| Verify | Send test ticket through Freshdesk → confirm observation note appears within 60s | Umair | ⚠️ Post-deploy |
| Verify | Manually complete a test Asana task → confirm customer reply + status=4 on Freshdesk ticket | Umair | ⚠️ Post-deploy |
