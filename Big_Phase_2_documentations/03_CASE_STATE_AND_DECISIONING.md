# 03 — Case State and Decisioning

## 1. The Unit of Work

The Case/Ticket record is the primary object. The conversation thread is not.

This is not a preference — it is an operational requirement. Consider the failure modes of a chat-centric design:

**Context drift:** A conversation thread re-read from scratch on each message accumulates noise. After 10 turns, the LLM context window is dominated by historical turns that are less relevant than the original ticket metadata. Retrieval quality degrades.

**Session loss:** If the customer closes the browser tab, or the mobile app is backgrounded, or the connection drops, the in-flight conversational state is lost. The support transaction cannot resume without re-establishing full context. Multi-step KYC support flows (VKYC re-link → OTP resend → liveliness retry) cannot be safely interrupted and resumed in a chat thread.

**No audit trail:** A conversation thread is not a compliance artifact. It cannot produce an immutable record of: what the system attempted, what the customer was told, what external APIs were called, what the outcome was. RBI V-CIP requires this audit trail.

**SLA blindness:** A chat thread has no SLA timestamp. The Case/Ticket record has `created_at`, `sla_breach_at`, `first_response_at`, `resolved_at`. SLA compliance reporting requires these fields at the system level, not reconstructed from chat logs.

The Case record survives container restarts (PostgreSQL persistence), can be serialized for human handoff (Transfer Context Payload), carries an immutable audit log (case_audit_log), and is the correct boundary for all workflow state.

---

## 2. Case State Machine

```
                  ┌─────────────┐
   ticket arrives │     NEW     │
                  └──────┬──────┘
                         │ classifier invoked
                  ┌──────▼──────┐
                  │ CLASSIFYING │
                  └──────┬──────┘
                         │
          ┌──────────────┼──────────────────┐
          │ confidence   │ confidence        │ unknown
          │ >= 0.85      │ < 0.85            │ topic
          │              │                  │
   ┌──────▼──────┐  ┌────▼──────────────────▼──────┐
   │TRIAGE_      │  │         ESCALATED             │
   │COMPLETE     │  │  Transfer Context Payload     │
   │(Level 1     │  │  posted as private note       │
   │ terminal)   │  └───────────────────────────────┘
   └──────┬──────┘
          │ Level 2: workflow engine activated
   ┌──────▼──────┐
   │ WORKFLOW_   │◄────────────────────┐
   │ ACTIVE      │                     │ clarification
   └──────┬──────┘                     │ received
          │                     ┌──────┴──────┐
          │ slot incomplete     │  AWAITING_  │
          ├────────────────────►│  INPUT      │
          │                     └─────────────┘
          │ action proposed
   ┌──────▼──────┐
   │  ACTION_    │
   │  PENDING    │
   └──────┬──────┘
          │
   ┌──────┴───────────────────────────┐
   │ validated + executed             │ IRREVERSIBLE or
   │                                  │ validation failed
   ▼                                  ▼
┌──────────┐                    ┌──────────┐
│ RESOLVED │                    │ESCALATED │
└──────────┘                    └──────────┘

On max retries exceeded: FAILED → dead-letter queue
```

### State Definitions

**NEW:** Ticket just created via Freshdesk webhook or API. No classification yet. Freshdesk ticket record is the only persistent store at this point.

**CLASSIFYING:** Topic classifier is executing. Transient state. If classifier crashes mid-execution, the case reverts to NEW on restart (idempotent re-classification).

**TRIAGE_COMPLETE:** Topic classified with confidence >= 0.85. Phase 1 RAG retrieved and generated Diagnostic Context Payload. Private note posted to Freshdesk ticket. This is the terminal state for Level 1. No further automation. Human agent acts on the note.

**AWAITING_INPUT:** Workflow has identified missing required parameters (e.g., session_id for VKYC link regen, phone number for OTP channel switch). Workflow paused. Slot state persisted in Redis (TTL 30 min). Single clarification question sent to agent or customer via private note. On slot fill + validation, transitions back to WORKFLOW_ACTIVE.

**WORKFLOW_ACTIVE:** Durable workflow engine is executing a playbook for the classified topic. Playbook state persisted in Temporal + PostgreSQL Saga table. LangGraph manages conversational micro-states within this macro-state.

**ACTION_PENDING:** Workflow node has output a structured JSON action proposal. Awaiting action gateway validation and execution. If SAFE or REVERSIBLE: proceeds to execution automatically. If IRREVERSIBLE: transitions to ESCALATED with the action proposal included in Transfer Context Payload.

**ESCALATED:** System has routed to human. Transfer Context Payload compiled and posted as Freshdesk private note. Case is frozen — no further automated state transitions. Human agent takes ownership.

**RESOLVED:** Workflow reached successful completion OR human agent marked the ticket resolved. Outcome logged. Case checkpoint TTL begins (15 min for Redis eviction; PostgreSQL record kept for audit).

**FAILED:** Maximum retry limit exceeded on workflow steps OR dead-letter queue threshold reached. Case frozen, written to DLQ, human notified. Separate from ESCALATED: FAILED indicates a system execution problem, ESCALATED indicates a deliberate human handoff.

---

## 3. Topic Classifier (Intake Gate)

The classifier is the first and most critical deterministic gate. Every case passes through it. No LLM reasoning executes before classification is complete.

### Five Primary Topics (Level 1)

