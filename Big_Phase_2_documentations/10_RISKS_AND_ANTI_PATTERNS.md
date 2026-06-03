# 10 — Risks and Anti-Patterns

## 1. Anti-Patterns to Avoid

Each anti-pattern below describes: what it looks like in a design proposal, the real-world consequence if built, and the correct alternative used in this architecture.

---

### Anti-Pattern 1: "Planner Everywhere"

**What it looks like:** A single LLM receives a list of 50 API tools (check OTP status, resend OTP, check VKYC session, toggle bandwidth, modify Aadhaar fields, send customer reply, update priority, close ticket, ...) and is asked to construct a dynamic execution plan for each incoming ticket. The design document says "the LLM will figure out the right sequence of tool calls."

**Consequence:**
- Execution path instability: the same input may produce different tool call sequences on different runs (temperature, token sampling). Two identical tickets may be handled differently with no audit explanation.
- Infinite tool loops: the LLM may generate a plan that calls the same tool multiple times with no exit condition, consuming API quota and blocking the worker.
- Unauthorized mutations: without a static tool registry and risk classification, the LLM may call IRREVERSIBLE tools (Aadhaar modification, customer reply) that should require human approval.
- Non-auditable decisions: there is no way to reproduce a specific execution path for a compliance audit.

**Correction:** Classify input to a specific topic first. Restrict available tools to the topic's minimal static allowlist. Use deterministic playbooks with pre-defined state transitions. Reserve LLM reasoning for evaluating unstructured transition conditions, not for constructing action plans from scratch.

---

### Anti-Pattern 2: "Memory Everywhere" (Conversational Vector Bloat)

**What it looks like:** Every chat turn is written to a vector database. On each new turn, the last 30 vectorized turns are retrieved by similarity and injected into the context window. A separate "episodic memory" store holds per-customer interaction history for 90 days. A "semantic memory" store holds customer profile embeddings for 365 days. The system description says "the AI remembers every interaction."

**Consequence:**
- Semantic noise: historical turns from unrelated issues contaminate the context. An OTP failure history retrieved for a VKYC liveliness issue confuses the LLM about the current issue type.
- Historical hallucination replay: the LLM may confabulate resolutions from historical turns as if they applied to the current case.
- DPDP Act violation: storing customer interaction history as vector embeddings creates an unstructured PII store. Purpose limitation, storage limitation, and user consent requirements are violated.
- Token cost explosion: injecting 30 historical turn embeddings per request significantly increases prompt size and cost.
- Retrieval latency: vector similarity search over a 90-day history adds latency to every request.

**Correction:** Use case memory (relational checkpoint, Tier 3) to store active workflow state. Use zero-copy CRM read (Tier 4) to access current account state. Use a 5-turn ephemeral context window (Tier 2) for conversational coherence. No persistent vector store for customer interaction history.

---

### Anti-Pattern 3: "Over-Agentification"

**What it looks like:** 10 autonomous LLM agents are instantiated for a single support case: a triage agent, a retrieval agent, a reasoning agent, a verification agent, an OTP agent, a VKYC agent, an escalation agent, a note-writing agent, a feedback agent, and a supervisor agent. They communicate via message passing. The design says "each agent has specialized expertise."

**Consequence:**
- Latency buildup: each LLM call takes 1-3 seconds. Ten sequential agent calls produce 10-30 seconds of latency per case. P95 latency targets (< 10 seconds) are impossible.
- Non-deterministic branching: agent-to-agent communication introduces prompt-dependent variation. The escalation agent may escalate for different reasons on different runs.
- Untraceable debug paths: when a case is handled incorrectly, tracing which agent introduced the error across 10 message hops is practically impossible.
- Deadlocks: two agents waiting for each other's response with no timeout policy produces a hung case.

**Correction:** If a step is rule-based (classify topic, check confidence threshold, apply rate limit), implement it as code. Reserve LLM calls for genuinely unstructured work: parsing ticket text, evaluating ambiguous transition conditions, generating diagnostic text. Total LLM calls per case: 1-3 (classifier Tier 2 if needed, RAG generation, Transfer Context Payload). All other steps are deterministic code.

