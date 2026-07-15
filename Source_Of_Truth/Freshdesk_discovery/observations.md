# Freshdesk Discovery Observations — Gaps, Readiness, Risks

**Source**: Live Freshdesk production audit 2026-06-18 (kwikid.freshdesk.com)  
**Audit scope**: Full architectural discovery — automation rules, webhook configuration, API capabilities, security posture, AI integration readiness, Asana escalation readiness

---

## 1. AI Integration Readiness Assessment

### 1.1 Overall readiness: PARTIALLY READY

The Freshdesk instance has the structural foundations needed for AI automation (custom fields, groups, Dispatch'r rules, Observer rules, webhook infrastructure). However, several blocking issues must be resolved before production AI traffic can flow safely.

| Capability | Ready? | Notes |
|:---|:---:|:---|
| Ticket creation webhook | ⚠️ Partial | Active but wrong scope — misses Unity Bank tickets |
| HMAC webhook authentication | ❌ No | `FRESHDESK_WEBHOOK_SECRET` not set |
| Customer reply detection webhook | ❌ No | Observer rule does not exist yet |
| Dedicated AI agent account | ❌ No | AI using human agent credentials |
| Ticket field write (classification) | ✅ Yes | All cf_* fields documented; API works |
| Private note posting | ✅ Yes | Endpoint functional; format documented |
| Public reply sending | ✅ Yes | Endpoint functional; irreversible — gate required |
| Status transitions | ✅ Yes | Closure requires required fields to be set first |
| Rate limiting (40 req/min) | ✅ Yes | Documented; AI must self-limit to 30 req/min |
| Tenant resolution via cf_clients | ✅ Yes | Dispatch'r rules pre-set cf_clients reliably |
| Custom fields for AI write | ✅ Yes | 12 fields with WRITE or READ_WRITE scope |
| Clarification loop (Supabase state) | ⚠️ Partial | Supabase schema defined; Observer trigger missing |

---

## 2. Asana Integration Readiness

### 2.1 Asana readiness: PHASE 3 — not yet needed for Sprint 2.28

Asana is used for engineering escalation when a ticket requires developer action. The Freshdesk side is ready — the `cf_asana_ticket_link` custom field exists. The Asana side (API key, project structure, task template) must be validated separately.

| Component | Status | Notes |
|:---|:---|:---|
| `cf_asana_ticket_link` field | ✅ Exists | `84000731676`, paragraph type, WRITE scope for AI |
| `cf_resolved_date_by_developer` field | ✅ Exists | `84000731699`, date type, WRITE scope for AI |
| Asana API key | Unknown | Not audited in Freshdesk discovery; must verify separately |
| Asana project structure | Unknown | Task creation template not yet defined |
| Escalation conditions | ✅ Defined | `cf_impact = "DOWNTIME 100% impact"` or `"Client Escalation"` always escalates; MEDIUM confidence below threshold escalates |
| Escalation action sequence | ✅ Defined | See notes_and_replies.md Section 6.3 |
| Fix completion loop | ✅ Defined | Dev fix confirmed → `cf_resolved_date_by_developer` update + status Resolved |

### 2.2 Escalation criteria

Tickets that MUST be escalated to Asana (no autonomous resolution attempt):
1. `cf_impact = "DOWNTIME 100% impact"` — platform-wide outage
2. `cf_impact = "Client Escalation"` — escalated by client management
3. `priority = 4 (Urgent)` — treat as downtime unless proven otherwise
4. Confidence score below threshold after full investigation
5. Required evidence missing after clarification loop (customer did not provide session ID after 2 attempts)
6. Tool execution fails (Support Admin API call fails, cannot investigate)

### 2.3 Asana task requirements (when escalating)

The Asana task created by the Execution Layer must include:
- Ticket ID and subject
- Tenant/client name
- Reproduction steps
- Session ID(s) and any evidence collected
- AI's initial root cause hypothesis
- Links to relevant logs/session data
- Priority classification from Freshdesk

---

## 3. Gap Analysis — Missing Configuration

### 3.1 BLOCKING gaps (cannot go to production without these)

**GAP 1: Observer rule for customer reply webhook**
- **What's missing**: No active Freshdesk Observer rule fires a webhook when a customer replies
- **Impact**: The clarification loop (AI asks question → customer replies → AI resumes) cannot function at all
- **Fix**: Create Observer rule: `When Reply is sent → By Requester` → POST to customer-reply webhook endpoint
- **Effort**: ~15 minutes in Freshdesk admin
- **Who**: Freshdesk administrator

**GAP 2: HMAC webhook authentication not configured**
- **What's missing**: `FRESHDESK_WEBHOOK_SECRET` is not set in application `.env`
- **Impact**: Webhook endpoint has no authentication. Any party who discovers the webhook URL can inject fabricated events
- **Fix**: Generate a secret, set `FRESHDESK_WEBHOOK_SECRET` in `.env`, confirm `FreshdeskWebhookService` enforces HMAC-SHA256 verification before processing any payload
- **Effort**: ~30 minutes
- **Who**: DevOps + backend developer

**GAP 3: "AI auto replies" Dispatch'r rule scope is too narrow**
- **What's missing**: Rule only fires for 2 internal test email addresses + `cf_clients = "Others"`
- **Impact**: Real Unity Bank tickets (from `*@unitybank.co.in`) DO NOT trigger the AI webhook. No production Unity traffic will reach the AI runtime
- **Fix (option A)**: Add condition to existing rule: `OR cf_clients in ["Unity"]` or `OR requester_email ends with @unitybank.co.in`
- **Fix (option B)**: Create a separate "AI auto replies — Unity" Dispatch'r rule targeting Unity
- **Effort**: ~15 minutes in Freshdesk admin
- **Who**: Freshdesk administrator

**GAP 4: No dedicated AI agent account**
- **What's missing**: AI is using human agent API keys; no `ai.support@getkwikid.com` (or equivalent) account exists
- **Impact**: Observer rule 84000606270 (first-responder auto-assign) will assign ticket ownership to whichever human's credentials the AI uses. Audit trail mixes AI actions with human actions. Human agents lose ticket ownership unexpectedly.
- **Fix**: Provision a dedicated Freshdesk agent account for the AI; generate its API key; use that key in all AI API calls
- **Effort**: ~30 minutes
- **Who**: Freshdesk administrator + DevOps

### 3.2 Non-blocking gaps (important but not blocking Sprint 2.28)

**GAP 5: Unity Admin API integration not yet live (Phase 2)**
- Investigation tools (`get_session`, `get_user`, `get_session_details`) are not yet connected
- Phase 1 (Sprint 2.28): Knowledge Layer (SOP) only — no real session data
- Phase 2: Unity Admin API integration adds real evidence to the investigation pipeline

**GAP 6: n8n not yet functioning as full webhook buffer (Phase 4)**
- Currently n8n forwards the Freshdesk webhook to FastAPI
- FastAPI must still respond within 10 seconds total
- Phase 4: n8n absorbs the 10-second timeout entirely; FastAPI can take as long as needed for synchronous processing without worrying about the Freshdesk window

**GAP 7: Approval UI for HIGH/CRITICAL actions (Phase 5)**
- Actions classified as HIGH (manual repush, crop video) or CRITICAL (PAN correction, user creation) require human approval
- No approval interface exists yet
- Current behavior: AI must post a private note with the proposed action and wait for human manual execution
- Phase 5: Support Operations UI provides approval workflow

**GAP 8: QA reviewer dropdown has only one value**
- `cf_qa_reviewer` currently has exactly one configured choice: "Pintu Bhattacharya"
- No action needed for AI (field is READ ONLY for AI); Freshdesk admin must expand when needed

---

## 4. Risk Register

| Risk | Severity | Probability | Current State | Mitigation |
|:---|:---:|:---:|:---|:---|
| Webhook timeout — synchronous LLM call in handler | 🔴 CRITICAL | High (if not implemented correctly) | Unknown | Return `200 OK` immediately; all LLM/RAG work in background task |
| Duplicate replies — missing idempotency | 🔴 HIGH | Medium | Unknown | Idempotency check keyed on ticket_id + event_type + conversation_id |
| HMAC authentication disabled — forged webhook injection | 🔴 HIGH | Low (requires URL discovery) | Confirmed disabled | Set `FRESHDESK_WEBHOOK_SECRET` immediately |
| AI using human credentials — ownership conflicts | 🔴 HIGH | Certain (when AI processes any ticket) | Confirmed issue | Provision dedicated AI agent account |
| Unity tickets missing from AI webhook scope | 🔴 HIGH | Certain (Sprint 2.28 production) | Confirmed gap | Extend "AI auto replies" rule scope |
| Clarification loop broken — no customer reply webhook | 🔴 HIGH | Certain (for any clarification needed) | Confirmed gap | Create Observer rule |
| Closed-ticket processing waste | 🟡 MEDIUM | High (several auto-close rules exist) | Unknown | Pre-filter status==5 at ingestion |
| Customer reply race condition | 🟡 MEDIUM | Low (uncommon) | Unknown | Serialize background tasks per ticket_id |
| Token expiry mid-workflow (Phase 2+) | 🟡 MEDIUM | Low per ticket | N/A Phase 1 | Re-generate token on TTL error, retry once |
| Unknown tenant routing failure | 🟡 MEDIUM | Low for Unity, medium for multi-tenant | Unknown | `UNKNOWN_TENANT` → stop pipeline, route to human |
| Reply API irreversibility — premature send | 🟡 MEDIUM | Low (if gated correctly) | Unknown | Safety Guardrails confidence gate + idempotency |
| SOP truncation — CHAT_CONTEXT_CHUNK_MAX_CHARS too small | 🟡 MEDIUM | Unknown | Unknown | Set to ≥3500 characters |
| Draft-less reply API — customer sees draft reply | 🟡 MEDIUM | Low (if gated correctly) | N/A Phase 1 | Never call `/reply` outside the Execution Layer |
| Inactive Observer rules reactivated without validation | 🟠 LOW | Low | 6 inactive Observer rules exist | Do not re-activate without full impact review |
| HIGH/CRITICAL Tool actions without approval (Phase 2+) | 🟠 LOW | Low (Phase 1 doesn't use these) | N/A Phase 1 | Action Gateway enforces; Phase 5 UI provides approval |
| PII in Supabase conversation_state | 🟠 LOW | Certain (ticket descriptions stored) | Policy undefined | Treat as sensitive financial-services data; define retention policy |

---

## 5. Observations — Architecture Quality

### 5.1 What is well-designed

1. **Tenant resolution via cf_clients** — Dispatch'r rules pre-classify every ticket with a client/tenant identifier before the AI runtime sees it. This is clean, reliable, and eliminates most ambiguity.

2. **Custom field structure for AI** — the 43 custom fields are well-organized for AI operations. Fields with AI WRITE scope cover exactly the data the AI needs to write (SOP status, resolution classification, RCA, handling time, review flag). Fields that are human-owned are correctly scoped as READ_ONLY or DO_NOT_WRITE.

3. **Observer rule 84000606271** (auto-reopen on customer reply) — this is a critical piece of the clarification loop that Freshdesk handles natively. The AI doesn't need to manage reopen logic; Freshdesk does it automatically.

4. **Pre-filtering via tags** — the automation rules tag noise (`alert`, `daily-report`, `mtd`, `btb`) consistently. The AI pre-filter pattern (skip on noise tags) maps directly to these existing conventions.

5. **Two-tier SOP field model** — `cf_sop_status` (was SOP used?) + `cf_stackoverflow_link` (where is the SOP?) + `cf_resolution_classification` (how was it resolved?) provides complete knowledge-loop closure. The classification vocabulary is operational and matches real resolution patterns.

### 5.2 What needs attention

1. **The "AI auto replies" scope is too narrow.** Position 2 gives it priority, but the conditions only match test accounts + `cf_clients="Others"`. It will never fire for actual client tickets in production. This is the highest-risk configuration gap.

2. **No customer-reply Observer webhook.** This single missing configuration blocks the entire clarification loop. It should take 15 minutes to create. The fact that it has not been created yet suggests it was not needed by the previous automation (n8n + legacy system) — but the new AI runtime requires it.

3. **Security posture is weak at webhook layer.** HMAC authentication disabled + no dedicated AI account = fabricated events possible and no audit trail integrity. Both fixes are low-effort.

4. **Inactive Observer rules create landmines.** Observer rules 84000622013, 84000606273, 84000621230, and 84000622583 are all inactive but point to live (or formerly live) endpoints. Re-activating any of them without understanding the target endpoint could cause duplicate tickets, duplicate webhooks, or unexpected automation behavior. Document and archive these.

5. **Multiple webhook destinations for same trigger.** The current "AI auto replies" rule and the test rule "AI_AUTOMATION_TEST_CREATED" both fire on some of the same trigger conditions (internal test emails). If both are active and both reach AI endpoints, test tickets may be processed twice.

### 5.3 Structural assumptions the AI must never violate

1. **`cf_clients` is set before the AI sees the ticket.** The AI must never set or overwrite `cf_clients`. If it is absent, the ClientResolver must handle this case explicitly.

2. **`cf_environment` is customer-reported.** The AI must never change the environment field — it reflects what the customer told us, not what the AI inferred.

3. **All external writes flow through the Action Gateway.** No component upstream of the Action Gateway is allowed to call Freshdesk write endpoints. This constraint is non-negotiable.

4. **`/reply` is irreversible.** The Safety Guardrails must gate every `/reply` call. There is no undo.

5. **The SLA clock is owned by Freshdesk.** The AI interacts with SLA via status changes (Pending pauses, Open/In Process runs), but must not try to manually modify `due_by` or `fr_due_by`.

---

## 6. Production Readiness Checklist

### Identity & Security
- [ ] **Provision dedicated AI agent account** in Freshdesk (e.g. `ai.support@getkwikid.com`) with own API key; stop using human agent credentials
- [ ] **Set `FRESHDESK_WEBHOOK_SECRET`** in production `.env`; confirm HMAC-SHA256 verification rejects any webhook with invalid `X-Webhook-Token`
- [ ] **Confirm token generation** (`POST /generate_token`) has no caching and TTL ≈ 15 minutes

### Webhook Configuration
- [ ] **Create the missing clarification Observer rule** — `When Reply is sent → By Requester` → POST to AI's update-webhook endpoint
- [ ] **Extend "AI auto replies" Dispatch'r rule** to cover Unity Bank tickets (add `cf_clients in ["Unity"]` or requester email domain condition)

### Rate Limiting
- [ ] **Confirm `FreshdeskClient` rate limiter is active** and capped at 30 req/min against the 40 req/min account-wide ceiling

### Tenant Resolution
- [ ] **Confirm `ClientResolver` correctly maps `*@unitybank.co.in`** to `TenantContext` with Unity Tool Registry
- [ ] **Confirm `UNKNOWN_TENANT` path stops the pipeline**, creates audit event, routes to human review

### Clarification State
- [ ] **Confirm `conversation_state` table exists in Supabase** with schema: `ticket_id`, `client`, `status`, `pending_questions`, `required_slots`, `workflow_state`, `last_agent_reply`, `updated_at`
- [ ] **Confirm `ConversationStateStore` upserts at every state transition** and loads at start of every update webhook

### Action Safety
- [ ] **Confirm Action Gateway enforces Safety Matrix**: SAFE = auto-execute; MEDIUM = confidence threshold gate; HIGH/CRITICAL = blocked without approval
- [ ] **Confirm no code path calls `/reply`, `/notes`, or `PUT /tickets` outside the Execution Layer**

### Field Validation
- [ ] **Verify required-closure-field validation runs before any `PUT status=4/5`** call
- [ ] **Confirm `ticket_type` writes are restricted to the 8 documented valid choices**

### Knowledge Layer
- [ ] **Confirm `CHAT_CONTEXT_CHUNK_MAX_CHARS`** (or equivalent) is set to at least ~3500 characters to avoid SOP content truncation

### Observability
- [ ] **Confirm logging/observability exists for Golden Path background-task failures** (a failed run after 200 OK must be detectable)
- [ ] **Confirm each tool execution is audit-logged** with: timestamp, ticket_id, tenant_id, tool_id, inputs (PII-redacted), output summary, AI agent identity

### Rollout Scope
- [ ] **Confirm Sprint 2.28 deployment is Phase 1 only** (Freshdesk → FastAPI, no Unity Admin API calls yet)
- [ ] **Document if any Phase 2 Unity API tools have been included** in the Sprint 2.28 delivery
