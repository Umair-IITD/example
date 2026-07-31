# KwikID AI Ingest Service — Current State (Sprint 2.64+)

**Last Updated:** 2026-07-31
**Branch:** `major-architecture-change`
**Status:** LIVE PRODUCTION — Real bank ticket traffic flowing

---

## 1. System Status

```
SUPPORT_AGENT_MODE = PRODUCTION
```

The AI agent is receiving and processing live support tickets from:
- **Unity Bank** (primary tenant, fully configured)
- **RBL Bank** (active)
- **CBI Bank** (active)

The server runs as a FastAPI/uvicorn service. Freshdesk Dispatch'r rules route new tickets via webhook to `/webhooks/freshdesk/ticket-created`. The agent processes them in a background task (10-second synchronous response budget).

Sentry APM is active (`SENTRY_DSN` set). All errors are captured in project `python-fastapi` on `think360-n0.sentry.io`.

---

## 2. L1 Pipeline (Automated Investigation)

The L1 pipeline runs on every new Freshdesk ticket. It produces an **internal observation note** on the Freshdesk ticket — this is the L1 deliverable.

```
Freshdesk Webhook (ticket-created)
  → HMAC Verification (freshdesk/verifier.py)
  → BackgroundTask: TicketCreatedHandler
  → NLP Semantic Router (case_engine/nlp_router.py)         [Layer 2.5 — GPT-4o-mini]
      Intent classification → 8 supported intents
      Entity extraction → URN, Session ID, Agent ID, etc.
      Negation detection → "not receiving", "nahi aa raha"
      Needs clarification? → YES → Post clarification question → await reply
                            → NO  → Proceed to investigation
  → SupportAgentRuntime.run_case() (case_engine/runtime/support_agent_runtime.py)
      Phase A: Unity Admin Portal (5 READ-ONLY tools)
        - GetUserDetails      → customer profile, phone, name
        - GetSessionDetails   → session status, timeline, auditor decision
        - GetFailureReason    → root cause code from portal
        - GetCaseHistory      → prior sessions for the same URN
        - GetOnboardingStatus → current onboarding stage
      Phase B: Grafana Loki Log Platform (1 tool)
        - GetSessionLogsTool  → raw log lines for session time window
        - _redact_and_truncate() called BEFORE BM25 (PII compliance)
        - BM25 relevance scoring → top-N log lines passed to LLM
      Phase C: Uptime Kuma Metrics (2 tools)
        - MetricTool   → service uptime %, incident history
        - ServerTool   → server health, active alerts
  → Root Cause Engine (case_engine/investigation/root_cause.py)
  → Knowledge Layer: Hybrid RAG (BM25 + pgvector + RRF)
      SOP retrieval from Supabase (rag_knowledge_articles + rag_knowledge_chunks)
      exclude_escalation=True — escalation docs excluded from automated replies
  → IntelligenceOrchestrator (intelligence/orchestrator.py) [GPT-4o-mini]
      Structured JSON prompt → conclusion + root_cause + recommended_action + confidence
  → Observation Generator (case_engine/investigation/observation.py)
  → FreshdeskResponseService.add_internal_note()  [SOLE approved write path]
```

**Total registered investigation tools: 8**

| Tool | Package | Backend | Mode |
|------|---------|---------|------|
| GetUserDetails | case_engine/tools/adapters/unity_tools.py | Unity Admin Portal | READ-ONLY |
| GetSessionDetails | case_engine/tools/adapters/unity_tools.py | Unity Admin Portal | READ-ONLY |
| GetFailureReason | case_engine/tools/adapters/unity_tools.py | Unity Admin Portal | READ-ONLY |
| GetCaseHistory | case_engine/tools/adapters/unity_tools.py | Unity Admin Portal | READ-ONLY |
| GetOnboardingStatus | case_engine/tools/adapters/unity_tools.py | Unity Admin Portal | READ-ONLY |
| GetSessionLogsTool | case_engine/tools/adapters/log_tools.py | Grafana Loki | READ-ONLY |
| MetricTool | case_engine/tools/adapters/metrics_tool.py | Uptime Kuma | READ-ONLY |
| ServerTool | case_engine/tools/adapters/metrics_tool.py | Uptime Kuma | READ-ONLY |

---

## 3. L2 Pipeline (Engineering Escalation & Resolution)

The L2 pipeline activates when the L1 investigation concludes that engineering intervention is required.

```
L1 Observation posted to Freshdesk
  → L2 Check: EngineeringEscalationService (case_engine/engineering/service.py)
      IF engineering action needed:
        → AsanaClient.create_task() (asana/client.py)
            Creates task in "Support Escalation" Asana project
            4 sections: New - Needs Triage / In Progress / Blocked - Needs Info / Done
            Custom fields: Priority, Task Progress
            Task description: full investigation summary + evidence + root cause (HTML)
            cf_asana_ticket_link updated on Freshdesk ticket
        → Escalation notice sent to customer (gated through ReplySafetyGate)

Engineering team fixes the issue, marks Asana task as complete:
  → Asana fires webhook → POST /webhooks/asana/task-completed
  → asana/webhook.py verifies X-Hook-Secret header
  → EngineeringEscalationService.handle_task_completed()
  → ClosureFieldGuard.guard_status_transition()
      Checks: cf_clients, ticket_type, cf_sop_status, cf_resolution_classification
      If any missing → post manual-closure note, halt
  → ReplySafetyGate gates the resolution reply
      confidence=1.0 (deterministic template), kill-switch/duplicate checks active
  → FreshdeskResponseService.send_customer_reply() → customer reply sent
  → FreshdeskResponseService.update_ticket_fields(status=4) → ticket Resolved
```

