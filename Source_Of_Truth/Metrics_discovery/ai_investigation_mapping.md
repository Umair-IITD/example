# AI Investigation Mapping — Monitoring Platform to AI Pipeline

**Platform**: Uptime Kuma **1.23.15** at `http://status.getkwikid.com:3001`  
**Source**: Live authenticated extraction 2026-07-11 + KwikID flow_diagram.mermaid + SUPPORT_OPERATIONS_BLUEPRINT.md

> **Live-data grounding (2026-07-11)**: The mapping below now reflects the real fleet of 332 monitors across 13 bank clients. Two facts materially shape AI investigation logic:
> 1. **Naming convention is highly structured** — `kwikid-vkyc-<client>-<env>-<service>-api` and `<CLIENT> | <SERVICE> API | <ENV>`. The AI can parse client + environment + service directly from a monitor name to match a Freshdesk ticket's tenant/service.
> 2. **MAINTENANCE ≠ outage** — 51 monitors are in scheduled maintenance windows (nightly/weekend UAT shutdowns). Always cross-check the 26 maintenance windows (see incident_system.md) before escalating.

---

## 1. Overview: Where Monitoring Data Enters the AI Pipeline

The KwikID Support Automation Platform's `flow_diagram.mermaid` defines two monitoring-specific tools in the Tool Registry:

- **METRICTOOL** — queries platform metrics (response times, uptime percentages, Prometheus time-series)
- **SERVERTOOL** — queries server-level health (infrastructure status, component availability)

Both tools are called by the **Evidence Collector** during the Investigation Phase. Their outputs flow into the **Root Cause Engine** and ultimately the **Reasoning Engine**.

---

## 2. Pipeline Position Map

```
Freshdesk Webhook (ticket created)
          │
          ▼
    Webhook Receiver
          │
          ▼
    Client Resolver (tenant resolution)
          │
          ▼
    Ticket Orchestrator
          │
          ▼
    Support Agent Runtime
          │
          ▼
    Workflow Engine ──── selects playbook based on ticket classification
          │
          ▼
    Investigation Planner ──── determines which tools to call
          │                         │
          │                         ├── Should I check monitoring? YES if:
          │                         │   - ticket mentions "not loading", "down", "error"
          │                         │   - ticket is from multiple users with same symptom
          │                         │   - ticket priority = Urgent
          │                         │   - cf_impact = "DOWNTIME 100% impact"
          │
          ▼
    Evidence Collector
          │
          ├── METRICTOOL ──► GET /metrics (Prometheus)
          │                  GET /api/status-page/heartbeat/<slug>
          │                  Returns: status of all monitors, response times, uptime %
          │
          ├── SERVERTOOL ──► GET /api/status-page/<slug>
          │                  Returns: server-level status, active incident, maintenance
          │
          ├── UNITYTOOL ──► Unity Admin API (Phase 2)
          ├── SOПТOOL ──► Knowledge Base RAG
          └── ... (other tools)
          │
          ▼
    Root Cause Engine ──── correlates monitoring evidence + ticket timestamp
          │
          ▼
    Reasoning Engine ──── builds confidence score + RCA hypothesis
          │
          ▼
    Observation Generator ──── writes private note with monitoring evidence
          │
          ▼
    Safety Guardrails ──── confidence gate
          │
          ▼
    Action Gateway ──── execute or draft
          │
          ▼
    Execution Layer ──── POST note, POST reply, PUT ticket fields
```

---

## 3. METRICTOOL — Tool Specification

### 3.1 Purpose

METRICTOOL retrieves current and historical performance metrics from Uptime Kuma. Its primary use is **outage correlation**: determining if a service was DOWN at the time the support ticket was created.

### 3.2 Input parameters

```python
class MetricToolInput(BaseModel):
    ticket_created_at: datetime      # ISO 8601 UTC timestamp of ticket creation
    tenant_id: str                   # e.g., "UNITY", "RBL"
    symptom_keywords: list[str]      # From ticket classification, e.g., ["session", "vkyc", "login"]
    slug: str = "kwikid"            # Status page slug; discovered via /api/entry-page
```

### 3.3 Output structure

```python
class MetricToolOutput(BaseModel):
    source: str = "uptime_kuma"
    collected_at: datetime
    
    # Current status snapshot
    all_monitors: dict[str, MonitorStatus]  # monitor_name → {status, response_time_ms}
    monitors_currently_down: list[str]      # monitor names currently DOWN
    
    # Historical correlation
    monitors_down_at_ticket_time: list[OutageEvent]  # DOWN at ticket creation
    
    # Context
    active_incident: Optional[IncidentInfo]  # null if no active incident
    maintenance_windows: list[MaintenanceInfo]  # active maintenance windows
    uptime_24h: dict[str, float]            # monitor_name → 0.0-1.0
    
    # Availability
    data_available: bool                    # False if platform unreachable
    error: Optional[str]                    # Error message if data_available=False
```

