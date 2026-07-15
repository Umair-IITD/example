# Limitations — Unity Admin Portal

**API Base URL**: `https://vkyc360.unitybank.co.in` (port 443)  
**Purpose**: Document what Unity cannot provide; identify blind spots for the AI pipeline  
**Source**: Live API confirmed — 2026-07-13

---

## 1. What Unity Admin Portal CAN Provide (Confirmed)

| Data | Available | API Field | Notes |
|:---|:---:|:---|:---|
| Session status | ✅ | `session_status` | 7 confirmed enum values |
| Auditor decision | ✅ | `auditor_feedback`, `feedback.type` | "Approve" / "Reject" / "Reopen" |
| Auditor name | ✅ | `auditor_name` | Email of reviewing auditor |
| Session timestamps | ✅ | `init_time`, `start_time`, `end_time`, `vkyc_start_time` | Unix epoch |
| Journey step results | ✅ | `overall_summary[]` | Per-step success/fail |
| Customer Q&A responses | ✅ | `qna[]` | Per-question answers |
| Document details (PAN, Aadhaar) | ✅ | `summary_data.docs[].details` | OCR-extracted fields |
| Face match score | ✅ | `facematch_score.selfie_pan_match` | 0–100 |
| PAN validation (NSDL) | ✅ | `docs[].validator.raw_nsdl` | Raw NSDL response |
| Multiple sessions per phone | ✅ | `getAllUserSession` → `session_list[]` | All sessions returned |
| Session ID for log lookup | ✅ | `session_id` (UUID v4) | Key for Loki correlation |
| Agent assignment | ✅ | `agent_id`, `agent_assignment_time` | Agent email + timestamp |
| Video presence | ✅ | `IS_VIDO_UPLOADED`, `video_duration` | Whether video was uploaded |
| Customer location | ✅ | `location` (JSON string) | Address + lat/lng |
| Captured images list | ✅ | `captured_images` | ["selfie", "pan"] |
| Link ID | ✅ | `link_id` | 12-char alphanumeric |
| Product code | ✅ | `productCode` | e.g. "ONEFIN" |
| Language | ✅ | `lang` | "hi", "en" |

---

## 2. What Unity Admin Portal CANNOT Provide

### 2.1 Platform Infrastructure State

| Data needed | Available in Unity | Where to get it |
|:---|:---:|:---|
| Was VKYC service UP/DOWN? | ❌ | Uptime Kuma (`/metrics`) |
| Was SMS gateway DOWN? | ❌ | Uptime Kuma (SMS gateway monitor) |
| Was video streaming service DOWN? | ❌ | Uptime Kuma (VKYC service monitor) |
| Platform-wide outage at ticket time | ❌ | Uptime Kuma + incident system |
| Historical uptime percentage | ❌ | Uptime Kuma (24h/30d uptime per monitor) |

Unity is a **consumer of the platform** — it records session outcomes, not infrastructure state. Uptime Kuma is the authoritative source for infrastructure health at any given time.

### 2.2 Granular Journey Failure Reason

| Data needed | Available | Notes |
|:---|:---:|:---|
| `failure_reason` field | ❌ | **Does not exist** — no dedicated failure reason code |
| `journey_status` field | ❌ | **Does not exist** — no granular step state field |
| `otp_verified` field | ❌ | **Does not exist** at top level — check `qna[]` and `stage_data` |
| `liveliness_result` field | ❌ | **Does not exist** at top level — check `overall_summary[]` |
| `ocr_status` field | ❌ | **Does not exist** at top level — check `docs[].details` for content |

> These fields were assumed to exist based on generic VKYC patterns but are **not present in confirmed API responses**. Use `session_status`, `overall_summary[]`, `qna[]`, and `feedback` JSON instead.

### 2.3 Customer Identifier — No URN

| Assumed field | Reality |
|:---|:---|
| `urn` (Unique Reference Number) | **Does not exist** in this system |
| Primary identifier: URN | **Reality**: `phone_number` (10-digit mobile) is the only customer identifier |

Support tickets that reference a "URN" must be handled by attempting to match against `phone_number` or `session_id` instead.

### 2.4 Network/Device Issues

| Data needed | Available in Unity | Notes |
|:---|:---:|:---|
| Customer's network quality | ❌ | Not captured in API |
| Customer's browser/device type | ❌ | Not in top-level fields; may be in `stage_data` (unparsed JSON) |
| WebRTC connection statistics | ❌ | Not in portal — may be in Loki logs |
| Customer internet speed during session | ❌ | Not captured |

### 2.5 Real-Time Monitoring

| Capability | Available | Notes |
|:---|:---:|:---|
| Push notifications on session failure | ❌ | Unity is query-only; no webhooks to AI |
| Proactive alerts on session failures | ❌ | Uptime Kuma handles alerting |
| Real-time session state changes | ❌ | Must poll `get_details` |

