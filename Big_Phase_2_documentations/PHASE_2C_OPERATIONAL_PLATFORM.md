# Phase 2C — Operational Platform Design

## 1. What Phase 2C Provides

Phase 2C transforms the platform from a backend service into an **observable, administrable operational system**. It adds no new AI capabilities — it makes existing capabilities visible, measurable, and controllable.

Without Phase 2C, the system operates in a "black box" mode: it processes tickets, but no one has a clear view of how well it's performing, where it's failing, or how to improve it.

With Phase 2C, support team leads and administrators can:
- See automation rates, confidence distributions, and SLA compliance in real time
- Review AI-generated responses before they reach customers (for CRITICAL risk responses)
- Audit governance decisions for compliance
- Configure per-tenant policies and tool permissions
- Receive proactive alerts when quality metrics degrade

---

## 2. Analytics Dashboard Design

### 2.1 Core Metrics

The analytics layer reads from `retrieval_quality_log`, `response_feedback`, and `tool_executions`. It never reads from `documents`, `rag_sop_chunks`, or other knowledge tables.

**Primary KPIs** (displayed on dashboard home):

| Metric | Definition | Target |
|--------|-----------|--------|
| Automation Rate | % of tickets fully auto-resolved (no human needed) | >65% |
| High-Confidence Rate | % of requests returning confidence=high | >70% |
| SLA Compliance | % of tickets first-responded within SLA | >95% |
| Feedback Positive Rate | % of thumbs-up among rated responses | >82% |
| Escalation Rate | % of tickets requiring human | <30% |
| P95 Response Latency | 95th percentile response time | <8s |

### 2.2 Dashboard API Endpoints

Phase 2C adds a `/analytics` route group to the existing FastAPI service:

```
GET /analytics/summary?client=unity_bank&window_days=7
Returns: {automation_rate, confidence_distribution, top_intents, sla_compliance}

GET /analytics/intents?client=unity_bank&window_days=7
Returns: per-intent breakdown of match_type, confidence, automation_rate

GET /analytics/feedback?client=unity_bank&window_days=7
Returns: feedback volume, positive_rate, thumbs_down_categories

GET /analytics/retrieval?client=unity_bank&window_days=7
Returns: avg_similarity, no_match_rate, top_retrieved_sops, gap_categories

GET /analytics/latency?client=unity_bank&window_days=1
Returns: latency histogram by component (embedding, retrieval, generation, total)

GET /analytics/calibration?client=unity_bank
Returns: confidence calibration report (thumbs-up rate by confidence level)
```

All analytics endpoints are:
- Read-only
- Client-scoped (a `unity_bank` admin sees only `unity_bank` data)
- Cached in Redis with TTL=300s (5 minutes) — analytics don't need real-time precision
- Authenticated with the same `X-API-Key` mechanism as the main service

### 2.3 Visualization

Phase 2C does not build a full frontend. The analytics API produces data that can be consumed by:
- **Metabase** (recommended for non-engineer team leads — low setup cost, direct Supabase connection)
- **Grafana** (for engineering team — Prometheus + custom Supabase data source)
- **n8n workflow** (automated weekly report sent to Telegram)
- **Google Sheets** (via n8n HTTP request → Google Sheets append)

Rationale for not building a custom frontend: a custom React dashboard requires frontend engineering capacity that should be focused on core platform features in Phase 2. Metabase connected directly to Supabase provides 90% of the needed visualization at near-zero development cost.

---

## 3. SLA Monitoring

### 3.1 SLA Definitions

```python
SLA_CONFIG = {
    "unity_bank": {
        "first_response_hours": 4,      # Time to first AI or human response
        "resolution_hours": 24,         # Time to ticket closure
        "escalation_response_hours": 2, # Time to human assignment if escalated
    },
    "default": {
        "first_response_hours": 8,
        "resolution_hours": 48,
        "escalation_response_hours": 4,
    }
}
```

### 3.2 SLA Event Detection

```sql
-- Tickets approaching SLA breach (alert when 80% of time elapsed)
CREATE VIEW sla_at_risk AS
SELECT 
    r.ticket_id,
    r.client,
    r.timestamp as created_at,
    r.intent_category,
    r.confidence,
    r.requires_human,
    EXTRACT(EPOCH FROM (NOW() - r.timestamp)) / 3600 as age_hours,
    (SELECT first_response_hours FROM sla_config WHERE client = r.client) as sla_hours
FROM retrieval_quality_log r
WHERE r.ticket_id NOT IN (
    SELECT DISTINCT ticket_id FROM response_feedback 
    WHERE signal IN ('thumbs_up', 'escalation_confirmed')
)
AND EXTRACT(EPOCH FROM (NOW() - r.timestamp)) / 3600 > 
    (SELECT first_response_hours * 0.8 FROM sla_config WHERE client = r.client);
```

