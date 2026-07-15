# Search Behaviour — Unity Admin Portal API Lookup Algorithm

**API Base URL**: `https://vkyc360.unitybank.co.in` (port 443, HTTPS)
**Purpose**: Document how to find a VKYC session from identifiers found in Freshdesk tickets
**Source**: Confirmed from live API calls on 2026-07-13

---

## Critical: No URN in This System

There is no URN (Unique Reference Number) concept in the Unity VKYC system. The primary customer identifier is `phone_number` — a 10-digit Indian mobile number. Any prior documentation referencing a URN was incorrect.

The two lookup paths are:
1. **By phone_number** → `getAllUserSession` returns all sessions for that customer
2. **By session_id** → `get_details` returns the full record for one specific session

---

## Identifier Types

| Identifier | Format | Example | Source API |
|:---|:---|:---|:---|
| `phone_number` | 10-digit string, starts with 6-9 | `"7045722923"` | `getAllUserSession` path param |
| `session_id` | UUID v4 (36 chars with hyphens) | `"329c5f9e-9406-4f47-b717-5c87f04054d2"` | `get_details` path param |
| `link_id` | 12-char alphanumeric | `"sZWzqDt2oDNZ"` | Not directly searchable via API; match to session list |

---

## Lookup Path 1: By Phone Number

### URL Pattern

```
GET /api/v1/getAllUserSession/{domain}/{phone_number}
```

### Example

```
GET /api/v1/getAllUserSession/unity/7045722923 HTTP/1.1
Host: vkyc360.unitybank.co.in
auth: eyJhbGciOi...
```

### Response — Found

```json
{
  "session_count": 5,
  "session_list": [ ... ],
  "status_code": 200,
  "success": true
}
```

### Response — Not Found

When no sessions exist for the phone number:

```json
{
  "session_count": 0,
  "session_list": [],
  "status_code": 200,
  "success": true
}
```

This is HTTP 200, not 404. The caller must check `session_count == 0` explicitly.

---

## Lookup Path 2: By Session ID

### URL Pattern

```
GET /v1/session/get_details/{session_id}
```

### Example

```
GET /v1/session/get_details/329c5f9e-9406-4f47-b717-5c87f04054d2 HTTP/1.1
Host: vkyc360.unitybank.co.in
auth: eyJhbGciOi...
```

### Response — Found

```json
{
  "session_data": { ... },
  "status": "success"
}
```

### Response — Not Found

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

---

## Regex Patterns for Freshdesk Ticket Extraction

Use these patterns to extract identifiers from ticket text (subject line, description, comments).

### Phone Number

```python
PHONE_PATTERN = r'\b[6-9]\d{9}\b'
```

- Matches any 10-digit Indian mobile number starting with 6, 7, 8, or 9
- The `\b` word boundaries prevent matching numbers embedded in longer strings
- Examples matched: `7045722923`, `9876543210`, `6012345678`
- Examples NOT matched: `12345678901` (11 digits), `12345` (too short)

### Session ID (UUID v4)

```python
SESSION_ID_PATTERN = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
```

- Matches standard UUID v4 format
- Case-insensitive: also match uppercase with `re.IGNORECASE`
- Example matched: `329c5f9e-9406-4f47-b717-5c87f04054d2`

### Link ID

```python
LINK_ID_PATTERN = r'\b[A-Za-z0-9]{12}\b'
```

- Matches 12-character alphanumeric strings
- Use only if phone and session_id patterns yield no match; prone to false positives
- Example matched: `sZWzqDt2oDNZ`

---

## Complete Extraction Function

```python
import re

PHONE_PATTERN = r'\b[6-9]\d{9}\b'
SESSION_ID_PATTERN = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
LINK_ID_PATTERN = r'\b[A-Za-z0-9]{12}\b'

def extract_identifiers_from_ticket(ticket_text: str) -> dict:
    """
    Extract Unity VKYC identifiers from a Freshdesk ticket.
    
    Returns:
        {
            "phone_number": str | None,   # 10-digit mobile
            "session_id": str | None,     # UUID v4
            "link_id": str | None         # 12-char alphanumeric (low confidence)
        }
    """
    phone_match = re.search(PHONE_PATTERN, ticket_text)
    session_match = re.search(SESSION_ID_PATTERN, ticket_text, re.IGNORECASE)
    link_match = re.search(LINK_ID_PATTERN, ticket_text) if not (phone_match or session_match) else None

    return {
        "phone_number": phone_match.group(0) if phone_match else None,
        "session_id": session_match.group(0).lower() if session_match else None,
        "link_id": link_match.group(0) if link_match else None
    }
```

