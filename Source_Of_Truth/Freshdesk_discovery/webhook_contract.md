# Freshdesk Webhook Contract

**Source**: Live Freshdesk production audit 2026-06-18 (kwikid.freshdesk.com) + live automation rule exports  
**Instance**: kwikid.freshdesk.com  
**CRITICAL**: Endpoint must return `200 OK` within **10 seconds**. Freshdesk drops the connection at timeout.

---

## 1. Webhook Overview

| Property | Value |
|:---|:---|
| Method | `POST` |
| Content-Type | `application/json` |
| Timeout | **10 seconds** — endpoint MUST return `200 OK` immediately |
| Auth header | `X-Webhook-Token` (HMAC-SHA256 over raw body bytes using `FRESHDESK_WEBHOOK_SECRET`) |
| Retry behavior | Freshdesk retries failed deliveries — implement idempotency |

---

## 2. Webhook Types

There are two distinct webhook event types the AI system must handle:

| Type | Trigger | Active? | Current Target |
|:---|:---|:---:|:---|
| **Ticket Creation** | Dispatch'r rule fires when ticket is created | ✅ | `https://n8n.app.getkwikid.com/webhook/freshdesk-ticket-created` |
| **Ticket Update** | Observer rule fires when customer replies | ❌ MISSING | Must be created — see Section 5 |

---

## 3. Ticket Creation Webhook

### 3.1 Trigger rule

**Rule name**: "AI auto replies"  
**Rule ID**: 84000621616  
**Position**: 2  
**Current scope**: `from_email is umair.alam@think360.ai OR from_email is dnyaneshwar.shekade@think360.ai OR cf_clients in ["Others"]`

> **CRITICAL GAP**: This rule does NOT fire for real Unity Bank tickets. For Sprint 2.28 (Unity scope), either a new rule must be created or this rule must be extended to include Unity's email domain / cf_clients value.

### 3.2 Payload structure (Freshdesk-native)

```json
{
  "freshdesk_webhook": {
    "id": 197416,
    "subject": "Not visible in Auditor view",
    "description": "<div>Hi Team,<br><br>The record is not visible in the Auditor view for mobile number 9XXXXXXXXX.<br>Session ID: F70a8888-96ee-45bb-b777-6942c2cd8ce3.<br><br>Regards,<br>Jane Doe.</div>",
    "description_text": "Hi Team, \n\nThe record is not visible in the Auditor view for mobile number 9XXXXXXXXX. \nSession ID: F70a8888-96ee-45bb-b777-6942c2cd8ce3. \n\nRegards, \nJane Doe.",
    "status": 2,
    "priority": 3,
    "ticket_type": "Issues",
    "created_at": "2026-06-18T08:06:12Z",
    "requester_email": "jane.doe@unitybank.co.in",
    "requester_name": "Jane Doe",
    "tags": "auto_assigned_client, auto_client_assign_as_unity",
    "ticket_custom_fields": {
      "cf_clients": "Unity",
      "cf_session_ids": "F70a8888-96ee-45bb-b777-6942c2cd8ce3",
      "cf_environment": "Production",
      "cf_issue_area": "Frontend",
      "cf_portal": "Admin",
      "cf_issue_type234462": "Recurring issue",
      "cf_sop_status": "SOP Present"
    },
    "attachments": []
  }
}
```

### 3.3 Field notes — creation payload

- `id` — integer; cast to string internally (`ticket_id`)
- `description` — HTML; use `description_text` for all parsing and regex extraction
- `description_text` — plain text stripped of HTML; use this for UUID/session ID extraction
- `status` — integer (2 = Open); cast to string internally
- `priority` — integer; cast to string internally
- `tags` — **comma-delimited string** in webhook; split and trim before use: `"a, b, c"` → `["a", "b", "c"]`
- `ticket_custom_fields` — custom fields key in webhook payloads; renamed to `custom_fields` in internal normalized model
- `id` field key — some webhook configs may send `ticket_id` instead of `id`; FastAPI MUST fall back to `ticket_id` key

### 3.4 Available template placeholders

These placeholders are available in Freshdesk Dispatch'r webhook content templates:

```
{{ticket.id}}
{{ticket.subject}}
{{ticket.description}}
{{ticket.description_text}}
{{ticket.status}}
{{ticket.priority}}
{{ticket.ticket_type}}
{{ticket.created_at}}
{{ticket.tags}}
{{ticket.cf_clients}}
{{ticket.cf_session_ids}}
{{ticket.cf_environment}}
{{ticket.cf_issue_area}}
{{ticket.cf_portal}}
{{ticket.cf_issue_type234462}}
{{requester.email}}
{{requester.name}}
```

### 3.5 Current "AI auto replies" rule action payload

The live rule sends this payload (minimal, not comprehensive):

