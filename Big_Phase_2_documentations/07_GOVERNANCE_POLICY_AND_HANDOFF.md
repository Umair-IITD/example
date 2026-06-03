# 07 — Governance, Policy, and Human Handoff

## 1. Governance Model

Governance is applied at three layers. Each layer can stop case execution and route to human. No layer can be bypassed by a workflow node or LLM output.

### Layer 1 — Intake Governance
Topic classifier confidence gate (threshold: 0.85). Unknown topics and below-threshold classifications are immediately routed to ESCALATED state. This layer executes before any LLM reasoning or retrieval.

### Layer 2 — Retrieval Governance
The Phase 1 governance engine is unchanged from Phase 1. It produces a match type score for every retrieval response:
- `exact_match`: retrieved chunks directly address the query; high confidence
- `related_match`: retrieved chunks are topically relevant but not a direct match; medium confidence
- `weak_match`: retrieved chunks are tangentially related; low confidence
- `no_match`: no sufficiently similar chunks found; confidence below retrieval threshold

`no_match` results trigger immediate escalation to human regardless of classifier confidence score.
`weak_match` results trigger a BranchCompletenessChecker: the generated response must include all required branches (escalation path, denial path, security path). If any required branch is missing, escalate.

### Layer 3 — Action Governance
Proposal/validation/execution split (see `08_TOOL_CALLING_AND_IDEMPOTENCY.md`). Action risk classification is static (compile-time). IRREVERSIBLE actions are blocked from automation and routed to human sign-off.

---

## 2. Action Risk Classification (Static, Compile-Time)

Action risk classification is defined in the tool registry at deployment time. It cannot be changed by a workflow node, LLM output, or runtime configuration. A policy change to action risk classification requires a code review and deployment.

### SAFE (Read-Only)
Execute instantly. No approval required. No audit record required (but execution is logged). Actions: fetch VKYC session status, check OTP delivery log, query service health, read ticket metadata.

No state mutation. No external write. No PII handling beyond read access.

### REVERSIBLE Side-Effect
Execute + write to case_audit_log. No human approval required. Actions: add ticket tag, update ticket priority, post Freshdesk private note, send OTP retry notification (subject to rate limit), create escalation task.

All reversible actions must have a defined compensating transaction (see `04_WORKFLOW_ENGINE.md`, Section 4).

### IRREVERSIBLE
Blocked from automation. Routed to human sign-off queue. Actions include (but are not limited to):
- Modify Aadhaar verification fields
- Update PAN verification status
- Bypass liveliness validation
- Send public-facing message to customer
- Clear OTP attempt counter
- Reset VKYC session state
- Issue customer credit or refund
- Hard Lock resolution (branch visit required per RBI; cannot be digital)

If a workflow node proposes an IRREVERSIBLE action, the action gateway rejects it immediately. The case transitions to ACTION_PENDING → ESCALATED. The proposed action (including its structured JSON intent) is included in the Transfer Context Payload so the human agent can execute it manually with appropriate authority.

---

## 3. Human Handoff Triggers

The following conditions cause immediate transition to ESCALATED state. The list is exhaustive for Level 1 and Level 2. Additional triggers may be added in Level 3 as new capabilities introduce new risk surfaces.

| # | Trigger | Condition | Notes |
|---|---------|-----------|-------|
| 1 | Classifier confidence below threshold | confidence < 0.85 | Both Tier 1 and Tier 2 misfire |
| 2 | Unknown topic | Topic not in known registry | Never open-ended LLM planning |
| 3 | Retrieval match type no_match | Phase 1 governance returns no_match | SOP gap detected |
| 4 | Low generation confidence | Generation confidence = low after BranchCompletenessChecker | Grounding failure |
| 5 | BranchCompletenessChecker failure | Required SOP branch missing from answer | Incomplete guidance |
| 6 | Consecutive workflow step failures | 3 consecutive failures in a single playbook | Execution instability |
| 7 | Explicit customer escalation | Customer text contains "speak to an agent", "escalate", "complaint", "RBI" | Customer intent |
| 8 | Fraud or security signals | Deepfake detected, multiple foreign IPs, excessive liveliness failures | Security priority |
| 9 | Compliance flag | Security Freeze active, Hard Lock state, 0-factor auth attempt, blocked entity | Regulatory |
| 10 | IRREVERSIBLE action proposed | Workflow proposes action classified IRREVERSIBLE | Action governance gate |

