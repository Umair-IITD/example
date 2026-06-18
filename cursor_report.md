---
name: support-agent-architecture-review-and-build-plan
overview: Deep read-only review of the kwikid_support_system against the SUPPORT_OPERATIONS_BLUEPRINT and flow_diagram.mermaid, plus a concrete plan to complete the Enterprise Support Agent from its current state.
todos:
  - id: align-golden-case-flow
    content: Document and standardize a single golden support-case flow through the case engine, deprecating or clearly scoping legacy RAG/chat paths.
    status: pending
isProject: false
---

## Support Agent Architecture Review & Completion Plan

### 1. Alignment With Source-of-Truth Blueprint

- **Ticket Ingestion & Case Layer (Layers 1–2)**
  - **Blueprint expectation**: Dedicated Ticket Ingestion layer feeding a Case Engine that owns case creation, state transitions, and workflow tracking (`CREATED → TRIAGE_COMPLETE → WORKFLOW_ACTIVE → AWAITING_INPUT → ACTION_PENDING → RESOLVED/ESCALATED → CLOSED`).
  - **Current implementation**:
    - `fumadocs_ingest_service/app/main.py` exposes webhook/API entrypoints that play the role of `Ticket Ingestion Service` and `Case Service` combined.
    - `case_engine/` (especially `case_state.py`, `state_machine.py`, `service.py`, `repository.py`, `models.py`) implements a **deterministic case state machine** with explicit enums and transition logic that map well to the blueprint states.
    - `CASEDB` in the mermaid diagram is represented by the case repository abstraction and backing storage (DB models + repositories; in-memory fallbacks exist for resilience/testing).
  - **Assessment**: **Strong alignment**. The case state machine, repository pattern, and separation between API layer and case lifecycle match the blueprint intent.

- **Topic Classification & Workflow Selection (Layers 3–4)**
  - **Blueprint expectation**: Explicit Topic Classification and Workflow Selection stages after case creation.
  - **Current implementation**:
    - Case service/workflow engine expose hooks for classification and workflow start; classification logic and LLM-backed topic models are partially wired but not fully implemented.
    - `workflow_engine` and workflow/playbook modules model the `Workflow Selection` and `Workflow Engine` concepts from the mermaid diagram.
  - **Assessment**: **Structurally present but partially stubbed**. The seams for classification and workflow selection are there; concrete topic models and full playbook catalog are future work.

- **Slot Extraction & Clarification (Layers 5–6)**
  - **Blueprint expectation**: Slot Extraction (URN, Session ID, etc.) with a Clarification loop when information is missing (`AWAITING_INPUT` state, outbound clarification messages to user via Freshdesk).
  - **Current implementation**:
    - Slot state and required-field tracking are modeled in `case_engine/action_state.py` and related models; case workflows can branch on slot completeness.
    - A dedicated `clarification` package (engine, models, service) plus `clarification_engine` hooks exist as a distinct module, matching the mermaid `CLARIFICATION` node and blueprint section.
    - Clarification routing back to Freshdesk/customer is designed as part of the response/adapter system; concrete Freshdesk send APIs are intentionally mocked.
  - **Assessment**: **Good structural alignment**, with message-transport adapters and UX copy still future tasks.

- **Knowledge Layer (SOPs, Knowledge Base, Workflow Playbooks – Section 6, flow_diagram “Knowledge Layer”)**
  - **Blueprint expectation**: A Knowledge Layer fed by SOP repository, historical fixes, and workflow playbooks; Hybrid RAG + vision powering reasoning.
  - **Current implementation**:
    - Data/SOP content lives under `data/sop/` (client registry + per-client SOP markdown), consistent with “SOP Repository” / “Workflow Playbooks” intent.
    - Ingest/RAG stack exists under `app/ingest.py`, `rag_engine/`, and `knowledge/` modules (importer, repository, matcher, retriever, service, unified_bundle/orchestrator); they represent `INGEST → INDEX → HYBRIDRAG` in the mermaid diagram.
    - Knowledge orchestration explicitly acknowledges Hybrid RAG as a future convergence point (currently partially mocked / template-based).
  - **Assessment**: **Conceptually aligned and partially implemented**. SOP data and ingest plumbing exist; end-to-end closed-loop Hybrid RAG for case reasoning is a **future task**.

