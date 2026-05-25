# Risks and Architectural Warnings — Big Phase 2

## Preface

This document is deliberately adversarial. Its job is to name what can go wrong, what is premature, and what should not be built. It is a counterweight to the excitement of designing new capabilities.

A platform that fails in production because it was too ambitious is worse than a platform that is less capable but reliable.

---

## Category 1: What Should NOT Be Built (Phase 2 Scope)

### 1.1 General-Purpose Autonomous Agents

**What is tempting**: A single "smart agent" that reads a ticket, decides what to do, calls tools, generates a response, and handles follow-ups — all without explicit governance.

**Why not to build it**:
- There is no governance model for emergent agent behavior
- When it fails, it fails in novel ways that no test suite caught
- The debugging surface is infinite: any combination of tool calls, in any order, can produce unexpected state
- Agents accumulate context errors across turns — a wrong inference in step 2 corrupts steps 3–10
- The KwikID support context is narrow enough that explicit rules (workflow classification, RESPONSE MODE injection, automation safety gate) outperform general agents with dramatically less risk

**The test**: Can you predict exactly what the system will do given a specific input? With Phase 1 governance, yes. With a general agent, no.

**Verdict**: Do not build. Use explicit orchestration (n8n + defined tool calls) instead.

---

### 1.2 LLM-Directed Tool Chains

**What is tempting**: Give the LLM a list of tools and let it "decide" which to call, in what order, with what parameters — similar to OpenAI function calling with multi-step chains.

**Why not to build it**:
- The LLM can be manipulated into calling unintended tools (prompt injection via ticket content)
- Tool call sequences are not predictable or testable
- A single LLM tool-calling decision failure can cascade (wrong tool call → wrong result → wrong next tool call)
- Audit trail becomes ambiguous: "The LLM decided to update the ticket priority" is not a governance-defensible statement

**What to do instead**: Use rule-based `ActionPlanner` (designed in PHASE_2B_ACTION_SYSTEMS.md). The LLM generates a *response*; rules determine *actions*. These are two separate systems.

**Verdict**: Do not build LLM-directed tool chains. All tool selection is rule-based.

---

### 1.3 Training on Production Data Without Human Review

**What is tempting**: Automatically retrain embeddings or prompts using feedback signals. Positive-feedback responses become training data; negative-feedback responses are filtered out.

**Why not to build it**:
- Feedback signals are noisy. Agents make mistakes. A "thumbs up" on a wrong response teaches the wrong thing.
- Self-reinforcing loops: if the model is slightly biased toward certain SOP sections, feedback rewards those sections, making the bias worse
- Catastrophic forgetting: fine-tuning a general model on a narrow domain erases general reasoning capabilities
- Privacy risk: production feedback data may contain PII that should not enter training pipelines

**What to do instead**: Human-reviewed annotation pipeline (DATA_AND_FEEDBACK_PIPELINE.md). Every training example has a `reviewed_by` field. Automation assists annotation (flagging candidates); humans approve.

**Verdict**: Do not automate training from production feedback. All training data requires human review.

---

### 1.4 RAG Answer Caching

**What is tempting**: Cache the LLM's generated answer for repeated queries. "OTP not received" gets the same answer every time — why generate it again?

**Why not to build it**:
- Ticket content is never exactly the same (customer name, device, timestamp, prior context all differ)
- Governance decisions depend on live retrieval (if a SOP is updated, the cached answer may no longer match)
- A cached wrong answer would be served repeatedly with no feedback signal per-serving
- The `requires_human` flag may change based on memory context — caching would miss this

**What to do instead**: Cache *embeddings* (query → vector) and *retrieval context* (embedding → top chunks). Never cache generated answers. This provides most of the latency benefit without the correctness risk.

**Verdict**: Do not cache LLM-generated answers.

---

### 1.5 Cross-Tenant Memory or Learning

**What is tempting**: "If unity_bank and client_b both have OTP failures, unity_bank's resolution might help client_b."

**Why not to build it**:
- Violates tenant isolation — the fundamental multi-tenant security model
- Clients have data privacy expectations; their ticket data does not belong to other clients
- Even "anonymized" cross-tenant learning can reveal client-specific patterns
- Regulatory risk: cross-tenant data processing may violate DPDP (India) or GDPR requirements

