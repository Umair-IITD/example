# Big Phase 2 — Master Architecture Document

## 1. Architectural Vision

Big Phase 1 built a governed RAG service: a single-pass pipeline that retrieves, classifies, generates, and gates. It is predictable, testable, and operationally safe.

Big Phase 2 transforms the platform into an **Intelligent Support Operating System** — one that remembers, learns, acts, and improves over time — while maintaining every governance, observability, and rollback property that Phase 1 established.

The core architectural principle: **extend, never replace**. Every Phase 2 component sits alongside Phase 1 components with explicit fallback to Phase 1 behavior if the new component is unavailable, misconfigured, or producing low-confidence output.

---

## 2. System Subsystem Map

```
╔══════════════════════════════════════════════════════════════════════════╗
║                  KwikID Intelligent Support Platform                     ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  ┌─────────────────────────────────────────────────────────────────┐    ║
║  │                    INGRESS LAYER                                 │    ║
║  │  Freshdesk Webhook │ Telegram Bot │ Direct API │ n8n Webhook     │    ║
║  └────────────────────────────┬────────────────────────────────────┘    ║
║                               │                                          ║
║  ┌────────────────────────────▼────────────────────────────────────┐    ║
║  │                 INTELLIGENCE LAYER (Phase 2A)                    │    ║
║  │  IntentClassifier │ WorkflowPlanner │ MemoryRouter               │    ║
║  │  ContextEnricher  │ SessionManager  │ RetriProfiles              │    ║
║  └──────┬──────────────────────┬──────────────────┬────────────────┘    ║
║         │                      │                  │                      ║
║  ┌──────▼──────┐   ┌───────────▼────────┐  ┌────▼───────────────────┐  ║
║  │   MEMORY    │   │  RETRIEVAL ENGINE   │  │    GOVERNANCE ENGINE    │  ║
║  │  SUBSYSTEM  │   │  (Phase 1 core +    │  │   (Phase 1 core +       │  ║
║  │  (Phase 2A) │   │   Phase 2A enhanc.) │  │    Phase 2 evolution)   │  ║
║  └──────┬──────┘   └───────────┬────────┘  └────┬───────────────────┘  ║
║         │                      │                 │                       ║
║  ┌──────▼──────────────────────▼─────────────────▼───────────────────┐  ║
║  │                    GENERATION ENGINE                                │  ║
║  │            (Phase 1 core + context enrichment)                     │  ║
║  └─────────────────────────────┬──────────────────────────────────────┘  ║
║                                │                                          ║
║  ┌─────────────────────────────▼──────────────────────────────────────┐  ║
║  │                  ACTION LAYER (Phase 2B)                            │  ║
║  │   ToolRegistry │ ActionExecutor │ ApprovalGate │ AuditLogger        │  ║
║  └─────────────────────────────┬──────────────────────────────────────┘  ║
║                                │                                          ║
║  ┌─────────────────────────────▼──────────────────────────────────────┐  ║
║  │              OPERATIONAL PLATFORM (Phase 2C)                        │  ║
║  │   Analytics │ SLAMonitor │ GovernanceAudit │ AdminPanel             │  ║
║  └────────────────────────────────────────────────────────────────────┘  ║
║                                                                          ║
║  ┌────────────────────────────────────────────────────────────────────┐  ║
║  │              ENTERPRISE LAYER (Phase 2D)                            │  ║
║  │   ModelRouter │ ProviderAbstraction │ EventBus │ CostTracker        │  ║
║  └────────────────────────────────────────────────────────────────────┘  ║
╚══════════════════════════════════════════════════════════════════════════╝
```

---

## 3. Component Interaction Map

### 3.1 Current Phase 1 Runtime (Baseline)

```
Request
  → Auth middleware
  → Rate limiter
  → /rag/chat handler
    → QueryPreprocessor
    → HybridRetriever (pgvector + FTS + RRF)
    → BM25Reranker
    → WorkflowClassifier
    → GovernanceEngine
    → ContextAssembler
    → ChatGenerator (OpenAI)
    → ResponseSerializer
  → Response
```

### 3.2 Target Phase 2 Runtime (Full)

```
Request
  → Auth middleware
  → Rate limiter
  → SessionManager.resolve(session_id, client)          [2A]
  → IntentClassifier.classify(query, session_context)   [2A]
  → MemoryRouter.load(session_id, customer_id, tenant)  [2A]
  → WorkflowPlanner.plan(intent, memory_context)        [2A]
  → /rag/chat handler
    → QueryPreprocessor (Phase 1, enriched with memory)
    → HybridRetriever (Phase 1 + adaptive profiles)     [2A]
    → BM25Reranker / CrossEncoder (Phase 1)
    → WorkflowClassifier (Phase 1)
    → GovernanceEngine (Phase 1 + advanced policies)    [2D]
    → ContextAssembler (Phase 1 + memory injection)     [2A]
    → ActionPlanner.evaluate(intent, governance)        [2B]
    → ApprovalGate.check(actions, risk_score)           [2B]
    → ChatGenerator (Phase 1 + model router)            [2D]
    → MemoryWriter.persist(session_id, result)          [2A]
    → FeedbackCapture.record(request, response)         [data pipeline]
    → ResponseSerializer
  → Response
  → Analytics.emit(request_event)                       [2C]
```

