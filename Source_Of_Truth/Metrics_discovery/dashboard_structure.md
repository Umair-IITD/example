# Dashboard Structure — Status Pages and UI

**Platform**: Uptime Kuma **1.23.15** at `http://status.getkwikid.com:3001`  
**Source**: Live authenticated extraction 2026-07-11 (`statusPageList` event)  
**Live data**: ✅ 11 published status pages extracted (slugs, titles, domains below)

---

## 0. Live Status Pages (11 published)

| ID | Slug | Title | Published | Custom domain |
|:--|:--|:--|:--|:--|
| 1 | `global` | Global Status Page | ✅ | — |
| 2 | `bob` | Bank Of Baroda | ✅ | `videokyc.bankofbaroda.com/v1/user/health` |
| 3 | `rbl` | RBL Bank | ✅ | — |
| 4 | `tatacap` | Tata Capital | ✅ | — |
| 5 | `cbi` | Central Bank of India | ✅ | — |
| 6 | `canarabank` | Canara Bank | ✅ | — |
| 7 | `unity-bank` | Unity Bank | ✅ | — |
| 9 | `fino` | FINO | ✅ | — |
| 10 | `nrfsi` | NRFSI | ✅ | — |
| 12 | `icici` | ICICI | ✅ | — |
| 13 | `bajaj` | Bajaj | ✅ | — |

Access any page publicly at `http://status.getkwikid.com:3001/status/<slug>`, and its data via `GET /api/status-page/<slug>` and `GET /api/status-page/heartbeat/<slug>` (no auth required for published pages).

> **Note**: The instance's default entry page is `dashboard` (the admin login), NOT a status page — so hitting the root URL redirects to `/dashboard`, not to a public page. Each client status page must be reached by its explicit slug. IDs 8 and 11 are absent (deleted pages).

---

---

## 1. Uptime Kuma UI Layout

Uptime Kuma has two distinct UI surfaces:

### 1.1 Admin Dashboard (authenticated)

Accessible at `http://status.getkwikid.com:3001/dashboard`

The admin dashboard provides:
- Full monitor list with live status indicators
- Real-time heartbeat log stream
- Monitor creation/edit/delete controls
- Notification channel management
- Status page management
- Settings panel (API key management, auth config)

**AI must never use this interface.** Read-only data is accessible via API.

### 1.2 Public Status Pages

Accessible at `http://status.getkwikid.com:3001/status/<slug>`

Public status pages show a curated subset of monitors. The page owner configures which monitors appear and how they are grouped. Multiple public status pages can exist on a single Uptime Kuma instance.

---

## 2. Status Page Structure

### 2.1 Status page object schema

```json
{
  "config": {
    "id": 1,
    "slug": "kwikid",
    "title": "KwikID Status",
    "description": "Current status of KwikID platform services",
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

### 2.2 Heartbeat object schema (from `/api/status-page/heartbeat/<slug>`)

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

**heartbeatList** is keyed by `monitorID` (as a string). Each array contains the most recent heartbeats for that monitor (default: last 50).

**uptimeList** is keyed by `<monitorID>_<hours>`. The two standard windows are:
- `_24` → last 24 hours uptime ratio (0.0–1.0)
- `_720` → last 720 hours (30 days) uptime ratio

---

## 3. Monitor Groups (Public Group List)

Within a status page, monitors are organized into **groups** (`publicGroupList`). Groups are ordered by `weight`. Each group has a name and contains an ordered list of monitors.

**Live grouping model**: This instance organizes by **client status page** rather than by service tier — each of the 11 status pages (global, bob, rbl, cbi, canarabank, unity-bank, fino, nrfsi, icici, bajaj, tatacap) curates that client's monitors. Additionally, two `type: group` container monitors exist in the admin view: `Saas client` (id 293) and `RBL Bank` (id 390). Within each client, monitors are informally grouped by the naming prefix convention (`[Server-Space]`, `[Memory-Space]`, `[Disk]`, `[CPU]`, `[Docker]`, `[health endpoint]`, service-API names).

---

## 4. Status Page Slugs

The `slug` determines the public URL: `http://status.getkwikid.com:3001/status/<slug>`

The entry page (default slug) is returned by:
```
GET /api/entry-page
→ { "slug": "<default-slug>", ... }
```

**Known slugs (live)**: `global`, `bob`, `rbl`, `tatacap`, `cbi`, `canarabank`, `unity-bank`, `fino`, `nrfsi`, `icici`, `bajaj` (11 published pages — see §0).  
**Default entry**: `GET /api/entry-page` returns `{"type":"entryPage","entryPage":"dashboard"}` — the root redirects to the admin login, NOT a public status page.

---

## 5. Incident Banner

Uptime Kuma supports a pinned incident banner on the status page. The `incident` field in the status page response is either `null` (no active incident) or:

```json
{
  "id": 12,
  "style": "danger",
  "title": "Platform Degradation",
  "content": "We are investigating elevated error rates on the KwikID API. Updates will follow.",
  "pin": true,
  "createdDate": "2026-07-10T09:30:00.000Z",
  "lastUpdatedDate": "2026-07-10T10:15:00.000Z"
}
```

**Incident styles**: `info` (blue), `warning` (yellow), `danger` (red), `primary` (blue), `light` (grey), `dark` (black)

The AI Investigation Layer should check `incident` field first — an active `danger`-style incident strongly indicates the root cause of tickets created at the same time.

---

## 6. Maintenance Windows

The `maintenanceList` field shows scheduled or active maintenance windows:

```json
[
  {
    "id": 3,
    "title": "Database Maintenance",
    "description": "Scheduled monthly database maintenance window",
    "strategy": "recurring-weekday",
    "range": [],
    "weekdays": [7],
    "daysOfMonth": [],
    "timeRange": [
      {"hours": 2, "minutes": 0},
      {"hours": 4, "minutes": 0}
    ],
    "timezoneOffset": 330,
    "timezone": "Asia/Kolkata",
    "cron": null,
    "active": true,
    "monitors": [1, 2, 5]
  }
]
```

**AI guidance**: If `maintenanceList` contains an active window covering the ticket creation time, the AI should include this in its investigation note — customer-reported issues during maintenance may be expected behavior.

---

## 7. Auto-Refresh and Real-Time Updates

The public status page auto-refreshes at `autoRefreshInterval` seconds (default: 60). The admin dashboard uses Socket.io for real-time push updates — status changes appear instantly without polling.

The AI should poll the REST API (not use Socket.io events) for investigation queries. Polling interval recommendation: on-demand per ticket, not continuous.

---

## 8. Accessing Live Dashboard Data (when network access is available)

```bash
# Get default slug
curl http://status.getkwikid.com:3001/api/entry-page

# Get full status page (replace 'kwikid' with actual slug)
curl http://status.getkwikid.com:3001/api/status-page/kwikid | jq .

# Get heartbeats (last 50 per monitor, plus uptime percentages)
curl http://status.getkwikid.com:3001/api/status-page/heartbeat/kwikid | jq .

# Parse monitor groups
curl http://status.getkwikid.com:3001/api/status-page/kwikid | \
  jq '.publicGroupList[].monitorList[] | {id, name, type}'

# Check active incident
curl http://status.getkwikid.com:3001/api/status-page/kwikid | \
  jq '.incident'

# Check maintenance windows
curl http://status.getkwikid.com:3001/api/status-page/kwikid | \
  jq '.maintenanceList'
```
