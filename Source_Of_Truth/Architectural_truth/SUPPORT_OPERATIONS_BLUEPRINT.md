# SUPPORT_OPERATIONS_BLUEPRINT.md

# Enterprise Support Agent — Business Truth & System Blueprint

Version: 1.5
Status: Approved Architecture (Updated: L1 Investigation-Only, Action Execution Reserved, Wave 8 Asana L2 Escalation, Multi-Tenant Log Platform integration corrected and specified, L2 Asana Resolution Loop specified — §§5,7-13,20B,21,27,33-35)
Owner: Support Automation Program

**v1.5 note**: Adds new §20B, documenting the Asana webhook resolution-loop
receiver built this sprint (uncertified — awaiting Claude Code sprint-cert
review; see `.remember/remember.md` "HANDOFF FROM PARALLEL COWORK SESSION"
for the full diff list). §20A previously documented only task *creation*;
§20B covers the other direction — detecting when engineering marks the
Asana task complete and closing the loop back to Freshdesk. As of this
revision the loop closes only as far as an internal verification note;
auto-reply-to-customer and auto-close are explicitly NOT yet wired pending
confirmation of the exact `cf_sop_status` / `cf_resolution_classification`
values (see §20B for the proposed mapping and why it isn't live yet). §23's
"Engineering Workflow" diagram is unchanged in spirit but §20B is now the
concrete implementation of its "Resolution Event → Freshdesk Update" step.

**v1.4 note**: This revision corrects a drift discovered during live
integration testing — the Admin Portal (§9) does not and cannot provide
backend logs; logs are sourced exclusively from the Multi-Tenant Log
Platform described in new §34, with a mandatory relevance-extraction and
PII-redaction stage specified in new §35. Section numbers 1-33 are
preserved from v1.3 for continuity with any existing "Blueprint §N"
references elsewhere in the codebase; all changes within them are content
corrections/additions in place. §34 and §35 are net new. See
`Loki_Log_Tool_Blueprint/Unity_Loki_Integration_Findings.md` and
`Source_Of_Truth/Architectural_truth/PENDING_SOT_UPDATES_Loki_Integration.md`
for the full research trail behind this revision.

Purpose: This document is the single source of truth for how the Enterprise Support Agent operates, how support tickets flow through the system, how investigation is performed, how actions are executed, how escalation occurs, and how tickets are ultimately resolved and closed.

All future development must align with this document and the approved flow_diagram.mermaid architecture.

---

# 1. Mission

The objective of the system is to automate the responsibilities currently performed by Support L1 and Support L2 teams while preserving enterprise-grade safety, auditability, approval controls, recovery mechanisms, and compliance requirements.

The system must:

* Understand incoming support tickets (NLU).
* Clarify vague user queries.
* Investigate the issue.
* Identify root cause.
* Execute approved resolutions.
* Generate internal observations (NLG).
* Escalate to engineering when required.
* Respond to users (NLG).
* Close tickets.

The system must never perform irreversible actions without appropriate safeguards.

---

# 2. Human Support Process (Current Reality)

This section describes the actual business process currently followed by support teams.

## L1 Responsibilities

L1 acts as the first line of support.

Responsibilities:

1. Read incoming ticket.
2. Understand user problem.
3. Clarify missing information (URN / Session ID).
4. Locate relevant session.
5. Investigate logs.
6. Investigate session summary.
7. Investigate videos.
8. Determine root cause.
9. Record findings in internal notes.
10. Escalate to L2 if required.

Primary Goal:
Understand what happened and why it happened. L1 DOES NOT resolve the issue immediately. L1 investigates, collects evidence, and writes observations.

---

## L2 Responsibilities

L2 acts as the escalation and engineering bridge.

Responsibilities:

1. Review L1 observations.
2. Validate findings.
3. Determine if engineering intervention is required.
4. Create Asana ticket.
5. Coordinate with development team.
6. Track resolution.
7. Update Freshdesk notes.
8. Approve closure.
9. Ensure customer response is sent.

Primary Goal:
Coordinate resolution and ticket completion.

---

# 3. Future Automated System

The system automates both L1 and L2 workflows using a strict two-phase LLM architecture: NLU for understanding, NLG for generation.

Target Flow:

```
User Message
↓
[NLU] Semantic Routing & Intent Extraction
↓
Topic Classification
↓
Slot Extraction (URN/Session ID)
↓
Clarification (If URN/Session ID missing)
↓
Investigation (Logs, Summary, APIs)
↓
Reasoning (LLM analyzes Evidence + SOPs)
↓
[NLG] Observation Generation (Internal L1 Note posted to Freshdesk)
↓
L2 Check (Does this require Engineering Intervention?)
↓ Yes                                    ↓ No
Asana Task Creation                  [NLG] Customer Reply Generation
↓                                         ↓
Update cf_asana_ticket_link          Ticket Closure
↓
[NLG] Customer Reply (Escalation Acknowledgement)
↓
Ticket Closure
```

**Critical Boundary:**
L1 ends at Observation Generation. L1 does NOT execute remediation actions
(session resets, OTP resends, OCR retries). Failed VKYC sessions are dead
sessions — the Action Execution layer is reserved for future safe actions.
The Action Gateway remains in the codebase but no L1 playbook routes to it.

---

# 4. Core Architecture

The approved architecture is present in the root directory, and is defined in:

flow_diagram.mermaid

That diagram is the canonical architectural blueprint.

No future implementation should violate its boundaries.

---

# 5. System Layers

## Layer 1 — Ticket Ingestion

Purpose:

Receive support requests.

Sources:

* Freshdesk
* Email
* Future chat channels

Responsibilities:

* Create Case
* Assign Case ID
* Persist metadata
* Trigger processing pipeline

Output:

Case Object

---

## Layer 1.5 — Client Resolution & Tenant Context

Purpose:

Determine which client environment the user belongs to before any investigation begins.

The system operates in a multi-tenant architecture.

Each client has:

* Separate Support Admin Portal
* Separate API credentials
* Separate operational data
* Separate configuration

Examples:
```
mrunali.gaikwad@unitybank.co.in
→ UNITY_BANK

agent@bobbank.in
→ BANK_OF_BARODA

officer@centralbank.co.in
→ CENTRAL_BANK
```

### Resolution Process:

1. Receive Freshdesk ticket.
2. Extract sender email.
3. Extract email domain.
4. Lookup Tenant Registry.
5. Resolve tenant.
6. Build Tenant Context.
7. Attach Tenant Context to Case.
8. Continue workflow.

Output:
```
Tenant Context
```

Contains:
```
tenant_id
tenant_name
portal_configuration
api_credentials_reference
log_datasource_reference
enabled_tools
workflow_overrides
```

`api_credentials_reference` and `log_datasource_reference` are deliberately
separate fields, not one combined credential. Each tenant has two
independent credentialed systems: the Admin Portal (session lookup and
metadata — see §9) and the Multi-Tenant Log Platform (backend logs — see
§34). A tenant may have working Admin Portal credentials and broken/missing
Log Platform credentials, or vice versa — the system must be able to
represent and report on that partial state rather than assuming one
credential set implies the other.

### Failure Handling

If tenant cannot be resolved:
```
UNKNOWN_TENANT
```

System must:
* Stop automation
* Create audit event
* route to human review

Automation must never continue with an unresolved tenant

---

## Layer 2 — Case Engine

Purpose:

Manage lifecycle of support cases.

Responsibilities:

* Case creation
* State transitions
* Slot state management
* Workflow tracking
* Audit integration

States include:

* CREATED
* TRIAGE_COMPLETE
* WORKFLOW_ACTIVE
* AWAITING_INPUT
* ACTION_PENDING
* RESOLVED
* ESCALATED
* CLOSED

---

## Layer 2.5 — Natural Language Understanding (NLU) Layer

**Status: Updated to use OpenAI LLM for Semantic Routing.**

Purpose:

Transform raw ticket text into structured signals that downstream layers can operate on deterministically. The NLU layer bridges the gap between unstructured human language and the structured domain model.

Implementation:

* **LLM Semantic Router**: Replaces all pattern-matching and regex logic. Uses OpenAI API (GPT-4o-mini or similar) with structured JSON output.
* **Ontology-Driven**: The LLM is provided with an Ontology file (`ontology.json`) containing Canonical Intents, Nested Cases, and Required Slots.
* Feeds directly into `CaseService.classify_case()` and `CaseService.receive_message()`.

Responsibilities:

* **Intent Recognition** — detect the primary intent from natural language (e.g. "I'm not getting my OTP" → `OTP_DELIVERY_FAILURE_INTENT`).
* **Entity Extraction** — surface domain entities from free-form text (phone numbers, URNs, session IDs, channel mentions, document types).
* **Negation Handling** — correctly parse negation-first phrasing ("not receiving", "haven't gotten", "unable to get") that simple keyword matching misses.
* **Signal Packaging** — produce a strict JSON `NLPSignal` payload consumed by Layer 3 (Classifier) and Layer 5 (Slot Extractor).

Critical Requirement:

The NLU layer must handle natural-language negation via LLM understanding. Customers write "I am not receiving OTP" not "OTP not received" — the NLU layer must normalize both to the same intent with confidence ≥ 0.85 before handing to the classifier.

Output:

```json
{
  "intent": "string",
  "nested_case": "string | null",
  "entities": {
    "urn": "string | null",
    "session_id": "string | null",
    "phone_number": "string | null"
  },
  "negation_detected": "boolean",
  "confidence": "float",
  "needs_clarification": "boolean",
  "clarification_question": "string | null",
  "raw_text": "string"
}
```

---

## Layer 3 — Topic Classification

Purpose:

Determine what problem category the ticket belongs to based on the `NLPSignal.intent`.

Input: NLPSignal from Layer 2.5

Examples:

* OTP Delivery Failure
* VKYC Session Failure
* OCR Failure
* Agent Portal Issue
* API Callback Failure

Output:

Topic Classification
Confidence Score

---

## Layer 4 — Workflow Selection

Purpose:

Choose the correct playbook based on Topic and Tenant Context.

Input:

* Topic
* Context
* Metadata

Output:

Workflow Playbook

---

## Layer 5 — Slot Extraction

Purpose:

Extract required information required for investigation from the `NLPSignal.entities`.

Examples:

* URN
* Session ID
* Application ID
* Phone Number
* Channel
* Document Type

Output:

Slot State

---

## Layer 6 — Clarification Engine

Purpose: Collect missing information REQUIRED FOR INVESTIGATION.

Critical Rule:
If URN or Session ID is missing, the system MUST ask for them. The system MUST NOT ask for customer details (like mobile number) to attempt a resolution prematurely. L1's job is to investigate the portal/logs, which strictly requires URN/Session ID.

Example:

User says:
"Video KYC failed."

Missing:
* URN
* Session ID

System sends:
"Please share your URN and Session ID so I can investigate the logs."

Clarification loop continues until required slots for investigation are available.

---

# 6. Knowledge Layer

Purpose:

Provide domain knowledge.

Components:

## SOP Repository

Contains:

* Support SOPs
* Resolution procedures
* Escalation rules

## Knowledge Base

Contains:

* Historical fixes
* FAQs
* Engineering guidance

## Workflow Playbooks

Contains:

* Approved automation logic
* Investigation steps
* Resolution paths

## Intent & Slot Ontology (`ontology.json`)

Contains:

* Dictionary of Canonical Intents
* Nested Cases
* Required Slots for Investigation

---

# 7. Investigation Layer

This is the most important business layer.

This layer automates L1 investigation.

---

## Investigation Objective

Determine:

What happened?
Why did it happen?
Can it be fixed automatically?
Should it be escalated?

---

## Scope Boundary

KwikID is a third-party Video KYC platform vendor — not the bank, and not
the customer. "Support" in this system means platform/technical issues
only. It does NOT mean KYC business decisions.

Concretely:

* A session rejected for a business reason (document mismatch, fraud
  suspicion, customer ineligibility, or an auditor feedback reason such as
  "Any Other Non-Technical Issues") is **out of scope** — that is the
  bank's/auditor's business call, not a platform failure.
* A session rejected where the auditor's own feedback text describes a
  platform/technical problem (audio/video unavailable, connection dropped,
  upload failure, portal error) **is in scope** — this is exactly what
  investigation, reasoning, and escalation should catch.
* `session_status` alone cannot reliably distinguish these two cases (see
  §11) — the auditor's own feedback/result text must be inspected, and even
  then only by identifying technical-issue language, not by treating every
  rejection as equally investigable.

This boundary governs §11 (Summary Analysis), §13 (Reasoning Engine), and
§21 (Escalation Logic): none of those layers should treat a business-reason
rejection as a platform failure requiring investigation or escalation.

---

## Critical Principle

Investigation MUST occur before any Action Proposal. The system must use the Tenant API Router, fetch logs, fetch session summaries, and pass this evidence to the Reasoning Engine BEFORE proposing a fix.

---

## Tenant-Aware Investigation

Every investigation must execute within the resolved Tenant Context.

All Support Admin API calls must be routed through:
```
Tenant API Router
```

Direct access to client portals is prohibited.

This guarantees:

* client isolation
* credential isolation
* safe onboarding of new clients

---

## Investigation Inputs

* URN
* Session ID
* Application ID
* Logs
* Summary
* Audit data
* Ticket content

---

# 8. URN / Phone Number Lookup Process

When Session ID is unavailable.

**Important per-tenant caveat, confirmed for Unity Bank by direct API
testing**: Unity's Admin Portal has no URN concept at all. Its only
customer identifiers are `phone_number` (10-digit) and `session_id`. If a
ticket references a "URN," the system must normalize it to `phone_number`
or `session_id` before this lookup can run — URN is not a field Unity's
portal can be queried on. Whether other tenants' admin portals genuinely
expose a URN field is unconfirmed per-tenant and must not be assumed from
Unity's model; check each tenant's own discovery document
(`Source_Of_Truth/{Tenant}_discovery/`) before relying on URN lookup there.

Process:

1. Receive URN (or normalize to phone_number if the tenant has no URN
   concept, per the caveat above).
2. Search Admin Portal (for Unity: `GET /api/v1/getAllUserSession/{domain}/{phone_number}`).
3. Retrieve user profile.
4. Retrieve user sessions.
5. Locate candidate session.
6. Match:

* Ticket timestamp
* Session timestamp
* Error notes
* Recent activity

Output:

Most probable session.
Confidence score.

---

# 9. Session Lookup Process

When Session ID exists.

**Corrected understanding (previously the Admin Portal was assumed to be
the source for backend logs — it is not).** The Admin Portal (Unity:
`GET /v1/session/get_details/{session_id}`) returns rich session evidence —
status, timeline, auditor decision, docs/QnA, summary — but contains no
backend/infrastructure logs whatsoever. There is no log endpoint on the
Admin Portal. Backend logs live on the platform's own server infrastructure
(Teleport) and are only reachable through the separate Multi-Tenant Log
Platform described in §34. This is a two-phase process across two
independent systems:

```
Phase A — Admin Portal (one call, per tenant credentials):
  1. Open session / get_details(session_id).
  2. -> session_status, timeline {start_time, end_time, vkyc_start_time,
        init_time, last_active_timestamp, ...}, auditor {result, feedback},
        summary, audit trail.
     Not every session status populates every timeline field (e.g. a
     session abandoned before video start may have init_time but no
     vkyc_start_time/end_time) — fall back through
     start_time -> vkyc_start_time -> init_time, and
     end_time -> last_active_timestamp -> start_time.

Phase B — Multi-Tenant Log Platform (separate call, uses Phase A's timeline):
  3. Fetch backend logs for [timeline.start_time, timeline.end_time] (+ a
     buffer, since client and server clocks are not perfectly aligned).
  4. -> raw merged log text. This can be large (500+ lines / several
     hundred KB for a single session — see §35) and must never be handed
     directly to the Reasoning Engine.
  5. -> extract only the log lines relevant to the specific issue described
     in the ticket (§35) before this evidence proceeds further.

Phase C — Video (future capability, see §12):
  6. Locate (near-term) and eventually analyze (future) the recorded
     session video.
```

Output:

Investigation context (Admin Portal evidence + curated, PII-redacted log
excerpt + video reference where available).

**Retention/availability caveat**: log platform retention is finite (on the
order of days, not months) and can be shortened further by ad-hoc
operational events (e.g. disk-space clearing) independent of the nominal
retention policy — confirmed by re-querying the exact same session_id and
time window that previously returned real log data and later returned
none. An empty result from Phase B is therefore **ambiguous**, not
conclusive: it can mean genuinely no backend activity occurred, or that the
data has already aged out. The system must not silently conclude "no
technical issue found" from an empty log pull alone — this ambiguity must
be surfaced in the generated observation (§14) and factored into escalation
logic (§21).

---

# 10. Logs Analysis Process

Logs are primary root-cause evidence. Logs are sourced exclusively from the
Multi-Tenant Log Platform (§34), never from the Admin Portal (see §9).

**Observed real log taxonomy** (confirmed against live sessions, one
tenant): organized by `service_name` (e.g. `userapi`, `agentapi`, `nginx`,
a video-processing celery worker, a notification service — the full set is
tenant-specific and should be treated as extensible, not fixed) and
`detected_level` (`info` / `warn` / `error` / `debug` / `unknown`), with two
structurally different line shapes: (a) structured client-telemetry events
carrying a named event code (e.g. an audio-track-muted event, a
room-disconnected event, a KYC-request-rejected event), and (b)
conventional application logger lines (INFO/ERROR with a module tag).
Earlier example event names in this section (GENERATE_OTP, SEND_SMS,
PAN_VALIDATION, etc.) were illustrative, not confirmed against real data —
treat any such list as a hypothesis to validate per-tenant against real log
samples, not an assumed taxonomy.

System must identify:

* Failures
* Timeouts
* Validation errors
* Callback failures
* Network issues
* Recurring, session-agnostic platform bugs that are NOT the ticket's root
  cause (e.g. a routine ingress rule blocking event-tracking pings on many
  sessions) — these should be recognized and labeled as known/recurring
  rather than mistaken for the specific issue under investigation.

**Mandatory intermediate step — Log Relevance Extraction (§35)**: a real
session's raw log pull can be large (500+ lines, several hundred KB, with
individual lines occasionally reaching tens of thousands of characters when
they embed full client-side payloads). This must never be passed to the
Reasoning Engine directly — both for cost/attention-dilution reasons and
because raw log lines can carry unredacted PII (§27). A relevance
extraction step, detailed in §35, must run between raw log retrieval and
Root Cause Candidate generation.

