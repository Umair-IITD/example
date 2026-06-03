# Documentation Changelog — Phase 2 Architecture Revision

**Date:** 2026-06-01  
**Author:** Phase 2 Architecture Review  
**Branch:** sprint0-singleton-stabilization  

---

## Files Deleted

The following 10 files were deleted because they described a Phase 2 architecture that has been superseded. The superseded design had fundamental structural problems: it was chat-centric rather than case-centric, relied on persistent vector memory for customer history (DPDP Act violation), proposed autonomous LLM planning without deterministic governance gates, used Phase 2A/2B/2C/2D labels that implied time-bounded subphases rather than maturity gates, and conflated retrieval with case execution control.

| File Deleted | Reason for Deletion |
|-------------|---------------------|
| `BIG_PHASE_2_MASTER_ARCHITECTURE.md` | Replaced by `01_MASTER_ARCHITECTURE.md`. Old file described a chat-centric pipeline; new file is case-centric with explicit Phase 1/Phase 2 boundary. |
| `BIG_PHASE_2_IMPLEMENTATION_ROADMAP.md` | Replaced by `02_IMPLEMENTATION_ROADMAP.md`. Old file lacked hard gates between phases; new file defines explicit pass/fail gates and "delay permanently" items. |
| `MEMORY_ARCHITECTURE.md` | Replaced by `06_MEMORY_AND_CONTEXT_MODEL.md`. Old file proposed episodic memory (90-day vectors) and semantic memory (365-day customer profile embeddings) — both DPDP Act violations. |
| `PHASE_2A_INTELLIGENCE_LAYER.md` | Superseded. "Phase 2A" label deprecated. Relevant content (topic classifier, retrieval scoping) absorbed into `03_CASE_STATE_AND_DECISIONING.md` and `05_RETRIEVAL_AND_KNOWLEDGE_LAYER.md`. |
| `PHASE_2B_ACTION_SYSTEMS.md` | Superseded. "Phase 2B" label deprecated. Relevant content (tool calling, idempotency, circuit breaker) rewritten in `08_TOOL_CALLING_AND_IDEMPOTENCY.md` with stronger safety guarantees and explicit tool registry. |
| `GOVERNANCE_EVOLUTION.md` | Replaced by `07_GOVERNANCE_POLICY_AND_HANDOFF.md`. Old file described governance as an evolving quality improvement mechanism; new file defines governance as a hard control gate with enumerated triggers, static action classification, and immutable audit tables. |
| `TOOL_CALLING_AND_AUTOMATION.md` | Replaced by `08_TOOL_CALLING_AND_IDEMPOTENCY.md`. Old file described tool calling without idempotency guarantees, circuit breaker design, or prompt injection defenses. |
| `PHASE_2C_OPERATIONAL_PLATFORM.md` | Superseded. "Phase 2C" label deprecated. Operational analytics content rewritten in `09_OPERATIONAL_ANALYTICS_AND_EVALUATION.md` with specific KPI targets, feedback schema, calibration loop process, and LLM-as-judge evaluation dimensions. |
| `PHASE_2D_ENTERPRISE_PLATFORM.md` | Superseded. "Phase 2D" label deprecated. Enterprise-scale content absorbed into Level 3 section of `02_IMPLEMENTATION_ROADMAP.md` with explicit prerequisite gate (Level 2 stable AND throughput ceiling hit). |
| `RISKS_AND_ARCHITECTURAL_WARNINGS.md` | Replaced by `10_RISKS_AND_ANTI_PATTERNS.md`. Old file listed generic risks; new file provides: specific anti-pattern descriptions with real-world consequences, KwikID-specific regulatory risks (Security Freeze, Hard Lock, Aadhaar masking, deepfake handling), and explicit "what not to build" list. |

---

## Files Created

