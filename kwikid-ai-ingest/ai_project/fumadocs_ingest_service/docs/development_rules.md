# Development Rules

**Status:** BINDING. These rules exist because we have been burned by not following them.  
**Date:** 2026-05-14  
**Scope:** All development on the KwikID AI Ingest project.

---

## Rule 1 — Validation Gates Are Mandatory

**NEVER** proceed to the next phase without completing all validation gates for the current phase.

```
B1 gates before starting B1.5:
  □ validate_b1_ingestion.py --live   → PASS
  □ validate_b1_retrieval.py --live   → PASS
  □ validate_b1_db_integrity.py --live → PASS

B1.5 gates before starting B2:
  □ SOP ingestion pipeline tested end-to-end
  □ Retrieval MRR@5 ≥ 0.70 on unity_bank labeled eval set
  □ Threshold tuning documented in data/reports/retrieval_eval.md

B2 gates before enabling B3:
  □ /rag/chat tested with 20+ real queries
  □ Confidence calibration verified (low/medium/high thresholds make sense)
  □ No hallucinations on known-answer queries
```

**Why:** Compounding errors. A bad retriever + bad generator = impossible to debug which layer failed.

---

## Rule 2 — NEVER Enable B2/B3 Before B1 Live-Validation

`FRESHDESK_WEBHOOK_ENABLED` must stay `false` until B1 passes all live validation.

`/rag/chat` must not be exposed publicly until retrieval precision is confirmed.

**Why:** Without a populated, validated vector DB, every AI response is fabricated from thin air. This will produce wrong answers that look confident, eroding trust in the system.

---

## Rule 3 — Always Run Security Scan Before Commit

```powershell
python scripts/security_scan.py
```

Exit code must be 0 before any `git commit`. If the scan finds CRITICAL or HIGH findings, fix them before committing.

**Why:** API keys were accidentally included in `.env.example` before. The scan prevents recurrence.

---

## Rule 4 — Never Hardcode Credentials

All secrets must come from environment variables. The only acceptable patterns for credentials in code:

```python
os.getenv("SOME_KEY", "")          # OK — reads from env
_require_env("SOME_KEY")           # OK — reads from env, fails fast if missing
os.environ["SOME_KEY"]             # OK — reads from env
```

**Never:**
```python
api_key = "sk-abc123..."           # NEVER
url = "https://real-url.supabase.co"  # NEVER
```

---

## Rule 5 — Always Validate Token Limits Before Embedding

Every code path that calls the embedding API must pass through `BatchEmbeddingProcessor._validate_chunk()`. Never call `provider.embed_batch()` directly with untrusted input.

**Why:** OpenAI's text-embedding-3-small has an 8192-token hard limit. Sending oversized input causes silent truncation or API errors. This was the original crash that prompted the B1 stabilization pass.

---

## Rule 6 — Preserve Tenant Isolation

Every query to `rag_ticket_chunks` must include a `client` filter. The `TicketRetriever` enforces this — never bypass it with raw SQL or direct Supabase queries.

```python
# ALWAYS use TicketRetriever with explicit client:
request = RetrievalRequest(client="unity_bank", query_text="...", top_k=10)
results = retriever.retrieve(request, embedding)

# NEVER:
supabase.table("rag_ticket_chunks").select("*").execute()  # no client filter
```

**Why:** Supabase RLS provides one layer; the application provides the second. Neither alone is sufficient for production multi-tenancy.

---

## Rule 7 — No Destructive Refactors While B1 is Unvalidated

While B1 has not completed live validation, do not:
- Rename tables, columns, or migration files
- Change the B1_INDEX_VERSION without running a full rebuild
- Modify the 3-chunk strategy (ISSUE_HEADER, QUERY_BODY, RESOLUTION_RCA)
- Change the UUID5 deterministic ID formula

**Why:** These changes invalidate the deduplication checker and force a full re-ingest. During the validation window, this wastes time and money.

---

## Rule 8 — Always Run Offline Validation on a New Machine First

When setting up on a new machine, run offline validations before connecting to any external service:

```powershell
python scripts/validate_b1_tokens.py          # must be 19/19
python scripts/validate_b1_infrastructure.py  # must pass all checks
python scripts/validate_b1_ingestion.py       # dry-run, no DB
```

If any offline test fails on the new machine, fix the environment (Python version, dependencies, imports) before running anything live.

---

## Rule 9 — Never Add Features to a Broken Foundation

If B1 validation scripts fail, do not continue adding B1.5/B2/B3 features. Fix B1 first.

The project state machine is:

```
[B1 offline-validated] → [B1 live-validated] → [B1.5 planned] → [B1.5 complete] → [B2/B3]
```

Jumping steps is how bugs compound silently.

---

## Rule 10 — Document All Architecture Decisions

When making a non-obvious design decision (chunking strategy, tenant isolation mechanism, retry policy, confidence thresholds), document WHY in the relevant `docs/` file. Future developers (and AI agents) will not have the historical chat context.

The `docs/ai_agent_handoff.md` file is the canonical context document for AI assistants. Update it when the project state changes significantly.

---

## Rule 11 — The Service Role Key Is Root Access

`SUPABASE_KEY` (service_role key) bypasses all Row-Level Security policies. Treat it like a database root password:
- Never log it
- Never return it in an API response
- Never commit it to git
- Never pass it to client-side code
- Rotate it immediately if you suspect exposure

---

## Rule 12 — Test Idempotency Before Live Runs

Before running a live ingestion, verify the idempotency guarantee holds:

```powershell
# Run dry-run twice, confirm same chunk IDs
python -m rag_engine.cli.ingest_cli --mode full --dry-run --verbose
# Check that the chunk IDs in the output are stable
```

If the same ticket produces different chunk IDs across runs, `B1_INDEX_VERSION` or the UUID5 formula has changed. Fix before live run.
