# Phase 2B — Action Systems Design

## 1. What Phase 2B Adds

Phase 2B extends the platform from a **response generator** to a **supervised action executor**. The system can now:

- Call external APIs during or after response generation
- Update ticket metadata automatically when governance conditions are met
- Create escalation tasks programmatically
- Draft SOP recommendations from resolved ticket patterns
- Adapt retrieval parameters based on accumulated feedback signals

Phase 2B does NOT add:
- Autonomous multi-step agent loops
- Self-directed action planning without governance gates
- Any action that bypasses the Phase 1 governance engine

Everything in Phase 2B is an extension of the `automation_safe` gate from Phase 1. If `automation_safe=False`, Phase 2B actions default to human-review queuing.

---

## 2. Operational Workflow Execution

### 2.1 How Action Planning Integrates

After `GovernanceEngine` produces its output, a new `ActionPlanner` evaluates whether any tool calls are appropriate:

```python
class ActionPlanner:
    def evaluate(
        self,
        intent: IntentResult,
        governance: GovernanceResult,
        ticket_context: TicketContext,
        memory: MemoryContext,
    ) -> ActionPlan:
        actions = []
        
        # Rule 1: Auto-categorize ticket if intent confidence is high
        if intent.confidence > 0.80 and governance.confidence == "high":
            actions.append(ToolCall(
                tool="add_ticket_tag",
                args={"ticket_id": ticket_context.ticket_id, 
                      "tag": intent.category.value},
                rationale="High-confidence intent classification",
            ))
        
        # Rule 2: Escalate priority for repeated failures
        if (memory and intent.category in memory.recurring_issues 
                and len(memory.recurring_issues) >= 2):
            actions.append(ToolCall(
                tool="update_ticket_priority",
                args={"ticket_id": ticket_context.ticket_id, "priority": "high"},
                rationale="Customer has experienced this issue multiple times",
            ))
        
        # Rule 3: Create Asana task if requires_human
        if governance.requires_human:
            actions.append(ToolCall(
                tool="create_asana_task",
                args={
                    "ticket_id": ticket_context.ticket_id,
                    "title": f"Manual review: {intent.category.value}",
                    "description": f"Confidence: {governance.confidence}. "
                                  f"Match type: {governance.workflow_match_type}. "
                                  f"RAG answer attached in private note.",
                    "priority": "medium",
                },
                rationale="Governance requires human review",
            ))
        
        # Rule 4: Fetch service status if infrastructure issue suspected
        if intent.category in [
            IntentCategory.LIVENESS_CHECK_STUCK,
            IntentCategory.OTP_DELIVERY_FAILURE,
        ] and governance.workflow_match_type == "no_match":
            actions.append(ToolCall(
                tool="get_service_status",
                args={},
                rationale="No SOP found — check if this is a live service outage",
                timing="before_generation",  # Execute before response generation
            ))
        
        return ActionPlan(
            actions=actions,
            total_risk_score=self._compute_plan_risk(actions, governance),
        )
```

### 2.2 Timing: Before vs. After Generation

Some tools are useful for enriching the generation context (e.g., `get_service_status` — inject "all systems operational" or "liveness API degraded" into the prompt). Others are post-generation side effects (e.g., `add_ticket_tag`, `create_asana_task`).

```python
class ActionTiming(str, Enum):
    BEFORE_GENERATION = "before_generation"  # Result injected into prompt
    AFTER_GENERATION = "after_generation"    # Side effect, not in prompt
    
# In the main request handler:
pre_gen_actions = [a for a in action_plan.actions if a.timing == ActionTiming.BEFORE_GENERATION]
post_gen_actions = [a for a in action_plan.actions if a.timing == ActionTiming.AFTER_GENERATION]

# Execute pre-generation actions synchronously (they enrich the response)
pre_gen_results = await action_executor.execute_batch(pre_gen_actions, governance)

# Generate response with enriched context
response = await generator.generate(context + pre_gen_results.context_additions)

# Execute post-generation actions as background tasks (they don't affect response)
for action in post_gen_actions:
    background_tasks.add_task(action_executor.execute, action, governance)
```

