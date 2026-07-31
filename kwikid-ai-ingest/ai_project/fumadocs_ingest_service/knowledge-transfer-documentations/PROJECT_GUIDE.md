# KwikID AI Ingest Service — Architectural Guide

**For:** Engineers taking over the project. Assumes familiarity with Python/FastAPI but not with this codebase.

---

## 1. What This System Does

KwikID is a Video KYC (VKYC) platform used by Indian banks to onboard customers digitally. When a customer fails VKYC — due to OTP not arriving, session dropout, camera issues, etc. — the bank's support agent files a Freshdesk ticket.

This service is an AI agent that:
1. Receives that Freshdesk ticket via webhook (L1 investigation)
2. Investigates the failure using real-time APIs (Unity Admin Portal + Grafana Loki + Uptime Kuma)
3. Reasons over the evidence using GPT-4o-mini
4. Posts a structured investigation note back to the Freshdesk ticket
5. If engineering is required, creates an Asana task (L2 escalation)
6. When engineering resolves the issue, automatically closes the Freshdesk ticket (L2 resolution)

The entire pipeline from webhook to observation note takes 20–60 seconds and runs asynchronously (the webhook returns 200 OK immediately; all work happens in a `BackgroundTask`).

---

## 2. Code Directory Map (Blueprint → Code)

The `flow_diagram.mermaid` subgraphs map to code directories as follows:

### `External [External Interfaces]`
- **Freshdesk** → `freshdesk/` (client, verifier, handlers, response service)
- **Asana** → `asana/` (client, webhook receiver)
- **OpenAI LLM** → `intelligence/` (orchestrator, llm_client)

### `Core [Ingestion & Case Management]`
- **Ticket Ingestion Service** → `api/routes/webhooks/freshdesk.py` + `freshdesk/handlers.py`
- **Case Service** → `case_engine/service.py`
- **Cases DB** → Supabase `cases` table via `case_engine/repository.py`

### `Tenant [Multi-Tenant Resolution]`
- **Client Resolution Engine** → `case_engine/tenant/resolver.py`
- **Tenant Registry** → `case_engine/tenant/registry.py`
- **Tenant Context** → `case_engine/tenant/models.py`

### `AI [AI & Reasoning Engine]`
- **NLP (LLM Semantic Router)** → `case_engine/nlp_router.py`
- **Topic Classification + Slot Extraction** → `case_engine/classifier.py` + signals from NLPSignal
- **Clarification Engine** → `case_engine/clarification/`
- **Workflow Selection + Engine** → `case_engine/workflows/workflow_engine.py`
- **Investigation Planner + Evidence Collector** → `case_engine/investigation/`
- **Root Cause Engine** → `case_engine/investigation/root_cause.py`
- **Reasoning Engine** → `intelligence/orchestrator.py`
- **Guardrails** → `freshdesk/safety_gate.py` (ReplySafetyGate)
- **Observation Generator** → `case_engine/investigation/observation.py`
- **Intent Ontology** → `ontology.json` (project root)

### `Knowledge [Knowledge Layer]`
- **Knowledge Ingestion** → `scripts/ingest_knowledge.py`, `scripts/ingest_sop.py`
- **Hybrid RAG** → `retrieval/` (fusion.py, pipeline.py, semantic.py, keyword.py)
- **Workflow Playbooks** → `case_engine/workflows/playbooks/*.yml`
- **Seed SOPs** → `runtime/assembly.py::_build_seed_knowledge_entries()`

### `AdminPortal [Multi-Tenant Admin Portal APIs]`
- **Unity Admin Portal** → `unity/` (client.py, session_resolver.py, token_manager.py)
- **5 Production Tools** → `case_engine/tools/adapters/unity_tools.py`

### `LogPlatform [Multi-Tenant Log Platform — Grafana Loki]`
- **Per-Tenant Log Registry** → `case_engine/integrations/loki/client.py::LOKI_REGISTRY`
- **Log Platform API** → `case_engine/integrations/loki/client.py::LokiClient`

### `Tools [Tool Registry & Investigation APIs]`
- **Tool Registry** → `case_engine/tools/tool_registry.py`
- **Tool Executor** → `case_engine/tools/tool_executor.py`
- **GetSessionLogsTool** → `case_engine/tools/adapters/log_tools.py`
- **Log Relevance Extractor (PII + BM25)** → `case_engine/integrations/loki/relevance.py`
- **MetricTool + ServerTool** → `case_engine/tools/adapters/metrics_tool.py`

