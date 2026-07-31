# Local Setup Guide

**Date:** 2026-05-14  
**Platform:** Windows 11 (also works on macOS/Linux with minor path adjustments)  
**IDE:** Cursor (recommended) or VS Code  
**Python:** 3.11 (standardized — do NOT use 3.14 on the new machine)

---

## Prerequisites

Install these on the new machine before cloning:

- [ ] **Python 3.11** — Download from python.org. Confirm: `python --version` → `Python 3.11.x`
- [ ] **Git** — `git --version` should work
- [ ] **Cursor IDE** — Download from cursor.com (or VS Code if preferred)
- [ ] **Supabase account** — Access to the Think360 Supabase project
- [ ] **OpenAI API key** — Access to the Think360 OpenAI account

---

## Step 1 — Clone / Transfer Repository

If migrating from another machine (not fresh clone):
```powershell
# Transfer the repo directory, excluding .venv and __pycache__
# Do NOT copy .env (contains real secrets — recreate from .env.example)
# Do NOT copy .venv (recreate below)
```

If starting from a git remote:
```powershell
git clone <repo-url> C:\Users\<username>\Desktop\Think360\kwikid-ai-ingest
cd "C:\Users\<username>\Desktop\Think360\kwikid-ai-ingest\ai_project\fumadocs_ingest_service"
```

---

## Step 2 — Create Virtual Environment

```powershell
# Always use Python 3.11, not the system default if multiple versions installed
py -3.11 -m venv .venv

# Activate
.venv\Scripts\Activate.ps1

# If PowerShell blocks scripts:
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
.venv\Scripts\Activate.ps1

# Confirm python version in venv
python --version   # Must be 3.11.x
```

---

## Step 3 — Install Dependencies

```powershell
# Ensure venv is activated (prompt shows (.venv))
pip install --upgrade pip
pip install -r requirements.txt

# Verify tiktoken installed (critical for token-safe chunking)
python -c "import tiktoken; print('tiktoken OK')"

# Verify supabase SDK
python -c "from supabase import create_client; print('supabase OK')"

# Verify httpx
python -c "import httpx; print('httpx OK')"
```

If `requirements.txt` doesn't exist, install from `requirements.txt` or manually:
```powershell
pip install fastapi uvicorn supabase httpx tiktoken pydantic python-dotenv pandas openpyxl
```

---

## Step 4 — Configure `.env`

```powershell
copy .env.example .env
```

Open `.env` in editor and fill in these required values:

```ini
# Supabase
SUPABASE_URL=https://your-actual-project.supabase.co
SUPABASE_KEY=your_service_role_key_here

# OpenAI (for embeddings + chat)
OPENAI_API_KEY=sk-...
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small

# Paths (adjust if your data is in a different location)
EXCEL_DATA_PATH=./data/excel
JSON_DATA_PATH=./data/kwikid
FUMADOCS_LOCAL_PATH=./data/fumadocs_repo
```

Leave all other variables at their defaults from `.env.example`.

**NEVER commit `.env` to git.** It is in `.gitignore`.

---

## Step 5 — Apply Supabase Migrations

See `docs/manual_migration_steps.md` for the exact step-by-step process.

Summary:
1. Enable `pgvector` extension in Supabase Dashboard
2. Apply B1_001 through B1_006 in SQL editor (in order)
3. Verify all 6 tables exist

---

## Step 6 — Run Offline Validations

Before touching any live service, confirm the local environment is correct:

```powershell
# From repo root with venv activated
cd "C:\...\fumadocs_ingest_service"

# Token safety validation (no API, no DB)
python scripts/validate_b1_tokens.py
# Expected: 19 passed, 0 failed

# Infrastructure validation (no API, no DB)  
python scripts/validate_b1_infrastructure.py
# Expected: all checks pass

# Ingestion dry-run (no API, no DB writes)
python scripts/validate_b1_ingestion.py
# Expected: 6/6 checks pass
```

If any offline test fails, fix the environment before proceeding.

---

## Step 7 — Run Live Ingestion

