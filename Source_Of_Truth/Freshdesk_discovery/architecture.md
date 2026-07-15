# Freshdesk Architecture — KwikID Support Automation Platform

**Source**: Live Freshdesk production audit 2026-06-18 (kwikid.freshdesk.com)  
**Instance**: kwikid.freshdesk.com  
**Audit date**: 2026-06-18  
**Scope**: 54 active ticket fields, 5 groups, 24 agents, 30 Dispatch'r rules, 10 Observer rules, 4 Supervisor rules, 4 configured webhooks.

---

## 1. Platform Overview

Freshdesk is the **Customer Communication Layer** of the KwikID Support Automation Platform. It is the single universal entry point through which every support ticket — across all 42 enterprise tenants (Unity, BOB, CBI, RBL, BajajFin, ThomasCook, Canara Bank, FINO, Toyota, and 30+ others) — enters the system. It is also the single channel through which every customer-facing communication (reply, note, status change) leaves the system.

**Freshdesk does NOT perform reasoning, retrieval, or decision-making.** It is the mailbox, the ticket database, and the rules engine for routing and bookkeeping. All intelligence lives downstream in the Support Agent Runtime.

Freshdesk's three jobs in this architecture:
1. **Ingest** — receive tickets from email, portal, phone, and other channels; apply Dispatch'r rules to do first-pass tenant/group/priority classification; fire a webhook so the runtime knows a ticket exists.
2. **Hold state** — store ticket status, custom fields, conversation history, and SLA timers as the canonical record of where a ticket is in its lifecycle.
3. **Communicate** — accept API calls from the Execution Layer to post public replies, private notes, and field/status updates, and to notify the runtime (via Observer webhooks) when the customer responds.

---

## 2. Golden Path Architecture

Every support ticket processed by the KwikID Support Agent Runtime must travel this exact path and no other:

```
Freshdesk
    ↓
Webhook Receiver
    ↓
Client Resolution Layer
    ↓
Ticket Orchestrator
    ↓
Support Agent Runtime
    ↓
Workflow Engine
    ↓
Investigation Planner
    ↓
Evidence Collection
    ↓
Knowledge Retrieval
    ↓
Reasoning Engine
    ↓
Safety Guardrails
    ↓
Action Gateway
    ↓
Execution Layer
    ↓
Freshdesk / Asana / Support APIs
```

### Layer responsibilities

| Layer | Responsibility | Must NOT |
|:---|:---|:---|
| Webhook Receiver | HMAC verification, normalization, idempotency check, return `200 OK`, enqueue background task | Block on LLM/RAG work |
| Client Resolution Layer | Resolve requester email → tenant, build Tenant Context, attach to Case | Continue if tenant is UNKNOWN |
| Ticket Orchestrator | Create/load Case object, manage lifecycle state transitions | Bypass Tenant Context |
| Support Agent Runtime | Orchestrate the full pipeline for one ticket/case | Skip Safety Guardrails |
| Workflow Engine | Select the correct playbook based on topic classification | Call external APIs |
| Investigation Planner | Determine what evidence to collect and in what order | Invent results |
| Evidence Collection | Call Support Admin APIs, retrieve session/user data | Call production write APIs |
| Knowledge Retrieval | Semantic + keyword SOP search, RRF+BM25 ranking | Replace investigation with SOP text |
| Reasoning Engine | Root cause analysis, confidence scoring, action recommendation | Execute actions |
| Safety Guardrails | Confidence threshold check, risk classification, approval routing | Approve high-risk actions without human sign-off |
| Action Gateway | Enterprise safety boundary — the ONLY path to external writes | Be bypassed |
| Execution Layer | Call Freshdesk/Asana/Support APIs with approved actions | Call without Action Gateway approval |

### What the Golden Path explicitly excludes

The following must never be invoked directly as primary handlers:
- **Legacy RAG endpoints** — any route that calls a retrieval pipeline without first passing through Webhook Receiver → Client Resolution → Ticket Orchestrator.
- **Knowledge utilities / search endpoints** — the Knowledge Layer exists to serve the Investigation Planner, not to answer customer questions directly.
- **Testing endpoints** — routes that exist for development/QA purposes and bypass Safety Guardrails or Action Gateway.
- **Direct Freshdesk API calls from non-Execution-Layer code** — no component upstream of the Action Gateway is permitted to call Freshdesk write endpoints (`/reply`, `/notes`, `PUT /tickets/{id}`).