| File Created | Contents |
|-------------|----------|
| `01_MASTER_ARCHITECTURE.md` | System overview with ASCII layer diagram; component role definitions (what each owns, what it explicitly does not do); Phase 1 vs Phase 2 boundary; explicit out-of-scope list; 10-entry Architecture Decision Log with business/compliance/failure mode reasoning. |
| `02_IMPLEMENTATION_ROADMAP.md` | Sprint-level breakdown for Level 1 (3 months, 1 engineer); Level 2 component list with constraints; Level 3 prerequisites and justification criteria; hard gates between each level; "delay permanently" list; team size and infrastructure table. |
| `03_CASE_STATE_AND_DECISIONING.md` | Case State Machine with 9 states (NEW through FAILED); operational failure modes of chat-centric design; topic classifier architecture (5 families, Tier 1 keyword/regex, Tier 2 semantic); three-layer hybrid decision model; slot filling and clarification design with validation patterns and failure handling. |
| `04_WORKFLOW_ENGINE.md` | Why a workflow engine is needed (not AI, infrastructure); engine selection comparison (Temporal vs LangGraph vs declarative playbooks); complete YAML playbook example for VKYC_Bandwidth_Failure; saga compensation pattern; dead-letter queue; Level 2 state separation (LangGraph vs Temporal vs PostgreSQL Saga boundaries). |
| `05_RETRIEVAL_AND_KNOWLEDGE_LAYER.md` | Retrieval as bounded support service; ABAC predicate pushdown security fix (SQL implementation); retrieval roles at Level 1/2/3; Graph RAG justification criteria (when justified vs not); synthetic SOP generation process (human-supervised, not automated). |
| `06_MEMORY_AND_CONTEXT_MODEL.md` | Five-tier memory taxonomy (ASCII diagram); hard constraints on LLM context (Aadhaar, phone, PAN, CRM data, conversation history); case_checkpoints schema with TTL and retention policy; explicit rejection of the previous memory design (episodic + semantic embeddings) with three compliance/quality/complexity reasons; offline-only learned memory process. |
| `07_GOVERNANCE_POLICY_AND_HANDOFF.md` | Three governance layers (intake, retrieval, action); action risk classification table (SAFE/REVERSIBLE/IRREVERSIBLE with examples); 10-trigger human handoff enumeration; Transfer Context Payload full schema with posting rules; RBI V-CIP compliance requirements (Aadhaar, biometric, session token); two audit table schemas (case_audit_log and security_compliance_audit). |
| `08_TOOL_CALLING_AND_IDEMPOTENCY.md` | Proposal-execution split diagram; idempotency key construction and Redis storage; ToolDefinition schema; Level 2 tool registry with risk levels and rate limits; IRREVERSIBLE tools blocked list; Redis circuit breaker implementation (three states, shared Redis state); rate limiting and OTP retry enforcement; prompt injection defense layers. |
| `09_OPERATIONAL_ANALYTICS_AND_EVALUATION.md` | Mandatory telemetry list (per case, per action, per escalation, per retrieval); 10-KPI table with Level 1/2 targets and measurement cadence; feedback event type schema (7 event types); calibration report (4 categories: miscalibration, retrieval gaps, classification failures, threshold drift); calibration change process; LLM-as-judge evaluation (groundedness, branch completeness, PII leakage) with schemas. |
| `10_RISKS_AND_ANTI_PATTERNS.md` | 7 anti-patterns (Planner Everywhere, Memory Everywhere, Over-Agentification, Chat-Centric Pipeline, Graph RAG as Backbone, Dynamic Multi-Agent Network, Autonomous Customer-Facing Writes) — each with: what it looks like, consequence, correction; explicit "what not to build" list; production failure modes with mitigations; KwikID-specific risks (Security Freeze, Hard Lock, Aadhaar masking, OTP channel exhaustion, session ID collision, deepfake handling). |
| `CHANGELOG.md` | This file. Documents the documentation revision history and architecture direction change. |

---

## Files Kept

| File | Status |
|------|--------|
| `Sprint_0_Latency_Diagnosis_Report.md` | Kept unchanged. It is a technical sprint report describing latency profiling results for the Phase 1 singleton stabilization work. It does not describe architecture; it is a historical record. |
| `PERFORMANCE_AND_SCALING_REVIEW.md` | Kept unchanged. Still valid as a performance analysis of the Phase 1 RAG stack. Its findings inform the P95 latency targets in `09_OPERATIONAL_ANALYTICS_AND_EVALUATION.md`. |
| `DATA_AND_FEEDBACK_PIPELINE.md` | Kept with header update. The header now states that this document is scoped to case-level feedback signals only, and references `06_MEMORY_AND_CONTEXT_MODEL.md` and `09_OPERATIONAL_ANALYTICS_AND_EVALUATION.md` as the authoritative sources for memory design and evaluation pipeline. Session-level memory and customer profile vectorization previously described in this document are rejected (see Section 4 of `06_MEMORY_AND_CONTEXT_MODEL.md`). |

---

## Architecture Direction Change

### From Chat-Centric to Case-Centric

The most significant architectural shift in this revision is the replacement of the chat conversation as the primary system object with the Case/Ticket record. The previous architecture was organized around conversation threads — each session started fresh, state was reconstructed from conversation history, and escalation meant handing over a chat transcript. This design fails in practice because KYC support issues span multiple sessions, require immutable audit trails, and must survive infrastructure restarts. The Case/Ticket record is persistent, carries SLA timestamps, can be serialized for human handoff, and is the correct boundary for compliance reporting.

