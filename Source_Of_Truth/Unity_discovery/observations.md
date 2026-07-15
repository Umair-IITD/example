# Observations — Unity Admin Portal Discovery

**Portal**: `https://vkyc360.unitybank.co.in:9090`  
**Discovery phase**: Phase A Wave 2  
**Date**: 2026-07-13  
**Status**: ⏳ TLS-blocked — domain in CCR allowlist but TLS rejected at server/proxy level

---

## 1. Discovery Status

### 1.1 Network Access

| Check | Status | Notes |
|:---|:---:|:---|
| DNS resolution | ✅ CONFIRMED | Resolves to 2 IPs: `15.206.150.14`, `35.154.11.197` (AWS ap-south-1 Mumbai, ELB) |
| TCP port 9090 reachable | ✅ CONFIRMED | CONNECT tunnel via CCR returns 200 Connection Established |
| Plain HTTP reaches server | ✅ CONFIRMED | Server responds "400 The plain HTTP request was sent to HTTPS port" (Server: awselb/2.0) |
| TLS handshake successful | ❌ FAILS | Connection reset by peer immediately after TLS ClientHello — all TLS versions |
| HTTP response from root | ❌ BLOCKED | Port 9090 requires TLS; plain HTTP is rejected by the server |
| `POST /generate_token` works | ❌ BLOCKED | TLS failure prevents all HTTPS requests |
| `GET /getAllUserSession` works | ❌ BLOCKED | TLS failure prevents all HTTPS requests |
| `GET /get_details` works | ❌ BLOCKED | TLS failure prevents all HTTPS requests |

**Root cause confirmed (2026-07-13)**: CCR proxy does not support non-443 HTTPS ports. This is a documented platform limitation (`/root/.ccr/README.md`). Port 9090 (the Unity portal HTTPS port) is explicitly listed as unsupported for TLS re-termination. The proxy allows TCP CONNECT tunnels but drops TLS handshakes on non-443 ports. All access paths confirmed blocked:

- Raw socket TLS → Connection reset by peer (0 bytes from server — proxy drops TLS)
- WebFetch → 403 from CCR proxy (policy block, not from Unity server)
- Direct TCP → Timeout (all outbound requires CCR proxy)

**Resolution required**: Share API responses captured from a local machine (Postman, browser DevTools HAR export, or curl from local terminal). No workaround exists within the CCR environment.

### 1.2 Prior Knowledge Applied

The following are confirmed from the Postman collection and API response samples provided before this session:

| Fact | Source | Status |
|:---|:---|:---|
| Auth endpoint: `POST /generate_token` | Postman collection | ✅ Confirmed |
| Session list endpoint: `GET /getAllUserSession` | Postman collection | ✅ Confirmed |
| Session detail endpoint: `GET /get_details` | Postman collection | ✅ Confirmed |
| JWT Bearer auth mechanism | Postman collection | ✅ Confirmed |
| `domain: "unity"` field required in token request | Postman collection | ✅ Confirmed |
| Credentials: `shubham.singh@think360.ai` / `New@12345` | User-provided | ✅ Will verify live |

---

## 2. Integration Assessment

### 2.1 What is well-designed

1. **JWT authentication is ideal for API integration.** Stateless tokens mean no session cookies to manage. A single `generate_token` call gives the AI a working credential for all subsequent queries.

2. **Dedicated admin portal with REST API** — Unity exposes a proper REST API rather than screen-scraping being required. This is far more reliable than parsing HTML.

3. **URN as the primary customer identifier** — URN is a stable, opaque identifier that appears in support tickets. Having it as the primary query parameter in `get_details` is a clean integration point.

4. **`getAllUserSession` supports customer-level querying** — searching by URN returns all sessions for a customer, which is important for multi-attempt scenarios (a customer who failed KYC twice before succeeding).

### 2.2 What needs investigation

1. **API response field names not yet confirmed.** The endpoint paths are confirmed but the exact JSON field names, status enum values, and pagination behavior all require live capture.

2. **Token expiry unknown.** If the JWT TTL is short (e.g., 1 hour), the AI must implement proactive token refresh. If TTL is long (e.g., 24 hours), a simpler one-time fetch per session is sufficient.

3. **Pagination model unknown.** If `getAllUserSession` paginates (which it almost certainly does for high-volume deployments), the AI must handle page traversal to find the session nearest the ticket time.

