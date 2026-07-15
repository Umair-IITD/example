# Investigation Mapping — Unity → AI Pipeline

**API Base URL**: `https://vkyc360.unitybank.co.in` (port 443)  
**Purpose**: Map every Unity API field to its role in the AI Investigation Layer  
**Source**: Live API confirmed — 2026-07-13

---

## Pipeline Architecture

```
Freshdesk Ticket
      │
      ▼
UNITYTOOL (Evidence Collector)
      │  Input: customer phone_number or session_id (UUID v4) from ticket
      │  Action: POST /v1/agent/generate_token
      │          GET /api/v1/getAllUserSession/unity/{phone_number}
      │          GET /v1/session/get_details/{session_id}
      │  Output: Structured Unity Evidence Bundle
      │
      ▼
Evidence Bundle (Unity section)
      │
      ▼
Root Cause Engine
      │  Merges: Unity evidence + Uptime Kuma metrics + Freshdesk context
      │  Produces: RCA hypothesis with confidence score
      │
      ▼
Observation Generator
      │  Produces: Private investigation note for Freshdesk
      │  Decides: Suggested reply + escalation flag
      ▼
Freshdesk Note + Reply
```

---

## 1. Evidence Collector — UNITYTOOL

### Input (from Freshdesk ticket)

The Evidence Collector extracts the following from the ticket before calling Unity:

| Input | Source in Freshdesk | Unity API parameter |
|:---|:---|:---|
| Customer phone number | Ticket subject or description (regex `r'\b[6-9]\d{9}\b'`) | Path param: `/api/v1/getAllUserSession/unity/{phone_number}` |
| Session ID | Ticket description or custom field (regex UUID v4) | Path param: `/v1/session/get_details/{session_id}` |
| Ticket created_at | Ticket metadata | Used for time correlation |
| Reported symptom | Ticket description | Used to select relevant Unity fields |

> **NOTE**: There is NO URN in this system. The primary customer identifier is `phone_number` (10-digit mobile). If a ticket contains something labelled "URN", attempt to treat it as a phone number or session ID.

### Output (Unity Evidence Bundle)

```json
{
  "source": "unity_admin_portal",
  "collected_at": "2026-07-13T09:00:00Z",
  "query_phone_number": "7045722923",
  "session_found": true,
  "session_id": "329c5f9e-9406-4f47-b717-5c87f04054d2",
  "session_status": "kyc_result_approved",
  "session_type": "VKYC",
  "productCode": "ONEFIN",
  "init_time": 1781840077,
  "start_time": 1781840019.436,
  "end_time": 1781841062.598,
  "vkyc_start_time": 1781840814.167,
  "agent_id": "Vidya.Rathod@unitybank.co.in",
  "audit_result": "1",
  "auditor_name": "ext.harshada.kaphare@unitybank.co.in",
  "auditor_feedback": "Approved",
  "feedback_type": "Approve",
  "overall_summary": [
    {"success": true, "title": "Questions"},
    {"success": true, "title": "Selfie"},
    {"success": true, "title": "Pan Card"}
  ],
  "qna": [
    {"q": "What is your Date Of Birth?", "a": "Correct"}
  ],
  "docs": [
    {
      "name": "Pan Card",
      "details": {"name": "...", "pa_number": "XXXXXX0M"},
      "facematch_score": {"selfie_pan_match": 98}
    }
  ],
  "all_session_count": 5,
  "error": null
}
```

---

## 2. Field → Root Cause Mapping

### Session Status Mappings

Confirmed session_status enum values and their AI interpretation:

| Unity `session_status` | Ticket symptom | Root Cause hypothesis | Confidence |
|:---|:---|:---|:---|
| `kyc_result_approved` | "KYC failed" / "account not opening" | KYC is done and approved — issue is downstream (account creation, ops team) | HIGH |
| `kyc_result_rejected` | "KYC rejected" / "account pending" | Explicit rejection by auditor — see `auditor_feedback`, `feedback.type`, `feedback.comment` | HIGH |
| `kyc_rejected` | "KYC rejected" / "account pending" | KYC rejected — may be early-stage rejection without full audit trail | HIGH |
| `kyc_result_partial_update` | "KYC pending" / "status unknown" | Partial processing — session not fully resolved; may need manual review | MEDIUM |
| `session_expired` | "link not working" / "session expired" | Customer didn't complete before link expiry | HIGH |
| `user_abandoned` | "KYC stuck" / "didn't finish" | Customer left mid-session — may retry or need a new link | MEDIUM |
| `waiting` | "KYC stuck" / "waiting forever" | Session active in queue — may indicate queue backlog or platform issue | MEDIUM |
| No session found | Any KYC complaint | No session exists for this phone number — KYC may not have been initiated | MEDIUM |

