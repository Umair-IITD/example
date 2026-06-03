# 01 — Master Architecture

## 1. Why This Architecture

Phase 1 is a production single-turn RAG system. It classifies a Freshdesk ticket, retrieves relevant SOP chunks via pgvector hybrid search, generates a grounded diagnostic note, and posts it as a Freshdesk private note. It works reliably for known issue families.

Phase 1 cannot: execute multi-step support transactions, track case state across multiple sessions or container restarts, safely call external APIs with audit trails, or route complex issues to human agents with structured diagnostic context. These are not retrieval limitations — they are architectural gaps. Adding more retrieval does not close them.

Phase 2 adds a case-centric operating layer on top of Phase 1 without replacing it. The controlling insight is that support issues are cases, not conversations. A case spans multiple sessions, requires an audit trail for compliance, must survive infrastructure restarts, and must be able to escalate with full context intact. Conversation threads cannot provide any of these guarantees. The Case/Ticket record is the primary unit of work. Chat is an I/O channel only.

---

## 2. System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                          INGRESS LAYER                              │
│  Freshdesk Webhook  │  Email Gateway  │  Direct API                 │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────────┐
│                         INTAKE GATE                                 │
│  Deterministic Topic Classifier                                     │
│  Tier 1: keyword/regex (< 5ms, ~70% coverage)                       │
│  Tier 2: semantic embedding fallback                                │
│  Unknown topic → immediate ESCALATED routing                        │
│  Confidence < 0.85 → immediate ESCALATED routing                    │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────────┐
│                     CASE CONTROL PLANE                              │
│  Case State Machine (NEW → CLASSIFYING → TRIAGE_COMPLETE →          │
│                       WORKFLOW_ACTIVE → ESCALATED → RESOLVED)       │
│  Workflow Engine (declarative playbooks, Level 1; Temporal Level 2) │
│  Case Checkpoint Store (Redis hot, PostgreSQL Saga tables)          │
└──────────────┬──────────────────────────────┬───────────────────────┘
               │                              │
┌──────────────▼──────────┐    ┌──────────────▼──────────────────────┐
│   SUPPORT LAYER         │    │         ACTION GATEWAY              │
│  Phase 1 RAG Engine     │    │  Proposal → Validation → Execution  │
│  (unchanged from Phs 1) │    │  Action Risk Classification         │
│  pgvector hybrid search │    │  Idempotency Key (Redis)            │
│  SOP index retrieval    │    │  RBAC enforcement                   │
│  Topic-scoped retrieval │    │  Circuit Breaker (Redis-backed)     │
└──────────────┬──────────┘    └──────────────┬──────────────────────┘
               │                              │
┌──────────────▼──────────────────────────────▼───────────────────────┐
│                      ESCALATION BRIDGE                              │
│  Transfer Context Payload compiler                                  │
│  Human Queue Router                                                 │
│  Freshdesk private note publisher (tagged for human attention)      │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────────┐
│                        OBSERVABILITY                                │
│  Audit Log (case_audit_log — append-only)                           │
│  Security Compliance Audit (security_compliance_audit — SOC routed) │
│  Analytics / KPI Dashboard                                          │
│  Feedback pipeline (response_feedback table)                        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Component Roles

### Ingress Layer
**Owns:** inbound event normalization. Converts Freshdesk webhook payloads, email events, and direct API calls into a uniform `IncomingCaseEvent` schema.
**Does NOT do:** business logic, classification, or any mutation.

### Intake Gate (Deterministic Topic Classifier)
**Owns:** topic assignment, confidence scoring, immediate routing decision.
**Does NOT do:** retrieve documents, generate text, execute tools, or speculate about unknown topics.
Unknown or below-threshold inputs are immediately routed to `ESCALATED` state. The classifier never falls through to open-ended LLM reasoning.

### Case Control Plane
**Owns:** the Case State Machine and all state transitions; the Workflow Engine that executes topic-specific playbooks; the Case Checkpoint Store that persists active workflow variables.
**Does NOT do:** generate customer-facing content, call external APIs directly, or make retrieval decisions. It delegates to the Support Layer and Action Gateway as bounded subservices.

