# 02 — Implementation Roadmap

## Overview

Three maturity levels. Each level has hard gates before the next begins. Level 3 is not a default — it starts only when Level 2 is stable and throughput ceiling is actually reached.

Priority throughout: reduce ticket handling time and human re-work for the five highest-volume issue families (OTP delivery failure, VKYC session failure, document OCR failure, agent portal issues, API/callback failures).

---

## Level 1 — Fast-Path MVP

**Timeline:** 3 months  
**Team:** 1 engineer  
**Infrastructure:** Existing Phase 1 stack (PostgreSQL + pgvector, Redis, Freshdesk API). No new infrastructure required.  
**Customer-facing AI writes:** None. Private notes to agents only.  
**Automated actions:** None. Read-only diagnostic output only.

### What Level 1 Delivers

A ticket enters the system via Freshdesk webhook. The topic classifier assigns it to one of five families. Phase 1 RAG retrieves relevant SOP content. A grounded Diagnostic Context Payload is generated and posted as a Freshdesk private note. If classifier confidence is below 0.85, the ticket is routed to the L1 human queue immediately. State is stored in the Freshdesk ticket record — no external state database required at this level.

### Sprint Breakdown

**Month 1 — Intake Gate and Classifier**

- Freshdesk webhook listener (hardened: HMAC signature verification, malformed payload rejection)
- Deterministic topic classifier with 5 topic families:
  - `OTP_Delivery_Failure`: OTP not arriving, invalid, expired, limit hit, channel switch
  - `VKYC_Session_Failure`: link not received/expired, camera/mic/video failures, bandwidth, liveliness
  - `Document_OCR_Failure`: PAN OCR mismatch, Aadhaar XML timeout, face match failure
  - `Agent_Portal_Issue`: login failures, account lock, queue visibility, performance issues
  - `API_Callback_Failure`: CBS callback failure, DMS failure, SFDC integration failure
- Tier 1: keyword/regex rules covering ~70% of known cases, < 5ms latency
- Tier 2: semantic embedding classifier for ambiguous inputs
- Confidence scoring: output `{topic, confidence, tier_used}`
- Unknown topic or confidence < 0.85: route directly to human queue, do not enter RAG path
- Labeled test set: minimum 200 examples per topic family, sourced from historical Freshdesk tickets
- Deliverable: classifier accuracy > 85% on labeled test set before proceeding to Month 2

**Month 2 — RAG Diagnostic Payload and Private Note**

- Topic-scoped RAG call: pass classified topic as retrieval filter to Phase 1 RAG
- ABAC predicate pushdown fix applied to pgvector queries (security priority — see `05_RETRIEVAL_AND_KNOWLEDGE_LAYER.md`)
- Diagnostic Context Payload schema:
  ```json
  {
    "classified_topic": "VKYC_Session_Failure",
    "confidence": 0.93,
    "match_type": "exact_match",
    "sop_steps": ["Step 1: ...", "Step 2: ..."],
    "cited_sop_ids": ["sop_vkyc_bw_001"],
    "pii_masked": true,
    "generated_at": "2026-06-01T10:00:00Z"
  }
  ```
- PII masking pipeline (Aadhaar 8-digit redaction, phone last-4 only, PAN last-4 only) applied before payload generation
- Private note publisher: post payload to Freshdesk ticket via API
- Freshdesk note tagged with `ai-diagnostic` for agent visibility
- End-to-end test: webhook → classifier → RAG → note published on 100 synthetic tickets

**Month 3 — PII Hardening, Confidence Gate, Escalation Routing**

- PII audit scan: automated check that private notes contain no unmasked Aadhaar, PAN, or phone numbers
- Confidence gate enforcement: hard cutoff at 0.85; below → post escalation note to L1 human queue instead of diagnostic payload
- L1 escalation note content: `"Topic: OTP_Delivery_Failure | Confidence: 0.71 | Reason: Below threshold — human review required."`
- Match type gate: `no_match` result from Phase 1 RAG → escalate regardless of classifier confidence
- Escalation routing: Freshdesk tag `ai-escalate-l1` added to ticket; ticket priority updated to High
- Rate limit protection on Freshdesk API calls: max 100 requests/minute, exponential backoff on 429
- Logging: every classifier decision, RAG call outcome, and note published event written to `case_audit_log`

