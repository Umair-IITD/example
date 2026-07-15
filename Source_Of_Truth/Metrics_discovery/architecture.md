# Monitoring Platform Architecture

**Platform**: Uptime Kuma **1.23.15** (self-hosted, container deployment)  
**Instance**: `http://status.getkwikid.com:3001`  
**Server**: AWS Mumbai (ap-south-1), IP `15.206.10.140`  
**Source**: Live instance access 2026-07-10 — unauthenticated discovery complete; authenticated fields require admin credentials

---

## 1. Platform Overview

Uptime Kuma is an open-source, self-hosted website and service monitoring tool. It provides:

- **HTTP(S)/TCP/DNS/Ping/Push/Docker/Database monitoring** with configurable intervals
- **Public status pages** served at configurable slugs
- **Real-time notifications** via dozens of notification channels (Slack, email, webhook, Telegram, PagerDuty, etc.)
- **Prometheus metrics endpoint** for scraping by Prometheus/Grafana
- **Socket.io-based real-time API** for authenticated management operations
- **Public REST API** for status pages and heartbeat data (no auth required)

Uptime Kuma uses a **SQLite** database for persistence (single-file, embedded), making it lightweight and portable. All state — monitors, heartbeat history, notification configs, status pages — lives in this database.

---

## 2. Deployment Topology

```
┌─────────────────────────────────────────────────────────┐
│  AWS Mumbai (ap-south-1)                                │
│  IP: 15.206.10.140                                      │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │  Uptime Kuma Process (Node.js)                  │   │
│  │                                                 │   │
│  │  Port 3001 (HTTP — TLS not active on this port) │   │
│  │                                                 │   │
│  │  ┌──────────────┐  ┌───────────────────┐        │   │
│  │  │  REST API    │  │   Socket.io API   │        │   │
│  │  │  (public +   │  │   (authenticated) │        │   │
│  │  │  auth'd)     │  │                   │        │   │
│  │  └──────────────┘  └───────────────────┘        │   │
│  │                                                 │   │
│  │  ┌──────────────────────────────────────────┐   │   │
│  │  │  SQLite Database (persistent)            │   │   │
│  │  │  monitors, heartbeats, incidents,        │   │   │
│  │  │  notifications, status pages, settings   │   │   │
│  │  └──────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘

External consumers:
  ┌─────────────────────┐     ┌──────────────────────────┐
  │  Public status page  │     │  KwikID AI Platform       │
  │  status.getkwikid   │     │  METRICTOOL + SERVERTOOL  │
  │  .com:3001          │     │  (Evidence Collector)     │
  └─────────────────────┘     └──────────────────────────┘
```

---

## 3. Server Specifications

| Property | Value |
|:---|:---|
| Cloud provider | AWS |
| Region | ap-south-1 (Mumbai) |
| Public IP | `15.206.10.140` |
| Port | `3001` |
| Protocol | HTTP (not HTTPS on this port) |
| TLS on port 3001 | Not active (TLS handshake rejected) |
| Runtime | Node.js |
| Database | SQLite (single-file embedded) |
| Deployment | **Container** (`isContainer: true`) — Docker/containerized |
| Uptime Kuma version | **1.23.15** — confirmed live via authenticated `info` event |
| Upstream latest version | 2.2.1 (instance is several releases behind) |
| Server timezone | Asia/Calcutta (UTC+05:30) — **confirmed live** via `info` Socket.io event |
| Primary base URL | `null` (not configured) — **confirmed live** via `info` Socket.io event |
| X-Frame-Options | `SAMEORIGIN` — **confirmed live** from HTTP response header |
| Entry page | `dashboard` — **confirmed live** (`GET /api/entry-page` → `{"type":"entryPage","entryPage":"dashboard"}`) |
| Public status pages | **11 published** (global + 10 per-client) — confirmed via authenticated `statusPageList` |
| Total monitors | **332** (263 active) |
| Configured proxies | 1 (CBI vKYC HTTPS proxy, `cbi.vkyc.getkwikid.com:3376`) |
| Docker hosts registered | 0 |

