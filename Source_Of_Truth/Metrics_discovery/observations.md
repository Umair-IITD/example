# Observations — Findings, Gaps, and Recommendations

**Platform**: Uptime Kuma at `http://status.getkwikid.com:3001`  
**Audit**: Phase A Wave 2 Discovery — completed with full authenticated extraction 2026-07-11  
**Scope**: Architecture assessment, integration readiness, gap analysis, risk register  
**Live access status**: ✅ COMPLETE — 332 monitors, 11 status pages, 26 maintenance windows, 3 API keys, 2 notifications extracted

> **Headline findings (2026-07-11)**: Uptime Kuma 1.23.15 (container), 332 monitors across 13 bank clients, 211 UP / 51 MAINTENANCE / 1 DOWN. The single production DOWN is Bajaj's monitoring server (HTTP 500). Biggest risks: (1) 236 monitors embed credentials in config; (2) all 3 API keys are non-expiring; (3) weak admin password; (4) software is several releases behind (latest 2.2.1); (5) Tata Capital fully unmonitored (all paused), ICICI mostly paused.

---

## 1. Platform Assessment

### 1.1 What is well-designed

1. **Prometheus `/metrics` endpoint is ideal for AI integration.** Machine-readable, standardized format, authenticated endpoint. METRICTOOL can parse the full monitor inventory in a single HTTP call. This is better than screen-scraping or parsing HTML status pages.

2. **Public heartbeat API enables no-auth status correlation.** `/api/status-page/heartbeat/<slug>` returns timestamp-stamped heartbeats without authentication. The AI can correlate ticket creation time with DOWN events without needing admin credentials. Clean and appropriate separation of public and private data.

3. **`important` flag on status-transition heartbeats simplifies outage detection.** Rather than scanning all heartbeat data for state changes, the AI can filter for `important: true` records. This is an efficient pattern for incident reconstruction.

4. **`localDateTime` in IST saves conversion code.** The heartbeat object includes the check time in both UTC and IST. Since KwikID operations are IST-based, the `localDateTime` field can be used directly in private notes without timezone conversion.

5. **Maintenance window API is production-grade.** The `maintenanceList` in the status page response provides structured start/end times, recurring schedules, and per-monitor scope. The AI can definitively determine "was a maintenance window active at ticket creation time" without guessing.

### 1.2 What needs attention

1. **HTTP only on port 3001 (no TLS).** The API key is transmitted in plaintext. For a production integration in a financial services context, this is unacceptable. Adding an HTTPS reverse proxy is a 15-minute configuration change.

2. **Public API leaks monitor target URLs** if `sendUrl: true` is configured. Any service URL included in the status page response is publicly accessible to anyone who discovers the slug. Audit all monitors for `sendUrl` setting.

3. **50-heartbeat history window is very short** for incident investigation. A support ticket submitted 90 minutes after a 2-hour outage would not show the outage start in the public API response. This blind spot could cause the AI to miss historical context.

4. **No coverage of external dependencies.** If a bank's external KYC API fails, Uptime Kuma has no monitor for it. This is a systematic gap — the monitoring covers what KwikID controls but not what it depends on.

5. **Platform itself is a single point of failure.** Uptime Kuma runs on a single server (15.206.10.140). If that server goes down, all monitoring data is unavailable. The AI must handle this gracefully (it does via `safe_collect_monitoring_evidence`).

---

## 2. Integration Readiness

### 2.1 METRICTOOL readiness

| Requirement | Status | Notes |
|:---|:---:|:---|
| API endpoint exists | ✅ | `/metrics` — responds (401 with shared key) |
| Authentication mechanism | ⚠️ | Prometheus key `uk6_...` returns 401; Socket.io login (`root_user`) works |
| Response format | ✅ | Prometheus text and/or Socket.io JSON events |
| Network access from CCR | ✅ | `*.getkwikid.com` allowlisted 2026-07-10 |
| Real monitor names/IDs | ✅ | **332 monitors extracted** (service_inventory.md) |
| Response time baselines | ✅ | Snapshot captured (122 monitors); Uptime Kuma tracks 24h/30d uptime natively |
| Status page slug | ✅ | 11 published slugs identified |