```json
{
  "ticket": {
    "id": "{{ticket.id}}",
    "subject": "{{ticket.subject}}",
    "description": "{{ticket.description}}"
  },
  "replyVisibility": "private"
}
```

This is different from the full `freshdesk_webhook` envelope format. The FastAPI receiver must handle both shapes.

---

## 4. Ticket Update / Observer Webhook

### 4.1 Status

**MISSING — must be created.** No active Observer rule currently fires a webhook when a customer replies. This is the single most important gap blocking the clarification loop.

Observer rule to create:
- **Condition**: `When Reply is sent → By Requester` (incoming: true, private: false)
- **Action**: `POST webhook` to `/webhooks/freshdesk/ticket-updated` (or equivalent AI endpoint)
- **Content-Type**: JSON
- **Auth**: `X-Webhook-Token` header with HMAC-SHA256 value

### 4.2 Payload structure — customer reply

```json
{
  "freshdesk_webhook": {
    "id": 197416,
    "subject": "Not visible in Auditor view",
    "status": 2,
    "priority": 3,
    "updated_at": "2026-06-18T10:22:10Z",
    "requester_email": "jane.doe@unitybank.co.in",
    "tags": "auto_assigned_client, auto_client_assign_as_unity, reopened",
    "ticket_custom_fields": {
      "cf_clients": "Unity",
      "cf_sop_status": "SOP Present"
    },
    "latest_comment": {
      "body": "<div>Yes, I have cleared the cache and verified that the session still fails.</div>",
      "body_text": "Yes, I have cleared the cache and verified that the session still fails.",
      "incoming": true,
      "private": false,
      "user_id": 84084194436
    }
  }
}
```

### 4.3 Payload structure — agent reply

```json
{
  "freshdesk_webhook": {
    "id": 197416,
    "subject": "Not visible in Auditor view",
    "status": 2,
    "priority": 3,
    "updated_at": "2026-06-18T11:00:00Z",
    "requester_email": "jane.doe@unitybank.co.in",
    "tags": "auto_assigned_client",
    "ticket_custom_fields": {},
    "latest_comment": {
      "body": "<div>Hi, please share the session ID so we can investigate.</div>",
      "body_text": "Hi, please share the session ID so we can investigate.",
      "incoming": false,
      "private": false,
      "user_id": 84054016729
    }
  }
}
```

### 4.4 Payload structure — internal note

```json
{
  "freshdesk_webhook": {
    "id": 197416,
    "subject": "Not visible in Auditor view",
    "status": 2,
    "updated_at": "2026-06-18T11:30:00Z",
    "requester_email": "jane.doe@unitybank.co.in",
    "ticket_custom_fields": {},
    "latest_comment": {
      "body": "<div>Internal: DB query shows session timeout at stage 3.</div>",
      "body_text": "Internal: DB query shows session timeout at stage 3.",
      "incoming": false,
      "private": true,
      "user_id": 84054016729
    }
  }
}
```

### 4.5 Comment type detection rules

Use `latest_comment` field values to determine comment type:

| Type | `incoming` | `private` |
|:---|:---:|:---:|
| Customer reply | `true` | `false` |
| Agent/AI public reply | `false` | `false` |
| Internal note | `false` | `true` |

**CRITICAL**: `latest_comment` is at the **TOP LEVEL** of `freshdesk_webhook` — NOT inside a `changes` dict. A `changes.conversations` key does NOT exist in real payloads.

---

## 5. Normalization Mapping

FastAPI-internal model (from `app/freshdesk_webhook.py`):

| Raw webhook field | Internal field | Transformation |
|:---|:---|:---|
| `freshdesk_webhook.id` | `ticket_id` | Cast integer → string |
| `freshdesk_webhook.status` (integer) | `status` (string) | Always cast explicitly |
| `freshdesk_webhook.priority` (integer) | `priority` (string) | Always cast explicitly |
| `freshdesk_webhook.tags` (comma-delimited string) | `tags` (string array) | Split on `,` and strip whitespace |
| `freshdesk_webhook.ticket_custom_fields` | `custom_fields` | Renamed key; flat dict structure preserved |
| `freshdesk_webhook.requester_name` | — | Not currently in internal model; re-fetch via API if needed |

### Normalized internal model example

```json
{
  "ticket_id": "197416",
  "subject": "Not visible in Auditor view",
  "description": "<div>Hi Team,<br>...</div>",
  "description_text": "Hi Team, \n\nThe record is not visible...",
  "requester_email": "jane.doe@unitybank.co.in",
  "status": "2",
  "priority": "3",
  "tags": ["auto_assigned_client", "auto_client_assign_as_unity"],
  "custom_fields": {
    "cf_clients": "Unity",
    "cf_session_ids": "F70a8888-96ee-45bb-b777-6942c2cd8ce3"
  }
}
```

---

## 6. HMAC Authentication

### 6.1 Specification

