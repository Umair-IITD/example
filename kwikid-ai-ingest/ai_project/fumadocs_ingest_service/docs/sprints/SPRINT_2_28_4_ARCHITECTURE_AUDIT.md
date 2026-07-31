# SPRINT 2.28.4 — PRODUCTION ARCHITECTURE AUDIT
**Date:** 2026-06-24  
**Branch:** major-architecture-change  
**Auditor:** Claude Sonnet 4.6 (automated, supervised)  
**Authority:** Source_Of_Truth/ (flow_diagram.mermaid, SUPPORT_OPERATIONS_BLUEPRINT.md, freshdesk_integration.md)

---

## EXECUTIVE SUMMARY

Three generations of Freshdesk integration coexist in production. Only one (Sprint B3, `POST /freshdesk/webhook`) currently handles live traffic. It violates the Golden Path in two ways: it calls Freshdesk write APIs directly (bypassing the Action Gateway) and it bypasses the TicketOrchestrator entirely. The Sprint 2.28.1 routes (`POST /webhooks/freshdesk/ticket-created`) are mounted and receive requests but are missing three critical service wirings that prevent them from being operational. The Golden Path entry `POST /tickets/process` → TicketOrchestrator exists and is fully built but Freshdesk never calls it.

---

## SECTION 1 — CANONICAL PRODUCTION APP

**Entry point:** `app/main.py:2448`
```python
app = create_app()   # module-level singleton
```
Uvicorn serves `app.main:app`. All routes, lifespan startup, and `app.state` wirings are in `create_app()` (line 889). The `api/app.py` file is a pure re-export wrapper for test backward compatibility only — zero production logic.

**Lifespan sequence (in order):**
1. Supabase client initialized (`_sb_singleton`)
2. Embedder initialized (`_embedder_singleton`)
3. ChatGenerator initialized (`_generator_singleton`)
4. `app.state.playbook_registry`, `tool_registry`, `tool_executor`, `reasoning_engine` set
5. `app.state.case_service` set (CaseService singleton)
6. `app.state.investigation_service` set
7. Gateway stack built → `app.state.stack` set (ProductionRuntime from `runtime/assembly.py`)
8. All 19 workflow services promoted from `stack` to `app.state` (clarification_service through ticket_orchestrator)
9. Background loops started (action_worker_loop, action_watchdog_loop)

**What is NOT wired during lifespan:**
- `app.state.freshdesk_idempotency_store` — Sprint 2.28.1 store (falls back to in-memory instance per request)
- `app.state.freshdesk_conversation_store` — Sprint 2.28.1 store (falls back to in-memory instance per request)
- `app.state.freshdesk_rag_processor` — Sprint 2.29 deferred (returns None at runtime)
- `app.state.freshdesk_response_service` — Sprint 2.28.1 response service (returns None at runtime)

---

## SECTION 2 — FASTAPI ROUTE INVENTORY

All routes are registered via `create_app()`. The production app has **four distinct route families**:

### Family 1: RAG Core Routes (Sprint B1/B2/B3)
Registered via `_rag_router` (APIRouter, no prefix).

| Method | Path | Sprint | Status |
|--------|------|--------|--------|
| GET | `/health` | B0 | Active |
| GET | `/health/live` | B0 | Active |
| GET | `/health/ready` | B0 | Active |
| GET | `/metrics` | 2.10 | Active |
| POST | `/query` | B1 | Active |
| POST | `/chat` | B2 | Active |
| POST | `/rag/chat` | B3 | Active |
| POST | `/rag/chat/stream` | B3 | Active |
| POST | `/freshdesk/webhook` | B3 | **Active — PRODUCTION FRESHDESK ENTRY (see Section 4)** |
| POST | `/feedback` | B2 | Active |
| POST | `/train/chat` | B3 | Active |

### Family 2: Gateway Action Routes (Sprint 2.6+)
Registered via `api/routes/actions.py`, `worker.py`, `watchdog.py`, `audit.py`, etc.

| Prefix | Purpose | Status |
|--------|---------|--------|
| `/actions` | Action Gateway CRUD | Active |
| `/worker` | Worker process tick | Active |
| `/watchdog` | SLA watchdog | Active |
| `/audit` | Audit log query | Active |
| `/gateway` | Health + metrics | Active |

### Family 3: Admin / Workflow Routes (Sprint 2.6–2.27)
Registered via 12 `include_router` calls in `create_app()`.

| Router | Purpose | Status |
|--------|---------|--------|
| `_wf_admin_routes` | Workflow admin | Active |
| `_tool_admin_routes` | Tool registry admin | Active |
| `_investigation_admin_routes` | Investigation admin | Active |
| `_action_proposals_admin_routes` | Action proposal admin | Active |
| `_execution_admin_routes` | Execution admin | Active |
| `_reasoning_admin_routes` | Reasoning admin | Active |
| `_clarification_admin_routes` | Clarification admin | Active |
| `_adapter_admin_routes` | Adapter admin | Active |
| `_tickets_routes` | **Golden Path entry** `POST /tickets/process` | Active, but Freshdesk never calls it |

