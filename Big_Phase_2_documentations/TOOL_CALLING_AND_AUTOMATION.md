# Tool Calling and Automation — Big Phase 2

## 1. Design Philosophy

Tool calling is the highest-risk capability in Phase 2. Unlike generation (where a wrong answer can be corrected), an executed action (a ticket updated, an Asana task created, a customer email sent) may have real-world consequences that are difficult or impossible to reverse.

The governing principle: **every action the system can take must be explicitly authorized by a human-defined policy, must be auditable, must be reversible or explicitly acknowledged as irreversible, and must fail safe.**

"Fail safe" means: if the tool execution system fails, the system returns an answer-only response without taking any action. It does not retry in the background, it does not guess at authorization, it does not execute partial actions.

---

## 2. What Tools Are and Are Not

### 2.1 Tools Are

- Deterministic functions with well-defined inputs and outputs
- Actions with explicit permission requirements
- Operations that are logged in full before and after execution
- Capabilities that can be disabled per-tenant with a feature flag

### 2.2 Tools Are Not

- Autonomous agents
- Self-directing chains of tool calls
- Actions the LLM can "decide" to take without a governance gate
- Operations with hidden side effects
- Free-form code execution

---

## 3. Tool Registry Design

### 3.1 Tool Contract

Every tool must satisfy this interface:

```python
@dataclass(frozen=True)
class ToolDefinition:
    name: str                          # Unique identifier
    description: str                   # Human-readable description for LLM
    risk_level: ToolRisk               # LOW / MEDIUM / HIGH / CRITICAL
    reversible: bool                   # Can this action be undone?
    requires_approval: bool            # Always requires human approval?
    idempotency_key_fields: list[str]  # Fields that uniquely identify this action
    parameters: dict[str, FieldSchema] # Input parameter schemas
    output_schema: dict                # Expected output schema
    tenant_allowlist: list[str] | None # None = all tenants; list = specific tenants only
    rate_limit_per_minute: int         # Max executions per minute per tenant
    
class ToolRisk(str, Enum):
    LOW = "low"          # Read-only, no side effects (fetch ticket status)
    MEDIUM = "medium"    # Writes, but easily reversible (add tag to ticket)
    HIGH = "high"        # Writes, hard to reverse (update ticket status, close ticket)
    CRITICAL = "critical" # Irreversible or significant impact (send customer email)
```

### 3.2 Registered Tools (Phase 2B Initial Set)

```python
TOOL_REGISTRY = {
    "get_ticket_status": ToolDefinition(
        name="get_ticket_status",
        description="Fetch the current status, priority, and assigned agent of a Freshdesk ticket",
        risk_level=ToolRisk.LOW,
        reversible=True,
        requires_approval=False,
        idempotency_key_fields=["ticket_id"],
        parameters={"ticket_id": FieldSchema(type="string", required=True)},
        rate_limit_per_minute=60,
    ),
    "get_service_status": ToolDefinition(
        name="get_service_status",
        description="Fetch current operational status of KwikID services from Uptime Kuma",
        risk_level=ToolRisk.LOW,
        reversible=True,
        requires_approval=False,
        idempotency_key_fields=[],  # No side effects
        parameters={},
        rate_limit_per_minute=30,
    ),
    "update_ticket_priority": ToolDefinition(
        name="update_ticket_priority",
        description="Update the priority level of a Freshdesk ticket",
        risk_level=ToolRisk.MEDIUM,
        reversible=True,
        requires_approval=False,  # Auto-approved if confidence == high AND risk < 0.4
        idempotency_key_fields=["ticket_id", "priority"],
        parameters={
            "ticket_id": FieldSchema(type="string", required=True),
            "priority": FieldSchema(type="string", enum=["low", "medium", "high", "urgent"]),
        },
        rate_limit_per_minute=10,
    ),
    "add_ticket_tag": ToolDefinition(
        name="add_ticket_tag",
        description="Add a tag to a Freshdesk ticket for categorization",
        risk_level=ToolRisk.MEDIUM,
        reversible=True,
        requires_approval=False,
        idempotency_key_fields=["ticket_id", "tag"],
        parameters={
            "ticket_id": FieldSchema(type="string", required=True),
            "tag": FieldSchema(type="string", pattern=r"^[a-z0-9_-]{1,50}$"),
        },
        rate_limit_per_minute=20,
    ),
    "create_asana_task": ToolDefinition(
        name="create_asana_task",
        description="Create an escalation task in Asana for human agent review",
        risk_level=ToolRisk.MEDIUM,
        reversible=True,   # Task can be deleted
        requires_approval=False,  # Created during escalation path (already approved by SOP gate)
        idempotency_key_fields=["ticket_id"],  # One task per ticket
        parameters={
            "ticket_id": FieldSchema(type="string", required=True),
            "title": FieldSchema(type="string", max_length=200),
            "description": FieldSchema(type="string", max_length=2000),
            "priority": FieldSchema(type="string", enum=["low", "medium", "high"]),
        },
        rate_limit_per_minute=5,
    ),
    "post_internal_note": ToolDefinition(
        name="post_internal_note",
        description="Post a private internal note to a Freshdesk ticket (not visible to customer)",
        risk_level=ToolRisk.MEDIUM,
        reversible=False,  # Notes cannot be deleted via API
        requires_approval=False,
        idempotency_key_fields=["ticket_id", "note_hash"],
        parameters={
            "ticket_id": FieldSchema(type="string", required=True),
            "content": FieldSchema(type="string", max_length=5000),
        },
        rate_limit_per_minute=10,
    ),
    "post_customer_reply": ToolDefinition(
        name="post_customer_reply",
        description="Post a public reply to a customer on a Freshdesk ticket",
        risk_level=ToolRisk.CRITICAL,
        reversible=False,  # Customer has already seen it
        requires_approval=True,  # ALWAYS requires approval or automation_safe=True
        idempotency_key_fields=["ticket_id", "reply_hash"],
        parameters={
            "ticket_id": FieldSchema(type="string", required=True),
            "content": FieldSchema(type="string", max_length=10000),
            "to_email": FieldSchema(type="string", format="email"),
        },
        rate_limit_per_minute=5,
    ),
}
```