### `Execution [Action & Resolution Layer]` (Reserved — not active in L1)
- **Action Gateway** → `case_engine/action_gateway.py`
- **Executors** → `executors/` (AddTicketNoteExecutor, UpdateTicketStatusExecutor, IdentityResetOtpExecutor)

### `Governance [Audit & Compliance]`
- **Audit Service** → `audit/service.py`, `audit/logger.py`
- **Audit Events DB** → Supabase `audit_events` table

### Runtime Wiring (not in flow diagram but critical)
- **ProductionRuntime** → `runtime/assembly.py` (single composition point)
- **Startup Validation** → `runtime/startup_validation.py`
- **Application Entry Point** → `app/main.py`

---

## 3. The Complete Lifecycle of a Support Ticket

### Phase 0: Ticket Arrives in Freshdesk

1. A bank agent (e.g., from Unity Bank) emails `support@getkwikid.com` or files a ticket via the Freshdesk portal.
2. Freshdesk creates the ticket. A **Dispatch'r rule** fires immediately:
   - Adds tag `client:unity_bank` to identify the tenant
   - Triggers a webhook `POST /webhooks/freshdesk/ticket-created`
   - Header: `X-Webhook-Token: <FRESHDESK_WEBHOOK_SECRET>` (or HMAC of body)

---

### Phase 1: Webhook Ingestion

**Code:** `api/routes/webhooks/freshdesk.py`

3. The FastAPI endpoint receives the request.
4. `freshdesk/verifier.py::WebhookVerifier.verify()` checks the HMAC signature.
   - If invalid → returns `403 Forbidden` immediately
   - If `FRESHDESK_WEBHOOK_ENFORCE_HMAC=false` → skips (dev only)
5. `freshdesk/idempotency.py::WebhookIdempotencyStore.is_duplicate()` checks if this `ticket_id` + `event_type` combination was already processed.
   - If duplicate → returns `200 OK` (idempotent), logs `DUPLICATE_EVENT`
6. Returns `200 OK` **synchronously** (within the 10-second budget).
7. Dispatches `BackgroundTask: handle_ticket_created(ticket_id, payload, app.state)`.

---

### Phase 2: NLU Classification

**Code:** `freshdesk/handlers.py::TicketCreatedHandler.handle()` → `case_engine/nlp_router.py`

8. Handler extracts: `ticket_id`, `client_id` (from `client:` tag), `ticket_body` (HTML stripped to text).
9. **NLP Router** is called: `NLPRouter.route(raw_ticket_text, tenant_context)`.
   - Calls OpenAI GPT-4o-mini with the ontology-based system prompt (built from `ontology.json`)
   - System prompt includes:
     - 8 domain intents with descriptions and required slots
     - KEYWORD→INTENT disambiguation table (e.g., "audio / mic → VKYC_SESSION_FAILURE")
     - Rules for negation handling, slot requirements, confidence calibration
   - Returns structured JSON parsed into `NLPSignal`:
     - `intent`: one of 8 canonical intents, or `UNKNOWN`
     - `entities`: dict of `{urn, session_id, phone_number, agent_id, application_id, callback_type}`
     - `negation_detected`: bool
     - `confidence`: 0.0–1.0
     - `needs_clarification`: bool (True if required slots are absent)
     - `clarification_question`: str or None
10. Logs `NLP_ROUTER_SIGNAL` at WARNING level (intent, confidence, negation, missing slots). **NEVER logs `raw_text`** (PII).

---

### Phase 3A: Clarification Loop (if URN/Session ID missing)

**Code:** `case_engine/clarification/engine.py`

11. If `signal.needs_clarification=True`:
    - `ClarificationEngine` generates a question (from ontology or NLPSignal)
    - Example: "To investigate this OTP delivery issue, please provide the URN and the VKYC Session ID (format: KID-XXXXXXXX)."
    - `FreshdeskResponseService.add_internal_note()` posts the question as a **private** Freshdesk note
    - Handler returns. Pipeline is paused.

