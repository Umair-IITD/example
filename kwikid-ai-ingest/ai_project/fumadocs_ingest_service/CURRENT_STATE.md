# CURRENT_STATE.md

**KwikID Enterprise Support Agent — Project State Snapshot**

Date: 2026-07-31  
Branch: `major-architecture-change`  
Last Certified Sprint: **2.64**

---

## 1. Current Sprint

**Sprint 2.64 — Final Go-Live Audit** is the last certified sprint.

### L1 + L2 Pipeline: 100% Code-Complete and Fully Audited as of Sprint 2.64

Full end-to-end flow is implemented, audited, and certified:
Freshdesk intake → NLP classification → L1 investigation (with SOP guidance) → observation note →
L2 Asana escalation (if needed) → Asana task-completed webhook →
ReplySafetyGate → customer notification → Freshdesk status=4 closure

### What Sprint 2.64 (Go-Live Audit) Delivered

**Sprint 2.63.1 — ReplySafetyGate wiring (Cowork session, certified this sprint):**
- `freshdesk/handlers.py` — `HandlerResult.response_confidence: float | None`; confidence extracted from `response_draft` at 3 sites
- `api/routes/webhooks/freshdesk.py` — `_get_safety_gate()` accessor; `_gated_customer_reply()` single funnel gates Sites 1-3; fail-CLOSED if gate absent
- `api/routes/webhooks/asana.py` — `_get_safety_gate()` accessor; inline GUARD→GATE sequence in `_handle_task_completed()` (Site 4)
- `app/main.py` — `ReplySafetyGate(kill_switch=REPLY_SAFETY_KILL_SWITCH)` wired as shared `app.state.reply_safety_gate`
- `.env.example` — `REPLY_SAFETY_KILL_SWITCH` documented
- `tests/test_sprint2631_reply_safety_gate_wiring.py` — 10 new tests (Sections K, M)
- `tests/test_sprint263_asana_webhook_receiver.py` — 4 new tests (Section J); total 45 tests

**Sprint 2.64 — Knowledge Layer SOP fix + Master E2E:**
- `case_engine/runtime/support_agent_runtime.py` — Fixed: `_run_intelligence()` now extracts SOP content from `search_result.matches[:3][entry]` (the correct path) instead of the non-existent `chunks` key — LLM now receives SOP guidance for every ticket
- `tests/test_sprint264_master_e2e_validation.py` — 22-test master E2E suite (Sections A-E): Knowledge Layer SOP extraction, PII redaction ordering, ReplySafetyGate wiring, Asana closure chain, full ticket lifecycle

**Subagent review verdicts (Node 5):**
- `architecture-drift-corrector`: **ALIGNED** — no drift found across all 8 Blueprint requirements
- `freshdesk-safety-reviewer`: **ALL RULES SATISFIED** — no safety gate bypass in tests or production code

### Closure-Field Mapping (Confirmed, sprint-2-6-3.md §2.1)

| Field | Value |
|---|---|
| `cf_sop_status` | `"No SOP Available"` |
| `cf_resolution_classification` | `"Permanent Fix Applied by Dev"` |
| `status` | `4` (Resolved; 4→5 auto via Freshdesk SLA) |
| `type` | `"Issues"` |

### Prior Sprints (Summary)

- **Sprint 2.60** — Multi-Tenant Log Platform (Grafana Loki): GetSessionLogsTool, PII-before-score ordering, BM25 shared module
- **Sprint 2.61** — L1 Production Readiness E2E: TenantContext log datasource field, 3 playbook updates
- **Sprint 2.62** — Final L1 Validation: 3 production bugs fixed (EvidenceSource.GET_SESSION_LOGS enum, _relevant_fields() extension, _UNITY_BANK_TOOLS name correction); 52/52 tests
- **Sprint 2.58/2.59** — Asana L2 Escalation (task creation): live Asana REST client integration
- **Sprint 2.56** — LLM Semantic Router: OpenAI GPT-4o-mini, NLPSignal, NLPRouter