- **Investigation Layer (Sections 7–12, mermaid “Investigation Planner” + “Evidence Collector” + tools)**
  - **Blueprint expectation**: Automate L1 investigation: URN/session lookup, logs/summary/video analysis, evidence aggregation, and root-cause candidates.
  - **Current implementation**:
    - `case_engine/investigation/` (collector, planner, observation, models, service) maps very closely to the blueprint’s Investigation Objective, Inputs, URN lookup, Session lookup, Logs/Summary analysis.
    - Tool-facing abstractions exist in `case_engine/integrations/` and `case_engine/tools`/adapters modules (Freshdesk, portal, metrics, logs, video, etc.), with many concrete integrations explicitly mocked.
    - Evidence objects produced by investigation feed into root-cause and reasoning modules.
  - **Assessment**: **One of the strongest matches** to the blueprint. Business workflow is captured well. The main gaps are external API wiring and richer heuristics/LLM-based signal fusion, which are correctly treated as future tasks.

- **Reasoning Engine & Observation Generator (Sections 13–14)**
  - **Blueprint expectation**: Evidence → Root Cause + Confidence + Recommended Action/Escalation; plus structured internal notes mirroring L1.
  - **Current implementation**:
    - Reasoning modules under `case_engine/reasoning/` and `case_engine/root_cause` convert investigation evidence into root-cause and recommendations, mirroring the blueprint’s “Evidence before reasoning, reasoning before execution” principles.
    - `case_engine/response_generation/` and `observation` models/services generate internal notes and customer-facing drafts; they align well with the Observation Generator spec and the “Issue Summary / Observed Evidence / Root Cause / Recommended Action / Escalation Required” schema.
    - LLM invocation is currently abstracted behind provider/router modules, with some paths still mocked.
  - **Assessment**: **High conceptual alignment**, with LLM prompts/policies and note templates still being refined. Good separation between evidence-based reasoning and note-writing.

- **Action System, Action Gateway, Risk/Approval/Execution/Verification/Recovery (Sections 15–20, Execution layer in mermaid)**
  - **Blueprint expectation**: All actions behind Action Gateway; risk-based routing (SAFE/REVERSIBLE/HIGH), explicit approvals, execution, verification, and recovery (retry/rollback/DLQ), all audited.
  - **Current implementation**:
    - `case_engine/action_gateway/` (gateway, models, risk_engine, approval_engine, execution_gateway, service) mirrors the blueprint almost one-to-one: proposal → risk check → approval → executor → verification → recovery/rollback/DLQ.
    - Action repository and runtime track action states and transitions aligned with the SAFE / REVERSIBLE / HIGH model.
    - Recovery and retry modules (`case_engine/execution/recovery.py`, `case_engine/retry/`) model the Recovery Layer including DLQ semantics.
    - Watchdog and worker loops (wired from `runtime/assembly.py` and `app/main.py`) enforce progress, reschedule stuck actions, and coordinate approvals.
  - **Assessment**: **Very strong implementation** relative to the blueprint. This is the clearest, deepest part of the system, with good leverage at the `Action Gateway` seam.

- **Escalation & L2 Automation (Sections 21–23, mermaid L2 workflow / Asana)**
  - **Blueprint expectation**: L2 package + Asana ticket creation + engineering coordination + Freshdesk update.
  - **Current implementation**:
    - Ticket orchestration and support-agent runtime modules model L2 escalation and engineering packaging.
    - Asana/Freshdesk integrations are represented by clear adapter interfaces and mock implementations, including specific future Asana ticket creation and note-update flows.
  - **Assessment**: **Flow is modeled; integrations are intentionally stubbed**. Correctly treated as future tasks, not missing architecture.

- **Customer Response & Ticket Closure (Sections 24–25)**
  - **Blueprint expectation**: Professional replies using SOPs/root-cause/resolution; ticket closure only after resolution and verification.
  - **Current implementation**:
    - Response generation and case completion logic respect the “Reasoning before execution, verification after execution, closure only after verified resolution” principles.
    - LLM is used as a rewriting/summarization/humanization layer; execution is kept in dedicated modules, not in prompts.
  - **Assessment**: **Aligned on responsibilities and guardrails**, with more work to do on customer-voice templates, localization, and integration with Freshdesk send endpoints.