On any trigger: case transitions to ESCALATED state, Transfer Context Payload is compiled and posted as Freshdesk private note, ticket priority is set to High if not already higher, human queue assignment is made.

---

## 4. Transfer Context Payload

The Transfer Context Payload is compiled at the moment of ESCALATED state transition. It is posted as a Freshdesk private note tagged `ai-transfer-context`. The human agent reads this note and does not need to re-read the full transcript to understand the case state.

### Full Schema

```json
{
  "payload_version": "1.0",
  "generated_at": "2026-06-01T10:15:00Z",
  "ticket_metadata": {
    "freshdesk_id": "TKT-88129",
    "customer_id": "CUST-9921",
    "client": "bank_alpha",
    "onboarding_stage": "VKYC_LIVELINESS",
    "sla_breach_at": "2026-06-01T15:30:00Z",
    "ticket_created_at": "2026-06-01T09:00:00Z"
  },
  "diagnostic_summary": {
    "detected_intent": "VKYC_Session_Failure",
    "sub_intent": "VKYC_Liveliness_Command_Failure",
    "classifier_confidence": 0.91,
    "retrieval_match_type": "related_match",
    "root_cause_analysis": "Customer failed liveliness validation. Session telemetry shows sub-300kbps bandwidth jitter during command sequence. Low bandwidth mode was activated but liveliness still failed after retry.",
    "attempted_remediations": [
      {"action": "toggle_low_bandwidth_mode", "outcome": "success", "timestamp": "2026-06-01T10:05:00Z"},
      {"action": "retry_liveliness_check", "outcome": "failure", "error_code": "ERR_JITTER_LOW_BANDWIDTH", "timestamp": "2026-06-01T10:08:00Z"}
    ],
    "escalation_trigger": "consecutive_workflow_failures_3"
  },
  "serialized_case_state": {
    "workflow_state": "WORKFLOW_ACTIVE",
    "playbook_id": "VKYC_Bandwidth_Failure_Triage",
    "playbook_step": "Re_Evaluate_Liveliness",
    "aadhaar_xml_verified": true,
    "pan_ocr_verified": true,
    "vkyc_attempts_used": 2,
    "vkyc_attempts_remaining": 1,
    "current_failure_code": "ERR_JITTER_LOW_BANDWIDTH",
    "slot_state": {
      "session_id": "KID-AB12CD34",
      "phone_masked": "XXXXXX1234",
      "otp_channel": "SMS"
    }
  },
  "cited_sop_ids": ["sop_vkyc_bw_001", "sop_vkyc_liveliness_002"],
  "recommended_action": "Advise customer to switch to 4G or Wi-Fi network and retry VKYC session. If failure persists after network switch, schedule a callback within SLA window. Note: 1 VKYC attempt remaining — do not exhaust without customer confirmation.",
  "pii_masked": true
}
```

### Payload Posting Rules
- Posted as Freshdesk private note (never a public reply)
- Tags applied: `ai-transfer-context`, `ai-escalated`
- Priority set to: High (unless already Urgent, in which case Urgent is kept)
- Group assignment: L1 Human Queue (default) or specialized queue based on escalation trigger (Fraud → Security queue; Compliance flag → Compliance queue)
- `pii_masked: true` field must be verified by audit scan before posting (see `09_OPERATIONAL_ANALYTICS_AND_EVALUATION.md`)

---

## 5. Regulatory Compliance — RBI V-CIP

KwikID is an RBI-regulated Video-based Customer Identification Process (V-CIP) solution. The following requirements are mandatory and non-negotiable:

### Security Anomaly Reporting
Any security anomaly must be logged to an immutable security audit table AND reported as a cyber event to the bank's SOC. The standard `case_audit_log` table is NOT sufficient for security events. Security anomaly types include:
- `DEEPFAKE_DETECTED`: AI model flags potential deepfake during VKYC
- `FOREIGN_IP_CONNECTION`: Customer or agent connecting from non-Indian IP during V-CIP
- `EXCESSIVE_LIVELINESS_FAILURE`: Liveliness commands failed more times than statistically expected for genuine customers
- `MULTI_DEVICE_SAME_SESSION`: Multiple devices accessing the same session ID simultaneously
- `SECURITY_FREEZE_BYPASS_ATTEMPT`: System or user attempting to proceed past a Security Freeze flag