### Summary Step Mappings

From `overall_summary[]` in `summary_data`:

| Step `title` | `success: false` → Root Cause |
|:---|:---|
| `"Questions"` | Customer could not correctly answer identity questions |
| `"Selfie"` | Selfie capture failed — camera, lighting, or liveness issue |
| `"Pan Card"` | PAN capture failed — image quality, OCR failure, or validation failure |
| `"Aadhaar"` | Aadhaar verification failed |

### Feedback Type Mappings

From `feedback.type` (parsed from `feedback` JSON string):

| `feedback.type` | Meaning |
|:---|:---|
| `"Approve"` | Auditor approved the KYC session |
| `"Reject"` | Auditor rejected the KYC session |
| `"Reopen"` | Auditor reopened/escalated the session |

### Face Match Score

From `docs[].facematch_score.selfie_pan_match`:

| Score | Interpretation |
|:---|:---|
| ≥ 90 | Strong match — face match successful |
| 70–89 | Moderate match — borderline |
| < 70 | Poor match — likely rejection cause |

---

## 3. Evidence Bundle → Root Cause Engine Rules

```python
import json
from datetime import datetime

def analyze_unity_evidence(unity_bundle, ticket, uptime_evidence):
    """
    Map Unity evidence to root cause hypotheses.
    Returns: list of RCA hypothesis dicts sorted by confidence.
    """
    status = unity_bundle.get("session_status")
    feedback_type = unity_bundle.get("feedback_type")
    auditor_feedback = unity_bundle.get("auditor_feedback", "")
    overall_summary = unity_bundle.get("overall_summary", [])
    init_ts = unity_bundle.get("init_time", 0)
    ticket_ts = datetime.fromisoformat(ticket["created_at"]).timestamp()

    hypotheses = []

    # 1. KYC approved — issue is downstream
    if status == "kyc_result_approved":
        hypotheses.append({
            "hypothesis": "KYC_APPROVED_DOWNSTREAM_ISSUE",
            "confidence": 0.90,
            "detail": f"KYC session approved by {unity_bundle.get('auditor_name', 'auditor')}. Issue is not in video KYC.",
            "suggested_action": "Escalate to account opening team or bank ops"
        })

    # 2. KYC rejected — auditor decision
    elif status in ("kyc_result_rejected", "kyc_rejected"):
        comment = unity_bundle.get("feedback", {}).get("feedbackComment", "")
        hypotheses.append({
            "hypothesis": "KYC_REJECTED_BY_AUDITOR",
            "confidence": 0.88,
            "detail": f"KYC rejected. Auditor: {unity_bundle.get('auditor_name')}. Feedback: {auditor_feedback}. Comment: {comment}",
            "suggested_action": "Advise customer on rejection reason; initiate fresh KYC if eligible"
        })

    # 3. Session expired
    elif status == "session_expired":
        hypotheses.append({
            "hypothesis": "SESSION_LINK_EXPIRED",
            "confidence": 0.92,
            "detail": "KYC session link expired before customer completed",
            "suggested_action": "Generate a new VKYC link for the customer"
        })

    # 4. User abandoned
    elif status == "user_abandoned":
        hypotheses.append({
            "hypothesis": "USER_ABANDONED_SESSION",
            "confidence": 0.85,
            "detail": "Customer left the KYC session before completing",
            "suggested_action": "Ask customer to retry; check if platform was stable at that time"
        })

    # 5. Waiting (stuck in queue)
    elif status == "waiting":
        sms_down = uptime_evidence.get("vkyc_service_down", False)
        hypotheses.append({
            "hypothesis": "SESSION_STUCK_IN_QUEUE",
            "confidence": 0.80 if sms_down else 0.65,
            "detail": f"Session still in queue. Platform issue: {sms_down}",
            "suggested_action": "Check Uptime Kuma for queue/agent availability; may need manual escalation"
        })

    # 6. Session not found
    if not unity_bundle.get("session_found"):
        hypotheses.append({
            "hypothesis": "SESSION_NOT_FOUND",
            "confidence": 0.60,
            "detail": f"No KYC session found for phone number {unity_bundle.get('query_phone_number')}",
            "suggested_action": "Verify phone number; check if KYC has been initiated; ask customer for session link"
        })

    # 7. Journey step failures (from overall_summary)
    failed_steps = [s["title"] for s in overall_summary if not s.get("success", True)]
    for step in failed_steps:
        hypotheses.append({
            "hypothesis": f"JOURNEY_STEP_FAILED_{step.upper().replace(' ', '_')}",
            "confidence": 0.80,
            "detail": f"Journey step '{step}' failed during KYC session",
            "suggested_action": f"Investigate {step} failure — may be device, lighting, or document quality issue"
        })

    # 8. Platform failure cross-reference
    if uptime_evidence.get("vkyc_service_down"):
        hypotheses.append({
            "hypothesis": "PLATFORM_OUTAGE_AT_SESSION_TIME",
            "confidence": 0.88,
            "detail": "VKYC service was DOWN at/near ticket creation time per Uptime Kuma",
            "suggested_action": "Platform outage response — acknowledge, provide ETA, no retry until resolved"
        })

    return sorted(hypotheses, key=lambda x: x["confidence"], reverse=True)
```

