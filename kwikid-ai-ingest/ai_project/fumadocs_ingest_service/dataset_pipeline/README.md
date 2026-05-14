# KwikID Dataset Engineering Pipeline

Transforms noisy Freshdesk ticket exports into clean, structured datasets for RAG ingestion,
automation classification, RCA mining, and AI evaluation.

---

## Quick Start

```bash
# From the fumadocs_ingest_service directory:
python -m dataset_pipeline.run

# Run specific stages only
python -m dataset_pipeline.run --stages filter
python -m dataset_pipeline.run --stages label
python -m dataset_pipeline.run --log-level DEBUG
```

---

## Architecture

```
Source files (READ-ONLY)
    │
    ├── 84000794166_...csv          (1,920 rows — UTF-8 CSV)
    ├── 84000794141_...xls          (1,993 rows — SpreadsheetML XML)
    ├── RBL_RCA 1.xlsx              (  869 rows — OOXML, Sheet1)
    └── RBL_RCA.xlsx                (2,339 rows — OOXML, Unity sheet)
    │
    ▼
Stage 1: Load      — Robust parsers; SpreadsheetML via lxml; no source mutation
Stage 2: Normalize — Map 4 schemas → CanonicalTicket (Pydantic v2); dedup within source
Stage 3: Filter    — Blocklist exclusion (Server Alert, Meeting, internal ops, etc.)
Stage 4: Clean     — HTML strip (BeautifulSoup4) + email signature removal (regex)
Stage 5: RCA Score — GOLD / SILVER / TRIVIAL / NONE quality tiers
Stage 6: Label     — Deterministic 4-class automation labeling
Stage 7: Extract   — Query taxonomy JSON + knowledge card candidates
Stage 8: Eval      — Evaluation dataset (AI-tagged + GOLD + SILVER RCA tickets)
Stage 9: Export    — 8 output artifacts + preprocessing quality report
```

---

## Output Artifacts

| File | Location | Contents |
|---|---|---|
| `unified_cleaned_dataset.parquet` | `data/processed/` | All 3,635 included tickets (Parquet, ~4MB) |
| `unified_cleaned_dataset.csv` | `data/processed/` | Same dataset in CSV format |
| `gold_dataset.csv` | `data/processed/` | 49 GOLD-tier tickets (rich RCA + description) |
| `automation_labels.csv` | `data/processed/` | All 5,204 tickets with automation class labels |
| `evaluation_dataset.json` | `data/processed/` | 219 Q→A eval pairs (AI-tagged + GOLD + SILVER) |
| `query_taxonomy.json` | `data/processed/` | 35 query types with SOP + automation signals |
| `knowledge_card_candidates.json` | `data/processed/` | 102 tickets ideal for Teach-the-AI |
| `preprocessing_report.md` | `data/reports/` | Quality metrics, exclusion breakdown, tenant distribution |

---

## Pipeline Numbers (last run)

| Stage | Metric | Value |
|---|---|---|
| S1 Load | Total rows | 7,121 |
| S2 Normalize | Canonical tickets | 5,204 |
| S2 Normalize | XLS deduped against CSV | 1,917 (96% overlap) |
| S3 Filter | Excluded (non-support) | 1,569 (30.1%) |
| S3 Filter | Top exclusion: Server Alert | 688 tickets |
| S4 Clean | HTML stripped | 1,971 tickets |
| S4 Clean | Signatures stripped | 246 tickets |
| S5 RCA Score | GOLD | 49 (1.3%) |
| S5 RCA Score | SILVER | 169 (4.6%) |
| S6 Label | AUTO_RESOLVABLE | 786 (15.1%) |
| S6 Label | HUMAN_REVIEW_REQUIRED | 1,545 (29.7%) |
| S6 Label | ESCALATION_REQUIRED | 1,304 (25.1%) |

---

## Module Layout

```
dataset_pipeline/
├── config.py                  # PipelineConfig dataclass; all paths + thresholds
├── run.py                     # CLI entrypoint (argparse)
├── models/
│   └── ticket.py              # CanonicalTicket (Pydantic v2)
├── loaders/
│   ├── schema.py              # Column name → canonical field maps (case-insensitive)
│   ├── loader_csv.py          # CSV + SpreadsheetML XLS parsers
│   └── loader_xlsx.py         # OOXML XLSX parser (openpyxl)
├── preprocessing/
│   ├── html_cleaner.py        # BeautifulSoup4 HTML → plain text
│   └── signature_cleaner.py   # Email signature + disclaimer stripping
└── stages/
    ├── s1_load.py             # Load all 4 source files
    ├── s2_normalize.py        # Map to CanonicalTicket + dedup
    ├── s3_filter.py           # Exclusion filter (auditable)
    ├── s4_clean.py            # Text cleaning
    ├── s5_rca_score.py        # RCA quality scoring
    ├── s6_automate_label.py   # Automation class labeling
    ├── s7_extract.py          # Query taxonomy + knowledge cards
    ├── s8_eval.py             # Evaluation dataset
    └── s9_export.py           # Write all 8 artifacts
```

---

## Automation Classification Rules

**AUTO_RESOLVABLE** (ALL must be true):
- Status = Closed or Resolved
- Resolution status = Within SLA
- Agent interactions ≤ 3
- Issue Recurrence = "Recurring issue"

**ESCALATION_REQUIRED** (ANY is sufficient):
- Priority = High or Urgent
- Resolution status = SLA Violated
- RCA status = "RCA Pending from dev"
- Asana Ticket Link populated
- Query Type in escalation blocklist (Server Alert, Production Down, DC-DR, etc.)

**EXCLUDE**:
- Query Type in exclusion blocklist (Server Alert, Meeting, Test Mail, internal ops, etc.)

**HUMAN_REVIEW_REQUIRED**: everything else.

---

## RCA Quality Tiers

| Tier | Criteria |
|---|---|
| GOLD | `cleaned_rca` ≥ 150 chars AND contains ≥ 1 technical keyword |
| SILVER | `cleaned_rca` ≥ 50 chars |
| TRIVIAL | `cleaned_rca` < 30 chars OR matches trivial phrase list |
| NONE | No RCA text present |

---

## Safety Constraints

- Source files in `C:\Users\HP\Desktop\Think360\` are **never modified**
- All loaders open files in read-only mode
- The pipeline can be re-run safely (outputs are overwritten, inputs are not)
