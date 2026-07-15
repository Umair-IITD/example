# Freshdesk Notes and Replies — AI Communication Strategy

**Source**: Live Freshdesk production audit 2026-06-18 (kwikid.freshdesk.com)  
**Applies to**: All AI-generated Freshdesk communications

---

## 1. Critical: No Draft Reply API

The Freshdesk REST API v2 has **no endpoint for creating a draft reply**. Any call to `POST /api/v2/tickets/{id}/reply` sends an outbound email to the customer immediately and cannot be undone.

**Implication**: "Let the AI draft inside Freshdesk and a human approves before sending" is NOT possible using a Freshdesk native feature. The private note is the only draft workaround (see Section 4).

---

## 2. Communication Types

There are four types of communication objects in a Freshdesk ticket thread:

| Type | `incoming` | `private` | Visibility | Triggers email? | Use for |
|:---|:---:|:---:|:---|:---:|:---|
| Customer reply | `true` | `false` | All agents + requester | N/A (inbound) | Customer's message |
| Public agent/AI reply | `false` | `false` | All agents + requester | ✅ YES | Resolving tickets, clarification questions, escalation acknowledgments |
| Internal agent/AI note | `false` | `true` | Agents only (yellow box) | ❌ NO | Diagnostic notes, draft responses, RCA breadcrumbs |
| Public note | `false` | `false` | All agents + requester | ❌ NO | Visible without triggering email (rarely used) |

---

## 3. Private Notes (Internal Notes)

### 3.1 Endpoint

```
POST /api/v2/tickets/{ticket_id}/notes
```

### 3.2 Request payload

```json
{
  "body": "<p><strong>AI Diagnostic Notes</strong><br>SOP Found: VKYC Auditor Visibility Guide<br>SOP Link: https://stackoverflowteams.com/c/kwikid/questions/514<br>Resolution Path: User form configuration reload needed.<br>Confidence: 🟢 HIGH (0.87)<br>Query Type: Case Not Visible In Agent/Admin<br>Issue Area: Frontend<br>Handling Time: 14 minutes</p>",
  "private": true
}
```

### 3.3 When the AI uses private notes

| Situation | Note content |
|:---|:---|
| High-confidence resolution (safety gate passes, reply sent) | Diagnostic summary: SOP found/used, session data, classification decisions, RCA, confidence score |
| Low-confidence or below threshold (reply NOT sent autonomously) | Full draft customer reply as note body, marked "AI DRAFT — PENDING HUMAN REVIEW"; reason why not auto-sent |
| Investigation complete, dev fix required | Investigation findings, session data, reproduction steps, escalation rationale |
| Token generation failure | Explanation of failure; instruction for human agent to investigate manually |
| Unknown tenant | `ClientResolver` result: unknown tenant; ticket routed for manual handling |
| Slot extraction incomplete | Questions asked to customer; what information is needed before AI can proceed |

### 3.4 Note formatting guidelines

Notes should be structured for human agent consumption using HTML:

```html
<p><strong>🤖 KwikID AI Support Agent</strong></p>
<p><strong>Classification:</strong><br>
Query Type: Session Not Available<br>
Issue Area: Backend<br>
Portal: Admin<br>
Issue Recurrence: Recurring issue</p>

<p><strong>SOP Match:</strong><br>
Status: SOP Present<br>
Link: https://stackoverflowteams.com/c/kwikid/questions/514<br>
Confidence: 0.87 (HIGH)</p>

<p><strong>Resolution:</strong><br>
[Resolution description here]</p>

<p><strong>RCA:</strong><br>
[Root cause analysis here]</p>

<p><strong>Handling Time:</strong> 12 minutes</p>
```

### 3.5 When to use private notes vs. field updates

| Data | Where to put it |
|:---|:---|
| RCA text | `cf_rca` custom field (paragraph) |
| SOP URL | `cf_stackoverflow_link` custom field (text) |
| Handling time | `cf_handling_time` custom field (paragraph, integer minutes) |
| Human-readable diagnostic context | Private note body |
| Draft reply for human review | Private note body with clear "AI DRAFT" header |

---

## 4. Draft Approval Pattern