4. **No webhook/push mechanism confirmed.** The portal appears to be query-only (no server-sent events). The AI must poll on demand — this is acceptable for per-ticket investigation.

5. **Multi-session disambiguation.** When a customer has multiple KYC attempts, the AI needs a clear rule for which session is "the one" relevant to a given ticket. See investigation_mapping.md §3.

---

## 3. Gap Analysis

### 3.1 Pre-discovery gaps (to be resolved by live access)

| Gap | Priority | Status |
|:---|:---:|:---|
| Exact response field names for `get_details` | 🔴 CRITICAL | ⏳ Pending live capture |
| Exact response field names for `getAllUserSession` | 🔴 CRITICAL | ⏳ Pending live capture |
| Session status enum values (complete list) | 🔴 CRITICAL | ⏳ Pending live capture |
| Journey status enum values (granular failure steps) | 🔴 CRITICAL | ⏳ Pending live capture |
| JWT payload claims (especially `exp`, `role`) | 🔴 HIGH | ⏳ Pending live capture |
| Pagination model | 🔴 HIGH | ⏳ Pending live capture |
| Are there additional endpoints not in Postman collection? | 🔴 HIGH | ⏳ Pending live capture |
| TLS certificate validity (custom CA vs public CA) | 🟡 MEDIUM | ⏳ Pending network access |
| CORS configuration (for future browser-based integration) | 🟡 MEDIUM | ⏳ Pending live capture |
| Session data retention period | 🟡 MEDIUM | ⏳ Pending live capture |
| Rate limiting behaviour | 🟡 MEDIUM | ⏳ Pending live capture |
| Error response format | 🟡 MEDIUM | ⏳ Pending live capture |

### 3.2 Architectural gaps (independent of network access)

| Gap | Recommendation |
|:---|:---|
| No webhook from Unity on session events | Add `POST /webhook` registration if supported; otherwise AI must poll |
| No structured failure_reason taxonomy | Build mapping from observed values to human-readable RCA categories |
| No cross-reference between Unity and Uptime Kuma | Root Cause Engine provides this (design already handles it) |
| No URN extraction from Freshdesk tickets | Build regex extractor for URN patterns (confirm URN format from live data) |

---

## 4. Risk Register

| Risk | Severity | Mitigation |
|:---|:---:|:---|
| JWT expires mid-investigation | 🟡 MEDIUM | Proactive token refresh 2 minutes before expiry |
| Unity portal unreachable at investigation time | 🟡 MEDIUM | `safe_collect_unity_evidence` fail-open wrapper |
| Multiple sessions per URN — wrong session picked | 🟡 MEDIUM | Select by time proximity to ticket + status priority |
| Session data retention purge (old tickets) | 🟡 MEDIUM | Handle 404/empty gracefully; note in investigation |
| PAN/Aadhaar inadvertently logged | 🔴 HIGH | PII scrubber in Evidence Collector output |
| Credentials stored insecurely | 🔴 HIGH | AWS Secrets Manager; never in source code |
| Admin credential shared in chat | 🔴 HIGH | Treat as compromised; rotate after discovery; store in secrets manager |

---

## 5. Pending Verification Items

Once `*.unitybank.co.in` is added to the CCR allowlist, verify ALL of the following:

### Authentication
- [ ] `POST /generate_token` response shape
- [ ] JWT payload claims
- [ ] Token TTL
- [ ] Whether refresh endpoint exists
- [ ] localStorage key name for token

### Session List
- [ ] Full `getAllUserSession` response shape
- [ ] Pagination parameter names and model
- [ ] Available filter parameters (status, date range, search term)
- [ ] Default sort order
- [ ] Total record count field name

### Session Detail
- [ ] Full `get_details` response shape (every field)
- [ ] Status enum values (complete list)
- [ ] Journey status enum values (complete list)
- [ ] Whether timeline is embedded or separate endpoint
- [ ] Video URL type (direct vs pre-signed vs viewer URL)
- [ ] OCR result structure

### Network
- [ ] All endpoints (capture via browser DevTools)
- [ ] Request correlation headers
- [ ] Response timing (for timeout calibration)
- [ ] Any WebSocket connections
- [ ] TLS certificate issuer and validity

### Security
- [ ] HTTPS certificate validity
- [ ] Sensitive fields masking (Aadhaar, PAN)
- [ ] Whether video URLs are signed or public
- [ ] CORS headers
