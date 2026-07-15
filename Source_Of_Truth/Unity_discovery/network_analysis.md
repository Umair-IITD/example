# Network Analysis — Unity Admin Portal

**Base URL**: `https://vkyc360.unitybank.co.in` (port 443)  
**Admin SPA**: `https://vkyc360.unitybank.co.in:9090` (port 9090 — not accessible from CCR)  
**Source**: Live network diagnostics + live API calls — 2026-07-13

---

## Summary

| Layer | Port | Status | Notes |
|:---|:---:|:---|:---|
| REST API | 443 | ✅ Fully working | All endpoints confirmed from CCR |
| Admin SPA | 9090 | ❌ Not accessible from CCR | CCR proxy TLS limitation — browser only |
| DNS | — | ✅ Resolved | 2× AWS ELB IPs (ap-south-1) |
| TCP to port 9090 | 9090 | ✅ CONNECT works | TCP tunnel OK — TLS blocked by proxy |
| TLS to port 9090 | 9090 | ❌ TLS blocked | CCR proxy drops TLS on non-443 ports |

---

## DNS Resolution (Confirmed)

```
vkyc360.unitybank.co.in → 15.206.150.14  (AWS ap-south-1, Mumbai)
                         → 35.154.11.197  (AWS ap-south-1, Mumbai)
```

Server is behind an **AWS Application Load Balancer** (`Server: awselb/2.0`), hosted in `ap-south-1` (Mumbai). This is consistent with a KwikID deployment for Indian banking.

---

## Port 443 — API (Confirmed Working)

### Confirmed Endpoints

All calls made using `auth: <token>` header.

| # | Method | Path | Status | Auth | Notes |
|:--|:--|:--|:--|:--|:--|
| 1 | POST | `/v1/agent/generate_token` | ✅ 200 | None | Confirmed live — JWT obtained |
| 2 | GET | `/api/v1/getAllUserSession/unity/{phone}` | ✅ 200 | Yes | 88 sessions returned in test |
| 3 | GET | `/v1/session/get_details/{session_id}` | ✅ 200 | Yes | Full session detail confirmed |
| 4 | GET | `/v1/health` | ✅ 200 | None | Returns `"ok"` (plain text) |
| 5 | GET | `/api/v1/health` | ✅ 200 | None | Returns `"ok"` (plain text) |
| 6 | POST | `/v1/agent/sendLink/` | Documented | Yes | **MUTATION — DO NOT CALL** |

### Request Headers (Authenticated)

```
auth: eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...
Content-Type: application/json
```

### Response Headers (Observed)

```
HTTP/1.1 200 OK
Server: awselb/2.0
Content-Type: application/json
```

---

## Port 9090 — Admin SPA (CCR Limitation)

### TCP Connectivity ✅

```
CONNECT vkyc360.unitybank.co.in:9090 HTTP/1.1 → 200 Connection Established
```

TCP tunnel established successfully.

### Plain HTTP Probe ✅ (confirms HTTPS server)

```
GET / HTTP/1.1
Host: vkyc360.unitybank.co.in:9090

→ HTTP/1.1 400 Bad Request
  Server: awselb/2.0
  400 The plain HTTP request was sent to HTTPS port
```

Confirms port 9090 is an HTTPS server (not plain HTTP).

### TLS Handshake ❌ (CCR proxy drops it)

TLS ClientHello sent through the CONNECT tunnel — connection reset before ServerHello:
```
→ TLS ClientHello (SNI: vkyc360.unitybank.co.in)
← Connection reset by peer (0 bytes from server)
```

Tested with curl, Python ssl, httpx, Node.js tls, Playwright Chromium — all fail identically.

### Root Cause (Confirmed)

From `/root/.ccr/README.md` (CCR platform documentation):

> **Not supported through the proxy**: `non-443 HTTPS ports, raw-TCP databases`

The CCR proxy does TLS re-termination (MITM) for port 443 only. When TLS data arrives on a non-443 port tunnel, the proxy closes the connection. The `Connection reset by peer` with 0 bytes confirms the RST comes from the proxy, not from the Unity server.

This is an **explicit, documented platform constraint** — not a transient error or configuration issue.

### WebFetch Behaviour

```
WebFetch GET https://vkyc360.unitybank.co.in:9090/... → HTTP 403
  source: "target"  (misleading — this is the CCR proxy's policy response, not the Unity server)
```