Output:

Root Cause Candidates, derived from the curated (relevance-extracted,
PII-redacted) log excerpt — never from the raw pull.

---

# 11. Summary Analysis Process

Session summary contains:

* Face Match
* Signature Match
* PAN Match
* Aadhaar Match
* Question Answers
* VKYC Outcome

System evaluates:

* Failed checks
* Confidence scores
* Rejection reasons

**Critical reliability caveat, confirmed against real session data**:
`session_status` alone must never be treated as the final outcome. A real
session has been observed where `session_status` reported success even
though the auditor's own review overrode the outcome to a rejection, with
the auditor's feedback text explicitly describing a technical/platform
problem. `auditor.result` / `auditor.feedback` (or the tenant-equivalent
fields) must always be independently inspected, regardless of what
`session_status` reports, since the two can disagree. This is the primary
mechanism for applying the Scope Boundary (§7): a rejection is only
platform-in-scope if the auditor's own feedback text indicates a
technical/platform cause, independent of whatever `session_status` says.

Output:

Summary Evidence

---

# 12. Video Analysis Process

Split into two sub-capabilities with different readiness.

## 12a. Video Location — Near-Term, No New Infrastructure Required

Recording file locations have been found to already be directly recoverable
from ordinary backend log lines pulled via the Multi-Tenant Log Platform
(§34/§35) — object-storage keys following a per-tenant but consistent
pattern appear in routine upload-path log lines. This means "locate the
recording for this session" falls out of the same log-extraction pipeline
already required for §10 — no separate capability is needed to find a
video, only to analyze one.

