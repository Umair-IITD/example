# Sprint 2.54 — Pipeline Wiring & Slot Extraction Certification

**Date:** 2026-07-16  
**Branch:** major-architecture-change  
**Scope:** Wire existing Sprint 2.50/2.51/2.53 components into the production runtime so that one real Freshdesk ticket traverses the complete pipeline up to the Observation Generator stop-point. Fix Slot Extraction terminal state. Add 8 Blueprint trace tags.

---

## 1. Blueprint Reconciliation Table

| Blueprint Node          | Implementation                                     | Status |
|-------------------------|----------------------------------------------------|--------|
| CLASSIFY                | `CaseService.receive_message()` topic extraction   | ✅ Unchanged |
| SLOT_EXTRACT            | `CaseService.receive_message()` enum slot fill + **new pre-fill loop** via `_extract_free_form_slots()` | ✅ Fixed |
| CLARIFY                 | `CaseService.receive_message()` → clarification Q when slots missing | ✅ Unchanged |
| INVESTIGATE → PLANNER   | `InvestigationPlanner.plan()` + `ENTER/EXIT_INVESTIGATION_PLANNER` trace | ✅ Wired |
| INVESTIGATE → COLLECT   | `EvidenceCollector.collect()` + `ENTER/EXIT_EVIDENCE_COLLECTION` trace | ✅ Wired |
| INVESTIGATE → TOOL      | `ToolExecutor.execute()` + `ENTER/EXIT_TOOL_EXECUTION` trace | ✅ Wired |
| HYBRIDRAG               | `SOPMatcher.match()` + `ENTER/EXIT_RETRIEVAL` trace | ✅ Wired |
| REASONING               | `IntelligenceOrchestrator` (Wave 4A, unchanged)    | ✅ Unchanged |
| ticket_updated resume   | Handler `else:` branch → `resume_ticket()` for OPEN/PENDING | ✅ Wired |
| Production Tools        | `register_unity_tools()` + `register_metrics_tools()` in `assembly.py` | ✅ Wired |

---

## 2. Architecture Reconciliation Table

| Layer            | Component                                 | Sprint | Wired? |
|------------------|-------------------------------------------|--------|--------|
| Tool Registry    | `ProductionToolRegistry` via `ToolRegistry()` | 2.52 | ✅ assembly.py step 2 |
| Unity Tools      | `register_unity_tools()` (5 adapters)     | 2.51   | ✅ assembly.py + fallback |
| Metrics Tools    | `register_metrics_tools()` (2 adapters)   | 2.50   | ✅ assembly.py + fallback |
| Investigation    | `InvestigationService` (planner → collector → RCA → obs) | 2.18 | ✅ full trace |
| Knowledge        | `KnowledgeService` (matcher → recommendation) | 2.20 | ✅ retrieval trace |
| Intelligence     | `IntelligenceOrchestrator`                | 2.53   | ✅ unchanged |
| Freshdesk Resume | `FreshdeskTicketUpdatedHandler` → `resume_ticket()` | 2.54 | ✅ new |

---

## 3. Dependency Graph (Sprint 2.54 additions)

```
_extract_free_form_slots(topic, message_text)
  └── regex patterns for phone_number / session_id / document_id / agent_id / endpoint_url
      └── pre-fill loop → CaseService.receive_message(slot_name=k, slot_value_str=v)
          └── WorkflowEngine → InvestigationService
              ├── ENTER_INVESTIGATION_PLANNER
              ├── InvestigationPlanner.plan()
              ├── EXIT_INVESTIGATION_PLANNER
              ├── ENTER_EVIDENCE_COLLECTION
              ├── EvidenceCollector.collect()
              │   ├── ENTER_TOOL_EXECUTION (per step)
              │   ├── ToolExecutor.execute() → production Unity/Metrics adapters
              │   └── EXIT_TOOL_EXECUTION (per step)
              └── EXIT_EVIDENCE_COLLECTION
                  └── KnowledgeService._search()
                      ├── ENTER_RETRIEVAL
                      ├── SOPMatcher.match()
                      └── EXIT_RETRIEVAL

FreshdeskTicketUpdatedHandler.handle()
  ├── awaiting_customer=True → resume_ticket() [TRACE_FD_05/06 event_type=customer_reply_resume]
  └── awaiting_customer=False + lifecycle=OPEN/PENDING → resume_ticket() [event_type=customer_reply_continue]

assembly._build_workflow_services() step 2:
  ToolRegistry() → register_unity_tools() [fallback: mocks] → register_metrics_tools()
  └── ToolExecutor(registry) → InvestigationService
```

