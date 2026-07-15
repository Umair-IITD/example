# Entity Model — Unity Admin Portal

**API Base URL**: `https://vkyc360.unitybank.co.in` (port 443)  
**Source**: Live API responses confirmed 2026-07-13 — `getAllUserSession` (88 sessions) + `get_details`  
**Purpose**: Document the complete data model of every entity the portal manages

> All field names in this document are confirmed from live API responses. No ⏳ speculation.

---

## Entity Relationship Overview

```
Customer (identified by phone_number)
  └── has many → KYC Sessions (session_id: UUID v4)
        └── has one → Audit Record (audit_id, auditor_name, audit_result)
        └── has one → Video Package (agent_video_url, user_video_url, agent_screen_url)
        └── has one → Documents (selfie_url, pan_url, aadhaar_xml, aadhaarpdf_url)
        └── has one → Summary (summary_data: docs, qna, overall_summary)
        └── has one → Report Package (summary_pdf_url, summary_json_url, zip_url)
        └── belongs to → Agent (agent_id: email address)
        └── has one → Location (location: JSON string with address, lat, lng)
        └── has one → Extras (extras: JSON string — supplemental data)
        └── has one → Stage Data (stage_data: JSON string — journey step data)
```

---

## 1. Customer Entity

A customer is identified by their **phone number** — the 10-digit mobile number used during KYC initiation.

> **CRITICAL**: There is NO URN (Unique Reference Number) field in this system. The primary customer identifier is `phone_number`. Any reference to URN in prior documentation was incorrect.

| Field | Type | Source | Example | Notes |
|:---|:---|:---|:---|:---|
| `phone_number` | string | Session fields | `"7045722923"` | **Primary identifier** — 10-digit mobile number |
| `user_id` | string | Session fields | `"7045722923"` | Always equal to `phone_number` in confirmed data |
| `customer_IP` | string | get_details | `"172.29.22.167,..."` | May be comma-separated list (multiple IPs) |
| `location` | string (JSON) | Session fields | `"{\"address\":\"...\",\"latitude\":19.145,\"longitude\":73.249}"` | JSON-encoded string — parse with json.loads() |

Customer PII is embedded in session fields. There is no separate `/customer` endpoint — all customer data comes from session records.

---

## 2. KYC Session Entity

The central object. One customer (phone_number) may have **multiple sessions** across time.

### Core Identity

| Field | Type | Example | Nullable | Notes |
|:---|:---|:---|:---:|:---|
| `session_id` | string (UUID v4) | `"329c5f9e-9406-4f47-b717-5c87f04054d2"` | No | **Secondary lookup key** — passed to get_details |
| `session_type` | string | `"VKYC"` | No | Always `"VKYC"` in all observed data |
| `client_name` | string | `"unity"` | No | Always `"unity"` for Unity Bank deployment |
| `productCode` | string | `"ONEFIN"` | No | Product identifier (e.g. ONEFIN) |
| `phone_number` | string | `"7045722923"` | No | 10-digit mobile — links session to customer |
| `user_id` | string | `"7045722923"` | No | Same as phone_number |
| `link_id` | string | `"n5A2RR2WdCiP"` | No | 12-char alphanumeric — used in VKYC invite link |
| `device_id` | string | `"3jdg81"` | No | Device identifier |
| `lang` | string | `"hi"` | No | Language (`"hi"` = Hindi, `"en"` = English) |

### Session Status

| Field | Type | Example | Notes |
|:---|:---|:---|:---|
| `session_status` | string (enum) | `"kyc_result_approved"` | See §3 — 7 confirmed values |
| `stage` | string | `"Stage1"` | Journey stage identifier |
| `stage1_valid` | boolean | `true` | Whether Stage 1 was completed |

### Queue / Routing

| Field | Type | Example | Notes |
|:---|:---|:---|:---|
| `queue` | string | `"free"` | Queue tier — `"free"` observed |
| `queue_id` | string | `"unity_free_hi_vkyc"` | Full queue identifier |
| `queue_mode` | string | `"unity_free_hi_vkyc"` | In get_details; same as queue_id |
| `queue_position_at_init` | integer/string | `5` or `"6"` | Position when joined queue (integer in getAllUserSession, string in get_details) |
| `inserted_in_queue_at` | string | `"init_api"` | How the session entered queue |

### Timestamps

All timestamps are Unix epoch (seconds). Some as integers, some as float strings.

| Field | Type | Example | Notes |
|:---|:---|:---|:---|
| `init_time` | integer / string | `1781838257` | When session was initialised |
| `latest_init_time` | string (float) | `"1781838257.492358"` | High-precision init time |
| `start_time` | string (float) | `"1781838234.5383844"` | When session started |
| `end_time` | string (integer) | `"1781839134"` | When session ended |
| `vkyc_start_time` | string (float) | `"1781840814.1674924"` | When video call began (get_details only) |
| `last_active_timestamp` | string (float) | `"1781839570.3054519"` | Last activity time |
| `agent_assignment_time` | string (float) | `"1781838234.5383856"` | When agent joined session |
| `removed_from_queue_timestamp` | string (float) | `"1781840778.913966"` | When removed from queue (get_details only) |

