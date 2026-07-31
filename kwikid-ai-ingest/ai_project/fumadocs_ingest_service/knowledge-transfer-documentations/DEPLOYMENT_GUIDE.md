# KwikID AI Ingest Service — Deployment Guide

**Audience:** DevOps / Platform engineer deploying the service for the first time or to a new server.

---

## 1. Required Environment Variables

### Mandatory (service refuses to start without these)
| Variable | Example | Notes |
|----------|---------|-------|
| `RAG_API_KEY` | `sk-rag-...` | Strong random key. Generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `OPENAI_API_KEY` | `sk-...` | OpenAI service key |
| `OPENAI_CHAT_API_KEY` | `sk-...` | Can be same as OPENAI_API_KEY |
| `INTELLIGENCE_LLM_API_KEY` | `sk-...` | Can be same as OPENAI_API_KEY |
| `SUPABASE_URL` | `https://xxx.supabase.co` | From Supabase project settings |
| `SUPABASE_KEY` | `eyJ...` | **service_role** key (not anon key) |
| `FRESHDESK_DOMAIN` | `getkwikid.freshdesk.com` | Freshdesk subdomain |
| `FRESHDESK_API_KEY` | `abc123...` | From Freshdesk admin agent profile |
| `FRESHDESK_WEBHOOK_SECRET` | `hex-string-32-chars` | Must match Freshdesk Dispatch'r rule setting |

### Security Settings (MUST be correct in production)
| Variable | Production Value | Risk if Wrong |
|----------|-----------------|---------------|
| `FRESHDESK_WEBHOOK_ENFORCE_HMAC` | `true` | Anyone can spoof webhook calls |
| `FRESHDESK_WEBHOOK_MODE` | `hmac` | Direct-mode bypasses HMAC check |
| `DEBUG_RAG` | `false` | Exposes chunk content (PII) in HTTP responses |
| `FASTAPI_DOCS_ENABLED` | `false` | Exposes full API schema publicly without auth |
| `SUPPORT_AGENT_MODE` | `PRODUCTION` | `DRY_RUN` = no real replies/actions executed |

### Unity Bank Integration
| Variable | Value | Notes |
|----------|-------|-------|
| `UNITY_ENABLED` | `true` | |
| `UNITY_BASE_URL` | `https://vkyc360.unitybank.co.in` | |
| `UNITY_USERNAME` | `unity` | Service account username |
| `UNITY_PASSWORD` | `*****` | Get from AWS Secrets Manager or departing engineer |

### Grafana Loki Integration
| Variable | Value | Notes |
|----------|-------|-------|
| `LOKI_ENABLED` | `true` | |
| `LOKI_SAAS_URL` | `https://utility-server-sfd.app.getkwikid.com` | |
| `LOKI_SAAS_USERNAME` | `*****` | Get from departing engineer |
| `LOKI_SAAS_PASSWORD` | `*****` | Get from departing engineer |

### Uptime Kuma Metrics
| Variable | Value | Notes |
|----------|-------|-------|
| `METRICS_PLATFORM_ENABLED` | `true` | |
| `METRICS_PLATFORM_BASE_URL` | `http://status.getkwikid.com:3001` | |
| `METRICS_PLATFORM_AUTH_MODE` | `bearer` | |
| `METRICS_PLATFORM_API_KEY` | `*****` | One of 3 configured Uptime Kuma keys |

### Asana L2 Escalation
| Variable | Value | Notes |
|----------|-------|-------|
| `ASANA_API_KEY` | `1/your-token` | Personal Access Token from Asana developer settings |
| `ASANA_PROJECT_ID` | `GID of "Support Escalation" project` | Copy from project URL |
| `ASANA_WORKSPACE_ID` | `GID of Think360 workspace` | Copy from workspace settings |

### Sentry APM
| Variable | Value |
|----------|-------|
| `SENTRY_DSN` | `https://...@us.sentry.io/...` |

### NLP Router
| Variable | Value | Notes |
|----------|-------|-------|
| `NLP_ROUTER_ENABLED` | `true` | Set `false` to disable LLM routing (falls back to UNKNOWN) |
| `NLP_ROUTER_MODEL` | `gpt-4o-mini` | Upgrade to `gpt-4o` for better accuracy |

### Optional Auth (recommended if internet-accessible)
| Variable | Value |
|----------|-------|
| `AUTH_ENABLED` | `true` |
| `OPERATOR_API_KEYS` | `kwikid-ops:your-key` |
| `ADMIN_API_KEYS` | `kwikid-admin:your-key` |

---

## 2. Server Startup Commands

### Standard production start
```bash
# Activate virtualenv
source .venv/bin/activate  # Linux/Mac
# OR
.venv\Scripts\activate     # Windows

# Load environment and start
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

> **Note:** Run with `--workers 1` only. The in-process rate limiter and WebhookIdempotencyStore are not distributed-safe. For horizontal scaling, enable `REDIS_RATE_LIMIT_ENABLED=true` and configure `REDIS_URL`.

### As a systemd service (Linux production)
```ini
# /etc/systemd/system/kwikid-ai.service
[Unit]
Description=KwikID AI Ingest Service
After=network.target

[Service]
Type=simple
User=kwikid
WorkingDirectory=/opt/kwikid/fumadocs_ingest_service
EnvironmentFile=/opt/kwikid/.env
ExecStart=/opt/kwikid/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable kwikid-ai
sudo systemctl start kwikid-ai
sudo systemctl status kwikid-ai
```

### With Ngrok (for local-to-internet testing)
```bash
# Install ngrok: https://ngrok.com/download
ngrok http 8000

