# Big Phase 2 — Implementation Roadmap

## Governing Principles

This roadmap follows a single governing principle: **prove each layer before building the next.**

The failure mode of AI platform projects is building impressive capabilities that no one trusts because the foundation is unstable. Trust comes from demonstrated, measured reliability — not from feature count.

The implementation order is:
1. **Fix what's broken** (Sprint 0 — pre-Phase 2 stabilization)
2. **Make it observable** (Phase 2A start)
3. **Make it remember** (Phase 2A core)
4. **Make it smarter at routing** (Phase 2A)
5. **Measure and calibrate** (Phase 2A + 2C)
6. **Add cautious action** (Phase 2B)
7. **Build the operator interface** (Phase 2C)
8. **Scale if needed** (Phase 2D — only when traffic demands it)

---

## Sprint 0 — Pre-Phase 2 Stabilization
### Duration: 1 week | Complexity: Low | Risk: High if skipped

These are not Phase 2 features. They are Phase 1 completion items that must exist before any Phase 2 work begins.

| Task | Owner | Effort | Rollback |
|------|-------|--------|---------|
| Rotate Supabase service-role key | DevOps | 30 min | Rotate again |
| Wire GitHub Actions secrets, remove `\|\| true` from pytest | DevOps | 1 hour | Revert CI file |
| Set `FRESHDESK_WEBHOOK_ENFORCE_HMAC=true` in production | DevOps | 15 min | Set back to false |
| Diagnose pgvector 6.8s latency (see PERFORMANCE doc §2.1) | Backend | 1–3 days | N/A (diagnostic only) |
| Fix connection pooling / HNSW config based on diagnosis | Backend | 1–2 days | Revert config change |
| Enable Redis rate limiting in production | DevOps | 2 hours | Set `REDIS_RATE_LIMIT_ENABLED=false` |
| Wire GitHub Actions secrets, enable blocking pytest | DevOps | 1 hour | Revert |

**Gate**: Sprint 0 is complete when:
- pgvector retrieval P50 < 500ms (measured in logs)
- CI is fully blocking (pytest failures fail the build)
- HMAC enforcement is active
- Supabase key is rotated

---

## Milestone 1 — Observability Foundation
### Duration: 2 weeks | Complexity: Medium | Risk: Low

Phase 2 cannot be operated without observability. Build this first — it makes everything else measurable.

### M1.1 — Structured Request Analytics

**Deliverable**: `request_analytics` table populated per-request  
**Files to create**:
- `app/analytics/request_recorder.py` — background task that writes request_analytics
- `database/migrations/P2_001_analytics_tables.sql` — creates request_analytics, retrieval_quality_log

**Integration**: Single `background_tasks.add_task(request_recorder.record, ...)` line in `/rag/chat` handler

**Test**: After 10 requests, verify `request_analytics` table has 10 rows with correct fields

**Rollback**: Remove the background task line. The table is still there but empty. No functional impact.

### M1.2 — Intent Classification (Tier 1 only)

**Deliverable**: Keyword/regex classifier that fixes `GENERAL_KNOWLEDGE / 0.0` routing  
**Files to create**:
- `app/intelligence/intent_classifier.py` — Tier 1 only initially
- `data/intent_patterns.json` — regex patterns for 12 intent categories

**Integration**: Called in `/rag/chat` before retrieval; result stored in `request_analytics.intent_category`

**Test**: Run `validate_intent_classifier.py` against 50 labeled queries. Target: >70% accuracy on Tier 1 alone.

**Rollback**: Set `INTENT_CLASSIFICATION_ENABLED=false`. System falls back to Phase 1 `QueryRouter`.

### M1.3 — Analytics API (Read-only)

**Deliverable**: `/analytics/summary` and `/analytics/intents` endpoints  
**Files to create**:
- `app/routers/analytics.py`

**Test**: Verify endpoints return correct data after M1.1 populates the table

**Rollback**: Remove router registration. No data loss.

### M1 Validation Gate

