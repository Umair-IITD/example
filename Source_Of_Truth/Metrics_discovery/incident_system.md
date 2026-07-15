# Incident System — Uptime Kuma Incidents and Correlation

**Platform**: Uptime Kuma **1.23.15** at `http://status.getkwikid.com:3001`  
**Source**: Live authenticated extraction 2026-07-11 (`maintenanceList` event) + Uptime Kuma platform architecture

---

## 0. Live Maintenance Windows (26 configured)

At extraction time there were **26 maintenance windows**: 9 `under-maintenance` (active now), 9 `scheduled` (future/recurring), 8 `ended`. This is operationally significant — **51 monitors currently read MAINTENANCE status** because they fall inside one of the active windows.

**Strategies in use**: recurring-weekday (7), single (8), recurring-interval (5), manual (4), cron (2).

| ID | Title | Strategy | Status | Active |
|:--|:--|:--|:--|:--|
| 1 | Planned Night Shutdown 8 PM to 8 AM | recurring-interval | scheduled | ✅ |
| 2 | Planned Night Shutdown 9 PM to 9 AM | recurring-interval | scheduled | ✅ |
| 3 | Planned Weekend Shutdown Saturday & Sunday | recurring-weekday | under-maintenance | ✅ |
| 4 | Planned Night Shutdown 8 PM to 10 AM | recurring-interval | scheduled | ✅ |
| 5 | RBL UAT OFF | recurring-weekday | under-maintenance | ✅ |
| 6 | RBL \| [Docker] RBL-uat - celery - elastic_mq - user_redis | single | ended | ✅ |
| 7 | FINO Service Shutdown on request of CLIENT | single | ended | ✅ |
| 8 | FINO Service | single | ended | ✅ |
| 9 | fino_maintenance | single | ended | ✅ |
| 11 | Bajaj UAT | manual | under-maintenance | ✅ |
| 14 | Unity old UAT server | manual | under-maintenance | ✅ |
| 15 | Unity UAT Maintenance | single | ended | ✅ |
| 18 | Unity UAT Daily Maintenance | cron | scheduled | ✅ |
| 20 | SaaS UAT Servers Planned Night Shutdown 8 PM to 8 AM | recurring-interval | scheduled | ✅ |
| 21 | SaaS UAT Servers Sunday Shutdown | cron | scheduled | ✅ |
| 22 | Sunday Shutdown | recurring-weekday | scheduled | ✅ |
| 23 | Unity UAT servers shutdown weekend | recurring-weekday | under-maintenance | ✅ |
| 30 | BOB Weekend Shutdown | recurring-weekday | under-maintenance | ✅ |
| 31 | Unity New servers | recurring-interval | scheduled | ✅ |
| 32 | RBL Uat server | single | ended | ✅ |
| 33 | Bajaj old server | manual | under-maintenance | ✅ |
| 34 | in dpelyment phase | single | ended | ✅ |
| 35 | BOB Prod Ext Server Maintenance | single | ended | ✅ |
| 36 | New Unity Down | manual | under-maintenance | ✅ |
| 37 | Prod Shutdown 12 PM to 8 AM | recurring-weekday | scheduled | ✅ |
| 39 | BOB UAT Maintenance | recurring-weekday | under-maintenance | ✅ |

> **AI correlation rule**: Before treating a DOWN/MAINTENANCE monitor as an incident, check whether it is covered by an active window above. Many client UAT servers (RBL, Bajaj, Unity, BOB UAT) are deliberately shut down nightly / on weekends via these windows — a MAINTENANCE status there is expected, not an outage. Windows like "Planned Night Shutdown 8 PM to 8 AM" and "…Weekend Shutdown" are routine cost-saving shutdowns of non-prod environments.

---

## 1. Uptime Kuma Incident Model

Uptime Kuma has two distinct concepts that are both loosely called "incidents":

1. **Status Page Incidents**: Manually posted announcements pinned to the public status page. Used for communicating with users about ongoing issues.
2. **Heartbeat Events (DOWN events)**: Automatically recorded when a monitor transitions to DOWN status. These are the technical record of actual service failures.

The AI Investigation Layer uses **both** — heartbeat DOWN events for automated root cause correlation, and status page incidents for context about known issues.

---

## 2. Status Page Incidents (Manual Announcements)

### 2.1 Incident object structure

