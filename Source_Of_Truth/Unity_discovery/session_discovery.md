# Session Discovery — getAllUserSession Endpoint

**API Base URL**: `https://vkyc360.unitybank.co.in` (port 443, HTTPS)
**Endpoint**: `GET /api/v1/getAllUserSession/{domain}/{phone_number}`
**Source**: Confirmed from live API calls on 2026-07-13

---

## Endpoint Overview

This is the primary session list endpoint. Given a customer's phone number and domain, it returns all VKYC sessions ever created for that customer, ordered most recent first.

**Critical note**: Port 9090 is the React admin frontend only. All API calls go to port 443 (standard HTTPS). There is no URN concept in this system — the primary customer identifier is `phone_number`.

---

## Request

### URL Pattern

```
GET /api/v1/getAllUserSession/{domain}/{phone_number}
```

Both `{domain}` and `{phone_number}` are **path parameters**, not query parameters.

### Path Parameters

| Parameter | Type | Example | Notes |
|:---|:---|:---|:---|
| `domain` | string | `unity` | Always `unity` for this deployment |
| `phone_number` | string | `7045722923` | 10-digit Indian mobile number |

### Headers

| Header | Value | Notes |
|:---|:---|:---|
| `auth` | `<token>` | Custom auth header — NOT `Authorization: Bearer` |
| `Content-Type` | `application/json` | Standard |

### Example Request

```
GET /api/v1/getAllUserSession/unity/7045722923 HTTP/1.1
Host: vkyc360.unitybank.co.in
auth: eyJhbGciOi...
```

---

## Response

### Top-Level Shape

```json
{
  "session_count": 5,
  "session_list": [ ... ],
  "status_code": 200,
  "success": true
}
```

### Top-Level Fields

| Field | Type | Example | Notes |
|:---|:---|:---|:---|
| `session_count` | integer | `5` | Total number of sessions for this customer |
| `session_list` | array | `[...]` | Array of session objects; most recent first |
| `status_code` | integer | `200` | HTTP-mirrored status; always 200 on success |
| `success` | boolean | `true` | Always `true` on successful retrieval |

### Empty Result Behavior

When the phone number has no sessions (or is not found in the system), the response is:

```json
{
  "session_count": 0,
  "session_list": [],
  "status_code": 200,
  "success": true
}
```

This is **not** a 404. An empty `session_list` means the customer either never initiated VKYC or used a different phone number. The caller must handle `session_count == 0` explicitly.

---

## Session List Item Fields (All 76 Fields)

Each item in `session_list` is a session object with the following fields. Fields are listed alphabetically to match the confirmed canonical list.

> **Nullable fields**: `stage`, `stage1_valid`, `init_time`, `lang`, `queue`, `location`, and most timestamp fields can be `null`.
>
> **JSON string fields**: `extras`, `stage_data`, `location`, `feedback`, `captured_images` are JSON-encoded strings that must be parsed with `json.loads()` before accessing nested values.