### 3.4 Internal implementation

```python
async def run_metric_tool(input: MetricToolInput) -> MetricToolOutput:
    client = UptimeKumaClient()
    
    try:
        # Parallel fetch for speed
        prometheus_data, heartbeat_data, status_page_data = await asyncio.gather(
            client.get_prometheus_metrics_async(),
            client.get_heartbeats_async(input.slug),
            client.get_status_page_async(input.slug)
        )
        
        # Parse Prometheus into monitor statuses
        all_monitors = client.parse_all_monitor_statuses(prometheus_data)
        monitors_down = [name for name, s in all_monitors.items() if s['status'] == 0]
        
        # Correlate with ticket creation time
        outages_at_ticket_time = correlate_outages_with_ticket(
            heartbeat_data['heartbeatList'],
            input.ticket_created_at
        )
        
        # Filter outages by symptom relevance
        relevant_outages = filter_by_symptom_relevance(
            outages_at_ticket_time,
            input.symptom_keywords
        )
        
        return MetricToolOutput(
            all_monitors=all_monitors,
            monitors_currently_down=monitors_down,
            monitors_down_at_ticket_time=relevant_outages,
            active_incident=status_page_data.get('incident'),
            maintenance_windows=status_page_data.get('maintenanceList', []),
            uptime_24h=extract_uptime_24h(heartbeat_data['uptimeList']),
            data_available=True
        )
    
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        return MetricToolOutput(
            data_available=False,
            error=f"Monitoring platform unreachable: {str(e)}",
            all_monitors={},
            monitors_currently_down=[],
            monitors_down_at_ticket_time=[],
            active_incident=None,
            maintenance_windows=[],
            uptime_24h={}
        )
```

---

## 4. SERVERTOOL — Tool Specification

### 4.1 Purpose

SERVERTOOL retrieves server-level health information. While METRICTOOL focuses on metrics/performance, SERVERTOOL focuses on **infrastructure component status**: database, API server, job queue, storage.

### 4.2 Input parameters

```python
class ServerToolInput(BaseModel):
    component: Optional[str]         # Specific component to check; None = all
    slug: str = "kwikid"
```

### 4.3 Output structure

```python
class ServerToolOutput(BaseModel):
    infrastructure_status: dict[str, ComponentStatus]  # component → {up, response_ms}
    any_infrastructure_down: bool
    database_status: Optional[ComponentStatus]         # Highest-priority component
    api_server_status: Optional[ComponentStatus]
    job_queue_status: Optional[ComponentStatus]
    
    data_available: bool
    error: Optional[str]
```

### 4.4 Difference between METRICTOOL and SERVERTOOL

| Aspect | METRICTOOL | SERVERTOOL |
|:---|:---|:---|
| Focus | Application-level performance metrics | Infrastructure component availability |
| Primary API | `/metrics` (Prometheus) + `/heartbeat` | `/api/status-page/<slug>` (grouped) |
| Key output | Response times, uptime %, outage correlation | Is the DB/API/Queue up or down? |
| When used | Ticket correlation, performance trending | Cascade failure analysis |
| Typical trigger | Any ticket with "slow" or "not loading" | Ticket with "everything is down" / multiple services |

---

## 5. Root Cause Engine — How It Uses Monitoring Data

The Root Cause Engine receives the combined Evidence Collector output and uses monitoring data to build RCA hypotheses.

### 5.1 RCA hypothesis templates

| Monitoring signal | RCA template | Confidence boost |
|:---|:---|:---|
| Monitor X DOWN, started before ticket, matches symptom | "Root cause: [monitor_name] was DOWN from [start] to [end] (duration: [N] minutes). This directly explains the reported [symptom]." | +0.3 confidence |
| Active status page incident, style=danger | "An active platform incident was declared at [time]: '[title]'. This outage is the likely root cause." | +0.25 confidence |
| No monitors DOWN, all UP | "No infrastructure outage detected at ticket creation time. Root cause is likely application-level, session-specific, or user configuration." | 0 (neutral) |
| Maintenance window active at ticket time | "A scheduled maintenance window was active: [title] ([start]–[end]). The reported issue may be expected behavior during this window." | +0.2 confidence |
| Response time elevated (>3× baseline) | "Elevated response times detected on [monitor_name] at ticket creation time (ping: [N]ms vs baseline [B]ms). Service degradation may explain intermittent issues." | +0.15 confidence |
| SSL cert expiring (<7 days) | "SSL certificate for [monitor_name] expires in [N] days. Users may be seeing certificate warnings." | +0.2 confidence |
| SSL cert invalid (cert_is_valid=0) | "SSL certificate for [monitor_name] is INVALID. TLS connection failures would explain 'connection not secure' reports." | +0.35 confidence |