### Family 4: Freshdesk Webhook Routes (Sprint 2.1 legacy + 2.28.1 new)
| Method | Path | File | Sprint | Status |
|--------|------|------|--------|--------|
| POST | `/webhook/{client}` | `api/routes/webhook.py` | 2.1 | Mounted, annotated NON_PRODUCTION_PATH |
| POST | `/webhooks/freshdesk/ticket-created` | `api/routes/webhooks/freshdesk.py` | 2.28.1 | Mounted, receives requests, **partially operational** |
| POST | `/webhooks/freshdesk/ticket-updated` | `api/routes/webhooks/freshdesk.py` | 2.28.1 | Mounted, receives requests, **partially operational** |

---

## SECTION 3 — WHAT ACTUALLY EXECUTES IN PRODUCTION

When Freshdesk sends a ticket webhook, this is the exact execution path:

```
Freshdesk
  └─► POST /freshdesk/webhook                      [app/main.py:2021]
        ├── verify_webhook_token()                  [app/freshdesk_webhook.py]
        │     ├─ FRESHDESK_WEBHOOK_MODE=static → hmac.compare_digest(secret, token)
        │     └─ FRESHDESK_WEBHOOK_MODE=hmac   → hmac.compare_digest(computed_hmac, token)
        ├── json.loads(body)
        ├── extract_ticket_info(payload)            [app/freshdesk_webhook.py]
        │     ├─ Supports wrapper format: {"freshdesk_webhook": {...}}
        │     └─ Supports flat format: {"ticket_id": ..., ...}
        ├── resolve_tenant(ticket)                  [app/freshdesk_webhook.py]
        │     ├─ Priority 1: cf_clients custom field (Freshdesk Dispatch'r)
        │     ├─ Priority 2: tag with "client:" prefix
        │     ├─ Priority 3: cf_client_slug / cf_client aliases
        │     ├─ Priority 4: email domain lookup (_TENANT_EMAIL_DOMAIN_MAP)
        │     └─ Priority 5: DEFAULT_CLIENT env var
        ├── build_query_text(ticket)                [app/freshdesk_webhook.py]
        ├── generator.generate(GenerationRequest)  [rag_engine/generation/chat_generator.py]
        │     ├── TicketRetriever.retrieve()        [rag_engine/retrieval/ticket_retriever.py]
        │     │     └── Supabase RPC: match_all_b1_sources
        │     ├── assemble_context(chunks)          [rag_engine/generation/context_assembler.py]
        │     └── B1LLMClient.complete_json()       [rag_engine/generation/llm_client.py]
        │           └── Anthropic API call
        └── IF _ACTION_GATEWAY_ENABLED:
              gateway.propose(action)              [case_engine/action_gateway.py]
            ELSE:
              FreshdeskReplyClient.post_note()     [app/freshdesk_webhook.py]
              ← ⚠️ DIRECT FRESHDESK WRITE — VIOLATES GOLDEN PATH
```

**What is bypassed:**
- TicketOrchestrator (the Golden Path entry layer)
- SupportAgentRuntime (the agent coordinator)
- WorkflowEngine (deterministic step executor)
- InvestigationService (tool-based evidence collection)
- ReasoningService (structured reasoning layer)
- Safety Guardrails (built into SupportAgentRuntime)
- Case lifecycle state machine (CaseService)
- Audit trail (AuditLogger — no audit events for this path)

---

## SECTION 4 — GOLDEN PATH GAP ANALYSIS

### The Non-Negotiable Golden Path (Source of Truth: freshdesk_integration.md)
```
Freshdesk
→ Webhook Receiver
→ Client Resolution Layer
→ Ticket Orchestrator
→ Support Agent Runtime
→ Workflow Engine
→ Investigation Planner
→ Evidence Collection
→ Knowledge Retrieval
→ Reasoning Engine
→ Safety Guardrails
→ Action Gateway          ← ONLY ENTRY POINT FOR FRESHDESK WRITES
→ Execution Layer
→ Freshdesk / Asana / Support APIs
```

### Current Production Path vs Golden Path

