# Status Model — All Status Values and Their Meaning

**Platform**: Uptime Kuma **1.23.15** at `http://status.getkwikid.com:3001`  
**Source**: Live authenticated extraction 2026-07-11 (status codes verified in heartbeat data) + Uptime Kuma platform architecture

> **Live status distribution (2026-07-11)**: of 332 monitors — **211 UP (code 1)**, **51 MAINTENANCE (code 3)**, **1 DOWN (code 0)**, 69 no-data (paused). The single DOWN monitor is id 432 (`[Monitoring]Bajaj new prod monitoring server`), message "Request failed with status code 500". The 0/1/2/3 codes below are confirmed against live heartbeats.

---

## 1. Monitor Status Values

Uptime Kuma uses integer status codes for monitor state. These appear in:
- Prometheus `monitor_status` metric
- Heartbeat object `status` field
- Socket.io `getMonitorList` response

| Status Code | Label | Color | Meaning |
|:---:|:---|:---|:---|
| `0` | DOWN | 🔴 Red | Monitor check failed; service is unreachable or returned error |
| `1` | UP | 🟢 Green | Monitor check succeeded; service is healthy |
| `2` | PENDING | 🟡 Yellow | Initial state; monitor has been created but no check has completed yet |
| `3` | MAINTENANCE | 🔵 Blue/Grey | Monitor is in a scheduled maintenance window; expected downtime |

---

## 2. Status Behavior Detail

### 2.1 DOWN (status: 0)

A monitor transitions to DOWN when:
- HTTP monitor: response status code outside `accepted_statuscodes` range (default: non-200-299)
- HTTP monitor: connection timeout or connection refused
- TCP monitor: port is unreachable or connection refused
- Ping monitor: no ICMP response within timeout
- DNS monitor: resolution fails or unexpected result
- Push monitor: no heartbeat received within `interval + push_timeout` seconds
- Keyword monitor: expected keyword absent from response body

**DOWN declaration timing**: The monitor must fail `maxretries` consecutive checks before status transitions to DOWN (default `maxretries`: 3). This prevents false positives from transient network blips.

**AI interpretation**: DOWN = confirmed service failure. High-confidence evidence for root cause. Duration of DOWN state is calculated from the `time` field of the first heartbeat with `status: 0` and `important: true` (status-change event).

### 2.2 UP (status: 1)

A monitor transitions to UP when a check succeeds after being in any other state.

**UP declaration timing**: A single successful check is sufficient to return to UP (no success-streak requirement by default).

**AI interpretation**: UP = service is currently healthy. If the ticket was created while the service was UP, the issue is likely not an infrastructure outage — look at application logs, session data, or user-specific configuration.

**Nuance**: A service can be UP in Uptime Kuma but degraded in performance (response time elevated). Always check `monitor_response_time` alongside status.

### 2.3 PENDING (status: 2)

Seen only during initial monitor setup or when a monitor is first activated after being paused. Once the first check runs, status moves to UP or DOWN.

**AI interpretation**: PENDING on any monitor during an active investigation is anomalous and should be flagged — it may indicate the monitor itself has been recently reset or the Uptime Kuma service restarted.

### 2.4 MAINTENANCE (status: 3)

The monitor is in a scheduled maintenance window. Uptime Kuma automatically sets this when a `maintenance` schedule overlaps with the current time.

**AI interpretation**: MAINTENANCE = expected downtime. Tickets created during a MAINTENANCE window should be routed differently:
- Include the maintenance window in the investigation note
- Do NOT escalate as an outage — this is planned
- Reply to customer: "We have a scheduled maintenance window from X to Y. The service should be restored shortly."

---

## 3. Status Color Mapping (for Observation Generator output)

When the AI Observation Generator writes a private note with monitoring evidence, use the following emoji/color convention:

| Status | Emoji | Note text |
|:---|:---|:---|
| UP | 🟢 | `🟢 UP — Response time: 142ms (normal)` |
| DOWN | 🔴 | `🔴 DOWN since 14:23 IST (41 minutes before ticket creation)` |
| PENDING | 🟡 | `🟡 PENDING — monitor status unclear; check monitor configuration` |
| MAINTENANCE | 🔵 | `🔵 MAINTENANCE WINDOW — scheduled downtime 02:00–04:00 IST` |
| Unknown | ⚪ | `⚪ UNKNOWN — monitoring data unavailable` |

---

## 4. Status Page Aggregate Status

