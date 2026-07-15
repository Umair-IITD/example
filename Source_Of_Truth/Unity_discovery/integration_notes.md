# Integration Notes — Unity Admin Portal

**API Base URL**: `https://vkyc360.unitybank.co.in` (port 443)  
**Admin Portal (SPA)**: `https://vkyc360.unitybank.co.in:9090` (browser only — not used by API integration)  
**Purpose**: Implementation architecture for AI Evidence Collector integration  
**Source**: Live API confirmed — 2026-07-13

---

## 1. UNITYTOOL Architecture

UNITYTOOL is the Evidence Collector component that queries Unity Admin Portal REST API. It must be:
- **Read-only**: Only GET requests + one POST for token generation
- **Stateless**: No server-side state; caches token in memory only
- **Fault-tolerant**: If Unity is unreachable, return empty evidence bundle (fail-open, not fail-closed)
- **Async-safe**: Can run concurrently with METRICTOOL (Uptime Kuma) and SERVERTOOL

### Component interface

```python
class UnityTool:
    """Evidence Collector for Unity Admin Portal."""

    BASE_URL = "https://vkyc360.unitybank.co.in"  # Port 443 — standard HTTPS
    DOMAIN = "unity"

    def collect_evidence(
        self,
        phone_number: str | None,
        session_id: str | None,
        ticket_created_at: datetime
    ) -> UnityEvidenceBundle:
        """
        Collect KYC session evidence for a support ticket.

        Args:
            phone_number: Customer 10-digit mobile number from ticket (preferred identifier)
            session_id:   UUID v4 session ID from ticket (fallback identifier)
            ticket_created_at: When the ticket was created (for correlation)

        Returns:
            UnityEvidenceBundle — always returns, never raises.
            If Unity is unreachable, returns bundle with error field set.
        """
        ...
```

> **NOTE**: The primary customer identifier is `phone_number` (10-digit mobile). There is NO URN in this system.

---

## 2. Token Management

### Credentials

API credentials (service account — not admin portal login):
```json
{"username": "unity", "password": "unity"}
```

> **SECURITY**: Store in AWS Secrets Manager at `kwikid/unity/vkyc_api_credentials`. Never hardcode.

### Token Request

```
POST https://vkyc360.unitybank.co.in/v1/agent/generate_token
Content-Type: application/json

{"username": "unity", "password": "unity"}
```

### Token Response

```json
{
  "Token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
  "status_code": 200,
  "success": true
}
```

> **CRITICAL**: Response field is `Token` (capital T) — NOT `token`, NOT `access_token`.

### Using the Token

```
auth: eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...
```

> **CRITICAL**: Header name is `auth` (lowercase) — NOT `Authorization`. There is NO "Bearer" prefix.

### Strategy: Cached token with proactive refresh

```python
import threading, time, json, base64, requests, boto3

class UnityTokenCache:
    """Thread-safe JWT token cache with proactive refresh."""

    REFRESH_BUFFER_SECONDS = 120  # Refresh 2 minutes before expiry

    _token: str | None = None
    _exp: float = 0.0
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> str:
        with cls._lock:
            if cls._is_valid():
                return cls._token
            cls._token, cls._exp = cls._fetch_new()
            return cls._token

    @classmethod
    def _is_valid(cls) -> bool:
        if not cls._token:
            return False
        return time.time() < (cls._exp - cls.REFRESH_BUFFER_SECONDS)

    @classmethod
    def _fetch_new(cls) -> tuple[str, float]:
        creds = get_unity_credentials()  # from Secrets Manager
        resp = requests.post(
            "https://vkyc360.unitybank.co.in/v1/agent/generate_token",
            json={"username": creds["username"], "password": creds["password"]},
            timeout=10,
            verify=True
        )
        resp.raise_for_status()
        data = resp.json()
        token = data["Token"]          # Capital T — confirmed from live response
        # Decode exp claim without signature verification
        padding = '=' * (4 - len(token.split('.')[1]) % 4)
        payload = json.loads(base64.b64decode(token.split('.')[1] + padding))
        return token, float(payload['exp'])

    @classmethod
    def invalidate(cls):
        """Call when a 401 is received to force re-fetch."""
        with cls._lock:
            cls._token = None
            cls._exp = 0.0


def make_unity_request(method: str, url: str, **kwargs) -> requests.Response:
    """Make authenticated request with automatic token refresh on 401."""
    headers = kwargs.pop('headers', {})
    headers['auth'] = UnityTokenCache.get()  # 'auth' header — NOT Authorization

    resp = requests.request(method, url, headers=headers, **kwargs)

    if resp.status_code == 401:
        # Token expired or invalid — invalidate cache and retry once
        UnityTokenCache.invalidate()
        headers['auth'] = UnityTokenCache.get()
        resp = requests.request(method, url, headers=headers, **kwargs)

    return resp
```

### Token Lifetime

```
TTL = 43200 seconds = 12 hours
Proactive refresh: 43080 seconds from issue (120s buffer)
Reactive refresh: any 401 → invalidate cache → re-fetch
```