---

## 4. Action Governance Model

### 4.1 Approval Decision Matrix

```
Tool Risk Level    |  Automation Safe?  | Requires Approval?
-------------------|--------------------|--------------------
LOW                |  ANY               | Never (auto-approve)
MEDIUM             |  True              | Never (auto-approve)
MEDIUM             |  False             | Always (queue for human)
HIGH               |  True + confidence==high | Configurable (MEDIUM_AUTO_APPROVE)
HIGH               |  False OR confidence!=high | Always
CRITICAL           |  ANY               | Always
```

```python
class ApprovalGate:
    def evaluate(
        self,
        tool: ToolDefinition,
        governance: GovernanceResult,    # Phase 1 output
        action_plan: ActionPlan,
    ) -> ApprovalDecision:
        
        # CRITICAL tools always require approval
        if tool.risk_level == ToolRisk.CRITICAL:
            return ApprovalDecision.REQUIRES_HUMAN
        
        # Tool explicitly requires approval
        if tool.requires_approval:
            return ApprovalDecision.REQUIRES_HUMAN
        
        # Low-risk tools: always auto-approve
        if tool.risk_level == ToolRisk.LOW:
            return ApprovalDecision.AUTO_APPROVED
        
        # Medium/High: requires automation_safe from Phase 1 governance
        if not governance.automation_safe:
            return ApprovalDecision.REQUIRES_HUMAN
        
        # Medium risk + automation_safe: auto-approve
        if tool.risk_level == ToolRisk.MEDIUM:
            return ApprovalDecision.AUTO_APPROVED
        
        # High risk + automation_safe: check per-tenant config
        if tool.risk_level == ToolRisk.HIGH:
            if self._config.high_risk_auto_approve_enabled:
                return ApprovalDecision.AUTO_APPROVED
            return ApprovalDecision.REQUIRES_HUMAN
        
        return ApprovalDecision.REQUIRES_HUMAN  # Fail safe
```

### 4.2 Risk Score Computation

In addition to the categorical risk level, compute a numeric risk score (0.0–1.0) for each action plan:

```python
def compute_risk_score(
    tool: ToolDefinition,
    governance: GovernanceResult,
    memory: MemoryContext,
) -> float:
    base_risk = {
        ToolRisk.LOW: 0.1,
        ToolRisk.MEDIUM: 0.3,
        ToolRisk.HIGH: 0.6,
        ToolRisk.CRITICAL: 0.9,
    }[tool.risk_level]
    
    # Modifiers
    if not tool.reversible:
        base_risk += 0.15
    if governance.confidence != "high":
        base_risk += 0.20
    if memory and memory.escalation_count > 0:
        base_risk += 0.10  # Customer has been escalated before — be careful
    if governance.requires_human:
        base_risk += 0.30
    
    return min(base_risk, 1.0)
```

