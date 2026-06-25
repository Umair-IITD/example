# Live Freshdesk Architectural Audit Report & Sprint 2.28 Readiness Review

**Date**: 2026-06-18  
**Auditor Role**: Senior Freshdesk Solutions Architect, Support Operations Architect, Enterprise Workflow Auditor, and Integration Readiness Reviewer  
**Status**: STRICTLY READ-ONLY Investigation Complete (Using Live API Auth)

---

## 1. Complete Automation Inventory

Every active and inactive automation rule has been retrieved and documented from the live Freshdesk instance.

### A. Ticket Creation Rules (Dispatch'r — Type ID 1)
These rules execute once when a ticket is created.

| Position | Rule Name | Active | Conditions Summary | Key Actions |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **Unity OTP - VKYC Portal Login OTP** | `True` | Subject is `"VKYC Portal Login OTP"` (Case Sensitive) | Set Status: Closed (5), Priority: High, Client: Unity, Query Type: Non support related, Recurrence: One-time, RCA: No RCA Needed, Tags: `unity_otp, auto_closed, auto_classified` |
| 2 | **AI auto replies** | `True` | Requester Email is `dnyaneshwar.shekade@think360.ai` OR `umair.alam@think360.ai` OR Clients is `"Others"` | Set Type: Issues, Group: L1, Tags: `auto_assigned_group, ai_auto_replied`, Trigger Webhook to n8n, Set CR Stage: NONE |
| 3 | **Delete \| Payu Payment Intimation 1** | `True` | Subject is `"Payu Payment Intimation"` | Delete Ticket, Set Client: CBI, Tags: `PAYU, CBI` |
| 4 | **Assign tickets to BOB as Priority** | `True` | Company Name is `"BankOfBaroda"` OR Requester Email contains `"bankofbaroda.com"` OR Subject contains `"BOB"`/`"bank of baroda"` OR CC Email contains `"bankofbaroda.com"` | Set Priority: High, Assign Agent: Harshkumar Koladiya, Group: L1, Client: BOB, Tier: T1, Team Lead: Tejesh More |
| 5 | **Assign tickets to Thomas Cook** | `True` | Company Name is `"ThomasCook"` OR Requester Email contains `"thomascook.in"` OR CC Email contains `"thomascook.in"` | Set Priority: Medium, Assign Agent: Swapnil Sabale, Group: L1, Tags: `btb`, Client: ThomasCook |
| 6 | **RBL alerts** | `True` | Subject contains `"Warning Alert - Memory utilization"` OR `"Warning Alert"` OR `"Ishan Dwivedi"` | Set Type: P1, Tags: `alert`, Client: RBL |
| 7 | **BOB Stage 2** | `True` | Subject is `"Stage 2 api call failed for these sessions - BOB"` (Case Sensitive) | Set Type: Incident, Status: Closed, Tags: `auto_mail` |
| 8 | **KwikID RBL Report -** | `True` | Subject is `"KwikID RBL Report - "` (Case Sensitive) | Set Type: Feature Request, Status: Closed, Tags: `daily-report` |
| 9 | **RBL VKYC Auditor Hold Notification** | `True` | Subject is `"RBL VKYC Auditor Hold Notification"` (Case Sensitive) | Set Priority: High, Assign Agent: Swapnil Sabale, Group: L1, Client: RBL |
| 10 | **\[kwikid-vkyc-unity-prod-admin-api\]** | `True` | Subject is `" [kwikid-vkyc-unity-prod-admin-api] "` OR `"[kwikid-vkyc-unity-prod-agent-api]"` | Set Status: Closed |
| 11 | **BOB VKYC Auditor Hold Notification** | `True` | Subject is `"BOB VKYC Auditor Hold Notification"` | Set Priority: Medium, Group: L1, Assign Agent: Harshkumar Koladiya |
| 12 | **postmaster@think360.ai** | `True` | Requester Email is `postmaster@think360.ai` | Set Status: Closed |
| 13 | **Tech Assign - Notification** | `True` | Group is Tech Assign | Send Email to Group: Tech Assign |
| 14 | **Push to Outlook** | `True` | To Email is `support@getkwikid.com` | Set office365_trigger |
| 15 | **Canara Support Mails to Assign to Sumit** | `True` | To/CC Email contains `"canarabank.com"` OR `"canarasupport@getkwikid.com"` | Group: L1, Assign Agent: Sumit Gorade, Client: Canara Bank, Tags: `auto_assigned_client` |
| 16 | **RBL MTD Report** | `True` | Subject is `"KwikID RBL Report"` | Status: Closed, Client: RBL, Query Type: Reports, Group: General, Tags: `mtd, report` |
| 17 | **ID Creation Auto Mail** | `True` | Subject/Description contains `"ID create"`, `"vkyc creation"`, `"create agent id"` | Send Auto Reply Email |
| 18 | **Add Watcher for the ticket** | `True` | Source is Phone OR Outbound Email | Add Watcher: Ticket Creating Agent |
| 19 | **Closing Recall or Automatic Mails** | `True` | Subject/Description is `"Recall"` OR `"Automatic reply:"` | Status: Closed, Type: P4, Group: General, Mark as Spam, Add Note |
| 20 | **Delete \| canarabank@canarabank.com** | `True` | Requester is `canarabank@canarabank.com` OR Subject contains `"OTP Verification"`/`"Welcome Kit"` | Delete Ticket |
| 21 | **ICICI MF iMobile SDK Journey MIS** | `True` | Requester is `sayali.chaudhari@think360.ai` | Status: Closed |
| 22 | **BA assignment rule** | `True` | To/CC contains `business@getkwikid.com` AND To/CC does not contain `support@getkwikid.com` | Group: Business Analyst |
| 23 | **Assign tickets to the Bajaj** | `True` | Requester Email contains `@bajajfinserv.in` | Client: BajajFin, Tier: T1, Lead: Baiju Dodhia |
| 24 | **Assign tickets to Unity** | `True` | Requester/CC Email contains `unitybank.co.in` OR Subject contains `"unity"` | Group: L1, Agent: Dnyaneshwar Shekade, Client: Unity, Tags: `auto_assigned_client` |
| 25 | **Assign tickets to RBL** | `True` | Requester/CC Email contains `rblbank.com` OR Subject contains `"RBL"` | Client: RBL, Tags: `auto_assigned_client` |
| 26 | **Assign tickets to FINO** | `True` | Requester/CC Email contains `finobank.com` OR Subject contains `"FINO"` | Group: L1, Client: FINO, Tags: `auto_assigned_client` |
| 27 | **Assign tickets to GHF** | `True` | Requester/CC Email contains `grihumhousing.com` OR Subject contains `"GHF"` | Client: Grihum Housing Finance, Group: L1 |
| 28 | **Assign tickets to TOYOTA** | `True` | Requester/CC Email contains `tfsin.co.in` OR Subject contains `"TOYOTA"` | Client: Toyota |
| 29 | **Assign tickets to CBI** | `True` | Requester/CC Email contains `centralbank` OR Subject contains `"CBI"` | Group: L1, Agent: Anil Mitkari, Client: CBI |
| 30 | **CBI Support Mails to Assign to Anil** | `True` | To Email is `cbi.support@think360.ai` | Group: L1, Agent: Anil Mitkari |

