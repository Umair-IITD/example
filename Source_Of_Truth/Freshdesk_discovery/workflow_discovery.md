# Freshdesk Workflow Discovery — AI Automation Patterns

**Source**: Live Freshdesk production audit 2026-06-18 (kwikid.freshdesk.com)  
**Covers**: Automation rules interaction with AI workflow, Observer webhook architecture, conversation state detection, clarification loop design

---

## 1. How the AI Workflow Integrates with Freshdesk

### 1.1 Interaction model

The AI system does NOT run inside Freshdesk. It is an external runtime that:
1. **Receives events** from Freshdesk via webhooks (ticket created, customer replied)
2. **Reads data** from Freshdesk via REST API (ticket fields, conversation history)
3. **Writes results** back to Freshdesk via REST API (notes, replies, field updates, status changes)

Freshdesk's automation rules (Dispatch'r, Observer, Supervisor) run independently on the Freshdesk side before and after AI processing. The AI must not conflict with them.

### 1.2 Two entry points

| Entry Point | Trigger | Handler |
|:---|:---|:---|
| **New ticket** | Dispatch'r rule fires on ticket creation → webhook to n8n → FastAPI | Full Golden Path pipeline |
| **Customer reply** | Observer rule fires when customer replies → webhook to n8n → FastAPI | Clarification resume pipeline |

---

## 2. Pre-AI Processing (Dispatch'r Rules)

Before the AI ever sees a ticket, Freshdesk Dispatch'r rules have already:

1. **Identified the tenant** — set `cf_clients` (e.g., `"Unity"`) based on requester/CC email domain, company name, or subject keywords
2. **Assigned to a group** — set `group_id` to L1 (84000293343) for most support tickets
3. **Set a default agent** — pre-assigned a named human agent
4. **Set ticket type** — usually `"Issues"`
5. **Added classification tags** — e.g., `auto_assigned_client`, `auto_client_assign_as_unity`
6. **Fired the AI webhook** — for tickets matching the "AI auto replies" rule scope

The AI must treat all Dispatch'r-set fields as **ground truth** and not overwrite them:
- `cf_clients` — READ ONLY for AI
- `cf_environment` — READ ONLY for AI (customer-reported, do not change)
- `group_id` — do not change from L1 unless escalating

---

## 3. AI Webhook Trigger Rules

### 3.1 Current production rule: "AI auto replies"

| Property | Value |
|:---|:---|
| Rule ID | 84000621616 |
| Position | 2 (fires very early) |
| Active | ✅ Yes |
| Conditions | `from_email is umair.alam@think360.ai` OR `from_email is dnyaneshwar.shekade@think360.ai` OR `cf_clients in ["Others"]` |
| Webhook target | `https://n8n.app.getkwikid.com/webhook/freshdesk-ticket-created` |
| Payload | `{ticket:{id,subject,description}, replyVisibility:"private"}` |

**⚠️ CRITICAL GAP**: This rule DOES NOT fire for real Unity Bank tickets (`cf_clients = "Unity"`). For Sprint 2.28 Unity production scope, either extend this rule or create a new Dispatch'r rule for Unity.

### 3.2 Test rule: "AI_AUTOMATION_TEST_CREATED"

| Property | Value |
|:---|:---|
| Rule ID | 84000623161 |
| Position | 68 |
| Active | ✅ Yes |
| Conditions | `from_email is umair.alam@think360.ai` OR `from_email is dnyaneshwar.shekade@think360.ai` |
| Webhook target | `https://viselike-pushcart-hemlock.ngrok-free.dev/freshdesk/webhook` |
| Auth header | `X-Webhook-Token: 0c59f5a346b9fc9e6ffb7ba51d7e95c7552080c252ff5dd7781023ad257b7f4e` |
| Full payload | `{ticket:{id,subject,description,status,priority,tags}, requester:{email,name}, custom_fields:{client:"{{ticket.cf_clients}}"}}` |

---

## 4. AI Tag Pre-Filters

The Webhook Receiver should skip the reasoning pipeline entirely if these tags are present:

| Tag | Set by | Meaning | AI action |
|:---|:---|:---|:---|
| `alert` | "RBL alerts" Dispatch'r rule | Infrastructure alert notification | Skip — not a support ticket |
| `daily-report` | RBL reporting rules | Scheduled report email | Skip — auto-handled |
| `mtd` | RBL MTD report rule | Month-to-date report email | Skip — auto-handled |
| `ai_test` | "AI_AUTOMATION_TEST_CREATED" rule | Developer test ticket | Process normally (test scope) |
| `auto_assigned_client` | Per-client Dispatch'r rules | Named-client rule matched; cf_clients is set | Proceed — high-quality routing |
| `auto_assigned_group` | "AI auto replies" rule | AI webhook scope matched | Proceed — AI is expected to handle |
| `ai_auto_replied` | "AI auto replies" rule | Paired with auto_assigned_group | Proceed |
| `Reopened` | Supervisor rule 84000607317 | L2→L1 re-assignment after client reopen | Proceed — treat as new L1 ticket |

---

## 5. AI Conflict Zones (Rules AI Must Not Fight)

### 5.1 First-responder auto-assignment (Observer 84000606270)

- **What it does**: Auto-assigns any ticket with no assigned agent to whoever posts the FIRST note or reply
- **AI impact**: If the AI's API key belongs to a human agent, it will silently take ownership of tickets from human agents
- **Mitigation**: Dedicated AI agent account (`ai.support@getkwikid.com` or equivalent)
- **Status**: ⚠️ NOT MITIGATED — dedicated account not yet provisioned at audit time

### 5.2 Pre-assigned human agents

- **What it does**: Dispatch'r rules like "Assign tickets to Unity" pre-assign a named human agent (e.g., `Pranav`)
- **AI impact**: AI may process a ticket "owned" by a different agent, creating a confusing audit trail
- **Mitigation**: AI should check current assignee before processing; leave clear private note regardless of assignment; use dedicated AI agent account

### 5.3 Auto-close rules vs. AI processing

- **What it does**: Several Dispatch'r rules close tickets immediately on creation (OTP tickets, RBL reports, alert subjects, postmaster bounces)
- **AI impact**: AI receives a webhook for a ticket that's already closed when it gets there
- **Mitigation**: Pre-filter `status == 5 (Closed)` at webhook ingestion; skip processing

### 5.4 Multiple webhook destinations

- **Risk**: If two Dispatch'r or Observer rules both fire webhooks to AI endpoints on the same trigger, both execute and the AI processes the same ticket twice → duplicate customer replies
- **Mitigation**: Exactly one Dispatch'r rule and one Observer rule per trigger condition; idempotency check in the Webhook Receiver

### 5.5 Non-standard ticket_type values from Dispatch'r actions

- **What it does**: Some Dispatch'r rules set `ticket_type` to `"P1"`, `"Incident"`, `"Feature Request"`, `"P4"` — values not in the configured choice list
- **AI impact**: Must not write these values back; must not fail if it reads them
- **Mitigation**: Tolerate on reads (log and continue); restrict AI writes to 8 documented valid values

---

## 6. Observer Rule Architecture for AI

### 6.1 Active Observer rules (AI-relevant)

| Rule ID | Trigger | Action | AI Impact |
|:---|:---|:---|:---|
| 84000606270 | Agent is NONE when ticket updated | Assign to first responder | AI must use dedicated account to avoid claiming ownership |
| 84000606271 | Customer replies + status not Open | Set status Open, email assigned agent | AI does NOT need to manually reopen tickets — this fires automatically |
| 84000607316 | Ticket assigned to L2 or Tech Assign | Add watcher | Low AI impact |
| 84000621699 | Customer has interacted 5–9 times | Add watchers, email Sumati Nadar | Low AI impact; escalation context indicator |

### 6.2 Missing Observer rule (BLOCKING)

**NO active Observer rule currently fires a webhook when a customer replies.** This is the single most critical missing piece blocking the clarification loop.

Observer rule to create:
- **Name**: "AI — Customer Reply Webhook"
- **Condition**: `When Reply is sent → By Requester` (i.e., `incoming: true`)
- **Condition 2**: `Status is NOT Closed` (avoid firing on closed ticket replies)
- **Action**: `Trigger webhook: POST https://{production-domain}/webhooks/freshdesk/ticket-updated`
- **Content-Type**: JSON
- **Auth**: `X-Webhook-Token: <HMAC-SHA256-signed-value>` in custom headers
- **Payload**: Include `ticket.id`, `ticket.status`, `ticket_custom_fields`, `latest_comment`

---

## 7. Clarification Loop Architecture