### Support Layer (Phase 1 RAG)
**Owns:** SOP retrieval, grounded answer generation, match type scoring (exact/related/weak/no_match), confidence scoring.
**Does NOT do:** govern case flow, decide what happens next, call external APIs, or store case state. It is a stateless bounded service called from workflow nodes.

### Action Gateway
**Owns:** the proposal/validation/execution pipeline. Validates every structured action proposal before any mutation. Enforces RBAC, risk classification, idempotency.
**Does NOT do:** generate action proposals (the workflow/LLM layer does that), or execute actions that are classified IRREVERSIBLE without explicit human approval.

### Escalation Bridge
**Owns:** Transfer Context Payload compilation; routing case to the correct human queue; posting the structured payload as a Freshdesk private note with human-attention tags.
**Does NOT do:** continue attempting automated resolution once escalation is triggered.

### Observability
**Owns:** case_audit_log (every state transition, append-only), security_compliance_audit (security anomalies, SOC-routed), feedback collection, KPI metrics.
**Does NOT do:** alter case execution based on metrics in real-time; all calibration is done offline via human review.

---

## 4. Phase 1 vs Phase 2 Boundary

### What stays exactly as-is from Phase 1
- Freshdesk webhook listener (already production-stable)
- pgvector hybrid retrieval engine (BM25 + semantic)
- SOP and knowledge base indexes
- Governance engine (exact_match / related_match / weak_match / no_match scoring)
- Prompt engineering layer (system prompt, diagnostic generation)
- Private note publisher (Freshdesk API integration)
- Phase 1 RAG settings, chunking, and ingestion pipeline

### What Phase 2 adds on top
- Case State Machine: tracks case lifecycle, survives restarts
- Topic Classifier upgrade: now a formal intake gate with confidence routing (Phase 1 had classification but it was not a hard routing gate)
- Workflow Engine: declarative playbooks per topic; durable execution in Level 2
- Case Checkpoint Store: Redis + PostgreSQL Saga tables; replaces ephemeral in-memory state
- Slot Filling Processor: structured clarification loops for missing parameters
- Action Gateway: proposal/validation/execution split; risk classification; idempotency
- Transfer Context Payload: structured diagnostic package on human escalation
- Audit tables: case_audit_log and security_compliance_audit
- Feedback pipeline: response_feedback table for calibration
- ABAC predicate pushdown on retrieval (security fix applied at the Phase 1 RAG layer)

---

## 5. What We Are Not Building Yet

The following are explicitly out of scope for Phase 2 (Level 1 and Level 2):

**Customer-facing autonomous writes.** The system posts only to Freshdesk private notes visible to agents. No AI-generated content is sent directly to customers. This constraint holds until an evaluation framework and human review gate are production-proven.

**Dynamic multi-agent networks.** No peer-to-peer agent negotiation. No dynamic agent discovery. Specialized workers are stateless functions called from a deterministic supervisor.

**Graph RAG as backbone.** The knowledge graph is not the control plane for case execution. It is an optional Level 3 retrieval enhancement, justified only when flat retrieval demonstrably fails on multi-hop SOP dependency chains.

**Open-ended LLM planning.** No LLM is given a list of 50 tools and asked to plan dynamically. LLM reasoning is constrained to: evaluating playbook transition conditions, generating grounded diagnostic text within topic bounds, and compiling the Transfer Context Payload.

**Persistent vector memory for customer history.** No episodic memory stores. No 90-day or 365-day vector histories. Customer profile data is accessed via zero-copy read-only CRM API only, never duplicated locally.

**Write-level API mutations on core KYC records.** Aadhaar field modification, PAN verification bypass, liveliness override — these are classified IRREVERSIBLE and blocked from automation at any level.

---

## 6. Architecture Decision Log

### ADR-001: Case-Centric Primary Object
**Decision:** The primary system object is the Case/Ticket record, not the conversation thread.
**Reasoning:** Support issues in KYC onboarding span multiple sessions, require compliance audit trails, and must survive container restarts. A chat thread collapses on browser close, carries no SLA tracking, and cannot be serialized for human handoff. The Case record lives in PostgreSQL, survives infrastructure failures, and is the authoritative source of truth for all state transitions.

