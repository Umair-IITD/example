# Session Details — get_details Endpoint

**API Base URL**: `https://vkyc360.unitybank.co.in` (port 443, HTTPS)
**Endpoint**: `GET /v1/session/get_details/{session_id}`
**Source**: Confirmed from live API calls on 2026-07-13

---

## Endpoint Overview

This endpoint returns the full detail record for a single VKYC session identified by its UUID. It provides a superset of the fields available in `getAllUserSession` — particularly `summary_data` (fully populated), `extras` (with complete stage_1_data), and all audit fields.

**When to use**: After identifying a session via `getAllUserSession`, call `get_details` to retrieve the complete evidence bundle including document verification results, QnA, PAN/Aadhaar checks, and auditor feedback.

---

## Request

### URL Pattern

```
GET /v1/session/get_details/{session_id}
```

`{session_id}` is a **path parameter** in UUID v4 format.

### Path Parameters

| Parameter | Type | Example | Notes |
|:---|:---|:---|:---|
| `session_id` | string (UUID v4) | `329c5f9e-9406-4f47-b717-5c87f04054d2` | Obtained from `getAllUserSession` response |

### Headers

| Header | Value | Notes |
|:---|:---|:---|
| `auth` | `<token>` | Same custom auth header as all other endpoints |

### Example Request

```
GET /v1/session/get_details/329c5f9e-9406-4f47-b717-5c87f04054d2 HTTP/1.1
Host: vkyc360.unitybank.co.in
auth: eyJhbGciOi...
```

---

## Response

### Top-Level Shape

```json
{
  "session_data": { ... },
  "status": "success"
}
```

### Top-Level Fields

| Field | Type | Example | Notes |
|:---|:---|:---|:---|
| `session_data` | object | `{...}` | Complete session record — see fields below |
| `status` | string | `"success"` | Request-level status string |

---

## session_data Fields

`session_data` contains all fields from the `getAllUserSession` session list items, plus the additional fields listed in this section. Fields unique to `get_details` or meaningfully richer here are called out explicitly.

### Core Identifiers

| Field | Type | Example | Notes |
|:---|:---|:---|:---|
| `session_id` | string (UUID v4) | `"329c5f9e-9406-4f47-b717-5c87f04054d2"` | Primary key |
| `phone_number` | string | `"7045722923"` | 10-digit mobile; primary customer identifier |
| `user_id` | string | `"7045722923"` | Same as `phone_number` |
| `client_name` | string | `"unity"` | Always `"unity"` |
| `productCode` | string | `"ONEFIN"` | Also seen: `"FINTECHFARM"`, `"PAISABAZAR"` |
| `session_type` | string | `"VKYC"` | Always `"VKYC"` |
| `session_status` | string (enum) | `"kyc_result_approved"` | See enum in session_discovery.md |
| `link_id` | string | `"sZWzqDt2oDNZ"` | 12-char alphanumeric |
| `device_id` | string | `"3jdg81"` | 6-char alphanumeric |

### Timing Fields

| Field | Type | Example | Notes |
|:---|:---|:---|:---|
| `init_time` | string (datetime) | `"2026-07-13T10:20:00"` | Session creation time; nullable |
| `latest_init_time` | string (datetime) | `"2026-07-13T10:22:00"` | Most recent init (after re-initiation) |
| `start_time` | string (datetime) | `"2026-07-13T10:30:00"` | When VKYC call started |
| `vkyc_start_time` | string (datetime) | `"2026-07-13T10:33:00"` | Precise VKYC video start; additional to getAllUserSession |
| `end_time` | string (datetime) | `"2026-07-13T10:55:00"` | When session ended |
| `last_active_timestamp` | string (datetime) | `"2026-07-13T10:50:00"` | Last customer activity |
| `agent_assignment_time` | string (datetime) | `"2026-07-13T10:35:00"` | When agent was assigned |
| `removed_from_queue_timestamp` | string (datetime) | `"2026-07-13T10:34:00"` | When dequeued; additional to getAllUserSession |

