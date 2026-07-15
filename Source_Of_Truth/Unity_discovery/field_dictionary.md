# Field Dictionary — Unity Admin Portal

**API Base URL**: `https://vkyc360.unitybank.co.in` (port 443, HTTPS)
**Purpose**: Complete field reference — type, example, nullability, PII status, and AI significance for every field
**Source**: Confirmed from live API calls on 2026-07-13

---

## Classification Legend

| Symbol | Meaning |
|:---|:---|
| 🔴 HIGH | Critical for AI investigation — always include in evidence bundle |
| 🟡 MEDIUM | Useful context — include when relevant to the ticket type |
| 🟢 LOW | Nice-to-have — include only in detailed investigation notes |
| ⚫ UNUSED | Not useful for AI investigation; skip entirely |
| 🔒 PII | Personally identifiable information — must be masked or excluded from notes |

A field can carry two ratings (e.g., 🟡 MEDIUM 🔒 PII) when it has investigative value but requires care.

---

## Section 1: Session Identifiers

| Field | Type | Example | Nullable | PII | AI Rating | Notes |
|:---|:---|:---|:---|:---|:---|:---|
| `session_id` | string (UUID v4) | `"329c5f9e-9406-4f47-b717-5c87f04054d2"` | No | No | 🔴 HIGH | Primary key for all session lookups; use in get_details call and log correlation |
| `link_id` | string | `"sZWzqDt2oDNZ"` | No | No | 🟡 MEDIUM | 12-char alphanumeric; may appear in customer communications |
| `device_id` | string | `"3jdg81"` | Yes | No | 🟢 LOW | 6-char alphanumeric device identifier |
| `user_id` | string | `"7045722923"` | No | No | 🟢 LOW | Always equals phone_number; redundant |
| `client_name` | string | `"unity"` | No | No | ⚫ UNUSED | Always `"unity"`; no investigative value |

---

## Section 2: Customer Identifiers (PII)

| Field | Type | Example | Nullable | PII | AI Rating | Notes |
|:---|:---|:---|:---|:---|:---|:---|
| `phone_number` | string | `"7045722923"` | No | Yes | 🔴 HIGH 🔒 PII | Primary customer identifier; 10-digit mobile; use for lookup, mask in logs |
| `otp` | string | `"123456"` | Yes | Yes | ⚫ UNUSED 🔒 PII | Raw OTP value; never log or include in any output |
| `customer_IP` | string | `"103.x.x.x"` | Yes | Yes | 🟢 LOW 🔒 PII | Customer IP; partial PII; useful only for network-related tickets |

---

## Section 3: Session Status and Type

| Field | Type | Example | Nullable | PII | AI Rating | Notes |
|:---|:---|:---|:---|:---|:---|:---|
| `session_status` | string (enum) | `"kyc_result_approved"` | No | No | 🔴 HIGH | Primary outcome indicator; drives all downstream investigation logic |
| `session_type` | string | `"VKYC"` | No | No | 🔴 HIGH | Always `"VKYC"`; confirms this is a video KYC session |
| `stage` | string | `"Stage1"` | Yes | No | 🔴 HIGH | Which stage the session reached or is at; null = not started |
| `stage1_valid` | boolean | `true` | Yes | No | 🔴 HIGH | Whether Stage 1 (pre-VKYC checks) passed; null = not reached |
| `productCode` | string | `"ONEFIN"` | No | No | 🔴 HIGH | Product/partner identifier; critical for routing and context |
| `lang` | string | `"hi"` | Yes | No | 🟡 MEDIUM | Language: `"hi"` (Hindi), `"en"` (English), or null |
| `queue` | string | `"free"` | Yes | No | 🟢 LOW | Queue type at session time |
| `queue_id` | string | `"unity_free_hi_vkyc"` | Yes | No | 🟢 LOW | Specific queue name |
| `queue_mode` | string | `"unity_free_hi_vkyc"` | Yes | No | ⚫ UNUSED | Mirrors queue_id; no additional value |