---

## 4. Files Created

| File | Description |
|------|-------------|
| `tests/test_sprint254_pipeline_wiring.py` | 40 tests covering sections A–G |

---

## 5. Files Modified

| File | Change |
|------|--------|
| `case_engine/runtime/support_agent_runtime.py` | Added `_extract_free_form_slots()` + pre-fill loop in `_run_pipeline()`. ENTER_PREFILL_SLOT trace (slot name only, no value — PII discipline). |
| `runtime/assembly.py` | Replaced `ToolRegistry.build_default()` (mocks) with `register_unity_tools()` + `register_metrics_tools()` + graceful fallback |
| `case_engine/investigation/service.py` | Added `ENTER/EXIT_INVESTIGATION_PLANNER` and `ENTER/EXIT_EVIDENCE_COLLECTION` traces |
| `case_engine/investigation/_collector_sprint218.py` | Added `ENTER/EXIT_TOOL_EXECUTION` traces around `ToolExecutor.execute()` |
| `case_engine/knowledge/service.py` | Added `ENTER/EXIT_RETRIEVAL` traces around `SOPMatcher.match()` |
| `freshdesk/handlers.py` | Added "continue" path in `FreshdeskTicketUpdatedHandler` else-branch for OPEN/PENDING conversations |
| `tests/test_sprint249_freshdesk_certification.py` | Updated `test_G3` to verify clarification-resume absent (not all of TRACE_FD_05) — corrects Sprint 2.54 behavioural extension |

---

## 6. Test Counts

| Section | Tests | Pass |
|---------|-------|------|
| A — Slot pre-extraction | 12 | 12 |
| B — Production tool registry wiring | 5 | 5 |
| C — Trace tag verification | 4 | 4 |
| D — End-to-end investigation execution | 3 | 3 |
| E — Ticket-updated multi-turn resume | 5 | 5 |
| F — Freshdesk 404 graceful handling | 3 | 3 |
| G — Regression guard | 8 | 8 |
| **Total** | **40** | **40** |

---

## 7. Regression Counts

**Full suite baseline (Sprint 2.53 Wave 4A):** ~131 pre-existing failures (sprint216/219/224/228x/229x/2292/golden/stackoverflow — documented in Sprint 2.47 handoff + CLAUDE.md).

**This sprint introduced:** 1 new test failure (`test_sprint249::TestG_UpdateResumeTraces::test_G3`) — caused by the new "customer_reply_continue" path firing `TRACE_FD_05` for OPEN conversations. Fixed by tightening the assertion to verify the *clarification-resume* event type is absent (not TRACE_FD_05 entirely), which is the correct invariant.

**Net new regressions after Sprint 2.54:** 0

---

## 8. Trace Tags Implemented

| Tag | Location | Fires when |
|-----|----------|------------|
| `ENTER_INVESTIGATION_PLANNER` | `investigation/service.py` | Before `InvestigationPlanner.plan()` |
| `EXIT_INVESTIGATION_PLANNER` | `investigation/service.py` | After plan produced |
| `ENTER_EVIDENCE_COLLECTION` | `investigation/service.py` | Before `EvidenceCollector.collect()` |
| `EXIT_EVIDENCE_COLLECTION` | `investigation/service.py` | After bundle assembled |
| `ENTER_TOOL_EXECUTION` | `investigation/_collector_sprint218.py` | Before each `ToolExecutor.execute()` |
| `EXIT_TOOL_EXECUTION` | `investigation/_collector_sprint218.py` | After each tool returns |
| `ENTER_RETRIEVAL` | `knowledge/service.py` | Before `SOPMatcher.match()` |
| `EXIT_RETRIEVAL` | `knowledge/service.py` | After SOPMatcher returns |
| `ENTER_PREFILL_SLOT` | `runtime/support_agent_runtime.py` | Per slot pre-fill (name only, no value) |