| Topic Key | Issue Families Covered |
|-----------|------------------------|
| `OTP_Delivery_Failure` | OTP not arriving, OTP invalid, OTP expired, OTP limit hit, channel switch required (SMS/email/voice) |
| `VKYC_Session_Failure` | VKYC link not received, link expired, camera failure, mic failure, video quality failure, bandwidth issues, liveliness command failure |
| `Document_OCR_Failure` | PAN OCR mismatch, Aadhaar XML fetch timeout, face match failure, document quality rejection |
| `Agent_Portal_Issue` | Agent login failure, account lock, queue visibility, performance issues, auditor portal lock/playback |
| `API_Callback_Failure` | CBS callback failure, DMS write failure, SFDC integration failure, post-KYC webhook errors |

### Classifier Architecture

**Tier 1 — Keyword/Regex (< 5ms)**
Covers approximately 70% of production volume. Deterministic rules built from historical ticket text analysis. Examples:
- `VKYC_Session_Failure`: `r"(vkyc|video.?kyc).*(link|session|expired|bandwidth|camera|mic|liveliness)"`
- `OTP_Delivery_Failure`: `r"(otp).*(not.received|invalid|expired|limit|resend|sms|email)"`
- Rules stored in code, version-controlled, not in a database

**Tier 2 — Semantic Embedding (fallback)**
Invoked only when Tier 1 produces no match or confidence is low. Uses same embedding model as Phase 1 RAG (text-embedding-3-small or equivalent). Cosine similarity against per-topic representative embeddings. Returns top-1 topic + confidence score.

**Routing Logic**
```python
result = tier1_classify(ticket_text)
if result.confidence < 0.85 or result.topic is None:
    result = tier2_classify(ticket_text)
if result.confidence < 0.85 or result.topic is None:
    route_to_escalated(case, reason="below_threshold_or_unknown_topic")
    return
proceed_with_topic(case, result.topic, result.confidence)
```

**Unknown Topic Handling**
If neither tier produces a match above threshold, the case goes directly to ESCALATED state. The system never falls through to open-ended LLM reasoning for unknown inputs. This is a hard guarantee.

**Classifier Expansion**
New topics are added by: (1) accumulating 50+ labeled examples from escalated/manual tickets, (2) writing Tier 1 rules, (3) adding Tier 2 representative embeddings, (4) running against labeled test set to verify accuracy > 85% on new topic before deployment.

---

## 4. Hybrid Decision Architecture

Three decision layers execute in sequence. Each layer has a hard gate that can stop execution and route to human.

### Layer 1 — Deterministic Intake Gate
The topic classifier (described above). Runs before any LLM reasoning. Gate: confidence >= 0.85 and topic in known registry.

### Layer 2 — Constrained ReAct Loop
Within the boundary of the classified topic, the LLM reasoning layer may:
- Evaluate transition conditions in the current playbook step
- Parse unstructured text from the ticket to extract slot values
- Generate grounded diagnostic text citing retrieved SOP chunks

What it cannot do:
- Access tools not in the classified topic's tool allowlist
- Generate new playbook states or steps
- Propose IRREVERSIBLE actions
- Speculate about topics outside its classification

The tool allowlist per topic is statically defined (compile-time), not dynamically generated by the LLM.

### Layer 3 — Deterministic Verification Gate
Before any output is published or action is executed:
- Groundedness check: response must cite retrieved chunks
- Confidence score from governance engine: exact_match/related_match/weak_match/no_match
- BranchCompletenessChecker: response must include required branches (escalation path, denial path, security path where applicable)
- PII scan: response must not contain unmasked Aadhaar, PAN, or full phone numbers

If any check fails: case transitions to ESCALATED, Transfer Context Payload compiled.

---

## 5. Slot Filling and Clarification

When a workflow step requires a parameter that was not present in the ticket, the case enters AWAITING_INPUT state.

### Mandatory Slots Per Topic

| Topic | Required Slots |
|-------|---------------|
| `VKYC_Session_Failure` (link regen) | `session_id`, `phone_number` |
| `OTP_Delivery_Failure` (resend) | `phone_number`, `channel` (SMS/email/voice) |
| `Document_OCR_Failure` (re-check) | `document_type`, `application_id` |
| `Agent_Portal_Issue` (unlock) | `agent_id`, `portal_type` |
| `API_Callback_Failure` (retry) | `callback_type`, `application_id` |

### Slot State Storage
Partial slot state is stored in Redis with a 30-minute TTL:
```json
{
  "case_id": "TKT-88129",
  "topic": "VKYC_Session_Failure",
  "required_slots": ["session_id", "phone_number"],
  "filled_slots": {"phone_number": "XXXXXX1234"},
  "pending_slots": ["session_id"],
  "clarification_sent_at": "2026-06-01T10:00:00Z"
}
```

### Clarification Rules
- One question per turn. Never ask for multiple slots in a single message.
- Question is specific: "Please provide the KwikID session ID for this case (format: KID-XXXXXXXX)."
- On response, validate before accepting:
  - Indian mobile number: `r"^[6-9]\d{9}$"`
  - KwikID session ID: `r"^KID-[A-Z0-9]{8}$"`
  - Application ID: `r"^APP-\d{8}$"`

### Slot Validation Failure
If the customer provides a value that fails validation after 2 attempts:
- Case transitions to ESCALATED
- Reason: `"slot_validation_failed_after_retry"`
- Human agent receives the partial slot state in Transfer Context Payload so they do not need to re-ask

On 30-minute Redis TTL expiry with unfilled slots: case transitions to ESCALATED with reason `"slot_fill_timeout"`.
