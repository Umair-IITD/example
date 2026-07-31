# ROADMAP.md

**KwikID Enterprise Support Agent — Complete Development Roadmap**

Date: 2026-07-21  
Current Position: Sprint 2.5.6 certified. Pre-production on `major-architecture-change` branch.

This document describes what has been built and what remains to build. It does not describe hypothetical future directions — only the planned roadmap as defined in `Big_Phase_2_documentations/02_IMPLEMENTATION_ROADMAP.md` and the Sprint-certified architecture.

---

## Part 1: Completed Waves

### Wave 0 — Phase B1: Knowledge Ingestion Platform

**Goal**: Build the RAG knowledge base that all L1 investigation draws from.

**Delivered**:
- Token-aware chunker (1200 tokens/chunk, 150 overlap, sentence-boundary aware)
- OpenAI embeddings (text-embedding-3-small, 1536 dimensions)
- Supabase pgvector storage with index versioning (v1 → v2)
- Full-text search (FTS) index on chunks (PostgreSQL tsvector + GIN)
- Hybrid retrieval: semantic + FTS + RRF fusion + BM25 reranking
- Circuit-breaker embedding pipeline
- StackOverflow Teams JSON importer
- OCR pipeline for images (RapidOCR + ONNX)
- SOP detection and structured ingestion
- Index version management (`ACTIVE_INDEX_VERSION`, `B1_INDEX_VERSION`)
- 6 SQL migration files (B1_001–B1_006)

**Status**: FROZEN at v2.1. Not under active development. Changes require re-ingestion run.

**Remaining debt**: SQL migrations B1_007 (FTS for RAG tables) and B1_008 (FTS RPC) must be applied to production Supabase. Not yet deployed.

---

### Wave 1 — Phase B2/B3: Case Engine Foundation (Sprints 2.11–2.17)

**Goal**: Build the case lifecycle, slot filling, workflow engine, and playbooks.

**Delivered**:
- `Case` dataclass, `CaseState` state machine (8 states)
- `CaseService` with `receive_message()`, `get_slot_state()`, `start_workflow()`, `resume_workflow()`
- `TopicKey` enum (5 families + UNKNOWN)
- `ClassificationResult` with confidence and tier_used
- `topic_registry.py` — slot definitions per topic (required vs optional)
- `ClarificationEngine` — ask customers for missing URN/Session ID
- `WorkflowDefinition`, `WorkflowStep`, `WorkflowStepType` models
- `PlaybookRegistry` — loads and validates YAML playbooks
- 5 YAML playbooks (v2.0) with full pipeline steps
- `WorkflowEngine` — deterministic step executor
- `WorkflowConsistencyChecker` — prevents double-start, invalid transitions
- Action Gateway with risk model (SAFE/REVERSIBLE/HIGH)
- Approval routing for REVERSIBLE/HIGH actions
- SQL migration `S2_008_workflow_state.sql` — workflow columns in cases table
- Admin API endpoints for workflow visibility

**Status**: COMPLETE. Workflow engine and playbooks are the backbone of the system.

---

### Wave 2 — Investigation Layer (Sprints 2.18–2.24)

**Goal**: Automate L1 investigation by replacing manual portal lookups with tool calls.

**Delivered**:
- `EvidenceBundle` model and full investigation domain models
- `InvestigationPlanner` — reads playbook `investigation_steps` metadata
- `EvidenceCollector` — executes tool call sequences
- `RootCauseEngine` — analysis of evidence for root cause candidates
- `ObservationGenerator` — formats evidence into L1 observation notes
- `InvestigationService` — orchestrates the full investigation flow
- Investigation tool framework (interface contracts)
- Mock tools (GetUserDetailsTool, GetSessionDetailsTool, GetFailureReasonTool, GetCaseHistoryTool, GetOnboardingStatusTool) — later replaced with real adapters
- INVESTIGATE step type added to `WorkflowStepType`
- `investigation_result` field on `WorkflowExecutionResult`
- PROPOSE_ACTION guard (cannot propose action before investigation completes)
- Investigation admin API
- Business pipeline contract: `StageStatus`, `StageSeverity`, `StageResult`
- Business pipeline stages (5 concrete adapters)

**Status**: COMPLETE. Mock tools replaced in Wave 3.

---