---

## 4. Observation Generator — What to Include in Notes

### Include in private Freshdesk note

| Data point | Format |
|:---|:---|
| Session status | `KYC Session Status: kyc_result_approved (as of <datetime IST>)` |
| Session ID | `Session ID: 329c5f9e-9406-4f47-b717-5c87f04054d2` (for support engineer reference) |
| Session init time | `Session initiated: 13-Jul-2026 09:47 IST` |
| Auditor feedback | `Auditor decision: Approved by ext.harshada.kaphare@unitybank.co.in` |
| Failed journey steps | `Failed steps: Selfie, Pan Card` (if any) |
| Face match score | `Face match (selfie vs PAN): 98/100` |
| Platform cross-reference | `Uptime Kuma: VKYC service was DOWN at ticket time` (if applicable) |
| RCA hypothesis | Top-ranked hypothesis with confidence |
| Evidence source | `Source: Unity Admin Portal (vkyc360.unitybank.co.in) — queried 2026-07-13 09:01 IST` |

### Never include in notes

| Data | Reason |
|:---|:---|
| Full PAN number | PII — sensitive financial ID |
| Full Aadhaar number | PII — biometric ID |
| Full customer mobile number | PII — use last 4 digits only |
| Customer date of birth | PII |
| Video recording URLs | Media PII + signed URL may expire |
| Agent/auditor personal details (beyond name in context) | Internal staff PII |
| Unity portal credentials | Security |
| Customer location / GPS coordinates | PII |
| Customer IP address | PII |

---

## 5. Suggested Reply Templates

### When KYC is kyc_result_approved

```
Hi [Customer Name],

We checked your Video KYC session and it has been successfully completed and approved on [date].

Account activation typically takes [X] business days after KYC approval. If you haven't received an update by [estimated date], please contact us again with your application reference.

Regards,
KwikID Support
```

### When KYC is kyc_result_rejected

```
Hi [Customer Name],

We reviewed your Video KYC session. Unfortunately, the KYC could not be approved at this time.

[If feedback available]: The session was reviewed and could not be accepted due to [general reason — do not share PAN/Aadhaar specifics].

Please contact your relationship manager or visit a branch for assistance in completing your KYC.

Regards,
KwikID Support
```

### When session is session_expired

```
Hi [Customer Name],

Your KYC session link has expired. KYC session links are valid for a limited time from when they are generated.

Please contact us so we can arrange a fresh KYC session for you. Check your registered mobile number [ending in XXXX] for a new link.

Regards,
KwikID Support
```

### When session is user_abandoned

```
Hi [Customer Name],

We can see that a Video KYC session was initiated for your account, but it appears the session was not completed.

Please use the link sent to your registered mobile number to complete your Video KYC at your convenience. If the link has expired, please let us know and we will arrange a new one.

Regards,
KwikID Support
```

---

## 6. Loki Correlation

The `session_id` (UUID v4) from Unity is the primary correlator for Loki server logs:

```logql
{job="unity-vkyc"} |= "329c5f9e-9406-4f47-b717-5c87f04054d2"
{job="unity-vkyc"} |= "7045722923"
```

The `link_id` (12-char alphanumeric) may also appear in logs:
```logql
{job="unity-vkyc"} |= "sZWzqDt2oDNZ"
```

> Exact Loki job names for Unity VKYC service are to be confirmed during Loki discovery phase.
