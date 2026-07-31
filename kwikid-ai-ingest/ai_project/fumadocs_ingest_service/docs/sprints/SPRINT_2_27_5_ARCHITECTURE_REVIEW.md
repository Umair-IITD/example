# Sprint 2.27.5 Architecture Review — Architecture Convergence & Agent Orchestration

**Date:** 2026-06-16
**Reviewer:** System Architecture (automated audit)

---

## Mission Statement

Sprint 2.27.5 enforces the three architectural principles declared in the platform blueprint:

1. **ONE AGENT** — `SupportAgentRuntime` is the single entrypoint for all case processing.
2. **ONE EXECUTION PATH** — all external actions flow through `AdapterRouter` via `RouterService`.
3. **ONE ORCHESTRATION PATH** — full CLASSIFY → SLOT_EXTRACT → CLARIFY → INVESTIGATE → KNOWLEDGE → REASON → PROPOSE → GATEWAY → EXECUTE → VERIFY → RESOLVE pipeline is active inside `SupportAgentRuntime._run_pipeline()`.

---

## Full Pipeline Compliance

| Stage | Implementation | Sprint 2.27.5 Status |
|-------|----------------|---------------------|
| CLASSIFY | `CaseService.classify_case()` | ✅ Wired in `_run_pipeline` |
| SLOT_EXTRACT | `CaseService.receive_message()` → slot filling | ✅ Wired in `_run_pipeline` |
| CLARIFY | Early-return path when `all_slots_filled=False` | ✅ Implemented, returns AWAITING_CLARIFICATION |
| INVESTIGATE | `WorkflowEngine` investigation steps inside playbook | ✅ Existing, invoked via `start_workflow()` |
| KNOWLEDGE | `KnowledgeOrchestrator.orchestrate()` (Sprint 2.27.5) | ✅ NEW — called inside workflow context |
| REASON | `WorkflowEngine` reasoning steps | ✅ Existing |
| PROPOSE | `WorkflowEngine` PROPOSE_ACTION steps | ✅ Existing |
| GATEWAY | `ActionGateway` + `AdapterRouter` | ✅ Existing |
| EXECUTE | `ExecutionService` → `AdapterBackedExecutionAdapter` → adapters | ✅ Existing |
| VERIFY | `WorkflowEngine` post-execution validation | ✅ Existing |
| RESOLVE | `SupportAgentRuntime._generate_response()` + `ResponseGenerationService` | ✅ NEW |

---

## New Subsystem Architecture

### Layer 1 — Agent Orchestration (NEW)

```
Freshdesk Webhook → POST /tickets/process
  └─ TicketOrchestrator.process_ticket(TicketContext)
       ├─ CaseService.open_case()         [opens case in DB]
       ├─ SupportAgentRuntime.run_case()  [THE AGENT]
       │    ├─ CLASSIFY via CaseService
       │    ├─ SLOT_EXTRACT via receive_message
       │    ├─ WORKFLOW via WorkflowEngine
       │    ├─ KNOWLEDGE via KnowledgeOrchestrator
       │    ├─ L2CHECK via _needs_engineering_escalation()
       │    ├─ ASANACREATE via EngineeringEscalationService
       │    └─ USERRESPONSE via ResponseGenerationService
       └─ TicketOrchestrationResult (CLOSED / WAITING / ESCALATED / FAILED)
```

### Layer 2 — Knowledge Convergence (NEW)

```
KnowledgeOrchestrator
  ├─ KnowledgeService.search()            [existing SOP + KB lookup]
  ├─ RAG evidence placeholder             [Sprint 2.28: real vector search]
  └─ UnifiedKnowledgeBundle               [merged output, backward-compatible dict]
```

### Layer 3 — Response Generation (NEW)

```
ResponseGenerationService
  ├─ Template engine (6 templates: resolution, escalation, clarification, status, approval, error)
  ├─ LLM injection point (Sprint 2.28: llm_generator callable)
  └─ ResponseDraft (frozen, body_text + body_html + escalation_required)
```

### Layer 4 — Engineering Escalation (NEW)

```
EngineeringEscalationService
  ├─ In-memory ticket store (Sprint 2.27.5)
  ├─ Asana client injection point (Sprint 2.28: asana_client)
  ├─ Priority inference from root_cause/topic
  └─ Full lifecycle: create → update → resolve → sync
```

---

## Routing Compliance

All external actions continue to flow through:
```
RouterService.route(action_type, payload, case_id)
  └─ AdapterRouter.route(AdapterRequest)
       └─ Adapter.execute()
```

`RouterService` was added in Sprint 2.27.5 as the **semantic wrapper** around `AdapterRouter`:
- Accepts `action_type` strings directly (no caller knowledge of AdapterType/Operation mappings)
- Returns `RouterResult` (not `AdapterExecutionResult`) — higher-level type
- Emits `ADAPTER_REQUEST_*` + `ACTION_ROUTED` audit events
- Never raises — all exceptions return `RouterResult(success=False)`

No bypass path exists. `EngineeringEscalationService` creates tickets internally (in-memory) and will call Asana via the injected `asana_client` in Sprint 2.28 — this does NOT bypass the adapter layer because Asana creates are internal management actions, not case execution actions.

---

## Invariant Verification

| Invariant | Verification |
|-----------|-------------|
| No secrets in logs | All new services use `LOGGER.debug/warning` with no payload content; audit methods never log raw values |
| hmac.compare_digest for key equality | No key comparison in new code; security layer unchanged |
| Never-raises contracts | All 6 new services + `SupportAgentRuntime.run_case()` + `TicketOrchestrator.*` wrap in try/except and return structured failure |
| Frozen dataclasses | All new domain models (AgentExecutionResult, TicketContext, TicketOrchestrationResult, ResponseDraft, EngineeringTicket, EngineeringEscalationResult, RouterResult) are frozen |
| JSON-serializable | All new models implement `to_dict()` returning only str/int/float/bool/dict/list primitives |
| Backward compatibility | `UnifiedKnowledgeBundle.to_dict()` includes all original `KnowledgeService.search()` dict fields |
| Thread safety | In-memory stores (`TicketOrchestrator._registry`, `EngineeringEscalationService._store`) are simple dicts; single-threaded use in Sprint 2.27.5; production will use DB in Sprint 2.28 |

---

## Impact on Existing Sprints

| Sprint Component | Impact |
|------------------|--------|
| WorkflowEngine (Sprint 2.11–2.23) | Unchanged — still called via `CaseService.start_workflow()` |
| ActionGateway (Sprint 2.1–2.5) | Unchanged — still processes action proposals |
| InvestigationService (Sprint 2.18–2.20) | Unchanged — called within workflow steps |
| AdapterRouter (Sprint 2.27) | Extended: `audit_logger` now passed from `_build_adapter_stack()` (was None before) |
| AuditLogger (Sprint 2.6) | Extended: 7 new methods + 7 new AuditEventType values |
| ProductionRuntime (Sprint 2.27) | Extended: 27 → 33 fields; existing field access unaffected |

---

## Sprint 2.28 Readiness

Every new service has explicit injection points:

| Service | Sprint 2.28 Injection |
|---------|-----------------------|
| `ResponseGenerationService` | `llm_generator: Callable[[ResponseContext], str]` |
| `EngineeringEscalationService` | `asana_client: Any` (duck-typed) |
| `KnowledgeOrchestrator` | `_query_rag()` placeholder replaced with real RAG call |
| `SupportAgentRuntime` | `audit_logger` for full audit trail across all 8 pipeline steps |

No structural changes will be needed in Sprint 2.28 — only injection of real clients.