### 5.2 Confidence scoring with monitoring evidence

```python
def calculate_confidence_with_monitoring(base_confidence, monitoring_evidence):
    boost = 0.0
    rca_components = []
    
    for outage in monitoring_evidence['monitors_down_at_ticket_time']:
        if outage['relevant']:  # matches ticket symptom
            boost += 0.30
            rca_components.append(
                f"{outage['monitor_name']} DOWN for {outage['duration_minutes']}m"
            )
    
    if monitoring_evidence.get('active_incident'):
        incident = monitoring_evidence['active_incident']
        if incident['style'] in ['danger', 'warning']:
            boost += 0.25
            rca_components.append(f"Active incident: {incident['title']}")
    
    # Cap total monitoring boost at 0.40 (can't over-rely on one signal)
    boost = min(boost, 0.40)
    
    final_confidence = min(base_confidence + boost, 1.0)
    
    return final_confidence, rca_components
```

---

## 6. Observation Generator — Monitoring Evidence in Private Notes

The Observation Generator includes monitoring evidence in the private note written to Freshdesk.

### 6.1 Monitoring section template

```html
<p><strong>📊 Infrastructure Status at Ticket Creation</strong><br>
Source: Uptime Kuma (status.getkwikid.com:3001)<br>
Checked: {collected_at_ist}</p>

<!-- If monitors were DOWN -->
<p><strong>🔴 Service Outage Detected:</strong><br>
• {monitor_name}: DOWN since {down_since_ist} ({duration_minutes}m before ticket created)<br>
• Error: {msg}<br>
• Recovery: {recovered_at_ist or "⚠️ Still ongoing"}</p>

<!-- If active status page incident -->
<p><strong>📢 Active Platform Incident:</strong><br>
Title: {incident_title}<br>
Severity: {incident_style}<br>
Posted: {incident_created_ist}<br>
"{incident_content_truncated}"</p>

<!-- If maintenance window -->
<p><strong>🔧 Maintenance Window Active:</strong><br>
{maintenance_title}: {start_time_ist} – {end_time_ist}</p>

<!-- If all UP -->
<p><strong>🟢 All Systems Operational</strong><br>
No outages detected at ticket creation time. Root cause is not infrastructure-level.</p>

<!-- Uptime context -->
<p><strong>Uptime (24h):</strong><br>
{monitor_name}: {uptime_24h_pct}% | Response time: {ping_ms}ms</p>
```

---

## 7. Investigation Planner Decision Tree (Monitoring)

```
Ticket received
    │
    ├── keyword match: "down", "not working", "error", "unavailable",
    │   "not loading", "timeout", "slow", "not responding"
    │         │
    │         ▼
    │   ALWAYS run METRICTOOL + SERVERTOOL
    │
    ├── cf_impact = "DOWNTIME 100% impact"
    │         │
    │         ▼
    │   ALWAYS run METRICTOOL + SERVERTOOL (outage investigation)
    │
    ├── priority = 4 (Urgent)
    │         │
    │         ▼
    │   ALWAYS run METRICTOOL + SERVERTOOL
    │
    ├── Multiple tickets with same symptom in past 30 min
    │   (detected via Freshdesk ticket search)
    │         │
    │         ▼
    │   ALWAYS run METRICTOOL + SERVERTOOL (cluster detection)
    │
    └── None of the above
              │
              ▼
        SKIP monitoring tools (not needed)
        Proceed with SOPTOOL + UNITYTOOL only
```

---

## 8. Monitoring Data in Action Gateway Decision

| Monitoring finding | Action Gateway impact |
|:---|:---|
| Monitor DOWN → outage confirmed → customer-facing service | Route to ESCALATION path (Asana); do not attempt autonomous fix |
| Monitor DOWN → infrastructure-level → affecting multiple tenants | Skip autonomous reply; post draft note flagged for human review |
| Monitor DOWN → recovered before ticket → historical outage | Include in reply as explanation; autonomous resolution appropriate |
| All monitors UP → no outage | Standard investigation path; confidence from SOP match governs action |
| Platform unreachable → no monitoring data | Proceed without monitoring evidence; note data unavailability in private note |