---

## Section 4: Timing and Timeline

| Field | Type | Example | Nullable | PII | AI Rating | Notes |
|:---|:---|:---|:---|:---|:---|:---|
| `init_time` | string (datetime) | `"2026-07-13T10:20:00"` | Yes | No | 🟡 MEDIUM | Session creation time; use for ticket date correlation |
| `latest_init_time` | string (datetime) | `"2026-07-13T10:22:00"` | Yes | No | 🟢 LOW | Updated on re-initiation |
| `start_time` | string (datetime) | `"2026-07-13T10:30:00"` | Yes | No | 🟡 MEDIUM | When VKYC call started; gap from init_time = queue wait |
| `vkyc_start_time` | string (datetime) | `"2026-07-13T10:33:00"` | Yes | No | 🟡 MEDIUM | Precise VKYC video start |
| `end_time` | string (datetime) | `"2026-07-13T10:55:00"` | Yes | No | 🟡 MEDIUM | Session end time |
| `agent_assignment_time` | string (datetime) | `"2026-07-13T10:35:00"` | Yes | No | 🟢 LOW | When agent was assigned |
| `removed_from_queue_timestamp` | string (datetime) | `"2026-07-13T10:34:00"` | Yes | No | 🟢 LOW | When customer was dequeued |
| `last_active_timestamp` | string (datetime) | `"2026-07-13T10:50:00"` | Yes | No | 🟢 LOW | Last customer activity |
| `inserted_in_queue_at` | string | `"init_api"` | Yes | No | ⚫ UNUSED | Queue entry marker; no investigative value |

---

## Section 5: Video and Upload

| Field | Type | Example | Nullable | PII | AI Rating | Notes |
|:---|:---|:---|:---|:---|:---|:---|
| `IS_VIDO_UPLOADED` | boolean | `true` | No | No | 🔴 HIGH | Confirms video was uploaded; `false` = upload failure |
| `video_duration` | string | `"239.64"` | Yes | No | 🔴 HIGH | Duration in seconds as string; very short = incomplete session |
| `number_of_videos_uploaded` | string | `"3"` | Yes | No | 🟡 MEDIUM | Segment count; stored as string |
| `captured_images` | string (JSON) | `'["selfie", "pan"]'` | Yes | No | 🔴 HIGH | JSON string listing captured image types; parse before use |
| `user_video_url` | string (URL) | `"https://..."` | Yes | Yes | ⚫ UNUSED 🔒 PII | PII media URL; never include in notes or logs |
| `agent_video_url` | string (URL) | `"https://..."` | Yes | Yes | ⚫ UNUSED 🔒 PII | PII media URL; never include in notes or logs |
| `agent_screen_url` | string (URL) | `"https://..."` | Yes | Yes | ⚫ UNUSED 🔒 PII | PII media URL; never include in notes or logs |

---

## Section 6: Agent Fields

| Field | Type | Example | Nullable | PII | AI Rating | Notes |
|:---|:---|:---|:---|:---|:---|:---|
| `agent_id` | string (email) | `"Vidya.Rathod@unitybank.co.in"` | Yes | No | 🔴 HIGH | Agent email; include for escalation or agent-error cases |
| `agent_region` | string | `"MUMBAI"` | Yes | No | 🟢 LOW | Agent's geographic region |
| `agent_tl` | string (email) | `"tl@unitybank.co.in"` | Yes | No | 🟢 LOW | Team leader email |

---

## Section 7: Network and Device