---

## 3. Session Lookup Strategy

```python
BASE_URL = "https://vkyc360.unitybank.co.in"

def find_session(phone_number, session_id, ticket_created_at):
    """Find the most relevant KYC session for a support ticket."""

    # Strategy 1: Lookup by phone_number (preferred — stable identifier)
    if phone_number:
        resp = make_unity_request(
            "GET",
            f"{BASE_URL}/api/v1/getAllUserSession/unity/{phone_number}",
            timeout=(5, 15)
        )
        resp.raise_for_status()
        data = resp.json()
        sessions = data.get("session_list", [])
        if sessions:
            return select_most_relevant(sessions, ticket_created_at)

    # Strategy 2: Direct lookup by session_id (UUID v4)
    if session_id:
        resp = make_unity_request(
            "GET",
            f"{BASE_URL}/v1/session/get_details/{session_id}",
            timeout=(5, 15)
        )
        if resp.status_code == 200:
            return resp.json().get("session_data")

    return None  # No session found
```

### Selecting the most relevant session (multiple sessions per customer)

```python
TERMINAL_STATUSES = {
    "kyc_result_approved", "kyc_result_rejected", "kyc_rejected",
    "session_expired", "user_abandoned"
}
NON_TERMINAL_STATUSES = {"waiting", "kyc_result_partial_update"}

def select_most_relevant(sessions, ticket_created_at):
    """
    From multiple sessions for a customer, pick the one most likely
    related to the support ticket.

    Priority:
    1. Session that was in a non-terminal (active/stuck) state near ticket time
    2. Session with init_time closest to (but before) ticket creation time
    3. Most recently initialised session
    """
    ticket_ts = ticket_created_at.timestamp()

    # Parse init_time from each session
    def get_init_ts(s):
        t = s.get("init_time") or s.get("latest_init_time", 0)
        return float(t) if t else 0.0

    # Filter to sessions created before the ticket
    before_ticket = [s for s in sessions if get_init_ts(s) <= ticket_ts]

    if not before_ticket:
        return max(sessions, key=get_init_ts)

    # Prefer non-terminal (active) sessions near ticket time
    non_terminal = [s for s in before_ticket
                    if s.get("session_status") in NON_TERMINAL_STATUSES]
    if non_terminal:
        return max(non_terminal, key=get_init_ts)

    return max(before_ticket, key=get_init_ts)
```

---

## 4. Endpoint Reference

| # | Method | URL | Auth | Purpose |
|:--|:--|:--|:--|:--|
| 1 | POST | `https://vkyc360.unitybank.co.in/v1/agent/generate_token` | No | Get JWT |
| 2 | GET | `https://vkyc360.unitybank.co.in/api/v1/getAllUserSession/unity/{phone_number}` | Yes | Session list |
| 3 | GET | `https://vkyc360.unitybank.co.in/v1/session/get_details/{session_id}` | Yes | Session detail |
| 4 | GET | `https://vkyc360.unitybank.co.in/v1/health` | No | Health check |
| 5 | GET | `https://vkyc360.unitybank.co.in/v1/download_content/{bucket}/{path}` | Yes | File download |

> `POST /v1/agent/sendLink/` — MUTATION. **NEVER CALL.** Read-only constraint.

---

## 5. Timeouts and Retries

```python
UNITY_CONFIG = {
    "base_url": "https://vkyc360.unitybank.co.in",  # Port 443 — standard HTTPS
    "connect_timeout_s": 5,
    "read_timeout_s": 15,
    "max_retries": 2,
    "retry_backoff_s": 1.0,
    "retry_on_status": {500, 502, 503, 504},
}

def unity_request(method, path, **kwargs):
    url = f"{UNITY_CONFIG['base_url']}{path}"
    timeout = (UNITY_CONFIG["connect_timeout_s"], UNITY_CONFIG["read_timeout_s"])

    for attempt in range(UNITY_CONFIG["max_retries"] + 1):
        try:
            resp = make_unity_request(method, url, timeout=timeout, **kwargs)
            if resp.status_code in UNITY_CONFIG["retry_on_status"] and attempt < UNITY_CONFIG["max_retries"]:
                time.sleep(UNITY_CONFIG["retry_backoff_s"] * (attempt + 1))
                continue
            return resp
        except requests.Timeout:
            if attempt < UNITY_CONFIG["max_retries"]:
                time.sleep(UNITY_CONFIG["retry_backoff_s"])
                continue
            raise
    return None
```

---

## 6. Fail-Open Pattern

UNITYTOOL must **never block the investigation pipeline**. If Unity is unreachable:

```python
def safe_collect_unity_evidence(phone_number, session_id, ticket_created_at):
    """Always returns, never raises. Returns empty bundle on failure."""
    try:
        return collect_unity_evidence(phone_number, session_id, ticket_created_at)
    except Exception as e:
        logger.warning(f"Unity evidence collection failed: {e}")
        return {
            "source": "unity_admin_portal",
            "collected_at": datetime.utcnow().isoformat() + "Z",
            "session_found": False,
            "error": str(e),
            "error_type": type(e).__name__,
            "evidence_available": False
        }
```