---

## 4. Service Boundaries

### 4.1 Core RAG Service (Phase 1 — remains unchanged)

**Responsibility**: retrieval, governance, generation  
**Boundary**: everything that happens between receiving a query and returning an answer  
**Change policy**: additive only — new components inject through well-defined hooks, not inline modifications

**Phase 1 contracts that must NOT change**:
- `/rag/chat` request/response schema (backward compatible)
- `GovernanceEngine` output fields (`confidence`, `requires_human`, `automation_safe`)
- `WorkflowClassifier` output (`exact_match`, `related_match`, `weak_match`, `no_match`)
- `SopDocumentFlags` interface
- `ACTIVE_INDEX_VERSION` isolation model

### 4.2 Memory Service (Phase 2A — new)

**Responsibility**: persist, retrieve, summarize, and manage conversational + organizational memory  
**Boundary**: owns all memory reads/writes; does not perform retrieval or generation  
**Interface**: async read/write over internal Python API (Phase 2A); HTTP service (Phase 2D if needed)

### 4.3 Intelligence Layer (Phase 2A — new)

**Responsibility**: intent classification, session management, workflow planning  
**Boundary**: stateless per request; consults memory service; produces enriched request context  
**Fallback**: if intelligence layer is unavailable, route to Phase 1 pipeline unchanged

### 4.4 Action Layer (Phase 2B — new)

**Responsibility**: tool execution, action governance, approval management  
**Boundary**: separate execution context from generation; explicit permission check before any action  
**Fallback**: if action layer fails, return answer-only response (no action taken)

### 4.5 Operational Platform (Phase 2C — new)

**Responsibility**: analytics, SLA tracking, governance audit, admin controls  
**Boundary**: read-only view of system state; writes only to operational tables (not knowledge tables)  
**Deployment**: can be deployed as a separate lightweight service or as additional FastAPI routes

### 4.6 Enterprise Layer (Phase 2D — new)

**Responsibility**: model provider abstraction, cost tracking, event routing  
**Boundary**: wraps all LLM calls; transparent to upstream components  
**Deployment**: Phase 2D only; not required for Phase 2A-2C

---

## 5. Data Store Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      Supabase PostgreSQL                      │
├──────────────────────────────────────────────────────────────┤
│  Phase 1 Tables (READ-ONLY from Phase 2 perspective)          │
│  ├── documents (vectors + metadata)                           │
│  ├── rag_sop_chunks                                           │
│  ├── rag_knowledge_articles / rag_knowledge_chunks            │
│  ├── chat_messages (existing session history)                 │
│  └── rag_review_queue                                         │
├──────────────────────────────────────────────────────────────┤
│  Phase 2A Tables (NEW)                                        │
│  ├── memory_episodes (per-session episodic records)           │
│  ├── memory_summaries (LLM-summarized session context)        │
│  ├── customer_profiles (cross-session customer state)         │
│  ├── tenant_context (per-tenant operational state)            │
│  ├── intent_logs (intent classification decisions)            │
│  └── retrieval_quality_log (per-request retrieval metrics)    │
├──────────────────────────────────────────────────────────────┤
│  Phase 2B Tables (NEW)                                        │
│  ├── tool_executions (audit log of all tool calls)            │
│  ├── approval_requests (pending human approvals)              │
│  ├── action_results (outcomes of executed actions)            │
│  └── sop_suggestions (auto-generated SOP drafts)             │
├──────────────────────────────────────────────────────────────┤
│  Phase 2C Tables (NEW)                                        │
│  ├── request_analytics (per-request operational metrics)      │
│  ├── sla_events (SLA threshold crossings)                     │
│  ├── quality_reviews (human-reviewed responses)               │
│  └── governance_audit_log (policy evaluations)                │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│                          Redis                                │
├──────────────────────────────────────────────────────────────┤
│  Phase 1 (existing): rate limiting sliding windows           │
│  Phase 2A (NEW): session state cache (TTL: 30 min)           │
│  Phase 2A (NEW): intent classification result cache          │
│  Phase 2B (NEW): tool execution locks (idempotency)          │
│  Phase 2C (NEW): real-time metrics aggregation               │
└──────────────────────────────────────────────────────────────┘
```

---

## 6. Phase Sequence and Dependency Graph

```
Phase 1 (COMPLETE)
       │
       ├─────────────────────┐
       │                     │
       ▼                     ▼
Phase 2A                  Performance
Intelligence Layer        Optimization
(memory, intent,          (parallel to 2A)
 routing, analytics)
       │
       ├─────────────────────┐
       │                     │
       ▼                     ▼
Phase 2B                  Phase 2C
Action Systems            Operational
(tool calling,            Platform
 SOP generation)          (analytics,
       │                   dashboards)
       │                     │
       └──────────┬──────────┘
                  │
                  ▼
              Phase 2D
              Enterprise
              Platform
              (multi-model,
               event-driven)