| Golden Path Component | Implementation Exists? | Wired at Startup? | Called by Freshdesk? |
|----------------------|----------------------|-------------------|----------------------|
| Webhook Receiver | ✅ `api/routes/webhooks/freshdesk.py` | ✅ (mounted) | ✅ (but wrong route) |
| Client Resolution Layer | ✅ `case_engine/tenant/resolver.py:ClientResolver` | ✅ (`app.state.client_resolver`) | ❌ (`resolve_tenant()` used instead) |
| Ticket Orchestrator | ✅ `case_engine/ticket_orchestration/orchestrator.py` | ✅ (`app.state.ticket_orchestrator`) | ❌ |
| Support Agent Runtime | ✅ `case_engine/runtime/support_agent_runtime.py` | ✅ (`app.state.support_agent_runtime`) | ❌ |
| Workflow Engine | ✅ `case_engine/workflows/workflow_engine.py` | ✅ (`app.state.workflow_engine`) | ❌ |
| Investigation Planner | ✅ `case_engine/investigation/planner.py` | ✅ (via investigation_service) | ❌ |
| Evidence Collection | ✅ `case_engine/investigation/collector.py` | ✅ (via investigation_service) | ❌ |
| Knowledge Retrieval | ✅ `case_engine/knowledge/orchestrator.py` | ✅ (`app.state.knowledge_orchestrator`) | ❌ |
| RAG Knowledge Layer | ✅ `rag_engine/retrieval/ticket_retriever.py` | ✅ (via generator) | ✅ (wrong path) |
| Reasoning Engine | ✅ `case_engine/reasoning/reasoning_engine.py` | ✅ (`app.state.reasoning_service`) | ❌ |
| Safety Guardrails | ✅ (in SupportAgentRuntime) | ✅ | ❌ |
| Action Gateway | ✅ `case_engine/action_gateway/gateway.py` | ✅ (`app.state.stack.gateway`) | ⚠️ conditional only |
| Execution Layer | ✅ `case_engine/execution/executor.py` | ✅ (via execution_service) | ❌ |

**Verdict:** Every Golden Path component is built and wired at startup. Freshdesk bypasses all of them except the RAG layer and a conditional Action Gateway path.

### The Specific Violation
`freshdesk_integration.md` Section 0.2 states:
> "No component upstream of the Action Gateway is permitted to call Freshdesk's write endpoints."

When `_ACTION_GATEWAY_ENABLED = False` (the default for direct testing), `FreshdeskReplyClient.post_note()` is called directly from inside the webhook handler, before any Action Gateway involvement. This is a hard architectural violation.

---

## SECTION 5 — THREE GENERATIONS OF FRESHDESK WEBHOOK HANDLING

### Generation 1: Sprint 2.1 Legacy (`POST /webhook/{client}`)
**File:** `api/routes/webhook.py`  
**Status:** Mounted, annotated `NON_PRODUCTION_PATH`  
**Annotated:** Yes — `# NON_PRODUCTION_PATH: Sprint 2.1 legacy entry. Bypasses TicketOrchestrator.`  
**Execution path:** `FreshdeskWebhookProcessor.validate()` → `stack.gateway.propose()` → does NOT call RAG, does NOT call TicketOrchestrator  
**Verdict:** Safe to deprecate when Sprint 2.28.1 routes are fully operational.

### Generation 2: Sprint B3 (`POST /freshdesk/webhook`)
**File:** `app/main.py:2021`  
**Status:** ACTIVE — Freshdesk currently calls this  
**Auth:** `verify_webhook_token()` — supports static + HMAC modes  
**Execution path:** resolve_tenant → ChatGenerator → FreshdeskReplyClient (direct write) or gateway.propose()  
**Architecture violations:**
- Calls Freshdesk write APIs directly when `_ACTION_GATEWAY_ENABLED=False`
- Bypasses TicketOrchestrator, SupportAgentRuntime, WorkflowEngine
- No audit trail (no AuditLogger calls)
- No case lifecycle (no CaseService calls)
- No idempotency protection
- No replay protection (no timestamp gate)
**Verdict:** Must be migrated. Cannot be the long-term production path.

### Generation 3: Sprint 2.28.1 (`POST /webhooks/freshdesk/ticket-created` + `ticket-updated`)
**File:** `api/routes/webhooks/freshdesk.py`  
**Status:** Mounted and receiving requests, but **not yet fully operational**  
**Auth:** `FreshdeskWebhookVerifier` — HMAC-SHA256 only (does not support static mode)  
**Security:** Replay protection (5-min window), payload size gate (1 MB), clock skew gate  
**Idempotency:** `WebhookIdempotencyStore` — wired, but falls back to in-memory because `app.state.freshdesk_idempotency_store` is not set  
**What works:**
- HMAC signature verification ✅
- Replay protection ✅
- Payload size gate ✅
- Event receipt logging (in-memory fallback) ✅
- Background task enqueueing ✅
- Conversation state tracking (in-memory fallback) ✅
**What does NOT work (not wired):**
- `freshdesk_rag_processor` → `None` → no RAG call, no response generated
- `freshdesk_conversation_store` → falls back to in-memory (state lost on restart)
- `freshdesk_idempotency_store` → falls back to in-memory (replay protection lost on restart)
- `freshdesk_response_service` → `None` → no Freshdesk note posted on unknown tenant
**Verdict:** Architecturally correct pattern, needs service wirings to be operational.

