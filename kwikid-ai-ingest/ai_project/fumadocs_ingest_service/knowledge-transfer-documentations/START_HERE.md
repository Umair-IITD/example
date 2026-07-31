# START HERE — Onboarding Guide for the Next Engineer

Welcome to the KwikID AI Ingest Service. This document gets you from zero to a running local instance in under 30 minutes.

---

## Step 1: Read These Documents First (in order)

1. **This file** — orientation and setup
2. `knowledge-transfer-documentations/PROJECT_GUIDE.md` — architectural deep-dive
3. `knowledge-transfer-documentations/CURRENT_STATE.md` — what is live right now
4. `CLAUDE.md` (project root) — permanent engineering rules you MUST follow
5. `C:\Users\Umair.Alam\Desktop\kwikid_support_system\Source_Of_Truth\Architectural_truth\SUPPORT_OPERATIONS_BLUEPRINT.md` — the business and system blueprint (single source of truth)
6. `Source_Of_Truth\Architectural_truth\flow_diagram.mermaid` — the visual architecture

The SOT (Source of Truth) docs live **one level above this repo** at:
```
C:\Users\Umair.Alam\Desktop\kwikid_support_system\Source_Of_Truth\
```

The most important SOT subdirectories:
- `Architectural_truth/` — Blueprint + flow diagram
- `Freshdesk_discovery/` — Freshdesk field mappings, closure fields, ticket lifecycle
- `Unity_discovery/` — Unity Admin Portal API reference
- `Metrics_discovery/` — Uptime Kuma API reference
- `Loki_Log_Tool_Blueprint/` — Grafana Loki integration findings

---

## Step 2: Environment Setup

### Prerequisites
- Python 3.11 (required — the codebase uses `match` statements and type unions)
- Git

### Create and activate virtualenv
```powershell
cd "C:\Users\Umair.Alam\Desktop\kwikid_support_system\kwikid-ai-ingest\ai_project\fumadocs_ingest_service"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### Configure environment
```powershell
# Copy the template
copy .env.example .env
```

Then open `.env` and fill in these REQUIRED values (the service will not start without them):

```env
# OpenAI (REQUIRED)
OPENAI_API_KEY=sk-...
OPENAI_CHAT_API_KEY=sk-...       # Can be same key
INTELLIGENCE_LLM_API_KEY=sk-...  # Can be same key

# Supabase (REQUIRED for persistence)
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_KEY=eyJ...              # service_role key (bypasses RLS)

# Freshdesk (REQUIRED for webhook writes)
FRESHDESK_DOMAIN=getkwikid.freshdesk.com
FRESHDESK_API_KEY=your-freshdesk-api-key

# Freshdesk webhook secret (REQUIRED — same value as in Freshdesk admin)
FRESHDESK_WEBHOOK_SECRET=your-hmac-secret-here

# RAG API key (REQUIRED — service refuses to start without it)
RAG_API_KEY=your-strong-random-key

# Unity Bank (REQUIRED for production tools)
UNITY_ENABLED=true
UNITY_BASE_URL=https://vkyc360.unitybank.co.in
UNITY_USERNAME=unity
UNITY_PASSWORD=your-unity-password

# Loki (REQUIRED for log investigation)
LOKI_ENABLED=true
LOKI_SAAS_URL=https://utility-server-sfd.app.getkwikid.com
LOKI_SAAS_USERNAME=your-loki-username
LOKI_SAAS_PASSWORD=your-loki-password

# Asana (REQUIRED for L2 escalation)
ASANA_API_KEY=1/your-asana-key
ASANA_PROJECT_ID=your-project-gid
ASANA_WORKSPACE_ID=your-workspace-gid

# Sentry (REQUIRED for production error tracking)
SENTRY_DSN=https://...@us.sentry.io/...

# CRITICAL: Set these correctly for production
SUPPORT_AGENT_MODE=DRY_RUN   # Change to PRODUCTION only when fully verified
DEBUG_RAG=false              # NEVER set to true in production
FRESHDESK_WEBHOOK_ENFORCE_HMAC=true
```

> **Get actual secrets from:** the departing engineer via secure channel (Teams/Slack DM). Never commit `.env`. See `HANDOVER_CHECKLIST.md` for the full list of secrets to transfer.

---

## Step 3: Start the Server

```powershell
# Activate venv first
.venv\Scripts\activate

# Start with auto-reload (development)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Start without auto-reload (production-like)
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The server starts on `http://localhost:8000`. You'll see startup logs indicating which services wired successfully:

```
assembly: unity_tools registered outcomes=...
assembly: metrics_tools registered outcomes=...
assembly: loki_tools registered outcomes=...
assembly: intelligence_orchestrator wired provider=openai model=gpt-4o-mini
assembly: support_agent_runtime wired intelligence_wired=True
assembly: startup_validation PASSED warnings=0
```

### Health check
```
GET http://localhost:8000/health
```
Expected: `{"status": "ok"}`

---

## Step 4: Run the Test Suite