### Wave 3 — External Platform Integration (Sprints 2.48–2.53)

**Goal**: Replace all mocks with real production adapters. Wire Freshdesk, Unity Bank, and Uptime Kuma.

**Sprint 2.48 — Freshdesk Integration Hardening**:
- `FreshdeskClient` — full API wrapper
- `FreshdeskResponseService` — sole approved write path
- `ClosureFieldGuard` — prevents HTTP 422 on status=4/5 writes
- `ReplySafetyGate` — gates all public reply sends
- `WebhookIdempotencyStore` — prevents double-processing
- Webhook receiver wired into investigation pipeline
- Full Blueprint reconciliation

**Sprint 2.49 — Freshdesk Runtime Traces**:
- `TRACE_FD_01` through `TRACE_FD_10` — canonical trace tags for every Freshdesk API call

**Sprint 2.50 — Uptime Kuma Metrics Platform**:
- `UptimeKumaClient` — async, read-only, retry
- `MetricsToolAdapter` — Tool Framework integration, 6 TRACE tags
- `MetricsEvidence`, `MetricPoint`, `ServiceHealth` domain models
- Mock metrics tools removed, replaced with real adapter

**Sprint 2.51 — Unity Bank Admin Portal Integration**:
- `unity/` package: config, exceptions, models, `TokenManager`, `UnityClient`, `SessionResolver`, normalizer
- 5 real production tool adapters replacing mocks
- 10 `TRACE_UNITY_*` tags
- Critical discovery: auth header is `auth:` not `Authorization: Bearer`; primary ID is `phone_number` not URN

**Sprint 2.52 — Platform Wiring & Startup Validation**:
- `app/startup_validator.py` — 9-check startup sequence
- Auto-registration of 7 production tools at startup
- `STARTUP_READY` event on every successful startup
- Prometheus vs Uptime Kuma auth distinction

**Sprint 2.53 Wave 3 — Intelligence Layer**:
- `intelligence/` package: config, exceptions, models, traces, `ContextBuilder`, `PromptBuilder`, `LLMClient`, `ReasoningParser`, `IntelligenceOrchestrator`
- 5 versioned prompt templates (one per topic family)
- 5 schema-strict response parsers
- 22 Wave-3 trace tags (`TRACE_INTEL_*`)
- Provider-agnostic design (OpenAI / Anthropic / any compatible)

**Status**: COMPLETE. All mocks replaced. Production adapters certified.

---

### Wave 4 — End-to-End Pipeline Wiring (Sprints 2.53 W4A, 2.54, 2.5.5)

**Goal**: Connect all layers into a functioning end-to-end L1 pipeline.

**Sprint 2.53 Wave 4A — Intelligence Runtime Wiring**:
- `IntelligenceOrchestrator` wired into `SupportAgentRuntime` after `EvidenceBundle`
- Hybrid RAG chunks + playbook SOPs wired into `LLMContext`
- 14 `ENTER`/`EXIT` boundary trace tags across all intelligence stages
- Runtime verified with realistic Freshdesk payload replay
- Security review: prompt injection, PII, sanitization

**Sprint 2.54 — Slot Pre-Extraction & Pipeline Wiring**:
- Slot pre-extraction from `NLPSignal.entities` at ticket creation (not deferred)
- 8 Blueprint trace tags for investigation pipeline
- `ticket-updated` handler with OPEN/PENDING resume logic

**Sprint 2.5.5 — Runtime Alignment (7 Blockers)**:
- 7 critical runtime blockers fixed: asyncio.to_thread(), frozen guard, double-planner, OBSGEN→FDNOTE, registry recovery, and 2 pipeline convergence fixes
- End-to-end pipeline functional with real OTP ticket replay

**Status**: COMPLETE. Full pipeline executes end-to-end. DRY_RUN mode.

---

### Wave 4B — NLP Semantic Router (Sprint 2.5.6)

**Goal**: Replace all regex classification with LLM semantic routing.

**Delivered**:
- All Tier 1 regex patterns removed
- `NLPRouter` using OpenAI structured output
- `NLPSignal` dataclass
- `ontology.json` with 5 intents and slot specs
- OTP required slots fixed: {urn, session_id} not {phone_number, channel}
- OTP playbook rewritten to v3.0 (investigation-only, no execution)
- 55 NLP router tests

