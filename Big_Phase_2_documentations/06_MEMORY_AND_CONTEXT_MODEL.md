# 06 — Memory and Context Model

## 1. Five-Tier Memory Taxonomy

```
+-----------------------------------------------------------------------------------+
| TIER 5 — Organizational Memory                                                    |
| SOPs, API contracts, compliance rules, product documentation                      |
| Location: pgvector DB (sop_chunks, knowledge_chunks tables)                       |
| Access: Phase 1 RAG retrieval (read-only from production system)                  |
| Write: human SOP authors → Phase 1 ingestion pipeline                             |
| TTL: indefinite (version-controlled via ingestion pipeline)                       |
| PII: none (organizational knowledge only)                                         |
+-----------------------------------------------------------------------------------+
| TIER 4 — Customer Memory (Zero-Copy)                                              |
| Account state, onboarding stage, Aadhaar/PAN verification status, VKYC history    |
| Location: bank's CRM / CBS (never in KwikID system)                               |
| Access: read-only API call at case initiation; result not stored locally           |
| Write: NOT PERMITTED from KwikID system                                            |
| TTL: not applicable (never stored)                                                 |
| PII: full PII present in source; masked before use (Aadhaar first 8, PAN last 4)  |
+-----------------------------------------------------------------------------------+
| TIER 3 — Case Memory (Session Checkpoint)                                         |
| Active workflow variables: session_id, slot_state, failure_code, remediation_log  |
| Location: Redis (hot, TTL 30min active / 15min post-resolution) + PostgreSQL Saga |
| Access: workflow engine reads/writes; audit log writes on transition               |
| Write: workflow engine only (via case checkpoint service)                          |
| TTL: Redis evicted 15min after resolution; PostgreSQL kept for audit (indefinite)  |
| PII: slot values masked (phone last-4, Aadhaar replaced with token)                |
+-----------------------------------------------------------------------------------+
| TIER 2 — Short-Term Memory (Ephemeral Context Window)                             |
| Last 5-10 conversation turns in LLM context                                        |
| Location: in-memory, current request context only                                  |
| Access: LLM reasoning layer (read); discarded after response                       |
| Write: never persisted                                                              |
| TTL: discarded on session end / response generation                                |
| PII: masked before entering context (Aadhaar, phone, PAN all masked)              |
+-----------------------------------------------------------------------------------+
| TIER 1 — Learned Memory (Offline Tuning)                                          |
| Corrected resolution patterns, new SOP examples, threshold calibrations            |
| Location: sop_suggestions table (staging), ingested via Phase 1 pipeline           |
| Access: human administrator review required before any injection                   |
| Write: offline batch job only; never from production traffic in real-time          |
| TTL: sop_suggestions held indefinitely until approved/rejected                     |
| PII: stripped before staging                                                       |
+-----------------------------------------------------------------------------------+
```

---

## 2. What Is Not in LLM Context

These are hard constraints, not guidelines. Violation of any of these is a compliance event.

**Aadhaar number:** Raw Aadhaar XML is never in the context window. OCR-extracted Aadhaar digits are replaced with a tokenized reference (`AADHAAR_TOKEN_<hash>`) before the payload reaches the LLM layer. The first 8 digits are masked; only the last 4 may appear in human-readable output, and only when operationally necessary.

**Phone numbers:** Masked to last 4 digits in all LLM context and all output. Format in context: `XXXXXX1234`. Full phone number is held only in slot_state in Redis (encrypted at rest); it is passed directly to tool calls as a parameter without entering the LLM context string.

**PAN numbers:** Masked to last 4 characters in LLM context. `ABCDE1234F` becomes `XXXXXX234F`. Full PAN is not held in any KwikID store — it is read from the CRM zero-copy API call and used only to confirm a match result (boolean), not to cache the value.

**Customer CRM data:** Fetched via zero-copy API read at case initiation. The result object is not embedded in the LLM prompt directly. Specific fields are extracted (e.g., `onboarding_stage`, `vkyc_attempts_remaining`) and included as structured metadata, never as a raw CRM dump.

**Conversation history:** Maximum 5 turns in the context window. Never vectorized and stored in a persistent embedding store. If the conversation is longer than 5 turns, earlier turns are dropped (not summarized into a persistent memory store). The Case memory (Tier 3) stores the workflow state; the conversation turns are not the state.

**Session tokens and API keys:** Never logged, never in LLM context, never in the case checkpoint. If a session token appears in ticket text (e.g., customer pasted it), it is redacted by the PII masking pipeline before any processing.

---

## 3. Case Memory — The New Addition in Phase 2

Case memory is the primary new memory component in Phase 2. It does not exist in Phase 1 (Phase 1 is stateless per request).

### Purpose
Store the active state of a multi-step support transaction so that:
- The workflow engine can resume after a container restart
- A human agent can receive full context on escalation without re-reading the transcript
- The compliance audit trail is complete and queryable

### Case Checkpoint Schema