```powershell
# Standard regression suite (runs in ~45 seconds)
python -m pytest `
  tests/test_sprint2281_freshdesk_client.py `
  tests/test_sprint2281_webhook_verifier.py `
  tests/test_sprint2281_conversation_state.py `
  tests/test_sprint2281_ticket_created_handler.py `
  tests/test_sprint2281_ticket_updated_handler.py `
  tests/test_sprint2281_response_service.py `
  tests/test_sprint2281_idempotency.py `
  tests/test_sprint2282_hmac.py `
  tests/test_sprint2282_e2e.py `
  tests/test_sprint2283_e2e.py `
  tests/test_sprint246_investigation_orchestrator.py `
  tests/test_sprint247_business_pipeline.py `
  tests/test_sprint248_freshdesk_integration.py `
  --tb=line

# Master E2E validation (Sprint 2.64)
python -m pytest tests/test_sprint264_master_e2e_validation.py -v

# Full suite (~2.5 minutes, ~9000+ tests)
python -m pytest tests/ --tb=line
```

**Expected baselines:**
- Standard regression: 281/283 (2 pre-existing failures in Sprint 2.30.1 — not regressions)
- Sprint 2.46+2.47: 522/522
- Full suite: ~1267 pass, ~129 pre-existing failures (Sprint 2.47 documented)

---

## Step 5: Test Webhooks with Postman

### Ticket Created (triggers full L1 pipeline)

**POST** `http://localhost:8000/webhooks/freshdesk/ticket-created`

Headers:
```
Content-Type: application/json
X-Webhook-Token: your-webhook-secret
```

Body (OTP failure, missing URN → triggers clarification):
```json
{
  "ticket_id": 98765,
  "subject": "OTP not received by customer",
  "description": "<p>Customer says OTP SMS is not coming. Please help.</p>",
  "status": 2,
  "priority": 2,
  "tags": ["client:unity_bank"],
  "requester_id": 500001,
  "group_id": 1001,
  "created_at": "2026-07-31T10:00:00Z",
  "updated_at": "2026-07-31T10:00:00Z"
}
```

Body (VKYC audio issue, with URN + Session ID → triggers full investigation):
```json
{
  "ticket_id": 98766,
  "subject": "Audio not working during VKYC session",
  "description": "<p>Customer cannot hear anything during VKYC. URN: URN12345678, Session: KID-AB123456</p>",
  "status": 2,
  "priority": 1,
  "tags": ["client:unity_bank"],
  "requester_id": 500001,
  "group_id": 1001,
  "created_at": "2026-07-31T10:00:00Z",
  "updated_at": "2026-07-31T10:00:00Z"
}
```

### Ticket Updated (customer reply with clarification)

**POST** `http://localhost:8000/webhooks/freshdesk/ticket-updated`

Headers: same as above

Body:
```json
{
  "ticket_id": 98765,
  "action": "reply",
  "status": 6,
  "priority": 2,
  "tags": ["client:unity_bank"],
  "latest_comment": {
    "body": "<p>URN is URN98765432, session ID is KID-XY789012</p>",
    "body_text": "URN is URN98765432, session ID is KID-XY789012",
    "private": false,
    "user_id": 500001
  },
  "updated_at": "2026-07-31T10:05:00Z"
}
```

### Asana Task Completed (triggers L2 resolution closure)

**POST** `http://localhost:8000/webhooks/asana/task-completed`

Headers:
```
Content-Type: application/json
X-Hook-Secret: (from data/asana_webhook_secrets.json)
```

Body:
```json
{
  "events": [{
    "action": "completed",
    "resource": {
      "resource_type": "task",
      "gid": "1234567890123456"
    },
    "user": {
      "resource_type": "user",
      "gid": "9876543210987654"
    }
  }]
}
```

---

## Step 6: Architecture Orientation

Key files to read in priority order:

| File | Purpose |
|------|---------|
| `case_engine/nlp_router.py` | Entry point: NLU classification |
| `freshdesk/handlers.py` | Webhook handlers (ticket-created, ticket-updated) |
| `case_engine/runtime/support_agent_runtime.py` | Core agent orchestration |
| `case_engine/investigation/service.py` | Investigation pipeline |
| `intelligence/orchestrator.py` | LLM reasoning |
| `freshdesk/response_service.py` | Sole approved write gateway |
| `freshdesk/safety_gate.py` | ReplySafetyGate |
| `freshdesk/closure_guard.py` | ClosureFieldGuard |
| `runtime/assembly.py` | Component wiring (start here to understand the DI graph) |

For any sprint before touching a layer, read the corresponding SOT document first (CLAUDE.md §Sprint workflow).

---

## Quick Reference Commands

```powershell
# Activate virtualenv
.venv\Scripts\activate

# Start server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Run regression
python -m pytest tests/test_sprint2281_*.py tests/test_sprint2282_*.py tests/test_sprint2283_*.py tests/test_sprint246_investigation_orchestrator.py tests/test_sprint247_business_pipeline.py tests/test_sprint248_freshdesk_integration.py --tb=line

# Register Asana webhook (run ONCE on new server)
python scripts/register_asana_webhook.py

# Setup Asana project sections + custom fields (run ONCE)
python scripts/setup_asana_project.py

# Verify runtime environment
python scripts/verify_runtime.py
```
