# PROJECT_CONTEXT.md

**KwikID Enterprise Support Agent — Complete Knowledge Transfer Package**

Version: 1.0  
Date: 2026-07-21  
Audience: AI engineer onboarding to this codebase cold  
Purpose: After reading this document you should understand this project as if you participated in every sprint, debugging session, and architecture decision from the beginning.

---

# 1. Project Overview

## What This Project Is

This project builds an **Enterprise Support Agent** for KwikID, a Video KYC (Video Know Your Customer) platform used by Indian banks. The agent automates the work currently done by L1 and L2 human support agents who handle support tickets submitted by bank employees when KYC sessions fail.

The system receives support tickets from Freshdesk (a customer support platform), investigates the root cause by querying the KwikID bank admin portal APIs, reasons about evidence, generates L1 observation notes, proposes and executes resolution actions, and closes the ticket — all autonomously. When it cannot resolve an issue autonomously, it escalates to human L2 and creates an Asana engineering ticket.

## Why It Exists

KwikID powers Video KYC for multiple banks in India. When a customer's KYC session fails (camera issues, OTP not delivered, document OCR failed, session expired, etc.), a bank agent submits a support ticket to KwikID's support team. Currently, this support team is entirely human: L1 agents investigate logs, session summaries, and portal data, then write internal notes. L2 engineers get escalations and coordinate fixes.

This project replaces that manual workflow with an AI pipeline. The target is that the AI handles the equivalent responsibilities of an L1 support agent and an L2 support coordinator while maintaining enterprise-grade safety, auditability, and approval controls.

## Business Domain

**Video KYC (VKYC)**: A regulatory-compliant process in India where banks verify a customer's identity through a live video call. The customer presents their Aadhaar card, PAN card, signs on screen, answers questions, and passes a liveness check. An auditor watches the session and approves it. If any step fails, the session fails.

**KwikID Platform**: A SaaS platform that provides the Video KYC infrastructure to multiple banks. Each bank (Unity Bank, Bank of Baroda, Central Bank of India, RBL, etc.) is a client. KwikID provides the agent portal, the customer-facing interface, the KYC session recording, the audit trail, and the admin portal where support can investigate issues.

**The Five Support Topics** (these are the only ticket categories handled by this system):

1. **OTP Delivery Failure** — Customer not receiving OTP via SMS/email/voice during KYC initiation
2. **VKYC Session Failure** — Video KYC session dropped, expired, camera/mic/bandwidth issues, liveness failure
3. **Document OCR Failure** — PAN or Aadhaar OCR failed, face match mismatch, document scan quality issues
4. **Agent Portal Issue** — Bank agent cannot login to KwikID portal, account locked, queue errors, performance degradation
5. **API/Callback Failure** — CBS (Core Banking System) callback did not fire after KYC completion, SFDC webhook failed, DMS integration timeout

## Real Users and Stakeholders

**Bank Agents** (support ticket submitters): Employees of banks like Unity Bank who see that a customer's KYC failed and raise a Freshdesk ticket asking for investigation.

**Customers** (end users): Bank customers who are trying to complete KYC. They do not directly interact with Freshdesk — their bank agent does on their behalf. Sometimes the customer emails directly.

**L1 Support Agents** (being replaced): KwikID support staff who currently investigate logs, check admin portal, write internal notes, and raise L2 escalations manually.

**L2 Engineers** (partially being replaced): KwikID engineering staff who coordinate resolution, create Asana tasks, and coordinate with the dev team.

**Enterprise Context**: This is NOT a consumer chatbot. This is an internal enterprise workflow automation system. The AI acts as a support agent, not a customer-facing assistant. Every action it takes is audited. Every write to Freshdesk or external API goes through safety gates.

---

# 2. Business Reality

## KwikID Platform in Detail

**KwikID** is a Video KYC SaaS platform. A bank licenses KwikID to handle remote customer onboarding. The journey looks like this:

1. A bank customer needs to open an account or complete KYC.
2. Bank agent initiates a KYC session from the KwikID agent portal.
3. Customer receives an OTP via SMS/email to verify their phone number.
4. Customer clicks the VKYC link (or enters a code) and joins a video call.
5. In the video session: customer presents Aadhaar card (OCR scanned), PAN card (OCR scanned), signs on screen, answers verification questions, completes a liveness check (blink, turn head, smile).
6. An auditor (bank employee) watches the session live and approves or rejects.
7. On session completion, callbacks fire to the bank's CBS (Core Banking System) and SFDC (Salesforce).
8. The completed KYC session is stored with video, transcript, audit trail, scores.

**When Something Goes Wrong**: A bank agent notices that a customer's KYC session failed (session status = FAILED, TIMEOUT, or INCOMPLETE). The agent opens a Freshdesk ticket: "Customer URN XYZ123 VKYC session failed, please check." This ticket arrives at KwikID's support team.

## Key Identifiers

**URN (Unique Reference Number)**: A customer-level identifier assigned when a customer's KYC application is created. Format varies by bank. This identifies the customer application, not the session.

**Session ID**: A UUID v4 assigned to each VKYC session. Example: `329c5f9e-9406-4f47-b717-5c87f04054d2`. Multiple sessions can exist per URN (customer may have had multiple VKYC attempts).

**Phone Number**: 10-digit mobile number. This is the primary lookup key in the Unity Bank admin portal (NOT URN — URN does not exist in the Unity admin system). Critical distinction: L1 asks for URN to match the bank's application numbering, but the Unity portal API requires phone_number for session lookup.

**Agent ID**: The login username or employee ID (format AGT-XXXXXXXX) of a bank agent. Used when the issue is an agent portal access problem.

**Application ID**: Used in API callback failures. Identifies the banking application transaction.

## L1 Support Workflow (Human, Current Reality)

1. Ticket arrives in Freshdesk (sent by a bank agent).
2. L1 agent reads the ticket.
3. If URN or Session ID is missing: L1 replies to the bank agent asking for them.
4. Once URN/Session ID is obtained: L1 logs into the KwikID admin portal for that bank.
5. L1 looks up the customer's sessions.
6. L1 finds the failing session.
7. L1 examines session logs (GENERATE_OTP, VALIDATE_OTP, SEND_SMS, PAN_VALIDATION, etc.).
8. L1 examines session summary (face match score, liveness score, OCR results, auditor feedback).
9. L1 watches the session video if needed.
10. L1 determines root cause: "OTP was generated but SMS provider returned timeout", "Liveness score 0.23 below 0.8 threshold", etc.
11. L1 writes internal notes in Freshdesk.
12. If resolvable: L1 takes action (resend OTP, reset session, retry OCR).
13. If requires engineering: L1 escalates to L2, who creates an Asana ticket.
14. L1 replies to the bank agent with the outcome.
15. L1 closes the ticket.

## Freshdesk Platform

Freshdesk is the support ticket management system used by KwikID.

- Instance: `kwikid.freshdesk.com`
- Tickets arrive via: email, Freshdesk portal, or webhook
- 30 active Dispatch'r rules fire when a ticket is created — they set custom fields (`cf_clients`, `cf_query_type`, etc.), assign groups, and fire webhooks
- 10 Observer rules fire on ticket updates (most important: auto-reopen when customer replies)
- Tickets have 11 status values: Open (2), Pending (3), Resolved (4), Closed (5), and 6 custom pending sub-states
- The AI system receives tickets via webhook from Freshdesk's Dispatch'r rules
- The AI writes to Freshdesk via: POST /notes (private internal notes), POST /reply (public customer replies), PUT /tickets/{id} (field updates, status changes)