When the Safety Guardrails determine a reply should NOT be sent automatically (confidence below threshold, HIGH/CRITICAL risk, policy-required human review), the AI uses this pattern:

```
1. AI composes candidate reply text
2. AI posts private note with:
   - "⚠️ AI DRAFT — PENDING HUMAN REVIEW" header
   - Full draft reply in the note body
   - Reason auto-send was not triggered (e.g., "Confidence: 0.61 — below 0.75 threshold")
3. AI sets cf_review_ticket = "Yes"
4. Human agent reads private note, edits if needed, manually sends reply
   — OR —
   Approval signal triggers Execution Layer to make the /reply call (Phase 5 dashboard)
```

### Draft note template

```html
<p><strong>⚠️ AI DRAFT — PENDING HUMAN REVIEW</strong></p>
<p><em>Reason not auto-sent: Confidence score 0.61 is below auto-send threshold (0.75).</em></p>
<hr>
<p>Hi [Customer Name],</p>
<p>[Draft reply body here]</p>
<p>Regards,<br>KwikID Support</p>
<hr>
<p><small>Review this draft and either send as-is or edit before sending to the customer.</small></p>
```

---

## 5. Public Replies

### 5.1 Endpoint

```
POST /api/v2/tickets/{ticket_id}/reply
```

### 5.2 Request payload

```json
{
  "body": "<p>Hi Jane,<br><br>The VKYC session has been updated. Please ask the user to clear their browser cache and log in again. The session should now be visible in the Auditor view.<br><br>If the issue persists, please reply with the Session ID and we will investigate further.<br><br>Regards,<br>KwikID Support</p>",
  "cc_emails": ["business-contact@unitybank.co.in"],
  "bcc_emails": []
}
```

### 5.3 When the AI sends public replies

| Scenario | Reply type | Status after |
|:---|:---|:---|
| High-confidence resolution, safety gate passes | Resolution message | Resolved (4) |
| Missing required information | Clarification question | Pending (3) |
| Issue escalated to engineering (Asana) | Escalation acknowledgment | Pending from Dev (6) or Open (2) |

### 5.4 Resolution message structure

```html
<p>Hi [Customer Name],</p>

<p>[Brief acknowledgment of the reported issue]</p>

<p>[Resolution explanation — what was done or what the customer should do]</p>

<p>If you experience any further issues, please don't hesitate to reply to this email.</p>

<p>Regards,<br>KwikID Support Team</p>
```

### 5.5 Clarification request structure

```html
<p>Hi [Customer Name],</p>

<p>Thank you for reaching out. To investigate this issue, we need a bit more information:</p>

<p>[Specific question(s) — e.g., "Could you please share the Session ID for the VKYC session that failed?"]</p>

<p>Regards,<br>KwikID Support Team</p>
```

Paired with: `PUT /api/v2/tickets/{id}` setting `"status": 3` (Pending) in the same or subsequent call.

### 5.6 Escalation acknowledgment structure

```html
<p>Hi [Customer Name],</p>

<p>Thank you for reporting this issue. We have reviewed the details and have escalated this to our engineering team for further investigation.</p>

<p>We will keep you updated on the progress and will notify you as soon as a resolution is available.</p>

<p>Regards,<br>KwikID Support Team</p>
```

---

## 6. Reply Routing and Pairing

### 6.1 Resolution reply — complete action sequence

When sending a resolution reply, the AI must also update all required closure fields in the same PUT call or before calling `/reply`:

```
1. POST /api/v2/tickets/{id}/notes (private note with diagnostics)
2. PUT /api/v2/tickets/{id} with:
   - status: 4  (Resolved)
   - custom_fields.cf_query_type: "<classified value>"
   - custom_fields.cf_issue_area: "<classified value>"
   - custom_fields.cf_portal: "<classified value>"
   - custom_fields.cf_sop_status: "SOP Present"
   - custom_fields.cf_resolution_classification: "<appropriate value>"
   - custom_fields.cf_rca_status: "RCA Shared" (or "No RCA Needed")
   - custom_fields.cf_rca: "<root cause analysis text>"
   - custom_fields.cf_stackoverflow_link: "<SOP URL>"
   - custom_fields.cf_handling_time: "<minutes>"
   - custom_fields.cf_review_ticket: "No"
   - custom_fields.cf_issue_type234462: "<Recurring issue | New/One-time issue>"
3. POST /api/v2/tickets/{id}/reply (the public resolution message to customer)
```