---

### Anti-Pattern 4: "Chat-Centric Pipeline"

**What it looks like:** The system is built around a chat widget. All case state is stored in the conversation thread. If the customer closes the browser tab, the transaction resets. The design says "we'll handle continuity by asking the customer to describe their issue again."

**Consequence:**
- High abandonment: customers experiencing technical VKYC issues are already frustrated. Requiring them to restart and re-describe their issue after a browser close produces abandonment and negative feedback.
- High re-work: human agents receive cases with no context about what was already attempted. They restart the triage process from scratch.
- SLA failure: the SLA clock started on the original ticket creation. A reset chat has no knowledge of this. SLA breaches are invisible to the system.
- No audit trail: there is no immutable record of what the system attempted in the previous session.

**Correction:** Build around the Case/Ticket record. Chat is an I/O channel, not the state store. Case checkpoint (Redis + PostgreSQL Saga) persists workflow state across container restarts, session drops, and browser closes. A customer returning to an in-flight case resumes from the last checkpoint, not from scratch.

---

### Anti-Pattern 5: "Graph RAG as Backbone"

**What it looks like:** The entire case execution logic is implemented as a knowledge graph traversal. SOPs are nodes; relationships between SOPs are edges. The system resolves support cases by traversing the graph. The design says "the knowledge graph is the single source of truth for case execution."

**Consequence:**
- Brittle business logic: every business logic change (e.g., new escalation path for a regulatory update) requires re-authoring the SOP graph structure, not just updating a playbook YAML file.
- Graph traversal latency: multi-hop graph queries add 100-500ms to every case.
- Enormous debug surface: tracing why a specific traversal path was chosen requires understanding both the graph structure and the embedding similarity scores at each hop.
- Re-authoring burden: converting existing prose SOPs into graph-compatible structured format requires significant manual effort with unclear quality gain.

**Correction:** Use deterministic workflow playbooks for case execution (the control plane). Use retrieval (flat pgvector hybrid or, in Level 3, optional Graph RAG) only within the knowledge support layer. SOP updates change playbook YAML or SOP text, not graph structure.

---

### Anti-Pattern 6: "Dynamic Multi-Agent Network"

**What it looks like:** Agents discover each other's capabilities at runtime. A coordinator agent broadcasts a task; available specialist agents bid for it; the winning agent executes it and publishes a result. The design says "this provides flexibility for handling novel issue types."

**Consequence:**
- Non-deterministic execution paths: which agent wins the bid is not predictable. Two identical tasks may be executed by different agents with different capabilities.
- High latency overhead: broadcast, bid, selection, and handoff protocols add multiple round-trips before any actual work begins.
- Context loss between agents: each agent starts with only the information passed to it in the bid request; accumulated diagnostic context from previous steps is not automatically preserved.
- Deadlocks: if no agent bids for a task, or if the coordinator waits indefinitely for a bid, the case hangs.

**Correction:** Use a constellation architecture with a deterministic supervisor. The supervisor is a state machine (not an LLM). Specialized workers are stateless functions called by the supervisor with explicit input/output contracts. Worker assignment is static (topic → worker mapping, defined in code). No dynamic discovery, no bidding, no negotiation.

---

### Anti-Pattern 7: "Autonomous Customer-Facing Writes"

**What it looks like:** The AI system is given permission to post replies directly to the customer-facing Freshdesk thread (not private notes). The design says "this will reduce agent workload by resolving simple cases without human review." The AI is also given permission to send WhatsApp/SMS notifications directly to the customer.

**Consequence:**
- Incorrect information delivered to customers: even a 5% error rate at high volume means thousands of customers receive wrong instructions about their KYC status.
- Compliance violations: customer-facing statements about KYC decisions are regulated. An incorrect AI-generated statement about Aadhaar verification status or VKYC outcome is a regulatory liability.
- Customer trust damage: customers who receive incorrect AI-generated instructions and act on them (retrying VKYC incorrectly, providing documents that fail again) will escalate with additional frustration.
- No human review layer: once AI writes directly to customers, there is no opportunity for an agent to catch errors before they reach the customer.