### Video / Media

| Field | Type | Example | Nullable | Notes |
|:---|:---|:---|:---:|:---|
| `IS_VIDO_UPLOADED` | boolean | `true` | No | Whether video was uploaded (note typo: VIDO not VIDEO) |
| `number_of_videos_uploaded` | string | `"3"` | get_details | Count of video files |
| `video_duration` | string | `"239.64"` | get_details | Duration in seconds |
| `agent_video_url` | string (URL) | `"https://vkyc360.../agent.webm"` | get_details | Agent camera recording |
| `agent_screen_url` | string (URL) | `"https://vkyc360.../agent_video_screen.webm"` | get_details | Agent screen recording |
| `user_video_url` | string (URL) | `"https://vkyc360.../user.webm"` | get_details | Customer camera recording |
| `selfie_url` | string (URL) | `"https://vkyc360.../selfie.jpg"` | get_details | Selfie captured during session |
| `selfie_request_time` | string | `"0"` | get_details | Timestamp when selfie was requested |
| `pan_url` | string (URL) | `"https://vkyc360.../pan_original.jpg"` | get_details | PAN card image |
| `pan_request_time` | string | `"0"` | get_details | Timestamp when PAN was requested |
| `captured_images` | string (JSON array) | `"[\"selfie\", \"pan\"]"` | get_details | List of image types captured |

### Documents (URL references)

| Field | Type | Example | Notes |
|:---|:---|:---|:---|
| `aadhaar_xml` | string (URL) | `"https://vkyc360.../aadhaar.xml"` | Aadhaar XML data from UIDAI |
| `aadhaarpdf_url` | string (URL) | `"https://vkyc360.../aadhaar_redacted.pdf"` | Redacted Aadhaar PDF |
| `summary_pdf_url` | string (URL) | `"https://vkyc360.../summary.pdf"` | Session summary PDF (get_details only) |
| `summary_json_url` | string (URL) | `"https://vkyc360.../summary.json"` | Session summary JSON (get_details only) |
| `zip_url` | string (URL) | `"https://vkyc360.../package.zip"` | Full session ZIP archive (get_details only) |

All URLs follow pattern: `GET /v1/download_content/{bucket}/{path}` — auth header required.

### Agent

| Field | Type | Example | Notes |
|:---|:---|:---|:---|
| `agent_id` | string | `"Vidya.Rathod@unitybank.co.in"` | Agent's email address (get_details only) |
| `agent_region` | string | `""` | Agent's region (may be empty) |
| `agent_tl` | string | `""` | Agent's team lead (may be empty) |

### Misc

| Field | Type | Example | Notes |
|:---|:---|:---|:---|
| `app_version_number` | string | `"NA"` | Customer app version (`"NA"` if not applicable) |
| `extras` | string (JSON) | `"{...}"` | JSON-encoded string — parse with json.loads() |
| `stage_data` | string (JSON) | `"{...}"` | JSON-encoded string — parse with json.loads() |
| `feedback` | string (JSON) | `"{\"type\":\"Approve\",\"comment\":\"\",\"feedbackComment\":\"ap available\"}"` | JSON-encoded auditor feedback (get_details only) |

---

## 3. Session Status Enum (Confirmed)

All 7 values observed from 88 live sessions. No other values seen.

| Value | Terminal? | Meaning | AI interpretation |
|:---|:---:|:---|:---|
| `kyc_result_approved` | ✅ Yes | VKYC completed — auditor approved | KYC successful; downstream issues unrelated to VKYC |
| `kyc_result_rejected` | ✅ Yes | VKYC completed — auditor rejected | Explicit rejection — see `auditor_feedback` and `feedback` JSON |
| `kyc_rejected` | ✅ Yes | KYC rejected (auditor decision) | Rejection without full result — may be early-stage |
| `kyc_result_partial_update` | ⚠️ Maybe | Partial update — session partially processed | Edge case; neither fully approved nor rejected |
| `session_expired` | ✅ Yes | Session link expired | Customer didn't complete before link expiry |
| `user_abandoned` | ✅ Yes | Customer left mid-session | Customer dropped out — may retry |
| `waiting` | ❌ No | In queue, waiting for agent | Session active — customer waiting |

---

## 4. Audit Entity (get_details only)

Present only for sessions that reached the audit stage (approved/rejected sessions).