**Readiness assessment**: Data path is READY via authenticated Socket.io. For a clean Prometheus pull, confirm the correct API key (likely `Support_Automation`).

### 2.2 SERVERTOOL readiness

| Requirement | Status | Notes |
|:---|:---:|:---|
| Public status page API | ✅ | 11 published pages; per-slug `/api/status-page/<slug>` works |
| Monitor groupings | ✅ | Per-client status pages + 2 group monitors + naming-prefix convention |
| Server component mapping | ✅ | 172 push monitors cover server/infra (disk, memory, CPU, docker, redis) |
| Maintenance window data | ✅ | 26 windows extracted (incident_system.md §0) |

**Readiness assessment**: READY. Full monitor, status-page, and maintenance data extracted; integration can proceed via Socket.io.

---

## 2.3 Live Discovery Summary (2026-07-11)

Full authenticated extraction against `http://status.getkwikid.com:3001` via Socket.io login (`root_user`):

| Observation | Detail |
|:---|:---|
| Version | Uptime Kuma 1.23.15 (container); upstream latest 2.2.1 |
| Monitors | 332 (263 active, 69 paused) |
| Status | 211 UP · 51 MAINTENANCE · 1 DOWN · 69 no-data |
| The 1 DOWN | id 432 `[Monitoring]Bajaj new prod monitoring server` — "Request failed with status code 500" |
| Status pages | 11 published (global + 10 per-client) |
| Maintenance windows | 26 (9 active, 9 scheduled, 8 ended) |
| Notifications | 2 (MS Teams default + Test Webhook) |
| API keys | 3 active, all non-expiring |
| Tags | 14 |
| Proxies / Docker hosts | 1 / 0 |
| Embedded credentials | 236 of 332 monitors carry secrets in config |
| Response time | 122 monitors report; p50 26 ms, p95 223 ms, max 1282 ms |

---

## 3. Gap Analysis

### 3.1 Discovery blockers — ALL RESOLVED ✅

| Gap | Status |
|:---|:---|
| Network access to `status.getkwikid.com:3001` | ✅ CLOSED — `*.getkwikid.com` allowlisted 2026-07-10 |
| Public status pages | ✅ CLOSED — 11 published pages found (were created since prior audit) |
| Admin credentials | ✅ CLOSED — `root_user` login verified 2026-07-11 |
| Monitor names/IDs | ✅ CLOSED — full 332-monitor inventory extracted (service_inventory.md) |

### 3.2 Remaining integration gaps (for Phase 2 deployment)

**GAP M-1: Prometheus `/metrics` key mismatch**
- **What**: The shared key `uk6_2w7…` returns 401 from `/metrics`. Instance has 3 hashed keys; cannot confirm match from extract.
- **Fix**: Test `/metrics` with the `Support_Automation` key, or mint a new key. Alternatively use the (proven) Socket.io path.
- **Who**: Umair / integration engineer

**GAP M-2: No webhook into the AI pipeline**
- **What**: Alerts go to MS Teams only; the sole webhook channel is "Test Webhook".
- **Fix**: Add a webhook notification pointing at n8n/FastAPI for proactive alerting.
- **Who**: Ops + integration engineer

**GAP M-3: Coverage holes**
- **What**: Tata Capital (14) fully paused; ICICI (13/15) paused; NRFSI UAT paused. These clients are effectively unmonitored.
- **Fix**: Confirm whether these are intentionally off-boarded; re-enable if still in service.
- **Who**: Ops team

**GAP M-4: Config hygiene**
- **What**: 236 monitors embed secrets; 3 non-expiring API keys; weak admin password; software several releases behind; inconsistent tag taxonomy.
- **Fix**: Rotate secrets, set key expiries, strengthen admin password, plan upgrade, normalize tags.
- **Who**: Security + ops

### 3.2 Non-blocking gaps (important for production quality)