- **Guardrails, Security, Audit (Sections 26–29, Governance subgraph in mermaid)**
  - **Blueprint expectation**: No component bypasses Action Gateway; strong authentication/authorization; exhaustive audit coverage for all major events.
  - **Current implementation**:
    - Action Gateway is the only sanctioned path for side-effecting actions in the case engine.
    - `audit/models.py` and audit hooks across case, workflow, action, and recovery modules map to the Governance box edges in the mermaid (`CASE -.-> AUDIT`, `ENGINE -.-> AUDIT`, etc.).
    - Some authz concerns and tenant/role modeling are currently out of scope or partially stubbed.
  - **Assessment**: **Good beginnings**, especially for audit. Security/authorization/PII controls are not yet fully implemented and should be treated as **explicit future work**.

### 2. Strong Points (What Is Working Well)

- **Deep, deterministic case + action state machines**
  - Explicit enums and state transition tables increase leverage: complex case lifecycles are driven by a small, understandable interface.
  - This strongly supports the blueprint’s safety principles and makes behavior auditable.

- **Clear Action Gateway seam with strong domain language**
  - SAFE/REVERSIBLE/HIGH risk levels, explicit approval workflows, and isolated executors are all modeled in dedicated modules.
  - This matches the blueprint’s insistence that **no external action may bypass Action Gateway**.

- **Investigation layer closely mirrors real L1 workflows**
  - URN/session lookup, logs/summary analysis, evidence modeling, and observation generation all map well to the described human process.
  - This increases trust that what is automated is actually what L1 agents do today.

- **Knowledge and SOP data are first-class**
  - SOPs and client-specific details live in `data/sop/`, which is already wired into ingestion and knowledge modules.
  - This keeps business truth in content rather than hardcoded in logic.

- **Composition root and modularity**
  - `runtime/assembly.py` provides a single place to wire major services, gateways, adapters, and workers.
  - Adapters for Freshdesk, Asana, metrics, logs, etc., are separated from core reasoning/investigation logic, giving good seams for future real integrations.

- **Audit and recovery thinking are baked in**
  - Audit models span case, workflow decisions, actions, approvals, retries, rollbacks, and closures.
  - Recovery and retry layers exist as first-class modules rather than afterthoughts.

- **Ingest/RAG stack is reusable across knowledge and support**
  - The ingestion CLI and RAG engine are generic enough to support SOP ingest, historical resolutions, and StackOverflow Teams exports in the way the blueprint describes.

### 3. Weak Points, Gaps, and Risks (Framed as Future Tasks, Not Bugs)

- **1) Dual architecture paths (legacy RAG vs case-engine convergence)**
  - **Symptom**: Older `app/*` RAG/chat paths and newer `case_engine`-centric convergence logic coexist, which can confuse new contributors and reviewers.
  - **Risk**: Higher cognitive load; potential for drift between “old” and “new” surfaces; harder to guarantee that all traffic flows through the canonical blueprint path.
  - **Desired end state**: One clearly documented “golden path” for support-case flows; legacy endpoints either deprecated or explicitly documented as auxiliary tools.

- **2) Security, authz, and PII handling still immature**
  - **Symptom**: Guardrails and audit exist, but detailed authentication/authorization layers, tenant isolation, and PII masking are not yet fully fleshed out.
  - **Risk**: Production rollout into real bank environments will require a much deeper security review (RBAC, data minimization, consent, key management, etc.).
  - **Desired end state**: A dedicated security/guardrail module and policy layer fronting Action Gateway, knowledge access, and LLM calls.

- **3) Knowledge/RAG convergence is still early**
  - **Symptom**: RAG ingestion and retrieval are in good shape; the bridge between root-cause findings and targeted SOP/historical resolution lookup is still partly mocked.
  - **Risk**: Without strong knowledge convergence, the reasoning engine leans too much on the LLM instead of on codified SOPs and known fixes.
  - **Desired end state**: Deterministic pipeline `Investigation → Root Cause → Knowledge Search → SOP/Playbook → Action Proposal`, with LLM mainly for explanation/phrasing.