| Field | Type | Example | Notes |
|:---|:---|:---|:---|
| `audit_id` | string (UUID) | `"d335ce2d-70b7-4a9a-9134-9fda2f77d5a5"` | Unique audit record ID |
| `audit_result` | string | `"1"` | `"1"` = approved; other values TBD |
| `audit_lock` | boolean | `true` | Whether audit is locked/finalised |
| `audit_init_time` | string (float) | `"1781841826.9012215"` | When auditor started review |
| `audit_end_time` | string (integer) | `"1781841915"` | When auditor submitted decision |
| `auditor_name` | string | `"ext.harshada.kaphare@unitybank.co.in"` | Auditor's email address |
| `auditor_feedback` | string | `"Approved"` | Human-readable auditor decision |
| `auditor_fdbk_code` | string | `"None"` | Auditor feedback code (can be `"None"` as string) |
| `feedback` | string (JSON) | `"{\"type\":\"Approve\",\"comment\":\"\",\"feedbackComment\":\"ap available\"}"` | Full feedback JSON — parse with json.loads() |

### `feedback` JSON Structure (parsed)

```json
{
  "type": "Approve",         // "Approve" | "Reject" | "Reopen"
  "comment": "",             // Auditor comment (free text, may be empty)
  "feedbackComment": "ap available"  // Short feedback note
}
```

---

## 5. Summary Data Entity (get_details only)

The `summary_data` field in `get_details` is a rich object (NOT a JSON string — it's already parsed).

```json
{
  "agent_id": "Vidya.Rathod@unitybank.co.in",
  "client_name": "unity",
  "session_id": "329c5f9e-9406-4f47-b717-5c87f04054d2",
  "user_id": "7045722923",
  "journey_id": null,
  "journey_sequence": "ID6|ID1|ID2",
  "lat": 19.145840259385306,
  "lng": 73.24920689558905,
  "docs": [ ... ],
  "overall_summary": [ ... ],
  "qna": [ ... ]
}
```

### summary_data.docs[] — Document Verification Results

Each entry represents one document type verified during the session:

```json
{
  "name": "Pan Card",
  "details": {
    "dob": "21/06/1997",
    "fathers_name": "ELISH SAMUEL",
    "name": "JOYBLESSY ELISH SAMUEL",
    "pa_number": "GFDPS8190M"
  },
  "facematch_score": {
    "selfie_pan_match": 98
  },
  "front_url": "https://...",
  "validator": {
    "raw_nsdl": { "NSDLResponse": { ... } }
  }
}
```

| Field | Notes |
|:---|:---|
| `name` | Document type — `"Pan Card"`, `"Aadhaar"`, etc. |
| `details` | Extracted OCR data — varies by document type |
| `facematch_score` | Face comparison scores (e.g. selfie vs PAN) |
| `front_url` | URL to front image of the document |
| `validator` | Raw validation API response (e.g. NSDL for PAN) |

### summary_data.overall_summary[] — Journey Step Results

```json
[
  {"success": true,  "title": "Questions"},
  {"success": true,  "title": "Selfie"},
  {"success": true,  "title": "Pan Card"}
]
```

Each entry: `{"success": boolean, "title": string}` — one per journey step.

### summary_data.qna[] — Customer Q&A During Session

```json
[
  {"q": "What is your Date Of Birth?", "a": "Correct"},
  {"q": "Read the number:6384",        "a": "Correct"},
  {"q": "Your Occupation is:",         "a": "Confirmed"}
]
```

### summary_data — Location and Journey

| Field | Type | Example | Notes |
|:---|:---|:---|:---|
| `lat` | float | `19.145840259385306` | Customer GPS latitude |
| `lng` | float | `73.24920689558905` | Customer GPS longitude |
| `journey_id` | string / null | `null` | Journey template ID (may be null) |
| `journey_sequence` | string | `"ID6|ID1|ID2"` | Pipe-delimited step sequence |

---

## 6. Agent Entity

Agents are identified by email address. No separate agent lookup endpoint confirmed.

| Field | Source | Example | Notes |
|:---|:---|:---|:---|
| `agent_id` | get_details | `"Vidya.Rathod@unitybank.co.in"` | Agent email — VKYC agent who handled session |
| `auditor_name` | get_details | `"ext.harshada.kaphare@unitybank.co.in"` | Auditor email — reviewed the session |

`ext.` prefix on auditor email suggests external auditor account type.

---

## Confirmed Absence

The following fields do NOT exist in this system (confirmed from live responses):

| Field | Status | Notes |
|:---|:---|:---|
| `urn` | ❌ Does not exist | No URN field anywhere in API responses |
| `journey_status` | ❌ Does not exist | No granular journey step field — use `stage`, `overall_summary`, `session_status` |
| `failure_reason` | ❌ Does not exist | No dedicated failure_reason field — use `session_status`, `auditor_feedback`, `feedback.type` |
| `otp_verified` | ❌ Does not exist | OTP result not a top-level field — may appear in qna[] or stage_data |
| `liveliness_result` | ❌ Does not exist | No top-level liveliness field — check overall_summary[] |
| `ocr_status` | ❌ Does not exist | No top-level OCR status — check docs[] in summary_data |
| `created_at` | ❌ Does not exist | Use `init_time` (integer) or `latest_init_time` (string float) |
| `updated_at` | ❌ Does not exist | Use `last_active_timestamp` |
| `completed_at` | ❌ Does not exist | Use `end_time` |
