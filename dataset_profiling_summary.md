# Dataset Profiling Summary
## KwikID Freshdesk Export — Executive Engineering Summary

> **Phase**: READ-ONLY engineering discovery (complete)
> **Analysis Date**: 2026-05-12
> **Outputs**: 5 analysis documents + 2 profiling scripts

---

## 1. What Was Analyzed

| File | Format | Rows | Has Description | Has RCA |
|---|---|---|---|---|
| `84000794141_tickets-...09_53 (1).xls` | SpreadsheetML XML | 1,993 | ❌ | ❌ |
| `84000794166_tickets-...10_29.csv` | UTF-8 CSV | 1,920 | ❌ | ❌ |
| `RBL_RCA 1.xlsx` | Excel OOXML (Sheet1) | 869 | ✅ | ✅ |
| `RBL_RCA.xlsx` | Excel OOXML (Unity) | 2,339 | ✅ | ✅ |
| **Total** | | **7,121** | | |
| **Unique ticket IDs (est.)** | | **~5,194** | | |

---

## 2. The Five Most Important Findings

### Finding 1 — XLS and CSV are Near-Duplicates (96–99% overlap)
The two largest files (`84000794141...xls` and `84000794166...csv`) cover almost identical ticket sets — exported at different timestamps on the same day. **They should not both be ingested.** CSV is preferred (standard encoding, simpler parser). The XLS adds only ~76 ticket IDs not in the CSV and has no Description field — minimal marginal value.

### Finding 2 — Description is the Critical Field, But It's Only in the XLSX Files
The only files with full ticket body text (`Description`) are `RBL_RCA 1.xlsx` (869 rows) and `RBL_RCA.xlsx` (2,339 rows) — together **3,208 tickets with actual content**. The XLS and CSV files have only Subject lines (50–68 character average) with no body text. This means **61% of the raw record count has no usable ticket body for AI training**.

### Finding 3 — Description Field Requires Mandatory Preprocessing Before Any Ingestion
Of the 3,208 Description-bearing tickets:
- **65–76% contain raw HTML tags** — LLMs will process `<div>`, `<span>`, `<table>` as literal text
- **48–60% contain email signatures and legal disclaimers** — these dominate vector embeddings and cause false high-similarity matches
- **2–4% are auto-generated system messages** — test emails, VKYC session notifications, server messages

Without stripping: the RAG corpus will surface legal boilerplate and HTML noise as "relevant context." This is the single highest-priority preprocessing requirement.

### Finding 4 — The AI System is Already Partially Live (and Producing Training Labels)
The Freshdesk tag data reveals that the AI pipeline has already been operating on live tickets:
- `ai_auto_replied`: **158 tickets** — AI already auto-posted replies
- `ai_note`: **557 tickets** — AI added internal notes
- `rag_context`: **8 tickets** — RAG pipeline confirmed running

These tagged tickets are a **built-in evaluation set** — they contain AI-generated responses alongside the eventual human resolution. This is immediately usable as a feedback/quality dataset.

### Finding 5 — ~45% of CSV Tickets Are Not Customer Support (Should Be Excluded from AI)
The largest single category in the CSV dataset is `Server Alert` (26.4%), followed by `Non support related (internal)` (10.1%), `Auditor Hold` (5.9%), `Call Test` (4.2%), and manual operational tasks (ID creation, mapping changes, repush, etc.). Together, these **exclude-from-AI categories represent ~45% of the CSV ticket volume** — a dataset that appears to be "1,920 support tickets" is really only ~1,050 actual customer queries.

---

## 3. Dataset Corpus Map (Post-Dedup, Post-Filter Estimates)

```
Raw files: 7,121 total rows
     │
     ├── Remove XLS (near-duplicate of CSV, no Description): -1,993
     │
     └── Remaining: ~5,128 unique tickets
              │
              ├── CSV/XLS batch (metadata only — no Description):
              │    ~1,920 tickets → ~1,050 after filtering non-support
              │    Useful for: ticket classification, SOP status, interaction counts
              │    NOT useful for: description-level RAG, training Q→A pairs
              │
              └── XLSX batch (with Description + RCA):
                   3,208 tickets → ~2,600 after filtering non-support
                        │
                        ├── With substantive Description AND RCA: ~200–400 (Gold)
                        ├── With Description, no RCA: ~2,200 (Silver — RAG corpus)
                        └── Non-support / too short / all-HTML: ~600 (Exclude)
```