### Queue Fields

| Field | Type | Example | Notes |
|:---|:---|:---|:---|
| `queue` | string | `"free"` | Nullable |
| `queue_id` | string | `"unity_free_hi_vkyc"` | Specific queue |
| `queue_mode` | string | `"unity_free_hi_vkyc"` | Matches queue_id; additional to getAllUserSession |
| `queue_position_at_init` | integer | `3` | Position when customer joined |
| `inserted_in_queue_at` | string | `"init_api"` | Queue entry marker |

### Document Capture Fields

| Field | Type | Example | Notes |
|:---|:---|:---|:---|
| `selfie_url` | string (URL) | `"https://..."` | Selfie image URL — PII; additional to getAllUserSession |
| `selfie_request_time` | string (datetime) | `"2026-07-13T10:38:00"` | PII media timestamp; additional to getAllUserSession |
| `pan_url` | string (URL) | `"https://..."` | PAN card image URL — PII; additional to getAllUserSession |
| `pan_request_time` | string (datetime) | `"2026-07-13T10:40:00"` | PII media timestamp; additional to getAllUserSession |
| `aadhaar_url` | string (URL) | `"https://..."` | Aadhaar image URL — PII |
| `aadhaarback_url` | string (URL) | `"https://..."` | Aadhaar back image — PII |
| `aadhaarpdf_url` | string (URL) | `"https://..."` | Aadhaar PDF — PII |
| `aadhaar_xml` | string (URL) | `"https://..."` | Aadhaar XML — PII |
| `signature_url` | string (URL) | `"https://..."` | Signature image — PII |
| `captured_images` | string (JSON) | `'["selfie", "pan"]'` | JSON string; parse before use |

### Video Fields

| Field | Type | Example | Notes |
|:---|:---|:---|:---|
| `user_video_url` | string (URL) | `"https://..."` | Customer-side video — PII media; additional to getAllUserSession |
| `agent_video_url` | string (URL) | `"https://..."` | Agent-side video — PII media |
| `agent_screen_url` | string (URL) | `"https://..."` | Agent screen recording — PII media |
| `video_duration` | string | `"239.64"` | Duration in seconds as string; additional to getAllUserSession |
| `number_of_videos_uploaded` | string | `"3"` | Video segment count as string |
| `IS_VIDO_UPLOADED` | boolean | `true` | Upload confirmation flag |

### Audit Fields

All audit fields are richer/more reliably populated in `get_details` than in `getAllUserSession`.

| Field | Type | Example | Notes |
|:---|:---|:---|:---|
| `audit_id` | string | `"audit_xyz789"` | Unique audit record ID; additional to getAllUserSession |
| `audit_result` | string | `"1"` | `"1"` = approved, `"0"` = rejected; additional to getAllUserSession |
| `audit_lock` | boolean | `false` | Whether audit is locked; additional to getAllUserSession |
| `audit_init_time` | string (datetime) | `"2026-07-13T11:00:00"` | When auditor began; additional to getAllUserSession |
| `audit_end_time` | string (datetime) | `"2026-07-13T11:05:00"` | When auditor finished; additional to getAllUserSession |
| `auditor_name` | string | `"auditor@unitybank.co.in"` | Auditor identifier |
| `auditor_feedback` | string | `"Approved"` | Human-readable decision; additional to getAllUserSession |
| `auditor_fdbk_code` | string | `"A01"` | Coded feedback category |
| `feedback` | string (JSON) | `'{"type":"Approve","comment":"","feedbackComment":"ap available"}'` | Full feedback object as JSON string; parse before use; additional to getAllUserSession |

### Artifact URL Fields