---

## SECTION 6 — DUPLICATE LOGIC INVENTORY

### Duplicate 1: Three Freshdesk HTTP Clients

| Implementation | File | Sprint | API Style | Used Where |
|---------------|------|--------|-----------|-----------|
| `FreshdeskReplyClient` | `app/freshdesk_webhook.py` | B3 | Sync HTTPX | `POST /freshdesk/webhook` (production) |
| `FreshdeskClient` | `freshdesk/client.py` | 2.28.1 | Async HTTPX + rate limit + retry | `freshdesk/handlers.py`, `freshdesk/response_service.py` |
| `FreshdeskProvider` | `freshdesk/freshdesk_provider.py` | 2.4 | Provider ABC (via ProviderRouter) | Action Gateway execution path |

**Desired end state:** One client. `FreshdeskClient` (Sprint 2.28.1) is the most complete implementation (async, rate-limited, retries). `FreshdeskProvider` is the correct adapter layer for the Action Gateway. `FreshdeskReplyClient` should be eliminated once `POST /freshdesk/webhook` is migrated.

### Duplicate 2: Two Tenant Resolution Paths

| Implementation | File | Used Where |
|---------------|------|-----------|
| `resolve_tenant()` | `app/freshdesk_webhook.py` | `POST /freshdesk/webhook` handler |
| `ClientResolver` | `case_engine/tenant/resolver.py` | `TicketOrchestrator` (Golden Path) |

Both resolve a ticket to a tenant slug. `ClientResolver` uses `TenantRegistry` (structured, Sprint 2.27.9). `resolve_tenant()` uses inline logic + `_TENANT_EMAIL_DOMAIN_MAP` (Sprint B3, flat dict). The Golden Path target is `ClientResolver`. The email domain map in `resolve_tenant()` should eventually be migrated into `TenantRegistry` configs.

### Duplicate 3: Two Action Gateway Access Patterns

| Pattern | Location | What it does |
|---------|----------|-------------|
| `_ACTION_GATEWAY_ENABLED` flag | `app/main.py` | Module-level bool, controls Sprint B3 path |
| `case_engine/action_gateway/gateway.py` | `ActionGateway.propose()` | Full gateway via ProductionRuntime |

The module-level `_ACTION_GATEWAY_ENABLED` flag in `app/main.py` is a development toggle for `POST /freshdesk/webhook`. The real Action Gateway (`case_engine/action_gateway/`) is a fully-wired service in ProductionRuntime. These are not the same code path — `_ACTION_GATEWAY_ENABLED=True` calls `app.state.stack.gateway.propose()` which is the real gateway, but `_ACTION_GATEWAY_ENABLED=False` calls `FreshdeskReplyClient` directly.

---

## SECTION 7 — DEAD CODE ANALYSIS

### Provably Dead: Not imported by any production path

| File | Evidence | Sprint |
|------|---------|--------|
| `app/chunker.py` | Superseded by `app/chunker_v2.py`. Not imported in any active route. | Pre-B1 |
| `rag_engine/cli/ingest_cli.py` | CLI tool — not imported by app. No `from rag_engine.cli` in any route. | B1 |
| `rag_engine/cli/sample_retrieval.py` | CLI tool — not imported by app. | B1 |

### Dormant: Built and wired but not triggered by Freshdesk

These components are production-quality code wired at startup. They do NOT execute when Freshdesk sends a webhook because the current `POST /freshdesk/webhook` path bypasses them. They will execute once the Golden Path migration is complete.

| Component | File | Wired State | Blocking Gap |
|-----------|------|-------------|-------------|
| `TicketOrchestrator` | `case_engine/ticket_orchestration/orchestrator.py` | `app.state.ticket_orchestrator` ✅ | Freshdesk sends to wrong route |
| `SupportAgentRuntime` | `case_engine/runtime/support_agent_runtime.py` | `app.state.support_agent_runtime` ✅ | Same |
| `WorkflowEngine` | `case_engine/workflows/workflow_engine.py` | `app.state.workflow_engine` ✅ | Same |
| `InvestigationService` | `case_engine/investigation/service.py` | `app.state.investigation_service` ✅ | Same |
| `KnowledgeOrchestrator` | `case_engine/knowledge/orchestrator.py` | `app.state.knowledge_orchestrator` ✅ | Same |
| `ReasoningService` | `case_engine/reasoning/service.py` | `app.state.reasoning_service` ✅ | Same |
| `ClarificationService` | `case_engine/clarification/service.py` | `app.state.clarification_service` ✅ | Same |
| `ClientResolver` | `case_engine/tenant/resolver.py` | `app.state.client_resolver` ✅ | Same |