The full `info` event payload (live):
```json
{
  "version": "1.23.15",
  "latestVersion": "2.2.1",
  "isContainer": true,
  "primaryBaseURL": null,
  "serverTimezone": "Asia/Calcutta",
  "serverTimezoneOffset": "+05:30"
}
```

---

## 3.5 Tag Taxonomy (live)

The instance defines **14 tags** used to classify monitors by application, client, environment, team, and infra dimension:

| ID | Tag | Color | Usage count |
|:--|:--|:--|:--|
| 3 | env | blue | 99 |
| 1 | app | grey | 68 |
| 2 | client | green | 56 |
| 4 | team | red | 25 |
| 10 | start | green | 24 |
| 11 | stop | red | 24 |
| 9 | ip | amber | 15 |
| 8 | server | indigo | 9 |
| 6 | client: rbl | green | 9 |
| 7 | app: server-mem | grey | 4 |
| 13 | CPU-utiliation *(sic)* | violet | 2 |
| 14 | Scylla-DB | amber | 1 |
| 5 | env: uat | blue | (scoped) |
| 12 | note | amber | (annotation) |

Tags `start`/`stop` appear to mark monitors tied to scheduled start/stop automation. The taxonomy is inconsistent (e.g. `CPU-utiliation` is misspelled, and both flat `client` and scoped `client: rbl` styles coexist) — worth normalizing.

---

## 4. API Architecture

Uptime Kuma exposes three API surfaces:

### 4.1 REST API — Public (no auth required)

Used by status pages and external consumers without authentication.

| Endpoint | Purpose | Auth |
|:---|:---|:---|
| `GET /api/entry-page` | Returns default slug for the entry status page | None |
| `GET /api/status-page/<slug>` | Full status page data: monitors, incidents, config | None |
| `GET /api/status-page/heartbeat/<slug>` | Recent heartbeat data for all monitors on the page | None |
| `GET /api/badge/<id>/status` | SVG badge showing current monitor status | None |
| `GET /api/badge/<id>/uptime/<duration>` | SVG badge showing uptime percentage | None |
| `GET /api/badge/<id>/ping/<duration>` | SVG badge showing average ping | None |
| `POST /api/push/<token>` | Push monitor heartbeat (external push check) | Push token |

### 4.2 REST API — Authenticated

| Endpoint | Purpose | Auth |
|:---|:---|:---|
| `GET /metrics` | Prometheus exposition format metrics for all monitors | API key (Bearer or Basic) |

### 4.3 Socket.io API — Authenticated

Real-time bidirectional API. Requires authentication via `login` event before any management operations.

**Authentication**: `socket.emit('login', { username, password, token })` → server responds with `loginResult`.

**Read events** (safe for the AI investigation layer):
- `getMonitorList` → full list of all monitors with all fields
- `getHeartbeatList` → heartbeat history for a specific monitor
- `getImportantHeartbeatList` → only status-change events for a monitor
- `getMonitor` → single monitor details
- `getStatusPage` → status page configuration
- `getIncidentList` → all incidents for a monitor
- `getTags` → tag list
- `getSettings` → instance settings

**Write events** (NEVER used by AI):
- `addMonitor`, `editMonitor`, `deleteMonitor`
- `addNotification`, `editNotification`, `deleteNotification`
- `addStatusPage`, `editStatusPage`, `deleteStatusPage`
- `postIncident`, `unpinIncident`
- `setSettings`, `changePassword`
- `resumeMonitor`, `pauseMonitor`

---

## 5. Data Persistence Model

Uptime Kuma uses SQLite. Data is written to a local database file (typically `data/kuma.db`). There is no external database dependency.

**Key tables and their AI relevance**:

| Table | Contains | AI use |
|:---|:---|:---|
| `monitor` | All monitor configurations | Read via Socket.io `getMonitorList` |
| `heartbeat` | Full heartbeat history (status, ping, time) | Read via `/api/status-page/heartbeat/<slug>` or `getHeartbeatList` |
| `incident` | Created incidents with title/content/style | Read via `getIncidentList` |
| `notification` | Notification channel configs (webhook URLs, etc.) | Not used by AI |
| `status_page` | Public status page configurations | Read via `/api/status-page/<slug>` |
| `monitor_tag` | Tag-to-monitor mappings | Context for grouping |
| `tag` | Tag definitions | Read via `getTags` |

---

## 6. Authentication Model

### 6.1 API key authentication (Prometheus metrics endpoint)

The API key `uk6_2w7DIcrXJd0y5g7bIJ_0flqk6vV_B6FRZLsEkyr3` is used for the `/metrics` endpoint.

Format: `uk<version>_<random-string>`

Accepted in two forms:
```
Authorization: Bearer uk6_2w7DIcrXJd0y5g7bIJ_0flqk6vV_B6FRZLsEkyr3
```
```
Authorization: Basic <base64(apikey:)>
```

### 6.2 Socket.io authentication (management API)

Socket.io requires interactive `login` event with username + password. **The AI should NOT use Socket.io management operations** — this requires storing admin credentials. Use REST + Prometheus for all AI needs.

### 6.3 Public endpoints (no auth)

`/api/status-page/<slug>` and `/api/status-page/heartbeat/<slug>` require no authentication. These provide sufficient data for the AI investigation layer without needing credentials.

---

## 7. How Uptime Kuma Fits into KwikID

In the KwikID Support Automation Platform, Uptime Kuma serves as the **external infrastructure observability layer** that the AI Investigation Layer consults when diagnosing support tickets.

```
Freshdesk ticket created
        │
        ▼
Webhook Receiver → Client Resolver → Ticket Orchestrator
        │
        ▼
Support Agent Runtime → Workflow Engine → Investigation Planner
        │
        ├─── METRICTOOL ──────────────────────────────────────────┐
        │    Queries Uptime Kuma                                  │
        │    ├── /api/status-page/heartbeat/<slug>                │
        │    │   (response times, UP/DOWN status, recent pings)   │
        │    └── /metrics (Prometheus: all monitors live status)  │
        │                                                         │
        ├─── SERVERTOOL ──────────────────────────────────────────┤
        │    Queries Uptime Kuma for server-level health          │
        │    ├── /api/status-page/<slug> (service groupings)      │
        │    └── Socket.io: getMonitorList (full monitor details) │
        │                                                         │
        ▼                                                         │
Evidence Collector ◄─────────────────────────────────────────────┘
        │
        ▼
Root Cause Engine ── correlates ticket symptom vs. monitor status
        │
        ▼
Reasoning Engine ── builds RCA hypothesis ("backend DOWN since 14:23")
        │
        ▼
Observation Generator ── writes private note with monitoring evidence
```

### 7.1 METRICTOOL responsibilities

- Query current UP/DOWN/PENDING/MAINTENANCE status of all relevant monitors
- Retrieve response time time-series for correlation with ticket creation time
- Retrieve uptime percentages for SLA context
- Identify monitors that were DOWN at the time the ticket was created

### 7.2 SERVERTOOL responsibilities

- Retrieve server-level health metrics (infrastructure monitors: database, API server, job queue)
- Identify if the KwikID platform components themselves are degraded
- Provide server response time trends to distinguish slow API from complete outage

---

## 8. Integration Rollout Phases (Monitoring)

| Phase | Scope | Status |
|:---|:---|:---|
| Phase 1 (Sprint 2.28) | SOP-only investigation; no monitoring integration yet | Current |
| Phase 2 | METRICTOOL added: `/metrics` Prometheus scrape at ticket creation time | Next |
| Phase 3 | SERVERTOOL added: server-level health correlation with ticket symptoms | Planned |
| Phase 4 | Historical heartbeat correlation: check status at the exact time of ticket creation | Planned |
| Phase 5 | Incident correlation: match Uptime Kuma incidents to Freshdesk ticket clusters | Planned |
