# Security Review — Uptime Kuma Instance Security Posture

**Platform**: Uptime Kuma **1.23.15** at `http://status.getkwikid.com:3001`  
**Audit scope**: Network exposure, authentication model, sensitive data, API key security, PII risk  
**Source**: Live authenticated extraction 2026-07-11 + network testing + Uptime Kuma security architecture

---

## 1. Security Posture Summary

| Category | Rating | Notes |
|:---|:---:|:---|
| Software version | 🔴 OUTDATED | Running **1.23.15**; upstream latest is **2.2.1** — multiple minor+major versions behind. Review changelog for security fixes. |
| Public endpoint exposure | ⚠️ ATTENTION | 11 published status pages, one per bank client — publicly accessible by slug |
| TLS/HTTPS on port 3001 | ❌ NOT ACTIVE | HTTP only on port 3001; data in transit not encrypted |
| Admin authentication | ✅ Present | Admin UI requires username + password (verified: `root_user` works) |
| Embedded monitor credentials | 🔴 RISK | **236 of 332 monitors** store credentials (auth headers, basic-auth passwords, OAuth secrets, push tokens, TLS keys) in monitor config |
| API keys | ⚠️ REVIEW | 3 active keys, **none with an expiry date** (`expires: null`) |
| Shared key `uk6_...` on /metrics | ⚠️ 401 | The shared key returns 401 — either not this instance's key or `/metrics` disabled. Confirm before relying on it |
| Public endpoint data exposure | ⚠️ REVIEW | Public status pages reveal client names (BOB, RBL, ICICI, Bajaj…) and health-endpoint URLs |
| Admin credential storage | 🔴 RISK | Admin creds now shared in chat; must be stored in a secret manager, not source control |
| Weak admin password | 🔴 RISK | Admin account `root_user` uses a guessable `<username>123$`-style password; rotate to a strong secret |

---

## 2. Network Exposure Analysis

### 2.1 HTTP on port 3001 (not HTTPS)

The instance runs HTTP (not HTTPS) on port 3001. This was confirmed by network testing:
- TLS handshake on port 3001 → connection reset (server does not speak TLS on 3001)
- Port 80 → blocked by proxy (not in CCR allowlist, but exists)
- Port 443 → blocked by proxy

**Security implication**: Any API calls to `http://status.getkwikid.com:3001/metrics` transmit the API key `uk6_...` in plaintext over the network. If the communication path traverses untrusted networks, the key could be intercepted.