```

**Critical dependency rule**: No phase begins until its predecessor is validated in production. Phase 2D is optional — it provides infrastructure maturity but does not deliver user-visible features.

---

## 7. Future Runtime Sequence Diagrams

### 7.1 Normal Ticket Flow (Phase 2A Active)

```
Freshdesk → n8n → POST /rag/chat
                      │
                      ▼
               SessionManager
               .resolve(session_id, client)
               ────────────────────────
               reads: Redis cache → Supabase
               Returns: SessionContext{
                 customer_id, tenant,
                 prior_issues[], memory_summary
               }
                      │
                      ▼
               IntentClassifier
               .classify(query, session_context)
               ────────────────────────
               Returns: Intent{
                 category: "otp_delivery_failure",
                 confidence: 0.91,
                 retrieval_profile: "sop_priority_high"
               }
                      │
                      ▼
               HybridRetriever
               (with retrieval_profile applied)
                      │
                      ▼
               GovernanceEngine (Phase 1)
                      │
                      ▼
               ContextAssembler
               + memory_summary injected
                      │
                      ▼
               ChatGenerator
                      │
                      ▼
               MemoryWriter
               .persist(episode, session_id)
                      │
                      ▼
               Response
```

### 7.2 Tool Execution Flow (Phase 2B Active)

```
(After GovernanceEngine produces answer + action_candidates)
               │
               ▼
        ActionPlanner
        .evaluate(answer, intent, confidence)
        ────────────────────────
        Returns: ActionPlan{
          actions: [
            {type: "update_ticket_priority", args: {...}},
            {type: "create_asana_task", args: {...}}
          ],
          risk_score: 0.3,
          requires_approval: false
        }
               │
               ▼
        ApprovalGate.check(action_plan)
        ─────────────────────────────
        risk_score < 0.5 AND confidence == "high"
        → APPROVED (automated)
        risk_score >= 0.5 OR confidence != "high"
        → PENDING (queue for human review)
               │
        ┌──────┴──────┐
        ▼             ▼
    APPROVED       PENDING
        │              │
        ▼              ▼
  ActionExecutor  ApprovalQueue
  .execute()      .enqueue(action_plan)
        │              │
        ▼              ▼
  AuditLogger     NotifyAgent
  .record()       (Telegram/Freshdesk)
```

---

## 8. Event Flow Architecture

Phase 2 introduces an internal event bus (not a separate message broker — implemented as async background tasks in FastAPI):

```
Request Completed
       │
       ├──→ memory.write (async, non-blocking)
       ├──→ analytics.emit (async, non-blocking)
       ├──→ feedback.capture (async, non-blocking)
       ├──→ retrieval_quality.log (async, non-blocking)
       └──→ sla_monitor.check (async, non-blocking)
```

**Design principle**: All post-response events are fire-and-forget with structured error handling. A failure in memory.write must never affect the response to the user.

Implementation: FastAPI `BackgroundTask` per request, not an external message queue (Phase 2D evaluates whether Kafka/RabbitMQ is warranted).

---

## 9. Scaling Architecture

### 9.1 Phase 2A Scaling Model

```
Load Balancer
    │
    ├── FastAPI Worker 1 (Gunicorn + Uvicorn)
    ├── FastAPI Worker 2
    ├── FastAPI Worker 3
    └── FastAPI Worker 4
         │
         ├── Redis (shared state: sessions, rate limits, locks)
         ├── Supabase (shared data: vectors, memory, analytics)
         └── OpenAI API (external, rate-limited by org tier)
```

**Bottleneck**: OpenAI API rate limits are per-organization, shared across all workers. Phase 2D addresses with multi-provider routing.

### 9.2 Target Throughput (Phase 2A)

| Metric | Phase 1 | Phase 2A Target |
|--------|---------|----------------|
| Requests/minute | ~12 (rate limited) | 60 (with Redis + 4 workers) |
| P50 latency | ~10s | <6s (with caching) |
| P95 latency | ~15s | <10s |
| Memory retrieval add | — | <100ms |
| Intent classification add | — | <200ms |

---

## 10. Backward Compatibility Guarantees

Every Phase 2 component must satisfy:

1. **Additive schema**: New response fields are added, never removed. Existing fields never change type.
2. **Feature flags**: Each Phase 2 subsystem is gated by an environment variable (`MEMORY_ENABLED`, `INTENT_CLASSIFICATION_ENABLED`, `TOOLS_ENABLED`). Setting to `false` produces identical Phase 1 behavior.
3. **Fallback contract**: If a Phase 2 component raises an exception, it is caught, logged, and the request proceeds through Phase 1 pipeline.
4. **No breaking n8n changes**: The `/rag/chat` response envelope gains fields but never loses or renames them.
5. **Index version isolation**: Phase 2 knowledge (auto-generated SOPs, etc.) is ingested under a new version tag, not mixed with Phase 1 data.