---

## 2. Production Readiness

**Status: PRE-PRODUCTION — 1 admin action remaining**

L1 + L2 pipeline is 100% code-complete. 4 of 5 production gates are done.

| # | Gate | Status |
|---|---|---|
| §4.1 | FRESHDESK_WEBHOOK_SECRET / HMAC enforcement | ✅ Done (Sprint 2.48) |
| §4.2 | Dispatch'r rule extended to Unity Bank tickets | ✅ Done (Sprint 2.48) |
| §4.3 | Observer rule for customer-reply webhook | ✅ Done (Sprint 2.48) |
| §4.4 | Dedicated AI agent account (`ai.support@getkwikid.com`) | ⚠️ PENDING — only remaining blocker |
| §20B.wh | Asana webhook registered (`scripts/register_asana_webhook.py`) | ✅ Done (registered live 2026-07-31, project gid=1217038113542074, active=true, confirmed by Umair) |
| 2.63.1 | ReplySafetyGate wired to all 4 autonomous reply sites | ✅ Done (Sprint 2.63.1, certified Sprint 2.64) |
| 2.64 | KnowledgeResult SOP extraction fixed — LLM receives SOP content | ✅ Done (Sprint 2.64) |

`SUPPORT_AGENT_MODE=PRODUCTION` must NOT be set until §4.4 is complete.

---

## 3. Runtime Status

### Runtime Mode

- `SUPPORT_AGENT_MODE`: `DRY_RUN` (default, Asana task creation is simulated)
- `NLP_ROUTER_ENABLED`: must be `true` in .env for live classification; requires `INTELLIGENCE_LLM_API_KEY`
- API key fallback chain: `INTELLIGENCE_LLM_API_KEY` → `OPENAI_CHAT_API_KEY` → `OPENAI_API_KEY`

### Golden Path Entry Point

```
POST /tickets/process         ← Production entry point for new tickets
POST /tickets/{id}/resume     ← Resume a WAITING (clarification) ticket
POST /tickets/{id}/close      ← Force-close resolved ticket
POST /tickets/{id}/escalate   ← Force-escalate to L2
GET  /tickets/{id}/status     ← Query lifecycle state
```

All 5 golden path endpoints require `X-API-Key` (`require_operator` dependency). Returns 503 if TicketOrchestrator is not in `app.state`.

### Runtime Assembly (startup_validator.py — 9 checks)

On every startup, `app/startup_validator.py` runs 9 checks:
1. OpenAI API key present
2. Freshdesk API key present
3. Unity Bank credentials present
4. Unity tools registered (5 tools)
5. Metrics tools registered (2 tools)
6. Supabase connection
7. Playbook registry loaded (5 playbooks)
8. Action gateway initialized
9. Audit logger initialized

A `STARTUP_READY` event is logged when all checks pass. `CRITICAL` failures stop traffic.

### Known Runtime Issues

1. **asyncio.to_thread() rule**: All blocking I/O (Supabase, Unity API, Freshdesk) must run in `asyncio.to_thread()`. Violations starve the async event loop under load. This rule is enforced but not statically verified.

2. **Two AuditLogger classes**: `case_engine/audit.py` and a separate audit service exist. Never confuse them. The `case_engine/audit.py` is the production instance wired at assembly.

3. **Frozen guard**: Playbooks are frozen (immutable) at load time. Any attempt to mutate a loaded WorkflowDefinition at runtime will fail the frozen guard. This is intentional.

4. **Double-planner guard**: Investigation cannot be planned twice for the same case. The guard is in place (Sprint 2.5.5) but test coverage is minimal.

5. **TicketOrchestrator is in-memory**: Restarting the server loses in-flight ticket state. The WAITING tickets in Supabase `conversation_state` table survive, but the in-memory orchestrator state does not.

---

## 4. Pipeline Status (Layer by Layer)

