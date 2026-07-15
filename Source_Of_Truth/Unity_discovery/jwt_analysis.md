# JWT Analysis — Unity Admin Portal

**Base URL**: `https://vkyc360.unitybank.co.in`  
**Source**: Live token obtained 2026-07-13 from `POST /v1/agent/generate_token`

---

## Live Token (obtained 2026-07-13)

```
eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9
.eyJzdWIiOiJ1bml0eSIsInJvbGUiOiJhZ2VudCIsImF1ZCI6InVzciIsImlhdCI6MTc4MzkzNjEyNCwiZXhwIjoxNzgzOTc5MzI0LCJpc3MiOiJpcHZfYXBpX0lvbmljQXBwIn0
.NoTi3d6II-3TpGQBngN-lUppQBNSL4vin_En3R7nnI8
```

---

## JWT Header

```json
{
  "typ": "JWT",
  "alg": "HS256"
}
```

---

## JWT Payload (Agent Token)

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

### Key Claims Analysis

| Claim | Value | Notes |
|:---|:---|:---|
| `alg` | `HS256` | HMAC-SHA256 — symmetric key signing |
| `typ` | `JWT` | Standard JWT type |
| `sub` | `"unity"` | Subject — client/domain identifier (Unity Bank) |
| `role` | `"agent"` | Role — `"agent"` for API credentials |
| `aud` | `"usr"` | Audience |
| `iat` | `1783936124` | Issued at: 2026-07-13 09:48:44 UTC |
| `exp` | `1783979324` | Expires: 2026-07-13 21:48:44 UTC |
| `iss` | `"ipv_api_IonicApp"` | Issuer — always this string |

---

## Token Lifetime

```
TTL = 1783979324 - 1783936124 = 43200 seconds = 12.0 hours
```

Tokens expire exactly **12 hours** after issue.

---

## Two Token Types Confirmed

The system issues two distinct token types for two different authentication flows:

### Type 1: Agent Token (AI integration)

**Credentials**: `{"username": "unity", "password": "unity"}`  
**TTL**: 12 hours  
**Claims**: `sub, role, aud, iat, exp, iss`  
**`role`**: `"agent"`  
**Used by**: UNITYTOOL, API integrations, Postman agent calls

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

### Type 2: Admin Token (human portal login)

**Credentials**: Admin email + portal password (e.g., `shubham.singh@think360.ai`)  
**TTL**: 7 days (604800 seconds)  
**Claims**: `sub, aud, iat, exp, iss, admin_id` — no `role` claim  
**`admin_id`**: Email address of the logged-in admin  
**Used by**: Human users in the admin portal at :9090

```json
{
  "sub": "unity",
  "aud": "usr",
  "iat": 1781505217,
  "exp": 1782110017,
  "iss": "ipv_api_IonicApp",
  "admin_id": "shubham.singh@think360.ai"
}
```

---

## Refresh Mechanism

No `/refresh_token` endpoint was discovered. The authentication model is:
1. Fetch a new token when the cached token expires (or 120 seconds before expiry)
2. On `401 Unauthorized`, invalidate the cached token and fetch a new one

---

## Security Notes

| Property | Status | Notes |
|:---|:---|:---|
| Algorithm | HS256 | Symmetric — secret key not exposed via API |
| HTTPS | ✅ | All API traffic on port 443 TLS |
| Token storage | Header `auth:` | Not in cookies; no httpOnly protection |
| Token transmitted over HTTPS | ✅ | Port 443 only |
| Short TTL | ✅ | 12 hours is reasonable for service-to-service |
| Refresh token | ❌ None | Stateless — just generate a new token |
| `alg: none` | Not tested | Server likely rejects unsigned tokens |

---

## AI Integration Caching Decision

Based on confirmed 12-hour TTL:

| TTL | Strategy |
|:---|:---|
| **12 hours** | Cache in memory; proactive refresh 120s before expiry; reactive on 401 |

```python
# Proactive refresh threshold: 43200 - 120 = 43080 seconds from issue
# Reactive: any 401 response invalidates cache and triggers re-auth
```

This means the UNITYTOOL should expect to refresh the token approximately twice per day if running continuously, or at most once per investigation pipeline run.
