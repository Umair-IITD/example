# Metrics Reference — All Available Metrics

**Platform**: Uptime Kuma **1.23.15** at `http://status.getkwikid.com:3001`  
**Source**: Live authenticated extraction 2026-07-11 (heartbeat/avgPing/uptime events) + Uptime Kuma Prometheus spec

---

## 0. Live Metrics Snapshot (2026-07-11)

Extracted from the authenticated Socket.io session (`avgPing`, `uptime`, `heartbeatList` events):

| Metric | Value |
|:---|:---|
| Monitors reporting response time | 122 (of 332) |
| Response time (avgPing) min / p50 / p95 / max | 1 ms / 26 ms / 223 ms / 1282 ms |
| Uptime periods tracked | 24h and 720h (30d) per monitor |
| Example 24h/30d uptime (monitor 202, Flagsmith) | 99.93% / 99.98% |
| Example low uptime (monitor 432, Bajaj monitoring, currently DOWN) | 60.08% / 78.40% |
| RBL memory-space monitors (31–34) 24h uptime | 50–88% (frequent threshold breaches) |

The Uptime Kuma `uptime` event delivers rolling values keyed as `{monitorID}_{periodHours}` (e.g. `202_24`, `202_720`) as a 0–1 fraction.

---

---

## 1. Prometheus Metrics (`/metrics` endpoint)

The `/metrics` endpoint returns data in Prometheus exposition format. Requires API key authentication.

### 1.1 Endpoint

```
GET http://status.getkwikid.com:3001/metrics
Authorization: Bearer uk6_2w7DIcrXJd0y5g7bIJ_0flqk6vV_B6FRZLsEkyr3
```

### 1.2 All available metrics

#### `monitor_status`

```
# HELP monitor_status Monitor Status
# TYPE monitor_status gauge
monitor_status{
  monitor_name="KwikID API",
  monitor_type="http",
  monitor_url="https://api.getkwikid.com/health",
  monitor_hostname="",
  monitor_port=""
} 1
```

| Property | Detail |
|:---|:---|
| Type | Gauge |
| Unit | Enumeration: 0=DOWN, 1=UP, 2=PENDING, 3=MAINTENANCE |
| Update frequency | Updated after each check (every `interval` seconds) |
| Label: `monitor_name` | Human-readable monitor name as configured |
| Label: `monitor_type` | Monitor type (http, tcp, ping, dns, push, etc.) |
| Label: `monitor_url` | Target URL (for http type) |
| Label: `monitor_hostname` | Hostname (for tcp/ping/dns type) |
| Label: `monitor_port` | Port number (for tcp type) |
| **AI use** | **Primary indicator** — check this first in every investigation |

#### `monitor_response_time`

```
# HELP monitor_response_time Monitor Response Time (ms)
# TYPE monitor_response_time gauge
monitor_response_time{
  monitor_name="KwikID API",
  monitor_type="http",
  monitor_url="https://api.getkwikid.com/health",
  monitor_hostname="",
  monitor_port=""
} 142
```

| Property | Detail |
|:---|:---|
| Type | Gauge |
| Unit | Milliseconds (ms) |
| Value when DOWN | `0` or absent (no response to measure) |
| Update frequency | Updated after each successful check |
| **AI use** | **Performance degradation signal** — elevated response times indicate overload before outage |
| Typical healthy range | 50–500ms for HTTP APIs (application-dependent) |
| Degradation threshold | Application-specific; flag if 3× above recent average |

#### `monitor_cert_days_remaining`

```
# HELP monitor_cert_days_remaining Monitor Cert Days Remaining
# TYPE monitor_cert_days_remaining gauge
monitor_cert_days_remaining{
  monitor_name="KwikID API",
  monitor_url="https://api.getkwikid.com/health"
} 67
```

| Property | Detail |
|:---|:---|
| Type | Gauge |
| Unit | Days until SSL certificate expiry |
| Only present for | HTTPS monitors where `expiryNotification: true` |
| **AI use** | Expired/expiring cert → "SSL certificate issues" root cause; customer may see cert errors |
| Alert threshold | Standard: <30 days = warning; <7 days = critical |

#### `monitor_cert_is_valid`

