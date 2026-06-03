# 04 — Workflow Engine

## 1. Why a Workflow Engine

A workflow engine is not an AI feature. It is the execution infrastructure that makes multi-step support transactions reliable.

Without a workflow engine, consider what happens when a VKYC triage flow has three steps — check session status, attempt bandwidth toggle, retry liveliness:

- If the container crashes after step 1 completes but before step 2 starts, step 1's result is lost. On restart, step 1 executes again. If step 1 sent an OTP, the customer receives a duplicate.
- If step 2 times out, there is no retry logic. The transaction silently fails. No human is notified.
- If step 2 succeeds but step 3 fails, there is no rollback. The session is in a partially modified state. The human agent takes over without knowing what changed.
- If two concurrent webhook events for the same ticket both start the workflow, there is no deduplication. Both run in parallel, both attempt the same mutations.

A workflow engine solves all of these by providing: durable execution (restart from last checkpoint), retry with backoff, saga-based compensation (rollback on partial failure), and idempotent step execution.

---

## 2. Engine Selection

Three paradigms evaluated for KwikID Phase 2:

### Durable Execution (Temporal)
Event-sourced execution model. Workflow code is ordinary Python/Go; Temporal's server replays function calls on crash recovery. Strong durability guarantees: survives container kill, network partition, and database failover. High infrastructure cost: requires a dedicated Temporal server (or Temporal Cloud), worker pool, and namespace management. Strong fit for long-running multi-day workflows.

**Assessment for KwikID:** Justified for Level 2 when multi-session sagas are required (e.g., VKYC session spans multiple calendar days while customer resolves bandwidth issue). Over-engineered for Level 1 single-session triage flows.

### State Graph (LangGraph)
In-memory graph execution with explicit state nodes and edges. Flexible for conversational cycles, easy to express conditional branching. No infrastructure-level durability: LangGraph state lives in process memory. If the worker crashes, the graph state is lost unless checkpointed externally (e.g., to PostgreSQL). Not designed for multi-hour transactions.

**Assessment for KwikID:** Correct fit for managing conversational micro-states within a session (Awaiting_Input, Model_Reasoning, Generating_Response). Not a replacement for a durable transaction engine.

### Declarative Playbooks (YAML State Machine)
Human-readable YAML defines states, actions, and transitions. Executed by a deterministic interpreter. No LLM autonomy within steps — LLM is only invoked to evaluate transition conditions. No additional infrastructure required.

**Assessment for KwikID:** Correct fit for Level 1. Zero infrastructure overhead. Version-controlled alongside code. Deterministic, auditable, easy to extend.

### Recommendation by Level

| Level | Workflow Engine | Rationale |
|-------|----------------|-----------|
| Level 1 | Declarative YAML playbooks | Zero infra overhead; deterministic; sufficient for single-session flows |
| Level 2 | Temporal for transactions + LangGraph for conversational state | Temporal for durability across sessions; LangGraph for in-session branching |
| Level 3 | Temporal + Kafka-triggered workflows | High-throughput event-driven activation |

---

## 3. Playbook Format

Each topic family has one or more playbooks. A playbook defines: initial state, all states, actions per state, and transitions. LLM autonomy is restricted to evaluating transition conditions using retrieved context; it cannot generate new states, call tools outside the playbook, or modify the playbook structure at runtime.

### Example: VKYC_Bandwidth_Failure