```json
{
  "id": 12,
  "style": "danger",
  "title": "KwikID API Degradation",
  "content": "We are investigating elevated error rates on the KwikID API server. The VKYC session creation endpoint is experiencing elevated response times. Our team is working to resolve this. ETA: 30 minutes.",
  "pin": true,
  "createdDate": "2026-07-10T09:30:00.000Z",
  "lastUpdatedDate": "2026-07-10T10:15:00.000Z"
}
```

### 2.2 Incident styles

| Style | Color | Severity | When to use |
|:---|:---|:---|:---|
| `danger` | 🔴 Red | Critical | Major outage; service completely unavailable |
| `warning` | 🟡 Yellow | Warning | Partial degradation; some users affected |
| `primary` | 🔵 Blue | Info | General announcement; investigation in progress |
| `info` | 💠 Light blue | Informational | Low-impact notice |
| `light` | ⚪ Grey | Minimal | Resolved or maintenance notice |
| `dark` | ⚫ Dark | — | Custom use |

### 2.3 AI interpretation of incident field

The `incident` field in `/api/status-page/<slug>` is `null` when there is no active pinned incident.

**When `incident` is not null**:
```python
def interpret_incident_for_ai(incident, ticket_created_at):
    if incident is None:
        return {"active_incident": False}
    
    created = parse_iso(incident['createdDate'])
    
    # Was the incident posted before the ticket was created?
    incident_predates_ticket = created <= ticket_created_at
    
    return {
        "active_incident": True,
        "title": incident['title'],
        "style": incident['style'],
        "severity": map_style_to_severity(incident['style']),
        "content": incident['content'],
        "created_at": incident['createdDate'],
        "predates_ticket": incident_predates_ticket,
        # If the incident was posted before the ticket, the user's issue is likely related
        "likely_related": incident_predates_ticket and incident['style'] in ['danger', 'warning']
    }
```

**AI action based on incident**:
- `likely_related: True` + `style: danger` → Strong outage signal; include incident content in RCA; reply to customer acknowledging the known issue
- `likely_related: True` + `style: warning` → Degradation signal; include in investigation; lower escalation threshold
- `likely_related: False` (incident posted AFTER ticket) → Coincidence; treat independently

---

## 3. Heartbeat DOWN Events (Automated Incidents)

### 3.1 What constitutes a heartbeat DOWN event

In Uptime Kuma's data model, a heartbeat with `important: 1` and `status: 0` is the start of a DOWN incident. The `important` flag is set only on status **transitions** — not on every failed check.

> **Live schema note**: The Socket.io `heartbeatList` event uses snake_case field names, NOT the camelCase shown in older docs. The **real** heartbeat object from this instance is:

```json
{
  "id": 259403961,
  "important": 0,
  "monitor_id": 2,
  "status": 1,
  "msg": "200 - OK",
  "time": "2026-07-11 10:36:20.318",
  "ping": 18,
  "duration": 120,
  "down_count": 0
}
```

A real DOWN heartbeat (the one live DOWN monitor, id 432 — "[Monitoring]Bajaj new prod monitoring server") carried `"msg": "Request failed with status code 500"`.

Key fields for incident reconstruction (live field names):

| Field | Meaning |
|:---|:---|
| `important: 1` | This is a state transition (UP→DOWN or DOWN→UP) |
| `status: 0` | This is the START of a DOWN event (0=DOWN, 1=UP, 2=PENDING, 3=MAINTENANCE) |
| `time` | Timestamp of the transition (server local time, Asia/Calcutta) |
| `monitor_id` | Integer monitor ID (snake_case) |
| `msg` | Error message from the failed check (e.g., "Request failed with status code 500", "200 - OK") |
| `down_count` | Cumulative DOWN count (snake_case; resets to 0 on recovery) |
| `duration` | Seconds since the previous heartbeat/transition |
| `ping` | Response time in ms (may be null when unreachable) |

The companion UP recovery heartbeat:
```json
{
  "monitorID": 3,
  "status": 1,
  "time": "2026-07-10 09:47:00.000",
  "msg": "200 - OK",
  "ping": 156,
  "important": true,
  "duration": 1440,
  "localDateTime": "2026-07-10 15:17:00.000",
  "timezone": "Asia/Kolkata",
  "retries": 0,
  "downCount": 0
}
```

The `duration` field on the recovery heartbeat is the number of **seconds** since the previous important event — in this case, the duration of the outage (1440 seconds = 24 minutes).

### 3.2 Calculating outage duration