## 12b. Video Content Analysis — Future Capability

Purpose:

Review recorded KYC journey content.

Examples:

* Camera issues
* Audio issues
* Blank screen
* Blurry image
* Liveness failures

This genuinely requires a vision/audio-capable model and remains a future
capability, unchanged from prior scope.

Output:

Video Evidence

---

# 13. Reasoning Engine (Intelligence Layer)

Purpose:

Convert evidence + knowledge into conclusions.

Input:

* Ticket
* Curated Log Excerpt (PII-redacted, ticket-relevant — see §35; raw log
  text must never reach this layer directly, per §27)
* Summary
* Tool results
* SOP knowledge

Output:

Root Cause
Confidence
Recommended Action
Recommended Escalation

---

# 14. Natural Language Generation (NLG) Layer — Observation Generator

This replaces L1 notes.

Generated note format:

Issue Summary
Observed Evidence
Root Cause
Recommended Action
Escalation Required

Example:

"User experienced OTP delivery failure.
Investigation found SMS provider timeout.
OTP generation succeeded.
SMS dispatch failed.
Recommended OTP resend."

This becomes Freshdesk internal note.

---

# 15. Action System — Reserved for Future Use

**Status: RESERVED. No L1 playbook routes to the Action Gateway in the current
implementation. The system is investigation-only at L1. This section is
preserved for future safe-action activation (e.g., SAFE-class OTP resend
initiated by a future L2 operator approval flow).**