### From Exploratory Agents to Deterministic Workflow with Bounded AI

The previous architecture proposed broad LLM planning autonomy — agents given large tool sets and asked to construct execution plans dynamically. This revision replaces dynamic planning with a deterministic workflow engine (declarative playbooks at Level 1, Temporal at Level 2) where LLM reasoning is restricted to three bounded roles: evaluating unstructured transition conditions, generating grounded diagnostic text, and compiling the Transfer Context Payload. Every other decision — topic routing, tool selection, action risk classification, escalation triggering — is made by deterministic code. This is not a limitation; it is a deliberate architectural choice that makes the system auditable, maintainable, and compliant.

### From Accumulated Memory to Zero-Copy Reads and Relational Checkpoints

The previous memory design proposed persistent vector stores for customer interaction history (90-day episodic memory, 365-day semantic memory). These are removed entirely. They violate the DPDP Act's purpose limitation and storage limitation principles, introduce semantic noise into retrieval, and provide no benefit that cannot be achieved more safely. The replacement is: a relational case checkpoint (PostgreSQL Saga table) for active workflow state, and a zero-copy read-only CRM API call for customer account state at case initiation. No persistent vector store for customer history at any scope.

### From Phased Subphases to Maturity Gates

The previous architecture used "Phase 2A / 2B / 2C / 2D" labels, which implied sequential calendar-based phases with work divided by label. This revision replaces that structure with three maturity levels (Level 1 / Level 2 / Level 3) gated by measurable criteria. Level 2 does not begin until Level 1 passes its gates. Level 3 does not begin until Level 2 is stable and throughput ceiling is demonstrably reached. Level 3 is not a default — it is justified only by traffic evidence. This prevents premature complexity and ensures that each capability is production-proven before the next is introduced.

---

## Why This Architecture

- **Case-centric because compliance requires it.** RBI V-CIP mandates an immutable audit trail of every action taken during a customer's KYC process. Only a persistent case record can provide this; a conversation thread cannot.

- **Deterministic intake gate because unknown topics must never reach open-ended LLM reasoning.** A prompt injection in ticket content, combined with an open-ended LLM planner, produces an attack surface. A hard classifier gate eliminates this by routing unknown inputs to humans before any LLM reasoning executes.

- **Proposal/validation/execution split because AI outputs must not write directly to production systems.** The validation gateway is the primary defense against prompt injection, malformed action proposals, and unauthorized mutations. Without it, the system cannot be safely operated.

- **Zero persistent vector memory for customers because DPDP Act compliance is non-negotiable.** Building a technical capability that violates data protection law is not acceptable regardless of its functional appeal.

- **Phase 1 RAG preserved as-is because replacing what works has real cost and no benefit.** Phase 1 retrieval is production-stable. Phase 2 adds on top of it, not in place of it. The ABAC security fix is applied to Phase 1 retrieval as a security patch, not an architectural change.

- **Human handoff as a first-class outcome because early automation coverage will be partial.** Designing escalation as an afterthought produces poor human handoff (no context, re-read transcript required). The Transfer Context Payload ensures human agents receive complete diagnostic context immediately.

- **Calibration loop offline and human-reviewed because production feedback is noisy.** Automatic training from feedback signals creates PII risk, catastrophic forgetting risk, and trust risk. The offline loop with human review is slower but safe.

- **Level 3 is gated behind throughput evidence because premature infrastructure complexity is a primary failure mode.** Kafka, supervisor orchestrators, and compliance trust layers add significant operational overhead. They are justified only when the simpler infrastructure is demonstrably insufficient.

---

## What We Are Not Building in Phase 2

1. Customer-facing autonomous AI content generation (no public replies, no SMS from AI without human review)
2. Write-level mutations on KYC records (Aadhaar modification, PAN bypass, liveliness override)
3. Persistent vector memory for customer interaction history (episodic or semantic)
4. Dynamic multi-agent networks with peer-to-peer negotiation
5. Open-ended LLM tool planning (LLM chooses tools from a large unscoped list)
6. Graph RAG as the case execution control plane
7. Real-time automatic training from production feedback
8. Multi-tenant retrieval without ABAC predicate pushdown (this is a Level 1 prerequisite fix, not a feature)
9. Kafka event bus (Level 3 only, gated behind throughput evidence)
10. Zero-copy data connector to live banking registry (Level 3 only, requires bank API contract and security review)
