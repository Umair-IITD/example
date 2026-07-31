# AUTH_FLOW_AUDIT.md
## Sprint 2.29 — Freshdesk Webhook Authentication Failure Investigation

**Date**: 2026-06-25  
**Status**: INVESTIGATION COMPLETE — DO NOT IMPLEMENT  
**Symptom**: `POST /webhooks/freshdesk/ticket-created` returns HTTP 401  
**Log line**: `freshdesk.verifier: REJECTED reason=HMAC mismatch on x-webhook-token`

---

## 1. Complete Call Graph

Every function call from route entry to rejection.

```
POST /webhooks/freshdesk/ticket-created
│
├── api/routes/webhooks/freshdesk.py:73
│   └── async def freshdesk_ticket_created(request, background_tasks)
│
├── api/routes/webhooks/freshdesk.py:89
│   └── body = await request.body()
│
├── api/routes/webhooks/freshdesk.py:103
│   └── payload = json.loads(body)                       ← parse BEFORE verify (by design)
│
├── api/routes/webhooks/freshdesk.py:123-125
│   └── inner = payload.get("freshdesk_webhook", payload)
│       event_ts_str = inner.get("created_at", "")
│       event_timestamp = _parse_iso_timestamp(event_ts_str)
│
├── api/routes/webhooks/freshdesk.py:127
│   └── verifier = _get_verifier(request)
│       │
│       └── api/routes/webhooks/freshdesk.py:402-413
│           def _get_verifier(request):
│             runtime = getattr(getattr(request, "app", None), "state", None)
│             # runtime is NOT None (app is running)
│             verifier = getattr(runtime, "freshdesk_verifier", None)
│             # *** freshdesk_verifier IS NOT SET on app.state ***
│             # Sprint 2.29 wiring block (app/main.py:590-720) sets 6 keys
│             # but NEVER sets app.state.freshdesk_verifier
│             # → falls through to env fallback ↓
│             secret = os.getenv("FRESHDESK_WEBHOOK_SECRET", "")
│             # secret is set (the static secret value, e.g. "my-static-token")
│             enforce = os.getenv("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false").lower() == "true"
│             # *** FRESHDESK_WEBHOOK_MODE is NEVER READ HERE ***
│             return FreshdeskWebhookVerifier(secret, enforce=enforce)
│             # Returns a pure-HMAC verifier with NO mode awareness
│
├── api/routes/webhooks/freshdesk.py:128
│   └── if verifier is not None:   ← TRUE
│
├── api/routes/webhooks/freshdesk.py:129-130
│   └── headers = dict(request.headers)
│       result = verifier.verify(headers, body, event_timestamp=event_timestamp)
│       │
│       └── freshdesk/verifier.py:78-126
│           def verify(headers, body, event_timestamp):
│             lower_headers = {k.lower(): v for k, v in headers.items()}
│             # Replay check first (if event_timestamp provided)
│             # [passes — timestamp is recent]
│             │
│             ├── freshdesk/verifier.py:107
│             │   token = lower_headers.get("x-webhook-token")
│             │   # token = "<the-raw-static-secret>" — Freshdesk sends the
│             │   # PLAIN SECRET as the header value; NOT an HMAC digest
│             │
│             ├── freshdesk/verifier.py:109
│             │   expected = self._compute_hmac(body)
│             │   # Computes HMAC-SHA256(secret_bytes, body)
│             │   # freshdesk/verifier.py:128-130
│             │   # → returns a 64-char hex digest
│             │
│             ├── freshdesk/verifier.py:110
│             │   hmac.compare_digest(expected, token.strip())
│             │   # expected = "a3f8c91d..."  (64-char HMAC hex)
│             │   # token    = "my-static-token"  (raw secret value)
│             │   # *** MISMATCH — always fails in static mode ***
│             │
│             └── freshdesk/verifier.py:112-113
│                 result = VerificationResult.fail("HMAC mismatch on x-webhook-token")
│                 return self._enforce_result(result)
│                 │
│                 └── freshdesk/verifier.py:147-157
│                     def _enforce_result(result):
│                       if self._enforce:
│                         LOGGER.warning(
│                           "freshdesk.verifier: REJECTED reason=%s", result.reason
│                         )
│                         # ← THIS IS THE LOG LINE SEEN IN PRODUCTION
│                         return result  # valid=False
│                       # enforce=False path: logs warning and returns valid=True (bypass)
│
├── api/routes/webhooks/freshdesk.py:131
│   └── if not result.valid:   ← TRUE when enforce=True
│
└── api/routes/webhooks/freshdesk.py:137-141
    └── return Response(
          content='{"detail":"signature_invalid"}',
          status_code=401   ← THE FAILURE
        )
```