**GAP M-4: No HTTPS on port 3001**
- Monitoring API key transmitted in plaintext
- Fix: HTTPS reverse proxy
- Effort: ~1 hour (nginx/Caddy config)

**GAP M-5: No external dependency monitors**
- UIDAI, SMS gateway, DigiLocker not monitored
- Fix: Add HTTP monitors for key external APIs
- Effort: 30 minutes per external service

**GAP M-6: Short public API heartbeat history (50 records)**
- Cannot investigate outages >50 minutes old via public API
- Fix: Expose Prometheus to Grafana for long-term retention, OR use Socket.io `getHeartbeatList` for historical queries
- Effort: Medium (Grafana setup or auth-aware Socket.io client)

**GAP M-7: Response time baselines not established**
- Cannot detect "elevated but not DOWN" degradation without baselines
- Fix: Collect 30 days of response time data, compute P50/P95 per monitor
- Effort: 30 days of collection + 1 hour of analysis

---

## 4. Risk Register

| Risk | Severity | Probability | Current State | Mitigation |
|:---|:---:|:---:|:---|:---|
| Monitoring platform unreachable at investigation time | 🟡 MEDIUM | Medium (single server) | Unmitigated | `safe_collect_monitoring_evidence` returns graceful fallback |
| API key exposed in HTTP transit | 🟡 MEDIUM | Low (internal network) | Confirmed (HTTP only on 3001) | Add HTTPS reverse proxy |
| Monitor name mapping gaps cause wrong relevance classification | 🟡 MEDIUM | Medium | UNKNOWN (no live data) | Conservative fallback: unknown monitor = neutral, not negative |
| Short history window misses historical outages in AI correlation | 🟡 MEDIUM | Medium (50-heartbeat limit) | Known limitation | Use uptime_24h percentage as proxy for long-term health |
| Transient outage (<60s) invisible to monitoring | 🟡 MEDIUM | High (transient failures common) | Known limitation | Phase 2: Unity Admin session logs supplement monitoring |
| Status page slug changes (URL changes break integration) | 🟠 LOW | Low | Unknown | Discover slug at runtime via `/api/entry-page`; cache with long TTL |
| API key rotation causes monitoring outage for AI | 🟠 LOW | Low | Unknown | Alert if `/metrics` returns 401; auto-fail-open |
| Monitoring data causes false positive outage correlation | 🟠 LOW | Low (requires wrong monitor mapping) | Unknown | Confidence boost from monitoring capped at 0.40 |

---

## 5. Architecture Quality Recommendations

### 5.1 Immediate (before Phase 2)

1. **Add domain to CCR egress** — 5 minutes; unblocks everything else
2. **Discover and document all monitor names** — 30 minutes after egress; populates MONITOR_RELEVANCE
3. **Establish slug and add to config** — 5 minutes; enables heartbeat API calls

### 5.2 Short-term (Phase 2 sprint)

1. **Enable HTTPS on port 3001** or put behind nginx HTTPS proxy
2. **Build MONITOR_RELEVANCE mapping** based on discovered monitor names
3. **Establish response time baselines** (30-day rolling averages)
4. **Test monitoring correlation end-to-end** with a simulated DOWN event

### 5.3 Medium-term (Phase 3-4)

1. **Add external dependency monitors** (UIDAI, SMS gateway, key bank APIs)
2. **Implement Grafana integration** for long-term metric retention and dashboards
3. **Add per-tenant monitors** if tenant-specific endpoints exist
4. **Integrate deployment events** with Uptime Kuma maintenance windows via API

### 5.4 Long-term (Phase 5+)

1. **Real-browser monitoring** for end-to-end user journey tests (VKYC session creation, document upload)
2. **Synthetic transaction monitoring** — simulate Unity Bank user login every 5 minutes
3. **Anomaly detection** on response time trends (ML-based, not threshold-based)
4. **Alert-to-ticket pipeline** — Uptime Kuma webhook → auto-create Freshdesk ticket for confirmed outages