### 3.3 SLA Alert Mechanism

A background task runs every 15 minutes:

```python
async def check_sla_alerts():
    at_risk = await get_sla_at_risk_tickets()
    for ticket in at_risk:
        if not await already_alerted(ticket.ticket_id):
            await send_telegram_alert(
                f"⚠️ SLA at risk: Ticket #{ticket.ticket_id} ({ticket.client})\n"
                f"Age: {ticket.age_hours:.1f}h / SLA: {ticket.sla_hours}h\n"
                f"Intent: {ticket.intent_category}\n"
                f"Status: {'requires human' if ticket.requires_human else 'AI handled'}"
            )
            await mark_alerted(ticket.ticket_id)
```

---

## 4. AI Quality Monitoring

### 4.1 Quality Degradation Alerts

Monitor for sudden changes in quality metrics that may indicate a regression:

```python
QUALITY_ALERT_THRESHOLDS = {
    "automation_rate_drop": 0.10,        # Alert if automation rate drops 10% in 1 hour
    "high_confidence_rate_drop": 0.15,   # Alert if high-confidence rate drops 15%
    "thumbs_up_rate_drop": 0.20,         # Alert if positive feedback drops 20%
    "p95_latency_spike": 5.0,           # Alert if P95 latency exceeds 5s threshold increase
    "no_match_rate_spike": 0.15,         # Alert if no-match rate increases by 15%
}
```

When thresholds are crossed:
1. Send Telegram alert with metric name, current value, baseline, delta
2. Log to `sla_events` table
3. If degradation persists >30 minutes: create Asana investigation task

**False positive prevention**: Use a 15-minute rolling average, not instantaneous values. A single slow request doesn't trigger an alert.

### 4.2 Anomaly Detection

Simple statistical anomaly detection without ML:

```python
def detect_anomaly(metric_name: str, current_value: float, history: list[float]) -> bool:
    if len(history) < 7:
        return False  # Not enough history
    mean = statistics.mean(history)
    stdev = statistics.stdev(history)
    z_score = abs(current_value - mean) / max(stdev, 0.001)
    return z_score > 3.0  # 3 sigma rule
```

This catches cases like: "today's no-match rate is 0.45 but the 30-day average is 0.12" → clear anomaly requiring investigation.

---

## 5. Human-in-the-Loop Review Interface

### 5.1 Review Queue UI

The human review interface is implemented as:
1. **Telegram inline keyboards** (primary — agents are already in Telegram)
2. **Freshdesk private notes** with action links (fallback)
3. **Admin panel** for bulk review (Phase 2C, later)

### 5.2 Telegram Review Flow

When a response requires human review:

```
Bot → Agent Telegram:
━━━━━━━━━━━━━━━━━━━━━━
🔍 Review Request #78901
Client: unity_bank | Risk: MEDIUM
Intent: otp_delivery_failure (conf: 0.72)
Match: related_match | Confidence: medium

AI Response (preview):
"Please try the following steps to resolve your OTP 
issue: 1. Check if your phone is in Do Not Disturb..."

[✅ Approve & Send]  [✏️ Edit & Send]  [❌ Reject]  [👁️ Full Response]
━━━━━━━━━━━━━━━━━━━━━━
```

Approve: `POST /actions/approve {request_id}`  
Edit: Opens a Telegram text input; agent types correction; system sends corrected version  
Reject: `POST /actions/reject {request_id}` — ticket stays open for manual handling

### 5.3 Agent Response Quality Feedback

After the ticket is closed (detected via Freshdesk webhook "ticket resolved" event), prompt for final feedback:

```
Bot → Agent Telegram:
Ticket #78901 was resolved. How was the AI's assistance?
[⭐ Excellent]  [👍 Good]  [😐 Average]  [👎 Poor]
```

This captures quality signals even for responses that didn't go through formal review.

---

## 6. Governance Audit Dashboard

### 6.1 Audit Log Explorer