# Note the HTTPS URL (e.g., https://abc123.ngrok-free.app)
# Use it as the Freshdesk webhook target URL
```

---

## 3. Freshdesk Admin Configuration

These must be done ONCE in the Freshdesk admin portal by a Freshdesk admin account.

### 3a. Create AI Agent Account
1. Log in to `https://getkwikid.freshdesk.com/a/admin`
2. **Admin → Agents → New Agent**
   - Email: `ai.support@getkwikid.com`
   - Name: `KwikID AI Support`
   - Role: Agent
3. Copy the API key and set `FRESHDESK_API_KEY=` in `.env`

### 3b. Create Dispatch'r Rule (new ticket → AI webhook)
1. **Admin → Helpdesk Productivity → Dispatch'r**
2. **New Rule:** "Route to AI Support Agent"
   - **Condition:** Ticket is created AND Source is Email
   - **Actions:**
     - Add tag: `client:unity_bank` (or appropriate client tag per email domain)
     - **Trigger webhook:** `POST https://your-server.com/webhooks/freshdesk/ticket-created`
     - Headers: `X-Webhook-Token: your-freshdesk-webhook-secret`

### 3c. Create Observer Rule (customer reply → AI webhook)
1. **Admin → Helpdesk Productivity → Observer**
2. **New Rule:** "Forward Customer Replies to AI"
   - **Condition:** Note/reply is added AND Performed by requester (not agent)
   - **Actions:**
     - **Trigger webhook:** `POST https://your-server.com/webhooks/freshdesk/ticket-updated`
     - Headers: `X-Webhook-Token: your-freshdesk-webhook-secret`
     - Body: include `latest_comment`, `ticket_id`, `action`, `status`

### 3d. Generate HMAC Secret
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```
- Set `FRESHDESK_WEBHOOK_SECRET=<generated-value>` in `.env`
- Set the same value in the Dispatch'r/Observer webhook configuration

---

## 4. Asana Webhook Registration

This must be done ONCE after the server has a stable public HTTPS URL.

```bash
# Set the production URL
export ASANA_WEBHOOK_TARGET_URL="https://your-server.com/webhooks/asana/task-completed"

# Register the webhook (writes handshake secret to data/asana_webhook_secrets.json)
python scripts/register_asana_webhook.py
```

The script will:
1. Call Asana's API to register a new webhook on your project
2. Handle the handshake challenge automatically
3. Write the `X-Hook-Secret` to `data/asana_webhook_secrets.json`

This file is gitignored — back it up securely. If it's lost, re-run the script (the old webhook becomes invalid).

### Set up Asana project structure (first-time only)
```bash
python scripts/setup_asana_project.py
```
This creates the 4 sections (New - Needs Triage / In Progress / Blocked - Needs Info / Done) and the Priority + Task Progress custom fields.

---

## 5. Database Migrations

If deploying from scratch or onto a fresh Supabase project:

```sql
-- Run these in order in the Supabase SQL editor
-- (located in sql/ and sql/b1_migrations/)

-- Core tables
sql/S2_001_initial_schema.sql
sql/S2_003_dead_letter_and_audit_events.sql
sql/S2_004_drop_case_fk.sql
sql/S2_008_workflow_state.sql

-- B1 (RAG) migrations
sql/b1_migrations/B1_001_documents_table.sql
sql/b1_migrations/B1_007_fts_setup.sql
sql/b1_migrations/B1_008_fts_rpc.sql

-- Knowledge tables (B3)
-- rag_knowledge_articles and rag_knowledge_chunks are created automatically
-- by the knowledge ingestion pipeline on first run
```

> **Verify:** After applying, check `AUDIT_BACKEND=supabase` is set. The startup validator will report FAILED if the `audit_events` table is missing.

---

## 6. Post-Deployment Verification

### Health checks
```bash
curl https://your-server.com/health
# Expected: {"status": "ok"}

curl -H "X-API-Key: your-rag-api-key" https://your-server.com/ready
# Expected: {"status": "ready"}
```

### Send a test ticket
Use the Postman payloads from `START_HERE.md §Step 5`. Confirm:
1. Webhook returns 200 OK within 1 second
2. Internal note appears on the Freshdesk ticket within 30 seconds
3. If URN/Session ID provided: investigation evidence appears in the note

### Verify Sentry
Navigate to `https://think360-n0.sentry.io` → project `python-fastapi`. Confirm no new errors appeared during the test.

---

## 7. Environment Variables Checklist (copy to your deployment runbook)

```
[ ] RAG_API_KEY                    ← generate fresh
[ ] OPENAI_API_KEY
[ ] OPENAI_CHAT_API_KEY
[ ] INTELLIGENCE_LLM_API_KEY
[ ] SUPABASE_URL
[ ] SUPABASE_KEY                   ← service_role key
[ ] FRESHDESK_DOMAIN
[ ] FRESHDESK_API_KEY              ← ai.support@getkwikid.com account key
[ ] FRESHDESK_WEBHOOK_SECRET       ← generate fresh, set in Freshdesk admin too
[ ] FRESHDESK_WEBHOOK_ENFORCE_HMAC=true
[ ] FRESHDESK_WEBHOOK_MODE=hmac
[ ] UNITY_PASSWORD
[ ] LOKI_SAAS_USERNAME
[ ] LOKI_SAAS_PASSWORD
[ ] METRICS_PLATFORM_API_KEY
[ ] ASANA_API_KEY
[ ] ASANA_PROJECT_ID
[ ] ASANA_WORKSPACE_ID
[ ] SENTRY_DSN
[ ] SUPPORT_AGENT_MODE=PRODUCTION
[ ] DEBUG_RAG=false
[ ] FASTAPI_DOCS_ENABLED=false
[ ] AUTH_ENABLED + keys (if internet-accessible)
[ ] ACTIVE_INDEX_VERSION=v2
[ ] AUDIT_BACKEND=supabase
```