---

## 4. Data Quality Scorecard

| Dimension | Score | Key Issue |
|---|---|---|
| Completeness (key fields) | 4/10 | Description absent in 61% of rows; RCA only 14–27% |
| Text cleanliness | 3/10 | HTML + signatures + auto-messages heavily contaminate |
| Classification consistency | 6/10 | Query Type / Issue Area populated for ~89% of XLSX rows |
| Deduplication | 9/10 | No within-file duplicates; cross-file overlap clearly mapped |
| Schema consistency | 5/10 | 4 different schemas; type mismatches; column renaming needed |
| Temporal coverage | 7/10 | ~2 years of data; adequate for trend analysis |
| Resolution documentation | 4/10 | 87–96% of Resolution Classification is null |
| SOP link quality | 5/10 | 55% SOP Present in CSV but SOP text itself not in dataset |

**Overall data quality: 5.4 / 10** — Functional for a first-generation RAG corpus; inadequate for supervised fine-tuning without significant enrichment.

---

## 5. Automation Feasibility Summary

Based on the combined dataset signal analysis:

```
Total unique tickets (~5,194):

  ❌ EXCLUDE (non-support, internal ops, server alerts):
     ~1,600 tickets (31%)
     → Server alerts, meetings, test mails, ID/mapping/repush ops

  ✅ AUTO_RESOLVABLE (~28–36% of remaining ~3,600):
     ~1,000–1,300 tickets
     → Recurring, closed within SLA, low interactions, SOP present
     → Categories: Login failures, OTP issues, Send Link, Portal issues, Audio

  ⚠️ HUMAN_REVIEW_REQUIRED (~40–46%):
     ~1,450–1,650 tickets
     → AI drafts response; human agent approves before posting
     → Categories: Video-related (needs session ID), connectivity,
                   unclassified "Others", API issues

  🔴 ESCALATION_REQUIRED (~18–26%):
     ~650–940 tickets
     → High priority, SLA violated, dev RCA pending, Asana escalated
     → Categories: Production outages, complex backend bugs,
                   compliance holds, infrastructure incidents
```

---

## 6. Recommended Data Pipeline Architecture

Based on profiling findings, the following ingestion pipeline is recommended for the next phase:

```
Phase 1 — Source Selection
├── PRIMARY:  RBL_RCA.xlsx  (2,339 tickets, Description + RCA) ✅
├── SECONDARY: RBL_RCA 1.xlsx (869 tickets, Description + RCA + session_ids) ✅
├── METADATA: CSV file (1,920 tickets, no Description — classification signals only)
└── EXCLUDE:  XLS file (near-duplicate of CSV, XML parser complexity, no Description)

Phase 2 — Mandatory Preprocessing per ticket
├── 1. Strip HTML tags from Description (html.parser/BeautifulSoup)
├── 2. Remove email signatures (regex: "Thanks & Regards", "Best Regards", "Disclaimer:")
├── 3. Remove legal disclaimers and bank boilerplate
├── 4. Unescape HTML entities (&amp; → &, &lt; → <, &nbsp; → space)
├── 5. Normalize whitespace (collapse multiple newlines, strip leading/trailing)
├── 6. Filter rows: exclude if Query Type IN exclude-list
└── 7. Filter rows: exclude if Description <100 chars after cleaning

Phase 3 — Content Enrichment
├── Extract session_ids from free text (if not in session_ids column)
├── Extract bank/client name from Subject if Clients field is null
├── Parse Handling Time to integer minutes
└── Normalize timestamps to UTC ISO8601

Phase 4 — Document Construction (per ticket)
├── Tier A (Gold):  Subject + cleaned Description + substantive RCA (≥50 chars)
├── Tier B (Silver): Subject + cleaned Description (no RCA)
└── Tier C (Metadata): Subject + Query Type + Issue Area + SOP Status only

Phase 5 — Metadata Tagging for RAG
└── source_type: "freshdesk"
    ticket_id, ticket_url, status, priority, client/tenant
    query_type, issue_area, environment
    has_rca: bool, rca_length: int
    has_sop: bool, sop_status
    resolution_class, issue_recurrence
    handling_time_mins, agent_interactions
    created_at, resolved_at
    ai_replied: bool (from tags)
```

