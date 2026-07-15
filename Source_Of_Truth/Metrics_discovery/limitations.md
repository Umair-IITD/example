# Limitations — What the AI Cannot Know from Monitoring Alone

**Platform**: Uptime Kuma **1.23.15** at `http://status.getkwikid.com:3001`  
**Purpose**: Defines blind spots, coverage gaps, and fallback strategies for the Investigation Layer  
**Updated**: 2026-07-11 with live-data coverage findings

---

## 0. Live Coverage Findings (2026-07-11)

Concrete gaps observed in the actual 332-monitor configuration:

- **69 monitors are paused (inactive)** — including **all 14 Tata Capital** monitors and **13 of 15 ICICI** monitors. The AI must treat these clients as *unmonitored*: absence of a DOWN signal there means nothing is being checked.
- **51 monitors read MAINTENANCE**, largely from routine nightly/weekend UAT shutdown windows. A MAINTENANCE status is not evidence of an outage.
- **Only 122 of 332 monitors report a response time** (`avgPing`). The 172 push monitors report liveness only (no latency), so response-time-based degradation detection covers under 40% of the fleet.
- **Version 1.23.15 lacks features** present in 2.x. Do not assume 2.x API behavior.
- **No public status page is the default entry** — the root URL redirects to the admin login. Public data must be pulled per-slug.

---

## 1. Platform-Level Limitations

### 1.1 Polling granularity — minimum interval is 60 seconds (by default)

Uptime Kuma checks monitors at configurable intervals. The default is 60 seconds. This means:

- A service that goes DOWN and recovers within 60 seconds may not be detected at all
- A service that fails for 30 seconds (user experiences error) → recovers → Uptime Kuma sees it as UP

**AI blind spot**: Transient outages shorter than the poll interval are invisible. A user who experienced a 20-second outage will open a ticket, but monitoring may show the service was UP the entire time.

**Fallback**: Check the Unity Admin API (Phase 2) for session-level error signals. Even if Uptime Kuma shows UP, session logs may show errors during that window.

### 1.2 Retry delay masks actual outage start time

With `maxretries: 3` and `retryInterval: 60s`, the first DOWN heartbeat is recorded 3 minutes after the actual failure. The actual outage start time is earlier than what Uptime Kuma records.

**AI guidance**: When correlating, use `down_heartbeat_time - (maxretries × retryInterval)` as the estimated actual outage start:

```python
estimated_actual_outage_start = down_heartbeat_time - timedelta(seconds=maxretries * retry_interval)
```

The `retries` field on the heartbeat indicates how many retries were made, confirming the delay.

### 1.3 No application-layer error detail

Uptime Kuma checks if a service returns a successful HTTP status code. It does NOT:
- Read the response body for error details (unless keyword monitoring is configured)
- Track which specific endpoints are failing within a service
- Capture request/response payloads
- Log stack traces or application errors

**AI blind spot**: A service returning `200 OK` with an error JSON body (`{"error": "database_connection_failed"}`) appears as UP to Uptime Kuma.

**Fallback**: Application-level logs (not currently integrated), Unity Admin API session errors (Phase 2).

### 1.4 No per-user or per-tenant granularity

Uptime Kuma monitors service endpoints, not user sessions. If Unity Bank's VKYC sessions are failing but only for users with a specific browser or OS, Uptime Kuma will show the service as UP.

**AI blind spot**: User-specific, tenant-specific, or browser-specific failures are invisible to monitoring.

**Fallback**: Session data from Unity Admin API (Phase 2) provides per-session, per-user context.

---

## 2. Data Availability Limitations

### 2.1 Heartbeat history window — last 50 heartbeats only (public API)

`/api/status-page/heartbeat/<slug>` returns the last 50 heartbeats per monitor. With a 60-second interval, this is approximately 50 minutes of history.

**AI blind spot**: Outages more than 50 minutes before the heartbeat query may not be in the public API response.

**Workaround**: For historical correlation (e.g., "this has been happening for 3 hours"), use Socket.io `getHeartbeatList` with a longer period — but this requires admin auth (not available to AI). Alternative: use the uptime percentage (`_24` / `_720`) as a proxy for historical health.

### 2.2 Monitors not on the public status page

The `/api/status-page/heartbeat/<slug>` endpoint only returns monitors that are **configured on the public status page**. Monitors that exist in Uptime Kuma but are not added to the public page are invisible via the public API.

**AI blind spot**: Internal monitors (e.g., internal DB health check not published to the status page) will not appear in heartbeat data.

**Workaround**: Use the `/metrics` Prometheus endpoint — it returns ALL monitors regardless of status page membership.

### 2.3 Paused monitors appear as PENDING or absent

When a monitor is paused (`active: false`), it stops making checks. It may appear as `status: 2 (PENDING)` or absent from the Prometheus output.

**AI guidance**: `PENDING` status on an existing monitor = monitor is paused. This is NOT a service outage. Check the `active` field in the monitor configuration.

### 2.4 Network unreachability of the monitoring platform itself

If `status.getkwikid.com:3001` is itself unreachable (platform down, network issue, maintenance), the AI receives no monitoring data. This is the current state (CCR egress policy).

**AI guidance**: Treat monitoring unavailability as neutral evidence (not negative). Continue investigation via other evidence sources (SOP, Unity Admin API). Note in private note: "Infrastructure status: unavailable at investigation time."

---