---

## 3. System Architecture Diagram

```
Customer: Email / Portal / Phone
    ↓  (Ticket Created)
Freshdesk [kwikid.freshdesk.com]
    ↓  (Dispatch'r Rules: tenant + group + priority classification)
Freshdesk [internal rules engine]
    ↓  (Webhook POST, 10s timeout)
n8n Orchestrator [n8n.app.getkwikid.com]
    ↓  (HMAC-SHA256 signed POST, X-Webhook-Token header)
FastAPI / Support Agent Runtime
    ↓  (200 OK immediately, then background task)
Client Resolution Layer
    ↓
Ticket Orchestrator
    ↓
Workflow Engine → Investigation → Knowledge → Reasoning
    ↓  (Approved action)
Action Gateway
    ├── POST /reply → Freshdesk (customer-facing)
    ├── POST /notes (private:true) → Freshdesk (agents-only)
    ├── PUT custom_fields + status → Freshdesk
    ├── Escalation task → Asana
    └── Operational action → Support Admin APIs
```

Observer webhook loop (customer reply):
```
Customer replies → Freshdesk detects reply
    ↓  (Observer rule fires)
n8n Orchestrator
    ↓  (HMAC signed POST to /webhooks/freshdesk/ticket-updated)
FastAPI / Support Agent Runtime
    ↓  (Loads conversation_state from Supabase)
Clarification resume pipeline
    ↓
Action Gateway → Execution Layer → Freshdesk
```

---

## 4. Core Architecture Components

| Component | Role | Location |
|:---|:---|:---|
| **Freshdesk** | Customer communication layer. Ticket database, rules engine, SLA clock, agent UI. | kwikid.freshdesk.com |
| **n8n Orchestrator** | Entry buffer that absorbs the Freshdesk 10-second webhook timeout, computes HMAC signature, forwards to FastAPI. Target end-state. | n8n.app.getkwikid.com |
| **FastAPI / Support Agent Runtime** | Owns all reasoning. Receives webhook, returns `200 OK` immediately, then runs Golden Path as background task. | FastAPI application |
| **Client Resolution Layer** | Resolves requester email → Tenant Context. First thing that runs inside background task after idempotency check. | `ClientResolver` component |
| **Ticket Orchestrator** | Creates and manages the Case object; drives state machine transitions throughout ticket lifecycle. | `TicketOrchestrator` component |
| **Knowledge Layer** | Hybrid semantic (pgvector) + keyword (FTS) retrieval over StackOverflow Teams SOP content, ranked with RRF and BM25. | Supabase pgvector + FTS |
| **Investigation Layer** | Calls tenant-specific Support Admin APIs via Tool Registry to pull session/case-level operational data. | Tool Registry execution |
| **Reasoning Engine** | Root cause analysis, runs confidence/safety gate. | LLM-driven pipeline |
| **Action Gateway** | Single enterprise safety boundary. All external writes flow through it. | `ActionGateway` component |
| **Execution Layer** | Makes actual API calls to Freshdesk, Asana, Support Admin APIs after Action Gateway approves. | `ExecutionLayer` component |
| **Supabase** | Persistent state for clarification loop (`conversation_state` table), tool execution logs. | Supabase instance |
| **Asana** | Engineering escalation destination when a ticket requires developer action. | Asana project |

---

## 5. Freshdesk Module Hierarchy

### 5.1 Freshdesk modules in use

| Module | Used | Notes |
|:---|:---:|:---|
| Tickets | ✅ | Core support ticket management |
| Conversations (Notes + Replies) | ✅ | Communication thread, API-accessible |
| Dispatch'r Rules (automation) | ✅ | 30 active rules; ticket creation triggers |
| Observer Rules (automation) | ✅ | 10 rules (4 active); ticket update triggers |
| Supervisor Rules (automation) | ✅ | 4 rules (2 active); time-based triggers |
| Webhooks | ✅ | 4 configured targets; HMAC-SHA256 signing |
| Custom Fields | ✅ | 43 custom fields (cf_*) in use |
| Groups | ✅ | 5 active support groups |
| Agents | ✅ | 24 configured agents |
| Companies | ✅ | Multi-tenant company records |
| Tags | ✅ | Automation-managed classification tags |
| SLA Policies | ✅ | `due_by`, `fr_due_by` driven by group+priority |
| Field Service Management (FSM) | ❌ | Module present but 6 FSM fields are DO NOT TOUCH |
| CSAT / Satisfaction Surveys | Not audited | |
| Reports / Analytics | Not audited | |