---

## 5. Action Execution Pipeline

```
ActionPlan received (list of ToolCall objects)
    │
    ├── For each tool call:
    │   ├── 1. Validate: tool exists in TOOL_REGISTRY
    │   ├── 2. Validate: parameters match ToolDefinition schema
    │   ├── 3. Validate: tenant is in tool.tenant_allowlist (if set)
    │   ├── 4. Validate: rate limit not exceeded (Redis sliding window)
    │   ├── 5. Compute risk score
    │   ├── 6. ApprovalGate.evaluate() → AUTO_APPROVED or REQUIRES_HUMAN
    │   │
    │   ├── REQUIRES_HUMAN:
    │   │   ├── Write to approval_requests table
    │   │   ├── Notify agent (Telegram / Freshdesk private note)
    │   │   └── Return pending status (do NOT execute)
    │   │
    │   └── AUTO_APPROVED:
    │       ├── Generate idempotency key (sha256 of key fields)
    │       ├── Check tool_executions for duplicate (idempotency check)
    │       │   DUPLICATE: return cached result (do NOT re-execute)
    │       │   NEW: continue
    │       ├── Write to tool_executions (status=pending)
    │       ├── Execute tool handler
    │       │   SUCCESS: update tool_executions (status=completed, result=...)
    │       │   FAILURE: update tool_executions (status=failed, error=...)
    │       │            log structured error
    │       │            apply retry policy (if idempotent and retriable)
    │       └── Return result
```

---

## 6. Idempotency

Tool executions must be idempotent. A retry or a duplicate request must not execute the same action twice.

```python
class IdempotencyManager:
    def get_or_create_execution(
        self,
        tool_name: str,
        idempotency_key: str,  # sha256 of key fields
        client: str,
    ) -> tuple[bool, ToolExecution | None]:
        """
        Returns (is_duplicate, existing_execution).
        If is_duplicate=True, caller must return existing_execution.result
        without executing the tool again.
        """
        existing = (
            self._db.table("tool_executions")
            .select("*")
            .eq("tool_name", tool_name)
            .eq("idempotency_key", idempotency_key)
            .eq("client", client)
            .eq("status", "completed")
            .maybe_single()
            .execute()
        )
        if existing.data:
            return True, ToolExecution(**existing.data)
        
        # Create pending record
        new_record = self._db.table("tool_executions").insert({
            "tool_name": tool_name,
            "idempotency_key": idempotency_key,
            "client": client,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
        }).execute()
        return False, None
```

**Race condition**: Two concurrent requests with the same idempotency key will both pass the check and attempt execution. Mitigation: use Supabase's `ON CONFLICT DO NOTHING` with a unique constraint on `(tool_name, idempotency_key, client)`:

```sql
CREATE UNIQUE INDEX idx_tool_executions_idempotency 
ON tool_executions(tool_name, idempotency_key, client);
```

The second insert fails silently; the duplicate check catches it on retry.

---

## 7. Audit Trail

Every tool execution is logged in full, regardless of success or failure:

```sql
CREATE TABLE tool_executions (
    execution_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_name TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    client TEXT NOT NULL,
    session_id TEXT,
    ticket_id TEXT,
    status TEXT NOT NULL,        -- pending / completed / failed / rejected / duplicate
    risk_level TEXT,
    risk_score FLOAT,
    approval_decision TEXT,      -- auto_approved / requires_human / rejected
    approval_by TEXT,            -- null for auto; agent email for manual
    parameters JSONB,            -- full input parameters
    result JSONB,                -- full tool output
    error TEXT,                  -- error message if failed
    governance_confidence TEXT,  -- Phase 1 confidence at time of execution
    governance_automation_safe BOOLEAN,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    duration_ms FLOAT
);

CREATE UNIQUE INDEX ON tool_executions(tool_name, idempotency_key, client);
CREATE INDEX ON tool_executions(client, created_at DESC);
CREATE INDEX ON tool_executions(ticket_id);
CREATE INDEX ON tool_executions(status);
```

**Retention**: Tool execution audit records are retained for 2 years (compliance requirement). Do not delete. Archive to cold storage after 6 months.

---

## 8. Retry Logic

