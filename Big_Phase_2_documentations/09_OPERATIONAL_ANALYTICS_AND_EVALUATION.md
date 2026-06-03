# 09 — Operational Analytics and Evaluation

## 1. Observability Requirements

A support automation system that cannot be observed cannot be operated. Observability is not a reporting nicety — it is the mechanism by which the system is governed, calibrated, and improved. The following telemetry is mandatory; without it, the system cannot be deployed to production.

**Per case (every case, no sampling):**
- Topic classification result (topic key, confidence, tier used)
- Retrieval match type (exact_match / related_match / weak_match / no_match)
- Generation confidence score
- Outcome (RESOLVED / ESCALATED / DEAD_LETTER)
- Escalation trigger (if applicable)
- End-to-end latency (webhook receipt to note posted)

**Per action (every reversible or irreversible action):**
- Action name and risk level
- Idempotency key
- Validation result (PASS / FAIL + fail reason)
- Execution result (SUCCESS / FAILURE + error code)
- Actor (workflow_engine / human:agent_id)

**Per escalation:**
- Escalation trigger (from the 10-trigger list in `07_GOVERNANCE_POLICY_AND_HANDOFF.md`)
- Transfer Context Payload summary (topic, confidence, attempted_remediations count, failure_code)
- Human agent who took the case (for escalation outcome tracking)

**Per SOP retrieval:**
- Query (topic-scoped, sanitized)
- Top-k chunks returned (sop_ids only, not content — content is large)
- Similarity scores (top 3)
- Match type from governance engine

All telemetry is written to `case_audit_log` in real-time. Analytics queries run against `case_audit_log` replicas or a materialized summary table (not against the primary audit table).

---

## 2. Core KPIs

| Metric | Definition | Level 1 Target | Level 2 Target | Measurement Cadence |
|--------|-----------|----------------|----------------|---------------------|
| Topic Classification Accuracy | % tickets correctly classified (labeled test set evaluation, not production ground truth) | > 85% per family | > 90% per family | Weekly (test set); monthly (spot audit) |
| Private Note Delivery Rate | % of webhook calls that produce a correct private note or escalation note within 30s | > 99% | > 99% | Real-time; daily summary |
| Confidence Distribution | % of cases in high/medium/low confidence bands, per topic family | Baseline (track weekly) | > 70% high confidence | Weekly |
| Escalation Rate | % of all cases routed to human (all triggers combined) | Track only (no target at Level 1) | < 30% | Weekly; broken down by trigger type |
| SLA Compliance | % of tickets with first note posted before SLA window closes | > 95% | > 95% | Daily; per-tenant |
| P95 Response Latency | 95th percentile time from webhook receipt to note posted (ms) | < 10,000ms | < 5,000ms | Real-time; hourly rollup |
| Automation Rate | % of cases fully handled without human review of content | 0% (Level 1 = triage only) | > 40% | Weekly |
| PII Leak Rate | % of private notes containing unmasked Aadhaar (>8 digits), PAN (>4 chars), or full phone (10 digits) | 0% | 0% | Per-note scan; daily audit |
| Circuit Breaker Trip Rate | # of times each circuit breaker opened per day | Baseline | < 2/day per service | Daily |
| Idempotency Short-Circuit Rate | % of actions that returned cached result (indicates duplicate requests) | Baseline | < 1% per action type | Daily |

### KPI Dashboard Requirements
- Real-time counters for: notes posted today, escalations today, webhook errors today
- Daily breakdown by topic family (classification distribution, escalation rate per topic)
- Weekly trend charts for all KPIs
- SLA breach alert: any ticket whose SLA window will expire in < 2 hours with no note posted must trigger an alert to the L1 queue

---

## 3. Feedback Pipeline

Freshdesk agents interact with AI-generated private notes. Their reactions are the primary feedback signal.

### Feedback Event Types

| Event | Trigger | Meaning |
|-------|---------|---------|
| `THUMBS_UP` | Agent clicks approve/helpful | Response was useful and accurate |
| `THUMBS_DOWN` | Agent clicks reject/unhelpful | Response was incorrect or unhelpful |
| `CORRECTION` | Agent adds correction note | Agent provides the correct resolution |
| `SOP_MISSING` | Agent flags no SOP exists | Knowledge gap: no SOP for this issue type |
| `SOP_WRONG` | Agent flags wrong SOP retrieved | Retrieval error: wrong SOP for this issue |
| `ESCALATION_CONFIRMED` | Human agrees AI was right to escalate | Escalation was correct |
| `ESCALATION_OVERRIDDEN` | Human says AI could have handled it | Escalation was over-triggered |

### Feedback Storage Schema

```sql
CREATE TABLE response_feedback (
  feedback_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  case_id           TEXT NOT NULL,
  ticket_id         TEXT NOT NULL,
  client            TEXT NOT NULL,
  agent_id          TEXT,             -- anonymized agent identifier
  feedback_type     TEXT NOT NULL,    -- event type from table above
  feedback_at       TIMESTAMPTZ DEFAULT NOW(),
  correction_text   TEXT,             -- populated for CORRECTION events (PII-stripped)
  classified_topic  TEXT,
  match_type        TEXT,
  classifier_confidence FLOAT,
  notes             TEXT              -- free-text from agent (optional)
);
```

