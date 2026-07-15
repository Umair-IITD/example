# Integration Guidelines — Uptime Kuma → KwikID AI Platform

**Platform**: Uptime Kuma **1.23.15** at `http://status.getkwikid.com:3001`  
**Target system**: KwikID Support Automation Platform (Evidence Collector, METRICTOOL, SERVERTOOL)  
**Updated**: 2026-07-11 with live integration findings

---

## 0. Verified Integration Path (2026-07-11)

Based on live testing, here is what actually works today:

| Route | Status | Recommendation |
|:---|:---|:---|
| **Socket.io authenticated** (`login` → `monitorList`/`heartbeatList`/…) | ✅ **Works** — used for this extraction | **Primary integration path.** Use a maintained client library (e.g. `uptime-kuma-api` for Python, or `socket.io-client`). Store `root_user` creds in a secret manager. |
| **Prometheus `/metrics`** (Bearer API key) | ⚠️ Returned 401 with shared key | Confirm which of the 3 API keys is valid (likely `Support_Automation`), or mint a new key, then this becomes a clean read-only pull for METRICTOOL. |
| **Public status-page API** (`/api/status-page/<slug>`) | ✅ Works per published slug | Good no-auth fallback for per-client health; 11 slugs available (`bob`, `rbl`, `cbi`, …). |
| **Webhook push into AI pipeline** | ❌ Not configured | Only MS Teams + a "Test Webhook" exist. Add a webhook notification → n8n/FastAPI for proactive alerting. |

> **Recommended architecture**: METRICTOOL/SERVERTOOL authenticate once via Socket.io, cache the `monitorList` (332 monitors) and subscribe to live heartbeat pushes, rather than polling `/metrics`. Parse the structured monitor names to resolve ticket → client → environment → service.

---

---

## 1. Integration Architecture

The integration is **read-only**, **on-demand**, and **non-blocking**. The AI evidence collection call to Uptime Kuma must not delay the Freshdesk webhook response (which must return 200 OK within 10 seconds).

```
Freshdesk webhook arrives
    │
    ▼
Webhook Receiver → return 200 OK immediately
    │
    └── spawn background task
            │
            ▼
    Investigation Planner → should monitoring be checked?
            │
            ├── YES → parallel fetch:
            │         METRICTOOL: /metrics + /heartbeat/<slug>
            │         SERVERTOOL: /status-page/<slug>
            │         Both run concurrently (asyncio.gather)
            │
            ├── NO → skip monitoring; proceed with SOPTOOL only
            │
            └── TIMEOUT → if either tool exceeds 5 seconds:
                          log warning, continue without monitoring data
                          mark evidence as "monitoring_unavailable"
```

---

## 2. HTTP Client Configuration

```python
# Recommended httpx client configuration for Uptime Kuma calls
import httpx

UPTIME_KUMA_CLIENT_CONFIG = {
    "base_url": "http://status.getkwikid.com:3001",
    "timeout": httpx.Timeout(
        connect=3.0,    # 3s to establish connection
        read=5.0,       # 5s to read response
        write=3.0,
        pool=3.0
    ),
    "headers": {
        "User-Agent": "KwikID-AI-Support-Agent/1.0"
    },
    "follow_redirects": True,
    "http2": False    # Uptime Kuma does not require HTTP/2
}

UPTIME_KUMA_AUTH_HEADERS = {
    "Authorization": f"Bearer uk6_2w7DIcrXJd0y5g7bIJ_0flqk6vV_B6FRZLsEkyr3"
}
```

---

## 3. Caching Strategy

Monitoring data changes at most every `interval` seconds (typically 60s). There is no value in re-fetching more frequently.

### 3.1 Cache TTL recommendations

| Endpoint | Cache TTL | Rationale |
|:---|:---|:---|
| `/metrics` (Prometheus) | 30 seconds | Updated every monitor check interval |
| `/api/status-page/heartbeat/<slug>` | 30 seconds | New heartbeat every 60s; 30s catches fast recoveries |
| `/api/status-page/<slug>` | 60 seconds | Status page config changes rarely |
| `/api/entry-page` | 300 seconds | Slug changes very rarely |

### 3.2 Cache implementation pattern

