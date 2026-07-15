# Freshdesk API Reference

**Source**: Live Freshdesk production audit 2026-06-18 (kwikid.freshdesk.com)  
**API version**: REST API v2  
**Base URL**: `https://kwikid.freshdesk.com/api/v2/`

---

## 1. Authentication

- **Method**: HTTP Basic Authentication
- **Username**: Your Freshdesk API key (e.g. `khqU5fb17tS7rr09z0IO`)
- **Password**: Literal string `"X"` (not your Freshdesk account password)

```bash
# Example curl
curl -X GET "https://kwikid.freshdesk.com/api/v2/tickets/197416" \
  -u "YOUR_API_KEY:X" \
  -H "Content-Type: application/json"
```

```python
# Example Python
import base64
import urllib.request

api_key = "YOUR_API_KEY"
credentials = base64.b64encode(f"{api_key}:X".encode()).decode()
headers = {
    "Authorization": f"Basic {credentials}",
    "Content-Type": "application/json"
}
```

**CRITICAL**: The AI agent must use a **dedicated AI agent API key** — not a human agent's personal API key. At audit time, a dedicated AI agent account had not been provisioned. This is a blocking item for production.

---

## 2. Rate Limits

| Limit | Value | Source |
|:---|:---|:---|
| Account-wide limit | **40 requests/minute** | Confirmed via `x-ratelimit-total: 40.0` response header |
| AI self-imposed limit | **30 requests/minute** | Preserves headroom for human agents |
| Rate limit scope | Account-wide — shared across ALL API keys, integrations, and human users | |

### Rate limit response headers

| Header | Meaning |
|:---|:---|
| `x-ratelimit-total` | Account-wide max requests per minute (40.0) |
| `x-ratelimit-remaining` | Requests remaining in current window |
| `x-ratelimit-used-current-request` | Cost of the current request |

### Rate limit handling

- Monitor `x-ratelimit-remaining` proactively
- On **HTTP 429**: back off with exponential backoff, do NOT immediately retry
- AI's `FreshdeskClient` must implement a token-bucket or sliding-window limiter capped at 30 req/min
- Limit is account-wide — heavy human agent activity reduces the AI's available budget

---

## 3. Ticket Endpoints

### 3.1 Get ticket

```
GET /api/v2/tickets/{ticket_id}
```

Returns the complete ticket object including all standard and custom fields.

**Response** (200 OK): Full ticket JSON — see ticket_schema.json for complete field list.

```bash
curl -X GET "https://kwikid.freshdesk.com/api/v2/tickets/197416" \
  -u "API_KEY:X" -H "Content-Type: application/json"
```

### 3.2 Update ticket

```
PUT /api/v2/tickets/{ticket_id}
```

Updates ticket fields. Can update standard fields, custom fields, and status in a single call.

**Custom fields** must be nested under `"custom_fields"` key (NOT `"ticket_custom_fields"`).

**Request body examples**:

Update classification fields only:
```json
{
  "custom_fields": {
    "cf_query_type": "Session Not Available",
    "cf_issue_area": "Backend",
    "cf_portal": "Agent",
    "cf_sop_status": "SOP Present",
    "cf_issue_type234462": "Recurring issue"
  }
}
```

Update status to Resolved (requires closure fields to be set first):
```json
{
  "status": 4,
  "custom_fields": {
    "cf_sop_status": "SOP Present",
    "cf_resolution_classification": "Solved by SOP (Temporary Workaround)",
    "cf_rca_status": "No RCA Needed",
    "cf_handling_time": "15",
    "cf_review_ticket": "No"
  }
}
```

Assign to group and change priority:
```json
{
  "group_id": 84000293342,
  "priority": 4
}
```

**Response codes**:
- `200 OK` — success; returns updated ticket JSON
- `422 Unprocessable Entity` — required field missing (e.g., closure fields not set before status=4)
- `429 Too Many Requests` — rate limit exceeded

**CRITICAL**: Moving to `status: 4 (Resolved)` or `status: 5 (Closed)` requires `cf_clients`, `ticket_type`, `cf_sop_status`, and `cf_resolution_classification` to be populated. HTTP 422 will occur if any are missing.

### 3.3 List tickets

```
GET /api/v2/tickets
```

Query parameters:
- `status` — filter by status (2, 3, 4, 5, etc.)
- `group_id` — filter by group
- `email` — filter by requester email
- `page` — pagination (default 1)
- `per_page` — results per page (max 100)
- `order_by` — sort field (e.g. `created_at`, `updated_at`)
- `order_type` — `asc` or `desc`

### 3.4 Search tickets

