# API Reference — Uptime Kuma API Complete Reference

**Platform**: Uptime Kuma **1.23.15** at `http://status.getkwikid.com:3001`  
**API key (read-only)**: `uk6_2w7DIcrXJd0y5g7bIJ_0flqk6vV_B6FRZLsEkyr3` — **⚠️ Returned 401 from `/metrics`; confirm against the 3 configured keys**  
**Live access date**: 2026-07-11 (full authenticated discovery via Socket.io login as `root_user`)  
**Source**: Uptime Kuma API documentation + live authenticated verification

> **What's verified live**: The Socket.io authenticated API is the proven data path. On `login`, the server pushes: `monitorList` (332), `heartbeatList`, `importantHeartbeatList`, `avgPing`, `uptime`, `maintenanceList` (26), `statusPageList` (11), `apiKeyList` (3), `notificationList` (2), `proxyList` (1), `info`. The public REST status-page endpoints work per published slug. The Prometheus `/metrics` endpoint exists but rejected the shared key with 401.

---

## 1. API Surfaces

Uptime Kuma exposes three API surfaces:

| Surface | Auth required | Use for AI |
|:---|:---:|:---|
| REST — public endpoints | ❌ No | Status pages, heartbeats — **PRIMARY for AI** |
| REST — Prometheus endpoint | ✅ API key | All monitors current status — **PRIMARY for AI** |
| Socket.io — management API | ✅ Username + password | Admin operations — **DO NOT USE** |

---

## 2. REST API — Public Endpoints (No Auth)

Base URL: `http://status.getkwikid.com:3001`

### 2.1 Entry Page

```
GET /api/entry-page
```

Returns information about the default status page, including its slug.

**Actual live response** (`GET /api/entry-page` — confirmed 2026-07-10):
```json
{
  "type": "entryPage",
  "entryPage": "dashboard"
}
```

> ⚠️ **IMPORTANT**: The entry page is `"dashboard"`, NOT a status page slug. This means NO public status page is configured as the default. There is no `statusPageSlug` field — the entry page type is `"entryPage"`, not `"statusPage"`. All subsequent `/api/status-page/<slug>` calls require a known slug.

**Use**: Determine whether a public status page is configured. Currently: none.

---

### 2.2 Status Page

```
GET /api/status-page/<slug>
```

Returns full configuration of the named status page, including all monitor groups and active incident.

**Parameters**: `slug` — the status page slug (e.g., `kwikid`)

**Response**:
```json
{
  "config": {
    "id": 1,
    "slug": "kwikid",
    "title": "KwikID Platform Status",
    "description": "Real-time status of KwikID services",
    "icon": "/icon.svg",
    "theme": "light",
    "published": true,
    "showTags": false,
    "domainNameList": [],
    "googleAnalyticsId": null,
    "customCSS": "",
    "footerText": null,
    "showPoweredBy": true,
    "showCertificateExpiry": false,
    "autoRefreshInterval": 60
  },
  "incident": null,
  "publicGroupList": [
    {
      "id": 1,
      "name": "Core Platform",
      "weight": 1,
      "monitorList": [
        {
          "id": 1,
          "name": "KwikID API",
          "sendUrl": false,
          "type": "http"
        }
      ]
    }
  ],
  "maintenanceList": []
}
```

**AI use**: Get monitor list, check active incident, check maintenance windows.

---

### 2.3 Status Page Heartbeats

```
GET /api/status-page/heartbeat/<slug>
```

Returns recent heartbeat history and uptime percentages for all monitors on the status page.

**Response**:
```json
{
  "heartbeatList": {
    "1": [
      {
        "monitorID": 1,
        "status": 1,
        "time": "2026-07-10 08:45:00.000",
        "msg": "200 - OK",
        "ping": 142,
        "important": false,
        "duration": 60,
        "localDateTime": "2026-07-10 14:15:00.000",
        "timezone": "Asia/Kolkata",
        "retries": 0,
        "downCount": 0
      }
    ]
  },
  "uptimeList": {
    "1_24": 1.0,
    "1_720": 0.987
  }
}
```

**heartbeatList**: Dictionary keyed by monitor ID (as string). Each value is an array of the most recent heartbeats (default: last 50).

**uptimeList**: Dictionary keyed by `"<monitorID>_<hours>"`. Standard windows:
- `_24` = last 24 hours
- `_720` = last 720 hours (30 days)

