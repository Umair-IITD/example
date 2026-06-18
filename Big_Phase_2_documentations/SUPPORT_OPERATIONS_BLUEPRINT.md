# SUPPORT_OPERATIONS_BLUEPRINT.md

# Enterprise Support Agent — Business Truth & System Blueprint

Version: 1.0

Status: Approved Architecture

Owner: Support Automation Program

Purpose: This document is the single source of truth for how the Enterprise Support Agent operates, how support tickets flow through the system, how investigation is performed, how actions are executed, how escalation occurs, and how tickets are ultimately resolved and closed.

All future development must align with this document and the approved flow_diagram.mermaid architecture.

---

# 1. Mission

The objective of the system is to automate the responsibilities currently performed by Support L1 and Support L2 teams while preserving enterprise-grade safety, auditability, approval controls, recovery mechanisms, and compliance requirements.

The system must:

* Understand incoming support tickets.
* Clarify vague user queries.
* Investigate the issue.
* Identify root cause.
* Execute approved resolutions.
* Generate internal observations.
* Escalate to engineering when required.
* Respond to users.
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
3. Clarify missing information.
4. Request URN/User ID if needed.
5. Request Session ID if needed.
6. Locate relevant session.
7. Investigate logs.
8. Investigate session summary.
9. Investigate videos.
10. Determine root cause.
11. Record findings in internal notes.
12. Escalate to L2 if required.

Primary Goal:

Understand what happened and why it happened.

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

The system automates both L1 and L2 workflows.

Target Flow:

User Message
↓
Topic Classification
↓
Workflow Selection
↓
Slot Extraction
↓
Clarification
↓
Investigation
↓
Reasoning
↓
Action Proposal
↓
Execution
↓
Verification
↓
Resolution
↓
Internal Notes
↓
Engineering Escalation (if required)
↓
Customer Reply
↓
Ticket Closure

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

enabled_tools

workflow_overrides
```

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

## Layer 3 — Topic Classification

Purpose:

Determine what problem category the ticket belongs to.

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

Choose the correct playbook.

Input:

* Topic
* Context
* Metadata

Output:

Workflow Playbook

---

## Layer 5 — Slot Extraction

Purpose:

Extract required information.

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

Purpose:

Collect missing information.

Example:

User says:

"Video KYC failed."

Missing:

* URN
* Session ID

System sends:

"Please share your URN and Session ID."

Clarification loop continues until required slots are available.

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

# 8. URN Lookup Process

When Session ID is unavailable.

Process:

1. Receive URN.
2. Search Admin Portal.
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

Process:

1. Open session.
2. Retrieve details.
3. Retrieve summary.
4. Retrieve logs.
5. Retrieve video.
6. Retrieve audit trail.

Output:

Investigation context.

---

# 10. Logs Analysis Process

Logs are primary root-cause evidence.

Examples:

* GENERATE_OTP
* VALIDATE_OTP
* SEND_SMS
* SEND_EMAIL
* PAN_VALIDATION
* AADHAAR_VALIDATION
* DMS_OPERATION_LOG
* CALLBACK_EVENTS

System must identify:

* Failures
* Timeouts
* Validation errors
* Callback failures
* Network issues

Output:

Root Cause Candidates

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

Output:

Summary Evidence

---

# 12. Video Analysis Process

Future Capability

Purpose:

Review recorded KYC journey.

Examples:

* Camera issues
* Audio issues
* Blank screen
* Blurry image
* Liveness failures

Output:

Video Evidence

---

# 13. Reasoning Engine

Purpose:

Convert evidence into conclusions.

Input:

* Ticket
* Logs
* Summary
* Tool results
* SOP knowledge

Output:

Root Cause

Confidence

Recommended Action

Recommended Escalation

---

# 14. Observation Generator

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

# 15. Action System

Actions are never executed directly.

All actions must pass through:

Action Gateway

Examples:

* OTP Resend
* Session Reset
* OCR Retry
* Portal Refresh
* Callback Retry

---

# 16. Action Gateway

Purpose:

Enterprise safety boundary.

Responsibilities:

* Risk evaluation
* Approval routing
* Audit logging
* Retry management
* Rollback management

No external action may bypass Action Gateway.

---

# 17. Risk Model

SAFE

Examples:

* OTP resend
* OCR retry

Auto execution allowed.

REVERSIBLE

Examples:

* Session reset

Approval may be required.

HIGH

Examples:

* Financial operations
* User-impacting irreversible actions

Approval required.

---

# 18. Approval System

Human-in-the-loop protection.

Responsibilities:

* Review action
* Approve
* Reject

All approval decisions are audited.

---

# 19. Verification Layer

Purpose:

Confirm action succeeded.

Examples:

* OTP delivered
* Session reset successful
* Callback acknowledged

Output:

Success / Failure

---

# 20. Recovery Layer

Purpose:

Handle failures safely.

Capabilities:

* Retry
* Rollback
* Dead-letter recovery
* Manual recovery

All recovery operations audited.

---

# 21. Escalation Logic

Escalate when:

* Confidence too low
* Root cause unclear
* Engineering issue detected
* Workflow exhausted
* Action rejected

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

---

# 24. Customer Response Generation

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

---

# 28. Audit Requirements

The following must always be audited:

Case Creation

Case Updates

Workflow Decisions

Tool Executions

Action Proposals

Approvals

Rejections

Retries

Rollbacks

Escalations

Ticket Closure

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
2. Request missing information
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

# 33.Future Integrations

- Freshdesk API
- Asana API
- Multi-Tenant Support Portal APIs
    * Current : Unity Bank
    * Future : Bank of Baroda, Central Bank, Additional Clients
- Session APIs
- Metrics APIs
- Video APIs