```yaml
playbook:
  id: VKYC_Bandwidth_Failure_Triage
  version: "1.0"
  topic: VKYC_Session_Failure
  initial_state: Assess_Network_State
  states:

    Assess_Network_State:
      description: "Retrieve bandwidth telemetry for the session."
      action: get_vkyc_session_status
      action_params:
        session_id: "{{slot.session_id}}"
      transitions:
        bandwidth_above_300kbps: Verify_Liveliness_Telemetry
        bandwidth_below_300kbps: Trigger_Fallback_Low_Bandwidth_SOP
        session_not_found: Escalate_To_Human_Workspace
        api_error: Retry_Or_Escalate

    Verify_Liveliness_Telemetry:
      description: "Check if liveliness command failure is bandwidth-related or device-related."
      action: get_vkyc_session_status
      action_params:
        session_id: "{{slot.session_id}}"
        detail_level: liveliness_commands
      transitions:
        liveliness_ok: Mark_Resolved_Network_Transient
        liveliness_failing_bandwidth: Trigger_Fallback_Low_Bandwidth_SOP
        liveliness_failing_device: Escalate_Device_Issue

    Trigger_Fallback_Low_Bandwidth_SOP:
      description: "Post SOP steps for low-bandwidth fallback to agent private note."
      action: post_private_note
      action_params:
        ticket_id: "{{case.ticket_id}}"
        note_content: "{{rag_sop_result.low_bandwidth_steps}}"
        tag: "ai-sop-vkyc-bandwidth"
      transitions:
        success: Await_Bandwidth_Resolution
        failure: Escalate_To_Human_Workspace

    Await_Bandwidth_Resolution:
      description: "Wait for agent or customer confirmation that network was improved."
      awaits_input: true
      slot_to_fill: bandwidth_resolved_confirmation
      clarification_prompt: "Please confirm: has the customer switched to a 4G or Wi-Fi network? (yes/no)"
      transitions:
        yes: Re_Evaluate_Liveliness
        no: Escalate_To_Human_Workspace
        timeout_30min: Escalate_To_Human_Workspace

    Re_Evaluate_Liveliness:
      description: "Re-check liveliness after bandwidth improvement."
      action: get_vkyc_session_status
      action_params:
        session_id: "{{slot.session_id}}"
        detail_level: liveliness_commands
      transitions:
        liveliness_ok: Mark_Resolved_Bandwidth_Fixed
        liveliness_failing: Escalate_To_Human_Workspace

    Mark_Resolved_Network_Transient:
      description: "Log outcome: transient network issue resolved."
      action: add_ticket_tag
      action_params:
        ticket_id: "{{case.ticket_id}}"
        tag: "ai-resolved-vkyc-network-transient"
      transitions:
        success: TERMINAL_RESOLVED

    Mark_Resolved_Bandwidth_Fixed:
      description: "Log outcome: bandwidth improved, liveliness passed."
      action: add_ticket_tag
      action_params:
        ticket_id: "{{case.ticket_id}}"
        tag: "ai-resolved-vkyc-bandwidth-fixed"
      transitions:
        success: TERMINAL_RESOLVED

    Escalate_Device_Issue:
      action: create_escalation_task
      action_params:
        reason: "Liveliness failure attributed to device hardware, not bandwidth"
        priority: high
      transitions:
        success: TERMINAL_ESCALATED

    Escalate_To_Human_Workspace:
      action: create_escalation_task
      action_params:
        reason: "{{escalation_reason}}"
        priority: high
      transitions:
        success: TERMINAL_ESCALATED

    Retry_Or_Escalate:
      description: "On transient API error: retry once, then escalate."
      retry_count: 1
      retry_delay_seconds: 5
      on_retry_exhausted: Escalate_To_Human_Workspace
      transitions:
        retry_success: Assess_Network_State
        retry_failed: Escalate_To_Human_Workspace
```

### Playbook Constraints (enforced by interpreter)
- Actions must be in the topic's tool allowlist (static check at playbook load time)
- Slot references (`{{slot.X}}`) must be declared in the topic's `required_slots` list
- `TERMINAL_RESOLVED` and `TERMINAL_ESCALATED` are built-in terminal states, not user-defined
- LLM is invoked only within `awaits_input` states (to generate clarification text) and for transition condition evaluation where the condition references unstructured ticket text
- Playbook interpreter is deterministic; it does not allow dynamic state generation

---