- **4) External integrations all sitting behind mocks**
  - **Symptom**: Freshdesk, Asana, admin portal, metrics, logs, and video tools are modeled as adapters and mock tools.
  - **Risk**: Functionally, this is correct for now; the real risk is underestimating the complexity/latency/error modes of these integrations during productionization.
  - **Desired end state**: A hardened adapter layer with clear SLAs, retries, circuit breakers, structured errors, and observability.

- **5) Single, very large FastAPI entrypoint**
  - **Symptom**: `app/main.py` glues together many concerns: routing, lifespan, worker loops, configuration, and runtime assembly.
  - **Risk**: Harder to change or test in isolation; risk of accidental coupling between unrelated endpoints and services.
  - **Desired end state**: A thin API layer delegating to well-factored runtime/assembly modules, possibly split by bounded context (ingest, knowledge, support-case, admin).

- **6) Database and schema design not fully documented vs blueprint**
  - **Symptom**: Repositories and models clearly exist, but the full relational schema, indexing strategy, and capacity plan are not expressed at the same level of precision as the blueprint.
  - **Risk**: Under- or over-indexing, missing uniqueness/integrity constraints, and performance issues at higher ticket volumes.
  - **Desired end state**: An explicit schema aligned with Neon Postgres (or equivalent), with clear tables for cases, actions, audit events, tools, knowledge artifacts, and approvals.

- **7) Testing and QA surface not yet treating the system as a product**
  - **Symptom**: Rich internal structure but relatively less emphasis (so far) on E2E tests that mimic real Freshdesk/FI flows, browser-based QA, and CI-driven regression harnesses.
  - **Risk**: Regressions in complex workflows (clarification loops, rare recovery paths, multi-step action approval) are hard to detect before prod.
  - **Desired end state**: Playwright/webapp testing harnesses and scripted flows that treat the support agent as an end user/system would.

### 4. Improvement Strategy (From Current State to Blueprint Fulfillment)

This plan assumes **API wiring and external integrations are consciously future tasks**, as you specified. The focus is on making the current architecture deep, coherent, and blueprint-faithful so that those integrations can be added predictably.

#### 4.1. Consolidate Architecture Around the Case Engine

- **Goal**: Make the `Case Engine + Action Gateway + Knowledge + Investigation + Reasoning` pipeline the *single golden path* for all support tickets.
- **Steps**:
  - Identify and document all entrypoints (webhook, chat, internal tools) and map which ones already use the case engine and which still use legacy RAG/chat paths.
  - Define a canonical “support case flow” diagram for the **implemented system** (a refinement of `flow_diagram.mermaid` using concrete module names) and add it to docs (no code changes needed to draft it).
  - Plan a gradual deprecation/migration of any endpoints that bypass case workflows so that, eventually, **every support interaction produces a Case and flows through the state machine**.
  - Keep legacy RAG endpoints as internal tools for agents if needed, but clearly mark them non-canonical in docs.

#### 4.2. Harden the Knowledge/RAG Convergence

- **Goal**: Make `Investigation → Root Cause → Knowledge Search → SOP/Workflow Playbook → Action Proposal` deterministic, with LLM as explanation only.
- **Steps**:
  - Use existing SOP markdown and `client_registry.yaml` to define a **knowledge taxonomy**: map topics/root-cause codes to SOP documents and playbooks.
  - In the knowledge orchestrator, plan a pipeline where:
    - Investigation outputs a structured `root_cause` object.
    - That object is used as a query key into RAG/knowledge repository (exact match + semantic expansion).
    - The result is a **concrete SOP/playbook reference** (not just free text).
  - Specify how Hybrid RAG will combine:
    - SOP docs (high precision, curated) and
    - Historical resolutions / engineering notes (broader recall).
  - Use `system-design` and `sql-optimization` guidance to sketch schemas for SOPs, topics, and historical resolutions (e.g., `sop_documents`, `sop_topics`, `root_cause_to_sop`, `resolution_history`).

#### 4.3. Formalize Security, Guardrails, and PII Strategy

