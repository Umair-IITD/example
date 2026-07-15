# API Reference — Unity Admin Portal

**Base URL**: `https://vkyc360.unitybank.co.in` (port 443)  
**Admin Portal**: `https://vkyc360.unitybank.co.in:9090` (React SPA — separate from API)  
**Auth**: `auth: <token>` header (see authentication.md)  
**Source**: Live API confirmed — 2026-07-13

---

## Endpoint Catalog

| # | Method | Path | Auth | Purpose |
|:--|:--|:--|:--|:--|
| 1 | POST | `/v1/agent/generate_token` | No | Get JWT token |
| 2 | GET | `/api/v1/getAllUserSession/{domain}/{phone_number}` | Yes | Session list for customer |
| 3 | GET | `/v1/session/get_details/{session_id}` | Yes | Full session detail |
| 4 | GET | `/v1/health` | No | Health check |
| 5 | GET | `/api/v1/health` | No | Health check (alt path) |
| 6 | POST | `/v1/agent/sendLink/` | Yes | Send VKYC link **(MUTATION — READ-ONLY CONSTRAINT: DO NOT USE)** |

---

## 1. POST /v1/agent/generate_token

Get a JWT for API authentication.

**Request:**
```
POST https://vkyc360.unitybank.co.in/v1/agent/generate_token
Content-Type: application/json

{
  "username": "unity",
  "password": "unity"
}
```

**Response (200 OK):**
```json
{
  "Token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
  "status_code": 200,
  "success": true
}
```

**Notes:**
- `Token` — capital T
- Token is valid for 12 hours (43200 seconds)
- Algorithm: HS256

---

## 2. GET /api/v1/getAllUserSession/{domain}/{phone_number}

Get all VKYC sessions for a customer by phone number.

**Request:**
```
GET https://vkyc360.unitybank.co.in/api/v1/getAllUserSession/unity/{phone_number}
auth: <token>
```

**Path Parameters:**
- `domain` — always `unity` for Unity Bank
- `phone_number` — 10-digit customer mobile number (same as `user_id`)

**Response (200 OK):**
```json
{
  "session_count": 5,
  "session_list": [
    {
      "session_id": "0d266e46-bc27-413f-ba30-b9335ccbc7bf",
      "session_status": "session_expired",
      "session_type": "VKYC",
      "phone_number": "7045722923",
      "user_id": "7045722923",
      "productCode": "ONEFIN",
      "stage": "Stage1",
      "stage1_valid": true,
      "init_time": 1781838257,
      "start_time": "1781838234.5383844",
      "end_time": "1781839134",
      "lang": "hi",
      "queue": "free",
      "queue_id": "unity_free_hi_vkyc",
      "link_id": "n5A2RR2WdCiP",
      "device_id": "3jdg81",
      "client_name": "unity",
      "inserted_in_queue_at": "init_api",
      "queue_position_at_init": 5,
      "IS_VIDO_UPLOADED": true,
      "app_version_number": "NA",
      "customer_IP": "172.29.22.167",
      "last_active_timestamp": "1781839570.3054519",
      "latest_init_time": "1781838257.492358",
      "agent_assignment_time": "1781838234.5383856",
      "aadhaar_xml": "https://vkyc360.unitybank.co.in/v1/download_content/...",
      "aadhaarpdf_url": "https://vkyc360.unitybank.co.in/v1/download_content/...",
      "extras": "{...json string...}",
      "stage_data": "{...json string...}",
      "location": "{\"address\":\"...\",\"latitude\":19.145,\"longitude\":73.249}"
    }
  ],
  "status_code": 200,
  "success": true
}
```

**session_status enum values (all confirmed):**

| Value | Meaning |
|:---|:---|
| `kyc_result_approved` | VKYC completed and auditor approved |
| `kyc_result_rejected` | VKYC completed and auditor rejected |
| `kyc_rejected` | KYC rejected (auditor decision) |
| `kyc_result_partial_update` | Partial update — session partially processed |
| `session_expired` | Session link expired before completion |
| `user_abandoned` | Customer left mid-session |
| `waiting` | Customer in queue, waiting for agent |