### Placeholder: Correct pattern, mock implementation

| Component | File | Status |
|-----------|------|--------|
| `FreshdeskAdapter` | `case_engine/adapters/freshdesk_adapter.py` | Placeholder — no HTTP calls, mock responses. Annotated: "Sprint 2.28 will replace this with real FreshdeskHTTPAdapter." |
| `GetSessionDetailsTool` et al. | `case_engine/tools/mock_tools.py` | Mock — deterministic fake data. Annotated: "Real KwikID API integrations will replace these in a future sprint." |
| `AsanaAdapter` | `case_engine/adapters/asana_adapter.py` | Placeholder — no HTTP calls. |
| `EngineeringEscalationService` | `case_engine/engineering/service.py` | Placeholder — mock Asana integration. |

---

## SECTION 8 — KNOWLEDGE LAYER ALIGNMENT

The system has two parallel knowledge layers that are not yet integrated:

### Layer A — RAG Engine (`rag_engine/`) — Production Active
Called by `POST /freshdesk/webhook`, `POST /chat`, `POST /rag/chat`.

```
TicketRetriever.retrieve()
  → EmbeddingProvider.embed_single()    [rag_engine/embedding/openai_provider.py]
  → Supabase RPC: match_all_b1_sources  [rag_ticket_chunks, rag_sop_chunks, rag_knowledge_chunks]
  → RerankingHook (NullReranker by default)
  → RetrievedChunk[]

ChatGenerator.generate()
  → _compute_retrieval_context()        [retrieval + context assembly]
  → assemble_context()                  [rag_engine/generation/context_assembler.py]
  → B1LLMClient.complete_json()         [Anthropic API]
  → GenerationResult { answer, confidence, confidence_score, requires_human, ... }
```

Tables searched: `rag_ticket_chunks`, `rag_sop_chunks`, `rag_knowledge_chunks`  
Tenant isolation: enforced — `client` parameter required, raises `ValueError` if omitted  
Fallback: `_fallback_search()` when RPC unavailable — non-semantic, low confidence

### Layer B — Case Engine Knowledge (`case_engine/knowledge/`) — Wired, Not Active
Called by `WorkflowEngine.KNOWLEDGE_RETRIEVAL` step only. Freshdesk never reaches this.

```
KnowledgeOrchestrator
  → KnowledgeService.retrieve()
  → KnowledgeRepository (Supabase)
  → KnowledgeMatcher
  → KnowledgeRecommendation
```

**Gap:** Layer A (RAG) and Layer B (Knowledge Service) are separate implementations of knowledge retrieval. The Golden Path calls Layer B via the WorkflowEngine. The current production Freshdesk path calls Layer A directly via ChatGenerator. These must converge: the Golden Path's `KNOWLEDGE_RETRIEVAL` step should call Layer A's `TicketRetriever`/`ChatGenerator` internally, or the `KnowledgeOrchestrator` should delegate to the RAG engine.

---

## SECTION 9 — TOOL REGISTRY ALIGNMENT

### Registered Tools (Sprint 2.17, all MOCK)

| Tool | Tags | Input Required | Real API? |
|------|------|----------------|----------|
| `GetSessionDetailsTool` | vkyc, session | session_id | ❌ Mock |
| `GetUserDetailsTool` | user, kyc | phone_number | ❌ Mock |
| `GetFailureReasonTool` | diagnostics, failure_analysis | operation_id | ❌ Mock |
| `GetCaseHistoryTool` | case_history, user | phone_number | ❌ Mock |
| `GetOnboardingStatusTool` | onboarding, kyc | application_id | ❌ Mock |

All tools return deterministic fake data. `ToolRegistry.build_default()` (Sprint 2.17) loads all five. `ToolExecutor` wraps the registry. Both are wired at startup:
```
app.state.tool_registry   = ToolRegistry.build_default()
app.state.tool_executor   = ToolExecutor(tool_registry)
```

### Alignment with flow_diagram.mermaid
The mermaid diagram shows `KwikID_APIs` → `Integration_Layer` → `Evidence_Collection`. Current state: the Evidence Collection step (`InvestigationService`) calls these tools in mock mode. The real KwikID API integration is the next required step for Investigation to be useful.

### Tenant-Aware Tool Registry (Sprint 2.27.9)
`TenantAwareToolRegistry` is wired as `app.state.tenant_tool_registry`. It wraps `TenantRegistry` to provide per-tenant tool access control. This is built and wired but only useful once real tools (not mocks) are registered.

---

## SECTION 10 — SPRINT 2.28.1 PARTIAL OPERATIONAL STATE

The Sprint 2.28.1 routes (`api/routes/webhooks/freshdesk.py`) follow the correct pattern. Here is exactly what is operational and what is missing:

### Operational
- **HMAC verification:** `FreshdeskWebhookVerifier` with `X-Webhook-Token` header — works
- **Replay protection:** event_timestamp compared against 5-minute window — works
- **Payload size gate:** 1 MB hard limit — works
- **Background task:** async processing after 200 OK — works
- **Handler dispatch:** `FreshdeskTicketCreatedHandler` / `FreshdeskTicketUpdatedHandler` — instantiated
- **Conversation state:** `ConversationStateStore` — falls back to in-memory instance (works, but state lost on restart)
- **Idempotency:** `WebhookIdempotencyStore` — falls back to in-memory (works, but replay protection across restarts lost)

### Missing (Must Be Wired in Sprint 2.29)

| Missing Service | `app.state` Key | Effect of Missing | Fix |
|----------------|----------------|-------------------|-----|
| `WebhookIdempotencyStore` (Supabase-backed) | `freshdesk_idempotency_store` | Falls back to in-memory; replay protection lost on pod restart | Wire in lifespan with Supabase client |
| `ConversationStateStore` (Supabase-backed) | `freshdesk_conversation_store` | Falls back to in-memory; clarification loop state lost | Wire in lifespan with Supabase client |
| RAG processor callable | `freshdesk_rag_processor` | Returns `None`; no AI response generated | Wire `ChatGenerator.generate` wrapper |
| `FreshdeskResponseService` | `freshdesk_response_service` | Returns `None`; no Freshdesk note posted on unknown tenant | Wire `FreshdeskResponseService(FreshdeskClient)` |

The `_get_rag_processor()` and `_get_conv_store()` helper functions at `api/routes/webhooks/freshdesk.py:450-463` already exist (added in Sprint 2.28.3) and will return the correct values once the lifespan wirings are added.

---

## SECTION 11 — WHAT CODE IS DEAD (SAFE TO REMOVE LATER)

These removals are **NOT safe yet** — removal should only happen after the stated migration condition is met.

### Remove after `POST /freshdesk/webhook` is migrated (Sprint 2.30+):
- `app/freshdesk_webhook.py:FreshdeskReplyClient` — sync Freshdesk HTTP client used only by Sprint B3 path
- `app/main.py:_ACTION_GATEWAY_ENABLED` — module-level toggle for Sprint B3 path
- `app/main.py:2021-2250` — the `POST /freshdesk/webhook` handler itself

### Remove after Sprint 2.28.1 routes are fully operational:
- `app/freshdesk_webhook.py:build_query_text()` — used only by `POST /freshdesk/webhook`
- The function `resolve_tenant()` in `app/freshdesk_webhook.py` — superseded by `ClientResolver`, but keep until email domain map is migrated to `TenantRegistry`

### Already annotated for removal:
- `api/routes/webhook.py` — `POST /webhook/{client}` Sprint 2.1 legacy

### Remove NOW (provably unused):
- `app/chunker.py` — superseded, not imported anywhere active
- `rag_engine/cli/ingest_cli.py` — CLI only, not app import
- `rag_engine/cli/sample_retrieval.py` — CLI only, not app import

---

## SECTION 12 — WHAT SHOULD BECOME SINGLE SOURCE OF TRUTH

| Concern | Current State | Target Single Source | Migration Path |
|---------|--------------|---------------------|---------------|
| **Freshdesk Webhook Entry** | 3 routes | `POST /webhooks/freshdesk/ticket-created` (Sprint 2.28.1) | Sprint 2.29: wire services; Sprint 2.30: migrate Freshdesk URL; Sprint 2.31: delete Sprint B3 route |
| **Tenant Resolution** | 2 implementations | `case_engine/tenant/resolver.py:ClientResolver` | Migrate email domain map from `_TENANT_EMAIL_DOMAIN_MAP` to `TenantRegistry` configs |
| **Freshdesk HTTP Client** | 3 implementations | `freshdesk/client.py:FreshdeskClient` (async, rate-limited) | Remove `FreshdeskReplyClient` after Sprint B3 migration; keep `FreshdeskProvider` as gateway adapter |
| **Knowledge Retrieval** | 2 subsystems (RAG + CaseEngine) | `KnowledgeOrchestrator` delegating to `TicketRetriever` | Wire `ChatGenerator` into `KnowledgeOrchestrator` in Sprint 2.29 |
| **Action Gateway** | 2 patterns | `case_engine/action_gateway/gateway.py` via ProductionRuntime | Remove `_ACTION_GATEWAY_ENABLED` toggle after Sprint B3 migration |

---

## SECTION 13 — WHAT ARCHITECTURE SHOULD REMAIN AFTER CONSOLIDATION

After consolidation, the architecture should be:

```
Freshdesk (ticket-created)
  └─► POST /webhooks/freshdesk/ticket-created
        ├── FreshdeskWebhookVerifier (HMAC)
        ├── WebhookIdempotencyStore (Supabase)   ← prevents duplicate processing
        ├── WAL pre-persist receipt
        └── BackgroundTask:
              TicketOrchestrator.process_ticket()
                ├── ClientResolver.resolve()       ← TenantRegistry
                ├── SupportAgentRuntime.run_case()
                │     ├── CaseService (state machine)
                │     └── WorkflowEngine
                │           ├── CLASSIFY
                │           ├── INVESTIGATE        ← InvestigationService + mock tools → real tools
                │           ├── KNOWLEDGE_RETRIEVAL ← KnowledgeOrchestrator → TicketRetriever/ChatGenerator
                │           ├── REASON             ← ReasoningService
                │           ├── PROPOSE_ACTION     ← ActionProposalService
                │           └── EXECUTE
                │                 └── ActionGateway.propose()
                │                       └── ExecutionService
                │                             └── AdapterRouter → FreshdeskAdapter (→ FreshdeskClient)
                │                                                → FreshdeskProvider (Action Gateway path)
                └── AuditLogger (all events)

Freshdesk (ticket-updated / clarification reply)
  └─► POST /webhooks/freshdesk/ticket-updated
        ├── FreshdeskWebhookVerifier (HMAC)
        ├── ConversationStateStore.get_state()    ← conversation context
        ├── [if AWAITING_INPUT] → forward reply to WorkflowEngine.resume_workflow()
        └── [else] → dispatch new TicketOrchestrator.process_ticket() call

Admin + Internal
  └─► POST /tickets/process                       ← keep for internal/testing use
        └── TicketOrchestrator.process_ticket()   ← same path as above
```

**Components that should be deleted eventually (after above is operational):**
1. `POST /freshdesk/webhook` and its entire handler
2. `POST /webhook/{client}` and `FreshdeskWebhookProcessor`
3. `FreshdeskReplyClient` in `app/freshdesk_webhook.py`
4. `_ACTION_GATEWAY_ENABLED` module-level bool

**Components that should remain permanently:**
1. `app/freshdesk_webhook.py:resolve_tenant()` — keep until ClientResolver + TenantRegistry fully covers all tenants, then migrate email map and delete
2. All of `rag_engine/` — this is the RAG backbone; `TicketRetriever` + `ChatGenerator` are the production-grade knowledge retrieval layer
3. All of `case_engine/` — this is the Golden Path runtime; fully wired, just not being called
4. `freshdesk/` module — the Sprint 2.28.1 Freshdesk integration layer (client, verifier, handlers, idempotency, conversation_state, metrics, response_service)

---

## SECTION 14 — SAFE CLEANUP THAT CAN BE DONE NOW

These are provably safe because nothing imports or calls them in production:

### 1. Delete orphaned CLI tools
```
rag_engine/cli/ingest_cli.py
rag_engine/cli/sample_retrieval.py
rag_engine/cli/__init__.py
```
These are standalone CLI scripts. Verified: no production route imports `rag_engine.cli`.

### 2. Delete legacy chunker
```
app/chunker.py
```
Verified: `app/chunker_v2.py` is the active implementation. `app/chunker.py` is not imported in any active route.

---

## SECTION 15 — RECOMMENDED SPRINT SEQUENCE

### Sprint 2.29 (Next): Wire Sprint 2.28.1 Routes
**Goal:** Make `POST /webhooks/freshdesk/ticket-created` fully operational.

Required changes:
1. In `app/main.py` lifespan: set `app.state.freshdesk_idempotency_store = WebhookIdempotencyStore(sb_client)`
2. In `app/main.py` lifespan: set `app.state.freshdesk_conversation_store = ConversationStateStore(sb_client)` (Supabase-backed variant)
3. In `app/main.py` lifespan: set `app.state.freshdesk_rag_processor = _make_rag_processor(generator, embedder)` — callable that wraps `ChatGenerator.generate()`
4. In `app/main.py` lifespan: set `app.state.freshdesk_response_service = FreshdeskResponseService(freshdesk_client)`
5. Update `FRESHDESK_WEBHOOK_MODE` support: Sprint 2.28.1 verifier only does HMAC. Add static mode support to `freshdesk/verifier.py` OR make `_get_verifier()` check the mode setting.

Constraint: Do NOT change the production Freshdesk URL yet. Run both paths in parallel (Sprint B3 still handles live traffic, Sprint 2.28.1 wired for testing).

### Sprint 2.30: Migrate Freshdesk URL
**Goal:** Point Freshdesk Dispatch'r to `POST /webhooks/freshdesk/ticket-created`.

Steps:
1. Update Freshdesk webhook URL in Freshdesk admin panel
2. Validate Sprint 2.28.1 route handles all live traffic
3. Monitor for 2 weeks

### Sprint 2.31: Delete Sprint B3 Route
**Goal:** Remove legacy `POST /freshdesk/webhook` and its dependencies.