### ADR-002: Deterministic Intake Gate Before Any LLM Reasoning
**Decision:** Every inbound event must be classified by the deterministic topic classifier before any LLM reasoning executes.
**Reasoning:** Without a bounded intake gate, the LLM reasoning layer receives unbounded input scope, dramatically increasing the risk of misclassification, prompt injection, and open-ended tool misuse. The intake gate costs < 5ms for keyword-matched cases and provides a hard routing contract that makes the system auditable.

### ADR-003: Phase 1 RAG as Bounded Service, Not Control Plane
**Decision:** Phase 1 RAG is called from workflow nodes as a stateless service. It does not govern case flow.
**Reasoning:** Retrieval systems are good at finding relevant context; they are not execution engines. Giving retrieval governance over case execution would mean SOP text changes could alter business logic, which is a maintenance and compliance risk. The workflow engine owns execution; retrieval is a constrained input to it.

### ADR-004: Declarative Playbooks for Level 1, Durable Execution (Temporal) for Level 2
**Decision:** Level 1 uses declarative YAML playbooks stored in code. Temporal is a Level 2 addition.
**Reasoning:** Temporal has real infrastructure cost and operational overhead. For Level 1 (1 engineer, 3 months), declarative playbooks achieve the same correctness guarantee for single-session transactions at near-zero infra cost. Temporal is justified only when multi-session sagas and cross-service compensation become required.

### ADR-005: Proposal/Validation/Execution Split for All Actions
**Decision:** AI outputs structured JSON intent; a separate gateway validates; a separate microservice executes. AI never writes directly.
**Reasoning:** This is the primary defense against prompt injection via ticket content, malformed action generation, and unauthorized mutations. The validation gateway is deterministic and auditable; it applies RBAC, schema validation, and idempotency checks that an LLM cannot reliably perform on its own.

### ADR-006: Zero-Copy Customer Profile, No Local Duplication
**Decision:** Customer CRM data is fetched via read-only API at case initiation and not stored locally in any form.
**Reasoning:** Duplicating customer profile data (Aadhaar details, PAN, account status) into local databases or vector stores creates unstructured PII stores that violate DPDP Act and RBI V-CIP guidelines. Zero-copy reads access current account state without creating compliance liability.

### ADR-007: Redis-Backed Circuit Breaker for All External Calls
**Decision:** All downstream API calls are wrapped by a Redis-backed circuit breaker (not in-memory).
**Reasoning:** In-memory circuit breakers are per-worker. A multi-worker deployment (gunicorn, k8s replicas) would require each worker to independently trip its own breaker, allowing N-1 workers to continue hammering a failing downstream service. Redis-backed state is shared across all workers, providing true cluster-level protection.

### ADR-008: ABAC Predicate Pushdown as Level 1 Security Fix
**Decision:** Tenant isolation predicates must be applied at the database query level, not as post-retrieval filters.
**Reasoning:** The current pgvector hybrid search returns high-similarity results before applying tenant filters. High-similarity cross-tenant results can pass a post-retrieval filter before being discarded only if the filter is applied correctly — but if the filter is misconfigured or bypassed, sensitive SOP content from one tenant can appear in another tenant's response. Pushing predicates to the SQL WHERE clause eliminates this risk class entirely.

### ADR-009: Three-Level Maturity Model with Hard Gates
**Decision:** Level 2 work does not begin until Level 1 gates are passed. Level 3 work does not begin until Level 2 gates are passed and throughput ceiling is actually reached.
**Reasoning:** Premature complexity is the primary cause of AI system failures in production. Level 2 adds durable workflow infrastructure, action execution, and customer data access patterns that carry real compliance and reliability risk. Introducing these before Level 1 is proven stable would compound risk without operational justification.

### ADR-010: Human Handoff as a First-Class Outcome
**Decision:** Human escalation is not a failure state. It is a fully engineered outcome with a structured Transfer Context Payload.
**Reasoning:** The system cannot handle every case correctly, especially in early operation. Designing escalation as a first-class outcome means human agents receive complete diagnostic context and never need to re-read the full transcript. This protects SLA compliance during the period when automation coverage is partial, and it maintains customer trust throughout the rollout.