```python
def calculate_outage_duration(heartbeats):
    """
    From a list of heartbeats (important=true only), reconstruct outage periods.
    """
    outages = []
    pending_down = None
    
    for h in sorted(heartbeats, key=lambda x: x['time']):
        if not h.get('important'):
            continue
        
        if h['status'] == 0 and pending_down is None:
            pending_down = h['time']
        elif h['status'] == 1 and pending_down is not None:
            outages.append({
                'start': pending_down,
                'end': h['time'],
                'duration_seconds': h.get('duration', 0),
                'msg': h.get('msg', ''),
                'recovery_ping_ms': h.get('ping')
            })
            pending_down = None
    
    # Still ongoing outage
    if pending_down is not None:
        outages.append({
            'start': pending_down,
            'end': None,
            'duration_seconds': None,
            'ongoing': True
        })
    
    return outages
```

---

## 4. Incident → Freshdesk Ticket Correlation

### 4.1 Correlation heuristic

A monitoring DOWN event is likely the root cause of a Freshdesk support ticket when:

1. The DOWN event started **at or before** the ticket creation time
2. The DOWN event ended **at or after** the ticket creation time (or is still ongoing)
3. The DOWN monitor is **relevant to the ticket's reported symptom** (e.g., "VKYC session not loading" → VKYC service monitor DOWN)

### 4.2 Correlation confidence levels

| Condition | Confidence | AI action |
|:---|:---|:---|
| Monitor DOWN, incident predates ticket, monitor name matches symptom | HIGH | Auto-reply with outage acknowledgment; skip deep investigation |
| Monitor DOWN, incident predates ticket, monitor name loosely related | MEDIUM | Include in investigation note; escalation threshold lowered |
| Monitor DOWN, incident starts after ticket | LOW | May be same root cause; note as context only |
| No monitors DOWN at ticket time | NONE | Outage correlation not the cause; proceed with session/user investigation |
| Active status page incident + DOWN heartbeats | HIGH | Both signals present; very high confidence |

### 4.3 Freshdesk reply template for confirmed outage correlation

When confidence is HIGH and the RCA is an infrastructure outage:

```html
<p>Hi [Customer Name],</p>

<p>Thank you for reaching out. We have identified a platform-level issue that was affecting [service name] between [start time IST] and [end time IST].</p>

<p>The issue has been [resolved / is currently being investigated by our team].</p>

<p>[If resolved]: The service has been restored as of [recovery time IST]. Please try the action again and let us know if the issue persists.</p>

<p>[If ongoing]: Our engineering team is actively working on a resolution. We will update you as soon as service is restored. Estimated resolution time: [ETA if available].</p>

<p>Regards,<br>KwikID Support Team</p>
```

---

## 5. Incident Lifecycle

```
Monitor check fails
        │
        ▼
Check is retried (maxretries times)
        │
        ├── Still failing after maxretries
        │         ▼
        │   Heartbeat recorded: status=0, important=true
        │         ▼
        │   Notification sent (configured channels)
        │         ▼
        │   METRICTOOL detects DOWN via /metrics or heartbeat API
        │         ▼
        │   Evidence Collector receives DOWN evidence
        │         ▼
        │   Root Cause Engine correlates with ticket timestamps
        │
        └── Recovers before maxretries
                  ▼
            Transient failure (not recorded as DOWN)
            Not visible to the AI — blind spot
```

### 5.1 Blind spot: transient failures

If a service fails 1-2 times but not enough to exceed `maxretries`, Uptime Kuma records the failed heartbeats with `status: 0` but does NOT set `important: true`. These appear in the heartbeat list but may not generate notifications.

The AI should scan ALL heartbeats (not just important ones) in the window around ticket creation for any `status: 0` entries — even transient failures may explain user-reported intermittent issues.

---

## 6. Active Incidents — Live Query

```bash
# Check for active status page incident
curl http://status.getkwikid.com:3001/api/status-page/<slug> | jq '.incident'

# Get important heartbeats only (status transitions) via Socket.io getImportantHeartbeatList
# (requires Socket.io client, not available via REST)

# Alternatively: get last 50 heartbeats and filter for important=true
curl http://status.getkwikid.com:3001/api/status-page/heartbeat/<slug> | \
  jq '.heartbeatList | to_entries[] | .value[] | select(.important == true)'
```

**Current active incidents (2026-07-11)**: No manually-posted status-page incidents were present in the extract. The only automated DOWN event at extraction time was monitor id 432 (`[Monitoring]Bajaj new prod monitoring server`, HTTP 500). 51 monitors were in MAINTENANCE via scheduled windows (§0) — these are planned, not incidents.
