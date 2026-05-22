# Security Review

## Summary

Production hardening was performed across two sessions. All critical and high severity issues have been resolved. The system is safe to push publicly with one required pre-deploy action (Supabase key rotation).

## Issues Found and Resolved

### CRITICAL

| Issue | Resolution | File |
|-------|-----------|------|
| Live Supabase service-role JWT committed in `supabasesuccess.py` | File deleted via `git rm` | Deleted |

**Action required**: The JWT `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...` was committed to git history. It exists in prior commits even though the file is deleted. **Before any organizational push: rotate the Supabase service-role key in the Supabase dashboard.**

### HIGH

| Issue | Resolution | File |
|-------|-----------|------|
| `/ready` endpoint exposed `str(exc)` — leaked Supabase URL and connection details to unauthenticated callers | Replaced with `"error": "dependency_check_failed"`, full exception logged server-side | `app/main.py:448–458` |
| `.env.example` had `DEBUG_RAG=true` — if copied to production, ticket PII (content_preview) exposed to API callers | Changed to `DEBUG_RAG=false` | `.env.example` |
| `.env.example` had `FASTAPI_DOCS_ENABLED=true` — full API schema exposed via unprotected `/openapi.json` | Changed to `FASTAPI_DOCS_ENABLED=false` | `.env.example` |
| `has_knowledge_context` always False — knowledge-only queries misclassified as `weak_match`, causing unnecessary escalations | Fixed: `has_knowledge_context = any(c.source_table == "rag_knowledge_chunks" for c in chunks)` | `rag_engine/generation/chat_generator.py` |
| `"pending.*review"` treated as literal substring — security-freeze detection silently failed | Fixed: replaced with proper regex `pending\s+(?:a\s+)?(?:security\s+)?review` | `rag_engine/sop/sop_parser.py` |

### MEDIUM

| Issue | Resolution | File |
|-------|-----------|------|
| One-off maintenance scripts with hardcoded absolute paths to another developer's machine | Deleted via `git rm` | `fix_*.py`, `patch_*.py`, `update_*.py`, etc. |
| `postman/` accidentally gitignored — Postman collections were not tracked | Removed from `.gitignore` | `.gitignore` (root) |
| `.postman/` (local Postman env files with API secrets) not in root `.gitignore` | Added `.postman/` to root `.gitignore` | `.gitignore` (root) |
| Docker volumes not persisted — logs, traces, reports lost on container restart | Added 3 volume mounts | `docker-compose.yml` |
| `redis>=5.0.8` unpinned — silent breaking upgrade possible | Pinned to `redis==5.0.8` | `requirements.txt` |
| `prometheus-client>=0.21.0` unpinned | Pinned to `prometheus-client==0.21.0` | `requirements.txt` |
| Inner CI file invisible to GitHub — CI never ran | Created root-level `.github/workflows/main.yml` | New file |
| `CHAT_CONTEXT_CHUNK_MAX_CHARS=1400` in `.env.example` silently truncated SOP chunks | Changed to 3500 in `.env.example` and fixed code default | `.env.example`, `app/config.py` |

## API Key Security

### Constant-Time Comparison

All API key validation uses `hmac.compare_digest()` over all valid keys simultaneously:

```python
def _constant_time_key_check(provided: str, valid_keys: frozenset[str]) -> bool:
    provided_bytes = provided.encode("utf-8")
    result = False
    for key in valid_keys:
        result |= hmac.compare_digest(provided_bytes, key.encode("utf-8"))
    return result
```

**Why bitwise-OR instead of short-circuit `or`?** Short-circuit evaluation (`a or b`) stops on the first True — creating a timing oracle. With bitwise-OR, all keys are always checked, taking constant time regardless of which key matches.

### Key Rotation Without Downtime

