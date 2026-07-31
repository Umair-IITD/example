# KwikID AI Ingest Service — Remaining Blockers

**Date:** 2026-07-31
**Status:** System is LIVE in production. These blockers prevent 100% hands-off autonomous operation.

---

## Blocker 1: Dedicated AI Agent Freshdesk Account Not Created

**Severity:** MEDIUM (currently operational but impersonating a human agent)
**Status:** Pending manual admin action

**What is blocked:**
The AI agent posts Freshdesk notes and replies under a human support agent's account. This means:
- AI-generated notes are indistinguishable from human-written ones in the Freshdesk activity log
- The human agent's activity metrics are inflated by AI actions
- If the human agent leaves or their API key is rotated, the AI loses write access

**What needs to happen:**
1. Log in to the Freshdesk admin portal at `https://getkwikid.freshdesk.com/a/admin`
2. Navigate to **Admin → Agents → New Agent**
3. Create a new agent with:
   - Name: `KwikID AI Support`
   - Email: `ai.support@getkwikid.com`
   - Role: Agent (not Admin)
4. Generate an API key for this agent
5. Update `FRESHDESK_API_KEY=` in the production `.env` to use this new key
6. Restart the uvicorn service

**Workaround in place:** Currently using a human agent's API key. The system works correctly; this is an audit/identity concern only.

---

## Blocker 2: Asana Webhook Registered to Localhost (Not Production URL)

**Severity:** HIGH (L2 resolution loop only works locally)
**Status:** Pending re-registration on production server URL

**What is blocked:**
When engineering marks an Asana task as complete, Asana fires a webhook to the URL registered at webhook-registration time. If that URL is `https://your-ngrok-id.ngrok-free.app/webhooks/asana/task-completed` (set during local testing), it will NOT reach the production server.

**Current state:**
- Asana webhook GID: `1217038113542074`
- Target URL: registered during Sprint 2.63 local testing — may point to an expired ngrok URL
- The webhook handshake secret is stored in `data/asana_webhook_secrets.json` (gitignored)

**What needs to happen:**
1. Deploy the service to a stable public URL (e.g., `https://ai-support.getkwikid.com`)
2. Run the registration script against that URL:
   ```powershell
   # Set the target URL
   $env:ASANA_WEBHOOK_TARGET_URL = "https://ai-support.getkwikid.com/webhooks/asana/task-completed"
   python scripts/register_asana_webhook.py
   ```
3. The script will write the new handshake secret to `data/asana_webhook_secrets.json`
4. Verify in Asana: **Settings → Apps → Developer Apps → Your App → Webhooks**

**Workaround:** L2 escalation still creates Asana tasks. But the resolution loop (closing Freshdesk tickets when engineering marks task complete) will not fire until the webhook URL is correct.

---

## Blocker 3: AUTH_ENABLED=false — Admin Routes Are Unauthenticated

**Severity:** HIGH if server is internet-accessible; NONE if behind VPN
**Status:** Awaiting explicit decision from the responsible engineer

**What is blocked:**
When `AUTH_ENABLED=false` (current setting), the following admin routes have NO authentication:
- `POST /tickets/process` — manually trigger ticket processing
- `POST /tickets/{id}/retry` — retry a failed ticket
- `GET /tickets/{id}/status` — view ticket processing state
- `GET /admin/*` — all admin and debug endpoints
- `GET /actions/*` — action gateway admin

The `/freshdesk/webhook` endpoint is independently protected by HMAC and is NOT affected.

**Decision required (choose one):**

**Option A — Enable Auth (recommended if internet-accessible):**
```env
AUTH_ENABLED=true
OPERATOR_API_KEYS=kwikid-ops:your-strong-random-key-here
ADMIN_API_KEYS=kwikid-admin:your-strong-random-admin-key-here
```
Then rotate the keys securely to the Freshdesk admin team.

**Option B — Accept as-is (acceptable if behind VPN/firewall):**
```env
AUTH_ENABLED=false
# Document this decision and the network-level protection in place
```

**Why this was not silently fixed:** This is a security posture decision that depends on your network topology. Only the responsible engineer can make this call.

---

## Blocker 4: BOB and Canara Loki Credentials Missing

**Severity:** LOW (Unity Bank works fully; BOB/Canara log investigation disabled)
**Status:** Credentials not provided yet

**What is blocked:**
When a BOB or Canara ticket comes in, the `GetSessionLogsTool` will fail to retrieve logs because the Loki credentials for those tenants are not configured. The investigation continues with Admin Portal data only (partial investigation).

**What needs to happen:**
Fill in `.env`:
```env
# Bank of Baroda (BOB)
LOKI_BOB_URL=http://43.204.15.162:3000
LOKI_BOB_USERNAME=your-bob-loki-username
LOKI_BOB_PASSWORD=your-bob-loki-password

# Canara Bank
LOKI_CANARA_URL=https://videokyc.canarabank.bank.in
LOKI_CANARA_USERNAME=your-canara-loki-username
LOKI_CANARA_PASSWORD=your-canara-loki-password
```

These credentials come from the bank's IT team. Contact the KwikID account manager for each bank to request them.

---

## Minor Known Issues (Not Blockers)

### NLU Classification Accuracy
The NLP Router (GPT-4o-mini) is well-calibrated for common cases but may produce lower confidence for:
- Mixed-language tickets that aren't Hindi/English code-mixing
- Very short tickets (< 10 words) with no technical keywords
- Tickets about completely unknown topics (correctly returns UNKNOWN)

If NLU UNKNOWN rate exceeds 5% of tickets, consider:
1. Checking the `NLP_ROUTER_SIGNAL` WARNING log lines for patterns
2. Adding more example_phrases to `ontology.json` for the misclassified intent
3. Upgrading `NLP_ROUTER_MODEL` from `gpt-4o-mini` to `gpt-4o` (higher accuracy, higher cost)

### No Cross-Session Pattern Detection
Each ticket is investigated independently. Systematic failures affecting many customers (e.g., SMS gateway down for 2 hours) are not automatically aggregated. A human reviewing the internal notes should notice the pattern and escalate.

### Asana 400 Errors
If `ASANA_PROJECT_ID` or `ASANA_WORKSPACE_ID` is incorrect, AsanaClient.create_task() returns a 400 error. The full HTTP response body is now logged (Sprint 2.64+ fix) to help diagnose. Verify these GIDs in the Asana project URL.