---

## 2. Verifier Inventory — All Webhook Authentication Implementations

### Implementation A — `verify_webhook_token()` (Legacy / Gen 2)

| Property | Value |
|:---|:---|
| **File** | `app/freshdesk_webhook.py` |
| **Line** | 220–260 |
| **Function** | `verify_webhook_token(raw_body, *, provided_token, expected_secret, mode="hmac")` |
| **Route** | `POST /freshdesk/webhook` (Gen 2, `app/main.py:2157`) |
| **Mode-aware** | **YES** — `mode` parameter |
| **Static mode** | **YES** — `if mode == "static":` at line 246 performs direct `hmac.compare_digest(provided_token.strip(), expected_secret.strip())` |
| **HMAC mode** | YES — computes `HMAC-SHA256(secret, body).hexdigest()` when `mode == "hmac"` |
| **Reads FRESHDESK_WEBHOOK_MODE** | YES — via `app_settings.freshdesk_webhook_mode` at `app/main.py:2193` |
| **Replay protection** | NO |
| **Clock skew check** | NO |
| **Enforce flag** | Via HTTP 401 at call site |

**Call site (`app/main.py:2186–2204`):**
```python
if app_settings.freshdesk_webhook_secret:
    token = request.headers.get("X-Webhook-Token", "")
    if not verify_webhook_token(
        raw_body,
        provided_token=token,
        expected_secret=app_settings.freshdesk_webhook_secret,
        mode=app_settings.freshdesk_webhook_mode,   # ← reads FRESHDESK_WEBHOOK_MODE
    ):
        raise HTTPException(status_code=401, ...)
```

---

### Implementation B — `FreshdeskWebhookVerifier` (Golden Path / Gen 3)

| Property | Value |
|:---|:---|
| **File** | `freshdesk/verifier.py` |
| **Line** | 53–170 |
| **Class** | `FreshdeskWebhookVerifier(webhook_secret, *, enforce=True, replay_window_seconds=300)` |
| **Route** | `POST /webhooks/freshdesk/ticket-created` and `POST /webhooks/freshdesk/ticket-updated` |
| **Mode-aware** | **NO** — no `mode` parameter, no conditional branch |
| **Static mode** | **NO** — does not exist in this class |
| **HMAC mode** | ALWAYS — `_compute_hmac()` is called unconditionally at lines 109 and 118 |
| **Reads FRESHDESK_WEBHOOK_MODE** | **NEVER** — not imported, not read, not passed |
| **Replay protection** | YES — 5-minute window, `_check_replay()` at line 132 |
| **Clock skew check** | YES — rejects timestamps > 60s in the future |
| **Enforce flag** | YES — constructor param. Logs "REJECTED" and returns `valid=False` when `enforce=True` |

**Constructor signature (`freshdesk/verifier.py:65–76`):**
```python
def __init__(
    self,
    webhook_secret: str,
    *,
    enforce: bool = True,
    replay_window_seconds: int = 300,
) -> None:
```

There is no `mode` parameter. No path inside this class performs a static (direct secret) comparison.

---

### Implementation C — `_get_verifier()` env-fallback construction (Gateway)

| Property | Value |
|:---|:---|
| **File** | `api/routes/webhooks/freshdesk.py` |
| **Line** | 402–413 |
| **Function** | `_get_verifier(request) → FreshdeskWebhookVerifier \| None` |
| **Purpose** | Service locator — checks `app.state.freshdesk_verifier` first, then constructs from env |
| **Reads FRESHDESK_WEBHOOK_MODE** | **NEVER** — reads only `FRESHDESK_WEBHOOK_SECRET` and `FRESHDESK_WEBHOOK_ENFORCE_HMAC` |