`RAG_API_KEYS=key1,key2,key3` allows multiple simultaneous valid keys. Rotate by:
1. Add new key to `RAG_API_KEYS`
2. Restart service
3. Update all clients to use new key
4. Remove old key from `RAG_API_KEYS`
5. Restart service

## Webhook HMAC

The Freshdesk webhook (`/freshdesk/webhook`) validates requests using HMAC-SHA256:

```python
expected = hmac.new(
    secret.encode("utf-8"),
    body,
    hashlib.sha256
).hexdigest()
if not hmac.compare_digest(expected, provided_signature):
    raise HTTPException(403, "Invalid webhook signature")
```

`FRESHDESK_WEBHOOK_ENFORCE_HMAC=true` ensures the service refuses to start if the webhook is enabled but no secret is configured — preventing accidental unauthenticated webhook deployment.

## Rate Limiting

Two-layer rate limiting:

1. **In-process** (default): Sliding window per IP using `collections.deque`. Resets on service restart. No external dependencies.

2. **Redis** (optional): Sliding window per IP with atomic Lua script in Redis. Survives worker restarts. Shared state across multiple uvicorn workers.

**Graceful degradation**: If Redis is unavailable, falls back to in-process limiting transparently.

## Unprotected Paths

These paths bypass API key authentication — verified safe:

| Path | Reason | Risk Mitigation |
|------|--------|-----------------|
| `/health` | Kubernetes/Docker health probe | Returns only `{"status": "ok"}` |
| `/ready` | Dependency readiness check | Returns sanitized error strings only |
| `/freshdesk/webhook` | External webhook receiver | HMAC signature validation |
| `/docs`, `/redoc`, `/openapi.json` | FastAPI docs | Only available when `FASTAPI_DOCS_ENABLED=true` |

## Information Leakage

| Surface | Status | Notes |
|---------|--------|-------|
| `/ready` exception details | Fixed | `str(exc)` → `"dependency_check_failed"` |
| Debug chunk content in API responses | Controlled by `DEBUG_RAG=false` | PII risk; production default is false |
| Log PII from ticket content | Not logged | Ticket content truncated in log messages |
| SOP internal structure in diagnostics | Sanitized | `sop_branch_flags` redacted when `DEBUG_RAG=false` |

## Prompt Injection

Accepted risk with mitigation:
- All user input passes through the RAG pipeline (retrieval-grounded, not direct LLM)
- Human-in-the-loop: final answers are reviewed before sending to customers in the current workflow
- Input sanitization: basic HTML/markdown stripping in query preprocessor
- No persistent memory that could be poisoned long-term (per-session history only)

A full prompt injection mitigation layer (input/output scanning) is planned for Phase 2.

## Subprocess Safety

`app/git_sync.py` uses subprocess for git operations. Analysis confirms safety:
- Command list is hardcoded: `["git", "clone", "--depth", "1", ...]`
- `repo_url` comes from `FUMADOCS_REPO_URL` env var (not user input)
- `timeout=GIT_COMMAND_TIMEOUT_S` prevents indefinite hanging
- `check=True` raises on non-zero exit code
- No shell=True

## Remaining Known Risks

| Risk | Severity | Action Required |
|------|----------|-----------------|
| Supabase JWT in git history | HIGH | Rotate key in Supabase dashboard before any org push |
| `FRESHDESK_WEBHOOK_ENFORCE_HMAC=false` in local `.env` | MEDIUM | Set to `true` before enabling webhook in production |
| `DEBUG_RAG=true` in local `.env` | LOW | Acceptable for dev; must be `false` in production deploy |
| `FASTAPI_DOCS_ENABLED=true` in local `.env` | LOW | Acceptable for dev; must be `false` in production deploy |
| app/train.py:535 PII redaction TODO | LOW | Non-blocking; schedule for Phase 2 |
| Docker bind-mount permissions (non-root user UID 1001 vs root-owned host dirs on Linux) | LOW | Add `chown -R 1001:1001 logs/ traces/ data/reports/` to deploy runbook |