| Field Name | Type | Example Value | Nullable | Notes |
|:---|:---|:---|:---|:---|
| `IS_VIDO_UPLOADED` | boolean | `true` | No | Whether video was uploaded; non-standard casing (all caps prefix) |
| `aadhaar_processing_status` | string | `"SUCCESS"` | Yes | Status of Aadhaar XML processing |
| `aadhaar_processing_task_id` | string | `"task_abc123"` | Yes | Background task ID for Aadhaar processing |
| `aadhaar_request_time` | string (datetime) | `"2026-07-13T10:30:00"` | Yes | When Aadhaar request was made |
| `aadhaar_url` | string (URL) | `"https://..."` | Yes | Aadhaar document storage URL — PII |
| `aadhaar_xml` | string (URL) | `"https://..."` | Yes | Aadhaar XML download URL — PII |
| `aadhaarback_url` | string (URL) | `"https://..."` | Yes | Aadhaar back image URL — PII |
| `aadhaarpdf_url` | string (URL) | `"https://..."` | Yes | Aadhaar PDF download URL — PII |
| `agent_assignment_time` | string (datetime) | `"2026-07-13T10:35:00"` | Yes | When an agent was assigned to this session |
| `agent_id` | string (email) | `"Vidya.Rathod@unitybank.co.in"` | Yes | Agent's email address |
| `agent_region` | string | `"MUMBAI"` | Yes | Geographic region of the assigned agent |
| `agent_screen_url` | string (URL) | `"https://..."` | Yes | Agent screen recording URL — PII media |
| `agent_tl` | string (email) | `"tl@unitybank.co.in"` | Yes | Agent's team leader email |
| `agent_video_url` | string (URL) | `"https://..."` | Yes | Agent-side video recording URL — PII media |
| `app_version_number` | string | `"NA"` | Yes | Customer app version; typically `"NA"` |
| `audit_end_time` | string (datetime) | `"2026-07-13T11:05:00"` | Yes | When audit review was completed |
| `audit_id` | string | `"audit_xyz789"` | Yes | Unique audit record identifier |
| `audit_init_time` | string (datetime) | `"2026-07-13T11:00:00"` | Yes | When auditor began reviewing the session |
| `audit_lock` | boolean | `false` | Yes | Whether the audit record is locked |
| `audit_result` | string | `"1"` | Yes | `"1"` = approved, `"0"` = rejected |
| `auditor_fdbk_code` | string | `"A01"` | Yes | Coded feedback category from auditor |
| `auditor_feedback` | string | `"Approved"` | Yes | Human-readable auditor decision text |
| `auditor_name` | string | `"auditor@unitybank.co.in"` | Yes | Auditor's identifier or email |
| `avg_network_download_speed` | integer | `1594` | Yes | Customer network download speed in Kbps |
| `avg_network_upload_speed` | integer | `993` | Yes | Customer network upload speed in Kbps |
| `captured_images` | string (JSON) | `'["selfie", "pan"]'` | Yes | JSON string listing captured image types; parse before use. Known values: `"selfie"`, `"pan"`, `"signature"` |
| `client_name` | string | `"unity"` | No | Always `"unity"` for this deployment |
| `customer_IP` | string | `"103.x.x.x"` | Yes | Customer's IP address — partial PII |
| `device_id` | string | `"3jdg81"` | Yes | 6-character alphanumeric device identifier |
| `end_time` | string (datetime) | `"2026-07-13T10:55:00"` | Yes | When the VKYC session ended |
| `extras` | string (JSON) | `'{"product_code":"ONEFIN",...}'` | Yes | JSON string containing full stage_1_data; must parse — see structure below |
| `feedback` | string (JSON) | `'{"type":"Approve","comment":"","feedbackComment":"ap available"}'` | Yes | JSON string; parse to get feedback object |
| `init_time` | string (datetime) | `"2026-07-13T10:20:00"` | Yes | Initial session creation timestamp; can be null |
| `inserted_in_queue_at` | string | `"init_api"` | Yes | Marker indicating how/when customer entered queue |
| `lang` | string | `"hi"` | Yes | Language code: `"hi"`, `"en"`, or null |
| `last_active_timestamp` | string (datetime) | `"2026-07-13T10:50:00"` | Yes | Last recorded customer activity time |
| `latest_init_time` | string (datetime) | `"2026-07-13T10:22:00"` | Yes | Most recent init time (updated on re-initiation) |
| `link_id` | string | `"sZWzqDt2oDNZ"` | No | 12-character alphanumeric session link identifier |
| `ll` | string | `"19.145,73.249"` | Yes | Compact lat/lng string |
| `location` | string (JSON) | `'{"lat":19.14,"lng":73.24}'` | Yes | JSON string with geolocation; parse before use |
| `number_of_videos_uploaded` | string | `"3"` | Yes | Count of video segments uploaded — stored as string, not integer |
| `otp` | string | `"123456"` | Yes | OTP value — PII; never log |
| `otp_attempts` | integer | `1` | Yes | Number of OTP attempts made by customer |
| `otp_attempts_timestamp` | string (datetime) | `"2026-07-13T10:21:00"` | Yes | Timestamp of last OTP attempt |
| `pan_request_time` | string (datetime) | `"2026-07-13T10:40:00"` | Yes | When PAN card capture was requested |
| `pan_url` | string (URL) | `"https://..."` | Yes | PAN card image URL — PII |
| `phone_number` | string | `"7045722923"` | No | 10-digit mobile number; primary customer identifier |
| `productCode` | string | `"ONEFIN"` | No | Product/partner code. Known values: `"ONEFIN"`, `"FINTECHFARM"`, `"PAISABAZAR"` |
| `queue` | string | `"free"` | Yes | Queue type; can be null if session bypassed queue |
| `queue_id` | string | `"unity_free_hi_vkyc"` | Yes | Specific queue identifier string |
| `queue_mode` | string | `"unity_free_hi_vkyc"` | Yes | Matches `queue_id` in observed data |
| `queue_position_at_init` | integer | `3` | Yes | Customer's position in queue when they joined |
| `removed_from_queue_timestamp` | string (datetime) | `"2026-07-13T10:34:00"` | Yes | When customer was dequeued (agent picked up) |
| `selfie_request_time` | string (datetime) | `"2026-07-13T10:38:00"` | Yes | When selfie capture was requested |
| `selfie_url` | string (URL) | `"https://..."` | Yes | Selfie image URL — PII |
| `session_id` | string (UUID v4) | `"329c5f9e-9406-4f47-b717-5c87f04054d2"` | No | Primary session identifier in UUID v4 format |
| `session_status` | string (enum) | `"kyc_result_approved"` | No | Current session status; see enum section below |
| `session_type` | string | `"VKYC"` | No | Always `"VKYC"` in this system |
| `signature_request_time` | string (datetime) | `"2026-07-13T10:42:00"` | Yes | When signature capture was requested |
| `signature_url` | string (URL) | `"https://..."` | Yes | Customer signature image URL — PII |
| `stage` | string | `"Stage1"` | Yes | Current or completed stage; can be null |
| `stage1_valid` | boolean | `true` | Yes | Whether Stage 1 passed validation; can be null |
| `stage_data` | string (JSON) | `'{"stage1":...}'` | Yes | JSON string with detailed stage progression; parse before use |
| `start_time` | string (datetime) | `"2026-07-13T10:30:00"` | Yes | When VKYC video call actually started |
| `summary_data` | object or null | `{...}` | Yes | Structured session summary; richer in `get_details` response |
| `summary_json_url` | string (URL) | `"https://..."` | Yes | URL to full session summary JSON artifact |
| `summary_pdf_url` | string (URL) | `"https://..."` | Yes | URL to session summary PDF artifact |
| `undefined_url` | string | `null` | Yes | Legacy field; always null in observed data |
| `user_ack` | boolean | `true` | Yes | Whether customer acknowledged consent/terms |
| `user_id` | string | `"7045722923"` | No | Same value as `phone_number` in all observed data |
| `user_video_url` | string (URL) | `"https://..."` | Yes | Customer-side video recording URL — PII media |
| `video_duration` | string | `"239.64"` | Yes | Total video duration in seconds; stored as string not float |
| `vkyc_start_time` | string (datetime) | `"2026-07-13T10:33:00"` | Yes | When the VKYC video call began |
| `zip_url` | string (URL) | `"https://..."` | Yes | URL to zipped bundle of session artifacts |