**Empty result (phone number not found):**
```json
{
  "session_count": 0,
  "session_list": [],
  "status_code": 200,
  "success": true
}
```

**Notes:**
- Results are ordered by recency (most recent first, based on observed data)
- No pagination parameters — all sessions returned in one response
- `extras` and `stage_data` are JSON-encoded strings, not objects — parse with `json.loads()`
- `session_status` provides the final outcome; see field_dictionary.md for interpretation

---

## 3. GET /v1/session/get_details/{session_id}

Get full detail for one VKYC session by its UUID.

**Request:**
```
GET https://vkyc360.unitybank.co.in/v1/session/get_details/{session_id}
auth: <token>
```

**Path Parameters:**
- `session_id` — UUID v4 (e.g., `329c5f9e-9406-4f47-b717-5c87f04054d2`)

**Response (200 OK):**
```json
{
  "session_data": {
    "session_id": "329c5f9e-9406-4f47-b717-5c87f04054d2",
    "session_status": "kyc_result_approved",
    "session_type": "VKYC",
    "phone_number": "7045722923",
    "user_id": "7045722923",
    "productCode": "ONEFIN",
    "client_name": "unity",
    "lang": "hi",
    "queue": "free",
    "queue_id": "unity_free_hi_vkyc",
    "queue_mode": "unity_free_hi_vkyc",
    "link_id": "sZWzqDt2oDNZ",
    "device_id": "3jdg81",
    "stage": "Stage1",
    "stage1_valid": true,
    "IS_VIDO_UPLOADED": true,
    "app_version_number": "NA",
    "init_time": "1781840077",
    "start_time": "1781840019.4368567",
    "end_time": "1781841062.598181",
    "vkyc_start_time": "1781840814.1674924",
    "last_active_timestamp": "1781841065.4184287",
    "latest_init_time": "1781840077.6513968",
    "agent_assignment_time": "1781840778.9274075",
    "removed_from_queue_timestamp": "1781840778.913966",
    "queue_position_at_init": "6",
    "inserted_in_queue_at": "init_api",
    "customer_IP": "172.29.22.167,...",
    "location": "{\"address\":\"...\",\"latitude\":19.145,\"longitude\":73.249}",
    "agent_id": "Vidya.Rathod@unitybank.co.in",
    "agent_region": "",
    "agent_tl": "",
    "agent_video_url": "https://vkyc360.unitybank.co.in/v1/download_content/.../agent.webm",
    "agent_screen_url": "https://vkyc360.unitybank.co.in/v1/download_content/.../agent_video_screen.webm",
    "user_video_url": "https://vkyc360.unitybank.co.in/v1/download_content/.../user.webm",
    "video_duration": "239.64",
    "number_of_videos_uploaded": "3",
    "IS_VIDO_UPLOADED": true,
    "selfie_url": "https://vkyc360.unitybank.co.in/v1/download_content/.../selfie.jpg",
    "selfie_request_time": "0",
    "pan_url": "https://vkyc360.unitybank.co.in/v1/download_content/.../pan_original.jpg",
    "pan_request_time": "0",
    "aadhaar_xml": "https://vkyc360.unitybank.co.in/v1/download_content/..._aadhaar.xml",
    "aadhaarpdf_url": "https://vkyc360.unitybank.co.in/v1/download_content/..._aadhaar_redacted.pdf",
    "summary_pdf_url": "https://vkyc360.unitybank.co.in/v1/download_content/.../summary.pdf",
    "summary_json_url": "https://vkyc360.unitybank.co.in/v1/download_content/.../summary.json",
    "zip_url": "https://vkyc360.unitybank.co.in/v1/download_content/.../package.zip",
    "audit_id": "d335ce2d-70b7-4a9a-9134-9fda2f77d5a5",
    "audit_result": "1",
    "audit_lock": true,
    "audit_init_time": "1781841826.9012215",
    "audit_end_time": "1781841915",
    "auditor_name": "ext.harshada.kaphare@unitybank.co.in",
    "auditor_feedback": "Approved",
    "auditor_fdbk_code": "None",
    "feedback": "{\"type\":\"Approve\",\"comment\":\"\",\"feedbackComment\":\"ap available\"}",
    "captured_images": "[\"selfie\", \"pan\"]",
    "summary_data": {
      "agent_id": "Vidya.Rathod@unitybank.co.in",
      "client_name": "unity",
      "session_id": "329c5f9e-9406-4f47-b717-5c87f04054d2",
      "user_id": "7045722923",
      "journey_id": null,
      "journey_sequence": "ID6|ID1|ID2",
      "lat": 19.145840259385306,
      "lng": 73.24920689558905,
      "docs": [
        {
          "name": "Pan Card",
          "details": {
            "dob": "21/06/1997",
            "fathers_name": "ELISH SAMUEL",
            "name": "JOYBLESSY ELISH SAMUEL",
            "pa_number": "GFDPS8190M"
          },
          "facematch_score": {"selfie_pan_match": 98},
          "front_url": "https://...",
          "validator": {"raw_nsdl": {"NSDLResponse": {...}}}
        }
      ],
      "overall_summary": [
        {"success": true, "title": "Questions"},
        {"success": true, "title": "Selfie"},
        {"success": true, "title": "Pan Card"}
      ],
      "qna": [
        {"q": "What is your Date Of Birth?", "a": "Correct"},
        {"q": "Read the number:6384", "a": "Correct"},
        {"q": "Your Occupation is:", "a": "Confirmed"}
      ]
    },
    "extras": "{...json string...}",
    "stage_data": "{...json string...}"
  },
  "status": "..."
}
```