- **Goal**: Upgrade from “good intentions” to a **concrete security model** that stands up to bank scrutiny while aligning with blueprint guardrails.
- **Steps**:
  - Define security requirements by area using the blueprint’s Section 27–29 language:
    - **Case & action operations**: RBAC and least-privilege roles (L1, L2, SRE, admin).
    - **Knowledge access**: PII masking, tenant scoping, and logs redaction.
    - **LLM usage**: No raw PII in prompts without masking strategy; strict separation between tool results and LLM interpretation.
  - Decide on an auth/authz stack for the FastAPI apps (e.g., JWT/OIDC with per-tenant scopes), informed by `nodejs-backend-patterns` but applied to Python.
  - Align Action Gateway with a **policy engine** mindset: risk levels, approval thresholds, who can approve which actions, and how policies are configured (DB table or config store).
  - Plan how Neon Postgres (or another DB) will store secrets/keys (ideally it should not; use a secret manager) and where sensitive audit fields live.

#### 4.4. Clarification and L1 Conversation Experience

- **Goal**: Ensure the Clarification Engine feels like a professional L1 agent and never gets stuck or confusing.
- **Steps**:
  - Enumerate slot requirements per topic/workflow (what fields are *really* needed to proceed vs nice-to-have) based on SOPs.
  - For each topic:
    - Define a small **finite set of clarification prompts** and their fallbacks.
    - Decide max clarification loop count and conditions for escalation.
  - Clarify ownership between case engine and chat layer:
    - Case engine tracks slot state and decisions.
    - Response generation/LLM layer renders prompts and user replies.
  - Plan tests (eventually via `webapp-testing`/`playwright-cli`) that simulate ambiguous tickets and verify the clarification loop converges or escalates correctly.

#### 4.5. L2 Automation & Engineering Handoff

- **Goal**: Make the L2 package + engineering handoff flow predictable and information-complete, even before real Asana/Freshdesk APIs are wired.
- **Steps**:
  - From the blueprint’s L2 responsibilities, derive a **canonical “L2 packet” schema**:
    - Case ID, user/session identifiers
    - Root cause (code + narrative)
    - Evidence (logs, summary, video pointers)
    - Steps attempted + actions taken
    - Recommended fix or open questions
  - Ensure ticket orchestration and response generation modules produce this packet shape consistently for escalations.
  - Define a clear mapping from L2 packet → Asana issue (fields, tags, attachments) and from engineering “Resolution Event” → Freshdesk note update.
  - Keep adapters mocked for now, but design their interfaces so later wiring is mechanical.

#### 4.6. Database & Schema Blueprint (Neon/Postgres-Oriented)

- **Goal**: Have a solid, future-proof relational model that supports the blueprint’s audit and lifecycle requirements.
- **Steps** (conceptual, implementation later):
  - Design core tables using `system-design` + `neon-postgres` + `sql-optimization` patterns:
    - `cases` (id, tenant_id, topic, state, created_at, closed_at, metadata JSONB).
    - `case_slots` (case_id, key, value, source, last_updated_at).
    - `actions` (id, case_id, type, risk_level, state, created_at, executed_at, verification_status).
    - `action_events` (id, action_id, event_type, payload, created_at).
    - `audit_events` (id, case_id, action_id?, event_type, actor, payload, created_at).
    - `tools` / `tool_invocations` (tool_id, request, response, status, latency_ms).
    - `sop_documents`, `root_cause_codes`, `root_cause_to_sop`.
  - Apply indexing strategies (from `sql-optimization`):
    - Composite indexes like `(tenant_id, state, created_at)` for active case queries.
    - Partial indexes for hot paths (e.g., open cases, pending approvals, failed actions).
  - Plan for Neon features (branching, read replicas) to support:
    - Safe schema migrations (branches per environment).
    - Read-heavy analytics/reporting via replicas.

#### 4.7. Observability & Operations