The status page itself (not individual monitors) shows an aggregate status in the page header:

| Page Status | Condition | Display text |
|:---|:---|:---|
| All operational | All monitors UP | "All Systems Operational" |
| Partial outage | Some monitors DOWN | "Partial Outage" |
| Major outage | Majority/critical monitors DOWN | "Major Outage" |
| Under maintenance | Any monitor in MAINTENANCE | "Under Maintenance" |
| Degraded performance | All UP but elevated response times | "Degraded Performance" |

These labels are **derived in the Uptime Kuma frontend** — they are not returned directly by the API. The AI must derive the aggregate status from the `heartbeatList` data.

---

## 5. Heartbeat Status vs. Monitor Status

Two slightly different concepts:

**Monitor status** = the current live state of the monitor (what appears in `getMonitorList`).

**Heartbeat status** = the historical record of each individual check result. Each heartbeat has its own `status` field. The current monitor status is the status of the most recent heartbeat.

```
Heartbeat history for monitor_id=1:
  time=14:20  status=1  ping=140ms  (UP)
  time=14:21  status=1  ping=145ms  (UP)
  time=14:22  status=1  ping=139ms  (UP)
  time=14:23  status=0  ping=null   (DOWN — this is when "important: true" fires)
  time=14:24  status=0  ping=null   (DOWN — still down, important=false)
  time=14:25  status=0  ping=null   (DOWN — still down)
  time=14:26  status=1  ping=148ms  (UP — recovery, important=true)
```

The `important: true` flag marks **state transitions** (DOWN→UP or UP→DOWN). Only important heartbeats generate notifications.

---

## 6. Status Transitions and AI Trigger Logic

| Transition | `important` flag | Notification sent? | AI action |
|:---|:---:|:---:|:---|
| UP → DOWN | `true` | ✅ Yes | Check if this transition time correlates with ticket creation time |
| DOWN → UP | `true` | ✅ Yes | Log recovery time; calculate outage duration for RCA |
| UP → UP | `false` | ❌ No | Normal heartbeat; record response time for trending |
| DOWN → DOWN | `false` | ❌ No | Continued outage; duration keeps growing |
| Any → MAINTENANCE | `true` | ✅ (if configured) | Note start of maintenance window |
| MAINTENANCE → Any | `true` | ✅ (if configured) | Note end of maintenance window |

---

## 7. Using Status Model in Investigation

### 7.1 Outage correlation algorithm

```python
def correlate_outage_with_ticket(ticket_created_at, heartbeat_list):
    """
    Check if any monitor was DOWN at or before ticket creation time.
    Returns list of outage events that overlap with ticket creation.
    """
    outages = []
    for monitor_id, heartbeats in heartbeat_list.items():
        # Find important status-change heartbeats (transitions)
        transitions = [h for h in heartbeats if h['important']]
        
        for i, h in enumerate(transitions):
            if h['status'] == 0:  # DOWN transition
                down_time = parse_iso(h['time'])
                # Find next UP transition (recovery)
                up_time = None
                for next_h in transitions[i+1:]:
                    if next_h['status'] == 1:
                        up_time = parse_iso(next_h['time'])
                        break
                
                # Does this DOWN window overlap with ticket creation?
                if down_time <= ticket_created_at:
                    if up_time is None or up_time >= ticket_created_at:
                        outages.append({
                            'monitor_id': monitor_id,
                            'down_since': down_time,
                            'recovered_at': up_time,
                            'duration_minutes': (up_time or now()) - down_time
                        })
    return outages
```

### 7.2 Response time anomaly detection

```python
def detect_response_time_anomaly(heartbeats, threshold_multiplier=3.0):
    """
    Detect if response time at ticket creation was abnormally high.
    """
    pings = [h['ping'] for h in heartbeats if h['ping'] is not None]
    if not pings:
        return None
    
    mean_ping = sum(pings) / len(pings)
    # Simple threshold: flag if 3× above the period average
    anomaly_threshold = mean_ping * threshold_multiplier
    
    recent = heartbeats[-3:]  # Last 3 checks
    elevated = [h for h in recent if h['ping'] and h['ping'] > anomaly_threshold]
    
    return {
        'mean_ping_ms': round(mean_ping, 1),
        'threshold_ms': round(anomaly_threshold, 1),
        'elevated_checks': len(elevated),
        'is_anomalous': len(elevated) >= 2
    }
```