### 5.2 Admin hierarchy

```
Freshdesk Account (kwikid.freshdesk.com)
├── Groups (5)
│   ├── L1 [84000293343] — frontline triage
│   ├── L2 [84000293342] — engineering escalation
│   ├── Tech Assign [84000293351] — platform/development
│   ├── Business Analyst [84000294040] — requirements, CRs
│   └── General [84000293360] — alerts, reports
├── Agents (24)
│   └── [dedicated AI agent account required — not yet provisioned at audit]
├── Custom Fields (43 cf_* fields)
├── Ticket Types (8 configured)
├── Ticket Statuses (11)
├── Priorities (4)
├── Sources (19)
└── Automation Rules
    ├── Dispatch'r (30 active) — fire on ticket creation
    ├── Observer (10 total, 4 active) — fire on ticket updates
    └── Supervisor (4 total, 2 active) — fire on schedule (hourly)
```

---

## 6. API Architecture

### 6.1 Freshdesk REST API v2

- **Base URL**: `https://kwikid.freshdesk.com/api/v2/`
- **Authentication**: HTTP Basic Auth — API key as username, `"X"` as password (literal)
  ```
  Authorization: Basic base64(API_KEY:X)
  ```
- **Content-Type**: `application/json`
- **Rate limit**: **40 requests/minute** account-wide (confirmed via `x-ratelimit-total: 40.0`)
- **AI self-limit**: **30 requests/minute** (safety margin preserving human agent headroom)

### 6.2 Key API endpoints

| Endpoint | Method | Purpose |
|:---|:---|:---|
| `/tickets/{id}` | `GET` | Retrieve a ticket and all its fields |
| `/tickets/{id}` | `PUT` | Update ticket fields, status, priority, group, custom fields |
| `/tickets/{id}/reply` | `POST` | Send a public customer-facing reply (SENDS EMAIL IMMEDIATELY) |
| `/tickets/{id}/notes` | `POST` | Add a note (private or public) |
| `/tickets/{id}/conversations` | `GET` | Retrieve full conversation thread (notes + replies) |
| `/tickets` | `GET` | List/search tickets (with filters) |
| `/tickets` | `POST` | Create a new ticket |
| `/agents` | `GET` | List all agents |
| `/groups` | `GET` | List all groups |

### 6.3 Custom field write format

Custom fields are **always** nested under `custom_fields` when writing via `PUT /api/v2/tickets/{id}`. They appear flat under `ticket_custom_fields` only inside webhook payloads — never use `ticket_custom_fields` as the write key:

```json
PUT /api/v2/tickets/197416
{
  "custom_fields": {
    "cf_sop_status": "SOP Present",
    "cf_resolution_classification": "Solved by SOP (Temporary Workaround)",
    "cf_rca_status": "No RCA Needed"
  }
}
```

---

## 7. Automation Architecture

### 7.1 Dispatch'r rules (Type ID 1) — ticket creation

- Fire exactly once when a ticket is created
- Execute in ascending position order (position 1 fires before position 30)
- Each rule evaluates conditions; if all conditions match, all actions execute
- **30 active rules** on this instance
- Primary AI entry point: **"AI auto replies"** rule (position 2, ID 84000621616)

### 7.2 Observer rules (Type ID 4) — ticket updates

- Fire when a specified update event occurs on any matching ticket
- **10 rules total: 4 active, 6 inactive**
- Critical active rules: auto-assign first-responder (84000606270), auto-reopen on customer reply (84000606271)
- **MISSING**: No active Observer rule fires a webhook on customer reply — required for the clarification loop

### 7.3 Supervisor rules (Type ID 3) — time-based

- Run hourly over all matching open tickets
- **4 rules total: 2 active, 2 inactive**
- Key active rule: Re-assign L2 → L1 after client reopens and 1h passes (84000607317)

### 7.4 Automation rule execution model

```
Ticket Created
    → All Dispatch'r rules evaluated (position order)
    → Matching rules execute actions in order

Ticket Updated
    → All active Observer rules checked against event type
    → Matching rules execute actions

Hourly timer fires
    → All active Supervisor rules scan all matching tickets
    → Execute actions on matching tickets
```