---

## 4. Supported Intent Types (8 Intents — ontology.json)

| Intent | Topic Key | Required Investigation Slots |
|--------|-----------|------------------------------|
| OTP_DELIVERY_FAILURE | OTP_Delivery_Failure | urn, session_id |
| VKYC_SESSION_FAILURE | VKYC_Session_Failure | urn, session_id |
| DOCUMENT_OCR_FAILURE | Document_OCR_Failure | urn, session_id |
| AGENT_PORTAL_ISSUE | Agent_Portal_Issue | agent_id |
| API_CALLBACK_FAILURE | API_Callback_Failure | application_id, callback_type |
| DOCUMENT_GENERATION_FAILURE | Document_Generation_Failure | session_id, urn |
| APPLICATION_DATA_ISSUE | Application_Data_Issue | application_id, session_id |
| SIGNATORY_AUTHORITY_ISSUE | Signatory_Authority_Issue | application_id |

---

## 5. Key Safety Invariants (NEVER change these)

- **NLU/NLG Split**: NLU = `case_engine/nlp_router.py` ONLY. NLG = `intelligence/orchestrator.py` + `freshdesk/responses.py` ONLY.
- **`FreshdeskResponseService` is the sole approved Freshdesk write path.** Never call `FreshdeskClient` write methods directly.
- **`ClosureFieldGuard` gates every `status=4` or `status=5` PUT.** HTTP 422 from Freshdesk if skipped.
- **`ReplySafetyGate` gates every customer-facing reply.** Wired at all 4 call sites in `api/routes/webhooks/freshdesk.py`.
- **`exclude_escalation=True` in `case_engine/knowledge/rag_adapter.py:83`.** NEVER revert.
- **PII discipline**: `NLPSignal.raw_text` is NEVER logged. Log email domains only. Never log ticket body.
- **`asyncio.to_thread()` required for all blocking I/O** (Unity, Loki, Freshdesk, Supabase).
- **`DEBUG_RAG=false` in production.** `true` exposes chunk content (PII) in HTTP responses.
- **`FRESHDESK_WEBHOOK_ENFORCE_HMAC=true` in production.**

---

## 6. Sprint History Summary

| Sprint | Feature |
|--------|---------|
| 2.28.x | Freshdesk client, webhook verifier, idempotency, conversation state |
| 2.46 | Investigation Orchestrator (sole production entry point) |
| 2.47 | Business Pipeline contract (5 stages, StageResult) |
| 2.48 | Freshdesk integration: ClosureFieldGuard, ReplySafetyGate (built) |
| 2.49 | Freshdesk runtime traces TRACE_FD_01..10 |
| 2.50 | Uptime Kuma metrics platform (MetricTool + ServerTool) |
| 2.51 | Unity Admin Portal (5 production tools replacing mocks) |
| 2.52 | Platform wiring: startup validation, auto-registration, STARTUP_READY |
| 2.53 | Enterprise Intelligence Layer (LLM, 5 prompt templates, 5 parsers) |
| 2.54 | Pipeline wiring + slot extraction fix |
| 2.5.5 | Runtime alignment: asyncio.to_thread, frozen guard, 9 permanent rules |
| 2.5.6 | NLP Semantic Router (Layer 2.5): regex removed, NLPRouter+NLPSignal |
| 2.60 | Multi-Tenant Log Platform (Grafana Loki) + PII redaction + BM25 |
| 2.61 | L1 Production Readiness certification |
| 2.63 | Asana L2 escalation + resolution loop (full §20A + §20B) |
| 2.63.1 | ReplySafetyGate wired to all 4 autonomous reply sites |
| 2.63.2 | Asana ticket content rewrite (real evidence, HTML, custom fields) |
| 2.64 | Go-Live master E2E validation (22/22 tests), production hardening |
| 2.64+ | NLU audio/mic fix, error logging improvements, DEBUG_RAG=false |

---

## 7. Test Coverage

- **Total tests**: 1267/1267 pass (0 errors, 2 pre-existing failures in Sprint 2.30.1)
- **Pre-existing failures** (do NOT count as regressions):
  - `test_sprint2281_ticket_created_handler.py::TestOrchestratorIntegration::test_orchestrator_called_with_correct_args`
  - `test_sprint2281_ticket_created_handler.py::TestParseError::test_completely_wrong_structure`
- **Master E2E suite**: `tests/test_sprint264_master_e2e_validation.py` (22 tests, Sprint 2.64)
