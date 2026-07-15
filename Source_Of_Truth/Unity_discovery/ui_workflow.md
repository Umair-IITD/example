# UI Workflow — Unity Admin Portal

**Portal (React SPA)**: `https://vkyc360.unitybank.co.in:9090`  
**API Base URL**: `https://vkyc360.unitybank.co.in` (port 443)  
**Purpose**: Document the admin UI capabilities and workflow  
**Source**: API response analysis (2026-07-13) + CCR proxy limitation note

---

## Access Constraint

> The React SPA at port 9090 **cannot be accessed from CCR** (Anthropic cloud environment). The CCR proxy does not support TLS re-termination for non-443 HTTPS ports. This is a platform limitation documented in `/root/.ccr/README.md`.
>
> The following workflow documentation is derived from:
> - Confirmed API responses (live, 2026-07-13)
> - Inferred UI capabilities based on what the API exposes
> - Postman collection structure
>
> Any UI-specific detail (button labels, field layouts, sidebar items) requires a human with the portal open in Microsoft Edge to confirm.

---

## Known Architecture

The portal is a **React Single Page Application (SPA)**:
- Hosted at: `https://vkyc360.unitybank.co.in:9090`
- Makes authenticated API calls to `https://vkyc360.unitybank.co.in` (port 443)
- Confirmed: opens in Microsoft Edge; returns Forbidden in Chrome (may be browser-specific CORS or cert policy)
- Auth: stores JWT (from `POST /v1/agent/generate_token`) — likely in localStorage or sessionStorage

---

## What the Admin Portal CAN Show (from API data)

### Session List View

When an admin searches by phone number, the portal fetches:
```
GET /api/v1/getAllUserSession/unity/{phone_number}
```

The session list shows these columns (inferred from API fields):

| Column (inferred) | API field | Notes |
|:---|:---|:---|
| Session ID | `session_id` | UUID v4 — clickable to open detail |
| Status | `session_status` | One of 7 confirmed values |
| Date / Init time | `init_time` or `latest_init_time` | When customer started |
| Product | `productCode` | e.g. ONEFIN |
| Language | `lang` | hi / en |
| Queue | `queue_id` | e.g. unity_free_hi_vkyc |
| Agent | `agent_id` | Agent email (visible in detail) |

### Session Detail View

When an admin opens a session, the portal fetches:
```
GET /v1/session/get_details/{session_id}
```

The session detail shows these sections (inferred from API response):

**Summary Panel**
- Session status badge
- Customer phone number / user ID
- Session type (VKYC)
- Product code
- Language

**Timeline**
- `init_time` → `start_time` → `vkyc_start_time` → `end_time`
- Queue position at init
- Agent assignment time

**Agent & Audit**
- Agent ID (email)
- Auditor name + feedback
- Audit result (1 = approved)
- `feedback.type` (Approve / Reject / Reopen)
- `auditor_feedback` text

**Documents**
- Aadhaar XML link → download
- Aadhaar PDF link → download
- PAN card image (pan_url)
- Selfie image (selfie_url)

**KYC Journey Summary** (from `summary_data`)
- Overall summary steps: Questions ✅ / Selfie ✅ / Pan Card ✅
- Q&A responses
- PAN details: name, DOB, father's name, PAN number
- Face match score: selfie vs PAN

**Videos**
- Customer video (user.webm)
- Agent video (agent.webm)
- Agent screen recording (agent_video_screen.webm)
- Video duration

**Package Downloads**
- Summary PDF
- Summary JSON
- Full ZIP package

**Location**
- Address (from Aadhaar or GPS)
- Latitude / Longitude

---

## Confirmed UI Workflows (from API)

### Search by Phone Number

1. Admin enters a 10-digit phone number in the search box
2. Portal calls: `GET /api/v1/getAllUserSession/unity/{phone_number}`
3. If sessions exist: shows list sorted by recency (most recent first)
4. If not found: returns `{"session_count": 0, "session_list": [], "status_code": 200}` — portal likely shows "No sessions found"

### Search by Session ID

Session ID (UUID v4) may be entered directly:
1. Admin enters the session UUID
2. Portal calls: `GET /v1/session/get_details/{session_id}`
3. Opens directly to session detail view

### Viewing a Session

1. Click session from list (or direct session_id lookup)
2. Portal loads `get_details` response
3. Shows status, timeline, document thumbnails, video player, audit result
4. All downloads authenticated via `auth` header (not pre-signed S3 URLs)

---

## Confirmed Action Buttons (READ-ONLY CONSTRAINT)

The portal has at minimum one mutation endpoint:

| Endpoint | What it does | AI Policy |
|:---|:---|:---|
| `POST /v1/agent/sendLink/` | Sends a new VKYC link to a customer | **NEVER CALL — MUTATION** |

> Any "Send Link", "Resend", "Create Session", "Retry" buttons in the UI call this endpoint.  
> The AI investigation pipeline must never trigger these actions.

---

## Confirmed Session Status Values (visible in UI)

| `session_status` value | Likely UI label |
|:---|:---|
| `kyc_result_approved` | Approved / Completed |
| `kyc_result_rejected` | Rejected |
| `kyc_rejected` | Rejected |
| `kyc_result_partial_update` | Partial / Pending |
| `session_expired` | Expired |
| `user_abandoned` | Abandoned |
| `waiting` | Waiting / In Queue |

---

## What Requires Human Confirmation

The following UI-specific details require a human to view the portal in Microsoft Edge:

- [ ] Exact field labels and order on the session list table
- [ ] Whether there are sidebar navigation items beyond session search
- [ ] Exact filter/search UI — date range pickers, status dropdowns, product filters
- [ ] Whether the portal shows real-time sessions (WebSocket / polling interval)
- [ ] Whether there are admin-specific views (agent management, audit queues)
- [ ] Exact logout button location and flow
- [ ] Whether token is stored in localStorage vs sessionStorage vs cookie
- [ ] Whether the portal has pagination or infinite scroll for session lists
- [ ] Whether all 7 `session_status` values appear as readable labels or raw strings
- [ ] Whether video can be played in-browser or requires download

---

## Token Storage Observation

Based on the architecture (React SPA + JWT + `auth` header):
- The JWT is almost certainly stored in **localStorage** or **sessionStorage** (not httpOnly cookie — the API uses `auth` header, not `Cookie`)
- This means the token is accessible to JavaScript and to the AI agent if browser access is granted
- 12-hour TTL means admin portal sessions expire twice per day (agent token)
- 7-day admin token TTL means the browser tab session lasts a full week

---

## Browser Compatibility Note

Microsoft Edge: ✅ Confirmed working  
Google Chrome: ❌ Returns "Forbidden"

Possible explanation: The portal may check `User-Agent`, enforce a browser-specific TLS policy, or have CORS configured for Edge only. The API on port 443 is not affected by this — it is browser-agnostic and works from any HTTP client.