### B. Ticket Update Rules (Observer — Type ID 4)
These rules trigger on event updates.

*   **Automatically assign ticket to first responder** (Active: `True` | ID: `84000606270`)
    *   *Conditions*: Agent is `NONE`
    *   *Actions*: Assign to Agent: Event Performing Agent
*   **Automatically reopen tickets when the customer responds** (Active: `True` | ID: `84000606271`)
    *   *Conditions*: Status is not Open AND Incoming email is not automatic
    *   *Actions*: Set Status: Open, Send Email to Agent: Assigned Agent
*   **Add Watcher If assigned ticket is to L2 or Tech Assign** (Active: `True` | ID: `84000607316`)
    *   *Conditions*: None
    *   *Actions*: Add Watcher: Event Performing Agent
*   **Avoid escalations due to customer frustration** (Active: `True` | ID: `84000621699`)
    *   *Conditions*: Requester Interactions between 5 and 9 AND Source is Email/Portal
    *   *Actions*: Add Watchers: Sumati Nadar & Shubham Singh, Send Email to Sumati Nadar
*   **customer replies to a closed ticket - create a new ticket ID** (Active: `False` | ID: `84000622013`)
    *   *Conditions*: Status is Closed/Resolved AND Source is Email/Portal
    *   *Actions*: Create a new ticket, Assign to Group: L1
