# External Integration Readiness — Sprint 2.27.8

**Date:** 2026-06-16
**Audience:** Freshdesk integration team, KwikID ops team

---

## What's Ready for Integration

### Freshdesk → KwikID

The `POST /tickets/process` endpoint is ready to receive ticket payloads. Freshdesk should send a webhook to this endpoint when a new support ticket is created.

**Endpoint:** `POST https://<host>/tickets/process`

**Headers:**
```
Content-Type: application/json
X-API-Key: <operator-api-key>
```

**Request Body:**
```json
{
  "ticket_id": "freshdesk_ticket_id_as_string",
  "client": "client_identifier",
  "subject": "Ticket subject line",
  "description": "Full ticket description text",
  "requester_email": "customer@example.com",
  "freshdesk_url": "https://yourcompany.freshdesk.com/tickets/12345",
  "metadata": {}
}
```

**Success Response (200):**
```json
{
  "orchestration_id": "orch-...",
  "ticket_id": "freshdesk_ticket_id_as_string",
  "case_id": "case-...",
  "lifecycle_state": "CLOSED",
  "success": true,
  "operation": "process",
  "executed_at": "2026-06-16T00:00:00+00:00",
  "duration_ms": 245
}
```

---

## Lifecycle State Reference

| State | Meaning | Next Action |
|-------|---------|-------------|
| `CLOSED` | Agent resolved the ticket | Update Freshdesk ticket as resolved |
| `WAITING` | Agent needs customer reply | Add agent reply to Freshdesk, wait for customer response |
| `ESCALATED` | L2/engineering ticket created | Notify L2 team; ticket in Asana |
| `FAILED` | Agent encountered an unrecoverable error | Route to human agent; check audit log |

---

## Customer Reply Flow (Resume)

When a customer replies to a WAITING ticket, Freshdesk should send:

**Endpoint:** `POST https://<host>/tickets/{ticket_id}/resume`
```json
{
  "message_text": "Customer's reply text here"
}
```

---

## Prerequisite Checklist

### Infrastructure

- [ ] FastAPI app deployed and health check returns 200
- [ ] Supabase connection string configured in `.env`
- [ ] CRITICAL startup validation passes (check server logs at startup)
- [ ] At least one operator API key configured in environment

### Dry Run Validation (Do This First)

Before enabling production mode, validate the full pipeline in DRY_RUN:

1. Send 5-10 real Freshdesk ticket payloads to `POST /tickets/process`
2. Verify responses have `lifecycle_state` values (CLOSED/WAITING/ESCALATED/FAILED)
3. Verify audit events appear in Supabase `audit_events` table
4. Verify `DRY_RUN_EXECUTION` events appear (confirms DRY_RUN is active)
5. Verify no Asana tickets were created

### Production Enablement

Only after DRY_RUN validation passes:

1. Set `SUPPORT_AGENT_MODE=PRODUCTION` in `.env`
2. Configure `ASANA_API_KEY` and `ASANA_ENGINEERING_PROJECT_ID` in `.env`
3. Restart the application
4. Send one test ticket
5. Verify Asana ticket created in engineering project
6. Verify `ASANACREATE` (not `ASANACREATE_DRY_RUN`) in `steps_completed`

---

## Rollback Plan

If production mode causes issues:

1. Set `SUPPORT_AGENT_MODE=DRY_RUN` in `.env` (or remove the env var)
2. Restart the application
3. The system reverts to safe DRY_RUN mode immediately
4. No data loss (in-memory orchestrator registry is reset on restart, which is expected)

---

## API Key Management

API keys are managed via `OPERATOR_API_KEYS` environment variable (comma-separated list). All ticket endpoints require an operator key.

The `X-API-Key` header value is compared using `hmac.compare_digest()` — timing-safe comparison. No key is ever logged.

---

## Monitoring Signals

Look for these in Supabase audit_events table:

| Event | Significance |
|-------|-------------|
| `TICKET_RECEIVED` | Ticket entered orchestrator |
| `CASE_OPENED` | Case created for ticket |
| `DRY_RUN_EXECUTION` | Workflow ran in simulation mode |
| `DRY_RUN_ACTION` | L2 escalation simulated |
| `ASANACREATE` | Real Asana ticket created (PRODUCTION mode) |
| `STARTUP_VALIDATION_FAILED` | CRITICAL service missing at startup |
| `INVARIANT_VIOLATION` | Logic invariant violated — investigate |

---

## Contact

- Engineering issues: Create ticket in Asana Engineering project
- API key requests: Contact KwikID ops team
- Audit log access: Supabase dashboard → `audit_events` table