**Correction:** Level 1 and Level 2 produce only Freshdesk private notes visible to agents. Agents review and act on the notes. Customer-facing writes require: an evaluation framework proving accuracy above a defined threshold, a human review gate for a probationary period, and explicit business and legal sign-off. This is not a Phase 2 scope item.

---

## 2. What Not to Build in Phase 2

These items are explicitly out of scope. They are not "to be done later in Phase 2" — they are deferred until they are specifically justified by production evidence.

**General-purpose autonomous agent.** An agent that receives an arbitrary support request and builds a novel action sequence without governance constraints. Rejected because: execution paths are non-auditable, governance cannot be pre-defined, and RBI V-CIP requires deterministic, auditable processes.

**LLM-directed tool chains.** Giving the LLM authority to decide which tools to call in what order. Rejected because: the same input may produce different tool sequences across calls, making audit trails unreliable. Tool chains must be defined in playbooks; LLMs evaluate conditions within playbooks only.

**Automatic training from production feedback.** Real-time or batch automatic weight updates, RAG knowledge base updates, or threshold changes triggered directly by feedback signals without human review. Rejected because: feedback is noisy, contains unreviewed PII, and could cause catastrophic forgetting of rare but critical case types.

**Customer-facing AI content generation.** AI-generated replies posted directly to customer tickets or sent via SMS/WhatsApp. Rejected because: requires evaluation framework, legal sign-off, and a probationary human review period — none of which are in Phase 2 scope.

**Write-level API mutations on KYC records.** OTP attempt counter clear, VKYC session reset, Aadhaar field modification, PAN verification status update. Rejected because: these are IRREVERSIBLE actions with direct regulatory impact. They require bank-level change management and are outside the support system's authorized scope.

**Dynamic long-term agent vector memory.** Per-customer or per-agent interaction history stored as embeddings. Rejected because: violates DPDP Act, adds retrieval noise, and the personalization goals are better served by zero-copy CRM reads and case checkpoints.

**Multi-tenant shared retrieval without ABAC.** The current pgvector retrieval runs without tenant predicates at the database level. Extending retrieval features before this vulnerability is fixed is blocked. The ABAC predicate pushdown fix (described in `05_RETRIEVAL_AND_KNOWLEDGE_LAYER.md`) is a Level 1 prerequisite for any production deployment.

---

## 3. Known Production Failure Modes and Mitigations

| Failure Mode | Root Cause | Mitigation |
|-------------|------------|------------|
| Container restart during active workflow | In-memory workflow state lost on restart | Case checkpoint in Redis + PostgreSQL Saga; workflow replays from last checkpoint on restart |
| Downstream API timeout during action execution | VKYC service, OTP gateway, or Freshdesk API slow or down | Circuit breaker (Redis-backed, shared state); on OPEN: immediate DLQ routing |
| Prompt injection via ticket content | Customer includes instruction-like text in ticket | Input sanitization at ingress; structured prompt templating; action gateway rejects unregistered actions |
| Cross-tenant retrieval leakage | Missing tenant predicate in pgvector query | ABAC predicate pushdown (Level 1 priority fix); not mitigated by post-retrieval filter |
| Confidence miscalibration | Classifier over-confident on edge cases | Weekly calibration loop; threshold review against feedback data; human sign-off on threshold changes |
| OTP retry storm | Workflow retrying OTP on same channel after limit hit | Per-session, per-channel rate limit in tool registry (max 3/channel/session); mandatory channel switch enforcement |
| PII exposure in audit log | Unmasked PII written to case_audit_log or private note | PII masking pipeline applied before any write; per-note PII scan before posting; automated daily audit |
| Redis slot state expiry during active clarification | Customer takes > 30 minutes to respond | On TTL expiry, case transitions to ESCALATED with reason `slot_fill_timeout`; human retains context via Transfer Context Payload |
| Idempotency key collision | SHA-256 collision (theoretical) | Collision probability negligible (2^-256); not a practical concern |
| Temporal worker failure mid-saga | Temporal worker crashes with in-flight transactions | Temporal event-sourced replay recovers from last persisted event; Saga table provides independent recovery path |