### Level 1 Gates (must pass before starting Level 2)

| Gate | Requirement |
|------|-------------|
| Classifier accuracy | > 85% on labeled test set (per-family, not only aggregate) |
| Private note delivery | > 99% of webhook calls produce correct note or escalation note |
| PII audit | Zero unmasked PII in any private note (automated scan passes on 1,000-note sample) |
| No customer-facing writes | Confirmed by code review: no Freshdesk reply-to-customer calls exist |
| Audit log coverage | 100% of classification and note events have audit log entries |

---

## Level 2 — Production Architecture

**Timeline:** Months 4–9  
**Team:** 2–3 engineers  
**Infrastructure additions:** LangGraph, Temporal (or lightweight alternative), Redis slot state, PostgreSQL Saga tables  
**Customer-facing AI writes:** Still none. All outputs remain Freshdesk private notes.  
**Automated actions:** Reversible only. Human sign-off required for any IRREVERSIBLE action.

### Components Added in Level 2

**LangGraph State Coordinator**
Manages conversational states within a session: `Awaiting_Input`, `Model_Reasoning`, `Generating_Response`, `Validation_Check`. Does not manage durable transaction state (Temporal owns that). LangGraph state is ephemeral — it does not survive container restart. Temporal checkpoints provide the durability.

**Durable Workflow Engine (Temporal)**
Manages multi-step transaction state: `Checking_PAN`, `OTP_Sent`, `OTP_Verified`, `Liveliness_Pending`, `Human_Takeover`. Event-sourced: on container restart, Temporal replays from last checkpoint. PostgreSQL Saga tables store checkpoint data as a secondary durable record.

**Slot Filling Processor**
When mandatory parameters are missing (session_id for VKYC link regen, phone number for OTP resend), activates `AWAITING_INPUT` state. Redis stores partial slot state with 30-minute TTL. Single targeted clarification question per turn. Validation regex applied to customer response before accepting slot value.

**Action Validation Gateway**
Receives structured JSON action proposals from workflow nodes. Validates: schema correctness, session ownership (customer owns the session_id in the proposal), RBAC (agent role allows the action), risk classification (SAFE/REVERSIBLE/IRREVERSIBLE), idempotency key lookup. Executes SAFE and REVERSIBLE actions. Routes IRREVERSIBLE to human sign-off queue.

**Redis Idempotency Cache**
SHA-256 key per action: `sha256(case_id + action_name + sorted_params)`. TTL: 24 hours. On duplicate request: short-circuit, return cached response. Prevents: double OTP sends, duplicate escalations, duplicate Freshdesk notes.

**Transfer Context Payload on Escalation**
On any ESCALATED state trigger, compile the full Transfer Context Payload (see `07_GOVERNANCE_POLICY_AND_HANDOFF.md`) and post as Freshdesk private note with `ai-transfer-context` tag. Human agent receives complete diagnostic context without reading the transcript.

**Case Memory (PostgreSQL Saga Tables)**
`case_checkpoints` table stores: case_id, state, topic, slot_state, remediation_log, failure_code, escalation_reason, outcome. Written at every state transition. TTL on Redis hot cache: 15 minutes after resolution. PostgreSQL record kept for audit.

### Level 2 Tool Set (Reversible Actions Only)

- `get_vkyc_session_status` (READ_ONLY)
- `get_otp_delivery_log` (READ_ONLY)
- `get_service_health` (READ_ONLY)
- `add_ticket_tag` (REVERSIBLE)
- `update_ticket_priority` (REVERSIBLE)
- `post_private_note` (REVERSIBLE)
- `send_otp_retry_notification` (REVERSIBLE — max 3/session, channel-switching enforced)
- `create_escalation_task` (REVERSIBLE)