### 2.6 Downstream Processes

| Data needed | Available in Unity | Where to get it |
|:---|:---:|:---|
| Account opening status | ❌ | Bank's core banking system |
| Account number assigned | ❌ | Bank's CRM |
| KYC decisioning reason (bank-side) | ❌ | Bank's KYC engine |
| Whether customer was contacted post-KYC | ❌ | Bank's CRM |

Unity only knows about the **video KYC session** — not what happens afterwards in account opening.

### 2.7 UI Access from CCR

| Capability | Available | Notes |
|:---|:---:|:---|
| React SPA at port 9090 from CCR | ❌ | CCR proxy does not support non-443 HTTPS TLS re-termination |
| API at port 443 from CCR | ✅ | Fully accessible — all REST API calls confirmed |

---

## 3. Known Blind Spots

### 3.1 Session Data Retention Window (Unknown)

Session data retention period has not been confirmed. Older sessions may be unavailable:

```python
if not unity_bundle.get("session_found"):
    # Could be: wrong phone number, session never created, OR data purged
    note = (
        "No KYC session found. Possible causes: "
        "(1) KYC not yet initiated for this number, "
        "(2) incorrect phone number provided, or "
        "(3) session data older than retention window."
    )
```

### 3.2 Multiple Sessions Per Customer

A customer may have attempted KYC multiple times. `getAllUserSession` returns all sessions in recency order. The AI must:
1. Parse all sessions in `session_list[]`
2. Select the session with `init_time` closest to (but before) the ticket creation time
3. Consider non-terminal sessions (`waiting`, `kyc_result_partial_update`) as higher priority if found near ticket time
4. Not assume the first (latest) session is the one the ticket is about

### 3.3 Rejection Cause Ambiguity

A `kyc_result_rejected` or `kyc_rejected` status could mean:
- Platform failure during video (system-caused rejection)
- Agent/auditor decision to reject (quality/fraud concern)
- Automated rule rejection (document mismatch)

The `feedback.type` / `auditor_feedback` field is the only Unity-side differentiator. Without a clear `feedbackComment`, the AI cannot definitively attribute the cause.

### 3.4 Transient Failures Not Captured

If a session failed transiently and was retried successfully within Unity (without creating a new session record), the failure may not appear in session data. Unity only records the **final state**, not intermediate failures.

### 3.5 `extras` and `stage_data` Opacity

Both fields contain JSON-encoded strings with supplemental data, but their internal schema has not been fully mapped. These may contain:
- OTP verification details
- Liveness check scores
- Device/browser info
- Step-by-step error codes

These fields should be parsed (with `json.loads()`) and included in the evidence bundle for human review, but their internal structure is not yet documented.

---

## 4. What Requires Loki (Server Logs)

The following failure modes are not visible in Unity portal but may appear in Loki:

| Failure mode | Unity visibility | Loki approach |
|:---|:---:|:---|
| WebRTC connection failure (signaling) | ❌ | `|= "session_id=<id>" |= "webrtc"` |
| Media server errors during video | ❌ | Search by `session_id` in media service logs |
| OTP delivery failure (carrier-level) | ❌ | Search by `phone_number` in SMS gateway logs |
| API timeout (UIDAI, NSDL) | ❌ | Search by `session_id` in backend service logs |
| Internal server errors (500s) | ❌ | Search by `session_id` in backend logs |

---

## 5. What Requires Freshdesk

| Data | Source |
|:---|:---|
| Customer's description of the problem | Freshdesk ticket description |
| Previous interactions with the customer | Freshdesk ticket history |
| Support agent's prior investigation notes | Freshdesk private notes |
| SLA breach risk | Freshdesk ticket priority/due date |
| Customer sentiment | Freshdesk ticket description + history |

---

## 6. Architecture Gap Recommendations

| Gap | Recommended fix | Effort |
|:---|:---|:---|
| No granular `failure_reason` field | Parse `stage_data` and `extras` to extract step errors | Medium |
| `session_status` enum not documented by Unity | Build exhaustive status table from observed sessions (done — 7 values) | Done |
| Platform failure not visible in Unity | Cross-reference every failed status with Uptime Kuma (already planned) | Already in design |
| Retention window unknown | Live test: query a session older than 90 days; document result | Low |
| No URN — tickets may mention URN | Build ticket parser that normalises "URN" to phone_number or session_id | Medium |
| `extras` and `stage_data` schemas unknown | Parse from additional live sessions; add to field_dictionary.md | Medium |
| UI not accessible from CCR | Use Microsoft Edge on local machine for manual UI workflow capture | Low (manual) |
| Admin portal returns Forbidden in Chrome | Investigate: user-agent check or CORS config on port 9090 | Low (informational) |