```
GET /api/v2/search/tickets?query="..."
```

Freshdesk's ticket search endpoint. Supports field-based queries.

Example:
```
GET /api/v2/search/tickets?query="cf_clients:'Unity' AND status:2"
```

---

## 4. Conversation Endpoints (Notes and Replies)

### 4.1 Add private note

```
POST /api/v2/tickets/{ticket_id}/notes
```

**Request body**:
```json
{
  "body": "<p><strong>AI Diagnostic Notes</strong><br>SOP Found: VKYC Auditor Visibility Guide<br>Confidence: HIGH<br>Resolution Path: User form reload required.</p>",
  "private": true
}
```

- `private: true` — restricted to logged-in agents (yellow box in agent portal)
- `private: false` — creates a public-facing note (does NOT trigger outbound email like `/reply`)
- Default: always use `private: true` for AI internal notes

**Response codes**:
- `201 Created` — note created; returns note object with `id`
- `422 Unprocessable Entity` — validation error

### 4.2 Send public reply

```
POST /api/v2/tickets/{ticket_id}/reply
```

**Request body**:
```json
{
  "body": "<p>Hi Jane,<br><br>The VKYC session has been updated. Please ask the user to clear their browser cache and log in again.<br><br>Regards,<br>KwikID Support</p>",
  "cc_emails": ["business-contact@unitybank.co.in"],
  "bcc_emails": []
}
```

**CRITICAL**: Calling `/reply` sends an email to the customer **IMMEDIATELY**. There is no staging or preview. Once called, the email cannot be unsent. Every call to this endpoint must:
1. Pass through the Safety Guardrails confidence check
2. Have explicit Action Gateway approval
3. Use idempotency protection to prevent double-sends

**Response codes**:
- `201 Created` — reply sent; returns conversation object
- `422 Unprocessable Entity` — validation error

### 4.3 List conversations

```
GET /api/v2/tickets/{ticket_id}/conversations
```

Returns all conversations for a ticket — both notes and replies — in chronological order.

Each conversation object includes:
- `id` — conversation ID
- `body` — HTML content
- `body_text` — plain text
- `incoming` — `true` if from customer, `false` if from agent/AI
- `private` — `true` if internal note
- `user_id` — agent or requester user ID
- `created_at` — timestamp

---

## 5. Agent and Group Endpoints

### 5.1 List agents

```
GET /api/v2/agents
```

Returns all configured agents. Use to find user IDs for assignment or filtering.

### 5.2 List groups

```
GET /api/v2/groups
```

Returns all groups. Live groups:

| Group Name | ID |
|:---|:---|
| L1 | `84000293343` |
| L2 | `84000293342` |
| Tech Assign | `84000293351` |
| Business Analyst | `84000294040` |
| General | `84000293360` |

---

## 6. Error Codes Reference

| HTTP Status | Meaning | AI handling |
|:---|:---|:---|
| `200 OK` | Success (GET, PUT) | Continue |
| `201 Created` | Success (POST) | Continue |
| `400 Bad Request` | Malformed JSON or missing required parameter | Log error, identify specific issue, fix before retry |
| `401 Unauthorized` | Invalid API key | Check credentials; alert if correct key is failing |
| `403 Forbidden` | Permission denied | Check if dedicated AI agent account has required permissions |
| `404 Not Found` | Ticket ID does not exist | Log; skip further processing for this ticket |
| `409 Conflict` | Duplicate record | Idempotency check failed — ticket/note already exists |
| `422 Unprocessable Entity` | Required field missing or invalid value | Identify specific missing field(s); log structured error; do NOT retry blindly |
| `429 Too Many Requests` | Rate limit exceeded | Exponential backoff; do NOT retry immediately |
| `500 Internal Server Error` | Freshdesk server error | Retry after delay (max 3 attempts); if persistent, route to human |
| `503 Service Unavailable` | Freshdesk down | Retry with extended backoff; alert on-call if persistent |

---

## 7. Key Field Reference for API Writes

### 7.1 Status values (integer)

| Label | API Value |
|:---|:---|
| Open | `2` |
| Pending | `3` |
| Resolved | `4` |
| Closed | `5` |
| Pending from Development Team | `6` |
| Pending from Client | `7` |
| Pending from L1 | `8` |
| Pending from L2 | `9` |
| In Process | `10` |
| Pending from support | `11` |
| Hold | `12` |

### 7.2 Priority values (integer)

| Label | API Value |
|:---|:---|
| Low | `1` |
| Medium | `2` |
| High | `3` |
| Urgent | `4` |

### 7.3 Valid ticket_type values (string)