---

## 7. Knowledge Gaps Identified — Teach-the-AI Targets

Categories with `SOP Status = "No SOP Available"` represent gaps in the knowledge base:
- **141 tickets in CSV** have no SOP — these are unresolved knowledge gaps
- Top gap categories (by Query Type): API Issues (~23), Video Recovery (~22), User Form Issue (~23)
- These should be prioritized in the `/train` (Teach-the-AI) feature to capture expert knowledge

---

## 8. Client-Specific Observations

| Client | Primary Dataset | Volume | Key Issues | Multi-tenant Risk |
|---|---|---|---|---|
| Unity Small Finance Bank | RBL_RCA1 + RBL_RCA | ~1,800 tickets | Video KYC, Audio, Connectivity | HIGH — different product config |
| Bank of Baroda | CSV | ~183 (tagged) | General VKYC issues | Medium |
| RBL Bank | RBL_RCA1 (file name origin) | ~13 tagged | Mixed | Medium |
| Bajaj Finance | CSV | ~24 (tagged) | Mixed | Medium |

**Critical**: Unity Bank tickets dominate the XLSX files (~500 tagged). Their issues are specific to their VKYC deployment (agent IDs, call routing, their server infrastructure). AI responses must never mix Unity-specific session details with BoB or RBL context. The `Clients` column is the tenant isolation key.

---

## 9. Immediate Next Steps (Engineering Priority Order)

| Priority | Action | Estimated Effort |
|---|---|---|
| P0 | Build HTML + signature stripping preprocessor for Description field | 1–2 days |
| P0 | Write unified schema normalizer (merge 4 files → standard 18-col schema) | 1 day |
| P0 | Implement exclusion filter (Query Type blocklist, empty-after-clean filter) | 0.5 days |
| P1 | Ingest cleaned XLSX tickets into Supabase via existing `/ingest` endpoint | 1 day |
| P1 | Tag all ingested tickets with `client`/`tenant` metadata for scoped retrieval | 0.5 days |
| P2 | Extract session IDs from Description free text (regex NER) | 1 day |
| P2 | Build quality-tier labeling (Gold/Silver/Bronze) for training data selection | 1 day |
| P3 | Evaluate existing `ai_auto_replied` tickets against human resolutions | 2 days |
| P3 | Submit "No SOP Available" tickets to Teach-the-AI workflow for knowledge capture | Ongoing |

---

## 10. Output Files Generated

| Report | Location | Content |
|---|---|---|
| `dataset_schema_analysis.md` | `C:\Users\HP\Desktop\Think360\` | Column schemas, type analysis, cross-file comparison |
| `data_quality_analysis.md` | `C:\Users\HP\Desktop\Think360\` | HTML, signatures, nulls, duplicates, remediation pipeline |
| `query_pattern_analysis.md` | `C:\Users\HP\Desktop\Think360\` | Category distributions, SOP coverage, topic mining, client analysis |
| `automation_feasibility_report.md` | `C:\Users\HP\Desktop\Think360\` | 3-class classification, feasibility matrix, risk assessment |
| `dataset_profiling_summary.md` | `C:\Users\HP\Desktop\Think360\` | This document — executive summary |
| `profiling_step1.py` | `C:\Users\HP\Desktop\Think360\` | Initial file discovery script (READ-ONLY) |
| `profiling_deep.py` | `C:\Users\HP\Desktop\Think360\` | Deep single-dataset profiling script (READ-ONLY) |
| `profiling_deep2.py` | `C:\Users\HP\Desktop\Think360\` | Cross-file analysis, RCA, subject mining script (READ-ONLY) |

---

*No source data files were modified during this analysis. All findings are derived from read-only profiling.*
