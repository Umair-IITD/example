# SOP Ingestion Guide

This guide is for support team leads and admin personnel who need to add or update SOPs
in the KwikID RAG system so that they become searchable in AI-assisted ticket resolution.

No backend code changes are required. You only need:
1. A markdown file in the correct format
2. One terminal command to reingest
3. One validation command to confirm

---

## SOP File Format

Each SOP is a markdown file with a YAML frontmatter header. Store files in `data/sop/`.

### Required frontmatter fields

```yaml
---
title: "Short, descriptive SOP title"
query_type: "AUTH_ISSUE"
issue_area: "Authentication"
---
```

| Field | Required | Description |
|---|---|---|
| `title` | Yes | Human-readable SOP name (shown in citations) |
| `query_type` | Yes | Maps to query routing category (see list below) |
| `issue_area` | No | Broader area for categorisation |
| `clients` | No | Leave blank for global SOP. Use `["unity_bank"]` to restrict to one tenant |

### Supported `query_type` values

```
AUTH_ISSUE         KYC_PROCESS         ACCOUNT_MANAGEMENT
TECHNICAL_ERROR    BILLING_ACCOUNT     ESCALATION
GENERAL_KNOWLEDGE  POLICY_COMPLIANCE   VIDEO_KYC
```

Use the value that best describes the type of ticket this SOP addresses.

### Optional frontmatter fields

```yaml
---
title: "Account Lockout - Resolution Procedure"
query_type: "AUTH_ISSUE"
issue_area: "Authentication"
clients: []                   # [] = global (all tenants). ["unity_bank"] = tenant-specific
sop_id: "sop_account_lockout" # Optional: stable ID for this SOP. Auto-generated if omitted.
---
```

If you omit `sop_id`, the system generates one from the filename. Always set `sop_id` explicitly
if you plan to update this SOP later — it ensures the update overwrites the right record.

### Markdown body

Write the SOP as standard markdown with clear section headings (`##`). Each `##` section
becomes a separate retrievable chunk.

- Use numbered steps for procedures
- Use tables for decision trees
- Avoid abbreviations without expanding them first
- Keep each section self-contained (the LLM may only see one section at a time)

Example:

```markdown
---
title: "OTP Verification Failure — Support Resolution"
query_type: "AUTH_ISSUE"
issue_area: "Authentication"
sop_id: "sop_otp_failure"
---

## Overview

Resolve OTP delivery failures for KwikID video KYC and login flows.

## Step 1: Confirm OTP delivery channel

Check whether the customer is expecting OTP via SMS or email...

## Step 2: Troubleshoot SMS non-delivery

...
```

---

## Adding a New SOP

1. Create the markdown file in `data/sop/`:

   ```
   data/sop/my_new_sop.md
   ```

2. Run reingestion for the SOP table (replace `unity_bank` with the target tenant, or omit
   `--client` to run for all clients when using a global SOP):

   ```bash
   python scripts/reingest_v2.py --client unity_bank --tables sop --verbose
   ```

   For a global SOP (no `clients:` restriction in frontmatter):

   ```bash
   python scripts/reingest_v2.py --client unity_bank --tables sop --verbose
   ```

   The system automatically processes all `.md` files in `data/sop/` that match the client's
   scope. Global SOPs (empty `clients:[]`) are always included.

3. Confirm the SOP is retrievable:

   ```bash
   python scripts/validate_b3_knowledge.py --client unity_bank
   ```

   Or query the RAG endpoint directly with a query that should match this SOP.

---

## Updating an Existing SOP

When a SOP's content changes, the pipeline detects the content hash difference and:
1. Bumps the SOP `version` field (v1 → v2 → ...)
2. Deletes old chunk rows for that `sop_id`
3. Re-embeds and inserts new chunks

This ensures stale sections never mix with updated ones.

**Steps:**

1. Edit the markdown file in `data/sop/`
2. Run the same reingestion command:

   ```bash
   python scripts/reingest_v2.py --client unity_bank --tables sop --verbose
   ```

3. Validate:

   ```bash
   python scripts/verify_index_integrity.py --client unity_bank --tables rag_sop_chunks
   ```

If the content is unchanged (you just re-ran without editing), the pipeline will produce
0 DB writes — it is fully idempotent.

---

## Deactivating / Retiring an SOP

Do not delete SOP files if you want a clean audit trail. Instead, mark the SOP as inactive
in the database directly via Supabase UI or SQL:

```sql
UPDATE rag_sop_library
SET is_active = FALSE
WHERE sop_id = 'sop_account_lockout';
```

The `is_active = FALSE` flag causes the SOP to be excluded from both the semantic search
RPC (`match_all_b1_sources`) and the FTS search (`search_b1_sources_fts`). The SOP record
and its chunks remain in the database for rollback.

To re-activate:

```sql
UPDATE rag_sop_library
SET is_active = TRUE
WHERE sop_id = 'sop_account_lockout';
```

---

## Coexistence with v1 and v2 Chunks

The reingestion command writes SOP chunks with the index_version controlled by `B1_INDEX_VERSION`
in `.env`. If `ACTIVE_INDEX_VERSION=v2`, also set `B1_INDEX_VERSION=v2` so newly ingested SOP
chunks are retrieved by the active pipeline.

To check which index_version your SOP chunks use:

```bash
python scripts/verify_index_integrity.py --client unity_bank --tables rag_sop_chunks --index-version v1
python scripts/verify_index_integrity.py --client unity_bank --tables rag_sop_chunks --index-version v2
```

---

## Rollback

To roll back all v2 SOP chunks for a client:

```bash
python scripts/reingest_v2.py --client unity_bank --tables sop --rollback --verbose
```

This deletes all `index_version=v2` rows from `rag_sop_chunks` and `rag_sop_library`
for the specified client. v1 chunks are NOT touched.

After rollback, set `ACTIVE_INDEX_VERSION=v1` in `.env` and restart the server.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| SOP not appearing in RAG responses | `is_active=FALSE` on the library record | Set `is_active=TRUE` in Supabase |
| SOP appearing for wrong tenant | Frontmatter `clients:` is empty (global) when it should be restricted | Add `clients: ["unity_bank"]` and reingest |
| Duplicate chunks after reingest | `sop_id` changed between runs (file renamed) | Set explicit `sop_id:` in frontmatter that matches the old SOP |
| Reingest fails with unique constraint error | `B1_009_sop_unique_constraint.sql` migration not applied | Apply the migration in Supabase SQL editor |
| SOP retrieved but LLM hedges heavily | SOP similarity score is low for the query | Revise SOP title and section headings to use query-matching language |

---

## Required Fields Summary

```
rag_sop_library columns written by ingestion:
  sop_id        (auto or from frontmatter)
  title         (from frontmatter)
  version       (integer, auto-bumped on content change)
  query_type    (from frontmatter)
  issue_area    (from frontmatter or "")
  clients       (from frontmatter, [] = global)
  content       (full markdown body)
  content_hash  (SHA256, for idempotency)
  index_version (from B1_INDEX_VERSION env var)
  is_active     (TRUE on new ingest)
```