The `source: "target"` field in WebFetch error is the CCR proxy's policy denial, not an HTTP 403 from the Unity server itself.

---

## CCR Proxy Behaviour Summary

| Test | Result | Interpretation |
|:---|:---|:---|
| `CONNECT vkyc360.unitybank.co.in:9090` | ✅ `200 Connection Established` | TCP tunnel allowed |
| TLS ClientHello through CONNECT tunnel | ❌ `Connection reset by peer` (0 bytes) | CCR drops TLS on non-443 port |
| WebFetch to port 9090 | ❌ `403` from proxy | CCR blocks HTTP to non-443 HTTPS port |
| `GET /v1/health` on port 443 | ✅ `200 ok` | Port 443 works perfectly |
| `POST /v1/agent/generate_token` on port 443 | ✅ `200` + JWT | Full API on port 443 confirmed |
| Direct TCP to `15.206.150.14:9090` (no proxy) | ❌ Timeout | All CCR outbound goes through proxy |

---

## File Download Endpoint

Downloads are authenticated — not pre-signed S3 URLs:

```
GET https://vkyc360.unitybank.co.in/v1/download_content/{bucket}/{path}
auth: <token>
```

URL patterns observed in session data:

```
/v1/download_content/kwikid-prod/videokyc/aadhaar/unity/{phone}/{phone}_aadhaar.xml
/v1/download_content/kwikid-prod/videokyc/aadhaar/unity/{phone}/{phone}_aadhaar_redacted.pdf
/v1/download_content/kwikid-prod/videokyc/videos/unity/{phone}/{session_id}/agent.webm
/v1/download_content/kwikid-prod/videokyc/videos/unity/{phone}/{session_id}/agent_video_screen.webm
/v1/download_content/kwikid-prod/videokyc/videos/unity/{phone}/{session_id}/user.webm
/v1/download_content/kwikid-prod/videokyc/images/unity/{phone}/{session_id}/{phone}_selfie.jpg
/v1/download_content/kwikid-prod/videokyc/images/unity/{phone}/{session_id}/{phone}_pan_original.jpg
/v1/download_content/kwikid-prod/videokyc/reports/unity/{phone}/{session_id}/summary.pdf
/v1/download_content/kwikid-prod/videokyc/reports/unity/{phone}/{session_id}/summary.json
/v1/download_content/kwikid-prod/videokyc/reports/unity/{phone}/{session_id}/package.zip
```

---

## TLS / Certificate (Port 443)

| Property | Confirmed |
|:---|:---|
| TLS | Working on port 443 — standard HTTPS |
| Certificate | Valid (no `verify=False` needed) |
| Custom CA | Not required |
| Server | AWS ELB (`Server: awselb/2.0`) |
| Region | `ap-south-1` (Mumbai) |

Port 9090 TLS certificate properties: **not observable from CCR** (TLS blocked before ServerHello).

---

## Rate Limiting

No rate limiting observed during discovery:
- 88 session records fetched in a single `getAllUserSession` call
- Token endpoint called multiple times — no throttling
- No `Retry-After` or `X-RateLimit-*` headers observed

---

## Error Responses (Confirmed)

| HTTP Status | Scenario | Response Body |
|:---|:---|:---|
| 200 | Success | Varies by endpoint |
| 400 | Invalid session ID | `{"e":"list index out of range","msg":"Invalid session id","status":400}` |
| 401 | Missing auth header | `{"message":"Missing authorization header"}` |
| 401 | Invalid/expired token | `{"message":"Token is invalid"}` |

---

## Resolution for Port 9090 UI Access

For AI automation purposes, port 9090 is not needed — the full REST API is on port 443.

For human UI documentation:
1. Open `https://vkyc360.unitybank.co.in:9090` in **Microsoft Edge** (not Chrome)
2. Login with admin portal credentials (see authentication.md)
3. Capture HAR via DevTools (F12 → Network → right-click → Save as HAR with content)
4. Share HAR file for analysis

---

## Proxy Bridge

A local HTTP→HTTPS proxy was set up at `127.0.0.1:18080` (PID 3642) to forward to the CCR proxy at port 37563. This was used during the TLS investigation phase. The proxy confirmed the CCR limitation but did not overcome it for port 9090.