| Field | Type | Example | Notes |
|:---|:---|:---|:---|
| `summary_json_url` | string (URL) | `"https://..."` | Session summary JSON artifact; additional to getAllUserSession |
| `summary_pdf_url` | string (URL) | `"https://..."` | Session summary PDF artifact; additional to getAllUserSession |
| `zip_url` | string (URL) | `"https://..."` | Zipped session artifacts bundle; additional to getAllUserSession |

### summary_data Object (Fully Populated in get_details)

`summary_data` is a structured object in `get_details` (not a JSON string). It contains the complete session outcome summary.

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
  "docs": [
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
        "raw_nsdl": {
          "NSDLResponse": { ... }
        }
      }
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
}
```

#### summary_data Sub-Fields

| Field | Type | Example | Notes |
|:---|:---|:---|:---|
| `agent_id` | string (email) | `"Vidya.Rathod@unitybank.co.in"` | Agent who handled the session |
| `client_name` | string | `"unity"` | Always `"unity"` |
| `session_id` | string (UUID) | `"329c5f9e-..."` | Cross-reference with top-level |
| `user_id` | string | `"7045722923"` | Same as phone_number |
| `journey_id` | string or null | `null` | Journey identifier; often null |
| `journey_sequence` | string | `"ID6\|ID1\|ID2"` | Pipe-delimited sequence of completed journey steps |
| `lat` | float | `19.145840259385306` | Customer latitude — PII |
| `lng` | float | `73.24920689558905` | Customer longitude — PII |
| `docs` | array | `[{...}]` | Array of captured document objects |
| `docs[].name` | string | `"Pan Card"` | Document type name |
| `docs[].details` | object | `{"dob":..., "name":...}` | OCR-extracted document fields — PII |
| `docs[].facematch_score` | object | `{"selfie_pan_match": 98}` | Face match confidence score (0-100) |
| `docs[].front_url` | string (URL) | `"https://..."` | Document front image URL — PII |
| `docs[].validator` | object | `{"raw_nsdl": {...}}` | Raw verification API responses |
| `overall_summary` | array | `[{"success": true, "title": "Questions"}, ...]` | Per-step pass/fail; critical for failure investigation |
| `overall_summary[].success` | boolean | `true` | Whether this step passed |
| `overall_summary[].title` | string | `"Questions"`, `"Selfie"`, `"Pan Card"` | Step name |
| `qna` | array | `[{"q": "...", "a": "Correct"}, ...]` | Agent's questions and customer responses |
| `qna[].q` | string | `"What is your Date Of Birth?"` | Question asked during VKYC |
| `qna[].a` | string | `"Correct"` | Agent's assessment of customer's answer |

---

## extras Field Structure (Full Stage Data)

`extras` is a JSON-encoded string. After parsing with `json.loads()`, it contains the complete `stage_1_data` hierarchy.

```json
{
  "product_code": "ONEFIN",
  "phone_number": "7045722923",
  "lang": "hi",
  "stage_1_data": {
    "BASIC_DETAILS": {
      "AML_CHECK": {
        "aml_exists": "False",
        "aml_api_code": "API38840..."
      },
      "VERIFY_AADHAAR_OTP": {
        "ekycDetails": {
          "aadhaarNo": "********9392",
          "name": "Joyblessy Elish Samuel",
          "address": "...",
          "careOf": "S/O: ...",
          "city": "Mumbai",
          "district": "Mumbai",
          "pin": "400022",
          "state": "Maharashtra",
          "subdistrict": "Mumbai",
          "photo": "<base64_string>",
          "aadhaarpdf_url": "https://..."
        }
      },
      "NSDL_CHECK": {
        "name": "JOYBLESSY ELISH SAMUEL",
        "pan_number": "GFDPS8190M",
        "aadhaar_linked": true,
        "aadhaar_match": true,
        "dob": "1997-06-21",
        "verified": "VALID"
      },
      "email": "Joyblessx@gmail.com",
      "pan_number": "GFDPS8190M",
      "politically_exposed_consent": false,
      "phone_number": "7045722923"
    },
    "PERSONAL_DETAILS": {
      "education": "NOT AVAILABLE",
      "father_spouse_full_name": "ELISH SAMUEL",
      "gross_annual_income": "600000",
      "marital_status": "Single",
      "mother_name": "",
      "occupation": "Consultants / Business Consultancy",
      "source_of_income": "Salary"
    },
    "PERSONAL_INFORMATION": {
      "aadhaar_address": "...",
      "current_address_address_line_1": "...",
      "current_address_address_line_2": "...",
      "current_address_address_line_3": "",
      "current_address_city": "Mumbai",
      "current_address_not_same_as_aadhaar_address": true,
      "current_address_pincode": "400022",
      "current_address_state": "MAHARASHTRA",
      "dob": "21-06-1997",
      "gender": "MALE",
      "name": "Joyblessy Elish Samuel"
    },
    "NOMINEE_DETAILS": {
      "nominee_address": "NA",
      "nominee_address_same_as_current_address": "NA",
      "nominee_date_of_birth": "NA",
      "nominee_full_name": "NA",
      "provide_nominee_details": "NA",
      "relationship_with_nominee": "NA"
    }
  }
}
```

### extras.stage_1_data Key Sub-sections

| Sub-section | Key Fields | Notes |
|:---|:---|:---|
| `BASIC_DETAILS.AML_CHECK` | `aml_exists`, `aml_api_code` | AML screening result |
| `BASIC_DETAILS.VERIFY_AADHAAR_OTP.ekycDetails` | `aadhaarNo` (masked), `name`, `address`, `city`, `state`, `pin`, `photo` | Aadhaar eKYC data — heavy PII; `aadhaarNo` always masked as `"********XXXX"` |
| `BASIC_DETAILS.NSDL_CHECK` | `name`, `pan_number`, `aadhaar_linked`, `aadhaar_match`, `dob`, `verified` | PAN verification via NSDL; `verified: "VALID"` is the success value |
| `PERSONAL_DETAILS` | `occupation`, `source_of_income`, `gross_annual_income`, `marital_status` | Customer-declared personal details |
| `PERSONAL_INFORMATION` | `name`, `dob`, `gender`, `aadhaar_address`, `current_address_*` | Addresses and identity |
| `NOMINEE_DETAILS` | `nominee_full_name`, `nominee_date_of_birth`, `relationship_with_nominee` | Often all `"NA"` |

---

## feedback Field Structure

The `feedback` field is a JSON-encoded string. After parsing:

```json
{
  "type": "Approve",
  "comment": "",
  "feedbackComment": "ap available"
}
```

| Field | Type | Values | Notes |
|:---|:---|:---|:---|
| `type` | string (enum) | `"Approve"`, `"Reject"`, `"Reopen"` | Agent/auditor decision type |
| `comment` | string | `""` or free text | Short comment; often empty |
| `feedbackComment` | string | `"ap available"` | Detailed feedback note |

---

## Error Cases

### Invalid Session ID

When the provided session_id does not exist in the system:

```
HTTP 400 Bad Request
```

```json
{
  "e": "list index out of range",
  "msg": "Invalid session id",
  "status": 400
}
```

### Authentication Errors

| Scenario | HTTP Status | Response Body |
|:---|:---|:---|
| Missing `auth` header | 401 | `{"message": "Missing authorization header"}` |
| Invalid or expired token | 401 | `{"message": "Token is invalid"}` |

---

## Relationship to getAllUserSession

| Aspect | getAllUserSession | get_details |
|:---|:---|:---|
| Scope | All sessions for a customer | One session by ID |
| summary_data | Minimal or null | Fully populated with docs, qna, overall_summary |
| extras | Present as JSON string | Present as JSON string; same structure |
| audit fields | Partially present | All present and fully populated |
| video URLs | Present | Present; confirmed additional fields |
| Use case | Discover sessions for a phone number | Full evidence bundle for one session |