### What Feedback Is Not Used For
Feedback is not used for automatic training, automatic threshold changes, or automatic knowledge base updates. It is human-reviewed in the calibration loop. See Section 4.

---

## 4. Calibration Loop

The calibration loop is a weekly human-reviewed process. It does not run automatically. A system engineer or product owner runs the calibration report, reviews the findings, and decides on changes.

### Calibration Report (Weekly)

The report surfaces four categories of findings:

**1. Confidence Miscalibration**
Query: cases where `classifier_confidence > 0.90` AND `feedback_type IN ('THUMBS_DOWN', 'SOP_WRONG', 'CORRECTION')`.
Interpretation: the system was highly confident but was wrong. This indicates the confidence score is not well-calibrated to actual accuracy. Potential fix: lower the confidence threshold for automation on the affected topic family.

**2. Retrieval Gaps**
Query: topics with `feedback_type = 'SOP_MISSING'` appearing 3+ times in the past 7 days for the same topic key.
Interpretation: a recurring issue family has no SOP coverage. Action: trigger the SOP generation process (see `05_RETRIEVAL_AND_KNOWLEDGE_LAYER.md`, Section 5).

**3. Topic Classification Failures**
Query: cases where `escalation_trigger = 'unknown_topic'` OR (`escalation_trigger = 'below_threshold'` AND `feedback_type = 'ESCALATION_OVERRIDDEN'`).
Interpretation: the classifier is missing a topic or under-confident on a known topic. Action: add Tier 1 rules, extend the labeled test set, retrain Tier 2 if needed.

**4. Threshold Drift**
Query: rolling 30-day accuracy by confidence band. If the high-confidence band (>0.9) is producing < 85% correct outcomes, the threshold should be raised.
Interpretation: as the knowledge base grows and ticket distributions shift, the optimal threshold may change. Action: review and propose new threshold value; test against labeled set before deployment.

### Calibration Change Process
1. Engineer runs calibration report, identifies findings
2. Proposed changes documented: threshold value change, new Tier 1 rules, SOP submissions
3. Changes reviewed by product owner or senior engineer
4. SOP submissions: go through human SOP review process
5. Threshold/rule changes: tested against labeled test set (accuracy must not decrease)
6. Changes deployed via standard release (not hotfix)

No calibration change is applied automatically. All changes are reviewed and deployed deliberately.

---

## 5. Evaluation — LLM-as-Judge

A background evaluation pass runs on a sampled 10% of all AI-generated responses. This is distinct from the feedback pipeline (which requires agent interaction) — it provides automated quality assessment on all responses, not only those that receive feedback.

### Evaluation Dimensions

**Groundedness**
Definition: does the generated response cite only information present in the retrieved SOP chunks? Does it not introduce claims not present in the source?

Evaluator prompt structure:
```
Given:
  RETRIEVED_CHUNKS: [list of chunk content]
  GENERATED_RESPONSE: [response text]

For each claim in GENERATED_RESPONSE:
  Is this claim supported by at least one of the RETRIEVED_CHUNKS? (yes/no)

Output: groundedness_score (0.0-1.0), ungrounded_claims (list)
```

Threshold: groundedness_score < 0.8 → flag for human review.

**Branch Completeness**
Definition: for the detected topic, are all mandatory branches present in the response?

Mandatory branches by topic:
- `OTP_Delivery_Failure`: must include escalation path (if OTP limit hit), channel switch instruction
- `VKYC_Session_Failure`: must include escalation path, bandwidth instruction, session expiry instruction
- `Document_OCR_Failure`: must include retry instruction, escalation path, manual verification alternative
- `Agent_Portal_Issue`: must include escalation path, account lock resolution, when to contact IT
- `API_Callback_Failure`: must include retry instruction, CBS contact escalation, SLA impact note

BranchCompletenessChecker: regex + semantic check for presence of each required branch. Missing branch → flag for human review.

**PII Leakage Scan**
Automated regex scan on every generated response before posting. Patterns checked:
```python
AADHAAR_PATTERN = r"\b[2-9]\d{3}\s?\d{4}\s?\d{4}\b"  # 12-digit Aadhaar format
PAN_PATTERN     = r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"         # full PAN format
MOBILE_PATTERN  = r"\b[6-9]\d{9}\b"                     # 10-digit Indian mobile
```

On PII detection: response is blocked from posting. Case transitions to ESCALATED with reason `"pii_detected_in_response"`. The detected pattern type (not the value) is logged.

### Evaluation Results Storage

```sql
CREATE TABLE evaluation_results (
  eval_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  case_id           TEXT NOT NULL,
  evaluated_at      TIMESTAMPTZ DEFAULT NOW(),
  sample_source     TEXT,    -- 'random_10pct', 'flagged', 'manual'
  groundedness_score FLOAT,
  branch_complete   BOOLEAN,
  pii_detected      BOOLEAN,
  pii_pattern_type  TEXT,    -- 'aadhaar', 'pan', 'mobile', null
  flagged_for_review BOOLEAN DEFAULT FALSE,
  reviewer_id       TEXT,
  review_outcome    TEXT     -- 'confirmed_ok', 'confirmed_issue', 'disputed'
);
```

Flagged evaluations appear in the weekly calibration report for human review.