```
# HELP monitor_cert_is_valid Monitor Cert Is Valid
# TYPE monitor_cert_is_valid gauge
monitor_cert_is_valid{
  monitor_name="KwikID API",
  monitor_url="https://api.getkwikid.com/health"
} 1
```

| Property | Detail |
|:---|:---|
| Type | Gauge |
| Unit | Boolean: 1=valid, 0=invalid/expired |
| **AI use** | `cert_is_valid: 0` + user reports "site not loading" / "security error" = cert expiry root cause |

---

## 2. Heartbeat Object Metrics

Heartbeats are the primary time-series data source. Retrieved from:
- `GET /api/status-page/heartbeat/<slug>` — last ~50 heartbeats per monitor on the status page
- Socket.io `getHeartbeatList` — configurable window of heartbeats

### 2.1 Full heartbeat object (real, live)

The authenticated `heartbeatList` event returns **snake_case** fields (verified live):

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

> The public status-page heartbeat API (`/api/status-page/heartbeat/<slug>`) returns a slightly different, camelCase shape. The `important`/`status`/`msg`/`time`/`ping` fields are common to both. When integrating, key off the transport you actually use.

### 2.2 Heartbeat field reference (live snake_case)

| Field | Type | Unit | AI significance |
|:---|:---|:---|:---|
| `id` | integer | — | Unique heartbeat row ID (monotonic; useful for ordering) |
| `monitor_id` | integer | — | Links to monitor configuration |
| `status` | integer | 0/1/2/3 | Current check result; 0=DOWN, 1=UP, 2=PENDING, 3=MAINTENANCE |
| `time` | string | server-local datetime | When the check ran (Asia/Calcutta, UTC+5:30); compare to ticket `created_at` |
| `msg` | string | — | HTTP status ("200 - OK"), error ("Request failed with status code 500"), etc. |
| `ping` | integer | milliseconds | Response time for this check; `null`/absent when DOWN |
| `important` | 0/1 | — | `1` = state transition (UP↔DOWN); key filter for incident reconstruction |
| `duration` | integer | seconds | Time since the previous heartbeat |
| `down_count` | integer | count | Cumulative consecutive DOWN checks; resets to 0 on recovery |

---

## 3. Uptime Percentage Metrics

### 3.1 From status page heartbeat response

The `/api/status-page/heartbeat/<slug>` response includes an `uptimeList` field:

```json
{
  "uptimeList": {
    "1_24": 1.0,
    "1_720": 0.9870,
    "2_24": 0.9917,
    "2_720": 0.9995
  }
}
```

Key: `<monitorID>_<hours>`

| Key suffix | Window | Description |
|:---|:---|:---|
| `_24` | Last 24 hours | Daily uptime percentage |
| `_720` | Last 720 hours (30 days) | Monthly uptime percentage |

Value range: `0.0` (0% uptime / fully down) to `1.0` (100% uptime / fully up)

**AI use**: Low `_24` uptime correlates with a bad day for the service; context for ticket triage:
- `_24 < 0.99` → Multiple short outages today; degraded service day
- `_24 < 0.95` → Significant outage; likely cause of ticket clusters
- `_24 < 0.90` → Major outage day; high-priority investigation

---

## 4. Error Message Taxonomy

The `msg` field in heartbeats provides diagnostic detail. Key patterns:

| `msg` value | Meaning | AI investigation action |
|:---|:---|:---|
| `200 - OK` | HTTP 200 response | Service UP and healthy |
| `201 - Created` | HTTP 201 | Service UP |
| `400 - Bad Request` | HTTP 400 | Service UP but may indicate config issue with monitor |
| `401 - Unauthorized` | HTTP 401 | Service UP but auth failing — potential config drift |
| `403 - Forbidden` | HTTP 403 | Service UP but access denied |
| `429 - Too Many Requests` | HTTP 429 | Rate limited; service UP but under load |
| `500 - Internal Server Error` | HTTP 500 | Service UP but returning errors — application-level failure |
| `502 - Bad Gateway` | HTTP 502 | Upstream/proxy issue; load balancer healthy but backend failing |
| `503 - Service Unavailable` | HTTP 503 | Service explicitly reporting unavailable — often maintenance or overload |
| `TIMEOUT` | No response within timeout | Network issue or complete service failure |
| `ECONNREFUSED` | Connection refused | Process not listening on port; service crashed |
| `ENOTFOUND` | DNS resolution failed | DNS issue or hostname wrong |
| `getaddrinfo ENOTFOUND` | DNS lookup failure | DNS server unreachable or domain deleted |
| `certificate has expired` | SSL cert expired | TLS/SSL issue — root cause for users getting "connection not secure" |
| `self signed certificate` | Self-signed cert | TLS verification failing |
| Keyword `not found` | Keyword monitor: expected string absent | Application-level health check failing |