Only use these 8 values when writing. Others may appear on reads — tolerate but never write.

- `"Issues"` — default for AI-actionable tickets
- `"Custom Change Request"`
- `"Login"`
- `"Logout"`
- `"BA BAU"`
- `"Feedback"`
- `"Service Task"`
- `"Internal mail"`

### 7.4 Custom field AI write reference

| Custom Field | API Name | Type | Valid Values |
|:---|:---|:---|:---|
| SOP Status | `cf_sop_status` | Dropdown | SOP Present, SOP Created (New), Old SOP Modified, No SOP Available, No SOP Required |
| Resolution Classification | `cf_resolution_classification` | Dropdown | Solved by SOP (Permanent Fix), Solved by SOP (Temporary Workaround), Temporary Fix Applied by Dev (Pending Permanent), Permanent Fix Applied by Dev, No SOP Issue Exists (No support action required), Wrongly reported by client Not an Issue (No actions taken) |
| RCA Status | `cf_rca_status` | Dropdown | RCA Shared, RCA Pending from dev, No RCA Needed |
| Query Type | `cf_query_type` | Dropdown | 67 values — see custom_fields.json |
| Issue Area | `cf_issue_area` | Dropdown | Frontend, Backend, Database, API, UI/UX, DevOps/infrastructure/deployments, Security-related, Network/firewall, Third-party Integrations, Other |
| Portal | `cf_portal` | Dropdown | Agent, Admin, Auditor, User, Agentless, Server, SaaS, Other, Maker, Checker |
| Issue Recurrence | `cf_issue_type234462` | Dropdown | Recurring issue, New/One-time issue |
| Review Ticket | `cf_review_ticket` | Dropdown | No, Yes |
| Session IDs | `cf_session_ids` | Paragraph (text) | UUID string(s) extracted from description_text |
| RCA | `cf_rca` | Paragraph (text) | Free-text root cause analysis |
| StackOverflow Link | `cf_stackoverflow_link` | Text | URL of SOP entry |
| Asana Ticket Link | `cf_asana_ticket_link` | Paragraph (text) | Asana task URL |
| Handling Time | `cf_handling_time` | Paragraph (numeric) | Integer value in minutes |
| Resolved Date by Developer | `cf_resolved_date_by_developer` | Date | YYYY-MM-DD format |

---

## 8. FreshdeskClient Implementation Requirements

The `FreshdeskClient` component must implement:

1. **Authentication**: Use dedicated AI agent API key only; never human agent credentials
2. **Rate limiting**: Token-bucket or sliding-window limiter capped at **30 req/min**
3. **Header monitoring**: Read `x-ratelimit-remaining` on every response; proactive backoff when remaining < 5
4. **Retry logic**:
   - HTTP 429: exponential backoff (2s, 4s, 8s), max 3 retries
   - HTTP 500/503: retry after 5s delay, max 3 retries
   - HTTP 422: structured error with missing field identification, no automatic retry
   - HTTP 401/403: immediate alert, no retry
5. **Typed methods**: get_ticket, update_ticket, post_reply, post_note, list_conversations
6. **Background-task safety**: All Freshdesk API calls happen asynchronously in the background task, never in the webhook request handler

---

## 9. API Gotchas and Critical Notes

1. **`ticket_custom_fields` vs `custom_fields`**: Freshdesk webhook payloads use `ticket_custom_fields`; write API calls require `custom_fields`. These are different keys on the same data. Never use `ticket_custom_fields` in a PUT body.

2. **`id` vs `ticket_id`**: Webhook payload may send either `id` or `ticket_id` as the key. FastAPI must fall back to the `ticket_id` key if `id` is absent.

3. **`/reply` is irreversible**: There is no draft endpoint. A call to `POST /reply` sends an email immediately. No undo.

4. **Required fields for status=4/5**: HTTP 422 occurs if `cf_clients`, `ticket_type`, `cf_sop_status`, or `cf_resolution_classification` are missing when setting Resolved/Closed status.

5. **tags in webhook are strings, in API are arrays**: Webhook delivers `"tag1, tag2"` as a comma-delimited string. The GET ticket API returns `["tag1", "tag2"]` as an array. Split and trim webhook tags.

6. **`description` is HTML; `description_text` is plain text**: All parsing, regex extraction (session IDs, URNs, phone numbers) must use `description_text` — parsing HTML with regex is error-prone.

7. **Non-standard ticket_type values may appear on reads**: Dispatch'r rules may have set `P1`, `Incident`, `Feature Request`, or `P4` on existing tickets. Tolerate on reads; only write the 8 documented values.
