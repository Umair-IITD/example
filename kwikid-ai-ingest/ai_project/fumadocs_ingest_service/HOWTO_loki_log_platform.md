# HOWTO — Multi-Tenant Log Platform (Grafana Loki) Integration

**Sprint 2.60** | Blueprint §34 + §35 | Last updated: 2026-07-29

---

## Overview

The Log Platform integration retrieves VKYC session backend logs from Grafana
Loki, applies mandatory PII redaction and BM25 relevance extraction, and
delivers a curated log excerpt to the L1 investigation pipeline. Raw logs are
scrubbed immediately after extraction and never reach the LLM, Freshdesk notes,
or audit records.

### Data flow (one VKYC session)

```
GetSessionDetailsTool          GetSessionLogsTool
     │                               │
     │ start_epoch, end_epoch ──────▶│
     │                               │
     │                         LokiClient
     │                         (sync httpx, asyncio.to_thread)
     │                               │
     │                         Three-phase sweep
     │                         1. ingress session_id filter
     │                         2. service_name union (always)
     │                         3. server-label per host
     │                               │
     │                         raw_text (string) ◀── NEVER leaves this scope
     │                               │
     │                         parse_line() per raw line
     │                           └─ _redact_and_truncate()   ← PII first
     │                               │
     │                         extract_relevant_lines()
     │                           ├─ BM25 score
     │                           ├─ LEVEL_WEIGHT boost (error=8, warn=4)
     │                           ├─ SERVICE_HINT_BOOST (+3 for known services)
     │                           ├─ forced error-level inclusion
     │                           ├─ auditor duration-window inclusion
     │                           └─ 9 KB char budget, chronological re-sort
     │                               │
     │                         raw_text = None (scrub)
     │                               │
     │                         curated_log_excerpt ──────────────────────▶ LLM
```

---

## How PII Compliance Is Enforced

### Rule (Blueprint §27, §35): Redact BEFORE scoring

`_redact_and_truncate()` in `case_engine/integrations/loki/relevance.py` runs
inside `parse_line()`, which executes **before** any score is computed or any
line is added to the budget. This ordering is non-negotiable: a 110,000-char
Aadhaar photo base64 string exists in real SaaS logs, and if it reached the
scoring stage it would consume the entire 9 KB budget.

```
parse_line(raw_line, index)
    │
    ├─ call _redact_and_truncate(raw_line)
    │       ├─ _PII_KEY_PATTERN.sub("[REDACTED]", ...)   ← JSON key-value redaction
    │       └─ text[:500]                                 ← hard truncation
    │
    └─ return LogLine(content=redacted_text, ...)         ← score computed later
```

### PII fields redacted

The regex `_PII_KEY_PATTERN` matches JSON key-value pairs whose keys match:

- `aadhaar*` (Aadhaar number and any derived field)
- `pan*` / `pan_number*`
- `dob` / `date_of_birth`
- `address` / `*_address`
- `*_image` / `*photo*` (base64 photo/document data)
- `income*` / `marital_status` / `occupation`
- `name*` (customer name fields)

If a log line contains `"aadhaar_number": "1234 5678 9012"`, the output is
`"aadhaar_number": "[REDACTED]"`. The line is then hard-truncated to 500 chars.

### Raw text scrub

```python
try:
    raw_text = await asyncio.to_thread(fetch_session_logs, client, ...)
    ...  # process into curated_excerpt
finally:
    raw_text = None   # defence-in-depth: explicit scrub
```

`_async_fetch()` in `log_tools.py` returns only `curated_log_excerpt` (a
`str`). `raw_text` is never returned, never logged, and explicitly set to
`None` in the `finally` block.

### Never-raise contract

`GetSessionLogsTool.run()` catches all exceptions. All failure paths return the
canonical dict with `log_availability` set to `UNAVAILABLE` or `DISABLED`.
No exception from the Loki layer can bubble into the LLM prompt builder.

---

## How to Add a New Tenant

### Step 1 — Register the datasource in `loki/client.py`

```python
LOKI_REGISTRY: dict[str, LokiDatasource] = {
    "saas": LokiDatasource(
        client_name="SaaS",
        datasource_name="loki - saas",
        uid="bfm1x2kaaifb4c",
        base_url="https://utility-server-sfd.app.getkwikid.com",
    ),
    # ADD NEW TENANT HERE:
    "bob": LokiDatasource(
        client_name="BOB",
        datasource_name="BOB - PROD - LOKI",
        uid="eft1zle0wzk00f",
        base_url="http://43.204.15.162:3000",
        via_grafana_proxy=True,     # set True if routing through Grafana proxy
    ),
}
```