### 6.2 Clarification reply — action sequence

```
1. ConversationStateStore.upsert(ticket_id, status="AWAITING_CUSTOMER", required_slots={...})
2. POST /api/v2/tickets/{id}/reply (clarification question to customer)
3. PUT /api/v2/tickets/{id} with:
   - status: 3  (Pending)
```

### 6.3 Escalation to Asana — action sequence

```
1. create_asana_task(...) via Tool Registry
2. PUT /api/v2/tickets/{id} with:
   - custom_fields.cf_asana_ticket_link: "<Asana task URL>"
   - group_id: 84000293342  (L2)
3. POST /api/v2/tickets/{id}/notes (private note with escalation details)
4. POST /api/v2/tickets/{id}/reply (escalation acknowledgment to customer)
```

---

## 7. Conversation Retrieval

### 7.1 List all conversations

```
GET /api/v2/tickets/{ticket_id}/conversations
```

Returns all notes and replies in chronological order.

### 7.2 Key conversation object fields

| Field | Type | Meaning |
|:---|:---|:---|
| `id` | integer | Conversation (note/reply) ID |
| `body` | HTML string | Full message body as HTML |
| `body_text` | string | Plain text stripped of HTML |
| `incoming` | boolean | `true` = from customer; `false` = from agent/AI |
| `private` | boolean | `true` = internal note; `false` = visible to customer |
| `user_id` | integer | User ID of the sender |
| `created_at` | ISO 8601 | Timestamp of creation |
| `attachments` | array | File attachments, if any |

### 7.3 How to detect customer reply in conversation history

To retrieve the latest customer message from the conversation thread:

```python
conversations = get_ticket_conversations(ticket_id)
customer_replies = [
    c for c in conversations
    if c["incoming"] == True and c["private"] == False
]
latest_customer_message = customer_replies[-1] if customer_replies else None
```

---

## 8. CC Emails Strategy

The reply endpoint accepts `cc_emails` as an array. Rules:

- Include all relevant client contacts on the ticket's CC list
- For Unity Bank tickets, do NOT add internal think360.ai addresses to customer-facing replies
- Retrieve existing CC from the ticket object's `ticket_cc_emails` field and include in reply if present
- `bcc_emails` is accepted but should generally not be used for customer support replies

---

## 9. Conversation State Detection

When an Observer update webhook arrives, the AI must detect what type of conversation event occurred using the `latest_comment` object:

| Detection | Condition | AI action |
|:---|:---|:---|
| Customer replied | `latest_comment.incoming = true AND latest_comment.private = false` | Load `conversation_state`, resume clarification loop or run full pipeline on new info |
| Agent replied | `latest_comment.incoming = false AND latest_comment.private = false` | Skip — likely AI's own reply echoed back, or human agent responded |
| Internal note added | `latest_comment.incoming = false AND latest_comment.private = true` | Skip — internal note, not a customer message |

**CRITICAL**: `latest_comment` is at the **top level** of `freshdesk_webhook` in the webhook payload. It is NOT inside a `changes` object. A `changes.conversations` key does NOT exist in real Observer webhook payloads.

---

## 10. Body Formatting

### HTML requirements

Freshdesk requires HTML in the `body` field for both notes and replies. Always wrap content in proper HTML:

```html
<!-- Minimal valid body -->
<p>Your message here.</p>

<!-- Multi-paragraph -->
<p>First paragraph.</p>
<p>Second paragraph.</p>

<!-- With line breaks -->
<p>Hi Jane,<br><br>The session has been resolved.<br><br>Regards,<br>KwikID Support</p>
```

Do NOT send plain text without HTML tags — Freshdesk renders it without proper formatting.

### Character limits

No documented hard limit on note/reply body size, but keep replies concise and readable. The Knowledge Layer's `CHAT_CONTEXT_CHUNK_MAX_CHARS` should be set to at least ~3500 characters to avoid SOP content truncation.