### 7.1 When the clarification loop activates

The Workflow Engine activates the clarification loop when:
1. Required slot is missing (e.g., `session_id` not found in `description_text` via UUID regex)
2. Information provided is ambiguous (e.g., multiple sessions, unclear environment)
3. Issue cannot be diagnosed with available information

### 7.2 Complete clarification loop flow

```
STEP 1: Slot extraction fails
    → Workflow Engine: "Missing session_id — cannot proceed"

STEP 2: Save state to Supabase
    → ConversationStateStore.upsert(
          ticket_id = "197416",
          client = "UNITY",
          status = "AWAITING_CUSTOMER",
          required_slots = {"session_id": {"required": True, "value": null}},
          pending_questions = ["Could you please share the Session ID?"],
          workflow_state = {paused_at_step: "slot_extraction", ...}
      )

STEP 3: Send clarification reply
    → Execution Layer: POST /api/v2/tickets/197416/reply
      {"body": "<p>Hi Jane,<br>...<br>Could you please share the Session ID?<br>...</p>"}

STEP 4: Set ticket to Pending
    → Execution Layer: PUT /api/v2/tickets/197416
      {"status": 3}

STEP 5: Customer replies
    → Customer sends email with session ID
    → Freshdesk receives it
    → Observer rule 84000606271 fires: status Pending → Open
    → (MISSING: Observer webhook rule must fire here to notify AI)

STEP 6: AI receives update webhook
    → Webhook Receiver detects: latest_comment.incoming = true
    → ConversationStateStore.load(ticket_id = "197416")
    → state.status == "AWAITING_CUSTOMER"

STEP 7: Resume Golden Path
    → Extract session ID from customer's latest_comment.body_text
    → Update required_slots: session_id = "A1B2C3D4-..."
    → Resume workflow from paused_at_step = "slot_extraction"
    → Continue: Investigation → Knowledge → Reasoning → Action

STEP 8: Resolution or second round
    → If resolved: POST /reply + PUT status=Resolved + field updates
    → If still missing info: repeat from STEP 2 (second clarification)
```

### 7.3 Supabase conversation_state schema

```sql
TABLE conversation_state (
    ticket_id          TEXT        PRIMARY KEY,
    client             TEXT        NOT NULL,
    status             TEXT        NOT NULL,
    pending_questions  JSONB,
    required_slots     JSONB,
    workflow_state     JSONB,
    last_agent_reply   TEXT,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
)
```

**Status values**:
- `CREATED` — Case just created; pipeline not yet started
- `IN_PROGRESS` — Golden Path running; no clarification needed yet
- `AWAITING_CUSTOMER` — AI sent clarification question; ticket is Pending
- `RESUMED` — Customer replied; pipeline resumed with new information
- `RESOLVED` — Ticket resolved; state can be archived
- `ESCALATED` — Ticket escalated to L2/Asana; human handling

### 7.4 Race condition: two simultaneous customer replies

If a customer replies twice before the AI finishes processing the first reply:
1. First Observer webhook fires → AI starts processing
2. Second Observer webhook fires → second background task starts
3. Both tasks attempt to write to `conversation_state` for the same `ticket_id`

**Mitigation**: Serialize background task processing per `ticket_id`. Second event for the same ticket waits until the first task completes (lock or queue per ticket ID).

---

## 8. Tenant Resolution Workflow

### 8.1 Resolution priority order

```
Webhook received
    ↓
1. cf_clients already set? → Validate against Tenant Registry
   - If recognized: Build TenantContext, continue
   - If "Others": Check email domain next

2. Extract email domain from requester_email
   - Match against known domains (see table below)
   - If match: set tenant_id, build TenantContext, continue

3. Check CC email domains
   - Same matching logic
   - If match: set tenant_id, build TenantContext, continue

4. Subject-line keyword fallback
   - Last resort; collision-prone
   - If match: set tenant_id, continue with low confidence

5. No match found
   - UNKNOWN_TENANT
   - Stop pipeline
   - Write internal note
   - Route to human review
   - Create audit event
```

### 8.2 Known email domain → tenant mappings

