# Memory Architecture — Big Phase 2

## 1. Problem Statement

Phase 1 treats every request as stateless. The only session continuity is `CHAT_HISTORY_TURNS=6` — a sliding window of prior messages within a single ticket session.

This produces three observable failures:

1. **Repeated context re-establishment**: A customer who has submitted 3 tickets about VKYC failures must re-describe their device, OS, and configuration every time. The agent must re-read the history. The AI has no memory.

2. **No organizational learning**: If Tenant A's `unity_bank` account sees a surge of OTP failures on Tuesday and they're resolved by a specific workaround, that operational knowledge is not retained. The next Tuesday's surge starts from zero.

3. **No retrieval personalization**: Every user gets the same retrieval profile regardless of their history of interactions, device type, or issue pattern.

Memory is the foundation of intelligence. Everything in Phase 2A depends on having a governed, reliable memory layer.

---

## 2. Memory Taxonomy

Phase 2 defines three distinct memory types with different storage, lifecycle, and retrieval semantics:

### 2.1 Episodic Memory

**What it stores**: Facts about specific past events — a specific ticket, a specific conversation turn, a specific resolution.

**Lifetime**: 90 days by default (configurable per tenant with `EPISODIC_MEMORY_TTL_DAYS`).

**Granularity**: One record per meaningful event (ticket closed, resolution confirmed, escalation triggered).

**Example record**:
```json
{
  "episode_id": "ep_20260522_T1234",
  "session_id": "ticket_78901",
  "customer_id": "cust_abc123",
  "client": "unity_bank",
  "timestamp": "2026-05-22T10:30:00Z",
  "issue_category": "otp_delivery_failure",
  "resolution_type": "sop_exact_match",
  "resolution_confirmed": true,
  "sop_ids_used": ["sop_otp_delivery_v2.md"],
  "device_context": {"os": "Android", "version": "12", "device": "Samsung A52"},
  "confidence": "high",
  "automation_safe": true,
  "requires_human": false,
  "feedback": null
}
```

### 2.2 Semantic Memory

**What it stores**: Synthesized knowledge about a customer or session — facts that persist across multiple episodes and remain relevant even after specific episode details fade.

**Lifetime**: 365 days (or until explicitly invalidated).

**Granularity**: One record per customer profile or entity summary.

**Example record**:
```json
{
  "memory_id": "sem_cust_abc123",
  "customer_id": "cust_abc123",
  "client": "unity_bank",
  "last_updated": "2026-05-22T10:30:00Z",
  "synthesized_facts": {
    "primary_device": "Samsung A52 (Android 12)",
    "recurring_issues": ["otp_delivery_failure", "liveness_check_stuck"],
    "preferred_resolution_channel": "email",
    "escalation_history": ["2026-04-15 — Tier 2 for liveness SDK crash"],
    "resolved_by_sop_count": 7,
    "open_issues": []
  },
  "summary_text": "Customer primarily uses Samsung A52 on Android 12. Has experienced OTP delivery failures twice (both resolved via Infobip gateway check SOP). One Tier 2 escalation for liveness SDK crash in April 2026. High automation success rate.",
  "embedding": "[1536-dim vector for semantic similarity search]"
}
```

**Critical design note**: The `embedding` field on semantic memory records enables similarity-based customer recall — "find me customers with similar issue patterns to this incoming ticket."

### 2.3 Organizational Memory

**What it stores**: Tenant-level operational knowledge — which SOPs are most effective for this tenant, which issue categories spike at what times, which products have known problems.

**Lifetime**: Indefinite (reviewed quarterly, never auto-deleted).

**Granularity**: One record per tenant, with time-windowed statistics.

**Example record**:
```json
{
  "org_id": "org_unity_bank",
  "client": "unity_bank",
  "last_updated": "2026-05-22T00:00:00Z",
  "top_issue_categories": [
    {"category": "otp_delivery_failure", "count_30d": 142, "resolution_rate": 0.87},
    {"category": "liveness_check_stuck", "count_30d": 89, "resolution_rate": 0.71}
  ],
  "most_effective_sops": [
    {"sop_id": "sop_otp_delivery_v2.md", "use_count": 98, "positive_feedback_rate": 0.92}
  ],
  "known_issue_periods": [
    {"description": "OTP failures spike Monday mornings", "pattern": "weekly/Monday/09:00-11:00"}
  ],
  "escalation_rate_30d": 0.13,
  "automation_rate_30d": 0.68
}
```

---

## 3. Storage Model

### 3.1 Database Tables