- `request_analytics` table is being populated per-request
- Intent category is logged for >95% of requests
- `/analytics/summary` returns sensible data
- No latency increase (M1 adds <20ms)

---

## Milestone 2 — Retrieval Quality Analytics + Calibration
### Duration: 2 weeks | Complexity: Medium | Risk: Low

### M2.1 — Retrieval Quality Log

**Deliverable**: `retrieval_quality_log` table with per-request retrieval metrics  
**Files to create**:
- `app/analytics/retrieval_quality_recorder.py`
- `database/migrations/P2_002_retrieval_quality.sql`

### M2.2 — Feedback API

**Deliverable**: `POST /feedback` endpoint  
**Files to create**:
- `app/routers/feedback.py`
- `database/migrations/P2_003_feedback_tables.sql` — `response_feedback` table

**Test**: Post thumbs-up and thumbs-down via API, verify records created

### M2.3 — Calibration Report

**Deliverable**: Weekly calibration report generated to `data/reports/calibration_YYYY-MM-DD.json`  
**Files to create**:
- `scripts/generate_calibration_report.py` — manual trigger initially; scheduled in Phase 2C

### M2 Validation Gate

- Feedback API accepts all signal types without error
- After 50+ feedback events, calibration report shows sensible thumbs-up rates by confidence level
- Retrieval quality log has no missing fields

---

## Milestone 3 — Memory System Core
### Duration: 3 weeks | Complexity: High | Risk: Medium

This is the most complex milestone. Memory adds statefulness; statefulness adds failure modes.

### M3.1 — Database Schema

**Files to create**:
- `database/migrations/P2_004_memory_tables.sql` — all 4 memory tables

**Test**: Schema validates against `validate_b1_db_integrity.py` equivalent for Phase 2 tables

### M3.2 — Episodic Memory Write

**Deliverable**: `MemoryWriter` that creates episode records post-response  
**Files to create**:
- `app/memory/writer.py`
- `app/memory/models.py` — Pydantic models for all memory types

**Integration**: `background_tasks.add_task(memory_writer.write, ...)` — non-blocking

**Test**: After 5 requests, verify 5 episode records in `memory_episodes`

**Rollback**: Set `MEMORY_ENABLED=false` → writer is no-op

### M3.3 — Session Memory Read

**Deliverable**: `MemoryRouter` that loads session context pre-request  
**Files to create**:
- `app/memory/router.py`
- `app/memory/session_manager.py`

**Latency requirement**: Must complete in <150ms for cached reads, <500ms for DB reads

**Test**: Verify memory is loaded for second request in same session

### M3.4 — Customer Profile Management

**Deliverable**: Customer profile created/updated on each request  
**Files**: Extension of `app/memory/writer.py`

### M3.5 — Memory Injection Into Context

**Deliverable**: Memory summary injected into generation prompt as structured block  
**Files**: Extension of `rag_engine/generation/context_assembler.py`

**Test**: Verify that second request in same session references prior session context

### M3.6 — Summarization Worker

**Deliverable**: LLM-based summarization triggered every 5 episodes  
**Files to create**:
- `app/memory/summarizer.py`

**Risk**: LLM summarization can fail. Must have fallback (preserve existing summary, log error).

### M3 Validation Gate

- Memory write adds <10ms to background task queue
- Memory read adds <150ms for cache hits
- Summarization runs successfully and produces valid summary_text
- A returning customer's second session receives memory context
- With `MEMORY_ENABLED=false`, behavior is identical to Phase 1

---

## Milestone 4 — Intent Classification Full Stack
### Duration: 2 weeks | Complexity: Medium | Risk: Low

### M4.1 — Semantic Intent Classifier (Tier 2)

**Deliverable**: Pre-computed prototype embeddings + cosine similarity classifier  
**Files to create**:
- `data/intent_prototypes.json` — 5 canonical examples per category (pre-embedded)
- `app/intelligence/semantic_classifier.py`
- `scripts/generate_intent_prototypes.py` — one-time script to pre-compute embeddings