---

## AI Search Algorithm (Full Decision Tree)

```
1. EXTRACT identifiers from ticket text using patterns above

2. IF session_id found:
   a. Call GET /v1/session/get_details/{session_id}
   b. IF HTTP 400 → session_id invalid or not found → try phone_number if available
   c. IF HTTP 200 → return full session_data

3. IF phone_number found (and no session_id, or session_id lookup failed):
   a. Call GET /api/v1/getAllUserSession/unity/{phone_number}
   b. IF session_count == 0 → no sessions found → escalate to human agent
   c. IF session_count >= 1 → select session (see selection logic below)
   d. Call GET /v1/session/get_details/{selected_session_id} for full data

4. IF neither phone_number nor session_id found:
   → Cannot look up session from available ticket information
   → Ask customer to provide their registered mobile number
```

---

## Multi-Session Disambiguation

When `getAllUserSession` returns multiple sessions (`session_count > 1`), select using this priority:

### Step 1: Default to Most Recent

`session_list[0]` is always the most recent session. Use it unless there is a specific reason to look further.

### Step 2: Status Priority (if most recent is non-terminal)

If `session_list[0].session_status` is `"waiting"` or `"user_abandoned"` but the ticket implies a completed review, scan for terminal statuses in this priority order:

| Priority | session_status Value |
|:---:|:---|
| 1 (highest) | `kyc_result_approved` |
| 2 | `kyc_result_rejected` |
| 3 | `kyc_rejected` |
| 4 | `kyc_result_partial_update` |
| 5 | `session_expired` |
| 6 | `user_abandoned` |
| 7 (lowest) | `waiting` |

### Step 3: Date Correlation

If the Freshdesk ticket contains a date (e.g., "I tried on July 10th"), match against `init_time` or `start_time` to find the session the customer is describing.

```python
from datetime import date

def select_session_by_date(session_list: list, target_date: date) -> dict | None:
    for session in session_list:
        if session.get("init_time"):
            session_date = session["init_time"][:10]  # "YYYY-MM-DD"
            if session_date == target_date.isoformat():
                return session
    return None
```

---

## Error Scenarios and Handling

| Scenario | API Response | Handling |
|:---|:---|:---|
| Phone number not in system | HTTP 200, `session_count: 0` | Ask customer to confirm their registered mobile number |
| Phone number has no VKYC sessions | HTTP 200, `session_count: 0` | Customer may not have initiated VKYC; check if link was sent |
| Session ID malformed or not found | HTTP 400, `{"msg": "Invalid session id"}` | Try phone_number lookup instead |
| Missing `auth` header | HTTP 401, `{"message": "Missing authorization header"}` | Token not passed; check integration configuration |
| Expired/invalid token | HTTP 401, `{"message": "Token is invalid"}` | Re-authenticate to get a new token |
| Multiple sessions for customer | HTTP 200, `session_count > 1` | Apply disambiguation logic above |

---

## Authentication

All API calls require the `auth` header with a valid token. The token is obtained via the login endpoint and placed in the `Token` field of the login response.

```
auth: <token_value>
```

Note: This is NOT standard Bearer token format. The header name is `auth` (lowercase), not `Authorization`.

---

## Notes on link_id

The `link_id` (12-char alphanumeric, e.g., `sZWzqDt2oDNZ`) appears in the session record but is **not directly searchable** via a dedicated API endpoint. If a link_id is found in a ticket:

1. Extract the phone_number from the same ticket if possible
2. Call `getAllUserSession` with the phone_number
3. Filter the resulting session list for the matching `link_id`

If only the link_id is available (no phone_number), escalate to a human agent — direct lookup by link_id is not supported by the current API.