| Field | Type | Example | Nullable | PII | AI Rating | Notes |
|:---|:---|:---|:---|:---|:---|:---|
| `avg_network_download_speed` | integer | `1594` | Yes | No | 🟡 MEDIUM | Download speed in Kbps; low values correlate with video/connection failures |
| `avg_network_upload_speed` | integer | `993` | Yes | No | 🟡 MEDIUM | Upload speed in Kbps; low values correlate with video upload failures |
| `app_version_number` | string | `"NA"` | Yes | No | ⚫ UNUSED | Typically `"NA"`; no investigative value |
| `device_id` | string | `"3jdg81"` | Yes | No | 🟢 LOW | 6-char alphanumeric |

---

## Section 8: OTP Fields

| Field | Type | Example | Nullable | PII | AI Rating | Notes |
|:---|:---|:---|:---|:---|:---|:---|
| `otp_attempts` | integer | `1` | Yes | No | 🟡 MEDIUM | Number of OTP attempts; high count = customer struggled |
| `otp_attempts_timestamp` | string (datetime) | `"2026-07-13T10:21:00"` | Yes | No | 🟢 LOW | Timestamp of OTP attempt(s) |
| `otp` | string | `"123456"` | Yes | Yes | ⚫ UNUSED 🔒 PII | Raw OTP; never log |
| `user_ack` | boolean | `true` | Yes | No | 🟢 LOW | Customer acknowledged terms |

---

## Section 9: Document URL Fields (PII)

| Field | Type | Example | Nullable | PII | AI Rating | Notes |
|:---|:---|:---|:---|:---|:---|:---|
| `selfie_url` | string (URL) | `"https://..."` | Yes | Yes | ⚫ UNUSED 🔒 PII | Selfie image; never include in notes |
| `pan_url` | string (URL) | `"https://..."` | Yes | Yes | ⚫ UNUSED 🔒 PII | PAN image; never include in notes |
| `aadhaar_url` | string (URL) | `"https://..."` | Yes | Yes | ⚫ UNUSED 🔒 PII | Aadhaar image; never include in notes |
| `aadhaarback_url` | string (URL) | `"https://..."` | Yes | Yes | ⚫ UNUSED 🔒 PII | Aadhaar back; never include in notes |
| `aadhaarpdf_url` | string (URL) | `"https://..."` | Yes | Yes | ⚫ UNUSED 🔒 PII | Aadhaar PDF; never include in notes |
| `aadhaar_xml` | string (URL) | `"https://..."` | Yes | Yes | ⚫ UNUSED 🔒 PII | Aadhaar XML; never include in notes |
| `signature_url` | string (URL) | `"https://..."` | Yes | Yes | ⚫ UNUSED 🔒 PII | Signature image; never include in notes |
| `undefined_url` | string | `null` | Yes | No | ⚫ UNUSED | Legacy field; always null |

---

## Section 10: Aadhaar Processing

| Field | Type | Example | Nullable | PII | AI Rating | Notes |
|:---|:---|:---|:---|:---|:---|:---|
| `aadhaar_processing_status` | string | `"SUCCESS"` | Yes | No | 🟡 MEDIUM | Aadhaar processing outcome; failure = investigation trigger |
| `aadhaar_processing_task_id` | string | `"task_abc123"` | Yes | No | ⚫ UNUSED | Internal task ID; no investigative value |
| `aadhaar_request_time` | string (datetime) | `"2026-07-13T10:30:00"` | Yes | No | ⚫ UNUSED | Timestamp only; no investigative value |

---

## Section 11: Document Request Timestamps

| Field | Type | Example | Nullable | PII | AI Rating | Notes |
|:---|:---|:---|:---|:---|:---|:---|
| `selfie_request_time` | string (datetime) | `"2026-07-13T10:38:00"` | Yes | No | 🟢 LOW | Timeline reference only |
| `pan_request_time` | string (datetime) | `"2026-07-13T10:40:00"` | Yes | No | 🟢 LOW | Timeline reference only |
| `signature_request_time` | string (datetime) | `"2026-07-13T10:42:00"` | Yes | No | 🟢 LOW | Timeline reference only |

---

## Section 12: Audit Fields