The Action Gateway remains in the codebase (`case_engine/action_gateway.py`).
No production playbook should route to it until explicitly approved.

---

# 16. Action Gateway — Reserved for Future Use

**Status: RESERVED (in codebase, not activated for L1).**

Purpose: Enterprise safety boundary for executing remediation actions.

Responsibilities (future):

* Risk evaluation
* Approval routing
* Audit logging
* Retry management
* Rollback management

No external action may bypass Action Gateway when this layer is active.

---

# 17. Risk Model — Reserved for Future Use

**Status: RESERVED.**

SAFE — auto-execution (e.g., OTP resend, OCR retry): future use only.

REVERSIBLE — approval may be required (e.g., session state change): future use only.

HIGH — always requires human approval (e.g., financial operations): future use only.

---

# 18. Approval System — Reserved for Future Use

**Status: RESERVED.**

Human-in-the-loop protection for reversible/high-risk actions.
Not active in current L1 pipeline.

---

# 19. Verification Layer — Reserved for Future Use

**Status: RESERVED.**

Confirms executed actions succeeded (OTP delivered, session reset confirmed, etc.).
Not active in current L1 pipeline.

---

# 20. Recovery Layer — Reserved for Future Use

**Status: RESERVED.**

Handles execution failures (retry, rollback, dead-letter recovery).
Not active in current L1 pipeline.