**What to do instead**: Better SOP coverage (benefit all tenants through shared SOPs); per-tenant memory (each client's memory improves their own service only).

**Verdict**: Absolute architectural prohibition. Cross-tenant memory must never be built.

---

## Category 2: What Is Premature (Don't Build Yet)

### 2.1 Knowledge Graph

A graph of entities (issues → resolutions → SOPs → products) would enable multi-hop retrieval. It's architecturally appealing.

**Why it's premature**:
- Requires a reliable entity extraction pipeline (which requires ML or LLM calls at ingest time)
- Requires graph database or PostgreSQL graph extensions
- Adds significant ingest latency and complexity
- The value over simple hybrid retrieval has not been demonstrated on this dataset
- The operational tooling to maintain a knowledge graph (adding nodes, pruning stale edges) does not exist

**When to revisit**: After Phase 2A-2C demonstrate that retrieval quality is the bottleneck (i.e., automation rate is high but specific query types still fail despite SOP coverage).

---

### 2.2 Fine-Tuned Language Models

Custom LLMs trained on KwikID support data would eliminate many prompt engineering challenges.

**Why it's premature**:
- Requires 1000+ high-quality annotated examples (the annotation pipeline is being built in Phase 2)
- Requires GPU infrastructure for training (not currently in scope)
- Requires model serving infrastructure (separate from the current OpenAI-dependent stack)
- The prompt engineering and governance system in Phase 1 achieves competitive quality without fine-tuning
- Fine-tuned models require re-training whenever SOPs change (ongoing cost)

**When to revisit**: When the annotated dataset exceeds 1000 examples and automation rate plateaus despite RAG improvements.

---

### 2.3 Multi-Agent Orchestration Frameworks (LangGraph, CrewAI, AutoGen)

These frameworks provide infrastructure for multi-agent systems.

**Why they're premature**:
- They are heavy dependencies with rapidly evolving APIs (breaking changes between minor versions)
- They abstract away the control flow that makes Phase 1 observable and debuggable
- None of them provide production-grade governance, audit logging, or rollback out of the box
- They are designed for research and exploration, not for enterprise production reliability
- The KwikID support problem does not require multi-agent coordination; it requires good single-pass RAG + explicit action rules

**When to revisit**: If a specific, well-defined multi-agent pattern (not "AI agents everywhere") is demonstrated to improve a measurable outcome.

---

### 2.4 Real-Time Streaming for n8n Workflow

SSE streaming for `/rag/chat` improves perceived latency in browser contexts.

**Why it's premature**:
- n8n (the primary consumer) does not support SSE natively
- The incremental complexity (SSE protocol, partial rendering, reconnection logic) is significant
- The latency improvement from streaming is perceptual (user sees tokens earlier) not actual (total time is the same)
- The operational benefit does not outweigh the implementation cost until there is a browser/mobile client

**When to revisit**: When a real-time web or mobile interface is being built for end customers.

---

### 2.5 Kafka / Event Streaming Platform

A full event streaming platform (Kafka, Pulsar) provides durable, partitioned, replayable event streams.

**Why it's premature**:
- At <50 RPM, Redis queues are sufficient for all async work
- Kafka adds: broker cluster management, topic configuration, consumer group management, offset management, schema registry
- This operational overhead requires dedicated DevOps capacity
- The failure mode of Kafka (consumer lag, partition rebalancing, message ordering issues) is more complex than Redis queue failure modes

**When to revisit**: When sustained webhook volume exceeds what a single Redis queue can handle (>10,000 events/hour).

---

## Category 3: Identified Risks

### 3.1 Memory Store Growth

**Risk**: The `memory_episodes` table grows at ~1 record per request. At 500 requests/day, that's 180,000 records/year per client. At 10 clients, 1.8 million records.

**Impact**: Query latency increases; Supabase storage costs increase; pruning job complexity increases.

**Mitigation**: TTL pruning (designed in MEMORY_ARCHITECTURE.md §4.4). Measure storage growth monthly. Set alerts at 50% of Supabase storage quota.

**Additional risk**: If the pruning job fails silently, the table grows unchecked.

**Mitigation**: The pruning job logs `memory_pruned_episodes_total` to Prometheus. Alert if this metric is 0 for >25 hours.

---

### 3.2 Summarization LLM Failure Cascade

**Risk**: The memory summarization pipeline calls the LLM (gpt-4o-mini). If the LLM API is degraded during summarization batch processing, all summaries for that period are lost or stale.

**Impact**: Returning customers receive no memory context — fallback to stateless behavior.

**Mitigation**: Preserve existing summaries on LLM failure (never delete a summary because a new one failed to generate). Retry summarization on next episode write. Log `summarization_failed_total` as alert metric.

---

### 3.3 Intent Classification Drift

**Risk**: The Tier 1 regex classifier is hand-crafted for current SOP vocabulary. If KwikID adds new products or the terminology evolves, new issue types will route to `AMBIGUOUS` or `GENERAL_KNOWLEDGE`.

**Impact**: New issue categories fall through to default retrieval profiles; potential retrieval quality degradation.

**Mitigation**: Monitor `AMBIGUOUS` rate in analytics. Alert if `AMBIGUOUS` rate exceeds 20% of requests. Add new patterns when new intent categories emerge. Tier 3 LLM classification provides a safety net for genuinely new categories.

---

### 3.4 Feedback Gaming

**Risk**: Support agents click "thumbs up" on responses they didn't read, or "thumbs down" out of frustration with the system rather than because the response was incorrect.

**Impact**: Feedback signals become noisy; retrieval weight adjustments are incorrect; calibration metrics are misleading.

**Mitigation**: 
- Require a 5-second delay between response display and feedback button activation (reduces reflexive clicking)
- Human review before any feedback influences retrieval weights (the review step catches systematic noise)
- Calibration report includes `feedback_coverage` (% of requests rated) — very high coverage may indicate gaming; very low coverage indicates under-engagement

---

### 3.5 Tool Execution Under Stale Governance

**Risk**: The `ActionPlanner` evaluates whether to execute a tool based on governance output (e.g., `automation_safe=True`). If the governance was computed with old/stale SOP versions (before a re-ingestion), the governance decision may be overconfident.

**Impact**: A tool action is executed based on a SOP that has been superseded.

**Mitigation**: Log `index_version` used for each request in `request_analytics`. If a tool execution's `index_version` does not match the current `ACTIVE_INDEX_VERSION`, flag for review. Re-ingestion clears the context cache (embedding cache), so stale context is less likely after re-ingestion.

---

### 3.6 Approval Queue Backlogs

**Risk**: If many tickets simultaneously require human approval (e.g., during a service outage causing a surge of escalations), the approval queue grows faster than agents can review.

**Impact**: Tickets sit in the queue until expiry (4 hours). Customers do not receive responses.

**Mitigation**: 
- SLA alert fires for tickets approaching the 80% mark of the SLA window
- Approval requests have configurable `expires_at` — expired requests are auto-rejected and the ticket is flagged for manual handling
- Telegram alert when approval queue depth exceeds configurable threshold (e.g., >20 pending)

---

### 3.7 Governance Erosion Through Policy Exceptions

**Risk**: Over time, per-tenant policy configurations and exception rules accumulate. What starts as "Client X gets slightly relaxed thresholds" becomes "Client X has 15 exception rules that effectively bypass governance."

**Impact**: The governance model becomes inconsistent across tenants; audit becomes harder; a breach in one tenant's exception rules may not be caught.

**Mitigation**: 
- Policy changes require admin authentication and are logged in `governance_audit_log`
- A quarterly policy review process (manual) reviews all active policies and removes obsolete exceptions
- The system enforces a maximum policy count per tenant (e.g., 10) — additional policies require removing existing ones

---

### 3.8 Vendor Lock-In (OpenAI)

**Risk**: The entire system is dependent on OpenAI's API — for embeddings (text-embedding-3-small), generation (gpt-4o-mini), and summarization. OpenAI pricing changes, model deprecations, or service outages directly affect the platform.

**Impact**: Cost increases, service disruption, or forced migration on short notice.

**Mitigation** (Phase 1 — already present): Ollama embedding provider support provides a self-hosted fallback.

**Mitigation** (Phase 2D): Provider abstraction layer + Azure OpenAI fallback.

**Critical note**: text-embedding-3-small is not interchangeable with other embedding models. Switching requires re-ingesting all documents. The migration cost is not zero.

---

### 3.9 PII Accumulation in Memory Tables

**Risk**: `memory_episodes` may contain issue descriptions that include PII (customer email in complaint, phone number mentioned in OTP context). Over the 90-day TTL window, this represents significant PII accumulation.

**Impact**: Data breach risk; regulatory compliance violation (DPDP/GDPR); reputational harm.

**Mitigation**:
- PII redaction at episode write time (same patterns as B3 ingestion: email, phone, government IDs)
- `customer_profiles.contact_email` is stored only for customers who explicitly consented (via Freshdesk contact data)
- Memory tables are covered by Supabase RLS with restricted access
- TTL pruning ensures PII is not retained beyond 90 days

---

## Category 4: Maintainability Risks

### 4.1 Feature Flag Sprawl

Phase 2 introduces: `MEMORY_ENABLED`, `INTENT_CLASSIFICATION_ENABLED`, `TOOLS_ENABLED`, `ANALYTICS_ENABLED`, `MULTI_AGENT_ENABLED` (if ever), `REDIS_RATE_LIMIT_ENABLED`, `B1_HYBRID_RETRIEVAL_ENABLED`...

By Phase 2C, there may be 15+ feature flags. Managing 15 flags across development, staging, and production environments is error-prone. A flag set correctly in staging but not production produces subtle bugs.

**Mitigation**: 
- All flags default to `false` for new Phase 2 features (opt-in, not opt-out)
- A startup validation function checks for flag inconsistencies: e.g., `TOOLS_ENABLED=true` requires `MEMORY_ENABLED=true`
- Document all flags in `ENVIRONMENT_VARIABLES.md` with their dependencies
- Limit total flags to the minimum necessary; merge related flags where possible

---

### 4.2 Test Suite Complexity

Phase 1 has 5 offline validation suites (174 total tests). Phase 2 adds: intent classification tests, memory system tests, tool execution tests, action planner tests, analytics query tests, governance policy tests.

By Phase 2C, the test suite may require 15+ minutes to run locally. Slow tests get skipped.

**Mitigation**:
- Separate fast offline tests (<5s per suite) from slow integration tests
- CI runs fast tests on every push; slow tests run nightly
- Memory tests use in-memory SQLite fixtures, not Supabase
- Tool execution tests use mock implementations of external APIs

---

### 4.3 n8n Workflow Maintenance Lag

The n8n workflow is a separate artifact from the Python service. When the Python API changes, the n8n workflow must be updated separately — and has been demonstrated to lag (Phase 1 finding: workflow was calling `/query` instead of `/rag/chat`).

**Mitigation**:
- Any API change that affects n8n workflow nodes is flagged as requiring workflow update (in PR description template)
- The n8n workflow is version-controlled and its changes are reviewed in the same PR as API changes
- The `Build Supabase Context` node uses backward-compatible parsing (handles both old and new formats)
- Contract tests: a CI step validates that the n8n workflow's expected request format matches the actual API schema

---

## Summary: The 5 Most Critical Warnings

1. **Do not build autonomous agents.** The governance model breaks under autonomous execution. Use explicit rule-based action planning.

2. **The 6.8s retrieval latency is a critical bug, not a baseline.** Do not design Phase 2 architecture around 10s latency. Diagnose and fix it in Sprint 0.

3. **Memory is the highest-complexity addition.** It introduces statefulness, PII risk, and failure modes that don't exist in Phase 1. Build it last among M1-M4, not first.

4. **Tool execution with CRITICAL risk requires mandatory human approval, no exceptions.** The post_customer_reply tool must never auto-execute. This invariant must be enforced in code and tested, not just documented.

5. **Phase 2D may never be needed.** Do not schedule it. Build it only when the throughput trigger is demonstrably hit in production. Premature infrastructure is the most expensive form of overengineering.