| Field | Type | Example | Nullable | PII | AI Rating | Notes |
|:---|:---|:---|:---|:---|:---|:---|
| `audit_result` | string | `"1"` | Yes | No | 🔴 HIGH | `"1"` = approved, `"0"` = rejected; primary audit outcome |
| `auditor_feedback` | string | `"Approved"` | Yes | No | 🔴 HIGH | Human-readable auditor decision |
| `auditor_fdbk_code` | string | `"A01"` | Yes | No | 🟡 MEDIUM | Coded rejection/approval reason |
| `auditor_name` | string | `"auditor@unitybank.co.in"` | Yes | No | 🟢 LOW | Auditor identifier |
| `audit_id` | string | `"audit_xyz789"` | Yes | No | 🟢 LOW | Audit record identifier |
| `audit_init_time` | string (datetime) | `"2026-07-13T11:00:00"` | Yes | No | 🟢 LOW | When audit began |
| `audit_end_time` | string (datetime) | `"2026-07-13T11:05:00"` | Yes | No | 🟢 LOW | When audit finished |
| `audit_lock` | boolean | `false` | Yes | No | ⚫ UNUSED | Internal lock state |

---

## Section 13: Feedback (JSON String Field)

The `feedback` field is a JSON-encoded string. After parsing with `json.loads()`:

| Sub-field | Type | Example | Nullable | PII | AI Rating | Notes |
|:---|:---|:---|:---|:---|:---|:---|
| `feedback` (raw) | string (JSON) | `'{"type":"Approve",...}'` | Yes | No | 🔴 HIGH | Must parse before use |
| `feedback.type` | string (enum) | `"Approve"` | No | No | 🔴 HIGH | `"Approve"`, `"Reject"`, or `"Reopen"` |
| `feedback.comment` | string | `""` | Yes | No | 🟡 MEDIUM | Short comment; often empty |
| `feedback.feedbackComment` | string | `"ap available"` | Yes | No | 🟡 MEDIUM | Detailed feedback note |

---

## Section 14: Location

| Field | Type | Example | Nullable | PII | AI Rating | Notes |
|:---|:---|:---|:---|:---|:---|:---|
| `location` | string (JSON) | `'{"lat":19.14,"lng":73.24}'` | Yes | Yes | ⚫ UNUSED 🔒 PII | Geolocation; parse before use; PII; not needed for AI investigation |
| `ll` | string | `"19.145,73.249"` | Yes | Yes | ⚫ UNUSED 🔒 PII | Compact lat/lng; same data as location |

---

## Section 15: Artifact URLs

| Field | Type | Example | Nullable | PII | AI Rating | Notes |
|:---|:---|:---|:---|:---|:---|:---|
| `summary_json_url` | string (URL) | `"https://..."` | Yes | No | 🟢 LOW | Link to full JSON summary artifact |
| `summary_pdf_url` | string (URL) | `"https://..."` | Yes | No | 🟢 LOW | Link to PDF summary |
| `zip_url` | string (URL) | `"https://..."` | Yes | No | ⚫ UNUSED | Zipped artifacts; not needed for AI investigation |

---

## Section 16: Queue and Misc

| Field | Type | Example | Nullable | PII | AI Rating | Notes |
|:---|:---|:---|:---|:---|:---|:---|
| `queue_position_at_init` | integer | `3` | Yes | No | 🟢 LOW | Position when customer joined; context for wait time |
| `inserted_in_queue_at` | string | `"init_api"` | Yes | No | ⚫ UNUSED | Internal marker |

---

## Section 17: summary_data Fields (get_details only)

These fields are inside the `summary_data` object, which is fully populated only in `get_details`.