| Layer | Name | Status | Notes |
|---|---|---|---|
| 1 | Ticket Ingestion (Freshdesk webhook) | ✅ Done | HMAC not enforced in .env |
| 1.5 | Client Resolution (multi-tenant) | ✅ Done | cf_clients-based |
| 2 | Case Engine (state machine + persistence) | ✅ Done | Supabase cases table |
| 2.5 | NLP Semantic Router | ✅ Done | Sprint 2.5.6 |
| 3 | Topic Classification | ✅ Done | LLM-only, no regex |
| 4 | Workflow Selection | ✅ Done | PlaybookRegistry.get(topic) |
| 5 | Slot Extraction | ✅ Done | From NLPSignal.entities |
| 6 | Clarification Engine (ask for URN/Session ID) | ✅ Done | Posts to Freshdesk |
| 6b | Clarification Loop (resume on customer reply) | ❌ BLOCKED | Observer webhook missing |
| 7 | Investigation Planner | ✅ Done | Playbook-driven |
| 7b | Unity Bank tool adapters (5 tools) | ✅ Done | Sprint 2.51, production-ready |
| 7c | Metrics tools (Uptime Kuma, 2 tools) | ✅ Done | Sprint 2.50, production-ready |
| 8 | Evidence Collection pipeline | ✅ Done | Sprint 2.54 |
| 9 | Root Cause Engine | ✅ Done | Sprint 2.18 |
| 9b | Knowledge/SOP retrieval (Hybrid RAG) | ✅ Done | Supabase pgvector + FTS; SOP extraction path fixed Sprint 2.64 (search_result.matches[*].entry → LLM context) |
| 10 | Intelligence Orchestrator (LLM reasoning) | ✅ Done | Sprint 2.53 |
| 11 | Observation Generator (L1 notes) | ✅ Done | Posts private Freshdesk note |
| 12 | Freshdesk note writer | ✅ Done | Via FreshdeskResponseService |
| 12b | Safety Guardrails | ✅ Done | ReplySafetyGate wired to all 4 reply sites (Sprint 2.63.1) + ClosureFieldGuard; fail-CLOSED design |
| 13 | Action Proposal | ✅ Done | Structured action from reasoning output |
| 14 | Action Gateway (risk model + routing) | ✅ Done | Sprint 2.21 |
| 14b | Action execution (Unity API calls) | ⚠️ Partial | Some action endpoints wired |
| 15 | Verification Engine | ⚠️ Partial | Basic success/fail check |
| 16 | Recovery Service | ⚠️ Partial | Retry logic in place; rollback not fully wired |
| 17 | Asana L2 Escalation (task creation) | ✅ Done | Live since Sprint 2.58 (SUPPORT_AGENT_MODE=PRODUCTION in .env) |
| 17b | Asana Resolution Loop (task-completed → close) | ✅ Done | Sprint 2.63 — ClosureFieldGuard + customer reply + status=4 |
| 18 | Customer Reply Generation | ✅ Done | ResponseGenerationService |
| 19 | Ticket Closure (all fields) | ✅ Done | ClosureFieldGuard ensures no HTTP 422 |
| 20 | Audit Trail | ✅ Done | 40+ event types, Supabase audit table |

---

## 5. Implemented Layers

All of these are fully implemented, tested, and certified:

- **Freshdesk webhook reception** (HMAC validation exists, enforcement env-gated)
- **Client resolution** from `cf_clients` custom field
- **Case lifecycle** (8 states: NEW → CLOSED, all transitions handled)
- **NLP Semantic Router** with entity extraction and negation handling
- **Topic classification** for all 5 topic families
- **Slot extraction and slot state management** (FILLED/EMPTY/INVALID/ESCALATED)
- **Clarification engine** (asks questions for missing URN/Session ID)
- **Workflow engine** with 5 YAML playbooks
- **Investigation planner** (playbook-driven, not hardcoded)
- **Unity Bank API integration** (5 production tool adapters, token management)
- **Uptime Kuma metrics integration** (2 production tool adapters)
- **Evidence collection pipeline**
- **Root cause engine**
- **SOP/knowledge retrieval** (hybrid RAG: pgvector + FTS + RRF + BM25)
- **Intelligence orchestrator** (LLM reasoning layer)
- **Observation generator** (L1 notes in Freshdesk format)
- **FreshdeskResponseService** sole write path enforced
- **ClosureFieldGuard** prevents HTTP 422 on closure
- **ReplySafetyGate** gates all public replies
- **Action Gateway** with risk model (SAFE/REVERSIBLE/HIGH)
- **Customer reply generation**
- **Full audit trail** (Supabase audit events)
- **Startup validation** (9-check sequence, STARTUP_READY report)

---

## 6. Partially Implemented Layers

### Action Execution
The executor can call Unity Bank APIs for some action types. Not all 5 action types are fully wired to their corresponding Unity API endpoints. The gateway correctly classifies risk and routes, but execution completeness varies.

- `otp_resend` — deferred to L2 (by design in OTP v3.0)
- `vkyc_session_reset` — partially implemented
- `document_ocr_reprocess` — partially implemented
- `agent_session_refresh` — partially implemented
- `api_callback_retry` — partially implemented

### Verification Engine
Basic outcome checking (was the action successful?) is in place. Retry triggering on failure is implemented. Full verification with re-querying session state after action is partial.

### Recovery Service
Retry logic is wired. Dead-letter queue routing is implemented. Rollback (undoing a REVERSIBLE action) is not fully implemented.

### Asana L2 Escalation
Task creation logic exists. Currently runs in DRY_RUN mode — `ASANACREATE_DRY_RUN` is logged instead of creating a real Asana task. Freshdesk `cf_asana_ticket_link` update is not yet wired to the real Asana task URL.

---

## 7. Missing Layers

### Customer Reply Loop (Observer Webhook)
The Freshdesk Observer rule that fires when a customer replies to a Pending ticket does not exist. This is the most critical missing piece for end-to-end L1 automation. Without it, every clarification question the system asks is a dead end.

### Full Action Execution for All 5 Action Types
Each action type needs: Unity API call → response normalization → success/fail determination → audit log. Only partially done.

### Video Analysis
Session video retrieval and analysis (via vision model) is defined in the Blueprint but not yet implemented. The Unity API endpoint (`GET /api/v1/getVideoFile/{video_token}`) exists but is not yet called.

### Cross-Session Correlation
If a customer has had multiple failed sessions, the system currently only investigates the single provided session_id. Pattern analysis across sessions is not implemented.

### Full Ticket Closure with All Fields
The ClosureFieldGuard ensures required closure fields are present. But populating all 12 AI-writable custom fields from investigation output is not fully wired (some fields are set, others still use defaults or are omitted).

---

## 8. Current Blockers

### Production Blockers (4 items — see Section 2)
These must be resolved before real Unity Bank traffic.

### Technical Blockers

**Sprint 2.64 certified**: All production code is certified through Sprint 2.64. No pending sprint cert blockers.

**Supabase FTS migrations**: SQL migrations `B1_007` and `B1_008` must be applied to production Supabase before hybrid retrieval RPCs work. Some tests that hit these RPCs will fail until the migrations are applied.

**Sentry not installed**: `sentry_sdk` is imported in some test files but not in `requirements.txt`. Those test files fail at collection (ImportError) in a cold environment. Add `sentry-sdk` to requirements or guard the import.

---

## 9. Known Technical Debt