Delete:
- `app/main.py:2021-2250` (webhook handler)
- `app/main.py:_ACTION_GATEWAY_ENABLED` (module-level bool)
- `app/freshdesk_webhook.py:FreshdeskReplyClient`
- `app/freshdesk_webhook.py:build_query_text()`
- `api/routes/webhook.py` (Sprint 2.1 legacy — NON_PRODUCTION_PATH)

Relocate:
- `app/freshdesk_webhook.py:resolve_tenant()` → migrate email domain map to `TenantRegistry`, then delete
- `app/freshdesk_webhook.py:extract_ticket_info()` → keep in `freshdesk/` module if still needed

### Sprint 2.32+: Replace Mock Tools with Real KwikID APIs
**Goal:** Activate real investigation tools.

Replace `case_engine/tools/mock_tools.py` implementations with real HTTP calls to KwikID's session, user, failure-reason, case-history, and onboarding endpoints. Tool interface (`BaseTool`, `ToolDefinition`, `run()`) is stable per Sprint 2.17 design.

---

## APPENDIX A — FILE → PRODUCTION PATH MAPPING

| File | Called In Production? | By What? |
|------|-----------------------|---------|
| `app/main.py` | ✅ | Entry point — all routes |
| `app/freshdesk_webhook.py` | ✅ | `POST /freshdesk/webhook` |
| `app/config.py` | ✅ | All routes (get_settings) |
| `rag_engine/generation/chat_generator.py` | ✅ | `POST /freshdesk/webhook`, `/chat`, `/rag/chat` |
| `rag_engine/retrieval/ticket_retriever.py` | ✅ | Via ChatGenerator |
| `rag_engine/embedding/openai_provider.py` | ✅ | Via TicketRetriever |
| `case_engine/ticket_orchestration/orchestrator.py` | ⚠️ Wired, not called by Freshdesk | `POST /tickets/process` only |
| `case_engine/runtime/support_agent_runtime.py` | ⚠️ Wired, not called by Freshdesk | Via TicketOrchestrator |
| `case_engine/workflows/workflow_engine.py` | ⚠️ Wired, not called by Freshdesk | Via SupportAgentRuntime |
| `case_engine/tools/mock_tools.py` | ⚠️ Wired, not called by Freshdesk | Via InvestigationService |
| `freshdesk/client.py` | ⚠️ Imported by handlers, not yet active | Sprint 2.28.1 routes (no rag_processor wired) |
| `freshdesk/verifier.py` | ✅ | Sprint 2.28.1 routes (HMAC verification) |
| `freshdesk/handlers.py` | ✅ | Sprint 2.28.1 routes |
| `api/routes/webhooks/freshdesk.py` | ✅ | Sprint 2.28.1 routes (receive, partial processing) |
| `api/routes/tickets.py` | ✅ | `POST /tickets/process` (internal/test) |
| `api/routes/webhook.py` | ✅ (mounted) | Sprint 2.1 — NON_PRODUCTION_PATH annotated |
| `api/app.py` | ❌ | Test backward compat re-export only |
| `runtime/assembly.py` | ✅ | Lifespan startup (ProductionRuntime) |
| `case_engine/adapters/freshdesk_adapter.py` | ⚠️ Wired as placeholder | Via AdapterRegistry/AdapterRouter |
| `app/chunker.py` | ❌ | Dead — superseded |
| `rag_engine/cli/ingest_cli.py` | ❌ | CLI tool |
| `rag_engine/cli/sample_retrieval.py` | ❌ | CLI tool |

---

## APPENDIX B — SECURITY CONSTRAINTS (NON-NEGOTIABLE)

These constraints from `freshdesk_integration.md` and prior sprint decisions must be preserved through all migrations:

1. `hmac.compare_digest()` is mandatory for ALL key comparisons — NEVER use `==`
2. Email addresses MUST NEVER be logged — only domain logged (PII protection)
3. API keys must be masked (first 4 chars only) in any logs
4. `credentials_ref` is a REFERENCE KEY — not the actual credential — never log or store actual API keys
5. `FRESHDESK_WEBHOOK_MODE=static` must remain supported alongside `hmac` mode
6. No component upstream of the Action Gateway is permitted to call Freshdesk write endpoints
7. Replay protection (5-minute window) must be enforced on all webhook routes
8. Do NOT touch n8n integration
9. Do NOT implement Unity APIs
10. Do NOT implement Asana directly — route through EngineeringEscalationService
11. Do NOT implement Action Gateway execution (FreshdeskProvider placeholder only)

---

*Produced by Sprint 2.28.4 — Architecture Consolidation Audit*  
*Authoritative reference: Source_Of_Truth/ (freshdesk_integration.md, flow_diagram.mermaid, SUPPORT_OPERATIONS_BLUEPRINT.md)*