## 3. Structural Blind Spots

### 3.1 Third-party service failures

Uptime Kuma monitors KwikID's own endpoints. If a failure is caused by a third-party service (e.g., India Post's Aadhaar verification API, SMS gateway provider, bank's SFTP connection), Uptime Kuma will not have a monitor for it unless explicitly configured.

**Examples of unmonitored external dependencies**:
- UIDAI (Aadhaar) API
- NSDL / DigiLocker
- SMS gateway provider
- Individual bank integration APIs (unless configured as monitors)
- Email delivery service (SendGrid, SES)

**AI guidance**: If KwikID services show UP but customer reports identity verification failure, check external dependency availability separately. This is a gap in the current monitoring scope.

### 3.2 DNS resolution issues

If the KwikID platform's own DNS is resolving correctly, Uptime Kuma will reach the service and show UP. But if the customer's ISP or corporate DNS is failing to resolve `getkwikid.com`, the customer experiences failure while Uptime Kuma shows UP.

**AI blind spot**: DNS propagation delays, ISP-specific DNS failures, corporate firewall DNS blocks.

**Fallback**: Session data showing the user's connection attempt failing at the DNS level (Phase 2 investigation).

### 3.3 Geographic/CDN issues

If KwikID uses a CDN or multi-region setup, Uptime Kuma checks from the server location (AWS Mumbai). A user in another region experiencing CDN issues may encounter failures that Uptime Kuma (checking from Mumbai) does not detect.

**Current assessment**: KwikID appears single-region (AWS Mumbai, IP 15.206.10.140). CDN-related geographic blind spots are low risk unless CDN is added.

### 3.4 No deployment-time visibility

Uptime Kuma does not know when a deployment occurred. A service might be UP but behaving incorrectly after a code deployment. The spike in response time right after a deployment would be visible, but the root cause (deployment) is not.

**AI guidance**: If a ticket cluster appears after a quiet period, check Freshdesk ticket timestamps against any deployment notifications. Deployment correlation requires a deployment pipeline event feed (not currently integrated).

---

## 4. What the AI CANNOT Conclude from Monitoring Data Alone

Even with full monitoring access, these conclusions require additional evidence:

| Claim | Why monitoring cannot confirm | Required evidence |
|:---|:---|:---|
| "This is a user-specific issue" | Monitoring shows service-level health only | Unity Admin API session data |
| "The session ID XXX failed because of X" | No session-level data | Unity Admin API session logs |
| "This is a browser/OS compatibility issue" | No client-side data | User-provided device details |
| "The SOP fix will work for this issue" | Monitoring confirms outage; not the fix | Knowledge Base SOP match |
| "The issue started exactly at HH:MM" | Poll granularity and retry delay | Precise log timestamp |
| "This affected N users" | No user-count data | Freshdesk ticket cluster count + Unity Admin API |
| "The deployment at HH:MM caused this" | No deployment event data | CI/CD pipeline logs |
| "This is a bank-side issue" | No visibility into bank's internal systems | Bank escalation / admin confirmation |

---

## 5. Investigation Planner Fallback Logic

When monitoring data is unavailable or inconclusive:

```python
def determine_investigation_path(monitoring_evidence, ticket):
    
    if not monitoring_evidence.data_available:
        # Monitoring platform unreachable
        return InvestigationPath(
            skip_monitoring=True,
            note="Monitoring platform unavailable; proceeding with SOP and session evidence only",
            required_tools=["SOPTOOL"],  # At minimum
            phase2_tools=["UNITYTOOL"] if ticket.has_session_id else []
        )
    
    if monitoring_evidence.monitors_down_at_ticket_time:
        # Outage confirmed: infrastructure root cause
        return InvestigationPath(
            outage_confirmed=True,
            affected_monitors=monitoring_evidence.monitors_down_at_ticket_time,
            skip_deep_investigation=True,  # No need for session-level investigation
            reply_template="outage_acknowledgment",
            confidence_boost=0.30
        )
    
    if monitoring_evidence.all_monitors_up:
        # All clear: issue is not infrastructure-level
        return InvestigationPath(
            skip_monitoring=False,
            note="All services UP at ticket time; infrastructure not the root cause",
            required_tools=["SOPTOOL", "UNITYTOOL"],
            confidence_adjustment=0  # Neutral
        )
    
    # Inconclusive (PENDING monitors, partial data)
    return InvestigationPath(
        monitoring_inconclusive=True,
        note="Monitoring data inconclusive; proceeding with full investigation",
        required_tools=["SOPTOOL", "UNITYTOOL"]
    )
```

---

## 6. Monitoring Coverage Gaps to Address

| Gap | Impact | Recommendation |
|:---|:---|:---|
| No external dependency monitors | Cannot detect Aadhaar/SMS/DigiLocker failures | Add HTTP monitors for key external APIs |
| No per-tenant monitors | Cannot distinguish Unity-specific from platform-wide | Add tenant-specific endpoint monitors if they exist |
| No deployment event tracking | Cannot correlate outages with code changes | Integrate CI/CD webhook to Uptime Kuma maintenance window API |
| No end-user experience monitoring (RUM) | Cannot detect client-side failures | Add real-browser monitors for key user flows |
| Short public API history (~50 heartbeats) | Cannot investigate issues from hours ago | Use Socket.io `getHeartbeatList` with longer period, or expose Prometheus to Grafana for long-term retention |