**Full code:**
```python
def _get_verifier(request: Request) -> FreshdeskWebhookVerifier | None:
    runtime = getattr(getattr(request, "app", None), "state", None)
    if runtime is None:
        return None
    verifier = getattr(runtime, "freshdesk_verifier", None)
    if verifier is not None:
        return verifier
    secret = os.getenv("FRESHDESK_WEBHOOK_SECRET", "")
    enforce = os.getenv("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false").lower() == "true"
    if not secret:
        return None
    return FreshdeskWebhookVerifier(secret, enforce=enforce)
    # ↑ Always constructs a pure-HMAC verifier. FRESHDESK_WEBHOOK_MODE is
    #   never passed to this constructor because FreshdeskWebhookVerifier
    #   has no mode parameter.
```

---

## 3. Multiple Authentication Systems: Yes — Source of Truth Violation

Two distinct webhook authentication systems coexist in the repository.

```
Route                                    Auth System           Mode-Aware   Static Support
──────────────────────────────────────   ───────────────────   ──────────   ──────────────
POST /freshdesk/webhook    (Gen 2)       verify_webhook_token  YES          YES
POST /webhooks/freshdesk/ticket-created  FreshdeskWebhookVerifier  NO       NO
POST /webhooks/freshdesk/ticket-updated  FreshdeskWebhookVerifier  NO       NO
```

This is a **source-of-truth violation**. The env var `FRESHDESK_WEBHOOK_MODE=static` was introduced to solve the problem that Freshdesk cannot generate HMAC signatures. It works on the Gen 2 route. It is silently ignored on the Gen 3 routes because `FreshdeskWebhookVerifier` was written to the future n8n-in-the-middle specification (HMAC only) and was never extended to support the same static mode the legacy route has.

The test file `tests/test_sprint2283_e2e.py:298–300` explicitly acknowledges this gap:

```
The Sprint 2.28.x verifier always uses HMAC (not static mode), so we mock
_get_verifier to return None (no-auth mode), matching a test environment
where FRESHDESK_WEBHOOK_ENFORCE_HMAC=false and no verifier is configured.
```

The tests worked around the problem rather than fixing it.

---

## 4. Does FRESHDESK_WEBHOOK_MODE=static Reach the New Route?

**Answer: No. It never reaches `FreshdeskWebhookVerifier`.**

Trace of the env var:

```
FRESHDESK_WEBHOOK_MODE=static
         │
         ▼
app/config.py:306
  freshdesk_webhook_mode = os.getenv("FRESHDESK_WEBHOOK_MODE", "hmac").strip().lower()
  # → Settings.freshdesk_webhook_mode = "static"
         │
         ▼
app/main.py:2172
  app_settings = get_settings()
         │
         ▼
app/main.py:2193  ← ONLY consumer
  mode=app_settings.freshdesk_webhook_mode   ← passed to verify_webhook_token()
  # This line is INSIDE /freshdesk/webhook handler — Gen 2 route ONLY
         │
         X  ← the env var terminates here
         │
         │  FRESHDESK_WEBHOOK_MODE is never read by:
         │    - _get_verifier() (api/routes/webhooks/freshdesk.py:402)
         │    - FreshdeskWebhookVerifier.__init__() (freshdesk/verifier.py:65)
         │    - FreshdeskWebhookVerifier.verify() (freshdesk/verifier.py:78)
```

The env var has zero effect on the Gen 3 routes.

---

## 5. Why freshdesk.verifier Expects HMAC

`FreshdeskWebhookVerifier` was designed exclusively for the **n8n production architecture** described in `freshdesk_integration.md` Section 14.4:

> "FastAPI expects header `X-Webhook-Token` carrying an HMAC-SHA256 signature computed over the webhook payload using a shared secret."

In that architecture, n8n sits between Freshdesk and FastAPI. n8n computes the HMAC signature and injects it into the `X-Webhook-Token` header. When Freshdesk sends the raw webhook, the token header carries a **64-character hex digest**, not the raw secret.

Sprint 2.28.1 built `FreshdeskWebhookVerifier` for that future n8n-in-the-middle production state. The header comment confirms this:

```python
# freshdesk/verifier.py:6-9
# Signature sources:
#   - X-Webhook-Token  (primary): n8n computes HMAC-SHA256(secret, body) and injects this header
#                                before forwarding to FastAPI.
#   - X-Freshdesk-Signature (fallback): direct Freshdesk webhook signature.
```

In the **current Sprint 2.28/2.29 state** (Freshdesk → FastAPI directly, no n8n), Freshdesk's Dispatch'r automation can only send a **static header value** — the raw secret — in `X-Webhook-Token`. This is a Freshdesk platform limitation: Dispatch'r webhook actions do not support HMAC computation on the payload. The `verify_webhook_token(mode="static")` function in `app/freshdesk_webhook.py` was added specifically to handle this. It was never ported to `FreshdeskWebhookVerifier`.