**Status**: COMPLETE. This is the last certified sprint.

---

## Part 2: Remaining Waves

---

### Wave 5 — Production Readiness (Next Sprint)

**Goal**: Resolve all 4 production blockers and activate real Unity Bank traffic.

**Dependencies**: Sprint 2.5.6 certified (✅ Done). Sprint-2-5-6.md report to be written.

**Deliverables**:

1. **Observer webhook rule** (Freshdesk admin action):
   - Create Observer rule in Freshdesk: "When Reply is sent By Requester → POST to /webhooks/freshdesk/ticket-updated"
   - Test: Customer replies to clarification question → system resumes from Supabase `conversation_state`

2. **HMAC security** (admin + code):
   - Generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
   - Set `FRESHDESK_WEBHOOK_SECRET` in .env AND in Freshdesk webhook configuration
   - Set `FRESHDESK_WEBHOOK_ENFORCE_HMAC=true` in production .env

3. **Dispatch'r rule expansion** (Freshdesk admin action):
   - Extend "AI auto replies" rule to include Unity Bank tickets
   - Test: Real Unity Bank ticket fires AI webhook

4. **Dedicated AI agent account** (Freshdesk admin action):
   - Create `ai.support@getkwikid.com` in Freshdesk
   - Obtain API key for that account
   - Set `FRESHDESK_API_KEY` in production .env to AI account key

5. **Production mode activation**:
   - Set `SUPPORT_AGENT_MODE=PRODUCTION` in production .env
   - Verify `assert_production_ready()` passes at startup
   - Deploy Supabase migrations B1_007 and B1_008

**Exit Criteria**:
- All 4 Freshdesk admin actions completed
- Real Unity Bank ticket processed end-to-end (ticket → investigation → note → reply → resolve)
- Zero HTTP 422 responses from Freshdesk in test run
- Clarification loop: ticket with missing Session ID → system asks → customer replies → system resumes → resolves

---

### Wave 6 — Action Execution Completion

**Goal**: Complete execution pipeline for all 5 action types.

**Dependencies**: Wave 5 complete. Production Unity Bank tokens available for testing.

**Current gap**: Action Gateway correctly classifies risk and routes, but the Executor does not fully call Unity Bank API endpoints for all action types.

**Deliverables** (per action type):

1. **`otp_resend`** — Deferred to L2 by design in OTP v3.0. L2 approval activates Unity API call to trigger OTP resend on correct channel. Requires: Unity `/resend_otp` API endpoint confirmed + L2 approval flow wired.

2. **`vkyc_session_reset`** — Wire Unity API call to reset/invalidate session, generate new session link, send to customer. Add verification: poll session status for SUCCESS/RESET confirmation.

3. **`document_ocr_reprocess`** — Wire Unity API call to trigger OCR retry on uploaded document. Add verification: check OCR result after reprocess.

4. **`agent_session_refresh`** — Wire Unity API call to invalidate agent session cache/token. Add verification: agent can log in after refresh.

5. **`api_callback_retry`** — Wire Unity API call to replay failed CBS/SFDC callback. Add verification: poll callback delivery status.

**Per action type, also deliver**:
- Verification step (re-query post-action to confirm success)
- Recovery step (retry on failure, dead-letter if retry exhausted)
- Audit events for execution, verification, recovery

**Exit Criteria**:
- All 5 action types: executor → Unity API → verification → audit trail
- Failed execution triggers retry (≤3 attempts) then dead-letter
- Integration tests for each action type using Unity API mock transport

---

### Wave 7 — Clarification Loop Completion

**Goal**: Full end-to-end clarification loop working in production.

**Dependencies**: Wave 5 (Observer webhook created). Wave 6 (action execution complete).

**Current gap**: The `/webhooks/freshdesk/ticket-updated` endpoint exists but the resume logic has not been tested with a real Freshdesk reply payload.

**Deliverables**:

1. **Observer webhook handler** (`/webhooks/freshdesk/ticket-updated`):
   - Parse `latest_comment` to detect: customer reply (incoming=true, private=false) vs agent note vs internal note
   - Load `conversation_state` from Supabase for the ticket
   - Resume `CaseService.receive_message()` with customer's reply text
   - Continue slot filling and pipeline