---

## 5. Response Time Baselines (to be established with live data)

Once live access to `status.getkwikid.com:3001` is available, establish baselines per monitor:

| Metric to establish | Method | Formula |
|:---|:---|:---|
| Baseline ping (30d mean) | Collect 30d heartbeat pings | `mean(ping for h where h.status == 1, h.time >= 30d ago)` |
| P95 response time | Collect 30d pings | `percentile(pings, 95)` |
| Degradation threshold | 3× baseline mean | `baseline_mean * 3.0` |
| Slow threshold | 2× baseline mean | `baseline_mean * 2.0` |

**Current baselines (2026-07-11 snapshot)**: A point-in-time `avgPing` was captured for 122 reporting monitors — fleet-wide min 1 ms, p50 26 ms, p95 223 ms, max 1282 ms. This is a single snapshot, not a 30-day rolling baseline; establish per-monitor 30d baselines by collecting heartbeat history over time. Uptime Kuma already computes rolling **24h and 30d uptime** per monitor (delivered via the `uptime` event), which can seed SLA tracking immediately.

Example live values:
```
# Monitor 202 (Flagsmith, dev.vkyc): uptime 24h 99.93% / 30d 99.98%
# Monitor 432 (Bajaj monitoring, DOWN): uptime 24h 60.08% / 30d 78.40%
# Monitors 31-34 (RBL memory-space): uptime 24h 50-88% (flapping)
```

---

## 6. Metric Collection Strategy for AI

### 6.1 On-demand collection (per-ticket)

METRICTOOL collects metrics at ticket investigation time:

```python
def collect_monitoring_evidence(ticket_created_at, slug):
    """
    Collect monitoring evidence at investigation time.
    """
    # 1. Get current status of all monitors
    prometheus_data = get_prometheus_metrics()  # /metrics endpoint
    
    # 2. Get heartbeat history
    heartbeat_data = get_heartbeats(slug)  # /api/status-page/heartbeat/<slug>
    
    # 3. Get active status page incident
    status_page = get_status_page(slug)  # /api/status-page/<slug>
    active_incident = status_page.get('incident')
    
    # 4. Correlate with ticket creation time
    outages = find_outages_at_time(heartbeat_data, ticket_created_at)
    
    return {
        'prometheus': prometheus_data,           # Current live status
        'outages_at_ticket_time': outages,       # Historical correlation
        'active_incident': active_incident,       # Known incident banner
        'uptime_24h': extract_uptime(heartbeat_data, '24'),  # Today's uptime
        'evidence_collected_at': now_utc()
    }
```

### 6.2 What to include in the Evidence Collector output

```json
{
  "source": "uptime_kuma",
  "collected_at": "2026-07-10T09:45:00Z",
  "monitors_down_at_ticket_time": [
    {
      "monitor_id": 3,
      "monitor_name": "KwikID VKYC Service",
      "down_since": "2026-07-10T09:22:00Z",
      "down_since_ist": "2026-07-10T14:52:00+05:30",
      "duration_minutes": 23,
      "error_msg": "TIMEOUT",
      "recovered_at": null,
      "ongoing": true
    }
  ],
  "active_incident": {
    "title": "VKYC Service Degradation",
    "style": "danger",
    "content": "...",
    "created_at": "2026-07-10T09:30:00Z"
  },
  "uptime_percentages": {
    "KwikID API_24h": 1.0,
    "KwikID VKYC Service_24h": 0.847
  },
  "all_monitors_current_status": {
    "KwikID API": {"status": 1, "ping_ms": 142},
    "KwikID VKYC Service": {"status": 0, "ping_ms": null}
  }
}
```