---

## 8. Tenant Resolution Architecture

### 8.1 Resolution priority order

1. **`cf_clients` field** — set by Dispatch'r rules before AI sees the ticket; treat as authoritative if present and recognized
2. **Requester email domain** — `unitybank.co.in` → UNITY, `bankofbaroda.com` → BOB, `bajajfinserv.in` → BAJAJ_FIN, etc.
3. **CC email domains** — same matching logic applied to `cc_emails`
4. **Subject-line keywords** — last-resort fallback; collision-prone, not reliable at scale

**Rule**: If `ClientResolver` returns `UNKNOWN_TENANT`, pipeline stops, audit event created, ticket routed to human review.

### 8.2 Current production scope

**Sprint 2.28: Unity only** (`*@unitybank.co.in`)

### 8.3 Dispatch'r rules that pre-resolve cf_clients

Freshdesk Dispatch'r rules match requester/CC email domain and set `cf_clients` before AI sees the ticket:
- "Assign tickets to Unity" → `cf_clients = Unity`
- "Assign tickets to BOB as Priority" → `cf_clients = BOB`
- "Assign tickets to RBL" → `cf_clients = RBL`
- *(one rule per client — see automation_rules.md)*

The AI treats `cf_clients` as **read-only** and does not overwrite a value already set.

---

## 9. Security Model

| Control | Status at Audit | Required State |
|:---|:---|:---|
| HMAC-SHA256 webhook authentication | **NOT SET** — `FRESHDESK_WEBHOOK_SECRET` unset | Set and enforced |
| Dedicated AI agent account | **NOT PROVISIONED** — AI using human credentials | `ai.support@getkwikid.com` (or equivalent) |
| Rate limiting | Not confirmed in code | 30 req/min self-imposed ceiling |
| PII handling | Not confirmed | All ticket content treated as sensitive financial-services data |
| Audit logging | Freshdesk activity history + Supabase | Required for each AI write action |
| Token security (Support Admin API) | TTL ~15 min, no caching per spec | Never log, cache, or share across tenants |

---

## 10. Ownership Boundaries

### Freshdesk owns
- The ticket record: ID, status, priority, group, agent assignment, all custom field values, tags, conversation thread
- SLA timers (`due_by`, `fr_due_by`) and their pause/resume behavior tied to status
- Tenant-routing Dispatch'r rules that run on ticket creation
- Observer rules that react to ticket updates
- Supervisor rules that run hourly
- The only interface human support agents use day-to-day

### Support Agent Runtime owns
- All reasoning: retrieval, root cause analysis, confidence scoring, safety-gate evaluation
- The decision of *what* to write back to Freshdesk and *whether* it is safe to do so
- Webhook signature verification (HMAC-SHA256 over `X-Webhook-Token`)
- Tenant context (resolved by Client Resolution Layer)
- Its own state store (Supabase) for clarification conversation state
- Rate-limit budgeting against the Freshdesk account-wide ceiling

### n8n owns (target end-state; not yet fully built)
- Buffering the Freshdesk webhook so FastAPI never has to respond within Freshdesk's 10-second window directly
- HMAC signature generation for outbound calls to FastAPI
- Any batching, filtering, or future multi-step orchestration logic

### Explicitly NOT Freshdesk's job
- Freshdesk has no concept of "draft" replies, no LLM, and no knowledge of SOP content. Any feature that sounds like "let the AI draft inside Freshdesk and a human approves" must be built as a **private note**.

---

## 11. Integration Rollout Phases

| Phase | Scope | Status |
|:---|:---|:---|
| Phase 1 (Sprint 2.28) | Freshdesk → FastAPI. Notes, replies, field/status updates, Asana basic escalation. Unity only, no Unity Admin API calls. | Current |
| Phase 2 | FastAPI + Unity Admin APIs (get_user, get_session, resend_otp, retry_ocr) | Future |
| Phase 3 | Full Asana L2 escalation loop with engineering fix closure back to Freshdesk | Future |
| Phase 4 | n8n as full webhook buffer; HMAC generation moves to n8n | Future |
| Phase 5 | Support Operations UI — approval workflow, AI decision visibility, human-in-the-loop queue | Future |