Not in Level 2: Aadhaar field modification, PAN verification bypass, customer-facing public reply, liveliness override.

### Level 2 Constraints
- All customer-visible content is still human-reviewed before delivery
- Circuit breaker wraps all external API calls
- All action executions are logged with idempotency key, outcome, and actor
- IRREVERSIBLE actions are blocked and routed to human, not queued for retry

### Level 2 Gates (must pass before starting Level 3)

| Gate | Requirement |
|------|-------------|
| Workflow completion rate | > 95% of initiated workflows reach terminal state without manual intervention |
| Idempotency verification | Zero duplicate OTP sends or duplicate notes in 30-day production run |
| Action gateway rejection rate | < 2% of proposals rejected due to schema or validation errors (indicates workflow correctness) |
| Escalation rate | Tracked and stable (not necessarily low — accurate escalation is valuable) |
| Throughput ceiling not hit | Level 3 is not justified unless Level 2 infrastructure is at capacity |

---

## Level 3 — Enterprise Scale

**Prerequisite:** Level 2 is stable in production AND throughput ceiling is actually hit. Level 3 is not a default milestone.

**Timeline:** Months 10+ (start date determined by traffic data, not by calendar)  
**Team:** 4+ engineers, dedicated DevOps/SRE  
**Infrastructure additions:** Kafka/MSK, supervisor orchestrator, zero-copy data connector, compliance trust layer

### Components (Only When Justified)

**Kafka/MSK Event Bus**
Justified when webhook-to-processing latency is the bottleneck, or when event fan-out to multiple consumers is required. Not justified at Level 2 throughput. Adds operational complexity (consumer group management, offset tracking, schema registry).

**Supervisor Orchestrator Agent**
A deterministic state machine that routes cases to specialized workers (VKYC worker, OTP worker, Document worker). Workers are stateless. The supervisor is not an LLM — it is a rule-based router with LLM reasoning available only for ambiguous routing decisions. No dynamic agent-to-agent negotiation.

**Zero-Copy Data Connector to Live Banking Registry**
Real-time read from CBS/banking core via secure connector. Data is never stored locally. Used for account status checks during complex escalation triage. Requires bank-approved API contract and security review.

**Compliance Trust Layer**
- PII masking enforced at pipeline ingress (not post-hoc)
- Aadhaar digit redaction at ingestion point
- Immutable audit trail with cryptographic integrity (append-only table with row-level checksums)
- All AI outputs evaluated for compliance before any external write
- Dedicated security_compliance_audit table routes to bank SOC in real-time

### What to Delay Permanently (or Until Level 3 is Explicitly Justified)

**Customer-facing conversational write pipeline.** Requires evaluation framework, human review gate, and legal sign-off. Not safe to build speculatively.

**Write-level API database mutations.** OTP clear, VKYC session reset, Aadhaar field modification — these require bank-level change management and legal approval beyond the support system's scope.

**Dynamic multi-agent network orchestration.** No business case exists for peer-to-peer agent discovery. Adds latency and non-determinism without operational benefit at any current scale.

**Dynamic unstructured long-term agent vector memory.** Violates DPDP Act. Adds semantic noise. The same goals (personalization, context) are met correctly by case memory (relational) and zero-copy CRM reads.

---

## Team Size and Infrastructure Summary

| Level | Engineers | Key Infrastructure | External Dependencies |
|-------|-----------|-------------------|----------------------|
| Level 1 | 1 | Existing Phase 1 stack | Freshdesk API, existing pgvector DB |
| Level 2 | 2–3 | + LangGraph, Temporal, Redis slot state, PostgreSQL Saga | Freshdesk API, CRM read API (new) |
| Level 3 | 4+ | + Kafka/MSK, supervisor orchestrator, compliance layer | Bank CBS API, security review, legal sign-off |