---

## 9. Bugs Found During Sprint

| Bug | Root Cause | Fix |
|-----|-----------|-----|
| Slot Extraction terminal state: free-form slots (phone_number, session_id) never filled → pipeline short-circuits to CLARIFICATION | `receive_message()` only handles enum slots via `extract_from_text()`; no regex-based extraction for typed slots | Added `_extract_free_form_slots()` + pre-fill loop before main `receive_message()` call |
| Mock tools in production assembly | `_build_workflow_services()` step 2 called `ToolRegistry.build_default()` which loaded Sprint 2.17 mocks | Replaced with `register_unity_tools()` + `register_metrics_tools()` with fallback |
| ticket_updated "continue" not wired | Handler's `else:` branch (non-clarification replies) only wrote audit event, never called `resume_ticket()` | Added OPEN/PENDING lifecycle guard + `resume_ticket()` call in the else path |
| `_extract_free_form_slots` document_id regex too broad | `[A-Z]{2}[0-9A-Z]{6,18}` matched word "document" (2 letters + 6+ letters) | Tightened to `[A-Z]{1,4}\d[0-9A-Z]{5,17}` (requires digit after letter prefix) |
| Test E1-E5: `conversations.create()` called but only `get_or_create()` exists | API drift between test and ConversationStateStore | Fixed to `get_or_create(ticket_id, client_id)` |
| Test E1-E2: `latest_comment` at wrong nesting level in payload | `FreshdeskUpdateEvent.from_dict()` reads `latest_comment` from `freshdesk_webhook` inner dict | Moved `latest_comment` inside `freshdesk_webhook` key |
| Sprint 2.49 test_G3 regression | Our new "continue" path fires `TRACE_FD_05` for OPEN conversations, breaking old assertion | Updated to verify clarification-resume event_type absent (still valid invariant) |

---

## 10. Security Review

| Check | Status |
|-------|--------|
| `ENTER_PREFILL_SLOT` logs slot NAME only, never VALUE | ✅ confirmed (`_sn` only, `_sv` never logged) |
| `exclude_escalation=True` in `rag_adapter.py` | ✅ unchanged (test_G1 guards) |
| `FreshdeskResponseService` sole write path | ✅ confirmed (test_G2 guards; handlers.py patched route also uses orchestrator) |
| `ClosureFieldGuard` still importable | ✅ test_G3 |
| `ReplySafetyGate` still importable | ✅ test_G4 |
| `InvestigationOrchestrator` sole investigation entry | ✅ test_G5 |
| `IntelligenceOrchestrator` sole LLM entry | ✅ test_G8 |
| PII discipline in all new trace tags | ✅ slot names only; no emails, bodies, keys logged |

---

## 11. Permanent Certification

Sprint 2.54 is certified with the following permanent additions:

1. **`_extract_free_form_slots(topic, message_text)`** is the canonical pre-fill path for non-enum slots. It is called before `CaseService.receive_message()` in `SupportAgentRuntime._run_pipeline()`.

2. **`runtime/assembly.py` step 2** must always use `register_unity_tools()` + `register_metrics_tools()` (with fallback to mocks). Never revert to `ToolRegistry.build_default()`.

3. **8 Blueprint trace tags** (`ENTER/EXIT_INVESTIGATION_PLANNER`, `ENTER/EXIT_EVIDENCE_COLLECTION`, `ENTER/EXIT_TOOL_EXECUTION`, `ENTER/EXIT_RETRIEVAL`) fire at WARNING level at component boundaries. These are permanent instrumentation fixtures.

4. **`ENTER_PREFILL_SLOT` trace logs slot name only** — slot VALUE must never appear in any log line. Enforced by `test_G6_extract_free_form_slots_no_pii_in_slot_names`.

5. **`FreshdeskTicketUpdatedHandler`** resume logic: clarification replies (`awaiting_customer=True`) AND non-clarification replies on OPEN/PENDING conversations both call `orchestrator.resume_ticket()`. Sprint 2.49 `test_G3` invariant updated: clarification-resume event type absent (not TRACE_FD_05 entirely).

---

*Certified by: Claude Sonnet 4.6 on 2026-07-16*