## 4. Failure Handling and Saga Pattern

### Transient Errors (API timeouts, 503s)
- Exponential backoff: initial delay 2 seconds, multiplier 2.0, maximum 3 retries
- On retry 3 failure: transition to dead-letter queue state, post escalation note to human
- Jitter applied: ±20% of delay to prevent thundering herd on shared downstream APIs

### Saga Compensation
When a multi-step workflow fails partway through, compensating transactions revert prior state changes in reverse order:

| Forward Action | Compensating Action |
|---------------|---------------------|
| `add_ticket_tag("ai-processing")` | `remove_ticket_tag("ai-processing")` |
| `update_ticket_priority(HIGH)` | `update_ticket_priority(ORIGINAL_PRIORITY)` |
| `post_private_note(diagnostic)` | Cannot delete (Freshdesk notes are immutable); append correction note |
| `send_otp_retry_notification` | Cannot unsend; log for human review |

Compensation is attempted in reverse order. If compensation itself fails, the case transitions to ESCALATED with reason `"saga_compensation_failed"` and the full saga log included in Transfer Context Payload.

### Dead-Letter Queue
A case enters the DLQ when:
- Maximum workflow retries are exhausted (3 retries by default, configurable per playbook)
- Saga compensation fails
- An unhandled exception occurs in the playbook interpreter

DLQ behavior:
- Case state frozen at `FAILED`
- Full playbook execution log written to `case_audit_log`
- Human operator notified via Freshdesk escalation task
- DLQ entries reviewed in weekly operational review

### Redis Circuit Breaker
All external API calls (Freshdesk API, VKYC service, OTP gateway) are wrapped by a Redis-backed circuit breaker. See `08_TOOL_CALLING_AND_IDEMPOTENCY.md` for full circuit breaker specification.

If the circuit is OPEN when a playbook step attempts an external call:
- The step immediately fails without attempting the call
- The playbook transitions to `Escalate_To_Human_Workspace`
- Reason logged: `"circuit_breaker_open:{service_name}"`

---

## 5. Level 2 State Separation

When Temporal and LangGraph are both active in Level 2, they manage different state boundaries. Mixing these concerns is a common error — do not let LangGraph manage durable transaction state, and do not let Temporal manage conversational micro-states.

### LangGraph Manages (Conversational States)
- `Awaiting_Input`: pending customer clarification
- `Model_Reasoning`: LLM evaluating transition conditions
- `Generating_Response`: LLM drafting diagnostic text or clarification question
- `Validation_Check`: groundedness and PII scan running

LangGraph state is in-memory (ephemeral). It does not survive container restart. That is acceptable because LangGraph manages sub-minute session states. Temporal's durable checkpoint is the recovery point.

### Temporal Manages (Transaction States)
- `Checking_PAN`: awaiting PAN OCR verification API response
- `OTP_Sent`: OTP dispatched, awaiting verification or timeout
- `OTP_Verified`: OTP confirmed; eligibility for next step unlocked
- `Liveliness_Pending`: liveliness check dispatched
- `Human_Takeover`: case assigned to human, Temporal workflow awaiting external signal to close

Temporal state is event-sourced. On container restart, Temporal replays the workflow from the last persisted event. The worker processes events idempotently (Temporal's replay mechanism ensures this).

### PostgreSQL Saga Tables
Act as a secondary durable checkpoint, independent of Temporal. This means: even if the Temporal server has a data loss event, the case state can be reconstructed from the Saga table for audit and manual recovery purposes.

Schema: see `06_MEMORY_AND_CONTEXT_MODEL.md`, Section 3 (`case_checkpoints` table definition).

State is written to the Saga table at every major state transition (NEW, CLASSIFYING, TRIAGE_COMPLETE, WORKFLOW_ACTIVE, ACTION_PENDING, ESCALATED, RESOLVED, FAILED). Not at every LangGraph micro-state (too granular, too high write volume).