---

## 4. Specific Risks for KwikID (RBI V-CIP Context)

These risks are specific to the KwikID deployment context and require explicit design decisions, not just general mitigations.

### RBI Audit Failure — Unmasked Aadhaar in Log or Context

Any Aadhaar number (even partially unmasked beyond the last 4 digits) in a log file, audit table, private note, or LLM context window is an immediate regulatory violation under UIDAI regulations and constitutes a reportable data incident.

**Required control:** Aadhaar masking must be applied at the pipeline ingress, before any processing. The masking function must be called in the same synchronous step as the webhook parse — not as a downstream async step. Masked token (`AADHAAR_TOKEN_<hash>`) replaces the value throughout all downstream processing. Audit: automated daily scan of `case_audit_log` and `response_feedback` for Aadhaar-pattern matches.

### Security Freeze Bypass

If a customer's account has a Security Freeze active (e.g., suspicious activity detected by the bank), the workflow must detect this state from the CRM zero-copy read and route immediately to the bank's Security team. The workflow must not attempt any automated resolution step, including OTP retry or VKYC link regeneration.

**Required control:** Security Freeze detection is a mandatory first check in every playbook's initial state. Before any other step executes, the playbook checks `crm_account_state.security_freeze == True`. If True: transition immediately to ESCALATED, escalation queue = Security, reason = "security_freeze_active". This check is not optional and cannot be removed without a security review.

### Hard Lock Resolution — Branch Visit Only

RBI regulations require that Hard Lock (account locked due to excessive OTP failures or suspicious activity) can only be resolved by the customer visiting a bank branch in person with identity documents. It cannot be resolved via digital channels, OTP, or VKYC.

**Required control:** `hard_lock_resolution` is registered in the tool registry as IRREVERSIBLE with `requires_approval: false` and `blocked_from_automation: true`. The correct action when Hard Lock is detected is to post a private note to the agent: "Account is in Hard Lock state. Resolution requires branch visit per RBI guidelines. Do NOT attempt digital resolution." The workflow terminates at this point.

### OTP Limit Exhaustion on Wrong Channel

Retrying OTP on the same channel after the per-channel limit is hit violates RBI OTP guidelines. The correct behavior is mandatory channel switch, not a retry on the same channel.

**Required control:** Per-session, per-channel rate limit enforced at the tool registry level (max 3/channel). The workflow playbook must have explicit channel-switch states: `SMS_Limit_Hit → Retry_Email`, `Email_Limit_Hit → Retry_Voice`, `Voice_Limit_Hit → Escalate_To_Human`. The fallback sequence is defined in the playbook, not left to LLM discretion.

### VKYC Session ID Collision in Redis Slot State

If two concurrent cases share a Redis slot state key due to a key construction error (e.g., only using `session_id` as the key without `case_id`), the action gateway may dispatch an action for Case A using Case B's slot values.

**Required control:** Redis slot state key must always be `slot:{case_id}:{session_id}`. The action gateway's session ownership check must verify that the `session_id` in the action proposal matches the `session_id` in the case checkpoint for the given `case_id`. If they do not match: reject the action, escalate with reason `"session_ownership_mismatch"`.

### Deepfake and Liveness Spoofing Events

If the VKYC liveliness service detects a deepfake or spoofing attempt, this is a security event, not a support event. The workflow must not attempt remediation (e.g., retrying the liveliness check). It must log to `security_compliance_audit` and route to the bank's SOC.

**Required control:** The VKYC session status tool response includes a `security_flags` field. If `security_flags` contains `DEEPFAKE_DETECTED` or `SPOOFING_ATTEMPT`: write to `security_compliance_audit` immediately, transition case to ESCALATED, escalation queue = Security, and halt all further automated processing. The bank's SOC receives the security event within 30 seconds via the SOC reporting job.
