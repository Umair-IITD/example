# Golden Path Architecture — Sprint 2.27.8

**Date:** 2026-06-16
**Status:** PRODUCTION READY

---

## What Is the Golden Path?

The Golden Path is the **one and only** production-approved integration route for processing Freshdesk support tickets through the KwikID AI support agent.

```
Freshdesk Webhook
      │
      ▼
POST /tickets/process  ← THE PRODUCTION ENTRY POINT
      │
      ▼
TicketOrchestrator.process_ticket(TicketContext)
      │
      ├─ cs.open_case(ticket_id, client, subject, description)
      │
      ▼
SupportAgentRuntime.run_case(case, message)
      │
      ├─ CLASSIFY      → CaseService.classify_case()
      ├─ SLOT_EXTRACT  → CaseService.receive_message()
      ├─ CLARIFY       → (if slots unfilled → WAITING state)
      ├─ WORKFLOW      → CaseService.start_workflow()  [deterministic playbook]
      ├─ NOTEGEN       → ResponseGenerationService.generate()
      ├─ L2CHECK       → workflow.escalated → needs_l2?
      ├─ ASANACREATE   → EngineeringEscalationService.create_ticket()
      │                  [DRY_RUN: skipped, ASANACREATE_DRY_RUN logged]
      └─ USERRESPONSE  → ResponseGenerationService (final draft)
      │
      ▼
AgentExecutionResult
      │
      ▼
TicketOrchestrationResult  (lifecycle state: CLOSED/WAITING/ESCALATED/FAILED)
      │
      ▼
JSON 200 response to caller
```

---

## The 5 Golden Path Endpoints

All endpoints are in `api/routes/tickets.py` with prefix `/tickets`.

| Method | Path | Purpose | Orchestrator Call |
|--------|------|---------|-------------------|
| POST | `/tickets/process` | Process new ticket end-to-end | `process_ticket(TicketContext)` |
| POST | `/tickets/{id}/resume` | Resume WAITING ticket after reply | `resume_ticket(ticket_id, message_text)` |
| POST | `/tickets/{id}/close` | Force-close a resolved ticket | `close_ticket(ticket_id=ticket_id)` |
| POST | `/tickets/{id}/escalate` | Force-escalate to L2/engineering | `escalate_ticket(ticket_id, reason)` |
| GET | `/tickets/{id}/status` | Query current lifecycle state | `get_lifecycle_state(ticket_id)` |

All endpoints:
- Require `X-API-Key` operator authentication via `Depends(require_operator)`
- Return 503 if `TicketOrchestrator` not wired in `app.state`
- Return 500 on unexpected exceptions (never let exceptions propagate)
- Never log or expose secrets

---

## NON-PRODUCTION PATH (Legacy)

`POST /webhook/{client}` (`api/routes/webhook.py`) is the Sprint 2.1 legacy entry point. It bypasses `TicketOrchestrator` entirely and calls the support agent pipeline directly. This path is **not** used for new integrations.

It is marked `# NON_PRODUCTION_PATH` in the source and preserved for backward compatibility only.

---

## Dry Run Gate

By default, `SupportAgentRuntime` runs in `DRY_RUN` mode. In this mode:
- All pipeline steps execute (CLASSIFY, SLOT_EXTRACT, WORKFLOW, etc.)
- The ASANACREATE step is **simulated** — no real Asana ticket is created
- `ASANACREATE_DRY_RUN` is logged in `steps_completed` instead of `ASANACREATE`
- `DRY_RUN_*` audit events are emitted for observability
- `engineering_result` is `None`

To enable production mode, set `SUPPORT_AGENT_MODE=PRODUCTION` in the environment.

---

## Assembly Requirements

For the golden path to be available, `ProductionRuntime` must have all of the following services built during `runtime/assembly.py`:

**CRITICAL (without these, app refuses traffic):**
- `workflow_engine`
- `case_service`
- `audit_logger`
- `action_gateway_service`
- `execution_service`

**GOLDEN_PATH (must be present for ticket processing):**
- `support_agent_runtime`
- `ticket_orchestrator`
- `router_service`
- `knowledge_orchestrator`
- `response_generation_service`
- `engineering_escalation_service`

Startup validation via `runtime/startup_validation.py` runs at assembly time and logs all check results. CRITICAL failures prevent the runtime from serving traffic.
