# KwikID AI Ingest Service — Handover Checklist

**For the departing engineer.** Complete every item before handover is considered done.

---

## Section A: Code & Git

- [ ] **All feature branches merged or explicitly abandoned.** The active branch is `major-architecture-change`. Confirm with incoming engineer whether to merge to `main` now or after post-deploy verification.
- [ ] **No uncommitted changes.** Run `git status` — should be clean. If Umair's docs reorg files are uncommitted, commit them separately: `git add docs/ && git commit -m "docs: reorganize documentation structure"`.
- [ ] **Sprint cert reports present.** Confirm `sprint-2-6-3.md` and `sprint-2-6-4.md` exist at project root.
- [ ] **`.remember/remember.md` is current.** Contains the latest handoff state.
- [ ] **`.gitignore` verified.** Confirm `.env`, `.env.local`, `secrets.json`, `credentials.json`, `data/asana_webhook_secrets.json` are all listed — they are (verified 2026-07-31).
- [ ] **No secrets in git history.** Run: `git log --oneline --all | head -20` and spot-check that no commit message mentions "add .env" or "add credentials".

---

## Section B: Secrets to Share Manually

**The following files and values contain live secrets and MUST NOT be committed to Git. Share them securely via Teams/Slack DM or a secure credential vault.**

### Files
| File | What It Contains |
|------|-----------------|
| `.env` | All production credentials (OpenAI, Supabase, Freshdesk, Unity, Loki, Asana, Sentry) |
| `data/asana_webhook_secrets.json` | Asana webhook X-Hook-Secret (gitignored; required for L2 resolution loop) |

### Individual Secrets (if `.env` cannot be shared as a file)
| Secret | Where Used |
|--------|-----------|
| `OPENAI_API_KEY` | NLP Router + Intelligence Layer |
| `SUPABASE_KEY` | All database operations |
| `FRESHDESK_API_KEY` | All Freshdesk writes |
| `FRESHDESK_WEBHOOK_SECRET` | HMAC verification of incoming webhooks |
| `UNITY_PASSWORD` | Unity Admin Portal service account |
| `LOKI_SAAS_USERNAME` + `LOKI_SAAS_PASSWORD` | Grafana Loki log retrieval |
| `METRICS_PLATFORM_API_KEY` | Uptime Kuma metrics |
| `ASANA_API_KEY` | Asana task creation and webhook management |
| `SENTRY_DSN` | Sentry error tracking |
| `RAG_API_KEY` | RAG chat API access |

---

## Section C: Admin Access to Transfer

- [ ] **Freshdesk admin access** transferred to incoming engineer (or a shared admin account documented).
  - URL: `https://getkwikid.freshdesk.com/a/admin`
  - Incoming engineer needs to see: Dispatch'r rules, Observer rules, Agent accounts

- [ ] **Asana access** confirmed for incoming engineer.
  - Project: "Support Escalation" in Think360 workspace
  - Incoming engineer needs to see/edit the project and verify webhook settings

- [ ] **Supabase project access** confirmed.
  - URL: from `SUPABASE_URL` in `.env`
  - Incoming engineer needs: project admin access (for SQL migrations, RLS review)

- [ ] **OpenAI account access** or API key rotation rights transferred.
  - Rotate `OPENAI_API_KEY` immediately if the departing engineer was using a personal account

- [ ] **Unity Bank service account credentials** documented.
  - SOT recommends storing in AWS Secrets Manager at `kwikid/unity/vkyc_api_credentials`
  - Confirm incoming engineer can access that path

- [ ] **Sentry project access** confirmed.
  - Org: `think360-n0`, project: `python-fastapi`
  - URL: `https://think360-n0.sentry.io`

- [ ] **GitHub/Bitbucket repo access** confirmed for incoming engineer on `major-architecture-change` branch.

---

## Section D: Pending Admin Actions (do before handing over)

- [ ] **Create `ai.support@getkwikid.com`** Freshdesk agent account and update `FRESHDESK_API_KEY` (see `REMAINING_BLOCKERS.md §Blocker 1`).
- [ ] **Re-register Asana webhook** on production public URL (see `REMAINING_BLOCKERS.md §Blocker 2`).
- [ ] **Make AUTH_ENABLED decision** and document it in `.env` with a comment explaining why (see `REMAINING_BLOCKERS.md §Blocker 3`).

---

## Section E: Knowledge Transfer Sessions

- [ ] **Architecture walkthrough:** Walk the incoming engineer through `PROJECT_GUIDE.md §3` (Complete Lifecycle of a Ticket) using a real Freshdesk ticket as an example.
- [ ] **Live demo:** Send a test ticket through Freshdesk (or Postman) and trace it through the logs in real time.
- [ ] **On-call runbook:** Explain what to check in Sentry first, then logs (`LOG_DIR=./logs`), then Freshdesk conversation history.
- [ ] **SOT docs location confirmed:** Incoming engineer has access to `C:\Users\...\kwikid_support_system\Source_Of_Truth\`.

---

