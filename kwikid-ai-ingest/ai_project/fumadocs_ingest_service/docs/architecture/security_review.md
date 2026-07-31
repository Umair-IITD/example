# Security Review

**Date:** 2026-05-14  
**Scope:** Full repository audit — secrets, endpoints, data handling, dependency surface  
**Classification:** Internal — do not share publicly

---

## CRITICAL: Secrets Exposure Risks

### SC-1 — Supabase URL Previously in `.env.example` (FIXED)
**File:** `.env.example`  
**Status:** FIXED (2026-05-13)  
The real Supabase project URL (`https://xvsokgmkoogooysuldks.supabase.co`) was committed in `.env.example`. It has been replaced with `https://your-project-id.supabase.co`. However:
- If this file was ever committed to a git repository with the real URL, the URL is in git history
- The Supabase project URL is not itself a secret (it's in every Supabase project's dashboard), but it narrows the attack surface for credential stuffing
- **Action:** Verify `.env.example` has no other real values before migration

### SC-2 — No `.gitignore` (FIXED)
**Status:** FIXED (2026-05-14)  
There was no `.gitignore`. This means `.env`, `.venv/`, `__pycache__/`, and data files containing customer data could have been committed accidentally. `.gitignore` now exists.

### SC-3 — `documents_video_realted.csv` at Repository Root
**Status:** OPEN  
A CSV file containing (potentially) real support ticket data is sitting at the repository root with no gitignore protection. Verify whether this file contains PII (email addresses, customer names, ticket details) before migration. If yes: do not commit to any version-controlled repository.

### SC-4 — `Telegram + Freshdesk...json` at Repository Root
**Status:** OPEN  
A JSON analysis file is at the root. Inspect for embedded secrets or PII before committing.

---

## API Key Handling

### SK-1 — `SUPABASE_KEY` is the Service Role Key
The `SUPABASE_KEY` environment variable must be the Supabase **service_role** key, which bypasses Row-Level Security. This key must NEVER be:
- Committed to any `.env` file in version control
- Logged in any log output
- Included in any API response or error message
- Passed to browser clients (front-end code)

### SK-2 — OpenAI API Key Validation (IMPLEMENTED)
`OpenAIEmbeddingProvider.__init__()` validates the API key is non-empty and not a placeholder. It fails fast before making any network calls. Error messages do NOT include the key value.

### SK-3 — LLM Error Response Sanitization (IMPLEMENTED)
`rag_engine/generation/llm_client.py` sanitizes API error response bodies before logging. Regex patterns strip `Bearer [token]` and `sk-[key]` patterns. Logs are capped at 300 chars.

### SK-4 — Webhook Token Verification (IMPLEMENTED)
`app/freshdesk_webhook.py` uses `hmac.compare_digest()` (constant-time) for HMAC-SHA256 webhook token verification. No timing side-channel leakage.

### SK-5 — Freshdesk API Key in env only
`FRESHDESK_API_KEY` is read from environment only. It is never logged or returned in any response. The `FreshdeskReplyClient` uses HTTP Basic Auth (base64 of `key:X`), which stays in the Authorization header and is not logged.

---

## Endpoint Security

### EP-1 — No Authentication Layer on FastAPI Endpoints
**Severity:** HIGH  
**Status:** OPEN  
All `/ingest`, `/query`, `/chat`, `/rag/chat`, `/train/*`, `/freshdesk/*` endpoints are publicly accessible with no API key, JWT, or IP allowlist. Anyone who knows the service URL can:
- Trigger embedding runs (cost)
- Query the knowledge base (data leak)
- Post fake webhook events (manipulation)

**Mitigation until proper auth is added:**
- Run service behind a VPN or private network only
- Set `FRESHDESK_WEBHOOK_ENABLED=false` (default) until B1 is stable
- Add API key header check as minimal auth: `X-API-Key: <shared_secret>`

### EP-2 — `/freshdesk/webhook` is Disabled by Default (IMPLEMENTED)
`FRESHDESK_WEBHOOK_ENABLED=false` means the endpoint returns `503 Service Unavailable` unless explicitly enabled. Safe default.

### EP-3 — Webhook Payload Size Not Bounded
**Severity:** MEDIUM  
**Status:** OPEN  
The `/freshdesk/webhook` endpoint reads `await request.body()` with no size limit. A large payload (e.g., 100MB) would be loaded entirely into memory before validation. Add `Content-Length` check or body size limit.

### EP-4 — No Rate Limiting
**Severity:** MEDIUM  
**Status:** OPEN  
The `/rag/chat` endpoint calls OpenAI per request. Without rate limiting, a single client can trigger unlimited LLM calls, causing cost overrun.

---

## Data Handling

### DH-1 — Customer Ticket Data (PII)
The ingestion pipeline processes real support tickets that may contain:
- Customer email addresses (in ticket metadata)
- Customer names
- Business-sensitive KYC failure reasons

These are stored in Supabase. Ensure:
- Supabase project is in an appropriate region for data residency
- RLS policies are applied (B1 migrations include RLS)
- Only `service_role` key is used server-side; `anon` key is NOT used anywhere

### DH-2 — Embeddings Are Not Reversible — But Are Searchable
Vector embeddings cannot be reversed to reconstruct the original text, but they can be used to perform similarity searches that retrieve the original chunks. The embedding store IS the data store. Treat it accordingly.

### DH-3 — Chat History Table Contains Conversation Logs
`CHAT_HISTORY_TABLE=chat_messages` stores full conversation turns. This table will accumulate sensitive support query history. Ensure this table is included in any data retention / GDPR deletion workflows.

---

## Dependency Surface

### DS-1 — Python 3.11 and 3.14 `.pyc` Files Both Present
Both `cpython-311` and `cpython-314` pyc files exist in `__pycache__/`. This indicates two different Python versions have been used. Standardize on one version before migration.

### DS-2 — Key Dependencies (verify no known CVEs before migration)
```
fastapi          — web framework
uvicorn          — ASGI server
supabase         — Supabase Python SDK
httpx            — async HTTP client
openai           — OpenAI SDK (not used directly — httpx is used for raw calls)
tiktoken         — tokenizer (used in token-aware chunking)
pydantic         — data validation
pandas           — data processing
openpyxl         — Excel parsing
python-dotenv    — env loading
```

Run `pip audit` or `safety check` before production deployment.

---

## Summary Risk Matrix

| Finding | Severity | Status |
|---------|----------|--------|
| No auth on API endpoints | HIGH | OPEN |
| Real Supabase URL in `.env.example` (historical) | HIGH | FIXED |
| No `.gitignore` | HIGH | FIXED |
| PII in `documents_video_realted.csv` | HIGH | OPEN — verify |
| No webhook payload size limit | MEDIUM | OPEN |
| No rate limiting on LLM endpoints | MEDIUM | OPEN |
| Chat history contains PII | MEDIUM | OPEN — policy needed |
| Multiple Python version pyc files | LOW | OPEN |
| `SUPABASE_KEY` naming ambiguity | LOW | OPEN |