| Field Path | Type | Example | Nullable | PII | AI Rating | Notes |
|:---|:---|:---|:---|:---|:---|:---|
| `summary_data.overall_summary` | array | `[{"success": true, "title": "Questions"}]` | No | No | 🔴 HIGH | Per-step pass/fail; always include in evidence bundle |
| `summary_data.overall_summary[].success` | boolean | `true` | No | No | 🔴 HIGH | `false` = step failed; critical for failure RCA |
| `summary_data.overall_summary[].title` | string | `"Questions"`, `"Selfie"`, `"Pan Card"` | No | No | 🔴 HIGH | Step name |
| `summary_data.qna` | array | `[{"q": "...", "a": "Correct"}]` | No | No | 🔴 HIGH | Agent Q&A log; use to verify customer identity confirmation |
| `summary_data.qna[].q` | string | `"What is your Date Of Birth?"` | No | No | 🔴 HIGH | Question asked |
| `summary_data.qna[].a` | string | `"Correct"` | No | No | 🔴 HIGH | Agent's answer assessment |
| `summary_data.docs` | array | `[{"name": "Pan Card", "details": {...}}]` | No | No | 🟡 MEDIUM | Document records with OCR output |
| `summary_data.docs[].name` | string | `"Pan Card"` | No | No | 🟡 MEDIUM | Document type |
| `summary_data.docs[].facematch_score` | object | `{"selfie_pan_match": 98}` | Yes | No | 🟡 MEDIUM | Face match confidence; low score = mismatch risk |
| `summary_data.docs[].details` | object | `{"dob": "21/06/1997", "name": "..."}` | Yes | Yes | 🟡 MEDIUM 🔒 PII | OCR-extracted document fields; mask PAN/Aadhaar numbers |
| `summary_data.journey_sequence` | string | `"ID6\|ID1\|ID2"` | Yes | No | 🟡 MEDIUM | Pipe-delimited journey steps completed |
| `summary_data.agent_id` | string (email) | `"Vidya.Rathod@unitybank.co.in"` | No | No | 🟢 LOW | Mirrors top-level agent_id |
| `summary_data.lat` | float | `19.145840259385306` | Yes | Yes | ⚫ UNUSED 🔒 PII | Customer latitude; not needed for AI investigation |
| `summary_data.lng` | float | `73.24920689558905` | Yes | Yes | ⚫ UNUSED 🔒 PII | Customer longitude; not needed for AI investigation |
| `summary_data.journey_id` | string | `null` | Yes | No | ⚫ UNUSED | Often null; no investigative value |

---

## Section 18: extras.stage_1_data Fields (JSON string — parse first)

These fields are inside `extras` (a JSON string) → `stage_1_data`.

