# Alerting — Notification Model and Alert Channels

**Platform**: Uptime Kuma **1.23.15** at `http://status.getkwikid.com:3001`  
**Source**: Live authenticated extraction 2026-07-11 (`notificationList` event) + Uptime Kuma alerting architecture

---

## 0. Live Notification Channels (2 configured)

| ID | Name | Type | Default? | Applies to |
|:--|:--|:--|:--|:--|
| 3 | Uptime Alert- Legacy | **Microsoft Teams** | ✅ default | Attached to monitors via `notificationIDList` |
| 8 | Test Webhook | **Webhook** | ✅ default | Generic webhook POST |

Both channels are marked default, so newly created monitors get both unless overridden. The primary production alert path is the **Microsoft Teams** channel ("Uptime Alert- Legacy"). The webhook channel is named "Test Webhook" — treat as non-production / experimental until confirmed.

> **Integration implication**: There is currently **no dedicated webhook into the KwikID AI pipeline** (n8n/FastAPI). Alerts flow to MS Teams. If the Support Automation platform wants proactive push (rather than polling `/metrics` or Socket.io), a new webhook notification pointing at the AI ingest endpoint should be added — see [integration_guidelines.md](./integration_guidelines.md).

---

---

## 1. Uptime Kuma Alert Model

Uptime Kuma fires notifications on **status transitions** (`important: true` heartbeats). Notifications are NOT fired on every failed check — only when the service transitions between states.

### 1.1 Alert triggers

| Event | Condition | Notification fired |
|:---|:---|:---|
| Service goes DOWN | status transitions from 1→0 (after `maxretries` failures) | ✅ Yes |
| Service recovers | status transitions from 0→1 | ✅ Yes (if configured) |
| SSL cert expiry warning | `cert_days_remaining < threshold` | ✅ Yes (if configured) |
| Maintenance window start | Schedule activates | ✅ Yes (if configured) |
| Maintenance window end | Schedule ends | ✅ Yes (if configured) |
| Resend notification | Monitor stays DOWN for `resendInterval` seconds | ✅ Yes (if `resendInterval > 0`) |

### 1.2 Alert delay (maxretries)

By default, Uptime Kuma tries `maxretries` times before declaring DOWN and firing the notification. This prevents false positives from transient network blips.

With `maxretries: 3` and `retryInterval: 60s`:
- Check 1 fails at T+0
- Retry 1 fails at T+60
- Retry 2 fails at T+120
- Retry 3 fails at T+180 → DOWN declared, notification sent

**Total alert delay**: `maxretries × retryInterval` seconds (up to 3+ minutes before notification)

This is important for AI correlation: the DOWN notification may arrive **after** the Freshdesk ticket is already created. Always check heartbeat history, not just current notification timing.

---

## 2. Notification Channels

Uptime Kuma supports 90+ notification providers. **This instance has exactly 2 configured** (live-verified): a Microsoft Teams channel and a webhook channel (see §0). The table below records what each does for AI correlation.

### 2.1 Configured channels (live)

| Channel | Type | Purpose | AI relevance |
|:---|:---|:---|:---|
| **Uptime Alert- Legacy** | Microsoft Teams | Posts DOWN/UP transitions to the ops Teams channel | Teams message timestamps may pre-date Freshdesk tickets — usable for correlation if Teams is connected |
| **Test Webhook** | Webhook | POST to a custom endpoint (test/experimental) | If re-pointed at n8n/FastAPI, AI could receive proactive alerts — not currently wired to the pipeline |

### 2.2 Webhook notification payload (if webhook channel is configured)

When Uptime Kuma fires a webhook notification, the payload is:

```json
{
  "heartbeat": {
    "monitorID": 3,
    "status": 0,
    "time": "2026-07-10 09:23:00.000",
    "msg": "TIMEOUT",
    "ping": null,
    "important": true,
    "duration": 60,
    "localDateTime": "2026-07-10 14:53:00.000",
    "timezone": "Asia/Kolkata",
    "retries": 3,
    "downCount": 1
  },
  "monitor": {
    "id": 3,
    "name": "KwikID VKYC Service",
    "url": "https://vkyc.getkwikid.com/health",
    "hostname": null,
    "port": null,
    "maxretries": 3,
    "weight": 2000,
    "active": true,
    "type": "http",
    "interval": 60,
    "retryInterval": 60,
    "resendInterval": 0,
    "keyword": null,
    "invertKeyword": false,
    "acceptedStatuscodes": ["200-299"],
    "dns_resolve_type": "A",
    "dns_resolve_server": "1.1.1.1",
    "dns_last_result": null,
    "docker_container": null,
    "docker_host": null,
    "proxyId": null,
    "notificationIDList": {},
    "tags": []
  },
  "msg": "KwikID VKYC Service went down.",
  "title": "Uptime Kuma Alert: KwikID VKYC Service",
  "type": 0
}
```