**AI use**: PRIMARY — correlate monitor down times with ticket creation, check uptime percentages.

---

### 2.4 Status Badge (SVG)

```
GET /api/badge/<monitorId>/status
GET /api/badge/<monitorId>/status?label=<label>&style=<style>
```

Returns an SVG badge image showing current monitor status. For display use only — AI should use `/metrics` or heartbeat endpoints for programmatic status checks.

---

### 2.5 Uptime Badge (SVG)

```
GET /api/badge/<monitorId>/uptime/<duration>
```

`duration`: `24h`, `7d`, `30d`, `1y`

Returns SVG badge with uptime percentage. For display only.

---

### 2.6 Ping Badge (SVG)

```
GET /api/badge/<monitorId>/ping/<duration>
```

Returns SVG badge with average ping over the duration. For display only.

---

### 2.7 Push Monitor Heartbeat

```
POST /api/push/<pushToken>?status=up&msg=OK&ping=<ms>
```

Used by external applications to send heartbeat to a Push-type monitor. **Not used by AI (AI is a consumer, not a heartbeat sender).**

---

## 3. REST API — Authenticated Endpoints

### 3.1 Prometheus Metrics

```
GET /metrics
Authorization: Bearer uk6_2w7DIcrXJd0y5g7bIJ_0flqk6vV_B6FRZLsEkyr3
```

Alternative auth format:
```
Authorization: Basic <base64("uk6_2w7DIcrXJd0y5g7bIJ_0flqk6vV_B6FRZLsEkyr3:")>
```

Note: Username is the API key, password is empty.

**Response format**: Prometheus text exposition format

```
# HELP monitor_status Monitor Status
# TYPE monitor_status gauge
monitor_status{monitor_name="KwikID API",monitor_type="http",monitor_url="https://api.getkwikid.com/health",monitor_hostname="",monitor_port=""} 1
monitor_status{monitor_name="KwikID VKYC",monitor_type="http",monitor_url="https://vkyc.getkwikid.com/health",monitor_hostname="",monitor_port=""} 0

# HELP monitor_response_time Monitor Response Time (ms)
# TYPE monitor_response_time gauge
monitor_response_time{monitor_name="KwikID API",monitor_type="http",monitor_url="https://api.getkwikid.com/health",monitor_hostname="",monitor_port=""} 142
monitor_response_time{monitor_name="KwikID VKYC",monitor_type="http",monitor_url="https://vkyc.getkwikid.com/health",monitor_hostname="",monitor_port=""} 0

# HELP monitor_cert_days_remaining Monitor Cert Days Remaining
# TYPE monitor_cert_days_remaining gauge
monitor_cert_days_remaining{monitor_name="KwikID API",monitor_url="https://api.getkwikid.com/health"} 67

# HELP monitor_cert_is_valid Monitor Cert Is Valid
# TYPE monitor_cert_is_valid gauge
monitor_cert_is_valid{monitor_name="KwikID API",monitor_url="https://api.getkwikid.com/health"} 1
```

**HTTP status codes**:
- `200 OK` — success
- `401 Unauthorized` — invalid or missing API key
- `403 Forbidden` — API key valid but lacks permission (should not occur with standard key)

---

## 4. Socket.io API (Management — AI MUST NOT USE for mutations)

Socket.io connects to: `http://status.getkwikid.com:3001`

### 4.1 Connection and authentication

```javascript
const socket = io('http://status.getkwikid.com:3001');

// Must authenticate first (verified working with root_user)
socket.emit('login', {
  username: '<admin-username>',
  password: '<admin-password>',
  token: ''  // 2FA token if enabled; empty string if not
});

// The login emit takes an ACK callback, not a 'loginResult' event:
socket.emit('login', creds, (result) => {
  if (result.ok) {
    // Authenticated. result also carries { token, tags: [...] }.
    // The server then AUTOMATICALLY pushes monitorList, heartbeatList, etc.
  } else {
    console.error('Login failed:', result.msg);
  }
});
```

**Verified login response shape** (live): `{ "ok": true, "token": "<jwt>", "tags": [ {id,name,color}, … 14 tags ] }`.