```
GET /audit/governance?client=unity_bank&start=2026-05-01&end=2026-05-31
Returns: paginated list of governance_audit_log records with filters:
  - by intent_category
  - by final_action (auto_replied / queued / escalated / blocked)
  - by compliance_flag=true
  - by hallucination_risk=true
```

### 6.2 Compliance Audit Export

For regulatory compliance, support a CSV export of governance decisions:

```
GET /audit/export?client=unity_bank&start=...&end=...&format=csv
Returns: CSV with columns:
  timestamp, ticket_id, intent_category, confidence, risk_score,
  requires_human, automation_safe, policies_triggered, final_action,
  compliance_flag, reviewed_by
```

This export is designed to satisfy regulatory requests for documentation of automated decision-making.

---

## 7. Admin Control Panel

### 7.1 Tenant Configuration

```
GET /admin/tenants
POST /admin/tenants/{client}/policies
PUT /admin/tenants/{client}/sla
GET /admin/tenants/{client}/tool-config
PUT /admin/tenants/{client}/tool-config
```

### 7.2 SOP Management

```
GET /admin/sop-suggestions?client=unity_bank&status=draft
PUT /admin/sop-suggestions/{suggestion_id}/approve
PUT /admin/sop-suggestions/{suggestion_id}/reject
GET /admin/sop-coverage?client=unity_bank
```

### 7.3 Threshold Management

```
GET /admin/thresholds?client=unity_bank
PUT /admin/thresholds?client=unity_bank
Body: {
    "workflow_exact_similarity": 0.55,
    "workflow_related_similarity": 0.35,
    "retrieval_min_similarity": 0.20,
    "sop_similarity_boost": 0.15
}
```

**Security**: All admin endpoints require a separate `X-Admin-Key` header (a distinct, more restricted API key from `RAG_API_KEY`). Admin key is configured separately in `.env`.

---

## 8. Workflow Observability

### 8.1 Per-Request Trace

Every request generates a trace in `DEBUG_TRACE=true` mode. Phase 2C promotes trace data to a structured table:

```sql
CREATE TABLE request_analytics (
    request_id TEXT PRIMARY KEY,
    session_id TEXT,
    client TEXT NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    
    -- Timing breakdown
    embedding_latency_ms FLOAT,
    semantic_retrieval_latency_ms FLOAT,
    fts_retrieval_latency_ms FLOAT,
    memory_read_latency_ms FLOAT,
    intent_classification_latency_ms FLOAT,
    governance_latency_ms FLOAT,
    generation_latency_ms FLOAT,
    total_latency_ms FLOAT,
    
    -- Retrieval quality
    semantic_candidates INTEGER,
    fts_candidates INTEGER,
    rrf_merged_count INTEGER,
    final_chunk_count INTEGER,
    top_similarity FLOAT,
    embedding_cache_hit BOOLEAN,
    context_cache_hit BOOLEAN,
    
    -- Governance output
    intent_category TEXT,
    workflow_match_type TEXT,
    confidence TEXT,
    confidence_score FLOAT,
    requires_human BOOLEAN,
    automation_safe BOOLEAN,
    risk_score FLOAT,
    
    -- Outcome
    tool_calls_executed INTEGER DEFAULT 0,
    memory_write_triggered BOOLEAN DEFAULT FALSE,
    summarization_triggered BOOLEAN DEFAULT FALSE
);
```

### 8.2 Operational Metrics Summary

The operational platform produces a weekly summary for stakeholders:

```
KwikID AI Support — Weekly Report (May 19–25, 2026)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Total Tickets Processed: 312
Auto-Resolved (no human): 221 (70.8%)
Escalated to Human: 91 (29.2%)

Top Issues This Week:
1. OTP Delivery Failure: 87 tickets (83% auto-resolved)
2. Liveness Check Stuck: 64 tickets (58% auto-resolved)
3. Camera Permission: 41 tickets (90% auto-resolved)
4. VKYC Session Expired: 38 tickets (75% auto-resolved)

SLA Compliance:
- First response <4h: 98.7%
- Resolution <24h: 87.3%

AI Quality:
- Avg Confidence Score: 0.74
- Feedback Positive Rate: 84.2% (from 67 rated responses)
- Thumbs-Down This Week: 11 (4 corrections provided)

Alerts:
- No critical alerts this week
- 2 SOP gap alerts: sdk_crash (12 no-match), api_integration (8 no-match)

Top Performing SOP: sop_otp_delivery_v2.md (68 uses, 88% positive)
```
