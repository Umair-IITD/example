# Security Checklist

Use this checklist before any of: git commit, machine migration, production deployment, or sharing the codebase with a new developer.

---

## Pre-Commit Checklist

Run before every `git commit` or `git push`:

- [ ] `python scripts/security_scan.py` passes with 0 findings
- [ ] `.env` is in `.gitignore` and NOT staged (`git status` shows no `.env`)
- [ ] No real API keys or URLs in `.env.example` (only placeholder text)
- [ ] No hardcoded secrets in any `.py` file (grep for `sk-`, `Bearer`, `xvsok`, `supabase.co`)
- [ ] No PII datasets staged (`*.csv`, `*.xlsx`, `*.parquet` in git status)
- [ ] No `__pycache__/` or `.venv/` staged

---

## Pre-Migration Checklist (Moving to Enterprise Machine)

- [ ] Run `python scripts/security_scan.py --path . --report` — review all findings
- [ ] Verify `documents_video_realted.csv` contains no PII, or exclude from migration
- [ ] Verify `Telegram + Freshdesk...json` contains no secrets
- [ ] Ensure `.env` is NOT copied to the new machine — create fresh from `.env.example`
- [ ] Confirm `.venv/` is NOT copied — recreate with `pip install -r requirements.txt`
- [ ] Confirm `data/fumadocs_repo/` (git clone) is NOT copied — re-clone on new machine
- [ ] Confirm `__pycache__/` is NOT copied (git clean or .gitignore handles this)
- [ ] Review `KNOWN_ISSUES.md` — note `supabasesuccess.py` has a hardcoded key (if that file exists)

---

## Endpoint Security Checklist (Before Exposing Service to Network)

- [ ] Service is behind VPN or private network — NOT on public internet
- [ ] `FRESHDESK_WEBHOOK_ENABLED=false` unless explicitly enabling B3
- [ ] `FRESHDESK_WEBHOOK_SECRET` is set to a strong random value if webhook is enabled
- [ ] API endpoints have at minimum IP allowlist or shared API key header
- [ ] `/docs` (FastAPI auto-docs) is disabled in production (`app = FastAPI(docs_url=None, redoc_url=None)`)
- [ ] Log output does not contain API keys (check with `grep -r "sk-" logs/`)

---

## Secrets Management Checklist

- [ ] `SUPABASE_URL` — project URL (not secret, but avoid public exposure)
- [ ] `SUPABASE_KEY` — service_role key (SECRET — treat as root password)
- [ ] `OPENAI_API_KEY` — OpenAI key (SECRET — has billing implications)
- [ ] `OPENAI_CHAT_API_KEY` — can be same as above
- [ ] `FRESHDESK_API_KEY` — Freshdesk API key (SECRET)
- [ ] `FRESHDESK_WEBHOOK_SECRET` — HMAC signing secret (SECRET)
- [ ] All secrets above should be injected via environment variables, NEVER hardcoded
- [ ] Rotate any key that was ever committed to git history

---

## Runtime Security Checklist

- [ ] Python version pinned in `Dockerfile` and `requirements.txt`
- [ ] `pip audit` run and 0 HIGH/CRITICAL vulnerabilities
- [ ] Supabase RLS policies applied (B1 migrations B1_001–B1_006)
- [ ] Supabase `anon` key is NOT used in any server-side code
- [ ] Chat history table has a retention policy defined
- [ ] Log files rotated — `LOG_DIR` does not accumulate unbounded