**Key behavior (verified)**: You do **not** need to call `getMonitorList` — on successful `login`, Uptime Kuma 1.23.15 automatically emits `monitorList`, `heartbeatList` (per monitor), `importantHeartbeatList`, `avgPing`, `uptime`, `maintenanceList`, `statusPageList`, `apiKeyList`, `notificationList`, `proxyList`, `dockerHostList`, and `info` to the authenticated socket.

**CRITICAL**: The AI must NEVER store or use admin username/password for mutations. Socket.io auth grants full admin access. If read integration uses this path, isolate credentials in a secret manager and never call write events.

### 4.2 Server-pushed events on login (verified live)

| Event received | Payload | Live count |
|:---|:---|:---|
| `monitorList` | `{ "<id>": {monitor…} }` full config per monitor | 332 |
| `heartbeatList` | `("<id>", [ {id,important,monitor_id,status,msg,time,ping,duration,down_count}, … ])` | 332 lists |
| `importantHeartbeatList` | transition-only heartbeats per monitor | 332 lists |
| `avgPing` | `("<id>", <ms>)` average response time | 122 reporting |
| `uptime` | `("<id>", <periodHours>, <fraction>)` | 24h + 720h per monitor |
| `maintenanceList` | `{ "<id>": {title,strategy,status,active,…} }` | 26 |
| `statusPageList` | `{ "<id>": {slug,title,published,domainNameList} }` | 11 |
| `apiKeyList` | `[ {id,name,active,expires,createdDate} ]` (hashed keys) | 3 |
| `notificationList` | `[ {id,name,config,isDefault} ]` | 2 |
| `proxyList` | `[ {id,protocol,host,port,…} ]` | 1 |
| `info` | `{version,latestVersion,isContainer,serverTimezone,…}` | 1 |

### 4.2b Explicit READ-ONLY events (emit to request)

| Event (emit) | Response event | Returns |
|:---|:---|:---|
| `getMonitorList` | `monitorList` | Complete list of all monitors with full config |
| `getHeartbeatList` | `heartbeatList` | Heartbeat history for a monitor (`{monitorID, period}`) |
| `getImportantHeartbeatList` | `importantHeartbeatList` | Status-transition heartbeats only |
| `getMonitor` | `monitor` | Single monitor details (`{monitorID}`) |
| `getStatusPage` | `statusPage` | Status page config + monitors |
| `getIncidentList` | `incidentList` | All incidents for a monitor |
| `getTags` | `tagList` | All tags (14 on this instance) |
| `getSettings` | `info` | Instance settings (non-sensitive) |

### 4.3 WRITE Socket.io events (AI MUST NEVER CALL)

```
addMonitor, editMonitor, deleteMonitor
addNotification, editNotification, deleteNotification
addStatusPage, editStatusPage, deleteStatusPage
postIncident, unpinIncident
setSettings, changePassword
resumeMonitor, pauseMonitor
clearEvents, clearHeartbeats
clearStatistics
```

---

## 5. API Client Implementation

### 5.1 METRICTOOL client (Python)