---

# 20A. L2 Asana Escalation (Active — Wave 8)

When the Reasoning Engine determines `outcome=ESCALATE` or the workflow
signals `needs_l2=True`, the system executes the L2 escalation path:

1. `EngineeringEscalationService.create_ticket()` — calls Asana REST API v1
   with the Evidence Bundle, Root Cause, and Observation as task body.
2. Extract Asana task URL: `https://app.asana.com/0/{project_gid}/{task_gid}`
3. Update Freshdesk ticket field `cf_asana_ticket_link` with the URL via
   `FreshdeskResponseService` (sole approved Freshdesk write path).
4. Post public reply to bank agent via `FreshdeskResponseService`:
   "Hi, we have investigated the issue regarding your KYC session.
   [Root cause summary]. This has been escalated to our engineering team
   (Ref: [Asana Task URL]). We will update you once it is resolved."

DRY_RUN mode: all Asana calls and Freshdesk writes are skipped. The audit
event `ASANACREATE_DRY_RUN` is emitted instead, containing the full payload
that would have been sent.

Env vars:
  ASANA_API_KEY       — Bearer token for Asana REST API
  ASANA_PROJECT_ID    — Target project GID for new tasks
  ASANA_WORKSPACE_ID  — Workspace GID (used in task creation payload)