---

## session_status Enum (All 7 Values)

Confirmed from live API data on 2026-07-13.

| Value | Meaning | Terminal? |
|:---|:---|:---|
| `kyc_result_approved` | VKYC completed and auditor approved the session | Yes — positive outcome |
| `kyc_result_rejected` | VKYC completed and auditor rejected the session | Yes — negative outcome |
| `kyc_rejected` | KYC rejected by auditor decision | Yes — negative outcome |
| `kyc_result_partial_update` | Partial update applied to the session | No — intermediate state |
| `session_expired` | Customer's VKYC link expired before completion | Yes — timeout outcome |
| `user_abandoned` | Customer left mid-session without completing | Yes — dropout outcome |
| `waiting` | Customer is in queue, waiting for an agent | No — active/pending state |

---

## JSON String Fields — Parse Before Use

These 5 fields contain JSON-encoded strings. Use `json.loads()` before accessing nested data.

| Field | Example Raw Value | What You Get After Parsing |
|:---|:---|:---|
| `extras` | `'{"product_code":"ONEFIN","stage_1_data":{...}}'` | Full stage data hierarchy including AML, Aadhaar, NSDL checks, personal details |
| `stage_data` | `'{"stage1":{...}}'` | Stage progression object |
| `location` | `'{"lat":19.145,"lng":73.249}'` | Geolocation object |
| `feedback` | `'{"type":"Approve","comment":"","feedbackComment":"ap available"}'` | Feedback object with type, comment, feedbackComment |
| `captured_images` | `'["selfie", "pan"]'` | Array of captured image type names |

**feedback.type values**: `"Approve"`, `"Reject"`, `"Reopen"`

---

## Multi-Session Scenarios

A customer may have multiple sessions across different attempts. `getAllUserSession` returns all of them with most recent first.

### Session Selection Priority

1. **Default**: Use `session_list[0]` — the most recent session.
2. **Status-based selection**: If the most recent session is in a non-terminal state but the ticket implies a completed/rejected session, scan the list using this priority order:
   - `kyc_result_approved` (highest priority — positive terminal)
   - `kyc_result_rejected`
   - `kyc_rejected`
   - `kyc_result_partial_update`
   - `session_expired`
   - `user_abandoned`
   - `waiting` (lowest — non-terminal)
3. **Date correlation**: If the Freshdesk ticket contains a date, match against `init_time` or `start_time` to locate the session the customer is describing.

---

## Authentication Errors

| Scenario | HTTP Status | Response Body |
|:---|:---|:---|
| Missing `auth` header | 401 | `{"message": "Missing authorization header"}` |
| Invalid or expired token | 401 | `{"message": "Token is invalid"}` |