`type`: `0` = DOWN alert, `1` = UP/recovery alert

---

## 3. Notification Configuration Schema

Each notification channel in Uptime Kuma has this base structure:

```json
{
  "id": 1,
  "name": "Ops Slack Alert",
  "type": "slack",
  "isDefault": true,
  "applyExisting": false,
  "active": true
}
```

Channel-specific config fields are stored in a `config` object but are NOT exposed by the public API (only via authenticated Socket.io `getNotificationList`). **The AI must never read or expose notification configs** — they contain webhook secrets, Slack tokens, email credentials.

---

## 4. Alert-to-Ticket Correlation

### 4.1 The gap between monitoring alert and ticket creation

Uptime Kuma detects DOWN after `maxretries × retryInterval` seconds. Users typically open a Freshdesk ticket 5–30 minutes after experiencing an issue. This creates a temporal pattern:

```
T+0     Service actually fails
T+180   Uptime Kuma declares DOWN (after 3 retries × 60s)
T+180   Alert fires to Slack / webhook
T+240   First user opens Freshdesk ticket (4 minutes after DOWN declared)
T+300   Second user opens Freshdesk ticket
T+420   Third user opens Freshdesk ticket (ticket cluster begins)
```

**Ticket cluster = infrastructure outage signal**: When multiple tickets arrive within a 5–15 minute window with similar symptoms, check if a monitor went DOWN ~3–15 minutes before the first ticket.

### 4.2 Proactive alert integration (future architecture)

If Uptime Kuma is configured with a **webhook notification** pointing to the KwikID platform, the AI could receive DOWN events before any Freshdesk ticket is created. This enables:

1. Pre-emptive ticket creation for known outages
2. Auto-tagging incoming Freshdesk tickets with related outage
3. Batch-reply to all affected tickets when recovery occurs

**Current state**: UNKNOWN — whether a webhook notification exists pointing to the KwikID platform is not known without live access to the notification list.

---

## 5. Alert Thresholds

### 5.1 Response time thresholds (Uptime Kuma native)

Uptime Kuma does NOT natively alert on elevated response time alone. It only fires DOWN alerts (status code failure / timeout). Response time monitoring is **observational only** in Uptime Kuma.

For response time alerting, options are:
1. Use a **keyword monitor** with a response-time-based assertion
2. Export to Prometheus and set Prometheus alerting rules
3. Use Grafana (if integrated) for response time threshold alerts

### 5.2 AI-defined alert thresholds (for investigation)

These are not Uptime Kuma native settings — they are AI-defined thresholds used during investigation:

| Metric | Warning threshold | Critical threshold | Source |
|:---|:---|:---|:---|
| Response time | >500ms | >2000ms | Application-dependent; refine with baselines |
| Uptime (24h) | <99.5% | <95% | SLA-derived |
| SSL cert days remaining | <30 days | <7 days | Security policy |
| Consecutive DOWN checks | 2 | 5 | Monitor config dependent |

---

## 6. Configured Alerts on This Instance

**UNKNOWN** — requires admin access to Socket.io `getNotificationList` event to enumerate configured channels.

Verification steps when access is available:
```bash
# Socket.io (requires admin credentials — AI should NOT use this)
# socket.emit('getNotificationList') → returns all notification configs
# DO NOT EXPOSE credentials or notification secrets

# Alternative: check /metrics output for monitor labels
# Monitor names in /metrics may hint at notification groupings (tags)
curl -H "Authorization: Bearer uk6_2w7DIcrXJd0y5g7bIJ_0flqk6vV_B6FRZLsEkyr3" \
  http://status.getkwikid.com:3001/metrics | grep monitor_name
```