**The exact code path that rejects the request:**

```python
# freshdesk/verifier.py:107–113
token = lower_headers.get("x-webhook-token")
# token = "my-static-secret"  (raw secret from Freshdesk)

if token:
    expected = self._compute_hmac(body)
    # expected = "a3f8c91d2b..." (HMAC-SHA256 of body)
    
    if hmac.compare_digest(expected, token.strip()):
        # "a3f8c91d2b..." == "my-static-secret" → FALSE
        return VerificationResult.ok(...)
    
    result = VerificationResult.fail(f"HMAC mismatch on {_TOKEN_HEADER}")
    # ← THIS FAIL RESULT IS PRODUCED
    return self._enforce_result(result)

# _enforce_result when enforce=True:
# freshdesk/verifier.py:150-152
if self._enforce:
    LOGGER.warning("freshdesk.verifier: REJECTED reason=%s", result.reason)
    return result  # valid=False → 401 returned
```

---

## 6. Side-by-Side Verifier Comparison

### Gen 2 Route: `POST /freshdesk/webhook`

```python
# app/main.py:2186–2204  (simplified)
if app_settings.freshdesk_webhook_secret:
    token = request.headers.get("X-Webhook-Token", "")
    if not verify_webhook_token(
        raw_body,
        provided_token=token,
        expected_secret=app_settings.freshdesk_webhook_secret,
        mode=app_settings.freshdesk_webhook_mode,   # "static" or "hmac"
    ):
        raise HTTPException(status_code=401, ...)

# app/freshdesk_webhook.py:220–260  (simplified)
def verify_webhook_token(raw_body, *, provided_token, expected_secret, mode="hmac"):
    if not provided_token:
        return False

    if mode == "static":
        # ← HANDLES DIRECT FRESHDESK CASE
        # Freshdesk sends the raw secret; compare directly (constant-time)
        return hmac.compare_digest(
            provided_token.strip(),
            expected_secret.strip(),
        )

    # mode == "hmac": n8n pre-computed the digest
    expected = hmac.new(
        expected_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(provided_token.lower(), expected.lower())
```

**Result with Freshdesk static token**: PASS (`mode="static"` does direct comparison)

---

### Gen 3 Routes: `POST /webhooks/freshdesk/ticket-created`

```python
# api/routes/webhooks/freshdesk.py:127,130  (simplified)
verifier = _get_verifier(request)
result = verifier.verify(headers, body, event_timestamp=event_timestamp)

# _get_verifier (api/routes/webhooks/freshdesk.py:402–413)
# Constructs: FreshdeskWebhookVerifier(secret, enforce=enforce)
# NO mode parameter is read or passed.

# freshdesk/verifier.py:107–113  (simplified)
def verify(headers, body, event_timestamp):
    token = lower_headers.get("x-webhook-token")
    if token:
        expected = self._compute_hmac(body)   # ← ALWAYS COMPUTES HMAC
        if hmac.compare_digest(expected, token.strip()):
            return VerificationResult.ok(...)
        # ALWAYS FAILS when token is the raw static secret
        return self._enforce_result(
            VerificationResult.fail("HMAC mismatch on x-webhook-token")
        )
```

**Result with Freshdesk static token**: FAIL (`mode` is non-existent; HMAC always computed)

---

## 7. Key Facts Summary

| Question | Answer |
|:---|:---|
| Does `app.state.freshdesk_verifier` get set at startup? | **NO** — Sprint 2.29 wiring block never sets this key |
| Which code path does `_get_verifier()` take? | Always the env-fallback: constructs `FreshdeskWebhookVerifier(secret, enforce=enforce)` |
| Does `FreshdeskWebhookVerifier` accept a `mode` param? | **NO** — constructor is `__init__(self, webhook_secret, *, enforce, replay_window_seconds)` |
| Does `FRESHDESK_WEBHOOK_MODE=static` affect Gen 3 routes? | **NO** — it is read only by `app/config.py` and consumed only by `app/main.py:2193` |
| When `enforce=False`, does the route pass? | Yes — `_enforce_result()` logs a warning and returns `valid=True` when `enforce=False` |
| Is the failure present whether `enforce=True` or `enforce=False`? | The rejection only happens when `enforce=True`. With `enforce=False` the request passes despite the mismatch |