---

# 20B. L2 Asana Resolution Loop (New — Sprint 2.6x, uncertified)

The other direction of §20A: detecting when engineering marks the Asana task
complete, and closing the loop back to the Freshdesk ticket. Implements the
"Resolution Event → Freshdesk Update" step of §23's Engineering Workflow
diagram.

**Detection mechanism: Asana webhooks (not polling).**
`AsanaClient.create_webhook(target_url)` registers a webhook against the
configured `ASANA_PROJECT_ID`. Asana pushes an event to
`POST /webhooks/asana/task-completed` the moment a task's `completed` field
changes — no polling loop, no added latency between "dev checks the box" and
detection.

1. **Handshake** (one-time, at registration): Asana POSTs an `X-Hook-Secret`
   header to the target URL; the receiver echoes it back and persists it via
   `AsanaWebhookSecretStore` (`asana/webhook.py`) for verifying all future
   deliveries.
2. **Event delivery**: every subsequent POST carries an `X-Hook-Signature`
   header (HMAC-SHA256 over the raw body, keyed with the handshake secret).
   `verify_signature()` checks this before anything else runs — an invalid
   signature is rejected with 401 and never parsed.
3. **Event filtering**: only `resource_type=="task"` events with
   `change.field=="completed"` and `new_value==true` are acted on
   (`extract_completed_task_events()`); all other field changes on the
   project are ignored.
4. **Resolution**: `EngineeringEscalationService.get_ticket_by_external_id()`
   maps the Asana task GID back to the internal `EngineeringTicket`, then
   `resolve_ticket()` transitions it to `RESOLVED`.
5. **Freshdesk write**: an **internal** note is posted via
   `FreshdeskResponseService.add_internal_note()` (sole approved write path,
   per §26/Rule A) linking the Asana task and asking a human to verify before
   replying to the customer or closing the ticket.

**Deliberately not yet implemented: auto-reply + auto-close.** Closing a
Freshdesk ticket (`status=4/5`) requires `ClosureFieldGuard`
(`freshdesk/closure_guard.py`) to see `cf_clients`, `ticket_type`,
`cf_sop_status`, and `cf_resolution_classification` all populated. The exact
allowed values for the latter two are documented in
`Source_Of_Truth/Freshdesk_discovery/api_reference.md` §3.2, but no existing
document states which specific value applies to an "engineering resolved via
Asana" closure — that is a business-semantics decision, not a technical fact,
and guessing wrong risks writing incorrect classification data to a live
ticket. Proposed mapping (pending confirmation):
`cf_sop_status = "No SOP Available"` (escalated precisely because no SOP
covered it), `cf_resolution_classification = "Permanent Fix Applied by Dev"`
(or `"Temporary Fix Applied by Dev (Pending Permanent)"` if the fix is a
workaround). Once confirmed, wiring `send_customer_reply()` +
`update_ticket_fields()` (guarded) into the same resolution handler is a
small, well-scoped addition — see `.remember/remember.md` for the exact
function to extend.

Files: `asana/webhook.py`, `asana/client.py::create_webhook()`,
`case_engine/engineering/service.py::get_ticket_by_external_id()`,
`api/routes/webhooks/asana.py`, `scripts/register_asana_webhook.py`.
Tests: `tests/test_sprint263_asana_webhook_receiver.py` (31 tests, Sections
A-G — signature verification, event parsing, secret persistence, webhook
client, ticket lookup, route handshake/signature gate, resolution-loop
background task). Not yet run through `/regression` + `sprint-auditor` +
`freshdesk-safety-reviewer` + `/sprint-cert`.

Env vars (new):
  ASANA_WEBHOOK_SECRET_STORE_PATH — optional, defaults to
    `./data/asana_webhook_secrets.json`. Never committed (see `.gitignore`).

---

# 21. Escalation Logic

Escalate when:

* Confidence too low
* Root cause unclear
* Engineering issue detected
* Workflow exhausted
* Action rejected
* Log evidence is unavailable/ambiguous (see §9's retention caveat) and no
  other evidence source resolves the ticket — this must be treated as
  "insufficient evidence," not silently folded into "no issue found."

Output:

L2 Escalation Package

---

# 22. L2 Automation

System generates:

* Internal notes
* Escalation summary
* Engineering evidence package

Creates:

Asana ticket

Includes:

* Root cause
* Evidence
* Logs
* Session details
* Reproduction steps

---

# 23. Engineering Workflow

```
L2
↓
Asana
↓
Engineering Team
↓
Fix
↓
Resolution Event
↓
Freshdesk Update
```

---

# 24. Natural Language Generation (NLG) Layer — Customer Response

Purpose:

Generate professional customer replies.

Uses:

* SOPs
* Root cause
* Resolution outcome

LLM responsibilities:

* Rewrite
* Summarize
* Humanize

LLM never executes actions.

---

# 25. Ticket Closure

Conditions:

* Resolution completed
* Customer informed
* Escalations completed
* Verification successful

Only then:

Case Closed

---

# 26. Guardrails

The system must never:

* Execute actions outside Action Gateway.
* Modify production state without audit.
* Bypass approvals.
* Close tickets without resolution.
* Invent investigation results.
* Invent tool outputs.
* Invent root causes.
* Perform destructive actions without authorization.

---

# 27. Security Guardrails

Every operation must enforce:

Authentication
Authorization
Auditability
Least Privilege
Approval Controls
Recovery Controls
Data Access Controls
PII Protection

**PII Protection — specific, non-negotiable rule for log evidence**: No raw
log text pulled from the Multi-Tenant Log Platform (§34) — or any content
derived from it — may reach an LLM prompt, a Freshdesk note, or an L1
agent's screen without a redaction pass first (§35). This is not
precautionary: a real, unredacted government-ID photo and associated PII
fields (ID number, name, DOB, address, and other sensitive fields) have
been confirmed present in plaintext within ordinary client-side telemetry
log lines, on what appears to be every session, not as a rare edge case.
The redaction pass in §35 is the control point; no path from raw log
retrieval to any downstream consumer may bypass it.

Separately, the fact that this PII reaches the log platform at all (rather
than being scrubbed at the point the client-side event is generated) is a
frontend/event-logging pipeline issue independent of this support-agent
system's scope. It should be tracked and fixed as its own workstream by
whoever owns that pipeline — redacting on the way out (this system's
responsibility) protects this one consumer of the data, not every other
system the log platform already feeds.

---

# 28. Audit Requirements

The following must always be audited:

* Case Creation
* Case Updates
* Workflow Decisions
* Tool Executions
* Action Proposals
* Approvals
* Rejections
* Retries
* Rollbacks
* Escalations
* Ticket Closure

---

# 29. Enterprise Safety Principles

Principle 1:
Investigation before action.

Principle 2:
Evidence before reasoning.

Principle 3:
Reasoning before execution.

Principle 4:
Verification after execution.

Principle 5:
Recovery after failure.

Principle 6:
Audit everything.

Principle 7:
Human approval where required.

Principle 8:
No component bypasses Action Gateway.

---

# 30. Definition of Success

The system is successful when it can autonomously perform the equivalent responsibilities of:

L1 Support Agent

and

L2 Support Coordinator

while maintaining enterprise-grade safety, auditability, explainability, and operational reliability.

---

# 31. Knowledge System

## Knowledge Source:
StackOverflow Teams Export

## Knowledge Format:
JSON export uploaded manually by administrators.

## Knowledge Content:
- SOPs
- Known Issues
- Engineering Fixes
- Historical Resolutions
- Operational Runbooks
- Screenshots
- Images

### Purpose:
NOT to answer customers directly.

### Purpose:
To guide investigations and determine resolution paths.

### Knowledge Retrieval Flow:

```
Investigation
↓
Root Cause
↓
Knowledge Search
↓
Relevant SOP
↓
Recommended Action
```

---

# 32. Investigation System

## L1 Support Agent Workflow

1. Understand issue
2. Request missing information (URN, Session ID)
3. Identify user
4. Identify session
5. Collect evidence
6. Analyse logs
7. Analyse summary
8. Analyse videos
9. Determine root cause
10. Search SOP
11. Produce observation
12. Resolve or escalate

---

# 33. Future Integrations

- Freshdesk API (future — see §8 in `PROJECT_CONTEXT.md` for current
  webhook/write-path status)
- Asana API (active — Wave 8, see §20A)
- Multi-Tenant Support Portal APIs — Unity Bank's integration is COMPLETE
  (client, token management, session resolution, normalization, and all
  five investigation tool adapters are built and production-grade); it is
  not yet serving live traffic pending an operational routing-scope
  decision, which is a deployment gate, not a missing-code gap. Bank of
  Baroda, Central Bank, and additional clients remain future onboarding
  work, each requiring its own discovery pass per §8's tenant caveat.
- Multi-Tenant Log Platform (see §34) — validated end-to-end for one
  tenant via live testing; a second tenant's endpoint path has been
  corrected from an initial wrong assumption and validated; a third
  tenant's correct endpoint is still unresolved; a fourth tenant's
  connection details have not yet been provided by engineering. No longer
  a purely speculative integration — real endpoint contracts, auth
  mechanism, and query patterns are confirmed working for at least one
  tenant.
- Metrics Platform (Uptime Kuma) — a separate system from the Log
  Platform above; monitors infrastructure uptime, not session-specific
  backend logs. Do not conflate the two when reading this document or the
  diagram.
- Video APIs — see §12: video location is no longer purely future (§12a);
  video content analysis remains future (§12b).

---

# 34. Multi-Tenant Log Platform (Grafana Loki)

## Purpose

Provide the backend/infrastructure log evidence that the Admin Portal
cannot (§9). The Admin Portal is a session-metadata system operated
jointly with bank agents — it was never intended to expose raw backend
logs, and does not. The platform's own backend logs (application logs,
error logs, ingress logs) exist on the platform's server infrastructure and
are not directly exposed to this system for both practical and security
reasons — direct server access is not something this system should have.
Engineering has made those logs available through a secondary, purpose-built
log aggregation platform (Grafana Loki), which this system integrates
against instead.

