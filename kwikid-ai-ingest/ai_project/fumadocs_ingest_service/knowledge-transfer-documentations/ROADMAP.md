# KwikID AI Ingest Service — Roadmap

**Reference:** `SUPPORT_OPERATIONS_BLUEPRINT.md` v1.7
**Date:** 2026-07-31

---

## 1. What Is 100% Complete (Production Certified)

### Core Infrastructure
- [x] FastAPI application with CORS, rate limiting, structured logging
- [x] Supabase persistence layer (cases, audit events, actions, chat history)
- [x] Role-based API authentication (`security/config.py`, Sprint 2.8)
- [x] Request correlation (X-Request-ID, Sprint 2.11)
- [x] Prometheus metrics endpoint (`/metrics`, Sprint 2.21)
- [x] Startup validation with 9-check validator + STARTUP_READY report (Sprint 2.52)
- [x] Sentry APM integration (`app/main.py`, sentry-sdk[fastapi])

### Knowledge & RAG Layer
- [x] Fumadocs + JSON + Excel ingestion pipeline
- [x] pgvector semantic search (text-embedding-3-small, 1536 dims)
- [x] PostgreSQL full-text search (FTS) via `match_all_b1_sources` RPC
- [x] Hybrid retrieval: Reciprocal Rank Fusion (RRF) + BM25 reranking
- [x] Token-aware chunking (CHUNK_TARGET_TOKENS=1200)
- [x] RAG chat endpoint (`/rag/chat`) with session history
- [x] StackOverflow Teams export ingestion (B3 knowledge articles)
- [x] 5 seed KnowledgeEntry SOPs loaded at startup (`runtime/assembly.py`)
- [x] `exclude_escalation=True` permanent security gate (Sprint 2.47)

### L1 Pipeline — Investigation
- [x] NLP Semantic Router: LLM-based intent + entity extraction (Sprint 2.5.6)
  - Handles negation, code-mixing ("OTP nahi aa raha"), 8 intents
  - Audio/mic/video/network keywords → VKYC_SESSION_FAILURE (Sprint 2.64+)
- [x] ClarificationEngine: asks for URN/Session ID when missing (Sprint 2.26)
- [x] WorkflowEngine: 7-service deterministic step executor (Sprint 2.26)
- [x] Investigation pipeline: Planner → Collector → RootCause → Observation
- [x] Unity Admin Portal (5 READ-ONLY production tools, Sprint 2.51)
- [x] Grafana Loki Log Platform (GetSessionLogsTool, Sprint 2.60)
  - PII redaction runs BEFORE BM25 (PII compliance gate)
  - BM25 relevance extraction before LLM context
- [x] Uptime Kuma Metrics (MetricTool + ServerTool, Sprint 2.50)
- [x] Enterprise Intelligence Layer: provider-agnostic LLM (Sprint 2.53)
  - 5 versioned prompt templates, 5 schema-strict parsers
  - confidence threshold gate (INTELLIGENCE_CONFIDENCE_THRESHOLD=0.75)
- [x] Investigation Orchestrator as sole production entry point (Sprint 2.46)
- [x] Business Pipeline contract: 5 stages, StageResult, guard (Sprint 2.47)

### L1 Pipeline — Response
- [x] Observation Generator (NLG): structured internal note (Sprint 2.18/2.46)
- [x] FreshdeskResponseService: sole approved Freshdesk write path (Sprint 2.28/2.48)
- [x] TRACE_FD_01..10 runtime traces for all Freshdesk operations (Sprint 2.49)
- [x] ClosureFieldGuard: gates every status=4/5 PUT (Sprint 2.48)
- [x] ReplySafetyGate: wired at all 4 autonomous reply call sites (Sprint 2.63.1)
  - confidence gate, force-escalation gate, duplicate hash detection, kill switch

### L2 Pipeline — Engineering Escalation
- [x] EngineeringEscalationService (case_engine/engineering/service.py)
- [x] AsanaClient: create_task() with real HTML evidence (Sprint 2.63.2)
  - Investigation summary, root cause, Loki log snippets in task description
  - Custom fields: Priority, Task Progress
  - 4 board sections: New - Needs Triage / In Progress / Blocked - Needs Info / Done
  - Live Asana webhook registered (gid=1217038113542074)