| Email Domain | Tenant ID | Client Name |
|:---|:---|:---|
| `unitybank.co.in` | UNITY | Unity Bank |
| `bankofbaroda.com` | BOB | Bank of Baroda |
| `centralbank.co.in` | CBI | Central Bank of India |
| `rblbank.com` | RBL | RBL Bank |
| `bajajfinserv.in` | BAJAJ_FIN | BajajFin |
| `thomascook.in` | THOMAS_COOK | Thomas Cook |
| `canarabank.com` | CANARA | Canara Bank |
| `finobank.com` | FINO | FINO Payments Bank |
| `tfsin.co.in` | TOYOTA | Toyota Financial |
| `grihumhousing.com` | GHF | Grihum Housing Finance |

---

## 9. AI Ticket Processing Decision Tree

```
Webhook received
    │
    ├── HMAC verification fails? → Reject with 4xx, log
    │
    ├── status == 5 (Closed)? → Skip
    ├── tags contain "alert", "daily-report", "mtd"? → Skip
    ├── Subject matches known auto-close pattern? → Skip
    ├── Already processed (idempotency)? → Return 200, skip
    │
    └── Return 200 OK immediately, spawn background task
                │
                ├── ClientResolver: tenant resolved?
                │     ├── No → Stop, write note, route to human
                │     └── Yes → Build TenantContext
                │
                ├── Load conversation_state from Supabase
                │     ├── status = "AWAITING_CUSTOMER" + incoming customer reply?
                │     │     └── Resume clarification loop → inject reply → continue
                │     └── No active state → Start fresh pipeline
                │
                ├── Workflow Engine: classify ticket, select playbook
                │
                ├── Investigation Planner: determine evidence needed
                │
                ├── Evidence Collection: call tool registry
                │     └── (Phase 1: SOP search only; Phase 2: Unity Admin APIs)
                │
                ├── Knowledge Retrieval: SOP match
                │     ├── Found → sop_status = "SOP Present"; issue_recurrence = "Recurring"
                │     └── Not found → sop_status = "No SOP Available"; issue_recurrence = "New/One-time"
                │
                ├── Reasoning Engine: root cause analysis, confidence scoring
                │
                ├── Safety Guardrails: confidence threshold check
                │     ├── HIGH confidence → Action Gateway: autonomous reply
                │     ├── MEDIUM confidence → human review; post draft note
                │     └── cf_impact is "DOWNTIME 100%" or "Client Escalation" → Asana escalation always
                │
                └── Execution Layer
                      ├── Write note (diagnostics)
                      ├── Write reply OR draft note
                      ├── Update ticket fields
                      ├── Update status
                      └── Create Asana task (if escalated)
```

---

## 10. Action Safety Matrix (Summary)

Full matrix in `observations.md`. Quick reference:

| Action | Risk | Auto-Executable? |
|:---|:---|:---|
| Post internal private note | SAFE | ✅ Always |
| Update ticket classification fields | SAFE | ✅ Always |
| Resend OTP / Retry OCR | SAFE | ✅ Always |
| Retrieve user/session data | SAFE (read) | ✅ Always |
| Send customer reply | MEDIUM | ✅ Above confidence threshold |
| Update status (Resolved) | MEDIUM | ✅ Above confidence threshold |
| Create Asana escalation task | MEDIUM | ✅ Above confidence threshold |
| Reset session to prior stage | REVERSIBLE | ✅ With confidence threshold |
| Manual session repush | HIGH | ❌ Requires approval |
| Crop/trim session video | HIGH | ❌ Requires approval |
| PAN data correction | CRITICAL | ❌ Always requires approval |
| User creation in Admin Portal | CRITICAL | ❌ Always requires approval |

---

## 11. Known Integration Gaps (Priority Order)

| Gap | Blocks | Priority |
|:---|:---|:---|
| Missing Observer rule for customer reply webhook | Clarification loop | 🔴 BLOCKING |
| `FRESHDESK_WEBHOOK_SECRET` not set (HMAC disabled) | Security | 🔴 BLOCKING |
| "AI auto replies" Dispatch'r rule scope too narrow (misses Unity tickets) | All Unity production traffic | 🔴 BLOCKING |
| No dedicated AI agent account (AI using human credentials) | First-responder conflict, audit trail | 🔴 BLOCKING |
| Unity Admin API integration (get_session, get_user) | Full investigation | 🟡 Phase 2 |
| n8n as full webhook buffer (absorbs 10s timeout natively) | Reliability | 🟡 Phase 4 |
| Phase 5 approval UI for HIGH/CRITICAL actions | Autonomous high-risk actions | 🟠 Phase 5 |