12. Bank agent sees the note, replies to the Freshdesk ticket with the missing info.
13. Freshdesk **Observer rule** fires: `POST /webhooks/freshdesk/ticket-updated`
14. `freshdesk/handlers.py::TicketUpdatedHandler.handle()` runs:
    - Extracts `latest_comment.body_text` (the customer's reply)
    - If empty body → logs `EMPTY_COMMENT_BODY` WARNING and skips (Sprint 2.64+ fix)
    - Re-runs `NLPRouter.route()` on the reply text to extract URN/Session ID slots
    - If slots now present → proceeds to Phase 3B (investigation)
    - If still missing → posts another clarification question

---

### Phase 3B: Full Investigation Pipeline

**Code:** `case_engine/runtime/support_agent_runtime.py::SupportAgentRuntime.run_case()`
→ `case_engine/ticket_orchestration/orchestrator.py::TicketOrchestrator`

15. `SupportAgentRuntime.run_case()` is the agent's main entry point.
16. `TicketOrchestrator` orchestrates the investigation in phases.

**Phase A: Unity Admin Portal (session metadata — READ-ONLY)**

17. `asyncio.to_thread()` wraps each Unity API call (blocking I/O).
18. JWT token management: `unity/token_manager.py` acquires/refreshes JWT automatically (TTL=12h, refresh 2 min early).
19. Auth header: `auth: <token>` (NOT `Authorization: Bearer`).
20. Tools called in parallel or sequence depending on intent:
    - `GetUserDetails(phone_number)` → customer profile (name, registered phone, email)
    - `GetSessionDetails(session_id)` → session status, timeline, auditor decision, video URL
    - `GetFailureReason(session_id)` → root cause code from portal
    - `GetCaseHistory(urn)` → prior sessions for this customer
    - `GetOnboardingStatus(urn)` → current onboarding stage (AADHAAR/PAN/VKYC)

**Phase B: Grafana Loki Log Platform (backend/infra logs — READ-ONLY)**

21. `GetSessionLogsTool.run(session_id, time_window)` calls `LokiClient`.
22. `LokiClient` queries the per-tenant Loki datasource using `LOKI_REGISTRY` to resolve base URL + credentials.
23. Returns raw log lines for the session time window.
24. **CRITICAL: `_redact_and_truncate()` is called BEFORE BM25 scoring** (`case_engine/integrations/loki/relevance.py::parse_line()`).
    - Redacts: URN patterns, phone numbers, email-like strings, JWT tokens, IP addresses
    - Truncates: lines > 500 chars
25. `bm25_score_list()` scores the redacted log lines against the ticket query.
26. Top-N log lines passed to LLM context.

**Phase C: Uptime Kuma Metrics**

27. `MetricTool.run(slug)` queries `https://status.getkwikid.com:3001/metrics` (Prometheus endpoint).
    - Auth: `Authorization: Bearer <METRICS_PLATFORM_API_KEY>`
28. `ServerTool.run()` queries Uptime Kuma heartbeat status.
29. Returns `MetricsEvidence(uptime_percent, active_incidents, service_health)`.

---

### Phase 4: Root Cause + Knowledge Retrieval

**Code:** `case_engine/investigation/root_cause.py` + `case_engine/knowledge/service.py`

30. `RootCauseEngine` analyzes the evidence bundle:
    - Classifies failure type (SMS_DELIVERY_FAILURE, EXPIRED_SESSION, LIVENESS_FAILURE, etc.)
    - Sets `recommended_action` (OTP_RESEND, SESSION_RESET, MANUAL_REVIEW, ESCALATE)
    - Sets `confidence` (0.0–1.0)

31. `KnowledgeOrchestrator` queries the Hybrid RAG:
    - Semantic search (pgvector) + keyword search (PostgreSQL FTS) + BM25 reranking
    - Fusion via Reciprocal Rank Fusion (RRF)
    - `exclude_escalation=True` — escalation playbooks are NEVER shown to the AI as potential automated resolutions
    - Top-3 matching knowledge entries (SOPs/runbooks) are retrieved

---

### Phase 5: LLM Reasoning

**Code:** `intelligence/orchestrator.py::IntelligenceOrchestrator`

32. `IntelligenceOrchestrator.reason()` builds `LLMContext`:
    - Evidence bundle (Unity + Loki + Metrics results)
    - Top-3 SOP chunks from Hybrid RAG
    - Active playbook steps (from `case_engine/workflows/playbooks/*.yml`)
    - Root cause analysis
    - Ticket history (conversation state)

33. Calls OpenAI GPT-4o-mini with structured JSON prompt (5 versioned templates in `intelligence/prompts/`).
    - Temperature: 0.2 (near-deterministic)
    - Response format: JSON (`json_mode=True`)
    - Max output: 1200 tokens

34. Parses `IntelligenceResult`:
    - `conclusion`: what happened (2–4 sentences)
    - `root_cause`: technical root cause code
    - `recommended_action`: what should be done
    - `confidence`: 0.0–1.0
    - `requires_l2_escalation`: bool

35. If `confidence < INTELLIGENCE_CONFIDENCE_THRESHOLD (0.75)` → routes to clarification instead of resolution.

---

### Phase 6: Observation Generation + Internal Note

**Code:** `case_engine/investigation/observation.py` + `freshdesk/response_service.py`

36. `ObservationGenerator` formats the full evidence bundle + LLM conclusion into a structured internal note:
    ```
    === L1 INVESTIGATION REPORT ===
    Intent: OTP_DELIVERY_FAILURE | Confidence: 0.92
    URN: URN12345678 | Session: KID-AB123456
    
    UNITY PORTAL FINDINGS:
    - Session status: FAILED (SEND_SMS_FAILURE)
    - Phone on record: +91 98765-XXXXX
    - OTP generated: YES | Delivery: FAILED
    
    LOKI LOG EVIDENCE:
    - 10:32:14 SMS gateway timeout (provider: Airtel) ...
    - 10:32:15 SEND_SMS_FAILURE code=504 ...
    
    ROOT CAUSE: SMS_DELIVERY_FAILURE
    RECOMMENDED ACTION: OTP_RESEND
    
    LLM CONCLUSION: [from IntelligenceOrchestrator]
    SOP REFERENCE: [from RAG — sop-otp-001]
    ```

37. `FreshdeskResponseService.add_internal_note(ticket_id, body)`:
    - Emits `TRACE_FD_07_NOTE_PREPARED` and `TRACE_FD_08_NOTE_SENT` traces
    - Calls `FreshdeskClient.add_private_note()` (HTTP PUT to Freshdesk API)
    - Never raises — returns `{}` on failure, logs specific error (including 404 if ticket missing)

---

### Phase 7: L2 Check

**Code:** `case_engine/engineering/service.py::EngineeringEscalationService`

38. Checks if `requires_l2_escalation=True` (from IntelligenceResult) OR if the playbook hit an ESCALATE step.

**7A: No L2 needed (direct reply path):**

39. `_gated_customer_reply()` in `api/routes/webhooks/freshdesk.py` runs the `ReplySafetyGate`:
    - Confidence gate: `confidence >= threshold` (default: low)
    - Force-escalation gate: `impact not in force_escalation_impacts`
    - Duplicate gate: `body_hash not in seen_hashes`
    - Kill switch: `REPLY_SAFETY_KILL_SWITCH != true`
    - If ANY gate fails → post reply as **draft internal note** instead
40. If passes: `FreshdeskResponseService.send_customer_reply(ticket_id, body)` sends the reply to the customer.
41. If customer confirms resolution → `ClosureFieldGuard` → ticket moved to status=4 (Resolved).

**7B: L2 needed (engineering escalation):**

42. `AsanaClient.create_task()`:
    - Creates task in "Support Escalation" project
    - Section: "New - Needs Triage"
    - HTML description: full investigation summary with Loki log snippets, root cause, SOPs
    - Custom fields: Priority (Critical/High/Medium/Low), Task Progress (0%)
    - If Asana returns 400: logs full HTTP response body with guidance to check ASANA_PROJECT_ID

43. `FreshdeskResponseService.update_ticket_fields()` sets `cf_asana_ticket_link` on the Freshdesk ticket.

44. Escalation notice sent to customer (via `_gated_customer_reply()` → `ReplySafetyGate`):
    - "Your issue has been escalated to our engineering team. Reference: [Asana task link]"

---

### Phase 8: Engineering Resolution (L2 Loop Closure)

**Code:** `asana/webhook.py` + `api/routes/webhooks/asana.py` + `case_engine/engineering/service.py`

45. Engineering team investigates, fixes the issue, marks the Asana task as "Completed".
46. Asana fires webhook to `POST /webhooks/asana/task-completed` (registered via `scripts/register_asana_webhook.py`).
47. `asana/webhook.py::AsanaWebhookReceiver.verify()` checks `X-Hook-Secret` header.
48. `EngineeringEscalationService.handle_task_completed(task_gid)`:
    - Looks up which Freshdesk ticket is linked to this Asana task GID
    - Retrieves current ticket state from Freshdesk
49. `ClosureFieldGuard.guard_status_transition(ticket, from_status=None, to_status=4)`:
    - Checks all 4 required fields: `cf_clients`, `ticket_type`, `cf_sop_status`, `cf_resolution_classification`
    - If any missing → posts internal note "Manual closure required — missing closure fields" → **HALT**
    - If all present → allows transition
50. `ReplySafetyGate` gates the resolution reply (confidence=1.0 template, kill-switch + duplicate checks).
51. `FreshdeskResponseService.send_customer_reply()` sends: "Your issue has been resolved. [Summary of fix]"
52. `FreshdeskResponseService.update_ticket_fields(status=4)` resolves the ticket.
53. Ticket is **CLOSED**. The lifecycle is complete.

---

## 4. Critical Invariants (Never Violate)

These are not preferences — they are hard architectural constraints enforced by code and tests.

| Invariant | Where Enforced | What Breaks if Violated |
|-----------|---------------|------------------------|
| NLU only in `case_engine/nlp_router.py` | CLAUDE.md rule | Architecture drift, harder to maintain |
| NLG only in `intelligence/orchestrator.py` + `freshdesk/responses.py` | CLAUDE.md rule | Same |
| `FreshdeskResponseService` is sole write path | CLAUDE.md, code review | Bypasses traces, audit, metrics, error handling |
| `ClosureFieldGuard` before every `status=4/5` PUT | `freshdesk/closure_guard.py` | Freshdesk returns HTTP 422 |
| `ReplySafetyGate` gates every customer reply | `api/routes/webhooks/freshdesk.py` | Duplicate/unsafe replies sent to customers |
| `exclude_escalation=True` in RAG | `case_engine/knowledge/rag_adapter.py:83` | Escalation instructions returned as automated resolutions |
| PII redaction BEFORE BM25 | `case_engine/integrations/loki/relevance.py` | PII reaches LLM context |
| `asyncio.to_thread()` for all blocking I/O | CLAUDE.md | Event loop blocking, request timeouts |
| `NLPSignal.raw_text` never logged | `case_engine/nlp_router.py` | PII (ticket content) in log files |

---

## 5. Data Flow Diagram (Text)

```
                          Freshdesk
                              │
                              ▼ webhook
                    HMAC Verification
                              │
                    Idempotency Check
                              │
                    200 OK ◄──┤ (synchronous)
                              │
                    BackgroundTask
                              │
                    NLP Router (GPT-4o-mini)
                    ┌─────────┴──────────┐
                    │ needs_clarification │
                    ▼                    ▼
             Ask Question         Investigation
             Post Note            ┌────────────────┐
             Await reply          │ Unity Portal   │ (5 tools)
                                  │ Loki Logs      │ (PII→BM25)
                                  │ Uptime Kuma    │ (2 tools)
                                  └────────┬───────┘
                                           │
                                   Root Cause Engine
                                           │
                                   Hybrid RAG (SOP)
                                           │
                               Intelligence Orchestrator
                                    (GPT-4o-mini)
                                           │
                               Observation Generator
                                           │
                                    Internal Note ──► Freshdesk
                                           │
                               ┌───────────┴──────────┐
                               │ requires_l2 ?         │
                               ▼                       ▼
                        Asana Task ──► Asana    ReplySafetyGate
                        Update cf_link          ▼
                        Escalation Reply   Customer Reply
                               │               │
                        (engineering)     Close Ticket
                               │
                        Asana Webhook
                               │
                      ClosureFieldGuard
                               │
                      ReplySafetyGate
                               │
                      Resolution Reply ──► Freshdesk
                      status=4           Close Ticket
```