**Test**: Tier 2 adds >10% accuracy improvement on test set vs Tier 1 alone

### M4.2 — Retrieval Profiles

**Deliverable**: Intent-specific retrieval parameters applied in retriever  
**Files to create**:
- `app/intelligence/retrieval_profiles.py`

**Integration**: Pass `profile` argument to `HybridRetriever.retrieve()`

### M4.3 — Workflow Planner

**Deliverable**: `WorkflowPlanner` synthesizing intent + memory into ResponsePlan  
**Files to create**:
- `app/intelligence/workflow_planner.py`

### M4.4 — Validate Routing Fix

**Test**: The `GENERAL_KNOWLEDGE / 0.0` failure for OTP queries must now return `otp_delivery_failure / >0.70`

**Rollback**: `INTENT_CLASSIFICATION_ENABLED=false`

---

## Milestone 5 — Performance Optimization
### Duration: 2–3 weeks | Complexity: Medium-High | Risk: Medium

This milestone runs in parallel with M3-M4 where possible.

### M5.1 — Query Embedding Cache

**Deliverable**: Redis-backed embedding cache  
**Files to create**:
- `rag_engine/embedding/cached_embedder.py`

**Test**: Second identical query has `embedding_cache_hit=true` in analytics

### M5.2 — Parallel Retrieval

**Deliverable**: Semantic + FTS retrieval run concurrently with `asyncio.gather`  
**Files to modify**: `retrieval/hybrid_retriever.py`

**Test**: `semantic_retrieval_latency_ms + fts_retrieval_latency_ms < previous_total_latency_ms`

### M5.3 — Async Client Migration

**Deliverable**: Supabase client calls wrapped with `asyncio.to_thread` or switched to async client  
**Files to modify**: `rag_engine/supabase_client.py`, all retrieval code

**Risk**: Subtle async behavior changes. Requires careful testing.

### M5.4 — HNSW Configuration Validation

**Deliverable**: Documented HNSW configuration + latency measurement after Sprint 0 fix  
**Files to modify**: Database migration (if needed)

### M5 Validation Gate

- P50 total latency < 5s (measured from `request_analytics.total_latency_ms`)
- Embedding cache hit rate > 15% after 1 day of traffic
- No correctness regressions on governance validation suite (48/48 still pass)

---

## Milestone 6 — Action Systems (Phase 2B)
### Duration: 4 weeks | Complexity: High | Risk: High

**Pre-condition**: M3 (memory) and M4 (intent) must be validated in production first.

### M6.1 — Tool Registry + Low-Risk Tools

**Deliverable**: `ToolRegistry`, `ActionExecutor`, first tools: `get_ticket_status`, `get_service_status`, `add_ticket_tag`  
**Files to create**: All files in `app/actions/`  
**Database**: `P2_005_tool_tables.sql`

**Test**: Execute `get_ticket_status` in test environment; verify result + audit log

### M6.2 — Approval Gate + Queue

**Deliverable**: `ApprovalGate`, `approval_requests` table, Telegram approval notifications  
**Test**: MEDIUM risk action creates approval request; agent approves via Telegram

### M6.3 — Medium-Risk Tools

**Deliverable**: `update_ticket_priority`, `create_asana_task`, `post_internal_note`

**Test**: With `automation_safe=True`, MEDIUM tools auto-execute. With `automation_safe=False`, they queue.

### M6.4 — CRITICAL Risk Tools (post_customer_reply)

**Deliverable**: `post_customer_reply` tool with mandatory approval  
**Risk**: Highest risk milestone item. Test extensively in staging before production.

**Test requirement**: 20 successful approval-flow tests before production deployment

### M6.5 — Action Planner Integration

**Deliverable**: `ActionPlanner` integrated into main request handler  
**Feature flag**: `TOOLS_ENABLED=false` by default; enable per-tenant

### M6 Validation Gate

- All tool executions appear in `tool_executions` audit log
- CRITICAL tools never auto-execute (verified with unit tests)
- Idempotency test: send same request twice, verify single execution
- Rate limits enforced per-tool-per-tenant