```powershell
# Dry-run first (reads parquet, builds chunks, but does NOT embed or upsert)
python -m rag_engine.cli.ingest_cli --mode full --dry-run

# Live ingestion (~15 minutes, ~$0.03 OpenAI cost for 5,817 chunks)
python -m rag_engine.cli.ingest_cli --mode full

# Expected output:
# Embedding complete: 5817 embedded, 0 failed, 0 invalid
# Run status: COMPLETED
```

---

## Step 8 — Run Live Validations

```powershell
# Validate ingestion (requires DB connection)
python scripts/validate_b1_ingestion.py --live --report

# Validate retrieval (requires DB + embeddings)
python scripts/validate_b1_retrieval.py --live --client unity_bank --report

# Validate DB integrity
python scripts/validate_b1_db_integrity.py --live --report
```

All three must pass before B1 is considered live-validated.

---

## Step 9 — Start the API Server

```powershell
# Start FastAPI server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Verify it started
# Open browser: http://localhost:8000/health
# Or: curl http://localhost:8000/health
```

---

## Step 10 — Test Key Endpoints

```powershell
# Health check
curl http://localhost:8000/health

# Vector query (old endpoint, uses public.documents)
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query": "KYC document rejected", "top_k": 5}'

# B1 RAG chat (new endpoint, uses rag_ticket_chunks)
curl -X POST http://localhost:8000/rag/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "Why is the biometric verification failing?", "client": "unity_bank"}'
```

---

## Cursor IDE Setup

**Recommended extensions:**
- Python (Microsoft)
- Pylance
- Ruff (linter)
- GitLens

**Cursor AI setup:**
- Place `docs/ai_agent_handoff.md` contents in Cursor's system prompt / Rules for AI
- This gives Cursor full context without re-explaining the project each session

**Workspace settings (`.cursor/settings.json` or VS Code `.vscode/settings.json`):**
```json
{
  "python.defaultInterpreterPath": ".venv/Scripts/python.exe",
  "python.terminal.activateEnvironment": true,
  "editor.formatOnSave": true
}
```

---

## DO NOT RUN YET — Unfinished Features

These features exist in code but are not production-ready:

| Feature | Status | Why Not Yet |
|---------|--------|------------|
| `FRESHDESK_WEBHOOK_ENABLED=true` | DISABLED | Requires B1 live-validated first |
| `/rag/chat` (public-facing) | Available but not exposed | Requires retrieval precision confirmed |
| SOP ingestion (`--mode sop`) | NOT IMPLEMENTED | B1.5 task |
| `HYBRID_RETRIEVAL_ENABLED=true` | DISABLED | Requires `sql/fts_setup.sql` applied |
| `ENABLE_QUERY_ROUTER=true` | DISABLED | B2 task |

---

## Common Issues

| Problem | Cause | Fix |
|---------|-------|-----|
| `SUPABASE_URL is not set` | `.env` not created or not loaded | `copy .env.example .env` then edit |
| `tiktoken not installed` | Dependency missing | `pip install tiktoken` |
| `ModuleNotFoundError: rag_engine` | Not running from repo root | `cd` to `fumadocs_ingest_service/` |
| `connection refused: localhost:11434` | Ollama not running (wrong embedding provider) | Set `EMBEDDING_PROVIDER=openai` in `.env` |
| `PowerShell cannot be loaded` | Execution policy | `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` |
| `httpx.ConnectError: getaddrinfo failed` | DNS / no internet | Check network; will retry automatically |
| Token validation test fails | tiktoken encoder mismatch | Ensure tiktoken is installed; check Python 3.11 |

---

## Resuming from Current Checkpoint

After migration, the exact checkpoint is:

> B1 is offline-validated. SQL migrations not yet applied. No live ingestion run completed.

Resume from Step 5 (Apply Supabase Migrations) after completing Steps 1–4 of setup.

The full path to pick up development after migration:
```
Setup (Steps 1–4) → Migrations (Step 5) → Offline validate (Step 6) → 
Live ingest (Step 7) → Live validate (Step 8) → 
THEN: begin B1.5 work per docs/current_project_state.md
```