*   **Create new ticket via Webhook, on replies to closed tickets** (Active: `False` | ID: `84000606273`)
    *   *Conditions*: Status is Closed
    *   *Actions*: Add Tag: `new_ticket_webhook`, Set Status: Open
*   **Send Auto Mail When This Tickets have been updated to L2** (Active: `False` | ID: `84000607319`)
    *   *Conditions*: Group is Tech Assign
    *   *Actions*: Send a reply via Email
*   **Predicted_Priority via api** (Active: `False` | ID: `84000620617`)
    *   *Conditions*: Status is Open, Pending from Dev, Pending from L1/L2, or In Process
    *   *Actions*: Trigger Webhook (PUT to `https://predict.test.getkwikid.com/predict`)
*   **Copy of Create new ticket via Webhook, on replies to closed tickets** (Active: `False` | ID: `84000621230`)
    *   *Conditions*: Status is Closed AND Requester is `support@vikingcustoms.in`
    *   *Actions*: Trigger Webhook (POST to `https://kwikid.freshdesk.com/api/v2/tickets`)
*   **L1-first-response-note** (Active: `False` | ID: `84000622583`)
    *   *Conditions*: Status is Open
    *   *Actions*: Trigger Webhook (POST to `https://ai.dnyan.cloud/freshdesk/webhook/l1-first-response-note`)

### C. Time Trigger Rules (Supervisor — Type ID 3)
These rules run on an hourly schedule.

*   **1 Hour Created or Pending** (Active: `True` | ID: `84000607012`)
    *   *Conditions*: Hour since ticket created is 1 OR Hour since pending is 1
    *   *Actions*: Send Email to Assigned Agent
*   **Re-Assign The Ticket From L2 to L1 After Client Re-Opens the Ticket** (Active: `True` | ID: `84000607317`)
    *   *Conditions*: Status is Open AND Group is L2 or Tech Assign AND Hour since reopened > 1
    *   *Actions*: Assign to Group: L1, Add Tag: `Reopened`
*   **Empty client check and assign to L2** (Active: `False` | ID: `84000614292`)
    *   *Conditions*: Client is `NONE`
    *   *Actions*: Assign to Group: L2
*   **hourly assignment** (Active: `False` | ID: `84000621615`)
    *   *Conditions*: Ticket Type is Issues
    *   *Actions*: Assign to Group: L1

---

## 2. Complete Webhook Inventory

| URL | Active | HTTP Method | Content Type | Payload Template | Conditions / Triggers |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `https://n8n.app.getkwikid.com/webhook/freshdesk-ticket-created` | **`True`** | `POST` | `JSON` | `{"ticket": {"id": "{{ticket.id}}", "subject": "{{ticket.subject}}", "description": "{{ticket.description}}"}, "replyVisibility": "private"}` | Ticket Creation: Requester is Dnyaneshwar/Umair OR Client is `"Others"` |
| `https://predict.test.getkwikid.com/predict` | `False` | `PUT` | `JSON` | `{...}` (Includes fields: id, subject, description, created_at, status, clients, priority, group) | Ticket Update: Status is Open/Pending/In Process |
| `https://kwikid.freshdesk.com/api/v2/tickets` | `False` | `POST` | `JSON` | `{"description": "{{ticket.description}}", "subject": "{{ticket.subject}}", ...}` | Ticket Update: Status is Closed AND Requester is `support@vikingcustoms.in` |
| `https://ai.dnyan.cloud/freshdesk/webhook/l1-first-response-note` | `False` | `POST` | `JSON` | `{"ticket_id": 183981, "mode": "l1_first_response_note", ...}` | Ticket Update: Status is Open |