---

## 8. Root Cause (Single Sentence)

`FreshdeskWebhookVerifier` (Gen 3, `freshdesk/verifier.py`) was built for the future n8n-HMAC production architecture and has no `mode` parameter; `FRESHDESK_WEBHOOK_MODE=static` is read only by the legacy `verify_webhook_token()` function (Gen 2, `app/freshdesk_webhook.py`) and is never passed to or inspected by the Gen 3 verifier, causing it to always attempt HMAC verification against the raw static secret Freshdesk sends, which always fails.

---

## 9. Recommended Fix (Investigation Only — Do Not Implement)

There are three candidate fixes. All three would be changes to a single file.

### Option A — Add `mode` to `FreshdeskWebhookVerifier` (Preferred)

**File**: `freshdesk/verifier.py`

Add a `mode: str = "hmac"` parameter to `__init__()`. In `verify()`, before calling `_compute_hmac()`, branch on `mode`:

- `mode == "static"`: direct `hmac.compare_digest(token.strip(), secret_str.strip())`
- `mode == "hmac"`: existing HMAC path

Then `_get_verifier()` in `api/routes/webhooks/freshdesk.py` must also be updated to read `FRESHDESK_WEBHOOK_MODE` and pass it to the constructor.

**Pro**: Centralises all logic in the verifier. The verifier becomes the single source of truth for both modes, matching the design intent of the Source of Truth document.  
**Con**: Two files change.

---

### Option B — Wire `app.state.freshdesk_verifier` at startup with mode awareness

**File**: `app/main.py` (Sprint 2.29 wiring block)

Set `app.state.freshdesk_verifier` to a mode-aware verifier at startup. Since `FreshdeskWebhookVerifier` currently has no mode support, this would require either Option A first, OR using a shim/subclass.

**Pro**: The `_get_verifier()` first-priority path (checking `app.state`) is already present; no change to the route file is needed.  
**Con**: Requires `FreshdeskWebhookVerifier` to be mode-aware (same as Option A), so this is Option A + wiring.

---

### Option C — Make `_get_verifier()` mode-aware with a custom verify path

**File**: `api/routes/webhooks/freshdesk.py`

Modify `_get_verifier()` to check `FRESHDESK_WEBHOOK_MODE` and, when `"static"`, return a wrapper object that does the direct comparison instead of constructing `FreshdeskWebhookVerifier`. This avoids touching `freshdesk/verifier.py`.

**Pro**: Single file change, no change to the verifier class.  
**Con**: Forks the authentication logic across two implementations rather than unifying them. `FRESHDESK_WEBHOOK_MODE` would still be consumed in two different places.

---

### Immediate Workaround (Zero Code Change)

Set `FRESHDESK_WEBHOOK_ENFORCE_HMAC=false` in the environment. When `enforce=False`, `FreshdeskWebhookVerifier._enforce_result()` at `freshdesk/verifier.py:153–157` logs a warning but returns `valid=True`, allowing all requests through regardless of signature.

**Risk**: Disables all webhook authentication. Acceptable only in a development environment where the service is not publicly reachable. Must not be used in production.

---

## 10. File Locations Quick Reference

| File | Relevance |
|:---|:---|
| `freshdesk/verifier.py:53–170` | `FreshdeskWebhookVerifier` — Gen 3 verifier, HMAC-only |
| `freshdesk/verifier.py:65–76` | Constructor — no `mode` param |
| `freshdesk/verifier.py:107–113` | Token check — always HMAC path |
| `freshdesk/verifier.py:147–157` | `_enforce_result()` — produces the rejection log line |
| `api/routes/webhooks/freshdesk.py:402–413` | `_get_verifier()` — never reads `FRESHDESK_WEBHOOK_MODE` |
| `api/routes/webhooks/freshdesk.py:127–141` | Verify block in `freshdesk_ticket_created()` — rejects on `not result.valid` |
| `app/freshdesk_webhook.py:220–260` | `verify_webhook_token()` — Gen 2, has static mode |
| `app/main.py:2186–2204` | Gen 2 route auth block — passes `mode=app_settings.freshdesk_webhook_mode` |
| `app/config.py:110, 306` | `freshdesk_webhook_mode` field and default `"hmac"` |
| `tests/test_sprint2283_e2e.py:298–300` | Explicit acknowledgement that Gen 3 verifier always uses HMAC |