- **Header**: `X-Webhook-Token`
- **Algorithm**: HMAC-SHA256
- **Message**: raw request body bytes (before JSON parsing)
- **Secret**: value of `FRESHDESK_WEBHOOK_SECRET` environment variable

### 6.2 Verification logic (pseudocode)

```python
import hmac
import hashlib

def verify_webhook(body_bytes: bytes, header_token: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode('utf-8'),
        body_bytes,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header_token)
```

### 6.3 Security status at audit

**`FRESHDESK_WEBHOOK_SECRET` was NOT SET** in `.env` at audit time. HMAC verification was effectively disabled. This must be remediated before production traffic flows.

### 6.4 Test rule custom header

The test rule "AI_AUTOMATION_TEST_CREATED" (ID 84000623161) uses this static token in its `X-Webhook-Token` header:
```
0c59f5a346b9fc9e6ffb7ba51d7e95c7552080c252ff5dd7781023ad257b7f4e
```

This is NOT the HMAC secret — it is a static bearer token for the test webhook only.

---

## 7. Pre-Filter Logic

The Webhook Receiver must apply these pre-filters before enqueuing any background task:

| Filter | Condition | Action |
|:---|:---|:---|
| Status check | `status == 5` (Closed) | Skip — ticket already closed |
| Noise tag filter | `tags` contains `alert`, `daily-report`, or `mtd` | Skip — infrastructure alert or scheduled report |
| Auto-closed subjects | Subject matches known auto-close patterns (e.g. `"[kwikid-vkyc-unity-prod-admin-api]"`, OTP subjects) | Skip |
| Idempotency | Same `ticket_id` + `event_type` + `conversation_id` already processed | Skip — duplicate delivery |

---

## 8. Timeout Handling Strategy

```
Freshdesk fires webhook
    ↓
Webhook Receiver receives POST
    ↓ (< 1 second)
1. Verify HMAC-SHA256 signature
2. Parse and normalize payload
3. Apply pre-filters
4. Persist idempotency record
5. Return 200 OK
    ↓ (immediately)
Freshdesk connection closed (within 10s window ✅)
    ↓ (background task starts asynchronously)
6. Enqueue Golden Path background task
7. Client Resolution → Ticket Orchestrator → Workflow Engine → ...
8. Execution Layer writes back to Freshdesk (minutes later, not seconds)
```

**Rule**: Any synchronous LLM/RAG call inside the webhook request handler is a correctness bug. The 10-second window is only sufficient for: HMAC verification, JSON parsing, normalization, pre-filter check, idempotency write, and `200 OK` response.

---

## 9. Retry Behavior and Idempotency

- Freshdesk retries webhook delivery if the endpoint does not respond within the 10-second window
- Implement idempotency keyed on: `ticket_id` + event type (`CREATED` or `UPDATED`) + `updated_at` or `latest_comment.user_id`
- If the idempotency check fires, log the duplicate and return `200 OK` without re-processing

---

## 10. Webhook Endpoint Configuration

### Current active webhooks (at audit time)

| Target URL | Method | Trigger | Status |
|:---|:---|:---|:---|
| `https://n8n.app.getkwikid.com/webhook/freshdesk-ticket-created` | POST | "AI auto replies" Dispatch'r rule | ✅ Active (limited scope) |
| `https://viselike-pushcart-hemlock.ngrok-free.dev/freshdesk/webhook` | POST | "AI_AUTOMATION_TEST_CREATED" Dispatch'r rule | ✅ Active (test only) |
| `https://predict.test.getkwikid.com/predict` | PUT | Observer rule 84000620617 | ❌ Inactive |
| `https://ai.dnyan.cloud/freshdesk/webhook/l1-first-response-note` | POST | Observer rule 84000622583 | ❌ Inactive |

### Production Gen3 target endpoints

| Event | Endpoint |
|:---|:---|
| Ticket created | `POST /webhooks/freshdesk/ticket-created` |
| Ticket updated (customer reply) | `POST /webhooks/freshdesk/ticket-updated` |

---

## 11. Missing Configuration — Action Required

| Item | Priority | Details |
|:---|:---|:---|
| Observer rule: customer reply webhook | **BLOCKING** | Create Observer rule: `When Reply is sent → By Requester` → POST to `/webhooks/freshdesk/ticket-updated`. Without this, clarification loop cannot resume. |
| Set `FRESHDESK_WEBHOOK_SECRET` | **BLOCKING** | Required for HMAC-SHA256 verification. Currently unset. Any party with the webhook URL can inject fabricated events. |
| Extend "AI auto replies" Dispatch'r rule scope | **BLOCKING for Unity traffic** | Currently only covers 2 internal test email addresses + cf_clients="Others". Must cover Unity Bank tickets for Sprint 2.28 production. |
| Dedicated AI agent account | **REQUIRED** | AI must use its own Freshdesk agent profile (`ai.support@getkwikid.com` or equivalent) for all API calls. |