### Biometric Data Constraints
- Aadhaar: first 8 digits must be masked before any storage, any log write, any LLM context injection. This is a statutory requirement under the Aadhaar (Targeting of Subsidies, Benefits and Services) Act, 2016 and UIDAI regulations.
- Face biometric data: never stored in KwikID system. Liveliness result (PASS/FAIL + confidence score) is stored; the biometric image or encoding is not.
- The V-CIP session recording is stored by the bank as required by RBI; KwikID does not duplicate it.

### Session Token Security
- Session tokens are never logged in any audit table (session_id is a case identifier, not a security token)
- OTP values are never logged
- API authentication tokens are never in case memory or audit logs

---

## 6. Audit Trail Design

Two separate audit tables serve distinct purposes. Using the wrong table for the wrong event type is an operational error.

### Table 1: case_audit_log

```sql
CREATE TABLE case_audit_log (
  audit_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  case_id         TEXT NOT NULL,
  ticket_id       TEXT NOT NULL,
  client          TEXT NOT NULL,
  event_timestamp TIMESTAMPTZ DEFAULT NOW(),
  actor           TEXT NOT NULL,     -- 'system', 'workflow_engine', 'human:agent_id'
  action_type     TEXT NOT NULL,     -- 'STATE_TRANSITION', 'ACTION_PROPOSED',
                                     -- 'ACTION_EXECUTED', 'ACTION_REJECTED',
                                     -- 'ESCALATION_TRIGGERED', 'NOTE_POSTED',
                                     -- 'RAG_CALLED', 'SLOT_FILLED'
  action_detail   JSONB,             -- sanitized action parameters (no PII)
  outcome         TEXT,              -- 'SUCCESS', 'FAILURE', 'REJECTED', 'PENDING'
  error_code      TEXT,
  idempotency_key TEXT               -- for action events
);

-- Append-only enforcement: no UPDATE or DELETE permitted
CREATE RULE no_update_case_audit AS ON UPDATE TO case_audit_log DO INSTEAD NOTHING;
CREATE RULE no_delete_case_audit AS ON DELETE TO case_audit_log DO INSTEAD NOTHING;
```

**Who writes:** case checkpoint service (state transitions), action gateway (action events), RAG service (retrieval events), private note publisher (note events).

**Who reads:** observability dashboard, calibration report generator, incident investigation.

### Table 2: security_compliance_audit

```sql
CREATE TABLE security_compliance_audit (
  event_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_timestamp TIMESTAMPTZ DEFAULT NOW() NOT NULL,
  tenant_id       UUID NOT NULL,
  session_id      TEXT NOT NULL,     -- KwikID session ID only, not token
  source_ip       INET,              -- masked to /24 prefix for privacy
  event_type      TEXT NOT NULL,     -- see event type list above
  severity        TEXT NOT NULL,     -- 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'
  details         JSONB NOT NULL,    -- event-specific details (no raw PII)
  soc_reported    BOOLEAN DEFAULT FALSE,
  soc_reported_at TIMESTAMPTZ
) WITH (security_barrier = true);

-- SECURITY DEFINER: only authorized security service role can insert
-- No DELETE policy enforced at database level
ALTER TABLE security_compliance_audit ENABLE ROW LEVEL SECURITY;
CREATE POLICY security_audit_insert_only ON security_compliance_audit
  FOR INSERT TO security_service_role
  WITH CHECK (true);
-- No SELECT, UPDATE, DELETE policies for application roles
```

**Who writes:** VKYC liveliness service (deepfake flags), session manager (foreign IP, multi-device), compliance checker (Security Freeze, Hard Lock events).

**Who reads:** SOC system (real-time replication), compliance officer (quarterly audit), RBI audit interface.

**SOC Routing:** A separate async job polls `soc_reported = FALSE` rows at 30-second intervals and forwards them to the bank's SOC notification endpoint. On successful delivery, `soc_reported_at` is set. This job runs with a dedicated database role that has SELECT + UPDATE on `soc_reported` only.