---

## 3. Ticket Creation Findings

When a new ticket is ingested, standard Liquid placeholders and custom fields can be injected into the webhook payload.

### Available Fields & Placeholders:
*   **Ticket Fields**: `{{ticket.id}}`, `{{ticket.subject}}`, `{{ticket.description}}` (HTML), `{{ticket.description_text}}` (Plain text), `{{ticket.status}}`, `{{ticket.priority}}`, `{{ticket.ticket_type}}`, `{{ticket.created_at}}`.
*   **Requester Fields**: `{{requester.email}}`, `{{requester.name}}`.
*   **Custom Fields**: `{{ticket.cf_clients}}`, `{{ticket.cf_session_ids}}`, `{{ticket.cf_environment}}`, `{{ticket.cf_issue_area}}`, `{{ticket.cf_portal}}`, `{{ticket.cf_issue_type234462}}` (Issue Recurrence).
*   **Tags**: `{{ticket.tags}}`.

---

## 4. Ticket Update Findings

Freshdesk's Observer engine can detect and trigger webhooks on the following events:
1.  **Public Reply**: `When Reply is sent -> By Requester` (Critical for RAG agent continuation).
2.  **Private Note**: `When Note is added -> Private` (Useful to inspect human agent queries).
3.  **Status Change**: `Status is changed -> From [Any] to [Resolved/Closed]`.
4.  **Group Change**: `Group is changed -> From [L1] to [L2]`.
5.  **Agent Assignment**: `Agent is changed -> From [None] to [Agent]`.
6.  **Custom Field Update**: E.g., `CR status is changed`.
7.  **Tag Addition**: E.g., Adding a specific trigger tag like `ai_audit_run`.

### Orchestration Utility:
*   **Clarification loop**: Triggering the webhook when a requester replies (Status changes from "Awaiting your Reply" to "Open") to let the RAG agent resume context.
*   **Action approvals**: When an agent changes a custom status field to "Approved", the Action Gateway executes the queued action.

---

## 5. Custom Fields Inventory

There are **54 ticket fields** configured. The primary custom fields are:

| Field Name | Type | Key Purpose | AI Control Scope |
| :--- | :--- | :--- | :--- |
| `cf_clients` | Dropdown | Identifies the client tenant (Unity, BOB, CBI, RBL, etc.) | **Read Only** (Inferred from email domain/Subject) |
| `cf_session_ids` | Paragraph | Stores the Video KYC Session IDs | **Read/Write** (Extracted via regex from description) |
| `cf_rca` | Paragraph | Root Cause Analysis description | **Write** (AI populates this on automated resolutions) |
| `cf_sop_status` | Dropdown | Status of SOP (SOP Present, No SOP Required, etc.) | **Write** (AI updates this based on RAG retrieval match) |
| `cf_rca_status` | Dropdown | Status of RCA (RCA Shared, No RCA Needed) | **Write** (AI updates to "RCA Shared") |
| `cf_asana_ticket_link` | Paragraph | Link to Asana engineering escalation ticket | **Write** (AI populates this on L2 escalation) |
| `cf_resolution_classification` | Dropdown | Solution classification | **Write** (AI sets to "Solved by SOP") |
| `cf_environment` | Dropdown | Environment (Production, UAT, CUG, Other) | **Read Only** |
| `cf_issue_area` | Dropdown | Area of issue (Frontend, Backend, Database, API, etc.) | **Write** (AI classifies this from logs) |
| `cf_portal` | Dropdown | Target Portal (Maker, Checker, Agent, Auditor, etc.) | **Write** (AI classifies this) |

