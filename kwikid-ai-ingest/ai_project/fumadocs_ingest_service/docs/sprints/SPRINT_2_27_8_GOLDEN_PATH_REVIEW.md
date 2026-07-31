# Golden Path Review — Sprint 2.27.8

**Date:** 2026-06-16
**Scope:** `api/routes/tickets.py` and its integration with `TicketOrchestrator`

---

## Overview

This document reviews the Golden Path API implementation. It covers:
- Contract correctness for each endpoint
- Error handling behaviour
- Auth enforcement
- Orchestrator lifecycle state mapping
- Known edge cases

---

## Endpoint Contracts

### POST /tickets/process

**Input:** `ProcessTicketRequest`
```json
{
  "ticket_id": "string (required)",
  "client": "string (required)",
  "subject": "string (required)",
  "description": "string (required)",
  "requester_email": "string (optional, default '')",
  "freshdesk_url": "string (optional, default '')",
  "metadata": "object (optional, default {})"
}
```

**Happy Path Response (200):** `TicketOrchestrationResult.to_dict()`
```json
{
  "orchestration_id": "...",
  "ticket_id": "...",
  "case_id": "...",
  "lifecycle_state": "CLOSED|WAITING|ESCALATED|FAILED",
  "success": true,
  "operation": "process",
  "executed_at": "ISO8601",
  "duration_ms": 0
}
```

**503:** `{"error": {"code": "SERVICE_UNAVAILABLE", "message": "..."}}`  
**500:** `{"error": {"code": "PROCESSING_ERROR", "message": "..."}}`

---

### POST /tickets/{ticket_id}/resume

**Input:** `ResumeTicketRequest`
```json
{
  "message_text": "string (required)"
}
```

**Orchestrator Call:** `resume_ticket(ticket_id=ticket_id, message_text=body.message_text)`

Returns `TicketOrchestrationResult.to_dict()` on success.

**Edge Case:** If the ticket is not in WAITING state, the orchestrator handles the state transition check internally and returns an appropriate result.

---

### POST /tickets/{ticket_id}/close

**Input:** None (no body required)

**Orchestrator Call:** `close_ticket(ticket_id=ticket_id)`

**Note:** This endpoint closes a ticket regardless of its current state. Callers should use status endpoint first if they need to verify state before closing.

---

### POST /tickets/{ticket_id}/escalate

**Input:** `EscalateTicketRequest`
```json
{
  "reason": "string (optional, default '')"
}
```

**Orchestrator Call:** `escalate_ticket(ticket_id=ticket_id, reason=body.reason)`

**Note:** Accepts empty `{}` body. Empty `reason` is valid and results in orchestrator using a default escalation reason.

---

### GET /tickets/{ticket_id}/status

**Response (200):**
```json
{
  "ticket_id": "...",
  "lifecycle_state": "RECEIVED|OPEN|PROCESSING|WAITING|CLOSED|ESCALATED|FAILED",
  "case_id": "...|null"
}
```

**404:** Returned when `get_lifecycle_state(ticket_id)` returns `None` (ticket not found in orchestrator registry).

---

## Auth Enforcement

All 5 endpoints use `Depends(require_operator)`. The `require_operator` dependency:
- Reads `X-API-Key` header
- Validates via `ApiKeyAuthenticator.check_operator(key)` using `hmac.compare_digest()`
- Returns 403 on missing or invalid key

Tests verify that auth is wired (`_auth=Depends(require_operator)` is present in each handler signature). Functional auth behaviour is covered by `test_sprint27_api.py`.

---

## Orchestrator Registry Design

The `TicketOrchestrator` maintains an in-memory registry mapping `ticket_id → lifecycle_state`. Implications:

| Scenario | Result |
|----------|--------|
| App restart | Registry cleared. All ticket state lost. |
| Concurrent requests for same ticket | In-memory dict is not thread-safe for concurrent writes. Single-worker Uvicorn is safe. |
| `get_lifecycle_state` for unknown ticket_id | Returns `None` → 404 response |

Future: Supabase persistence for the orchestrator registry will address restart durability.

---

## DRY_RUN Impact on Golden Path

When `SUPPORT_AGENT_MODE` is not set or is `DRY_RUN`:

1. `POST /tickets/process` executes fully through the pipeline
2. `SupportAgentRuntime.run_case()` runs all steps
3. At the ASANACREATE step, Asana is **NOT** called
4. `steps_completed` includes `ASANACREATE_DRY_RUN` instead of `ASANACREATE`
5. `engineering_result` is `None` in the returned result
6. `TicketOrchestrationResult.lifecycle_state` will be `CLOSED` or `ESCALATED` depending on workflow outcome

The caller sees a valid 200 response with a real result — just no Asana ticket was created.

---

## 503 Guard Design

The `_get_orchestrator()` helper reads `request.app.state.ticket_orchestrator`. It returns `None` if:
1. `app.state` has no `ticket_orchestrator` attribute (assembly failed)
2. `app.state.ticket_orchestrator` is `None` (service not built)

All 5 handlers check for `None` before any orchestrator call. This prevents uninitialized runtime from causing 500 errors with cryptic messages.

---

## Known Gaps (Not Bugs)

| Gap | Reason | Mitigation |
|----|--------|-----------|
| No request ID in ticket route responses | Request ID middleware is global | Check `X-Request-ID` response header |
| No pagination on status endpoint | Single-ticket lookup only | Use admin APIs for bulk operations |
| `metadata` field is not validated | Open dict — any JSON object | Orchestrator ignores unknown keys |
