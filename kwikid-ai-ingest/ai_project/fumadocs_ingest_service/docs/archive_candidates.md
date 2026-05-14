# Archive Candidates

**Date:** 2026-05-14  
**Status:** Review before migration. Do NOT delete — move to `archive/` or tag with `# DEAD` if confirmed unused.

---

## Dead Root-Level Scripts (n8n Patch Scripts)

These scripts were written as one-off patches to an n8n workflow running on a different machine. They import `requests` and write directly to a hardcoded n8n API URL. None are imported by any other module or tested. They have zero value on a new machine.

| File | Why Dead |
|------|----------|
| `fix_minimal_reply_issue.py` | One-off n8n node patch — fixes a reply format bug in a running n8n workflow. No longer applicable. |
| `fix_public_body.py` | One-off n8n patch — corrects the public reply body field. Superseded by B3 webhook. |
| `patch_formatter_labels.py` | One-off n8n patch — fixes formatter node labels. Dead once the workflow was re-imported. |
| `reapply_formatter_safeguards.py` | One-off n8n patch — re-adds formatter guards after accidental deletion. Dead. |
| `refine_public_reply_professional.py` | One-off n8n patch — rewrites reply prompt for professional tone. Dead. |
| `remove_result_tags_public_reply.py` | One-off n8n patch — strips XML-style tags from public replies. Dead. |
| `update_n8n_workflow.py` | References a hardcoded absolute path to an n8n export JSON on a DIFFERENT machine. Dead on any other machine. |
| `update_prompt_grounding.py` | Updates prompts in n8n workflow. Dead — prompt management is now in B2 `prompt_builder.py`. |
| `validate_workflow_changes.py` | Validates n8n workflow JSON structure. Dead — n8n is not the primary automation path anymore (B3 webhook is). |

**Recommendation:** Move all 9 files to `archive/n8n_patch_scripts/` before migration. They are not importable in CI and will only cause confusion.

---

## Oversized Data File

| File | Issue |
|------|-------|
| `documents_video_realted.csv` | CSV dataset left at repository root (note: filename has typo "realted"). Should be in `data/processed/` or removed entirely. |
| `Telegram + Freshdesk -_ OpenAPI Issue Analysis -_ Supabase RAG -_ OpenAPI Response (8).json` | Analysis JSON with spaces in the name. Not importable, not tested. Move to `data/` or `docs/`. |

---

## Potentially Superseded Scripts in `scripts/`

| File | Status | Notes |
|------|--------|-------|
| `scripts/evaluate_rag.py` | Superseded | Early RAG eval — replaced by `evaluation/` module and B1.5-B retrieval eval plan |
| `scripts/export_supabase_schema.py` | Still useful | Use before migration to snapshot the old schema |
| `scripts/export_video_ticket_conversations.py` | Possibly dead | Video-specific export — unclear if still needed |
| `scripts/generate_video_issues_report.py` | Possibly dead | Video report — unclear if still needed |
| `scripts/freshdesk_export_ticket_fields.py` | Still useful | Useful for exploring Freshdesk field structure |
| `scripts/freshdesk_ticket_fields_probe.py` | Still useful | Useful for exploring Freshdesk field structure |
| `scripts/ingest_freshdesk_ticket_ids.py` | Superseded | Pre-B1 ingestion script — B1 pipeline (`ingest_cli.py`) replaces this |
| `scripts/list_freshdesk_contacts_by_email_domain.py` | Possibly useful | Admin utility |

---

## Modules with Dead Code

| Module | Dead Element | Notes |
|--------|-------------|-------|
| `rag_engine/observability/metrics_collector.py` | `MetricsCollector` class | Instantiated in pipeline but never read or logged (REVIEW.md M4) |
| `rag_engine/cli/sample_retrieval.py` | Entire file | Not imported by anything, not tested (REVIEW.md L5) |
| `retrieval/` (root-level) | Entire directory | Pre-B1 retrieval implementation — superseded by `rag_engine/retrieval/` |
| `query_router/` (root-level) | Entire directory | Pre-B1 router stubs — superseded by B2 plan in `rag_engine/` |
| `observability/` (root-level) | Entire directory | Pre-B1 observability — superseded by `rag_engine/observability/` |
| `app/chat.py`, `app/chunker.py`, `app/embedder.py`, `app/embeddings.py` | Possibly dead | Pre-B1 implementations — verify if `app/main.py` still imports them |

---

## Action Checklist Before Migration

- [ ] Create `archive/n8n_patch_scripts/` and move the 9 dead n8n scripts
- [ ] Move `documents_video_realted.csv` → `data/processed/` or delete
- [ ] Move `Telegram + Freshdesk...json` → `docs/` or `data/`
- [ ] Confirm which `scripts/` scripts are truly dead before deleting
- [ ] Confirm `retrieval/`, `query_router/`, `observability/` at root are not imported by `app/main.py`
- [ ] Add a `# ARCHIVE` header comment to any file confirmed dead but kept for reference