---

## 6. Groups Inventory

The helpdesk defines 5 groups:
1.  **L1** (ID: `84000293343`): Primary support group. Incoming client-specific tickets are routed here.
2.  **L2** (ID: `84000293342`): Developer-level escalations.
3.  **Tech Assign** (ID: `84000293351`): Engineering assignment.
4.  **Business Analyst** (ID: `84000294040`): Requirements, BAU tasks, and CRs.
5.  **General** (ID: `84000293360`): Generic system alerts.

**Ticket Movement**: Tickets are classified by creation rules, assigned to L1, and then manually transitioned by agents to L2/Tech Assign when engineering support is needed.

---

## 7. Network Payload Findings

From the codebase audit (`app/freshdesk_webhook.py`), the inbound webhook model maps the following structure:

```json
{
  "ticket_id": "183981",
  "subject": "OTP Delivery Failure",
  "description": "<p>VKYC session failed at validation stage.</p>",
  "description_text": "VKYC session failed at validation stage.",
  "requester_email": "officer@unitybank.co.in",
  "status": "2",
  "priority": "1",
  "tags": ["auto_assigned_group"],
  "custom_fields": {
    "cf_clients": "Unity",
    "cf_session_ids": "123456"
  }
}
```

---

## 8. Sprint 2.28 Integration Recommendations

1.  **FastAPI Trigger**: Freshdesk can trigger FastAPI directly. The webhook target should be `http://<your-fastapi-domain>/freshdesk/webhook`.
2.  **n8n Nervous System Routing**: The future path (`Freshdesk -> n8n -> FastAPI`) is highly recommended. n8n will:
    *   Receive the webhook from Freshdesk.
    *   Handle any batching, filtering, or rate-limiting.
    *   Compute the SHA256 HMAC signature using a shared secret and append it in the `X-Webhook-Token` header.
    *   Forward it to FastAPI's `/freshdesk/webhook` endpoint.
3.  **HMAC Security Enforcement**: FastAPI expects `X-Webhook-Token` when `FRESHDESK_WEBHOOK_SECRET` is set.
4.  **Update Loop**: Connect an Observer webhook on `Reply is sent` to FastAPI `/freshdesk/webhook` so the conversation state can be updated when a customer replies to a clarification request.

---

## 9. Risks

*   **Webhook Timeout**: Freshdesk webhooks time out after **10 seconds**. If FastAPI blocks while running the LLM/RAG pipeline, Freshdesk drops the connection. *Mitigation*: FastAPI must accept the webhook instantly, write to the database (Case CREATED), return `200 OK`, and spawn the reasoning process asynchronously.
*   **Rate Limits**: Freshdesk API has standard IP-based and token-based rate limits. The uvicorn rate-limiter currently limits webhook endpoints to `60 req/60s`.

---

## 10. Conflicts

*   **Auto-Close Rules**: Rules like "Unity OTP" and "BOB Stage 2" immediately close incoming tickets. If the AI system is triggered on ticket creation, it might process tickets that are already closed by Freshdesk rules. *Mitigation*: In the AI creation rule, verify that the status of the ticket is "Open" or exclude closed subjects.
*   **Multiple Webhook Destinations**: The active "AI auto replies" rule routes to `n8n.app.getkwikid.com`. Running duplicate automation pipelines concurrently will cause double-posting.

---

## 11. Missing Requirements

*   **HMAC secret is currently inactive**: The `.env` file does not define `FRESHDESK_WEBHOOK_SECRET`, disabling HMAC signature validation. This must be set for security in Sprint 2.28.
*   **No Customer Reply Webhook Hooked Up**: There is currently no active Observer rule sending webhooks back to the AI backend when a customer responds to a clarification email. This prevents the clarification loop from functioning.

---
**Investigation Concluded.** Read-only audit completed successfully.
