# Freshdesk Source of Truth — Index

**Instance**: kwikid.freshdesk.com  
**Audit date**: 2026-06-18 (live production audit)  
**Generated**: 2026-07-10  
**Purpose**: Complete engineering reference for implementing Freshdesk integration in the KwikID Support Automation Platform (Sprint 2.28+). Every document in this directory was produced from live Freshdesk data — no assumptions, no placeholders for values that could be retrieved.

---

## Document Index

### Core Architecture

**[architecture.md](./architecture.md)**  
Complete Freshdesk platform overview, Golden Path architecture, system components, module hierarchy, admin hierarchy (5 groups, 24 agents), API architecture, automation rule execution model, tenant resolution architecture, security model, and integration rollout phases (1–5).  
_Start here for understanding how Freshdesk fits into the overall system._

**[ticket_lifecycle.md](./ticket_lifecycle.md)**  
Full ticket lifecycle from creation through closure, all 11 status values and their SLA clock behavior, state machine diagram, events that trigger automations (creation, update, hourly), required fields for closure (HTTP 422 prevention), priority model, ticket types, and group routing rules.  
_Start here when implementing ticket state transitions._

---

### Webhook Integration

**[webhook_contract.md](./webhook_contract.md)**  
Complete webhook contract: the 10-second timeout constraint, HMAC-SHA256 authentication specification, ticket creation payload structure (full Freshdesk-native JSON), ticket update payload (3 scenarios: customer reply, agent reply, internal note), comment type detection rules, field normalization mapping, pre-filter logic, missing Observer rule (BLOCKING), and all active/inactive webhook targets.  
_Start here when implementing the webhook receiver._

**[webhook_payload.json](./webhook_payload.json)**  
Machine-readable webhook payload examples: ticket creation event, ticket update event (3 scenarios), live Dispatch'r rule JSON for both "AI auto replies" and "AI_AUTOMATION_TEST_CREATED" rules, full list of template placeholders, FastAPI internal normalized model, and normalization mapping.  
_Import this for test fixtures and parser validation._

---

### API Reference

**[api_reference.md](./api_reference.md)**  
Authentication (Basic Auth, API key format), rate limits (40 req/min account-wide; AI self-limit 30 req/min), all ticket endpoints (GET, PUT, list, search), conversation endpoints (notes, replies, list conversations), agent and group endpoints, complete error code reference (200–503), key field reference for writes (status values, priority values, valid ticket_type values, custom field write reference), `FreshdeskClient` implementation requirements.  
_Start here when implementing API client code._

---

### Ticket Data Schema

**[ticket_schema.json](./ticket_schema.json)**  
Complete ticket object model: all 20 standard fields with field IDs, types, and required flags; all 43 custom fields organized by functional domain; 5 group definitions with IDs; all 11 status values; 4 priority values; 8 ticket types with non-standard value warning; 19 source channels; required fields for closure; example complete ticket JSON.  
_Import this as the authoritative field reference for API client implementation._

**[custom_fields.json](./custom_fields.json)**  
All 43 custom (`cf_*`) fields with: API name, field ID, type, required_for_closure, required_for_agents, AI scope (`WRITE`/`READ_ONLY`/`READ_WRITE`/`WRITE_WITH_CAUTION`/`DO_NOT_WRITE`/`DO_NOT_TOUCH`), notes, and complete value lists for all dropdowns. Includes the full 67-value `cf_query_type` list and 42-value `cf_clients` list.  
_Start here when implementing field classification logic._

---

### Communication Strategy

**[notes_and_replies.md](./notes_and_replies.md)**  
Complete communication strategy: why there is no draft reply API, four communication types (customer reply, public agent reply, internal note, public note), private note endpoint and payload, draft approval pattern (note as staging area), public reply endpoint and payload, resolution/clarification/escalation message structures, complete action sequences for resolution + clarification + escalation, conversation retrieval and customer reply detection, CC email strategy, body formatting requirements.  
_Start here when implementing note and reply posting._

---

### Automation Rules

**[automation_rules.md](./automation_rules.md)**  
All 30 Dispatch'r rules (full table with positions, conditions, actions; detailed breakdown of the 2 AI-critical rules), all 10 Observer rules (4 active, 6 inactive with IDs and conditions), all 4 Supervisor rules, 5 automation conflict analyses, AI conflict zone matrix, all active webhook targets, complete tag reference table with pre-filter logic.  
_Start here when auditing automation conflicts or understanding what Freshdesk does before the AI sees a ticket._

---

### Workflow and Integration Design