**Mitigation recommendations**:
1. Place Uptime Kuma behind an HTTPS reverse proxy (nginx, Caddy) on port 443
2. Or use Uptime Kuma's built-in SSL certificate support if hosting on HTTPS domain
3. Alternatively, restrict access to `/metrics` to known IP ranges (the KwikID AI server's IP)

### 2.2 Public status page endpoints

`/api/status-page/<slug>` and `/api/status-page/heartbeat/<slug>` are completely public — no authentication required. This is by design (public status pages are meant for public visibility).

**Security implication**: Anyone who discovers the slug can read:
- Service names and types for all monitors on the page
- Target URLs (if `sendUrl: true` is configured on monitors)
- Historical heartbeat data (response times, up/down history)
- Active incidents and their descriptions
- Maintenance window titles and schedules

**Assessment**: This is acceptable risk for an ops status page. However, ensure:
- Monitor `sendUrl` is `false` for any internal services whose URLs should not be public
- Incident descriptions do not include internal IP addresses or credentials
- Maintenance window titles do not reveal internal system architecture details

### 2.3 /metrics endpoint access

The `/metrics` endpoint requires a Bearer token. It returns:
- All monitor names and types (including those not on the public status page)
- All target URLs (all monitors, not just public ones)
- Current status of every monitor

**If the API key is compromised**: An attacker gains read access to the full infrastructure map (all service names, URLs, current status). This is sensitive but not catastrophic — it reveals what services exist and their health, but does not allow any modification.

---

## 3. API Key Security

### 3.1 Live API keys (3 configured)

The instance has **3 active API keys**, extracted live 2026-07-11. Keys are stored **hashed** in Uptime Kuma, so only metadata is visible (the cleartext values cannot be recovered from the extract):

| ID | Name | Active | Expires | Created |
|:--|:--|:--|:--|:--|
| 3 | `supportdashboard` | ✅ | **never** | 2026-01-14 |
| 4 | `script` | ✅ | **never** | 2026-03-31 |
| 6 | `Support_Automation` | ✅ | **never** | 2026-06-15 |

🔴 **None of the three keys has an expiry date.** Non-expiring API keys are a standing risk — a leaked key stays valid indefinitely. Set expiries and rotate.

### 3.2 The shared key `uk6_2w7...`

`uk6_2w7DIcrXJd0y5g7bIJ_0flqk6vV_B6FRZLsEkyr3` — provided for this discovery. During unauthenticated testing it returned **401** from `/metrics`. Because keys are hashed, it cannot be matched to one of the three above from the extract. The `Support_Automation` key (created 2026-06-15, matching the Support Automation project timeline) is the most likely intended match — verify by testing `/metrics` with it once `/metrics` access is confirmed enabled.

**This key is now recorded in this document.** Treat this document as sensitive — do not commit it to public repositories.

### 3.2b API key properties

- Format: `uk<version>_<random-string>` (version 6 prefix)
- Scope: Read-only access to `/metrics` endpoint (Prometheus)
- Cannot be used to: create/modify monitors, send notifications, change settings, post incidents
- Rotation: via admin UI `Settings → API Keys`

### 3.3 🔴 Embedded monitor credentials (major finding)

**236 of the 332 monitors (71%)** carry embedded secrets in their monitor configuration — one or more of: custom auth `headers`, request `body`, `basic_auth_user`/`basic_auth_pass`, `oauth_client_id`/`oauth_client_secret`/`oauth_token_url`, `pushToken`, `databaseConnectionString`, `radiusPassword`/`radiusSecret`, `mqttUsername`/`mqttPassword`, or `tlsCa`/`tlsCert`/`tlsKey`.

Anyone with admin access (or a broad API key, or a Socket.io login) can read all of these in cleartext. Implications:
- The `includeSensitiveData` flag governs whether these are echoed in some API responses — audit it per monitor.
- These docs were generated with those fields **redacted**; they are intentionally NOT reproduced here.
- Rotate any secret that may have been exposed, and prefer server-side agents (push monitors) or a secrets proxy over embedding credentials directly in monitor config.

### 3.3 Key storage recommendations

```python
# CORRECT: Load from environment variable
import os
UPTIME_KUMA_API_KEY = os.environ["UPTIME_KUMA_API_KEY"]

# WRONG: Hardcoded in application code
UPTIME_KUMA_API_KEY = "uk6_2w7DIcrXJd0y5g7bIJ_0flqk6vV_B6FRZLsEkyr3"  # DO NOT DO THIS
```

The key should be stored in:
- The application's `.env` file (not committed to git)
- AWS Secrets Manager or equivalent
- Never in source code, never in logs, never in Freshdesk notes

---

## 4. Admin Authentication Model

### 4.1 Socket.io admin login

Admin operations require username + password credentials via Socket.io `login` event. These credentials are the Uptime Kuma admin account credentials — effectively superuser access to all configuration.

**The AI must never use admin credentials.** Admin credential storage anywhere in the AI pipeline is prohibited.

The admin account `root_user` was verified working during this extraction. The password follows a weak, guessable pattern (`<username>123$`). 🔴 **Rotate to a strong, random password** and store it in a secret manager.

### 4.2 2FA (Two-Factor Authentication)

Uptime Kuma supports TOTP-based 2FA. Status on this instance: not confirmed via API. Given that port 3001 is publicly reachable over plain HTTP and the admin password is weak, enabling 2FA is strongly recommended.

---

## 5. PII and Sensitive Data Exposure

### 5.1 What Uptime Kuma stores

- Monitor target URLs (may contain internal hostnames, tokens in query strings)
- Monitor HTTP authentication credentials (if using auth monitoring)
- Notification channel secrets (Slack webhook URLs, email passwords, API keys)
- Heartbeat response messages (may include HTTP response body snippets if keyword monitoring returns them)

### 5.2 PII in monitoring data

Uptime Kuma does NOT store customer PII. The AI monitoring integration has low PII risk because:
- Monitor names are service names (e.g., "KwikID API"), not customer data
- Heartbeat messages are HTTP status codes and error types, not customer data
- Response times are timing data, not content

**One exception**: If a keyword monitor is configured to return the matched keyword from the response body, and the response body contains PII (e.g., session ID in a health check endpoint), that PII could appear in the `msg` field. This is unlikely but should be audited for each monitor.

### 5.3 AI note content guidelines

The Observation Generator must NOT include in private notes:
- Raw URLs from monitors if they contain authentication tokens
- Internal hostnames that should not be visible to L1 agents
- The Uptime Kuma API key

The Observation Generator SHOULD include:
- Monitor names and types
- Status (UP/DOWN/MAINTENANCE)
- Duration of outage
- IST-formatted timestamps
- Active incident title and (safe) content

---

## 6. Sensitive Configuration Items

The following items in Uptime Kuma contain sensitive credentials and must never be accessed by the AI:

| Configuration | Contains | AI access |
|:---|:---|:---|
| Notification channel configs | Slack webhook URLs, email credentials, PagerDuty API key | NEVER — admin only |
| Monitor HTTP auth config | Basic auth username/password for authenticated health endpoints | NEVER — admin only |
| Proxy configurations | Internal proxy credentials | NEVER — admin only |
| Database connection strings | (For DB monitors) Full connection strings with passwords | NEVER — admin only |
| Docker host configs | Docker socket paths, TLS certs | NEVER — admin only |
| Admin password | Uptime Kuma admin account password | NEVER |

---

## 7. Incident Description Security

When the Uptime Kuma admin posts a status page incident, the `content` field may contain:
- Internal investigation notes accidentally
- Internal team names
- Partial root cause details that should be confidential

**AI policy**: The AI Observation Generator may include the incident `title` in private notes. It should NOT include the full `content` in customer-facing replies without human review.

---

## 8. Recommended Security Improvements

| Priority | Recommendation | Effort | Impact |
|:---|:---|:---|:---|
| 🔴 HIGH | Enable HTTPS (TLS) on port 3001 or expose via HTTPS reverse proxy | Medium (nginx config) | Protects API key in transit |
| 🔴 HIGH | Store API key in environment variable / secrets manager, not in source code | Low | Prevents key leakage in code |
| 🟡 MEDIUM | Enable 2FA on Uptime Kuma admin account | Low (admin UI) | Hardens admin access |
| 🟡 MEDIUM | Set `sendUrl: false` on all monitors for internal services | Low (per monitor setting) | Prevents URL disclosure via public status page |
| 🟡 MEDIUM | Rotate API key periodically (quarterly or on team member departure) | Low | Limits blast radius of potential compromise |
| 🟠 LOW | IP-allowlist access to `/metrics` endpoint | Medium (nginx config) | Defense-in-depth for API key |
| 🟠 LOW | Audit `msg` field of keyword monitors for PII | Low (manual review) | Prevents PII in heartbeat history |