```sql
-- Episodic memory: raw events
CREATE TABLE memory_episodes (
    episode_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    customer_id TEXT,
    client TEXT NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    issue_category TEXT,
    resolution_type TEXT,
    resolution_confirmed BOOLEAN DEFAULT FALSE,
    sop_ids_used TEXT[],
    device_context JSONB,
    confidence TEXT,
    automation_safe BOOLEAN,
    requires_human BOOLEAN,
    feedback TEXT,
    raw_answer_hash TEXT,        -- SHA256 of answer (for deduplication)
    metadata JSONB,
    ttl_expires_at TIMESTAMPTZ   -- for cleanup jobs
);

CREATE INDEX idx_episodes_customer ON memory_episodes(customer_id, client);
CREATE INDEX idx_episodes_session ON memory_episodes(session_id);
CREATE INDEX idx_episodes_timestamp ON memory_episodes(timestamp DESC);
CREATE INDEX idx_episodes_ttl ON memory_episodes(ttl_expires_at);

-- Semantic memory: synthesized customer profiles
CREATE TABLE memory_summaries (
    memory_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    client TEXT NOT NULL,
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    synthesized_facts JSONB,
    summary_text TEXT,
    embedding VECTOR(1536),      -- enables similarity search across customers
    episode_count INTEGER DEFAULT 0,
    last_episode_id TEXT REFERENCES memory_episodes(episode_id)
);

CREATE INDEX idx_summaries_customer ON memory_summaries(customer_id, client);
CREATE INDEX ON memory_summaries USING hnsw(embedding vector_cosine_ops);

-- Customer profiles: cross-session stable facts
CREATE TABLE customer_profiles (
    profile_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    client TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ DEFAULT NOW(),
    contact_email TEXT,
    freshdesk_contact_id TEXT,
    known_devices JSONB,          -- [{os, version, device, last_seen}]
    issue_categories_seen TEXT[], -- historical, for retrieval profile selection
    total_tickets INTEGER DEFAULT 0,
    escalation_count INTEGER DEFAULT 0,
    metadata JSONB
);

CREATE UNIQUE INDEX idx_profiles_customer ON customer_profiles(customer_id, client);

-- Organizational memory: tenant-level operational state
CREATE TABLE tenant_context (
    org_id TEXT PRIMARY KEY,
    client TEXT NOT NULL UNIQUE,
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    top_issue_categories JSONB,
    most_effective_sops JSONB,
    known_issue_periods JSONB,
    escalation_rate_30d FLOAT,
    automation_rate_30d FLOAT,
    metadata JSONB
);
```

### 3.2 Caching Layer (Redis)

```
Key: session:{session_id}:{client}
Value: JSON blob of SessionContext
TTL: 1800s (30 minutes — matches typical support session length)

Key: customer_profile:{customer_id}:{client}
Value: JSON blob of CustomerProfile
TTL: 3600s (1 hour — stable facts change rarely)

Key: org_context:{client}
Value: JSON blob of TenantContext
TTL: 7200s (2 hours — statistical aggregations are slow to change)
```

Cache-aside pattern: read from Redis, fall back to Supabase on miss, write back to Redis after Supabase read.

---

## 4. Memory Lifecycle

### 4.1 Write Path (Post-Response)

```
Request completes
    │
    ▼ (BackgroundTask — non-blocking)
MemoryWriter.write(request, response, session_context)
    │
    ├── Create/update memory_episodes record
    │   (episode_id = sha256(session_id + timestamp)[:16])
    │
    ├── Update customer_profiles.last_seen_at, total_tickets
    │
    ├── Invalidate Redis cache for session:{session_id}
    │
    └── Enqueue summarization if episode_count % 5 == 0
        (every 5 episodes, trigger async summarization)
```

### 4.2 Summarization Pipeline

Summarization converts episodic records into synthesized semantic memory. It runs asynchronously, not on the critical path.

```
Summarization trigger (every 5 new episodes for a customer)
    │
    ▼
SummarizationWorker.run(customer_id, client)
    │
    ├── Fetch last 20 episodes for customer from Supabase
    │
    ├── If memory_summaries exists for customer:
    │   └── Fetch existing summary_text
    │
    ├── Build summarization prompt:
    │   "Given this customer's support history, extract key stable facts:
    │    - Primary device and OS
    │    - Recurring issues (>1 occurrence)
    │    - Resolution patterns that work / don't work
    │    - Escalation history
    │    - Any special context needed for future support"
    │
    ├── Call LLM (gpt-4o-mini, temperature=0.1)
    │   timeout: 30s, max_retries: 2
    │
    ├── Parse response → synthesized_facts JSONB
    │
    ├── Embed summary_text (text-embedding-3-small)
    │
    └── Upsert memory_summaries
```

**Governance**: Summarization LLM calls are governed by the same `OPENAI_CHAT_MODEL` setting. If the LLM call fails, the existing summary is preserved. No summary is better than a bad summary.

### 4.3 Read Path (Pre-Request)