2. **Slot validation on resume**:
   - Customer provides URN/Session ID in reply → validate format → fill slot
   - Invalid format → re-ask (up to `max_attempts=2`) → escalate on repeat failure
   - Valid → continue to investigation

3. **Multi-turn clarification**:
   - If agent_portal topic requires both `agent_id` AND `portal_type`, system may need 2 clarification turns
   - Each turn saves state, resumes correctly

4. **Edge cases**:
   - Customer replies after ticket has been resolved: handle gracefully (re-open? log and ignore?)
   - Customer replies with unrelated content: retry clarification question
   - Customer replies after `max_attempts` already exceeded: graceful escalation

**Exit Criteria**:
- End-to-end: ticket arrives → system asks "Please provide Session ID" → customer replies → system resumes → investigation runs → ticket resolved
- All clarification slot validation paths tested
- Multi-turn clarification works for topics requiring 2+ rounds

---

### Wave 8 — Asana L2 Integration (Production Mode)

**Goal**: Switch Asana task creation from DRY_RUN to live, and close the L2 loop.

**Dependencies**: Wave 5. Asana API credentials configured.

**Deliverables**:

1. **Live Asana task creation**:
   - Set `SUPPORT_AGENT_MODE=PRODUCTION` + configure `ASANA_API_KEY` and `ASANA_PROJECT_ID`
   - `EngineeringEscalationService` creates real Asana task on escalation
   - Task contains: root cause, evidence package, session details, logs, reproduction steps

2. **`cf_asana_ticket_link` population**:
   - After Asana task is created, Freshdesk `cf_asana_ticket_link` field updated with real task URL
   - Freshdesk internal note includes Asana task link for L2 visibility

3. **Asana → Freshdesk resolution loop** (the "closed loop"):
   - When Asana task is marked Complete by engineering, Freshdesk ticket is updated
   - Options: webhook from Asana, periodic polling, or n8n connector
   - On completion: post resolution note to Freshdesk, update closure fields, close ticket