```python
RETRY_POLICY = {
    ToolRisk.LOW: RetryPolicy(max_attempts=3, backoff_base_s=0.5, retriable_errors=["timeout", "connection"]),
    ToolRisk.MEDIUM: RetryPolicy(max_attempts=2, backoff_base_s=1.0, retriable_errors=["timeout"]),
    ToolRisk.HIGH: RetryPolicy(max_attempts=1, backoff_base_s=None, retriable_errors=[]),  # No retry for HIGH
    ToolRisk.CRITICAL: RetryPolicy(max_attempts=1, backoff_base_s=None, retriable_errors=[]),  # No retry for CRITICAL
}
```

**Design rationale**: High and Critical risk tools are not retried because the first execution may have partially succeeded. Retrying could double an action (e.g., send the same customer reply twice). For these, a failed execution is queued for human review.

---

## 9. Rollback Strategy

Rollback is tool-specific. For each tool, define the rollback operation:

| Tool | Reversible | Rollback Operation |
|------|-----------|-------------------|
| `get_ticket_status` | n/a | No action needed (read-only) |
| `get_service_status` | n/a | No action needed (read-only) |
| `update_ticket_priority` | Yes | Call `update_ticket_priority` with the previous priority |
| `add_ticket_tag` | Yes | Call `remove_ticket_tag` with the same tag |
| `create_asana_task` | Yes | Call `delete_asana_task` with the task ID |
| `post_internal_note` | No | Cannot delete; log "note was in error" as follow-up |
| `post_customer_reply` | No | Send follow-up reply: "We apologize — our previous message contained an error. Please disregard." |

**Rollback is never automatic**. It is queued as a human-approved action in the `approval_requests` table. An agent reviews and approves the rollback.

---

## 10. SOP Recommendation Engine

The `SOP Recommendation Engine` analyzes patterns in the `retrieval_quality_log` to identify gaps in SOP coverage:

### 10.1 Gap Detection

```sql
-- Queries with no SOP match in the last 30 days, grouped by intent category
SELECT 
    intent_category,
    COUNT(*) as no_match_count,
    AVG(top_similarity) as avg_similarity
FROM retrieval_quality_log
WHERE workflow_match_type IN ('weak_match', 'no_match')
  AND timestamp > NOW() - INTERVAL '30 days'
  AND client = 'unity_bank'
GROUP BY intent_category
ORDER BY no_match_count DESC;
```

If `no_match_count > 5` for a category → flag for SOP creation.

### 10.2 Auto-SOP Draft Generation

When a gap is detected:

1. Fetch the last 10 resolved tickets in that category from `memory_episodes` (where `resolution_confirmed=true`)
2. Cluster by resolution type
3. Generate a draft SOP from the most common resolution pattern:

```
PROMPT:
"You are a support documentation author. Based on these resolved support tickets:
[ticket summaries]

Write a Standard Operating Procedure document following this format:
- Title
- Issue Description
- Affected Platforms (iOS/Android/Both)
- Prerequisites
- Resolution Steps (numbered)
- Escalation Criteria
- Root Cause

Focus on actionable, step-by-step instructions. Do not include customer names or PII."
```

4. Save the draft to `sop_suggestions` table with status `draft`
5. Notify a support team lead via Telegram: "New SOP draft available for review"

**Critical constraint**: Auto-generated SOP drafts are NEVER automatically ingested into the knowledge base. They require human review, edit, and explicit approval before ingestion. The `sop_suggestions` table has no path to the ingestion pipeline without a human step.

---

## 11. Permission Boundaries

### 11.1 What the System Can NEVER Do

These are hard architectural constraints enforced by the absence of tools, not by policy:

1. **No shell execution**: No tool in the registry executes arbitrary code or shell commands
2. **No database writes to knowledge tables**: No tool can write to `documents`, `rag_sop_chunks`, or `rag_knowledge_chunks` (ingestion is a separate, human-triggered pipeline)
3. **No credential access**: No tool can read API keys, environment variables, or authentication tokens
4. **No cross-tenant access**: All tool calls are validated against the `client` from the authenticated request
5. **No bulk operations**: No tool can affect more than one ticket/task/customer per execution call

### 11.2 Tenant Tool Configuration

Tenants can restrict which tools are available to them:

```sql
CREATE TABLE tenant_tool_config (
    client TEXT PRIMARY KEY,
    enabled_tools TEXT[],        -- NULL = all tools; list = only these tools
    auto_approve_medium BOOLEAN DEFAULT TRUE,
    auto_approve_high BOOLEAN DEFAULT FALSE,
    require_approval_for TEXT[]  -- Additional tools requiring approval for this tenant
);
```

A tenant that does not want automated Asana task creation can set `enabled_tools` to exclude `create_asana_task`.
