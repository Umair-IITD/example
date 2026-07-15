# Authentication — Unity Admin Portal API

**Base URL**: `https://vkyc360.unitybank.co.in` (port 443 — standard HTTPS)  
**Admin Portal Frontend**: `https://vkyc360.unitybank.co.in:9090` (browser only — not used by API integration)  
**Auth mechanism**: JWT via custom `auth` header  
**Source**: Live API confirmed — 2026-07-13

> **CRITICAL**: The auth header name is `auth`, NOT `Authorization`. Do not send `Authorization: Bearer <token>`.

---

## Token Generation

### Endpoint

```
POST https://vkyc360.unitybank.co.in/v1/agent/generate_token
Content-Type: application/json
```

### Request Body

```json
{
  "username": "unity",
  "password": "unity"
}
```

> **Note**: These are the **agent API credentials** (service account), not the human admin portal credentials. The admin portal uses `shubham.singh@think360.ai` / portal password to log in via browser and generates a different token type (see §Token Types below).

### Success Response (HTTP 200)

```json
{
  "Token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
  "status_code": 200,
  "success": true
}
```

> **Note**: The token field is `Token` with capital T.

---

## Using the Token

All authenticated requests must include the token in the `auth` header (no "Bearer" prefix):

```
auth: eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...
```

**Example (getAllUserSession):**
```bash
curl "https://vkyc360.unitybank.co.in/api/v1/getAllUserSession/unity/7045722923" \
  -H "auth: eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9..."
```

---

## JWT Structure (Agent Token)

### Header
```json
{
  "typ": "JWT",
  "alg": "HS256"
}
```

### Payload (confirmed from live token, 2026-07-13)
```json
{
  "sub": "unity",
  "role": "agent",
  "aud": "usr",
  "iat": 1783936124,
  "exp": 1783979324,
  "iss": "ipv_api_IonicApp"
}
```

| Claim | Value | Notes |
|:---|:---|:---|
| `sub` | `"unity"` | Client/domain identifier |
| `role` | `"agent"` | Always `"agent"` for API integration tokens |
| `aud` | `"usr"` | Audience |
| `iat` | Unix timestamp | Issued at |
| `exp` | Unix timestamp | Expiry |
| `iss` | `"ipv_api_IonicApp"` | Issuer — always this value |

### Token Lifetime

```
TTL = exp - iat = 43200 seconds = 12 hours
```

The agent token (from `unity`/`unity` credentials) expires **12 hours** after issue.

---

## Token Types

Two distinct token types exist in this system:

| Type | Credentials | TTL | Extra Claims | Used For |
|:---|:---|:---|:---|:---|
| **Agent token** | `username: "unity"`, `password: "unity"` | 12 hours | `role: "agent"` | API integration (UNITYTOOL) |
| **Admin token** | Admin email / portal password | 7 days | `admin_id: "<email>"` | Human admin portal at :9090 |

The AI integration must use the **agent token** only.

---

## Authentication Errors

### No `auth` header

```
HTTP/1.1 401 Unauthorized
{"message": "Missing authorization header"}
```

### Invalid or expired token

```
HTTP/1.1 401 Unauthorized
{"message": "Token is invalid"}
```

---

## Token Caching Strategy for AI Integration

```python
import threading, time, json, base64, requests

class UnityTokenCache:
    """Thread-safe JWT cache with proactive refresh."""
    
    _token: str | None = None
    _exp: float = 0.0
    _lock = threading.Lock()
    
    REFRESH_BUFFER_SECONDS = 120  # Refresh 2 minutes before expiry
    
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
    def _fetch_new(cls) -> tuple:
        creds = get_unity_credentials()  # from Secrets Manager
        resp = requests.post(
            "https://vkyc360.unitybank.co.in/v1/agent/generate_token",
            json={"username": creds["username"], "password": creds["password"]},
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        token = data["Token"]  # Capital T
        payload = json.loads(base64.b64decode(token.split('.')[1] + '=='))
        return token, float(payload['exp'])
    
    @classmethod
    def invalidate(cls):
        with cls._lock:
            cls._token = None
            cls._exp = 0.0


def make_unity_request(method: str, url: str, **kwargs) -> requests.Response:
    """Make authenticated request with automatic token refresh on 401."""
    headers = kwargs.pop('headers', {})
    headers['auth'] = UnityTokenCache.get()
    
    resp = requests.request(method, url, headers=headers, **kwargs)
    
    if resp.status_code == 401:
        # Token expired or invalid — invalidate cache and retry once
        UnityTokenCache.invalidate()
        headers['auth'] = UnityTokenCache.get()
        resp = requests.request(method, url, headers=headers, **kwargs)
    
    return resp
```

---

## Credential Storage

> **SECURITY**: Credentials must be stored in AWS Secrets Manager.  
> Path: `kwikid/unity/vkyc_api_credentials`  
> Never store in source code, environment files, or logs.

```python
import boto3, json

def get_unity_credentials() -> dict:
    client = boto3.client('secretsmanager', region_name='ap-south-1')
    secret = client.get_secret_value(SecretId='kwikid/unity/vkyc_api_credentials')
    return json.loads(secret['SecretString'])
    # Returns: {"username": "unity", "password": "unity"}
```

> **Admin credentials** (portal login `shubham.singh@think360.ai`) must be rotated — they were shared in plaintext during discovery. Store at `kwikid/unity/vkyc_admin_credentials`.