- **Goal**: Make failures visible before users notice, in line with the blueprint’s “Zero silent failures” and audit requirements.
- **Steps**:
  - Define **metrics and logs** per module:
    - Case: opened/closed counts, time-in-state histograms, escalation rate.
    - Investigation: tool failure rates, time-to-first-root-cause.
    - Action Gateway: proposals by risk level, approval decisions, execution success/failure rates.
    - Recovery: retries, rollbacks, DLQ depth.
  - Design dashboards:
    - “Support pipeline health” view from ingress → resolution.
    - “Action safety” view for risk levels and failures.
  - Specify alert thresholds (e.g., 5xx rate on webhook, spike in `HIGH` risk actions, sustained DLQ growth).
  - Plan how audit logs relate to observability (audit is truth, metrics are aggregate lenses).

#### 4.8. Testing & QA Strategy (Webapp + Workflow)

- **Goal**: Treat the support agent like a product, with stable automated tests.
- **Steps**:
  - Use `webapp-testing`/`playwright-cli` patterns to design:
    - Test flows for: simple OTP failure ticket, ambiguous VKYC failure, log-based root cause, high-risk action requiring approval.
    - Assertions on internal notes content (contain evidence, root cause, recommended action) and state transitions.
  - Plan dedicated E2E test scenarios where:
    - A synthetic Freshdesk ticket is ingested.
    - Clarification loops gather required slots.
    - Investigation hits mock tools.
    - Reasoning proposes SAFE action and executes via gateway.
    - Verification confirms success; case closes.
  - Over time, add regression tests for bugfixes and production incidents, aligned with `system-design` reliability patterns (canary flows, synthetic traffic).

#### 4.9. Documentation, Onboarding, and Skill Discovery

- **Goal**: Make the system navigable for future contributors and easy to extend with new skills/tools.
- **Steps**:
  - Create a concise “What to read first” guide in docs (not code) pointing to:
    - `flow_diagram.mermaid`
    - `SUPPORT_OPERATIONS_BLUEPRINT.md`
    - The 8–10 most important modules in `case_engine/` and `runtime/`.
  - Use the `find-skills` mindset to identify areas where additional ecosystem skills (e.g., observability, security review, changelog generation) could help, and list them as optional future tooling.
  - Keep blueprint and implementation in sync: any change to workflows or risk model should update both the source-of-truth docs and code.

### 5. Recommended Execution Order

1. **Align and document the golden case flow** (architecture consolidation around case engine; clarify legacy vs canonical paths).
2. **Harden investigation + knowledge convergence** (tie root-cause codes to SOPs/playbooks deterministically).
3. **Design and agree on the security and guardrail model** (roles, policies, PII strategy, LLM usage boundaries).
4. **Define L2 packet schema and escalation flow** (even with mocks; align with Asana/Freshdesk expectations).
5. **Sketch and validate the Postgres/Neon schema** (tables, indexes, capacity planning for expected ticket volume).
6. **Add observability design** (metrics, dashboards, alerts) for each major module.
7. **Plan and gradually build E2E tests** using Playwright/webapp-testing patterns against mock integrations.
8. **Only then**: wire real Freshdesk/Asana/portal/metrics/video APIs into the existing adapter seams, using the hardened architecture as the backbone.

### 6. CEO Review Style Summary

- **Mode**: HOLD SCOPE with **SELECTIVE EXPANSION** — the core architecture matches the blueprint; we should make it bulletproof and selectively deepen critical seams (knowledge, security, observability) before expanding feature surface.
- **Strongest challenges**:
  - Dual-path architecture (legacy vs convergence) that needs a single golden flow.
  - Knowledge/RAG convergence not yet reflecting the full SOP/historical resolution loop.
  - Security/PII/authorization not yet at bank-ready depth.
- **Recommended path**: Commit to the case engine + action gateway as the single canonical flow, deepen investigation and knowledge integration, formalize security/guardrails, then add real integrations and E2E QA on top.
- **Accepted scope**: Automate L1+L2 workflows including investigation, reasoning, action proposal/execution, escalation, response, and closure with strong audit and safety guarantees.
- **Deferred (future tasks)**: Freshdesk/Asana/support-portal APIs, full Hybrid RAG implementation, video analysis tooling, production-grade authz/PII pipeline, and full browser-based QA harness.
- **NOT in scope**: Re-architecting the core case engine or action gateway patterns; they already match the source-of-truth blueprint and should be preserved as the backbone.