---

## Milestone 7 — Operational Platform (Phase 2C)
### Duration: 3 weeks | Complexity: Medium | Risk: Low

### M7.1 — Full Analytics API

**Deliverable**: All `/analytics/` endpoints  
**Test**: Connect Metabase to Supabase, verify dashboards render correctly

### M7.2 — SLA Monitoring + Alerts

**Deliverable**: Background SLA checker, Telegram alerts

### M7.3 — Governance Audit Log

**Deliverable**: `governance_audit_log` populated per-request

### M7.4 — Admin Panel API

**Deliverable**: `/admin/` endpoints for tenant config, threshold management, SOP suggestions

### M7 Validation Gate

- Metabase dashboard shows correct automation rate and intent breakdown
- SLA alert fires correctly in test scenario
- Admin can update thresholds via API and they take effect on next request

---

## Milestone 8 — Phase 2D (Conditional)
### Duration: 8–12 weeks | Complexity: Very High | Risk: High

**Pre-condition**: Throughput is demonstrably hitting OpenAI API rate limits OR latency SLOs are not met despite M5 optimizations.

If the traffic targets are not reached, **do not build Phase 2D**.

### M8.1 — Provider Abstraction + Azure Fallback
### M8.2 — Query Embedding Service (self-hosted, if justified)
### M8.3 — Redis Queue for Webhook Events
### M8.4 — Streaming Response API (SSE)
### M8.5 — Horizontal Scaling (Kubernetes / multi-pod)

---

## Risk Assessment by Milestone

| Milestone | Technical Risk | Operational Risk | Recommended Buffer |
|-----------|---------------|-----------------|-------------------|
| Sprint 0 | Low | Low | 0% |
| M1 Observability | Low | Low | 10% |
| M2 Feedback API | Low | Low | 10% |
| M3 Memory | High | Medium | 30% |
| M4 Intent Full | Medium | Low | 20% |
| M5 Performance | Medium | Medium | 25% |
| M6 Actions | High | High | 40% |
| M7 Platform | Medium | Low | 20% |
| M8 Enterprise | Very High | High | 50% |

---

## Rollback Requirements by Milestone

Every milestone must be deployable as a feature-flag toggle:

```
INTENT_CLASSIFICATION_ENABLED=false   → M4 reverts to Phase 1 QueryRouter
MEMORY_ENABLED=false                  → M3 reverts to stateless Phase 1
TOOLS_ENABLED=false                   → M6 reverts to answer-only responses
ANALYTICS_ENABLED=false               → M1-M2-M7 disable analytics writes
```

These flags must be tested in CI as part of each milestone's test suite.

---

## Dependency Order (Critical Path)

```
Sprint 0 → M1 → M2 → M3 → M4 → M6 → M7
                  ↓
                  M5 (parallel to M3-M4)
                              
M7 → (gate: traffic > 40 RPM sustained?) → M8
```

Items not on the critical path:
- LLM Tier 3 intent classification (M4 extension — can be added after M4 ships)
- Auto-SOP generation (M6 extension — Phase 2B late addition)
- Streaming API (M8 — Phase 2D only)
- Multi-model fallback (M8 — Phase 2D only)

---

## Total Estimated Timeline

| Phase | Duration | Cumulative |
|-------|----------|------------|
| Sprint 0 | 1 week | 1 week |
| M1 + M2 (Observability + Feedback) | 4 weeks | 5 weeks |
| M3 (Memory) | 3 weeks | 8 weeks |
| M4 (Intent) | 2 weeks | 10 weeks |
| M5 (Performance, parallel) | 3 weeks | 10 weeks |
| M6 (Actions) | 4 weeks | 14 weeks |
| M7 (Platform) | 3 weeks | 17 weeks |
| M8 (Enterprise, conditional) | 10 weeks | 27 weeks |

**Phase 2A-2C realistically ships in 17 weeks (4 months) with a team of 2–3 engineers.**

**Phase 2D is conditional and should not be scheduled until the traffic trigger is hit.**