| Field Path | Type | Example | Nullable | PII | AI Rating | Notes |
|:---|:---|:---|:---|:---|:---|:---|
| `extras.stage_1_data.BASIC_DETAILS.NSDL_CHECK.verified` | string | `"VALID"` | Yes | No | 🔴 HIGH | PAN verification result; `"VALID"` = pass |
| `extras.stage_1_data.BASIC_DETAILS.NSDL_CHECK.aadhaar_linked` | boolean | `true` | Yes | No | 🔴 HIGH | Whether PAN is Aadhaar-linked |
| `extras.stage_1_data.BASIC_DETAILS.NSDL_CHECK.aadhaar_match` | boolean | `true` | Yes | No | 🔴 HIGH | Whether PAN-Aadhaar name/DOB matches |
| `extras.stage_1_data.BASIC_DETAILS.AML_CHECK.aml_exists` | string | `"False"` | Yes | No | 🔴 HIGH | AML screening result; `"True"` = flagged |
| `extras.stage_1_data.BASIC_DETAILS.NSDL_CHECK.pan_number` | string | `"GFDPS8190M"` | Yes | Yes | 🟡 MEDIUM 🔒 PII | PAN number from NSDL; mask in all outputs |
| `extras.stage_1_data.BASIC_DETAILS.NSDL_CHECK.name` | string | `"JOYBLESSY ELISH SAMUEL"` | Yes | Yes | 🟡 MEDIUM 🔒 PII | Name from NSDL records — PII |
| `extras.stage_1_data.BASIC_DETAILS.NSDL_CHECK.dob` | string | `"1997-06-21"` | Yes | Yes | 🟡 MEDIUM 🔒 PII | Date of birth from NSDL — PII |
| `extras.stage_1_data.BASIC_DETAILS.VERIFY_AADHAAR_OTP.ekycDetails.aadhaarNo` | string | `"********9392"` | Yes | Yes | 🟡 MEDIUM 🔒 PII | Always pre-masked; last 4 digits only |
| `extras.stage_1_data.BASIC_DETAILS.VERIFY_AADHAAR_OTP.ekycDetails.name` | string | `"Joyblessy Elish Samuel"` | Yes | Yes | ⚫ UNUSED 🔒 PII | Duplicates NSDL name; PII |
| `extras.stage_1_data.BASIC_DETAILS.VERIFY_AADHAAR_OTP.ekycDetails.photo` | string (base64) | `"<base64>"` | Yes | Yes | ⚫ UNUSED 🔒 PII | Base64 photo from Aadhaar; never log or transmit |
| `extras.stage_1_data.PERSONAL_DETAILS.occupation` | string | `"Consultants / Business Consultancy"` | Yes | No | 🟢 LOW | Customer's declared occupation |
| `extras.stage_1_data.PERSONAL_DETAILS.source_of_income` | string | `"Salary"` | Yes | No | 🟢 LOW | Customer's declared income source |
| `extras.stage_1_data.PERSONAL_DETAILS.gross_annual_income` | string | `"600000"` | Yes | Yes | 🟢 LOW 🔒 PII | Financial PII; include only if directly relevant |
| `extras.stage_1_data.PERSONAL_INFORMATION.gender` | string | `"MALE"` | Yes | No | 🟢 LOW | Customer's gender |
| `extras.stage_1_data.PERSONAL_INFORMATION.dob` | string | `"21-06-1997"` | Yes | Yes | ⚫ UNUSED 🔒 PII | Duplicate DOB — PII |
| `extras.stage_1_data.PERSONAL_INFORMATION.current_address_city` | string | `"Mumbai"` | Yes | Yes | ⚫ UNUSED 🔒 PII | Address — PII |
| `extras.stage_1_data.NOMINEE_DETAILS` | object | `{"nominee_full_name": "NA"}` | Yes | No | ⚫ UNUSED | Typically all `"NA"` |
| `extras.stage_1_data.BASIC_DETAILS.politically_exposed_consent` | boolean | `false` | Yes | No | 🟡 MEDIUM | PEP declaration; `true` = politically exposed |
| `extras.stage_1_data.BASIC_DETAILS.email` | string | `"customer@gmail.com"` | Yes | Yes | ⚫ UNUSED 🔒 PII | Customer email — PII |

---

## Quick Reference: Always Include in Evidence Bundle (🔴 HIGH)

When preparing an AI investigation evidence bundle, always include:

1. `session_id`
2. `session_status`
3. `phone_number` (masked: show last 4 digits only)
4. `productCode`
5. `stage`
6. `stage1_valid`
7. `IS_VIDO_UPLOADED`
8. `video_duration`
9. `session_type`
10. `agent_id`
11. `audit_result`
12. `auditor_feedback`
13. `feedback.type` (after parsing `feedback`)
14. `captured_images` (after parsing JSON string)
15. `summary_data.overall_summary` (all items)
16. `summary_data.qna` (all Q&A pairs)
17. `extras.stage_1_data.BASIC_DETAILS.NSDL_CHECK.verified`
18. `extras.stage_1_data.BASIC_DETAILS.NSDL_CHECK.aadhaar_linked`
19. `extras.stage_1_data.BASIC_DETAILS.NSDL_CHECK.aadhaar_match`
20. `extras.stage_1_data.BASIC_DETAILS.AML_CHECK.aml_exists`