- [x] L2 resolution loop: Asana webhook → ClosureFieldGuard → ReplySafetyGate → Freshdesk closure (Sprint 2.63)

### Freshdesk Integration
- [x] Webhook receiver: ticket-created, ticket-updated (Sprint 2.28)
- [x] HMAC signature verification (`freshdesk/verifier.py`)
- [x] Idempotency store: prevents duplicate processing (Sprint 2.28)
- [x] Conversation state: tracks multi-turn clarification (Sprint 2.28)
- [x] 10-second synchronous response budget + BackgroundTasks
- [x] Observer webhook rule for customer replies → ticket-updated (Sprint 2.54)

### Multi-Tenant Architecture
- [x] TenantRegistry + ClientResolver (Sprint 2.27.9)
- [x] Tenant tag resolution: `client:unity_bank`, `client:rbl`, `client:cbi`
- [x] TenantAwareToolRegistry (Sprint 2.27.9)
- [x] Unity Bank: fully configured + live
- [x] RBL Bank: tenant tag active
- [x] CBI Bank: tenant tag active

### Audit & Governance
- [x] AuditLogger + AuditService (action-gateway layer)
- [x] Supabase audit_events table (AUDIT_BACKEND=supabase)
- [x] Case-engine AuditLogger (workflow layer)
- [x] SLAWatchdog (case_engine/sla_watchdog.py)
- [x] ActionGateway (reserved for future action execution)

---

## 2. Future Waves (Not Yet Started)

### Wave 9: Additional Bank Onboarding (BOB, Canara)
- Configure `LOKI_BOB_URL`, `LOKI_BOB_USERNAME`, `LOKI_BOB_PASSWORD` in `.env`
- Configure `LOKI_CANARA_URL`, `LOKI_CANARA_USERNAME`, `LOKI_CANARA_PASSWORD`
- Register Unity-equivalent Admin Portal adapters per bank
- Add tenant tags: `client:bob`, `client:canara`
- Add bank-specific playbooks if different SLA/SOP applies
- **Effort estimate:** Medium (infrastructure is multi-tenant; credentials + config only unless BOB/Canara use a different portal API)

### Wave 10: Video Analysis
- `VIDEOTOOL` appears in `flow_diagram.mermaid` as `Session Video Tool` but is NOT registered in production
- Would retrieve VKYC session recordings and pass to a vision model for:
  - Liveness failure analysis
  - Environmental quality assessment (lighting, background)
  - Document capture quality check
- **Prerequisite:** `VISION` node wired to `HYBRIDRAG` in the flow diagram (currently placeholder)
- **Effort estimate:** Large (new integration, LLM vision API, new tool adapter)

### Wave 11: Cross-Session Pattern Analysis
- Aggregate evidence across multiple sessions for the same customer URN
- Detect systematic failures (e.g., all sessions failing liveness for bank X)
- Generate trend-based Asana tickets rather than per-ticket escalations
- **Prerequisite:** Historical session data stored in Supabase (`cases` table)
- **Effort estimate:** Large (new query patterns, aggregation pipeline)

### Wave 12: Action Execution (Deferred)
- The `Execution [Action & Resolution Layer]` subgraph in `flow_diagram.mermaid` is marked "Reserved for Future Use"
- `ACTIONGW` → `RISKCHECK` → `APPROVAL` → `EXECUTE` path is coded but deliberately inactive
- Covers: OTP resend, session reset, identity portal actions
- **Prerequisite:** Human approval workflow, dual-control gate
- **Effort estimate:** Very large (safety-critical, requires formal approval process)

---

## 3. Immediate Admin Actions Pending

See `REMAINING_BLOCKERS.md` for the 4 items that block hands-off production operation:
1. `ai.support@getkwikid.com` Freshdesk agent account not created
2. Asana webhook must be re-registered on public production URL
3. `AUTH_ENABLED` decision required (currently `false`)
4. BOB/Canara Loki credentials not configured