When `evidence_available: False`, the Root Cause Engine:
- Skips Unity-specific hypotheses
- Notes in the observation: "Unable to retrieve KYC session data at investigation time"
- Does not penalize confidence scores on other evidence sources

---

## 7. Caching Strategy

| Data | Cache duration | Rationale |
|:---|:---|:---|
| JWT token | Until `exp - 120s` | Tokens valid 12h; re-generating per request is wasteful |
| `getAllUserSession` for phone_number | 5 minutes | Session list changes slowly; short cache prevents duplicate calls |
| `get_details` for session_id | 5 minutes | Session details change on state transitions; short TTL |
| Error responses (400, 500) | 60 seconds | Avoid hammering failing endpoint |

---

## 8. Data Parsing Notes

Several fields require secondary parsing:

```python
import json

# extras and stage_data are JSON-encoded strings
extras = json.loads(session.get("extras", "{}"))
stage_data = json.loads(session.get("stage_data", "{}"))

# feedback is a JSON-encoded string (in audit fields)
feedback = json.loads(session.get("feedback", "{}"))
# feedback.type: "Approve" | "Reject" | "Reopen"

# location is a JSON-encoded string
location = json.loads(session.get("location", "{}"))
# {"address": "...", "latitude": 19.145, "longitude": 73.249}

# captured_images is a JSON-encoded array
captured = json.loads(session.get("captured_images", "[]"))
# ["selfie", "pan"]

# summary_data is already a parsed dict in get_details — do NOT json.loads() it
summary = session_data.get("summary_data", {})  # Already a dict

# Timestamps — mix of int and string float
init_ts = float(session.get("init_time", 0))
start_ts = float(session.get("start_time", 0))
```

---

## 9. Tenant Abstraction (Multi-Bank)

KwikID serves multiple banks. Unity Admin Portal is the Unity Bank deployment. Other banks have equivalent portals. The implementation should support extension:

```python
class VKYCPortalProvider:
    """Abstract base class for bank-specific VKYC admin portal integrations."""

    def get_session_by_phone(self, phone_number: str) -> list[dict]: ...
    def get_session_by_id(self, session_id: str) -> dict | None: ...

class UnityPortalProvider(VKYCPortalProvider):
    """Unity Bank VKYC portal via https://vkyc360.unitybank.co.in (port 443)."""
    BASE_URL = "https://vkyc360.unitybank.co.in"
    DOMAIN = "unity"
    ...
```

---

## 10. Secret Management

```python
import boto3, json

def get_unity_credentials() -> dict:
    """Fetch Unity API credentials from AWS Secrets Manager."""
    client = boto3.client('secretsmanager', region_name='ap-south-1')
    secret = client.get_secret_value(SecretId='kwikid/unity/vkyc_api_credentials')
    return json.loads(secret['SecretString'])
    # Returns: {"username": "unity", "password": "unity"}

# Admin portal credentials (browser login) — separate secret
# Path: kwikid/unity/vkyc_admin_credentials
# Note: Admin credentials shared in plaintext during discovery — ROTATE IMMEDIATELY
```

---

## 11. PII Handling

| PII field | Handling in pipeline |
|:---|:---|
| `phone_number` / `user_id` | Log as `mobile: XXXXXX<last4>`; safe for private note |
| `pan_url` image content | Never include in logs or notes |
| PAN number (in docs[].details) | Log as `PAN: XXXXXX<last4>` — never full PAN |
| Aadhaar (in docs[].details) | Already masked in some contexts; never log full Aadhaar |
| `customer_IP` | Internal use only; do not include in customer-facing notes |
| Video URLs (`agent_video_url`, etc.) | Never log; never include in notes |
| `auditor_name` | Internal staff PII — do not expose to customer |
| `location` (address) | PII — do not include in customer notes |

---

## 12. Verified Checklist (2026-07-13)

- [x] Token endpoint: `POST /v1/agent/generate_token`
- [x] Token response field: `Token` (capital T)
- [x] Auth header: `auth:` (not Authorization)
- [x] No "Bearer" prefix
- [x] JWT `exp` claim exists — TTL 12 hours
- [x] JWT algorithm: HS256
- [x] getAllUserSession path parameters: `/unity/{phone_number}`
- [x] get_details path parameter: `/v1/session/get_details/{session_id}`
- [x] No pagination on getAllUserSession (all sessions returned)
- [x] HTTPS with valid cert on port 443
- [x] No rate limiting observed
- [x] No CSRF token needed for GET requests
- [x] Error 401: `{"message": "Missing authorization header"}` or `{"message": "Token is invalid"}`
- [x] Error 400 (invalid session ID): `{"e": "list index out of range", "msg": "Invalid session id", "status": 400}`
- [x] extras and stage_data: JSON strings (need json.loads())
- [x] summary_data: already parsed dict in get_details