**[workflow_discovery.md](./workflow_discovery.md)**  
How the AI integrates with Freshdesk's automation model, pre-AI processing (what Dispatch'r rules have already done), AI webhook trigger rules (current scope gaps), tag pre-filter table, AI conflict zones (first-responder assignment, pre-assigned agents, auto-close rules, duplicate webhooks, non-standard ticket_type values), Observer rule architecture (active rules, missing customer-reply rule), complete clarification loop flow (8-step sequence with code), Supabase conversation_state schema and status values, tenant resolution decision tree (with email domain → tenant mappings), AI ticket processing decision tree, action safety matrix summary, and prioritized gap list.  
_Start here when implementing the clarification loop or the Golden Path pipeline._

---

### Gap Analysis and Readiness

**[observations.md](./observations.md)**  
AI integration readiness assessment (capability-by-capability), Asana integration readiness assessment, complete gap analysis (4 BLOCKING gaps + 4 non-blocking gaps), full risk register (15 risks with severity and mitigation), architecture quality observations (what is well-designed vs. what needs attention), structural constraints the AI must never violate, and the complete production readiness checklist.  
_Start here when planning Sprint 2.28 pre-production work, or for QA/security review._

---

## Quick Reference: Blocking Issues Before Production

These four items must be resolved before any real Unity Bank production traffic can flow:

1. **Create Observer rule: customer reply webhook** — `When Reply is sent → By Requester` → POST to AI update endpoint. Without this, the clarification loop is broken.

2. **Set `FRESHDESK_WEBHOOK_SECRET`** — HMAC-SHA256 webhook authentication is currently disabled. Set the env var and enforce verification.

3. **Extend "AI auto replies" Dispatch'r rule scope** — currently matches only 2 test email addresses + `cf_clients="Others"`. Real Unity Bank tickets are NOT reaching the AI.

4. **Provision dedicated AI agent account** — AI must not use human agent credentials. Observer rule 84000606270 will assign ticket ownership to human agents' accounts when the AI acts on a ticket.

---

## Key Facts at a Glance

| Fact | Value |
|:---|:---|
| Freshdesk instance | kwikid.freshdesk.com |
| API base URL | `https://kwikid.freshdesk.com/api/v2/` |
| Authentication | Basic Auth — API key + `"X"` |
| Rate limit | 40 req/min account-wide; AI self-limit: 30 req/min |
| Webhook timeout | 10 seconds — must return 200 OK immediately |
| Webhook auth header | `X-Webhook-Token` (HMAC-SHA256) |
| Total custom fields | 43 (`cf_*`) |
| AI-writable custom fields | 12 |
| Groups | 5 (L1 ID: 84000293343; L2 ID: 84000293342) |
| Statuses | 11 (Open=2, Pending=3, Resolved=4, Closed=5) |
| Dispatch'r rules | 30 active |
| Observer rules | 10 total; 4 active |
| Supervisor rules | 4 total; 2 active |
| Sprint 2.28 tenant scope | Unity only (`*@unitybank.co.in`) |
| AI webhook target | `https://n8n.app.getkwikid.com/webhook/freshdesk-ticket-created` |
| Closure required fields | `cf_clients`, `ticket_type`, `cf_sop_status`, `cf_resolution_classification` |
| HMAC secret env var | `FRESHDESK_WEBHOOK_SECRET` |
| Supabase state table | `conversation_state` |

---

## Discovery Objectives Completion

| Objective | File(s) | Status |
|:---|:---|:---:|
| 1. Complete Freshdesk architecture | architecture.md | ✅ |
| 2. Ticket lifecycle and state machine | ticket_lifecycle.md | ✅ |
| 3. Full ticket field inventory | ticket_schema.json, custom_fields.json | ✅ |
| 4. Automation rules — complete inventory | automation_rules.md | ✅ |
| 5. AI_AUTOMATION_TEST_CREATED + AI auto replies rules in detail | automation_rules.md, webhook_payload.json | ✅ |
| 6. Webhook payload structures | webhook_contract.md, webhook_payload.json | ✅ |
| 7. Observer webhook architecture + clarification loop | workflow_discovery.md, webhook_contract.md | ✅ |
| 8. Notes API strategy | notes_and_replies.md | ✅ |
| 9. Customer reply API strategy | notes_and_replies.md | ✅ |
| 10. Draft pattern (no native draft API) | notes_and_replies.md | ✅ |
| 11. API reference (auth, rate limits, endpoints, errors) | api_reference.md | ✅ |
| 12. Webhook integration readiness | webhook_contract.md, observations.md | ✅ |
| 13. Asana readiness | observations.md | ✅ |
| 14. AI integration readiness | observations.md, workflow_discovery.md | ✅ |
| 15. Gap analysis | observations.md | ✅ |
| 16. Risk register and observations | observations.md | ✅ |