```python
import httpx
import re
from typing import Optional

class UptimeKumaClient:
    BASE_URL = "http://status.getkwikid.com:3001"
    API_KEY = "uk6_2w7DIcrXJd0y5g7bIJ_0flqk6vV_B6FRZLsEkyr3"
    
    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.headers_auth = {"Authorization": f"Bearer {self.API_KEY}"}
    
    def get_entry_page(self) -> dict:
        """Returns default status page slug."""
        r = httpx.get(f"{self.BASE_URL}/api/entry-page", timeout=self.timeout)
        r.raise_for_status()
        return r.json()
    
    def get_status_page(self, slug: str) -> dict:
        """Returns full status page config, incident, monitor groups, maintenance."""
        r = httpx.get(f"{self.BASE_URL}/api/status-page/{slug}", timeout=self.timeout)
        r.raise_for_status()
        return r.json()
    
    def get_heartbeats(self, slug: str) -> dict:
        """Returns recent heartbeats and uptime percentages for all monitors."""
        r = httpx.get(
            f"{self.BASE_URL}/api/status-page/heartbeat/{slug}",
            timeout=self.timeout
        )
        r.raise_for_status()
        return r.json()
    
    def get_prometheus_metrics(self) -> str:
        """Returns raw Prometheus metrics text for all monitors."""
        r = httpx.get(
            f"{self.BASE_URL}/metrics",
            headers=self.headers_auth,
            timeout=self.timeout
        )
        r.raise_for_status()
        return r.text
    
    def parse_prometheus_metrics(self, raw: str) -> list[dict]:
        """
        Parse Prometheus text format into structured records.
        Returns list of {metric, monitor_name, monitor_type, monitor_url, value}.
        """
        results = []
        pattern = re.compile(
            r'^(monitor_\w+)\{([^}]+)\}\s+([\d.]+(?:e[+-]?\d+)?)',
            re.MULTILINE
        )
        for match in pattern.finditer(raw):
            metric_name = match.group(1)
            labels_raw = match.group(2)
            value = float(match.group(3))
            
            labels = {}
            for label_match in re.finditer(r'(\w+)="([^"]*)"', labels_raw):
                labels[label_match.group(1)] = label_match.group(2)
            
            results.append({
                'metric': metric_name,
                'value': value,
                **labels
            })
        return results
    
    def get_all_monitor_statuses(self) -> dict[str, dict]:
        """
        Returns dict keyed by monitor_name with status and response_time.
        """
        raw = self.get_prometheus_metrics()
        parsed = self.parse_prometheus_metrics(raw)
        
        statuses = {}
        for entry in parsed:
            name = entry.get('monitor_name', 'unknown')
            if name not in statuses:
                statuses[name] = {
                    'monitor_name': name,
                    'monitor_type': entry.get('monitor_type'),
                    'monitor_url': entry.get('monitor_url'),
                    'status': None,
                    'response_time_ms': None,
                    'cert_days_remaining': None,
                    'cert_is_valid': None
                }
            
            if entry['metric'] == 'monitor_status':
                statuses[name]['status'] = int(entry['value'])
            elif entry['metric'] == 'monitor_response_time':
                statuses[name]['response_time_ms'] = int(entry['value'])
            elif entry['metric'] == 'monitor_cert_days_remaining':
                statuses[name]['cert_days_remaining'] = int(entry['value'])
            elif entry['metric'] == 'monitor_cert_is_valid':
                statuses[name]['cert_is_valid'] = bool(int(entry['value']))
        
        return statuses
```

---

## 6. Rate Limits

Uptime Kuma does not document explicit API rate limits. However:

- The `/metrics` endpoint hits the SQLite database on each request — frequent polling (< 10s intervals) could cause performance degradation
- Recommendation: **Poll at most once per ticket investigation** (on-demand, not continuous)
- For status page endpoints (public): safe to poll at the page's `autoRefreshInterval` (default 60s) as a maximum cadence

---

## 7. Error Handling

| HTTP Status | Cause | AI handling |
|:---|:---|:---|
| `200 OK` | Success | Parse and use response |
| `401 Unauthorized` | Invalid API key for `/metrics` | Log error; investigation proceeds without monitoring data |
| `404 Not Found` | Invalid slug for status page | Log error; slug may have changed; try `entry-page` to re-discover |
| `500 Internal Server Error` | Uptime Kuma server error | Retry once after 2s; if still failing, proceed without monitoring data |
| Connection timeout | `status.getkwikid.com:3001` unreachable | Log; monitoring platform itself may be down; mark as UNAVAILABLE in evidence |

---

## 8. Curl Examples (for live testing)

```bash
# Get entry page slug
curl -s http://status.getkwikid.com:3001/api/entry-page | jq .

# Get status page with monitors and incident
curl -s http://status.getkwikid.com:3001/api/status-page/kwikid | jq .

# Get heartbeats + uptime
curl -s http://status.getkwikid.com:3001/api/status-page/heartbeat/kwikid | jq .

# Get Prometheus metrics (all monitors, current)
curl -s -H "Authorization: Bearer uk6_2w7DIcrXJd0y5g7bIJ_0flqk6vV_B6FRZLsEkyr3" \
  http://status.getkwikid.com:3001/metrics

# Check which monitors are currently DOWN
curl -s -H "Authorization: Bearer uk6_2w7DIcrXJd0y5g7bIJ_0flqk6vV_B6FRZLsEkyr3" \
  http://status.getkwikid.com:3001/metrics | grep "monitor_status" | grep " 0$"

# Get monitor IDs from heartbeat list
curl -s http://status.getkwikid.com:3001/api/status-page/heartbeat/kwikid | \
  jq '.heartbeatList | keys'
```