## Architectural Position

The Log Platform is queried in Phase B of the Session Lookup Process (§9),
strictly after Phase A (Admin Portal) has produced a session's time window.
It is a peer to the Admin Portal in the tenant-resolution model (§5), not a
sub-component of it — each tenant has independent credentials, independent
connection details, and independent availability for each of the two
systems.

## Multi-Tenant Registry

Like the Admin Portal (§5), each tenant has:

* A separate log-platform base endpoint
* Separate credentials
* A separate logical datasource identifier

Onboarding a new tenant to log retrieval is an independent step from
onboarding that tenant's Admin Portal — a tenant can have one without the
other, and the system must represent and report that state accurately
rather than assuming they arrive together.

## Query Model

A log query for a session requires: the session's time window (from Admin
Portal Phase A, with a small buffer since client and server clocks are not
perfectly aligned), the session identifier, and the tenant's log-platform
credentials. Because a single tenant's backend is composed of multiple
services (web-facing API services, background workers, ingress/edge), a
single query strategy against one label is not reliable — a cross-service
sweep strategy that checks multiple known service identifiers, plus the
ingress layer, is required to reliably surface the complete relevant log
set for a session. This has been validated in practice against real
sessions for one tenant.

## Retention and Availability

See §9's retention caveat. Do not treat retention as a fixed, reliable
window — verify empirically per tenant and treat an empty result as
ambiguous rather than conclusive.

---

# 35. Log Relevance Extraction

## Why This Step Exists

A real session's raw log pull can run to several hundred log lines and
several hundred kilobytes of text. Handing that directly to the Reasoning
Engine on every single ticket is unacceptable on three independent
grounds: recurring per-ticket cost and latency at volume; PII exposure
(§27) since individual log lines can carry unredacted sensitive fields;
and signal dilution, since the large majority of any session's log volume
is routine, non-diagnostic traffic (heartbeats, polling, navigation
telemetry) that would otherwise crowd out the small number of lines that
actually explain the reported issue.

## Required Behavior

Between raw log retrieval (§34) and Root Cause Candidate generation (§10),
the system must:

1. **Redact known PII-bearing fields and cap oversized lines, before any
   other processing.** This ordering matters: if an oversized, PII-bearing
   line is scored or budgeted before it is redacted/capped, it can by
   itself consume an entire evidence budget and starve every other
   genuinely relevant line. Redaction and capping must be the first
   transformation applied to every line, not a step applied only to what
   survives selection.
2. **Score every remaining line for relevance to the specific ticket**,
   using a combination of: lexical match against the ticket's own text,
   a fixed priority boost for error/warning-level lines, and a boost for
   lines whose service matches keywords guessed from the ticket text. This
   must run at near-zero marginal cost per ticket — no per-ticket paid API
   call is acceptable for this step alone, independent of whatever the
   Reasoning Engine itself costs downstream.
3. **Always force-include every error-level line**, plus any lines falling
   inside a sub-window the auditor's own feedback text pinpoints (e.g. a
   stated duration range), regardless of relevance score — these are
   safety guarantees, not scoring trade-offs.
4. **Recognize and label known, recurring, session-agnostic platform bugs**
   (e.g. a routine ingress rule blocking event-tracking calls on many
   sessions) distinctly from ticket-specific evidence, so the Reasoning
   Engine does not mistake platform noise for the reported issue's root
   cause.
5. **Collapse near-identical repeated evidence** (the same event recurring
   many times) into a representative first/last occurrence plus a count,
   rather than repeating full content — preserving the "this was sustained,
   not a one-off" signal without spending the evidence budget on duplicates.
   Lines force-included under rule 3 are never collapsed.
6. **Re-order the final selection chronologically** before it proceeds to
   the Reasoning Engine, so the evidence still reads as a coherent timeline.

## Output

A compact, redacted, chronologically-ordered log excerpt — this, and only
this, is what feeds into §13's Curated Log Excerpt input. The raw pull
itself must not be persisted, traced, or exposed anywhere downstream of
this step.

## Why Not a Semantic/Embedding Approach

A per-ticket embedding-based semantic search was considered and rejected
for this step specifically because it would introduce a paid, per-call,
network-dependent cost on every single investigation, which contradicts
the near-zero-marginal-cost requirement in rule 2 above. A lexical
approach has been found, in practice, to already bridge the vocabulary gap
between natural-language ticket phrasing and structured event-code naming
conventions used in the logs, at zero marginal cost. This should be
revisited only if a future production evaluation demonstrates real,
recurring cases the lexical approach misses.