```
POST /rag/chat received
    │
    ▼
MemoryRouter.load(session_id, customer_id, client)
    │
    ├── Check Redis: session:{session_id}
    │   HIT: return SessionContext (p50: <5ms)
    │   MISS: continue
    │
    ├── Fetch from Supabase:
    │   ├── memory_episodes WHERE session_id = ?  LIMIT 6  (last 6 events this session)
    │   ├── memory_summaries WHERE customer_id = ? AND client = ?  (semantic summary)
    │   └── customer_profiles WHERE customer_id = ? AND client = ?  (stable facts)
    │
    ├── Build SessionContext{
    │     session_history: [last 6 episodes],
    │     customer_summary: memory_summaries.summary_text,
    │     known_device: customer_profiles.known_devices[most_recent],
    │     issue_history: customer_profiles.issue_categories_seen,
    │     org_context: (from tenant_context cache)
    │   }
    │
    ├── Write back to Redis: TTL 1800s
    │
    └── Return SessionContext
```

**Latency budget**: Memory read path must complete in <150ms. If it exceeds 500ms, log a warning and proceed without memory (fallback to Phase 1 stateless behavior).

### 4.4 Pruning

```
Scheduled job: daily at 02:00 UTC (not on request path)

MemoryPruner.run():
    ├── DELETE FROM memory_episodes WHERE ttl_expires_at < NOW()
    ├── DELETE FROM memory_summaries WHERE customer_id NOT IN (
    │       SELECT DISTINCT customer_id FROM memory_episodes
    │       WHERE timestamp > NOW() - INTERVAL '90 days'
    │   )
    └── UPDATE customer_profiles SET known_devices = ... (remove devices >1 year old)
```

---

## 5. Memory Injection Into Generation

Memory context is injected into the generation prompt as a structured block:

```
[CUSTOMER CONTEXT — DO NOT SHARE WITH CUSTOMER]
Device: Samsung A52 (Android 12)
Prior issues this session: OTP not received (2 min ago)
Customer history summary: Has experienced OTP delivery failures twice before.
Both resolved via Infobip gateway check. One Tier 2 escalation for liveness
crash in April. High automation success rate.
Known device issues: None flagged for Android 12 / Samsung A52.
[END CUSTOMER CONTEXT]
```

**Governance constraints on memory injection**:
1. Memory context is labeled non-shareable in the prompt
2. Memory context does NOT override SOP content — it only adds context
3. If memory summary contains escalation history, `requires_human` signal is boosted
4. Memory context is logged in `diagnostics` for audit (content hash, not full text)

---

## 6. Memory Governance

### 6.1 Tenant Isolation

Memory reads and writes are always scoped by `client`. The SQL queries include `WHERE client = ?`. The application layer validates that `session_id` belongs to `client` before loading memory.

There is no cross-tenant memory lookup, ever. This is enforced at both the application layer and the SQL layer.

### 6.2 PII in Memory

Episodic memory may contain PII (customer emails, phone numbers, device identifiers). Controls:

- `memory_episodes.raw_answer_hash` stores only a hash of the answer — not the full text
- Customer email addresses are stored only in `customer_profiles.contact_email` under RLS
- Summarization prompt explicitly instructs: "Do not include specific personal identifiers in the summary"
- Summary text is scanned for PII patterns before storage (same redaction logic as B3 ingestion)

### 6.3 Memory Observability

Every memory read and write emits a structured log event:
```json
{"event": "memory_read", "session_id": "...", "customer_id": "...", 
 "cache_hit": true, "latency_ms": 4, "episode_count": 3}

{"event": "memory_write", "episode_id": "...", "session_id": "...",
 "issue_category": "otp_delivery_failure", "summarization_triggered": false}
```

Prometheus metrics (Phase 2C):
- `memory_read_latency_ms` (histogram)
- `memory_cache_hit_rate` (gauge)
- `memory_write_total` (counter by client)
- `summarization_runs_total` (counter)
- `memory_pruned_episodes_total` (counter, daily)

---

## 7. Memory Architecture Anti-Patterns to Avoid

### 7.1 Do Not Store Full Conversation Text in Episodes

Storing full conversation text per episode will cause the memory store to grow unboundedly. Store structured metadata (category, resolution_type, confidence) and the content hash, not the text.

### 7.2 Do Not Inject All Memory Into Every Prompt

Memory injection increases token cost. Inject only the summary (200–400 tokens max) and the current session history (already in `CHAT_HISTORY_TURNS`). Full episode history is never injected.

### 7.3 Do Not Use Memory to Override Governance

A prior resolution does not mean the same resolution is correct today. If a customer had an OTP failure resolved by workaround X, and they have a new OTP failure, retrieval still runs through the full pipeline. Memory is context, not a shortcut.

### 7.4 Do Not Trust Summarization Output Without Validation

LLM summarization can hallucinate. After summarization, run a structure validation check: does `synthesized_facts` contain expected keys? Is `summary_text` within expected length bounds? If validation fails, preserve the previous summary.

### 7.5 Do Not Make Memory Reads Blocking

Memory reads are on the critical response path. If Supabase is slow or Redis is unavailable, fall back immediately to stateless Phase 1 behavior. A degraded response without memory is better than a slow response with memory.