**Invalid session ID (HTTP 400):**
```json
{
  "e": "list index out of range",
  "msg": "Invalid session id",
  "status": 400
}
```

---

## 4 & 5. GET /v1/health (and /api/v1/health)

Health check endpoints. No auth required.

**Response (200 OK):**
```
ok
```

Plain text string `ok`. Used for uptime monitoring.

---

## 6. POST /v1/agent/sendLink/ — MUTATION (READ-ONLY CONSTRAINT)

> **DO NOT USE**. This endpoint sends a new VKYC link to a customer and creates a session. It is a state-changing mutation. The AI investigation pipeline must never call it.  
> Documented here for completeness only.

---

## 7. GET /v1/download_content/{bucket}/{path}

File download endpoint (confirmed from URL patterns in session data).

```
GET https://vkyc360.unitybank.co.in/v1/download_content/kwikid-prod/videokyc/{type}/unity/{phone}/{filename}
auth: <token>
```

Used to download:
- Aadhaar XML: `.../aadhaar/unity/{phone}/{phone}_aadhaar.xml`
- Aadhaar PDF (redacted): `.../aadhaar/unity/{phone}/{phone}_aadhaar_redacted.pdf`
- Agent video: `.../videos/unity/{phone}/{session_id}/agent.webm`
- Agent screen: `.../videos/unity/{phone}/{session_id}/agent_video_screen.webm`
- Customer video: `.../videos/unity/{phone}/{session_id}/user.webm`
- Selfie: `.../images/unity/{phone}/{session_id}/{phone}_selfie.jpg`
- PAN image: `.../images/unity/{phone}/{session_id}/{phone}_pan_original.jpg`
- Summary PDF: `.../reports/unity/{phone}/{session_id}/summary.pdf`
- ZIP package: `.../reports/unity/{phone}/{session_id}/package.zip`

> **Note**: The `auth` token is required for download. URLs are direct (not pre-signed S3), authenticated by the `auth` header.

---

## Error Response Summary

| HTTP Status | Scenario | Response Body |
|:---|:---|:---|
| 200 | Success | Varies by endpoint |
| 400 | Invalid session ID | `{"e":"list index out of range","msg":"Invalid session id","status":400}` |
| 401 | Missing auth header | `{"message":"Missing authorization header"}` |
| 401 | Invalid/expired token | `{"message":"Token is invalid"}` |
| 404 | Unknown path | (varies) |
| 405 | Wrong method (e.g., GET on POST endpoint) | (varies) |