Set `via_grafana_proxy=True` if the Loki instance is accessible only through
the Grafana datasource proxy (`/api/datasources/proxy/uid/{uid}/loki/api/v1/`).
Leave `False` for direct Loki API access (`/loki/api/v1/`).

### Step 2 — Add credentials to `LokiConfig` in `loki/config.py`

```python
@dataclass
class LokiConfig:
    saas_base_url: str
    saas_username: str
    saas_password: str
    # ADD:
    bob_base_url: str = ""
    bob_username: str = ""
    bob_password: str = ""
    enabled: bool = True
    timeout: float = 30.0
    query_limit: int = 1000

    @classmethod
    def from_env(cls) -> "LokiConfig":
        return cls(
            saas_base_url=os.getenv("LOKI_SAAS_URL", ""),
            saas_username=os.getenv("LOKI_SAAS_USERNAME", ""),
            saas_password=os.getenv("LOKI_SAAS_PASSWORD", ""),
            # ADD:
            bob_base_url=os.getenv("LOKI_BOB_URL", ""),
            bob_username=os.getenv("LOKI_BOB_USERNAME", ""),
            bob_password=os.getenv("LOKI_BOB_PASSWORD", ""),
            ...
        )
```

### Step 3 — Wire credentials in `LokiClient.__init__`

```python
if tenant_key == "saas":
    base_url = config.saas_base_url
    username = config.saas_username
    password = config.saas_password
elif tenant_key == "bob":          # ADD THIS BLOCK
    base_url = config.bob_base_url or self.ds.base_url
    username = config.bob_username
    password = config.bob_password
else:
    base_url = self.ds.base_url
    username = ""
    password = ""
```

### Step 4 — Add env vars to `.env.example`

```bash
# BOB Loki credentials
LOKI_BOB_URL=http://43.204.15.162:3000
LOKI_BOB_USERNAME=
LOKI_BOB_PASSWORD=
```

### Step 5 — Invoke with `tenant="bob"` from the tool

```python
result = tool.run({
    "session_id": "abc-123",
    "start_epoch": 1722000000.0,
    "end_epoch": 1722003600.0,
    "tenant": "bob",          # ← pass tenant key here
    "ticket_query": "face liveness failed",
})
```

No changes needed to `relevance.py` or `GetSessionLogsTool` — they are
tenant-agnostic above the `LokiClient` layer.

---

## Testing Locally

### Check DISABLED mode (no credentials)

```bash
# Unset Loki credentials
unset LOKI_SAAS_USERNAME LOKI_SAAS_PASSWORD

python -c "
from case_engine.tools.adapters.log_tools import GetSessionLogsTool
tool = GetSessionLogsTool()
r = tool.run({'session_id': 'test-123'})
print(r['log_availability'])   # should print: DISABLED
"
```

### Run the integration test suite

```bash
pytest tests/test_sprint260_loki_integration.py -v
```

Expected: 39/39 pass in ~7 seconds. All tests use `httpx.MockTransport` — no
real Loki credentials required.

### Smoke test against real Loki (with credentials in .env)

```bash
# With real credentials set in .env
python -c "
import asyncio
from case_engine.tools.adapters.log_tools import GetSessionLogsTool
tool = GetSessionLogsTool()
result = tool.run({
    'session_id': '<real-session-uuid>',
    'ticket_query': 'face liveness failed',
    'tenant': 'saas',
})
print('availability:', result['log_availability'])
print('lines:', result['log_line_count'])
print('chars:', result['log_char_count'])
print(result['curated_log_excerpt'][:500])
"
```

Check `log_availability`:
- `AVAILABLE` — logs found and curated.
- `PARTIAL` — HTTP 200 but no lines matching session_id (may be retention aging).
- `UNAVAILABLE` — HTTP call failed; check `error` field.
- `DISABLED` — credentials not set.

---

## Key Configuration Reference

| Constant | Location | Value | Purpose |
|---|---|---|---|
| `_FETCH_BUFFER` | `log_tools.py` | `timedelta(minutes=5)` | Window padding around session timeline |
| `char_budget` | `relevance.py::extract_relevant_lines` | `9000` | Max chars delivered to LLM |
| `top_k` | `relevance.py::extract_relevant_lines` | `40` | Max lines before budget trim |
| `LEVEL_WEIGHT["error"]` | `relevance.py` | `8.0` | Error-level priority multiplier |
| `SERVICE_HINT_BOOST` | `relevance.py` | `3.0` | Service-name match score boost |
| `max_lines` | `client.py::parse_loki_response` | `500` | Max raw lines before Loki payload cap |
| `_DEDUP_THRESHOLD` | `relevance.py::_collapse_duplicates` | `3` | Consecutive identical-line collapse |