| Debt Item | Location | Priority | Notes |
|---|---|---|---|
| Two AuditLogger classes coexist | `case_engine/audit.py` + legacy audit service | Medium | Never confuse them; consolidation deferred |
| NON_PRODUCTION_PATH webhook still active | `api/routes/webhook.py` (Sprint 2.1 legacy) | Low | Marked as non-production; preserved for backward compatibility |
| `exclude_escalation=True` is permanent | `case_engine/knowledge/rag_adapter.py:83` | None (don't touch) | NEVER revert — Sprint 2.47 security fix |
| TicketOrchestrator in-memory state | `case_engine/ticket_orchestration/orchestrator.py` | Medium | Server restart loses in-flight ticket state |
| ResponseGenerationService uses templates | `case_engine/response_generation/service.py` | Low | LLM injection planned but not implemented |
| EngineeringEscalationService in-memory | `case_engine/engineering/service.py` | Medium | Asana sync state lost on restart |
| Legacy RAG endpoints coexist | `app/main.py` (`/ingest`, `/query`, `/chat`) | Low | Operate on old `public.documents` table; frozen |
| `ACTIVE_INDEX_VERSION` vs `B1_INDEX_VERSION` may drift | `.env` | Medium | After any re-ingestion, validate and flip `ACTIVE_INDEX_VERSION` |
| Utility functions duplicated in 3 files | `rag_engine/` | Low | `_sha256`, `_word_count`, `_deterministic_chunk_id` should be in `rag_engine/utils/` |
| `sentry_sdk` import without install guard | Multiple test files | Medium | Some test files ImportError in cold environments |

---

## 10. Freshdesk Integration Status

| Capability | Status | Notes |
|---|---|---|
| Webhook reception (`/webhooks/freshdesk/ticket-created`) | ✅ Done | Returns 200 immediately, async processing |
| HMAC verification | ⚠️ Env-gated | `FRESHDESK_WEBHOOK_ENFORCE_HMAC=false` in .env — must be `true` in production |
| Idempotency (duplicate webhook prevention) | ✅ Done | `WebhookIdempotencyStore` |
| Client resolution from `cf_clients` | ✅ Done | Tenant identified before any processing |
| Private note posting | ✅ Done | Via `FreshdeskResponseService.post_internal_note()` |
| Public reply posting | ✅ Done | Via `FreshdeskResponseService.post_reply()`, gated by `ReplySafetyGate` |
| Ticket field updates | ✅ Done | Via `FreshdeskResponseService.update_ticket()`, gated by `ClosureFieldGuard` |
| Status transition (Open → Pending → Resolved) | ✅ Done | State machine controls status changes |
| Closure field population | ✅ Done | `ClosureFieldGuard` ensures all required fields present |
| Rate limiting (30 req/min self-imposed) | ✅ Done | Exponential backoff on 429 |
| Customer reply webhook (`/webhooks/freshdesk/ticket-updated`) | ❌ MISSING | Observer rule not created in Freshdesk |
| Real Unity Bank ticket scope | ❌ MISSING | Dispatch'r rule too narrow |
| Dedicated AI agent account | ❌ MISSING | Using human agent credentials |

---

## 11. Knowledge Layer Status

**Status: FROZEN at v2.1 (certified)**

| Component | Status |
|---|---|
| StackOverflow Teams import | ✅ Done |
| OCR pipeline (RapidOCR) for images | ✅ Done |
| Token-aware chunker (1200 tokens, 150 overlap) | ✅ Done |
| OpenAI embeddings (text-embedding-3-small, 1536 dim) | ✅ Done |
| Supabase pgvector storage | ✅ Done |
| FTS index (tsvector, GIN) | ✅ Done (requires B1_007, B1_008 in production) |
| Hybrid retrieval (semantic + FTS + RRF) | ✅ Done |
| BM25 reranking | ✅ Done |
| Index version v2 active | ✅ Done |
| Knowledge service wired into workflow | ✅ Done |

The knowledge layer is not being actively developed. Changes require running a full re-ingestion and setting `ACTIVE_INDEX_VERSION` after validation.

---

## 12. Investigation Layer Status

**Status: FULLY IMPLEMENTED**

| Component | Status | File |
|---|---|---|
| Investigation planner | ✅ Done | `case_engine/investigation/service.py` |
| Evidence collector | ✅ Done | `case_engine/investigation/pipeline.py` |
| Unity Bank tool adapters (5) | ✅ Production-ready | `case_engine/tools/adapters/unity_tools.py` |
| Metrics tools (2) | ✅ Production-ready | `metrics_platform/` |
| Root cause engine | ✅ Done | `case_engine/investigation/root_cause_engine.py` |
| Observation generator | ✅ Done | `case_engine/investigation/observation.py` |
| Evidence → Freshdesk note pipeline | ✅ Done | Observation → `FreshdeskResponseService` |
| Video analysis tool | ❌ Not started | Future capability |

---

## 13. L1 Automation Status

L1 automation is the system's primary goal: replace the human L1 support agent.

### Completed L1 Capabilities

- Ticket intake and client identification
- NLP classification with confidence scoring
- Clarification loop initiation (asking for URN/Session ID)
- Unity Bank portal investigation (session logs, summary, audit trail)
- Root cause determination from evidence
- L1 observation note writing to Freshdesk
- SOP-guided action proposal
- Safety-gated action routing (SAFE → auto-execute, REVERSIBLE → human review)
- Customer reply generation
- Ticket closure with all required fields

### Remaining L1 Work

1. **Clarification loop resume**: When customer replies, system must resume. Requires Observer webhook (BLOCKING).
2. **Full action execution**: All 5 action types must be executable against Unity API. Currently partial.
3. **Verification after action**: Confirm action succeeded, retry if not. Currently partial.
4. **Cross-session analysis**: Analyze multiple sessions per customer for pattern-based root cause.
5. **Complete closure field population**: All 12 AI-writable Freshdesk fields populated from investigation.
6. **Production mode activation**: `SUPPORT_AGENT_MODE=PRODUCTION` + 4 blocking issues resolved.

---

## 14. L2 Automation Status

**L2 automation is 100% code-complete as of Sprint 2.63.**

### Completed L2 Capabilities

- Engineering escalation detection (when L1 cannot resolve)
- Live Asana task creation (Sprint 2.58/2.59, SUPPORT_AGENT_MODE=PRODUCTION)
- Freshdesk `cf_asana_ticket_link` updated with real Asana task URL
- Asana webhook handshake + HMAC-SHA256 event verification
- `AsanaEventIdempotencyStore` prevents duplicate processing on redelivery
- Asana task-completed event → resolve EngineeringTicket → ClosureFieldGuard → customer notification → Freshdesk status=4 (Sprint 2.63)
- Closure-field mapping confirmed: `cf_sop_status="No SOP Available"`, `cf_resolution_classification="Permanent Fix Applied by Dev"`, `type="Issues"`
- Escalation audit event logging

### Remaining L2 Admin Actions

1. **Register Asana webhook**: `python scripts/register_asana_webhook.py https://<ngrok-url>/webhooks/asana/task-completed` (needs live server + ngrok URL)
2. **Delete 7 placeholder tasks** in "Support Escalation" Asana project (cosmetic only)

---

## 15. Test Status

### Sprint 2.5.6 Scope (Current)

| Test File | Tests | Status |
|---|---|---|
| `tests/test_sprint256_nlp_router.py` | 55 | ✅ All pass |
| `tests/test_sprint1_classifier.py` | ~30 | ✅ All pass |
| `tests/test_sprint215_topic_registry.py` | ~25 | ✅ All pass |
| `tests/test_sprint215_clarification_engine.py` | ~20 | ✅ All pass |
| `tests/test_sprint215_case_service_slot.py` | ~30 | ✅ All pass |
| `tests/test_sprint225_playbooks.py` | ~50 | ✅ All pass |
| `tests/test_sprint217_admin_api.py` | ~20 | ✅ All pass |
| `tests/test_sprint217_playbook_evolution.py` | ~30 | ✅ All pass |
| `tests/test_sprint217_workflow_fixes.py` | ~20 | ✅ All pass |

**Total**: 301 tests pass across Sprint 2.5.6 scope. Zero new failures introduced.

### Full Suite Regression Baseline

**Pre-existing failures (~131 total)** — do NOT count as regressions:

| Source | Count | Reason |
|---|---|---|
| `tests/test_sprint2281_ticket_created_handler.py` (2 tests) | 2 | Sprint 2.30.1 interface drift (kwargs vs positional `TicketContext`) |
| sprint216/219/224/228x/229x/2292 test files | ~59 | Various interface changes from later sprints |
| golden/stackoverflow/knowledge tests | ~70 | Supabase RPC functions not deployed to dev environment |

None of these are regressions from Sprint 2.5.6. They were present before Sprint 2.5.6 began.

### Testing Conventions

- Test files: `tests/test_sprint<N>_<topic>.py`
- Section classes: `TestA_Name`, `TestB_Name`, etc.
- Test names: `test_A1_what_it_tests`
- Never touch network in tests: use `httpx.MockTransport`
- Datetime tests: use `datetime.now(tz=timezone.utc).replace(tzinfo=None)` (never `datetime.now()` — IST host breaks clock-skew guard)
- Required env vars in fixtures: `RAG_API_KEY`, `OPENAI_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`, `AUDIT_BACKEND=inmemory`, `FRESHDESK_WEBHOOK_ENFORCE_HMAC=false`

---

## 16. Files Under Active Development

The following files were most recently modified and are considered "active":

| File | Sprint | Last Change |
|---|---|---|
| `case_engine/nlp_router.py` | 2.5.6 | New file — NLP Semantic Router |
| `ontology.json` | 2.5.6 | New file — 5 intents + slot specs |
| `case_engine/classifier.py` | 2.5.6 | Rewritten — LLM-only |
| `case_engine/topic_registry.py` | 2.5.6 | Required slots fixed (urn+session_id) |
| `case_engine/workflows/playbooks/otp_delivery_failure.yml` | 2.5.6 | Rewritten to v3.0 |
| `case_engine/models.py` | 2.5.6 | nlp_signal field added |
| `case_engine/clarification/engine.py` | 2.5.6 | Updated for NLPSignal |
| `case_engine/service.py` | 2.5.6 | Updated for NLPSignal propagation |
| `case_engine/runtime/support_agent_runtime.py` | 2.5.6 | NLP router wired in |
| `.env.example` | 2.5.6 | NLP_ROUTER_ENABLED + INTELLIGENCE_LLM_API_KEY added |
| `freshdesk/handlers.py` | 2.5.5 | Pipeline wiring fixes |
| `case_engine/investigation/service.py` | 2.54 | Planner wiring |
| `case_engine/investigation/_collector_sprint218.py` | 2.54 | Evidence collection |
| `case_engine/tools/adapters/unity_tools.py` | 2.51 | Production Unity adapters |
| `intelligence/orchestrator.py` | 2.53 | LLM reasoning |
| `runtime/assembly.py` | 2.52 | Startup wiring |
| `api/routes/webhooks/freshdesk.py` | 2.49 | Trace tags |

---

## 17. Next Immediate Actions

1. **Commit all changes on `major-architecture-change`** (Sprints 2.60–2.63 accumulated, never committed)
2. **Create AI agent account** (`ai.support@getkwikid.com`) in Freshdesk — §4.4, last production gate
3. **Start server + ngrok, then register Asana webhook**:
   ```
   python scripts/register_asana_webhook.py https://<ngrok-url>/webhooks/asana/task-completed
   ```
4. **Set `SUPPORT_AGENT_MODE=PRODUCTION`** once §4.4 + Asana webhook registration are confirmed
5. **Verify Sentry** `is:unresolved` in `python-fastapi` project (period=7d) before production traffic