---

## 3. Human Approval Architecture

### 3.1 Approval Queue

When `ApprovalGate` returns `REQUIRES_HUMAN`, the action is placed in a queue:

```sql
CREATE TABLE approval_requests (
    request_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_name TEXT NOT NULL,
    tool_args JSONB NOT NULL,
    client TEXT NOT NULL,
    session_id TEXT,
    ticket_id TEXT,
    risk_level TEXT,
    risk_score FLOAT,
    rationale TEXT,
    status TEXT DEFAULT 'pending',   -- pending / approved / rejected / expired
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ,          -- auto-reject if not reviewed within SLA
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    review_note TEXT
);
```

**SLA for approval**: Default 4 hours. After 4 hours without review, status changes to `expired` and the action is permanently dropped (not auto-executed). The ticket remains open for human handling.

### 3.2 Approval Notification

When an approval is queued, notify the support team:

**Telegram notification** (existing channel):
```
🔔 Action Pending Approval
Ticket: #78901 | Client: unity_bank
Tool: post_customer_reply
Risk: CRITICAL (score: 0.82)
Reason: Customer reply requires approval — confidence was medium
Approve: /approve {request_id}
Reject: /reject {request_id}
Expires: 4 hours
```

**Freshdesk private note** (on the ticket):
```
[AI Automation — Pending Approval]
The system generated a response but requires human approval before sending.
Risk level: CRITICAL | Reason: Medium confidence
Please review and send manually, or use /approve {request_id} in Telegram.
```

### 3.3 Approval API

```
POST /actions/approve
Body: {request_id: "...", agent_email: "..."}
Auth: X-API-Key (same as main service)

POST /actions/reject  
Body: {request_id: "...", agent_email: "...", note: "..."}

GET /actions/pending?client=unity_bank
Returns: list of pending approval requests
```

---

## 4. Adaptive Retrieval

### 4.1 Feedback-Driven Retrieval Weights

Every thumbs-up or thumbs-down response updates a `chunk_feedback_weights` table:

```sql
CREATE TABLE chunk_feedback_weights (
    chunk_id UUID NOT NULL,          -- REFERENCES documents(id)
    client TEXT NOT NULL,
    intent_category TEXT,
    positive_count INTEGER DEFAULT 0,
    negative_count INTEGER DEFAULT 0,
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (chunk_id, client)
);
```

At retrieval time, apply a multiplicative weight adjustment:

```python
def compute_feedback_weight(chunk_id: str, client: str) -> float:
    """
    Returns a multiplier for the chunk's RRF score.
    Range: [0.7, 1.3] — bounded to prevent extreme amplification.
    """
    weights = get_feedback_weights(chunk_id, client)
    if not weights:
        return 1.0
    
    pos, neg = weights.positive_count, weights.negative_count
    total = pos + neg
    if total < 3:
        return 1.0  # Not enough signal yet
    
    # Wilson score lower bound (conservative estimate)
    ratio = pos / total
    # Map [0.3, 0.9] ratio to [0.7, 1.3] multiplier
    multiplier = 0.7 + (ratio / 0.9) * 0.6
    return max(0.7, min(1.3, multiplier))
```

**Important constraint**: The feedback weight adjustment is applied AFTER RRF fusion, as a post-processing step. It does not change the pgvector scan — only the final ranking of RRF results. This preserves retrieval coverage (no SOP chunks are excluded) while adjusting preference ordering.

### 4.2 Retrieval Profile Tuning from Analytics

The `retrieval_quality_log` table enables systematic tuning:

```sql
-- Find intents where retrieval often fails (no_match rate > 20%)
SELECT 
    intent_category,
    COUNT(*) as total,
    SUM(CASE WHEN workflow_match_type = 'no_match' THEN 1 ELSE 0 END) as no_match_count,
    AVG(top_similarity) as avg_similarity
FROM retrieval_quality_log
WHERE timestamp > NOW() - INTERVAL '7 days'
GROUP BY intent_category
HAVING SUM(CASE WHEN workflow_match_type = 'no_match' THEN 1 ELSE 0 END) / COUNT(*)::float > 0.2;
```

For intents with high no-match rates: lower `min_similarity` threshold or increase `semantic_top_k` in the retrieval profile. For intents with high automation rates and positive feedback: the profile is working, don't change it.

---

## 5. Workflow Decomposition

For complex tickets involving multiple issues, the system must decompose the query:

### 5.1 Multi-Issue Detection

IntentClassifier sets `intent.category = MULTI_ISSUE` when it detects compound problems. The `WorkflowDecomposer` then splits the query:

```python
class WorkflowDecomposer:
    def decompose(
        self,
        query_text: str,
        intent: IntentResult,
    ) -> list[SubQuery]:
        """
        Only invoked when intent.category == MULTI_ISSUE.
        Returns a list of sub-queries, each treated as an independent retrieval.
        """
        # Use LLM to decompose (this is a structured, bounded call — not open-ended)
        prompt = f"""
        This support ticket contains multiple issues. Extract each distinct issue 
        as a separate sub-query. Return as a JSON array of strings.
        Max 3 sub-queries. Focus on actionable KwikID support issues only.
        
        Ticket: {query_text[:500]}
        """
        # ... call LLM with structured output
```

**Limit**: Maximum 3 sub-queries. If decomposition produces more, take the first 3 by priority.

**Retrieval**: Each sub-query is retrieved independently. Results are merged and de-duplicated by `chunk_id` before governance. The governance engine receives the merged context with all sub-query retrievals labeled.

**Generation**: The LLM is instructed to address each sub-issue in order.

### 5.2 Decomposition Guard

Multi-issue decomposition adds ~1–2 additional LLM calls. It is only triggered when:
1. `intent.category == MULTI_ISSUE` AND
2. `len(query_text) > 200` (short queries are unlikely to be truly multi-issue) AND
3. `MULTI_ISSUE_DECOMPOSITION_ENABLED=true` (feature flag)

If any condition fails, fall back to standard single-query retrieval.

---

## 6. Auto-SOP Generation Pipeline

See `TOOL_CALLING_AND_AUTOMATION.md` §10 for the detailed design. Summary:

```
Gap detection (weekly SQL query on retrieval_quality_log)
    → Alert: "Category X has >5 no-match events"
    → Fetch resolved tickets in category X from memory_episodes
    → LLM draft generation (structured, bounded prompt)
    → Save to sop_suggestions (status=draft)
    → Notify team lead via Telegram
    
Team lead reviews draft in Telegram / admin panel
    → Approve → Download as .md file → Edit → Manual ingestion trigger
    → Reject → Archive draft + note reason
    
NEVER: auto-ingest, auto-approve, auto-publish
```

---

## 7. Action Safety Invariants

These invariants must be enforced in code and tested:

1. **No action without a completed governance pass**: `governance.confidence` must be non-null before `ActionPlanner.evaluate()` is called
2. **No CRITICAL action without explicit approval**: `ApprovalGate` returns `REQUIRES_HUMAN` for all `ToolRisk.CRITICAL` tools, unconditionally
3. **No action after generation failure**: If `ChatGenerator` returns a degraded result (`confidence_score <= 0.05`), no post-generation actions are executed
4. **No cross-tenant action**: `tool_args` are validated to ensure the `ticket_id` belongs to the authenticated `client` before execution
5. **Idempotency key checked before every execution**: Two requests with the same idempotency key cannot execute the same action twice
6. **Rate limit enforced before every execution**: `rate_limit_per_minute` is checked against Redis per-tool-per-tenant sliding window