```python
from functools import lru_cache
from datetime import datetime, timedelta
import asyncio

class CachedUptimeKumaClient:
    def __init__(self):
        self._cache: dict[str, tuple[any, datetime]] = {}
        self._client = UptimeKumaClient()
    
    async def get_with_cache(self, key: str, fetch_fn, ttl_seconds: int):
        now = datetime.utcnow()
        if key in self._cache:
            data, cached_at = self._cache[key]
            if (now - cached_at).total_seconds() < ttl_seconds:
                return data
        
        data = await fetch_fn()
        self._cache[key] = (data, now)
        return data
    
    async def get_prometheus_metrics(self):
        return await self.get_with_cache(
            'prometheus',
            self._client.get_prometheus_metrics_async,
            ttl_seconds=30
        )
    
    async def get_heartbeats(self, slug: str):
        return await self.get_with_cache(
            f'heartbeat_{slug}',
            lambda: self._client.get_heartbeats_async(slug),
            ttl_seconds=30
        )
```

**Important**: Cache is per-process. If the AI platform runs multiple workers, each worker has an independent cache. This is acceptable — the data is the same within the 30s TTL, and cache divergence across workers is benign.

---

## 4. Error Handling and Fallback

### 4.1 Error types and responses

```python
async def safe_collect_monitoring_evidence(ticket_created_at, slug):
    """
    Always returns a MonitoringEvidence object.
    Never raises — failures are captured as evidence.data_available=False.
    """
    try:
        return await collect_monitoring_evidence(ticket_created_at, slug)
    
    except httpx.ConnectError:
        # Network unreachable or connection refused
        return MonitoringEvidence(
            data_available=False,
            error="monitoring_connect_error",
            error_detail="Cannot connect to status.getkwikid.com:3001"
        )
    
    except httpx.TimeoutException:
        # Took longer than configured timeout
        return MonitoringEvidence(
            data_available=False,
            error="monitoring_timeout",
            error_detail="Uptime Kuma response exceeded 5s timeout"
        )
    
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return MonitoringEvidence(
                data_available=False,
                error="monitoring_auth_error",
                error_detail="API key rejected by /metrics endpoint"
            )
        return MonitoringEvidence(
            data_available=False,
            error=f"monitoring_http_{e.response.status_code}",
            error_detail=str(e)
        )
    
    except Exception as e:
        return MonitoringEvidence(
            data_available=False,
            error="monitoring_unexpected_error",
            error_detail=str(e)
        )
```

### 4.2 Graceful degradation

When monitoring data is unavailable, the AI investigation continues without it. The Observation Generator uses a fallback note:

```html
<p><strong>⚪ Infrastructure Status: Unavailable</strong><br>
Monitoring platform (status.getkwikid.com:3001) was not reachable at investigation time.<br>
Error: {error_detail}<br>
Root cause analysis proceeds without infrastructure correlation.</p>
```

The absence of monitoring data does NOT:
- Block the investigation
- Reduce confidence score (it is treated as neutral, not negative)
- Prevent autonomous replies if SOP confidence is HIGH

The absence of monitoring data DOES:
- Prevent HIGH confidence RCA for infrastructure outages
- Require manual verification if the ticket symptom strongly suggests an outage

---

## 5. Timestamp Handling

Uptime Kuma stores times in UTC. The heartbeat object provides both UTC (`time`) and IST (`localDateTime`). KwikID operations are IST-based.

### 5.1 Time conversion rules

| Field in heartbeat | Format | Timezone | AI use |
|:---|:---|:---|:---|
| `time` | `"YYYY-MM-DD HH:mm:ss.SSS"` | UTC | Use for comparison with Freshdesk `created_at` (also UTC) |
| `localDateTime` | `"YYYY-MM-DD HH:mm:ss.SSS"` | IST (UTC+5:30) | Use in Observation Generator private notes (human-readable) |
| `timezone` | `"Asia/Kolkata"` | — | Confirms localDateTime timezone |

### 5.2 Freshdesk ticket timestamp format

Freshdesk `created_at` is ISO 8601 UTC: `"2026-07-10T09:23:45Z"`

Convert both to datetime objects for comparison:

```python
from datetime import datetime, timezone

def parse_freshdesk_timestamp(ts: str) -> datetime:
    """Parse Freshdesk ISO 8601 UTC timestamp."""
    return datetime.fromisoformat(ts.replace('Z', '+00:00'))

def parse_uptime_kuma_timestamp(ts: str) -> datetime:
    """Parse Uptime Kuma UTC timestamp string."""
    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S.%f")
    return dt.replace(tzinfo=timezone.utc)

def utc_to_ist_display(dt: datetime) -> str:
    """Convert UTC datetime to IST display string for notes."""
    from zoneinfo import ZoneInfo
    ist = dt.astimezone(ZoneInfo("Asia/Kolkata"))
    return ist.strftime("%Y-%m-%d %H:%M:%S IST")
```

---

## 6. Parallel Tool Execution

METRICTOOL and SERVERTOOL should run concurrently, not sequentially:

```python
async def run_evidence_collection(ticket, tenant_context):
    # Define all tool tasks
    tasks = {
        'monitoring': safe_collect_monitoring_evidence(
            ticket.created_at, slug="kwikid"
        ),
        'sop': collect_sop_evidence(ticket.description, tenant_context),
        # Phase 2: add Unity API tool calls here
    }
    
    # Run ALL tools in parallel
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    
    # Map results back to tool names
    evidence = {}
    for tool_name, result in zip(tasks.keys(), results):
        if isinstance(result, Exception):
            evidence[tool_name] = {'data_available': False, 'error': str(result)}
        else:
            evidence[tool_name] = result
    
    return evidence
```

---

## 7. Monitor Name → Tenant/Service Mapping

When monitoring evidence contains a DOWN monitor, the AI must determine if it is relevant to the current ticket's tenant and symptom.

### 7.1 Mapping strategy

Build a mapping table (populated after live access to get real monitor names):

```python
# To be populated after live access to status.getkwikid.com:3001
MONITOR_RELEVANCE = {
    # "monitor_name": {"tenant": [...], "symptom_keywords": [...]}
    "KwikID API": {
        "tenant": ["ALL"],  # Affects all tenants
        "symptom_keywords": ["api", "error", "not working", "request failed"]
    },
    "KwikID VKYC Service": {
        "tenant": ["ALL"],
        "symptom_keywords": ["vkyc", "video", "kyc", "session", "auditor", "not visible"]
    },
    "Unity Bank Integration": {
        "tenant": ["UNITY"],
        "symptom_keywords": ["unity", "bank", "integration", "connection"]
    },
    # UNKNOWN — add real monitor names after live access
}

def is_monitor_relevant(monitor_name, tenant_id, symptom_keywords):
    config = MONITOR_RELEVANCE.get(monitor_name)
    if not config:
        return False  # Unknown monitor — cannot determine relevance
    
    tenant_match = "ALL" in config["tenant"] or tenant_id in config["tenant"]
    keyword_match = any(kw in symptom_keywords for kw in config["symptom_keywords"])
    
    return tenant_match and (keyword_match or not symptom_keywords)
```

---

## 8. Integration Checklist

### Before Phase 2 deployment (METRICTOOL)

- [ ] Add `status.getkwikid.com` to CCR network egress allowlist
- [ ] Verify `/metrics` endpoint accessible with API key
- [ ] Verify `/api/status-page/heartbeat/<slug>` returns data (discover actual slug)
- [ ] Populate `MONITOR_RELEVANCE` mapping with real monitor names from live data
- [ ] Establish response time baselines (30-day mean ping per monitor)
- [ ] Test `safe_collect_monitoring_evidence` in isolation with real data
- [ ] Confirm monitoring calls complete within 5s timeout (measure with real network)
- [ ] Add monitoring evidence to Observation Generator note template
- [ ] Test correlation: create test Freshdesk ticket at same time a monitor is paused/resumed

### Before Phase 3 deployment (SERVERTOOL)

- [ ] Identify which monitors represent "server-level" components (DB, API server, queue)
- [ ] Map monitor names → infrastructure component types
- [ ] Implement `ServerToolOutput` with component-level breakdown
- [ ] Integrate SERVERTOOL output into Root Cause Engine cascade failure detection
- [ ] Test: pause DB monitor → create ticket → verify SERVERTOOL detects and reports correctly