4. **L2 observation package**:
   - Structured engineering brief as Asana task description:
     - Issue classification
     - Customer impact (# attempts, session timeline)
     - Root cause hypothesis (from L1 investigation)
     - Evidence: session logs, API responses, error codes
     - Reproduction steps
     - First-response SOP steps already tried

**Exit Criteria**:
- End-to-end escalation: L1 investigation → cannot resolve → Asana task created → task URL in Freshdesk → engineering fixes → Asana closed → Freshdesk updated → ticket closed
- `ASANACREATE_DRY_RUN` never appears in production logs after Wave 8

---

### Wave 9 — Additional Bank Clients (Multi-Tenant Expansion)

**Goal**: Onboard Bank of Baroda (BOB) and other bank clients.

**Dependencies**: Unity Bank production stable. Wave 5–8 complete.

**Each bank onboarding requires**:

1. **Discovery** (read SOT pattern):
   - Audit bank's admin portal: endpoints, auth mechanism, response formats
   - Create `Source_Of_Truth/{BankName}_discovery/` with API reference, auth, field mappings
   - Map bank's session status values to internal `SessionStatus` enum

2. **Tool Adapters** (follow Unity pattern):
   - Create `{bank}/` package: config, models, token_manager, client, session_resolver, normalizer, traces
   - Implement all 5 tool adapter interfaces (GetUserDetails, GetSessionDetails, etc.)
   - Tests using MockTransport (no real network in tests)

3. **Tenant Registry**:
   - Add bank to tenant registry: email domain mapping, portal_url, enabled_tools, workflow_overrides
   - `cf_clients` value for the bank's Freshdesk tickets

4. **Playbook Overrides** (if needed):
   - If bank's portal API differs significantly, create bank-specific workflow overrides in TenantContext
   - Required slots may differ by bank

5. **Freshdesk Rules**:
   - Extend Dispatch'r rule to include bank's email domain
   - Set `cf_clients` correctly for bank's tickets

6. **Certification**:
   - End-to-end test with real bank ticket
   - Regression run confirming Unity Bank not affected

**Exit Criteria per bank**: Real ticket from that bank's email domain processed end-to-end without touching Unity Bank tools or data.

---

### Wave 10 — Video Analysis

**Goal**: Analyze recorded VKYC session video to find visual root causes.

**Dependencies**: Wave 6 complete. Vision model API access confirmed.

**Current gap**: Session video retrieval (`GET /api/v1/getVideoFile/{video_token}`) is in the Unity API but not yet called. Video analysis is defined in Blueprint §12 as a future capability.

**Deliverables**:

1. **`GetVideoFileTool`**: Call Unity API to retrieve video token → download session recording
2. **Video frame extraction**: Sample key frames from the recording (not full video — too large for LLM context)
3. **Vision model integration**: Send frames to vision-capable LLM with investigation prompt: "What do you see that explains this VKYC failure?"
4. **Video evidence model**: `VideoEvidence` added to `EvidenceBundle`
5. **Observation integration**: Video findings included in L1 observation note

**Relevant failure types for video analysis**:
- Camera not working (blank frame, glitchy video)
- Lighting issues (too dark, backlit)
- Liveness check failure (customer too far, face not centered)
- Document presentation issues (document blurry, at wrong angle)
- Connection quality issues (pixelation patterns)

**Exit Criteria**: VKYC session with "liveness check failure" — investigation note includes video frame analysis with specific finding ("customer's face was not visible in 8 of 10 liveness frames").

---

### Wave 11 — Cross-Session Pattern Analysis

**Goal**: Detect systematic failures affecting multiple customers.

**Dependencies**: Wave 6, 7 complete. Multiple real tickets processed.

**Current gap**: Each ticket is investigated independently. A system issue (Unity Bank portal down, SMS gateway degraded) may cause 50 simultaneous OTP failures — the current system investigates each one separately, misses the pattern.

**Deliverables**:

1. **Multi-session lookup**: Given a time window, fetch all sessions with similar failure codes
2. **Pattern detector**: Detect: same error code for >N sessions in 1 hour → systematic failure
3. **Service correlation**: Correlate session failures with Uptime Kuma monitor status (was the VKYC server DOWN at that time?)
4. **Batch investigation**: For systematic failures, investigate once → apply finding to all affected tickets
5. **Infrastructure alert → ticket link**: If Uptime Kuma shows monitor DOWN, automatically link to all tickets from that time window

---

## Part 3: Production Milestones

### Milestone 1 — First Real Ticket (Target: After Wave 5)

The system processes its first real Unity Bank Freshdesk ticket autonomously, posts a correct internal investigation note, and either resolves or escalates correctly.

**Gate criteria**:
- All 4 production blockers resolved
- One real Unity Bank ticket processed in PRODUCTION mode
- Freshdesk note contains accurate investigation findings (manual verification)
- No HTTP 422, no uncaught exceptions, no silent failures

---

### Milestone 2 — Clarification Loop Live (After Wave 7)

The system completes its first end-to-end conversation: asks for missing information, customer replies, system resumes and resolves.

**Gate criteria**:
- Observer webhook firing confirmed in Freshdesk
- Supabase `conversation_state` persists correctly across webhook calls
- At least 3 real tickets resolved through clarification loop

---

### Milestone 3 — L1 Autonomous (After Waves 6, 7)

The system handles ≥70% of incoming Unity Bank tickets autonomously without human intervention, with correct resolution or escalation.

**Gate criteria**:
- 30-day production run on Unity Bank tickets
- Classification accuracy >85% (per CLAUDE.md Level 1 gate)
- Private note accuracy: L1 support agent reviews AI notes and confirms findings are correct on >90% of tickets
- No customer-facing errors (no incorrect public replies)
- Zero closure HTTP 422s

---

### Milestone 4 — Asana Loop Live (After Wave 8)

Engineering escalations are created as real Asana tasks and the loop closes when engineering marks the task complete.

**Gate criteria**:
- 10 real escalations created as Asana tasks
- At least 3 escalations fully closed (Asana complete → Freshdesk updated → ticket closed)

---

### Milestone 5 — Level 2 (Multi-Client) (After Wave 9)

Bank of Baroda onboarded as second client. Both Unity Bank and BOB handled without cross-contamination.

**Gate criteria**:
- BOB tickets correctly identified and routed to BOB tool adapters
- Unity Bank tickets unaffected by BOB onboarding
- Tenant isolation test: BOB knowledge never retrieved for Unity Bank tickets

---

## Part 4: Infrastructure & Operational Roadmap

### n8n Migration

Currently, Freshdesk webhook fires to `https://n8n.app.getkwikid.com/webhook/freshdesk-ticket-created`, which forwards to the FastAPI service. This n8n hop adds latency and a potential failure point.

**Future**: Direct Freshdesk → FastAPI routing (remove n8n from the critical path). n8n stays for non-critical automations (daily reports, batch tasks).

**Timeline**: After Milestone 1 (first real ticket). Not a blocker for production but removes a dependency.

### Observability & Monitoring

**Currently implemented**:
- Prometheus metrics endpoint (`/metrics`)
- Uptime Kuma integration (monitoring KwikID services)
- Trace tags: TRACE_FD_01–10, TRACE_UNITY_01–10, TRACE_METRICS_01–06, TRACE_INTEL_01–22
- Audit events in Supabase (40+ event types)
- Sentry integration defined in CLAUDE.md (org: think360-n0, project: python-fastapi) but `sentry-sdk` not yet in requirements.txt

**Remaining**:
- Sentry SDK installed and configured (`SENTRY_DSN` in .env)
- Grafana dashboard for AI-specific metrics (classification confidence distribution, investigation duration, action execution rate)
- Alert: classification confidence drops below 0.85 for >10% of tickets in 1 hour
- Alert: investigation duration >30s (Unity API latency degradation)
- Alert: Freshdesk write failure rate >1%

### Security Hardening

**Currently done**:
- `exclude_escalation=True` in `case_engine/knowledge/rag_adapter.py:83` — PERMANENT, never revert
- HMAC webhook verification implemented (not yet enforced in .env)
- PII discipline: log email domains only, never ticket body, never API keys
- Supabase service role key treated as root access

**Remaining**:
- `FRESHDESK_WEBHOOK_ENFORCE_HMAC=true` in production (Wave 5)
- Rotate Supabase service role key before production go-live
- Security scan on every commit: `python scripts/security_scan.py` (exit code 0 required)
- Tenant isolation audit: confirm no cross-tenant data leakage in retrieval

### Level 3 (Long-Term, Only If Level 2 Hits Capacity)

Per `Big_Phase_2_documentations/02_IMPLEMENTATION_ROADMAP.md`:

> Level 3 is NOT a default milestone. Only begin when Level 2 is stable in production AND throughput ceiling is actually reached.

Level 3 would add:
- Kafka/MSK event bus (only if webhook-to-processing latency is the bottleneck)
- Supervisor orchestrator with specialized workers per topic
- Zero-copy connector to live bank CBS registry (requires bank-approved contract + security review)
- Immutable audit trail with cryptographic integrity
- Customer-facing conversational interface (requires evaluation framework + legal sign-off)

Do not build Level 3 components speculatively. Do not build customer-facing write pipelines without legal sign-off.

---

## Summary: Wave Sequence

```
Wave 0   [DONE] Knowledge Ingestion Platform (B1)
Wave 1   [DONE] Case Engine, Slot Filling, Workflow Engine, Playbooks
Wave 2   [DONE] Investigation Layer (Planner, Collector, RCA, Observation)
Wave 3   [DONE] Freshdesk + Unity Bank + Uptime Kuma production adapters
Wave 4   [DONE] End-to-end pipeline wiring + Intelligence Orchestrator
Wave 4B  [DONE] LLM Semantic Router — Sprint 2.5.6

Wave 5   [NEXT] Production Readiness — 4 blockers + PRODUCTION mode
Wave 6         Action Execution Completion (all 5 action types)
Wave 7         Clarification Loop Completion (Observer webhook + resume)
Wave 8         Asana L2 Integration (live tasks, closed loop)
Wave 9         Additional Bank Clients (BOB, CBI, etc.)
Wave 10        Video Analysis (vision model for VKYC session video)
Wave 11        Cross-Session Pattern Analysis

Milestone 1    [After Wave 5] First real Unity Bank ticket processed
Milestone 2    [After Wave 7] First clarification loop completed
Milestone 3    [After Waves 6+7] L1 autonomous on ≥70% of tickets
Milestone 4    [After Wave 8] Asana loop live
Milestone 5    [After Wave 9] BOB onboarded

Level 3        [Only if Level 2 hits throughput ceiling] Kafka, compliance layer
```