```sql
CREATE TABLE case_checkpoints (
  checkpoint_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  case_id           TEXT NOT NULL,
  ticket_id         TEXT NOT NULL,          -- Freshdesk ticket ID
  client            TEXT NOT NULL,          -- tenant/bank identifier
  created_at        TIMESTAMPTZ DEFAULT NOW(),
  updated_at        TIMESTAMPTZ DEFAULT NOW(),
  state             TEXT NOT NULL,          -- current Case State Machine state
  topic             TEXT,                   -- classified topic
  topic_confidence  FLOAT,                  -- classifier confidence score
  slot_state        JSONB,                  -- extracted parameters (PII masked)
  remediation_log   JSONB[],               -- array of attempted actions and outcomes
  failure_code      TEXT,                   -- last known error code (e.g., ERR_JITTER_LOW_BANDWIDTH)
  escalation_reason TEXT,                   -- why case was escalated (if applicable)
  transfer_payload  JSONB,                  -- serialized Transfer Context Payload
  resolved_at       TIMESTAMPTZ,
  outcome           TEXT                    -- RESOLVED / ESCALATED / DEAD_LETTER
);

CREATE INDEX idx_case_checkpoints_ticket_id ON case_checkpoints(ticket_id);
CREATE INDEX idx_case_checkpoints_state ON case_checkpoints(state) WHERE resolved_at IS NULL;
CREATE INDEX idx_case_checkpoints_client ON case_checkpoints(client);
```

### TTL and Retention Policy
- **Redis hot cache:** evicted 15 minutes after `resolved_at` is set, or 15 minutes after `outcome` is written. During active workflow: refreshed on every write.
- **Redis slot state:** TTL 30 minutes from last activity (slot fill, clarification exchange). On expiry, case transitions to ESCALATED.
- **PostgreSQL Saga table:** kept indefinitely for compliance audit. No TTL. Can be archived to cold storage after 12 months per data retention policy.

### Masked Fields in slot_state
The `slot_state` JSONB field stores parameter values used by the workflow. PII fields are masked before writing:

```json
{
  "session_id": "KID-AB12CD34",
  "phone_masked": "XXXXXX1234",
  "application_id": "APP-00129871",
  "aadhaar_token": "AADHAAR_TOKEN_a3f8b2c1",
  "pan_last4": "234F",
  "otp_channel": "SMS",
  "otp_attempt_count": 2
}
```

Full phone and Aadhaar values are not stored in this table. They exist only in the encrypted Redis slot state during active workflow execution.

---

## 4. What the Earlier Memory Design Got Wrong

An earlier draft of the Phase 2 architecture proposed:
- **Episodic memory:** per-ticket interaction vectors stored in pgvector for 90 days
- **Semantic memory:** customer profile embeddings derived from interaction history stored for 365 days
- **Preference memory:** learned patterns from per-customer interaction history

These proposals are incorrect and are rejected. Here is why:

### Reason 1 — Compliance (DPDP Act, RBI V-CIP)
Vectorizing customer interaction history creates an unstructured store of customer behavioral data. The DPDP Act requires: purpose limitation (data collected for KYC cannot be used to build behavioral profiles), storage limitation (data must be deleted when the purpose is served), and user consent for each distinct use. A 90-day interaction vector store violates all three principles. RBI V-CIP guidelines additionally require that customer biometric and identity data not be used beyond the V-CIP transaction.

### Reason 2 — Retrieval Quality Degradation
Injecting 90-day episode history into every request context means the LLM receives semantically noisy historical turns that are less relevant than the current ticket metadata. Historical tickets about OTP delivery will contaminate context for a current VKYC liveliness issue, even though the embedding distance is non-zero. Retrieval precision degrades.

### Reason 3 — The Personalization Goal Is Already Met Correctly
The stated goal of episodic and semantic memory was "personalization" — understanding the customer's onboarding history. This goal is correctly achieved by: zero-copy CRM read (retrieves current account state at case initiation) and case checkpoint (retrieves active workflow state if the case is a continuation). Neither requires persistent vector storage.

### Reason 4 — Complexity Without Corresponding Benefit
Building and maintaining a 90-day/365-day vector memory pipeline would require: ingestion pipeline, TTL enforcement, PII audit scans on historical data, retrieval tuning, and a separate compliance review. The operational cost is high; the benefit is achievable by simpler means.

---

## 5. Learned Memory — Offline Only

The system does not learn in real-time. There are no real-time weight updates, no automatic training from feedback signals, and no automatic injection of production outcomes into retrieval.

### Why Not Real-Time Learning

**Feedback is noisy.** Freshdesk agent feedback (THUMBS_UP/THUMBS_DOWN) reflects agent preference, not ground truth correctness. A THUMBS_DOWN may mean: the answer was wrong, the answer was correct but the agent wanted to handle it differently, the agent fat-fingered, or the agent escalated out of habit. Without human review, these signals cannot be used as training labels.

**PII risk.** Production feedback includes ticket text, which may contain unmasked PII that was not caught by the masking pipeline. Injecting feedback directly into training data without a human review step risks embedding PII in the knowledge base.

**Catastrophic forgetting risk.** Fine-tuning or RAG knowledge base updates based on recent traffic can cause the model to forget correct behavior for rare but critical cases (e.g., Hard Lock resolution, Security Freeze detection). Human review of new SOP additions prevents this.

### The Correct Offline Loop

1. Human administrators review flagged cases in the weekly calibration report
2. Corrected resolution patterns are extracted from human-reviewed escalations and agent corrections
3. New SOPs or retrieval examples are authored or edited by the SOP team
4. New SOPs submitted to the Phase 1 ingestion pipeline via the standard process
5. Calibration threshold changes (confidence gate, match_threshold) are applied to configuration, reviewed, and deployed via standard release

Cadence: weekly for calibration review; monthly for SOP authoring cycle; ad-hoc for critical issue type additions (when a new recurring issue family appears in escalations).