**Closure Requirements** (Freshdesk returns HTTP 422 if any are missing when setting status to Resolved or Closed):
- `cf_clients` (which bank — set by Dispatch'r, AI must not overwrite)
- `ticket_type` (AI sets to "Issues")
- `cf_sop_status` ("SOP Present" / "No SOP Available" / etc.)
- `cf_resolution_classification` ("Solved by SOP (Permanent)" / "Escalated to Engineering" / etc.)

## Asana Integration

When the system determines engineering intervention is needed, it creates an Asana task. The Asana task contains:
- Root cause summary
- Evidence package (logs, session data, API responses)
- Reproduction steps
- Customer impact

The Freshdesk ticket is tagged with the Asana task URL (`cf_asana_ticket_link`). When engineering fixes the issue, the Freshdesk ticket is updated and closed.

## Unity Bank (Current Active Client)

Unity Bank is the only client in active production scope right now. All real traffic comes from Unity Bank agents. The Unity Bank admin portal is at `https://vkyc360.unitybank.co.in`. The API authenticates with a custom `auth:` header (NOT standard `Authorization: Bearer`) and uses username/password `{"username":"unity","password":"unity"}` for a service account token.

---

# 3. Vision: The Enterprise Support Agent

## What This Is NOT

This system is NOT:
- A chatbot that chats with customers
- A knowledge base FAQ responder
- A simple ticket routing system
- A sentiment classifier with auto-reply templates
- A Retrieval-Augmented Generation (RAG) Q&A bot

## What This IS

An **Enterprise Support Agent** that autonomously performs the full L1 and L2 support workflow:

1. Understands incoming tickets using LLM semantic routing
2. Collects missing investigation data through structured clarification
3. Investigates root cause by querying real banking portal APIs
4. Reasons about evidence using LLM
5. Generates observation notes (replacing human L1 notes)
6. Proposes and executes resolution actions through a safety-gated Action Gateway
7. Escalates to engineering via Asana when needed
8. Closes tickets with all required fields set

## How It Compares to Competitors

**Sierra AI**: A conversational AI platform for customer support. Sierra focuses on customer-facing chat and voice. It is a chat interface that replaces human agents talking TO customers. KwikID's system is different — it is a back-office investigation agent that processes tickets and investigates portal data, not a conversational agent talking to customers.

**Intercom Fin**: An LLM-powered bot that answers customer questions using the company's knowledge base. It responds to customers with FAQ-style answers. KwikID's system goes far beyond this: it actually queries live banking portal APIs, retrieves session logs, performs root cause analysis, and executes actions. Fin's equivalent in this system would only be the "reply generator" — a small component at the very end of a long pipeline.

**Salesforce Agentforce**: Salesforce's enterprise AI agent platform. Closer in spirit to KwikID's system, as it integrates with CRM data and can take actions. However, Agentforce is a general platform; KwikID's system is domain-specific, deeply integrated with Video KYC portal APIs, and operates under banking compliance constraints (audit trails, action gateways, approval workflows for irreversible actions).

**The defining difference of this system**: It performs real L1 investigation using live banking portal data, not just knowledge base retrieval. It maintains a state machine for each ticket (Case object), tracks slots (collected information), runs a multi-step investigation pipeline, generates grounded observations from real evidence, and operates under enterprise safety guardrails. No existing vendor product does this for the VKYC support domain.

---

# 4. Architecture

## Canonical Reference

The architectural truth lives in two files:
1. `Source_Of_Truth/Architectural_truth/SUPPORT_OPERATIONS_BLUEPRINT.md` — business rules and pipeline definition
2. `Source_Of_Truth/Architectural_truth/flow_diagram.mermaid` — canonical system diagram

No implementation may violate the flow described in these two files.

## Architecture Layers (Top to Bottom)

### Layer 1 — Ticket Ingestion
**Purpose**: Receive support requests and create Case objects.

Freshdesk fires a webhook to our FastAPI endpoint when a new ticket is created. The webhook contains the full ticket JSON including custom fields (which bank it's from, ticket description, email, etc.).

Components:
- `api/routes/webhooks/freshdesk.py` — FastAPI route handler for webhook
- `freshdesk/handlers.py` — Payload parsing and normalization
- `freshdesk/idempotency.py` — WebhookIdempotencyStore (prevents double-processing same ticket)

The handler immediately returns HTTP 200 to Freshdesk (within 10 seconds, or Freshdesk retries) and processes asynchronously.

### Layer 1.5 — Multi-Tenant Client Resolution
**Purpose**: Determine which bank client this ticket belongs to.

KwikID serves multiple banks. Each bank has separate admin portal credentials, separate API endpoints, and separate configuration. Before any investigation begins, the system resolves which client environment the ticket belongs to.

Resolution uses the sender email domain or the `cf_clients` custom field (set by Freshdesk Dispatch'r rules before our webhook fires). Example: `mrunali.gaikwad@unitybank.co.in` → UNITY_BANK tenant.

Output: `TenantContext` object containing `tenant_id`, `portal_configuration`, `api_credentials_reference`, `enabled_tools`, and `workflow_overrides`.

If tenant cannot be resolved: UNKNOWN_TENANT → stop automation → escalate to human.

### Layer 2 — Case Engine
**Purpose**: Manage the complete lifecycle of each support case.

The Case Engine is the central orchestrator. It owns:
- Case object creation and persistence (Supabase database)
- State machine transitions (NEW → TRIAGE_COMPLETE → WORKFLOW_ACTIVE → AWAITING_INPUT → ACTION_PENDING → RESOLVED / ESCALATED → CLOSED)
- Slot state management (which information has been collected)
- Workflow tracking (which playbook is running, which step we're on)
- Audit integration (every state transition is logged)

Key files:
- `case_engine/models.py` — Case dataclass, TopicKey enum, ClassificationResult
- `case_engine/case_state.py` — CaseState enum
- `case_engine/service.py` — CaseService (slot filling, state transitions, slot-based clarification)

### Layer 2.5 — NLP Semantic Router
**Purpose**: Transform raw ticket text into structured intent signals.

**Current Implementation (Sprint 2.5.6)**: All regex/pattern-matching logic has been removed. Classification is now 100% LLM-driven using OpenAI (GPT-4o-mini) with structured JSON output.

The NLP Router receives the raw ticket text and returns an `NLPSignal` — a structured object containing:
- `intent`: The canonical intent name (e.g., "OTP_DELIVERY_FAILURE")
- `nested_case`: Sub-intent if present
- `entities`: Extracted values (URN, session_id, phone_number, etc.)
- `negation_detected`: True when customer writes "not receiving" rather than "received"
- `confidence`: 0.0–1.0 (must be ≥0.85 to proceed without escalation)
- `needs_clarification`: True if required slots are missing
- `clarification_question`: The question to ask the customer
- `raw_text`: Never logged (contains PII)

Key files:
- `case_engine/nlp_router.py` — NLPRouter class, LLM call, NLPSignal dataclass
- `ontology.json` — Canonical intents, nested cases, required slot definitions

### Layer 3 — Topic Classification
**Purpose**: Map NLPSignal intent → TopicKey enum.

The TopicClassifier receives the NLPSignal from the router and maps the intent string to one of five `TopicKey` values (or `UNKNOWN`). This is now trivially a dictionary lookup since all actual classification logic lives in the NLP Router.

- `tier_used = 0`: No router, no text → UNKNOWN
- `tier_used = 2`: Router used → TopicKey assigned (whether known or UNKNOWN)

Key file: `case_engine/classifier.py`

### Layer 4 — Workflow Selection
**Purpose**: Choose the correct investigation playbook for the topic.

Once a topic is known, the system selects the matching YAML playbook from the PlaybookRegistry. Each of the 5 topic families has a dedicated playbook.

Key files:
- `case_engine/workflows/playbook_registry.py` — PlaybookRegistry.get(topic)
- `case_engine/workflows/playbooks/*.yml` — 5 YAML playbook files

### Layer 5 — Slot Extraction
**Purpose**: Extract investigation-required information from the ticket text.

Using the entities surfaced by the NLP Router's NLPSignal, the system populates the Case's slot_state. Required slots are those needed for INVESTIGATION (URN + Session ID for most topics), not for resolution.

Critical rule (from Blueprint §6): The system MUST ask for URN and Session ID. It MUST NOT ask for customer phone number or other details before investigation. L1's job is to investigate portal logs, which strictly requires URN/Session ID.

Key file: `case_engine/topic_registry.py` (defines required vs optional slots per topic)

### Layer 6 — Clarification Engine
**Purpose**: Collect missing required slots from the ticket submitter.

If required slots (URN, Session ID) are not present in the ticket, the system:
1. Saves the current case state to Supabase
2. Posts a clarification question as a public Freshdesk reply to the ticket
3. Sets the ticket status to Pending (3)
4. Waits for the customer to reply

When the customer replies (via Freshdesk Observer webhook — currently a BLOCKING production gap), the system resumes.

Key file: `case_engine/clarification/engine.py`

### Layer 7 — Investigation Layer
**Purpose**: Automate L1 investigation by querying real portal APIs.

This is the most important business layer. After all required slots are filled:

1. **Investigation Planner** (`investigation/service.py`): Reads the playbook's `investigation_steps` metadata to determine which tools to call and in what order.

2. **Evidence Collector** (`investigation/pipeline.py`): Executes the planned tool calls against the Tenant API Router.

3. **Root Cause Engine** (`investigation/root_cause_engine.py`): Analyzes collected evidence to determine root cause candidates.

4. **Observation Generator** (`case_engine/investigation/observation.py`): Formats the evidence and root cause into a structured L1 observation note (replaces what human L1 agents would write).

Tool calls hit the Unity Bank Admin API:
- `GetUserDetailsTool` → `GET /api/v1/getAllUserSession/{domain}/{phone_number}`
- `GetSessionDetailsTool` → `GET /v1/session/get_details/{session_id}`
- `GetFailureReasonTool` → `GET /api/v1/auditDetails/{session_id}`
- `GetCaseHistoryTool` → Case history lookup
- `GetOnboardingStatusTool` → Onboarding status
- `LogTool` → Session log data (OTP events, SMS events, etc.)

### Layer 8 — Knowledge Layer
**Purpose**: Provide SOP and historical fix context to the reasoning engine.

After evidence collection, the system queries the knowledge base (stored in Supabase pgvector) to find:
- Relevant SOPs for the identified root cause
- Historical fixes for similar issues
- Engineering guidance

The knowledge base is populated from the StackOverflow Teams export (KwikID's internal engineering Q&A/wiki). This includes SOPs, runbooks, known issues, screenshots, and engineering fix descriptions.

Key components:
- `rag_engine/` — All RAG/retrieval logic
- Hybrid retrieval: semantic (pgvector cosine) + FTS (full-text search) + RRF fusion
- `case_engine/knowledge/service.py` — Knowledge service that the workflow engine calls

### Layer 9 — Reasoning Engine (Intelligence Layer)
**Purpose**: Convert evidence + knowledge into conclusions and action proposals.

The Intelligence Orchestrator (`intelligence/orchestrator.py`) constructs an LLM prompt containing:
- Ticket description
- Collected evidence (session logs, summary, Unity API responses)
- Retrieved SOP chunks
- Reasoning instructions from the playbook

The LLM (GPT-4o-mini) reasons about the evidence and returns:
- Root cause statement
- Confidence score
- Recommended action
- Customer reply draft
- Whether escalation is needed

### Layer 10 — Safety Guardrails
**Purpose**: Policy enforcement before any action is proposed.

Before any action can be proposed, guardrails check:
- Confidence meets threshold (≥0.85 for auto-execute, lower → human review)
- Action is within scope (no financial mutations, no PII modifications)
- Idempotency (same action not already executed for this ticket)
- Customer impact assessment

Key file: `freshdesk/reply_safety_gate.py` — ReplySafetyGate for all Freshdesk writes

### Layer 11 — Observation Generator (L1 Notes)
**Purpose**: Write structured internal investigation notes to Freshdesk.

Before any action is taken, the system posts an internal Freshdesk note containing:
```
Issue Summary
Observed Evidence
Root Cause
Recommended Action
Escalation Required: Yes/No
```

This replaces what a human L1 agent would write. These notes are private (agents only, no email sent to customer).

Key file: `case_engine/investigation/observation.py` → `freshdesk/responses.py`

### Layer 12 — Action Gateway
**Purpose**: Enterprise safety boundary. No action bypasses this gateway.

The Action Gateway receives a structured action proposal and:
1. Validates the schema
2. Evaluates risk level (SAFE / REVERSIBLE / HIGH)
3. Routes SAFE actions to immediate execution
4. Routes REVERSIBLE/HIGH to human approval workflow
5. Logs everything to audit database

Current action types:
- `otp_resend` (SAFE — OTP delivery failure)
- `vkyc_session_reset` (REVERSIBLE — session reset)
- `document_ocr_reprocess` (SAFE — OCR retry)
- `agent_session_refresh` (SAFE — portal refresh)
- `api_callback_retry` (REVERSIBLE — callback retry)

Key guardrail: Actions are NEVER executed directly. Every action goes through the gateway.

### Layer 13 — Executor, Verification, Recovery
**Purpose**: Execute approved actions and verify success.

After gateway approval:
- Executor calls the appropriate Unity Bank API endpoint
- Verification engine confirms the action succeeded (re-queries session status, etc.)
- Recovery service handles failures: retry, rollback, dead-letter queue

### Layer 14 — Resolution & Ticket Closure
**Purpose**: Close the loop.

After successful action or escalation:
1. Notes Generator produces final resolution note
2. Customer Reply Generator drafts professional response
3. Freshdesk ticket fields are updated (closure fields, resolution classification, etc.)
4. Ticket status set to Resolved (4) or appropriate pending state

On escalation:
1. Asana task created with full evidence package
2. Freshdesk ticket linked to Asana task (`cf_asana_ticket_link`)
3. Customer notified that issue has been escalated

---

# 5. Runtime Pipeline

## Step-by-Step Execution (Current Implementation)

```
1. Bank agent submits Freshdesk ticket
       ↓
2. Freshdesk Dispatch'r rules fire (30 rules, pre-AI):
   - cf_clients set (which bank)
   - Group assigned (L1 queue)
   - Default agent assigned
   - Tags added
   - AI webhook fires: POST to https://n8n.app.getkwikid.com/webhook/freshdesk-ticket-created
       ↓
3. n8n receives webhook, forwards to FastAPI:
   POST /webhooks/freshdesk/ticket-created
       ↓
4. FastAPI handler (api/routes/webhooks/freshdesk.py):
   - Validates HMAC signature (if FRESHDESK_WEBHOOK_ENFORCE_HMAC=true)
   - Parses payload
   - Checks idempotency (WebhookIdempotencyStore)
   - Returns HTTP 200 immediately
   - Enqueues async processing
       ↓
5. freshdesk/handlers.py — FreshdeskWebhookHandler:
   - Normalizes payload (maps webhook fields to internal schema)
   - Extracts: ticket_id, subject, description, from_email, custom_fields
       ↓
6. Client Resolution Engine:
   - Reads cf_clients custom field
   - Looks up Tenant Registry
   - Builds TenantContext (credentials, portal_url, enabled_tools)
       ↓
7. Case Service — create_case():
   - Creates Case object (ticket_id, client, initial state=NEW)
   - Persists to Supabase (cases table)
       ↓
8. NLP Semantic Router (Layer 2.5):
   - Receives ticket description text
   - Calls OpenAI GPT-4o-mini with structured output schema
   - Returns NLPSignal {intent, entities, confidence, negation_detected, needs_clarification}
       ↓
9. Topic Classifier (Layer 3):
   - Maps NLPSignal.intent → TopicKey enum
   - Confidence < 0.85 → UNKNOWN topic → clarification or escalation
       ↓
10. Slot Extraction (Layer 5):
    - Uses NLPSignal.entities to populate slot_state
    - Checks required slots: {urn, session_id} for most topics
       ↓
11. Decision: All Required Slots Present?
    → NO: Clarification Engine fires
          Post clarification question to Freshdesk
          Set ticket to Pending (3)
          Save case state to Supabase
          WAIT for customer reply via Observer webhook (CURRENTLY MISSING — BLOCKING)
    → YES: Continue to investigation
       ↓
12. Workflow Engine:
    - Selects playbook for TopicKey
    - Starts workflow execution (first step is always CLARIFY, then INVESTIGATE)
       ↓
13. Investigation Planner:
    - Reads playbook.investigation_steps metadata
    - Plans tool call sequence
       ↓
14. Evidence Collector:
    - Calls Unity Bank APIs via TenantContext credentials
    - Collects: user sessions, session details, audit trail, failure reasons
    - Collects: Uptime Kuma metrics (server health, monitor status)
       ↓
15. Root Cause Engine:
    - Analyzes collected evidence
    - Identifies root cause candidates with confidence scores
       ↓
16. Knowledge Service:
    - Queries Supabase pgvector for relevant SOPs
    - Hybrid retrieval (semantic + FTS + RRF fusion)
    - Returns top-K SOP chunks
       ↓
17. Intelligence Orchestrator (Reasoning Engine):
    - Builds LLM context: evidence + SOPs + playbook instructions
    - Calls OpenAI GPT-4o-mini
    - Parses structured response: root_cause, recommended_action, customer_reply, needs_escalation
       ↓
18. Safety Guardrails:
    - Policy checks
    - Confidence gate
    - PII check
       ↓
19. Observation Generator:
    - Formats evidence + root_cause into L1 observation note
    - Posts as private internal note to Freshdesk
       ↓
20. Needs Engineering? (L2 check)
    → YES: Create Asana ticket with evidence package
           Post escalation note to Freshdesk
           Update cf_asana_ticket_link
    → NO: Continue to action proposal
       ↓
21. Action Proposal:
    - Structured action proposal based on root cause and SOP
       ↓
22. Action Gateway:
    - Risk assessment (SAFE / REVERSIBLE / HIGH)
    - SAFE → Execute immediately
    - REVERSIBLE → Post draft note for human approval
    - HIGH → Block, escalate to human
       ↓
23. Executor:
    - Calls Unity Bank API for action (session reset, OTP resend, etc.)
       ↓
24. Verification Engine:
    - Confirms action succeeded
    - Re-queries session status
       ↓
25. Resolution:
    - Notes Generator writes final resolution note
    - Customer Reply Generator drafts professional response
    - Freshdesk ticket updated: fields, status → Resolved (4)
    - Ticket closed
```

## What Is Currently Implemented vs Expected

| Stage | Status |
|---|---|
| Freshdesk webhook receiver | ✅ Implemented |
| Idempotency store | ✅ Implemented |
| Client resolution (cf_clients-based) | ✅ Implemented |
| Case creation & persistence | ✅ Implemented |
| NLP Semantic Router (LLM) | ✅ Implemented (Sprint 2.5.6) |
| Topic Classification | ✅ Implemented |
| Slot extraction | ✅ Implemented |
| Clarification engine (ask questions) | ✅ Implemented |
| Clarification loop (resume on reply) | ⚠️ Partial — Observer webhook MISSING |
| Workflow engine (playbook execution) | ✅ Implemented |
| Investigation planner | ✅ Implemented |
| Unity Bank API tool adapters | ✅ Implemented (Sprint 2.51) |
| Uptime Kuma metrics tools | ✅ Implemented (Sprint 2.50) |
| Evidence collection pipeline | ✅ Implemented |
| Root cause engine | ✅ Implemented |
| Knowledge/SOP retrieval (RAG) | ✅ Implemented |
| Intelligence orchestrator (LLM reasoning) | ✅ Implemented (Sprint 2.53) |
| Observation generator | ✅ Implemented |
| Freshdesk note writer | ✅ Implemented |
| Action Gateway | ✅ Implemented (stub mode) |
| Executor (Unity API actions) | ⚠️ Partial — action execution stubs |
| Verification engine | ⚠️ Partial |
| Recovery service | ⚠️ Partial |
| Asana escalation | ⚠️ Partial |
| Ticket closure (all fields) | ⚠️ Partial — ClosureFieldGuard in place |
| Customer reply generation | ✅ Implemented |

---

# 6. Knowledge Layer

## Purpose

The knowledge layer provides SOPs, runbooks, historical fixes, and engineering guidance to the reasoning engine. It does NOT answer customers directly. It guides the AI's investigation decisions and action selection.

## Knowledge Source: StackOverflow Teams Export

KwikID uses StackOverflow Teams as its internal Q&A/wiki. Engineers and support staff document:
- SOPs (how to handle specific failure types)
- Historical fixes (what worked for issue X)
- Engineering guidance (which API to call, what parameters to use)
- Screenshots of portal states
- Known issues and their resolutions

This data is exported as JSON and ingested into the AI system.

## Ingestion Pipeline

```
StackOverflow Teams JSON Export
    ↓
Knowledge Importer (parses questions, answers, tags, images)
    ↓
SOP Parser (detects step-by-step SOPs vs general Q&A)
    ↓
OCR Pipeline (RapidOCR for images with text)
    ↓
Token-Aware Chunker (1200 tokens/chunk, 150 overlap, sentence-boundary aware)
    ↓
OpenAI Embeddings (text-embedding-3-small, 1536 dimensions)
    ↓
Supabase pgvector (documents table, index version v2)
    ↓
FTS Index (PostgreSQL full-text search, tsvector, GIN index)
```

## Retrieval: Hybrid RAG

The system uses hybrid retrieval combining:
1. **Semantic retrieval**: pgvector cosine similarity, top-K=20
2. **Full-text search (FTS)**: PostgreSQL tsvector, top-K=20
3. **RRF fusion**: Reciprocal Rank Fusion with k=60 to merge rankings
4. **BM25 reranking**: Further reranks the fused results
5. **Final top-K=5**: Only the 5 most relevant chunks are sent to the LLM

## Index Versioning

Two environment variables control which index version is active:
- `ACTIVE_INDEX_VERSION=v2` — SOURCE OF TRUTH for retrieval
- `B1_INDEX_VERSION=v2` — Written by ingestion pipeline

After a re-ingestion run, validate the new index, then set `ACTIVE_INDEX_VERSION` to activate it. The old index is not deleted immediately.

## OCR and Images

For images embedded in StackOverflow posts (screenshots of portal states, error screens), the system uses RapidOCR (ONNX runtime) to extract text from images. This extracted text is chunked and embedded alongside the surrounding post text.

## Current Status

The knowledge layer is frozen at version 2.1 (KNOWLEDGE_LAYER_FREEZE.md). The ingestion pipeline, chunking, embedding, retrieval, and reranking are all implemented and certified. The knowledge base contains SOPs for all 5 support topics.

## Future Plans

- Image understanding with vision models (beyond OCR text extraction)
- Automatic SOP update when new engineering fixes are posted to StackOverflow Teams
- Multi-client knowledge isolation (currently all clients share one index)

---

# 7. Workflow Engine

## Playbooks

Each of the 5 support topics has a YAML playbook in `case_engine/workflows/playbooks/`. A playbook defines the exact sequence of steps the workflow engine must execute.

## Standard Playbook Structure (v2.0 — 4 topics)

All non-OTP playbooks follow this step sequence:
```
CLARIFY         → Ask for missing slots (URN, Session ID)
INVESTIGATE     → Run investigation tools against admin portal
KNOWLEDGE_LOOKUP → Search SOPs for root cause
REASON          → LLM reasoning on evidence + SOPs
PROPOSE_ACTION  → Generate action proposal
ACTION_GATEWAY  → Safety gate (risk assessment, approval routing)
EXECUTE         → Call admin API to take action
RESOLVE_CASE    → Close ticket, write final note, reply to customer
ESCALATE_CASE   → Create Asana task, escalate to L2
```

## OTP Playbook (v3.0 — Special Case)

The OTP playbook was rewritten in Sprint 2.5.6 as v3.0 because OTP resend is a DEFERRED action. L1's job is to investigate why OTP failed (SMS provider timeout? DND? Wrong number?), NOT to immediately resend it.

OTP v3.0 steps:
```
CLARIFY          → Ask for URN and Session ID
INVESTIGATE      → Fetch OTP logs, SMS delivery logs via LogTool
KNOWLEDGE_LOOKUP → Retrieve OTP failure SOPs
REASON           → LLM reasoning on OTP failure evidence
RESOLVE_CASE     → Write L1 investigation observation to Freshdesk
ESCALATE_CASE    → Escalate to L2 for OTP resend (if needed)
```

**OTP v3.0 does NOT have**: PROPOSE_ACTION, ACTION_GATEWAY, EXECUTE. These are deferred to L2 because OTP resend requires phone number confirmation and carrier coordination — outside L1's investigation scope.

## Playbook Required Slots

| Topic | Required Slots (for investigation) | Optional Slots |
|---|---|---|
| VKYC_Session_Failure | urn, session_id | failure_code, phone_number (last 4) |
| OTP_Delivery_Failure | urn, session_id | channel (SMS/EMAIL/VOICE), phone_number (last 4), attempt_count |
| Document_OCR_Failure | urn, session_id | document_type, application_id, error_code |
| Agent_Portal_Issue | agent_id | portal_type, error_message |
| API_Callback_Failure | application_id, callback_type | error_code, retry_count |

**Why urn+session_id for OTP?** The phone_number previously was required — this was WRONG. L1 needs to investigate logs (which requires session_id), not resend OTP (which would require phone_number). Blueprint §6 mandates investigation first.

## WorkflowDefinition Model

```python
@dataclass
class WorkflowDefinition:
    workflow_id: str          # e.g., "vkyc_session_failure_v2"
    topic: str                # e.g., "VKYC_Session_Failure"
    version: str              # "2.0" or "3.0" for OTP
    name: str
    steps: tuple[WorkflowStep, ...]
    required_slots: tuple[str, ...]
    investigation_steps: tuple[dict, ...]   # {tool, purpose} sequence
    tool_candidates: tuple[str, ...]        # Tool names available to investigation
    resolution_paths: dict[str, str]        # {"automatic": step_id, "escalation": step_id}
```

## WorkflowEngine

The workflow engine (`case_engine/workflows/workflow_engine.py`) is a deterministic step executor. It:
- Receives a case and a playbook
- Executes steps in order
- For each step, dispatches to the appropriate subsystem (CLARIFY → ClarificationEngine, INVESTIGATE → InvestigationService, etc.)
- Records step results in `workflow_context` (persisted to Supabase)
- Handles ESCALATE_CASE by routing to the escalation path

## Investigation Metadata in Playbooks

Each playbook YAML contains `investigation_steps` (which tools to call and why) and `tool_candidates` (list of tool names the investigation planner may use). This metadata guides the InvestigationPlanner without hardcoding tool sequences in Python.

Example from VKYC playbook:
```yaml
investigation_steps:
  - tool: GetSessionDetailsTool
    purpose: Retrieve full session data including status and timestamps
  - tool: GetFailureReasonTool
    purpose: Get audit trail and failure reason from admin portal
  - tool: GetUserDetailsTool
    purpose: Look up user sessions by phone number to correlate
tool_candidates:
  - GetSessionDetailsTool
  - GetFailureReasonTool
  - GetUserDetailsTool
  - GetCaseHistoryTool
```

---

# 8. Freshdesk Integration

## Webhook Contract

**Ticket Created Webhook** (fires when new ticket arrives):
- Configured in Freshdesk Dispatch'r rule "AI auto replies" (ID 84000621616)
- Target URL: `https://n8n.app.getkwikid.com/webhook/freshdesk-ticket-created`
- n8n forwards to FastAPI
- Auth: `X-Webhook-Token` header (HMAC-SHA256; CURRENTLY NOT ENFORCED — BLOCKING)
- Timeout: 10 seconds — FastAPI must return 200 OK immediately

**Ticket Updated Webhook** (fires when customer replies):
- Status: MISSING — this Observer rule does not exist yet — BLOCKING
- Purpose: Resume clarification loop when customer provides URN/Session ID
- Must be created: Observer rule "When Reply is sent By Requester → POST to /webhooks/freshdesk/ticket-updated"
- Payload must include: `latest_comment.body`, `latest_comment.incoming`, `latest_comment.private`

**Webhook Payload Fields** (key mappings):

| Webhook Field | Internal Field |
|---|---|
| `freshdesk_webhook.id` | `ticket_id` (cast to string) |
| `freshdesk_webhook.ticket_custom_fields.cf_clients` | `client` / tenant identifier |
| `latest_comment.incoming` | `true` = customer reply, `false` = agent note |
| `latest_comment.private` | `true` = internal note, `false` = public reply |

## Freshdesk Write Rules (CRITICAL)

**Rule 1 — Sole Write Path**: All Freshdesk writes MUST go through `freshdesk/responses.py` → `FreshdeskResponseService`. No code may call `FreshdeskClient.add_reply()` or `FreshdeskClient.add_note()` directly. This is the only way to guarantee all safety gates, idempotency, and audit logging are applied.

**Rule 2 — ClosureFieldGuard**: Before any PUT to `/tickets/{id}` with `status=4` (Resolved) or `status=5` (Closed), `freshdesk/closure_guard.py` → `ClosureFieldGuard` must run. It validates that `ticket_type`, `cf_sop_status`, and `cf_resolution_classification` are set. Missing any one field → HTTP 422 from Freshdesk.

**Rule 3 — ReplySafetyGate**: Before any public reply (POST `/tickets/{id}/reply`), `freshdesk/reply_safety_gate.py` → `ReplySafetyGate` must evaluate confidence. Below threshold → post as private DRAFT note instead, set `cf_review_ticket=Yes`.

**Rule 4 — No Draft API**: Freshdesk has no draft reply API. Any call to POST `/reply` sends an email to the customer immediately and cannot be undone. Use POST `/notes` with `private=true` as a draft mechanism.

## Comment Type Detection

When the ticket-updated webhook fires:
```
incoming=true,  private=false → Customer reply → Resume clarification loop
incoming=false, private=false → Agent/AI public reply → Skip
incoming=false, private=true  → Internal note → Skip
```

## Custom Fields Written by AI

| Field | API Name | Set When |
|---|---|---|
| Ticket Type | `ticket_type` | Always "Issues" |
| SOP Status | `cf_sop_status` | At closure: "SOP Present" / "No SOP Available" / etc. |
| Resolution Classification | `cf_resolution_classification` | At closure |
| RCA Status | `cf_rca_status` | After investigation |
| Query Type | `cf_query_type` | After classification (67 possible values) |
| RCA | `cf_rca` | Free-text root cause |
| StackOverflow Link | `cf_stackoverflow_link` | When SOP found |
| Asana Link | `cf_asana_ticket_link` | When escalated |
| Handling Time | `cf_handling_time` | At closure (integer minutes) |
| Review Ticket | `cf_review_ticket` | Set to "Yes" when confidence below threshold |

## Dispatch'r Rules (Pre-AI, Cannot Be Changed by AI)

These 30 rules fire BEFORE the AI webhook. They set `cf_clients`, assign groups, add tags. The AI must not fight these rules. Specifically:

- **Observer 84000606270**: Auto-assigns ticket to first responder if agent is NONE. If the AI uses a human agent's API key, this rule will assign the ticket to that human agent.
- **"AI auto replies" rule**: Currently only fires for test emails + "Others" category. Real Unity Bank tickets DO NOT trigger this rule — the AI never sees them. This must be fixed before production.

## Rate Limiting

- Freshdesk account-wide limit: 40 req/min
- AI self-imposed limit: 30 req/min
- Headers in every response: `x-ratelimit-remaining`
- On 429: exponential backoff, max 3 retries

---

# 9. Investigation Engine

## Planner

`case_engine/investigation/service.py` → `InvestigationService`

Reads `playbook.investigation_steps` (the tool sequence metadata in each YAML playbook) and produces a plan: ordered list of (tool_name, tool_params) tuples.

The planner is driven by the playbook, not by hardcoded Python logic. Adding a new investigation step only requires updating the playbook YAML.

## Collector

`case_engine/investigation/pipeline.py` (or `_collector_sprint218.py` for Sprint 2.18 version)

Executes the planned tool calls against the Tenant API Router. Each tool call:
1. Authenticates via TenantContext credentials (for Unity: generate JWT token via `/v1/agent/generate_token`)
2. Calls the Unity Admin API endpoint
3. Normalizes the response into the internal EvidenceBundle format
4. Logs the trace event

Tool adapters live in `case_engine/tools/adapters/unity_tools.py`. Each adapter:
- Wraps a specific Unity API endpoint
- Handles authentication token refresh (tokens expire after 12 hours)
- Maps the raw API response to an internal model (e.g., `UnitySession` dataclass)
- Captures trace tags: `ENTER_TOOL_{name}`, `EXIT_TOOL_{name}`, `TOOL_ERROR_{name}`

## Reasoner

`case_engine/investigation/root_cause_engine.py`

Analyzes the collected EvidenceBundle to produce root cause candidates. Each candidate has:
- A root cause description
- Supporting evidence references
- Confidence score
- Recommended action type

The reasoner is a combination of rule-based analysis (known failure patterns from logs) and LLM reasoning.

## Observation Generator

`case_engine/investigation/observation.py`

Formats the EvidenceBundle + root cause into a structured L1 observation note. This note is what gets posted to Freshdesk as a private internal note — replacing what a human L1 agent would write.

Format:
```
**Issue Summary**: [One sentence]
**Observed Evidence**:
  - Session status: FAILED
  - OTP generation: SUCCESS at 14:23:01
  - SMS dispatch: TIMEOUT at 14:23:03 (carrier: Jio, error: GATEWAY_TIMEOUT)
**Root Cause**: SMS provider (Jio) gateway timeout during OTP dispatch
**Recommended Action**: Retry OTP via alternate channel (email)
**Escalation Required**: No (SAFE action available)
```

## Current Implementation

The investigation layer is implemented end-to-end (Sprint 2.54). The Unity Bank adapter tools are production-ready (Sprint 2.51). The Uptime Kuma metrics tools are production-ready (Sprint 2.50).

## Missing Parts

- **Video retrieval tool**: `GetVideoFile` endpoint exists in Unity API but is not yet integrated. Session video analysis is a future capability.
- **Cross-session correlation**: If a customer has multiple sessions, the current implementation investigates only the provided session_id. Cross-session pattern analysis is not yet implemented.
- **RPC-based golden retrieval**: Some test infrastructure depends on Supabase RPCs that require specific functions to be deployed to production Supabase.

---

# 10. Runtime Status (Per Subsystem)

| Subsystem | Status | Notes |
|---|---|---|
| FastAPI webhook receiver | ✅ Production Ready | HMAC not enforced yet |
| Idempotency store | ✅ Production Ready | Redis or in-memory fallback |
| Client resolution | ✅ Production Ready | cf_clients based |
| Case persistence (Supabase) | ✅ Production Ready | |
| NLP Semantic Router (LLM) | ✅ Production Ready | Sprint 2.5.6 |
| Topic Classifier | ✅ Production Ready | LLM-only, no regex |
| Slot extraction | ✅ Production Ready | |
| Clarification engine | ✅ Production Ready | |
| Clarification loop (resume) | ❌ BLOCKING | Observer webhook missing |
| Playbook registry | ✅ Production Ready | 5 playbooks loaded |
| Workflow engine | ✅ Production Ready | |
| Investigation planner | ✅ Production Ready | |
| Unity Bank auth/token | ✅ Production Ready | Sprint 2.51 |
| Unity GetUserDetailsTool | ✅ Production Ready | Sprint 2.51 |
| Unity GetSessionDetailsTool | ✅ Production Ready | Sprint 2.51 |
| Unity GetFailureReasonTool | ✅ Production Ready | Sprint 2.51 |
| Unity GetCaseHistoryTool | ✅ Production Ready | Sprint 2.51 |
| Unity GetOnboardingStatusTool | ✅ Production Ready | Sprint 2.51 |
| LogTool | ✅ Implemented | OTP-specific |
| Uptime Kuma MetricTool | ✅ Production Ready | Sprint 2.50 |
| Uptime Kuma ServerTool | ✅ Production Ready | Sprint 2.50 |
| Knowledge/SOP retrieval | ✅ Production Ready | Hybrid RAG |
| Intelligence orchestrator | ✅ Production Ready | Sprint 2.53 |
| Observation generator | ✅ Production Ready | |
| Freshdesk note writer | ✅ Production Ready | |
| Freshdesk reply generator | ✅ Production Ready | |
| ClosureFieldGuard | ✅ Production Ready | |
| ReplySafetyGate | ✅ Production Ready | |
| FreshdeskResponseService | ✅ Production Ready | Sole write path |
| Action Gateway | ✅ Implemented | Risk model + routing |
| Executor (Unity actions) | ⚠️ Partial | Action endpoints partially implemented |
| Verification engine | ⚠️ Partial | |
| Recovery service | ⚠️ Partial | Retry logic in place |
| Asana escalation | ⚠️ Partial | Basic task creation |
| Audit logging | ✅ Production Ready | 40+ event types |
| Startup validator | ✅ Production Ready | Sprint 2.52, 9-check sequence |
| Prometheus metrics | ✅ Implemented | Optional dependency |
| HMAC webhook verification | ❌ NOT SET | Secret not configured |
| Dedicated AI agent account | ❌ MISSING | Using human credentials |
| Unity Bank real ticket scope | ❌ BLOCKED | Dispatch'r rule too narrow |

---

# 11. Sprint History

## Big Phase 1 (Pre-Case Engine)

### Sprints 1–2.9 (Knowledge Ingestion Platform)

**Goal**: Build the RAG knowledge ingestion and retrieval pipeline.

**Sprint 1–2.1** (B1 Phase): Established the core FastAPI service, Supabase + pgvector infrastructure, and basic document ingestion. Built the first version of the semantic retrieval pipeline.

**Sprint B1 (Phase B1)**: Token-aware chunker v2 (1200 tokens/chunk, sentence-boundary aware). Circuit-breaker embedding pipeline. Adaptive RRF hybrid retrieval. CrossEncoder reranker (worker-safe, GPU-capable). Redis-backed distributed rate limiter. Index versioning (v1 → v2).

**Sprint B2 (Phase B2)**: RAG generation pipeline. LLM chat interface. Conversation history (6-turn depth). Context assembler. PII masking. Security hardening (HMAC, log redaction).

**Sprint 2.7**: Application consolidation — merged multiple FastAPI apps into one. Metrics infrastructure. Route audit.

**Sprint 2.8**: Dead-letter state and optimistic locking in action gateway. Prometheus metrics. Supabase audit repository.

**Sprint 2.9**: Production hardening. Docker multi-stage build. Config reference.

**Sprint 2.10**: Metrics endpoint, action gateway metrics wiring, 120+ production tests.

## Big Phase 2 (Case Engine)

### Sprint 2.11 — State Machine Foundation

**Goal**: Build the Case object and CaseState state machine.

**Implementation**: `Case` dataclass, `CaseState` enum, `CaseService`, `AuditLogger`. Slot state management framework. Slot filling processor (AWAITING_INPUT state). Case persistence in Supabase.

**Result**: Case lifecycle from NEW → CLOSED fully modeled.

### Sprint 2.13 — Operational Visibility

**Goal**: Internal admin dashboard and audit trail.

**Implementation**: Admin API endpoints for case management, audit log viewer, case state queries.

### Sprint 2.14 — Human Recovery Layer

**Goal**: Human-in-the-loop recovery for failed automations.

**Implementation**: Human takeover mechanism. Transfer context payload (structured package for human agent). Escalation note template. DEAD_LETTER state handling.

### Sprint 2.15 — Case Engine Foundation (Slot Filling)

**Goal**: Build slot extraction and clarification loop.

**Implementation**:
- `topic_registry.py` — slot definitions per topic
- `clarification/engine.py` — ClarificationEngine
- `CaseService.receive_message()` — full slot-filling state machine
- `CaseService.get_slot_state()` — slot state inspection
- Tests for all slot transitions, escalation on max attempts

**Key**: This sprint established the "ask for URN/Session ID" pattern.

### Sprint 2.16 — Workflow Orchestration

**Goal**: Build the workflow engine and 5 playbooks.

**Implementation**:
- `case_engine/workflows/models.py` — WorkflowDefinition, WorkflowStep, WorkflowStepType
- `case_engine/workflows/playbook_registry.py` — PlaybookRegistry
- 5 YAML playbooks (initial v2.0 versions)
- `case_engine/workflows/workflow_engine.py` — step executor
- `CaseService.start_workflow()` and `resume_workflow()`
- Audit events: WORKFLOW_STARTED, STEP_COMPLETED, ESCALATED, RESOLVED

**Result**: Deterministic workflow execution over YAML playbooks.

### Sprint 2.17 — Workflow Reliability

**Goal**: Fix ACTION_PENDING → RESOLVED state machine gap and add enterprise reliability features.

**Implementation**:
- `WorkflowConsistencyChecker` — validates state machine invariants
- Double-start guard for workflows
- Admin visibility API (tool catalog, playbook admin endpoints)
- Playbook evolution: investigation_steps, tool_candidates, resolution_paths added to YAML
- Investigation tool framework (mock tools)

### Sprint 2.18 — Investigation Framework

**Goal**: Build the complete investigation layer (planner, collector, root cause, observation).

**Implementation**:
- `case_engine/investigation/models.py` — EvidenceBundle, RootCauseCandidates, etc.
- `case_engine/investigation/planner.py` — InvestigationPlanner
- `case_engine/investigation/collector.py` — EvidenceCollector
- `case_engine/investigation/root_cause.py` — RootCauseEngine
- `case_engine/investigation/observation.py` — ObservationGenerator
- Investigation admin API
- INVESTIGATE step type added to WorkflowEngine dispatcher

### Sprint 2.19 — Knowledge Layer Integration

**Goal**: Wire the knowledge/SOP retrieval into the workflow.

**Implementation**: `case_engine/knowledge/service.py` — KnowledgeService integrates hybrid RAG into the workflow as a KNOWLEDGE_LOOKUP step.

### Sprint 2.20 — Investigation Step Integration

**Goal**: Wire INVESTIGATE step into WorkflowEngine and connect to evidence collection.

**Implementation**: Investigation step execution wired end-to-end. WorkflowExecutionResult now carries `investigation_result`. PROPOSE_ACTION guard (blocks action until investigation is complete).

### Sprint 2.21 — Action Gateway Foundation

**Goal**: Build the Action Gateway and risk model.

**Implementation**: Action Gateway with risk classification (SAFE/REVERSIBLE/HIGH). Approval workflow. Audit integration. Idempotency key management. Dead-letter queue.

### Sprint 2.22–2.23 — Pipeline Hardening

**Goal**: End-to-end pipeline hardening, performance, and observability.

**Implementation**: Architecture audit, pipeline wiring improvements, trace tag instrumentation, runtime dependency graph.

### Sprint 2.24 — Business Pipeline

**Goal**: Build the StageResult pipeline contract and business-facing pipeline stages.

**Implementation**: `pipeline/contract.py` (StageStatus, StageSeverity, StageResult), `pipeline/stages.py` (5 concrete stage adapters), `pipeline/manifest.py`, `pipeline/guard.py`.

### Sprint 2.25 — Playbook Certification

**Goal**: Full playbook certification with 300+ tests.

**Implementation**: Complete test coverage for all 5 playbooks, all step types, required slots, investigation metadata, risk levels, execution action types.

### Sprint 2.26 — Runtime Dependency Graph

**Goal**: Map all runtime dependencies and wire them cleanly.

**Implementation**: Full runtime dependency graph. Integration readiness matrix.

### Sprint 2.27–2.27.9 — Golden Path Architecture

**Goal**: Implement the complete end-to-end "golden path" from ticket receipt to resolution.

**Implementation**:
- Full golden path from Freshdesk → Note Posted verified
- `case_engine/runtime/support_agent_runtime.py` — SupportAgentRuntime (main runtime coordinator)
- `case_engine/ticket_orchestration/orchestrator.py` — TicketOrchestrator
- Architecture review, code review, dependency graph all certified

### Sprint 2.28 — Production Readiness

**Goal**: First production readiness milestone for Unity Bank.

**Implementation**: Freshdesk write path hardening, all closure fields set, rate limiting, security review.

### Sprint 2.30 — Execution Verification

**Goal**: Verify execution layer and recovery.

**Implementation**: Execution verification report. Action DB integration.

### Sprint 2.48 — Freshdesk Integration Audit & Hardening

**Goal**: Full audit of Freshdesk integration against Freshdesk API discovery documents.

**Implementation**:
- Blueprint reconciliation matrix
- FreshdeskResponseService (sole write path)
- ClosureFieldGuard (prevents HTTP 422)
- WebhookIdempotencyStore
- Full webhook receiver pipeline

**Blocking issues identified**: 4 manual admin actions required before real Unity traffic.

### Sprint 2.49 — Freshdesk Runtime Traces

**Goal**: Add TRACE_FD_01 through TRACE_FD_10 trace tags throughout the Freshdesk pipeline.

**Implementation**: 10 canonical trace tags for every Freshdesk API call. Postman verification recipe.

### Sprint 2.50 — Metrics Platform (Uptime Kuma)

**Goal**: Build real production Uptime Kuma integration.

**Implementation**:
- `UptimeKumaClient` — async, read-only, retry, auth from config
- `MetricsToolAdapter` — Tool Framework integration, capability routing
- MetricsEvidence, MetricPoint, ServiceHealth, DashboardSnapshot models
- 6 TRACE tags: TRACE_METRICS_01 through TRACE_METRICS_06

### Sprint 2.51 — Unity Admin Portal Integration

**Goal**: Replace all 5 Unity mock tool adapters with real production adapters.

**Implementation**:
- `unity/` package: config, exceptions, models, token_manager, client, session_resolver, normalizer, traces
- 5 real adapters: GetUserDetailsTool, GetSessionDetailsTool, GetFailureReasonTool, GetCaseHistoryTool, GetOnboardingStatusTool
- 10 TRACE tags: TRACE_UNITY_01 through TRACE_UNITY_10
- **Critical discovery**: Auth header is `auth:` not `Authorization: Bearer`; primary ID is phone_number not URN

### Sprint 2.52 — Platform Wiring & Runtime Stabilization

**Goal**: Auto-register all production tools at startup with validation.

**Implementation**:
- `app/startup_validator.py` — 9-check startup validation sequence
- Auto-registration of 5 Unity + 2 Metrics tools at FastAPI startup
- STARTUP_READY report logged on every startup
- 13 runtime trace tags for startup and runtime boundaries
- Prometheus vs Uptime Kuma auth distinction (password-only vs username+password)

### Sprint 2.53 (Wave 3) — Enterprise Intelligence Layer

**Goal**: Build the LLM reasoning layer (provider-agnostic, schema-strict).

**Implementation**:
- `intelligence/` package: config, exceptions, models, traces, context_builder, prompt_builder, llm_client, reasoning_parser, orchestrator
- 5 versioned prompt templates (one per topic family)
- 5 schema-strict response parsers
- 22 Wave-3 trace tags
- Provider-agnostic design (OpenAI, Anthropic, or any compatible API)

### Sprint 2.53 Wave 4A — Intelligence Runtime Wiring

**Goal**: Wire the IntelligenceOrchestrator into the full L1 pipeline.

**Implementation**:
- Full Blueprint L1 pipeline wired end-to-end
- 14 ENTER/EXIT boundary trace tags for all intelligence stages
- Hybrid RAG chunks + playbook SOPs wired into LLMContext
- Runtime verification with realistic Freshdesk payload
- Security review: prompt injection, PII, sanitization

### Sprint 2.54 — Pipeline Wiring & Slot Extraction

**Goal**: Fix slot pre-extraction and complete production tool wiring.

**Implementation**:
- Slot pre-extraction fix (slots extracted from NLPSignal entities at ticket creation)
- 8 Blueprint trace tags for investigation pipeline
- ticket-updated handler with OPEN/PENDING resume logic
- 40 targeted tests

### Sprint 2.5.5 — Runtime Alignment (7 Blockers)

**Goal**: Fix 7 critical runtime blockers discovered during full pipeline execution.

**Implementation** (7 permanent architectural rules established):
1. `asyncio.to_thread()` for all blocking I/O in async context
2. Frozen guard for immutable artifacts (playbooks cannot be mutated at runtime)
3. Double-planner fix (investigation cannot be planned twice for same case)
4. OBSGEN→FDNOTE pipeline (observation generator feeds Freshdesk note writer)
5. Registry recovery (tool registry reloads on startup failure)
6. Plus 2 more pipeline convergence fixes

**Result**: End-to-end pipeline functional with real OTP ticket replay.

### Sprint 2.5.6 — LLM Semantic Router (Current)

**Goal**: Replace all regex/pattern classification with LLM semantic routing.

**Implementation**:
- All Tier 1 regex patterns removed (they never matched real-world phrasing)
- `case_engine/nlp_router.py` — NLPRouter class with OpenAI structured output
- `NLPSignal` dataclass (intent, entities, confidence, negation_detected, etc.)
- `ontology.json` — canonical intents and slot specs
- Classifier rewritten to delegate to NLPRouter
- Critical bug fixed: OTP required slots changed from {phone_number, channel} to {urn, session_id}
- OTP playbook rewritten to v3.0 (L1 investigation-only model)
- 55 tests for NLP router
- 57 test regressions from slot/playbook changes fixed across 8 test files
- 301 tests across 8 test files now passing

---

# 12. Current Reality

## What Works (as of 2026-07-21, Sprint 2.5.6)

1. **Freshdesk webhook reception** works. The FastAPI server receives tickets, validates them, and queues them for processing.

2. **NLP classification** works. The LLM router correctly classifies the 5 topic families with confidence scoring, entity extraction, and negation handling.

3. **Slot extraction** works. URN and Session ID are extracted from ticket text (or from NLPSignal entities).

4. **Clarification** works. The system asks for missing slots via Freshdesk public reply and sets ticket to Pending. However, when the customer replies, the system does NOT yet receive the reply (Observer webhook missing).

5. **Workflow execution** works. Playbooks are loaded, steps execute in order, state is persisted.

6. **Unity Bank API integration** works. All 5 tool adapters are production-ready, authentication token management works, Unity API responses are normalized correctly.

7. **Evidence collection** works. The investigation pipeline fetches real data from Unity Bank portal.

8. **LLM reasoning** works. The Intelligence Orchestrator constructs prompts from evidence + SOPs and calls OpenAI for root cause analysis and action proposals.

9. **Freshdesk note writing** works. Internal investigation notes are posted correctly as private notes.

10. **Knowledge retrieval (RAG)** works. SOPs are retrieved from Supabase pgvector using hybrid retrieval.

11. **Test suite**: 301 tests pass across Sprint 2.5.6 test files. Pre-existing failures (~131) are in knowledge ingestion and golden retrieval tests that require RPC functions deployed to production Supabase.

## What Does Not Work

1. **Clarification loop resume**: When a customer replies to the clarification question, the AI does not receive the reply. This requires an Observer rule in Freshdesk that does not exist yet.

2. **Real Unity Bank tickets reach AI**: The Dispatch'r rule "AI auto replies" currently only fires for test emails and "Others" category. Real Unity Bank tickets (from `unitybank.co.in` email domain, with `cf_clients="Unity"`) do not trigger the webhook.

3. **HMAC verification**: `FRESHDESK_WEBHOOK_SECRET` is not set. Anyone who discovers the webhook URL can send fake ticket events.

4. **Dedicated AI agent account**: The system uses a human agent's API key. This means the Observer auto-assignment rule assigns tickets to that human agent, creating audit confusion.

5. **Full action execution**: Some action endpoints in the Unity Bank API are not yet wired. The executor partially works.

## Current Blockers (4 Before Production)

| Blocker | Fix Required |
|---|---|
| Missing Observer rule for customer-reply webhook | Create Freshdesk Observer rule: "Reply sent by Requester → POST /webhooks/freshdesk/ticket-updated" |
| FRESHDESK_WEBHOOK_SECRET not set | Generate strong random key, configure in both Freshdesk and .env |
| "AI auto replies" Dispatch'r rule scope too narrow | Extend rule to include `cf_clients = "Unity"` or `from_email domain contains "unitybank.co.in"` |
| No dedicated AI agent account | Create `ai.support@getkwikid.com` Freshdesk account, get API key, update codebase |

## Current Known Bugs

1. **Index version mismatch**: `B1_INDEX_VERSION` vs `ACTIVE_INDEX_VERSION` may be out of sync after a re-ingestion run. After any ingestion, validate the new index before flipping `ACTIVE_INDEX_VERSION`.

2. **Supabase RPC functions**: Some tests depend on Supabase stored procedures (`match_documents_hybrid`) that must be deployed via SQL migrations `sql/b1_migrations/B1_007.sql` and `B1_008.sql`.

3. **Sentry not integrated**: `sentry_sdk` is imported in some test files but not installed in the environment. These files fail at collection (not execution). Install `sentry-sdk` or add the optional import guard.

---

# 13. Lessons Learned

## Major Architectural Decisions

### Decision 1: LLM Router Replaces Regex (Sprint 2.5.6)

**Rejected**: Regex/keyword patterns for Tier 1 classification.

**Reason**: KwikID support tickets are written by Indian bank employees in informal, mixed-language English. "OTP nahi aaya" (Hindi), "vkyc link expire ho gaya", "camera nahi chal raha" — regex patterns match none of these reliably. The Tier 1 accuracy was below 60% on real tickets.

**Decision**: Use OpenAI LLM with structured JSON output for all classification. Accept the ~200ms latency increase for correctness.

**Result**: Classification accuracy dramatically improved. The LLM handles negation, informal phrasing, mixed Hindi-English, and entity extraction in one pass.

### Decision 2: Investigation Slots Are NOT Resolution Slots

**Rejected**: Asking for `phone_number` and `channel` as required OTP slots.

**Reason**: L1's job is to investigate the log trail, not to resend OTP prematurely. To investigate, you need URN and Session ID (to look up the session). Phone number is only needed to execute the resend (a later step, requiring L2 approval in the new model).

**Decision**: Required slots = {urn, session_id} for all topics except Agent Portal (needs agent_id) and API Callback (needs application_id, callback_type).

**Result**: Fewer clarification loops for the most common ticket types. Investigation can proceed as soon as URN+Session ID are provided.

### Decision 3: OTP L1 Is Investigation-Only (v3.0 Playbook)

**Rejected**: OTP L1 workflow that proposes and executes OTP resend.

**Reason**: OTP resend requires: (a) confirming the correct phone number, (b) selecting the channel, (c) knowing the carrier isn't blocking, (d) rate limit awareness. L1 doesn't have the full context to safely execute this. L1's job is to determine WHY the OTP failed (SMS gateway timeout? DND? Wrong number?) and hand off to L2 for actual resend.

**Decision**: OTP v3.0 playbook stops at RESOLVE_CASE (write investigation observations) without EXECUTE. L2 decides whether to resend.

### Decision 4: FreshdeskResponseService As Sole Write Path

**Rejected**: Ad-hoc calls to `FreshdeskClient.add_reply()` from anywhere in the codebase.

**Reason**: A previous implementation had notes being written from 3 different places in the code. This meant safety gates (closure guard, reply safety gate) were sometimes bypassed. An HTTP 422 from Freshdesk during a status update crashed the pipeline without explanation.

**Decision**: All Freshdesk writes go through `FreshdeskResponseService`. This service enforces ClosureFieldGuard before all status=4/5 writes and ReplySafetyGate before all public replies.

### Decision 5: Playbook-Driven Investigation (Not Hardcoded)

**Rejected**: Hardcoding which tools to call for each topic in Python.

**Reason**: The investigation sequence changes frequently as new Unity API endpoints become available and as we learn more about failure patterns. Hardcoded sequences require code changes.

**Decision**: Investigation sequences are defined in YAML playbook metadata (`investigation_steps`, `tool_candidates`). Adding a new investigation step = update YAML only.

### Decision 6: Multi-Tenant From Day One

**Rejected**: Unity-Bank-specific implementation.

**Reason**: KwikID serves 6+ banks. Each bank has its own admin portal, different API signatures, different authentication mechanisms, different custom field mappings. Building Unity-specific code would require rewrite for each new bank.

**Decision**: TenantContext abstraction from the beginning. All tool calls go through `TenantAPIRouter` which routes to the correct client implementation. Adding a new bank = implement new tool adapters + add to Tenant Registry.

## Important Discoveries

1. **Unity Bank auth header**: The Unity admin API uses `auth: <token>` not `Authorization: Bearer <token>`. This took debugging to discover and is a non-obvious difference from standard HTTP auth practices.

2. **Unity primary ID is phone_number, not URN**: The Unity portal has no concept of URN. To look up a customer's sessions, you must use their 10-digit phone number. URN is a KwikID application-layer concept that Unity's portal doesn't expose.

3. **Freshdesk Dispatch'r runs before AI**: The AI webhook fires AFTER Dispatch'r rules have already run. `cf_clients` is already set when the AI receives the ticket. The AI must NOT re-process this field.

4. **Observer 84000606270 auto-assigns tickets**: If the AI operates under a human agent's Freshdesk account, this rule immediately assigns incoming tickets to that agent, conflicting with the auto-assignment logic.

5. **No draft reply API in Freshdesk**: Any POST to `/reply` immediately sends an email. The "draft" pattern must use private notes with `cf_review_ticket=Yes`.

6. **asyncio.to_thread() is mandatory for blocking I/O**: The FastAPI server runs on an async event loop. Any blocking database call or HTTP call made directly in async context will starve the event loop under load.

## Design Principles (Immutable)

1. **Investigation before action**: Never propose an action before collecting evidence.
2. **Evidence before reasoning**: Never reason without evidence. Never invent evidence.
3. **Reasoning before execution**: Never execute without reasoning output.
4. **Verification after execution**: Always confirm action succeeded.
5. **Recovery after failure**: Never leave a ticket in a broken state.
6. **Audit everything**: Every decision, every transition, every tool call is logged.
7. **Human approval for REVERSIBLE and HIGH risk**: Never bypass the approval workflow.
8. **No component bypasses Action Gateway**: Not even the observation generator.
9. **Sole write path**: All Freshdesk writes through FreshdeskResponseService.
10. **PII protection**: NLPSignal.raw_text never logged. Phone numbers masked to last 4 digits.

---

# 14. Future Roadmap

## Immediate (Before Production, ~1 Week)

| Task | Owner | Description |
|---|---|---|
| Create Freshdesk Observer webhook rule | Admin | When customer replies → POST to /webhooks/freshdesk/ticket-updated |
| Set FRESHDESK_WEBHOOK_SECRET | Admin | Strong random key in both .env and Freshdesk |
| Extend Dispatch'r rule scope | Admin | Include Unity Bank real tickets in AI webhook scope |
| Create AI agent account | Admin | `ai.support@getkwikid.com` in Freshdesk |
| Deploy Supabase SQL migrations | DevOps | B1_007, B1_008 for hybrid retrieval RPCs |

## Wave 5 — Action Execution Completion

Complete the action execution pipeline for all 5 action types:
- OTP resend (via Unity API)
- VKYC session reset
- OCR reprocess
- Agent session refresh
- API callback retry

For each: implement executor, wire verification, implement recovery.

## Wave 6 — Clarification Loop Completion

After the Observer webhook is created:
- Complete the ticket-updated handler to resume conversation state
- Test the full: ticket → clarification → customer reply → resume → investigation → resolve
- Handle edge cases: customer replies multiple times, customer replies with wrong format

## Wave 7 — Asana L2 Integration

Complete the L2 escalation path:
- Asana task creation with full evidence package
- Freshdesk ticket linking
- Monitor for Asana completion event
- Update Freshdesk on resolution
- Auto-close ticket when Asana marks fixed

## Wave 8 — Additional Bank Clients

Extend beyond Unity Bank:
- Bank of Baroda (BOB) portal integration
- Central Bank of India integration
- RBL Bank integration
- Discovery → SOT docs → tool adapters → tenant registry entry → test → certify

Each bank requires its own discovery (what APIs exist, what auth, what fields map to what).

## Wave 9 — Multi-Session Correlation

For cases where a customer has had multiple failed sessions:
- Analyze all sessions for patterns
- Detect systematic failures (infrastructure issues vs user error)
- Include cross-session correlation in root cause analysis

## Wave 10 — Video Analysis

Use vision models to analyze recorded VKYC session video:
- Camera quality issues
- Lighting problems
- Document presentation issues
- Liveness check failures

## Level 3 (Long-Term, Only If Level 2 Hits Capacity)

Only begin when Level 2 throughput ceiling is actually reached:
- Kafka/MSK event bus for high-volume ticket processing
- Supervisor orchestrator for specialized workers
- Zero-copy connector to live banking CBS registry
- Full compliance trust layer with cryptographic audit trail
- Customer-facing conversational interface (requires legal sign-off)

---

# 15. Important Files

| File | Purpose | Importance | Mandatory Reading |
|---|---|---|---|
| `Source_Of_Truth/Architectural_truth/SUPPORT_OPERATIONS_BLUEPRINT.md` | Single source of truth for business rules, pipeline stages, enterprise principles | CRITICAL | YES |
| `Source_Of_Truth/Architectural_truth/flow_diagram.mermaid` | Canonical system architecture diagram | CRITICAL | YES |
| `Source_Of_Truth/Freshdesk_discovery/` | Freshdesk API facts, automation rules, webhook contracts, closure field rules | CRITICAL | YES |
| `Source_Of_Truth/Unity_discovery/` | Unity Bank portal API discovery: real endpoints, auth header, phone_number vs URN | CRITICAL | YES |
| `Source_Of_Truth/Metrics_discovery/` | Uptime Kuma API, Prometheus endpoints, monitor inventory | HIGH | YES |
| `case_engine/models.py` | Case dataclass, TopicKey enum, ClassificationResult | CRITICAL | YES |
| `case_engine/classifier.py` | LLM-only topic classifier, tier_used logic | HIGH | YES |
| `case_engine/nlp_router.py` | NLPRouter, NLPSignal, LLM call, ontology integration | HIGH | YES |
| `case_engine/topic_registry.py` | Required vs optional slots per topic — contains the Sprint 2.5.6 investigation-first fix | CRITICAL | YES |
| `case_engine/service.py` | CaseService: receive_message, slot filling, state transitions | CRITICAL | YES |
| `case_engine/clarification/engine.py` | ClarificationEngine: slot-based question generation | HIGH | YES |
| `case_engine/runtime/support_agent_runtime.py` | SupportAgentRuntime: main pipeline coordinator | CRITICAL | YES |
| `case_engine/ticket_orchestration/orchestrator.py` | TicketOrchestrator: webhook to runtime bridge | HIGH | YES |
| `case_engine/investigation/service.py` | InvestigationService: planner and coordinator | HIGH | YES |
| `case_engine/knowledge/service.py` | KnowledgeService: SOP retrieval integration | HIGH | YES |
| `case_engine/workflows/playbooks/*.yml` | 5 YAML playbooks — investigation sequences, action types, risk levels | HIGH | YES |
| `case_engine/workflows/playbook_registry.py` | PlaybookRegistry: loads and validates all YAML playbooks | HIGH | YES |
| `case_engine/workflows/workflow_engine.py` | WorkflowEngine: deterministic step executor | HIGH | YES |
| `case_engine/workflows/models.py` | WorkflowDefinition, WorkflowStep, WorkflowStepType, WorkflowExecutionResult | HIGH | YES |
| `case_engine/tools/adapters/unity_tools.py` | 5 Unity Bank API tool adapters (real production, not mocks) | HIGH | YES |
| `freshdesk/handlers.py` | Payload normalization, webhook processing | HIGH | YES |
| `freshdesk/responses.py` | FreshdeskResponseService — sole Freshdesk write path | CRITICAL | YES |
| `freshdesk/closure_guard.py` | ClosureFieldGuard — prevents HTTP 422 | HIGH | YES |
| `freshdesk/reply_safety_gate.py` | ReplySafetyGate — gates all public replies | HIGH | YES |
| `runtime/assembly.py` | RuntimeAssembly: wires all layers at startup | HIGH | YES |
| `intelligence/orchestrator.py` | IntelligenceOrchestrator: LLM reasoning pipeline | HIGH | YES |
| `.env.example` | All 60+ environment variables with descriptions | HIGH | YES |
| `api/routes/webhooks/freshdesk.py` | FastAPI webhook route handler | HIGH | YES |
| `Big_Phase_2_documentations/02_IMPLEMENTATION_ROADMAP.md` | Three-level maturity roadmap with gates | HIGH | YES |
| `Big_Phase_2_documentations/01_MASTER_ARCHITECTURE.md` | Original architecture design document | HIGH | YES |
| `docs/GOLDEN_PATH_ARCHITECTURE.md` | End-to-end golden path description | HIGH | YES |
| `docs/KNOWLEDGE_LAYER_FREEZE.md` | Knowledge layer v2.1 certification | MEDIUM | No |
| `docs/SPRINT_2_*_CTO_REPORT.md` | Sprint certification reports (Sprint 2.14–2.28) | MEDIUM | No |
| `docs_internal/PHASE_B1_FINAL_AUDIT.md` | Phase B1 knowledge ingestion final audit | MEDIUM | No |
| `tests/test_sprint1_classifier.py` | Classifier tests (uses mock NLPRouter) | MEDIUM | No |
| `tests/test_sprint215_case_service_slot.py` | Slot filling and CaseService tests | MEDIUM | No |
| `tests/test_sprint225_playbooks.py` | Full playbook structure certification tests | MEDIUM | No |
| `rag_engine/retrieval/hybrid_ticket_retriever.py` | Hybrid semantic+FTS+RRF retrieval | MEDIUM | No |
| `sql/b1_migrations/` | Supabase SQL migrations for FTS, RPC functions | HIGH | Before first deploy |
| `unity/` | Unity Bank integration package | HIGH | YES |
| `metrics_platform/` | Uptime Kuma integration package | MEDIUM | No |

---

# 16. Glossary

**Action Gateway**: The enterprise safety boundary that all action proposals must pass through before execution. Evaluates risk, routes to approval if needed, enforces idempotency. Defined in Blueprint §16.

**Agent** (in support context): A bank employee (e.g., Unity Bank agent) who uses the KwikID portal to conduct VKYC sessions with customers. Also the support agent at KwikID who handles Freshdesk tickets. Context determines which one is meant.

**Agent Portal**: The web application (`https://vkyc360.unitybank.co.in:9090`) that bank agents use to initiate, monitor, and review VKYC sessions. Different from the REST API backend.

**Aadhaar**: India's national biometric identity system. Customers present their Aadhaar card during KYC. The system OCRs the card and validates the extracted data. Aadhaar numbers are PII — must be masked in logs (show only last 4 digits).

**Application ID**: An identifier for a banking application transaction. Used in API callback failures.

**Asana**: Project management tool used by KwikID for engineering escalation tickets. When the AI creates an L2 escalation, it creates an Asana task and stores the URL in `cf_asana_ticket_link`.

**Auditor**: A bank employee (often a senior agent) who watches VKYC sessions live and approves or rejects them.

**Blueprint**: Short for `SUPPORT_OPERATIONS_BLUEPRINT.md`. The single source of truth for all business rules. When "Blueprint §X" is referenced in code comments, it refers to a section number in this document.

**CBS (Core Banking System)**: The bank's primary transaction processing system. KYC completion triggers a callback to CBS updating the customer's account status.

**cf_clients**: A Freshdesk custom field that identifies which bank client the ticket belongs to. Set by Dispatch'r rules before the AI sees the ticket. Valid values include "Unity", "BOB", "Others", etc.

**Clarification Loop**: The process of asking the ticket submitter for missing required information (URN, Session ID), waiting for their reply, then resuming the pipeline.

**ClosureFieldGuard**: A safety component (`freshdesk/closure_guard.py`) that validates all required Freshdesk closure fields are present before setting ticket status to Resolved (4) or Closed (5). Prevents HTTP 422.

**Dispatch'r Rules**: Freshdesk automation rules that fire synchronously when a ticket is created. KwikID has 30 active rules that set custom fields, assign groups, add tags, and fire webhooks. These run BEFORE the AI webhook.

**DMS (Document Management System)**: A system used by the bank to store KYC documents. DMS operations can fail during document upload/retrieval, causing callback failures.

**Domain**: In the Unity API context, this is the tenant identifier used for the `getAllUserSession` endpoint. For Unity Bank, the domain is "unity".

**EvidenceBundle**: The internal data model that carries all collected investigation evidence (session data, logs, API responses, metrics) through the investigation pipeline.

**Freshdesk**: The customer support platform (SaaS) used by KwikID. All tickets arrive via Freshdesk. The AI posts notes and replies via the Freshdesk API.

**FreshdeskResponseService**: The SOLE approved path for all Freshdesk writes. Located at `freshdesk/responses.py`. No code may bypass this service.

**KwikID**: The company/product. KwikID is a Video KYC SaaS platform used by Indian banks for remote customer identity verification.

**L1 (Level 1 Support)**: First-line support agents who handle initial ticket triage, investigation, and basic resolutions. This AI system automates L1 responsibilities.

**L2 (Level 2 Support)**: Escalation support / engineering bridge. Handles cases that require code fixes, infrastructure changes, or complex investigation. The AI automates some L2 responsibilities (escalation packaging, Asana task creation).

**Liveness Check**: A biometric step in the VKYC session where the customer must perform specific actions (blink, turn head, smile) to prove they are a real, present person and not a photo or video.

**NLPSignal**: A structured dataclass returned by the NLPRouter. Contains: intent, nested_case, entities, negation_detected, confidence, needs_clarification, clarification_question, raw_text.

**NLPRouter**: The LLM-based semantic router (`case_engine/nlp_router.py`) that transforms raw ticket text into NLPSignal. Uses OpenAI GPT-4o-mini with structured JSON output.

**Observer Rules**: Freshdesk automation rules that fire when a ticket is UPDATED (as opposed to Dispatch'r which fires on create). Used for auto-reopen on customer reply, auto-assignment. The MISSING customer-reply webhook is an Observer rule gap.

**OCR (Optical Character Recognition)**: The process of extracting text from scanned/photographed documents. Used to extract Aadhaar number, PAN number, name, DOB from document images during KYC.

**OTP (One-Time Password)**: A numeric code sent to the customer's phone (SMS/email/voice) at the start of the KYC process to verify their mobile number. OTP failures are one of the 5 main support topics.

**PAN Card**: India's Permanent Account Number card. A government-issued tax ID card. Presented during KYC for OCR verification. PAN numbers are PII.

**Playbook**: A YAML file that defines the exact workflow for a specific support topic. Contains: steps, required_slots, investigation_steps, tool_candidates, resolution_paths.

**PlaybookRegistry**: The component (`case_engine/workflows/playbook_registry.py`) that loads and validates all YAML playbooks at startup.

**ReplySafetyGate**: A safety component (`freshdesk/reply_safety_gate.py`) that evaluates whether the AI's confidence is sufficient to send a public reply to the customer. Below threshold → post as draft private note instead.

**RRF (Reciprocal Rank Fusion)**: The algorithm used to combine rankings from semantic retrieval and FTS retrieval into a single merged ranking.

**Session ID**: UUID v4 identifying a specific VKYC session. Critical for investigation — every tool call to investigate a session requires this ID.

**SFDC (Salesforce)**: Salesforce CRM. KYC completion triggers webhooks to SFDC for lead/application status updates. SFDC webhook failures are one of the API callback failure sub-types.

**Slot**: A named piece of information required for investigation. Examples: urn, session_id, agent_id, application_id. Slots are defined per topic in `topic_registry.py`.

**Slot State**: A dict stored on the Case object tracking which slots have been filled (FILLED), are missing (EMPTY), are invalid (INVALID), or exceeded max attempts (ESCALATED).

**SOT / Source_Of_Truth**: The `Source_Of_Truth/` directory at the repository root. Contains architectural blueprints and discovery documents for Freshdesk, Unity, and Metrics platforms. These are the authoritative reference for how external systems actually work (as discovered by actual API calls and admin panel investigation).

**SOP (Standard Operating Procedure)**: A documented step-by-step procedure for handling a specific type of support issue. SOPs are stored in the StackOverflow Teams export and indexed in Supabase pgvector.

**StackOverflow Teams**: KwikID's internal Q&A/wiki platform. Engineers and support staff document SOPs, fixes, runbooks, and known issues here. The knowledge base is built from this export.

**Supabase**: The PostgreSQL-based cloud database used by this system. Provides: pgvector for semantic search, standard SQL for case management, and FTS for hybrid retrieval.

**TenantContext**: An object carrying all client-specific configuration: which bank, which portal URL, API credentials reference, enabled tools, workflow overrides. Created by the Client Resolution Engine and attached to the Case.

**TopicKey**: The Python enum (`case_engine/models.py`) with values: OTP_DELIVERY_FAILURE, VKYC_SESSION_FAILURE, DOCUMENT_OCR_FAILURE, AGENT_PORTAL_ISSUE, API_CALLBACK_FAILURE, UNKNOWN.

**URN (Unique Reference Number)**: A customer-level identifier used in KwikID's application system to track a customer's KYC application. Format varies by bank. Important: URN does NOT exist in the Unity Bank admin portal — the Unity portal uses phone_number instead.

**VKYC / Video KYC**: Video Know Your Customer. The core business process: a live video call between a customer and a bank system where the customer's identity is verified via biometrics, document scan, and auditor review.

**WorkflowConsistencyChecker**: A component that validates state machine invariants and prevents double-starts or invalid state transitions.

**WorkflowDefinition**: The Python dataclass representation of a loaded YAML playbook. Contains all steps, metadata, required slots, and investigation configuration.

**WorkflowEngine**: The step executor (`case_engine/workflows/workflow_engine.py`) that reads a WorkflowDefinition and executes steps in order, dispatching to the correct subsystem for each step type.

---

# Files the New AI Should Read In Addition to This Document

Before beginning any development, read these files in order:

## Must Read (Day 1)

1. `Source_Of_Truth/Architectural_truth/SUPPORT_OPERATIONS_BLUEPRINT.md` — Business rules. Nothing overrides this.
2. `Source_Of_Truth/Architectural_truth/flow_diagram.mermaid` — System architecture. Canonical.
3. `Source_Of_Truth/Freshdesk_discovery/` — Every file in this directory. Freshdesk API, automation rules, closure fields, webhook contracts.
4. `Source_Of_Truth/Unity_discovery/` — Every file in this directory. Unity Bank API, auth header, phone_number vs URN.
5. `.env.example` — All 60+ configuration variables.
6. `Big_Phase_2_documentations/01_MASTER_ARCHITECTURE.md` — Master architecture design.
7. `Big_Phase_2_documentations/02_IMPLEMENTATION_ROADMAP.md` — Three-level maturity roadmap.
8. `case_engine/models.py` — Case object, TopicKey, ClassificationResult.
9. `case_engine/topic_registry.py` — Slot definitions (investigation-first rule).
10. `case_engine/runtime/support_agent_runtime.py` — Main pipeline coordinator.

## Must Read (Day 2)

11. `freshdesk/responses.py` — FreshdeskResponseService. Sole write path.
12. `freshdesk/handlers.py` — Webhook handling.
13. `case_engine/nlp_router.py` — NLP Semantic Router.
14. `case_engine/service.py` — CaseService.
15. `case_engine/workflows/playbooks/*.yml` — All 5 YAML playbooks.
16. `case_engine/workflows/workflow_engine.py` — WorkflowEngine.
17. `case_engine/tools/adapters/unity_tools.py` — Unity tool adapters.
18. `runtime/assembly.py` — How everything is wired at startup.
19. `docs/GOLDEN_PATH_ARCHITECTURE.md` — Golden path description.
20. `docs_internal/PHASE_B1_FINAL_AUDIT.md` — Knowledge layer audit.

## Read Before Any Freshdesk Work

21. `freshdesk/closure_guard.py` — ClosureFieldGuard.
22. `freshdesk/reply_safety_gate.py` — ReplySafetyGate.
23. `freshdesk/idempotency.py` — Idempotency store.

## Read Before Any Test Work

24. `tests/test_sprint1_classifier.py` — How to mock NLPRouter in tests.
25. `tests/test_sprint215_case_service_slot.py` — CaseService test patterns.
26. `tests/test_sprint225_playbooks.py` — Playbook test patterns and V2_TOPICS split.
27. `tests/test_sprint217_playbook_evolution.py` — Playbook evolution test patterns.

---

*End of PROJECT_CONTEXT.md*

*This document was generated on 2026-07-21 after reading the full repository. The authoritative sources for any conflict are the SOT documents in `Source_Of_Truth/` and the SUPPORT_OPERATIONS_BLUEPRINT.md.*
