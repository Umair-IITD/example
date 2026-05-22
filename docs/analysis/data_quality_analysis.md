# Data Quality Analysis
## KwikID Freshdesk Export — Production Dataset Profiling

> **Phase**: READ-ONLY engineering discovery
> **Analysis Date**: 2026-05-12

---

## 1. Critical Quality Issues Overview

| Issue | Severity | Files Affected | Impact on AI |
|---|---|---|---|
| Description field is raw HTML | 🔴 Critical | RBL_RCA1, RBL_RCA | LLM receives HTML tags instead of readable text |
| Email signatures in Description | 🔴 Critical | RBL_RCA1 (89.7%), RBL_RCA (95%) | Signature noise dominates retrieved context |
| `Summary` column is all "Not Set" | 🔴 Critical | CSV, XLS, RBL_RCA1 | False signal — must exclude |
| XLS file has no Description | 🔴 Critical | XLS only | Ticket body text completely missing |
| RCA coverage only 14–27% | 🟠 High | RBL_RCA1, RBL_RCA | Most resolution reasoning is unrecorded |
| Short/trivial RCA text | 🟠 High | Both XLSX | 128 of 237 RCAs < 30 chars (too brief for training) |
| Auto-generated messages mixed in | 🟠 High | All files | Noise from test emails, system notifications |
| 30–35% of schema is 100% null | 🟡 Medium | All files | Bloats ingestion, wastes embedding tokens |
| session_ids 55.5% null | 🟡 Medium | RBL_RCA1 | KwikID sessions unlinked for many tickets |
| Duplicate Tags column in XLS | 🟡 Medium | XLS only | Parse-time collision — wrong tags get ingested |
| Timestamp strings (CSV/XLS) | 🟡 Medium | CSV, XLS | Require explicit parsing, may fail on malformed values |
| SOP status 64–90% unpopulated | 🟡 Medium | RBL_RCA, RBL_RCA1 | SOP availability unclear for most tickets |
| Query Type / Issue Area 8–11% null | 🟢 Low | All files | Missing classification for ~10% of tickets |

---

## 2. HTML Contamination in Description Field

The `Description` field is the most content-rich column for AI training. However, it is severely contaminated with raw HTML.

| File | Has HTML | HTML Presence Rate |
|---|---|---|
| RBL_RCA1 (869 rows) | 567 / 865 non-null | **65.5%** |
| RBL_RCA (2339 rows) | 1,768 / 2,339 | **75.6%** |

**What the HTML contains:**
- `<div>`, `<p>`, `<span>`, `<br>` formatting tags
- `<table>` structures (form data, structured fields)
- `<img>` tags (inline images embedded as base64 or URLs — 0.1% of rows)
- `<a href="...">` hyperlinks — sometimes contain diagnostic URLs
- Microsoft Outlook HTML email thread formatting (the majority)

**Remediation required**: Strip all HTML tags using `html.parser` or `BeautifulSoup`, preserve block-level line breaks, unescape HTML entities (`&amp;`, `&lt;`, `&gt;`, `&nbsp;`), and normalize whitespace.

**Character length impact**: Median raw description is 1,894–2,154 characters (HTML included). After stripping, estimated reduction to 400–900 characters of actual content.

---

## 3. Email Signature Contamination

Email signatures are present in the overwhelming majority of Description fields. This is because tickets originate from email threads and Freshdesk stores the full email body including recurring signature blocks.

| File | Signature-containing Descriptions | Rate |
|---|---|---|
| RBL_RCA1 | 415 / 865 | **48.0%** |
| RBL_RCA | 1,409 / 2,339 | **60.2%** |

**Common signature patterns detected:**
- `"Thanks & Regards"` + agent name + bank name
- `"Best Regards"` + disclaimers
- Legal disclaimer blocks (Unity Bank "Confidential" boilerplate, up to 300–500 chars)
- `"Disclaimer: This e-mail message is legally privileged..."` repeated across 100s of tickets

**Example signature block occupying most of a ticket body:**
```
Thanks & Regards,
Nandini Devendra
Disclaimer: This e-mail message is legally privileged, confidential and intended
for the addressee only. If you are not the intended receiver, please do not disclose,
copy, circulate or in any other way use the information contained in this message.
[Unity Small Finance Bank Limited] | www.theunitybank.com
```

**Impact on RAG**: If signature blocks are not stripped, the vector embedding for a short technical question will be dominated by generic signature content. Two tickets asking different things but with the same signature will have artificially high similarity to each other.

**Remediation**: Regex-based signature stripping using the patterns already identified (`Thanks & Regards`, `Best Regards`, `Disclaimer:`, `This e-mail is classified as INTERNAL`). Must run after HTML stripping.

---

## 4. Auto-Generated and System Messages

A subset of tickets appear to be system-generated notifications rather than real support queries:

| File | Auto-messages detected (sample of 300) | Rate |
|---|---|---|
| CSV (Subject) | 9 / 300 | 3.0% |
| RBL_RCA1 (Description) | 7 / 300 | 2.3% |
| RBL_RCA (Description) | 11 / 300 | 3.7% |

**Examples found in raw data:**
```
"This is a test e-mail message from VKYC Server.
Session ID: 748654f0-8d92-4482-b37c-f447c6122c06
Account Info: productCode = STABLEMONEY..."
```
```
Query Type = "Test Mail" (7 in RBL_RCA1, 1 in RBL_RCA)
Query Type = "Meeting" (37 in RBL_RCA1, 2 in RBL_RCA)
Query Type = "Non support related (internal)" (75 in RBL_RCA1, 5 in RBL_RCA, 194 in CSV)
```

**Filter criteria**: Tickets with `Query Type` in `{"Test Mail", "Meeting", "Non support related (internal)"}` or subject/body matching auto-reply patterns should be **excluded from AI training** — they represent internal operations, not customer issues.

**Estimated exclusion volume**: ~400–600 tickets across all files (~8–10% of total).

---

## 5. The `Summary` Column — False Signal

Across all files where `Summary` exists as a string column:
- **CSV**: All 1,920 values = `"Not Set"` (exactly 10 characters)
- **RBL_RCA1**: All 869 values = `"Not Set"` (exactly 10 characters)
- **RBL_RCA**: Column is `float64` (all NaN — not even exported)

**Root cause**: The `Summary` field in Freshdesk requires agents to manually fill in a custom ticket summary. This was never done. The export template outputs the field default value `"Not Set"`.

**Action**: Remove `Summary` from all ingestion pipelines entirely. Do not embed it.

---

## 6. RCA Coverage and Quality

The `RCA` (Root Cause Analysis) column is the highest-value training signal in the dataset — but it is critically underpopulated.

### Coverage by file:
| File | Non-null RCA | Total Rows | Coverage |
|---|---|---|---|
| RBL_RCA1 | 237 | 869 | **27.3%** |
| RBL_RCA | 343 | 2,339 | **14.7%** |

### Quality distribution:
| Quality Band | RBL_RCA1 | RBL_RCA |
|---|---|---|
| Very short (<30 chars) — often just a label | 128 (54%) | 78 (23%) |
| Short (30–100 chars) — minimal explanation | ~60 (25%) | ~120 (35%) |
| Substantive (100–500 chars) — usable content | ~35 (15%) | ~100 (29%) |
| Detailed (>500 chars) — high value | ~14 (6%) | ~45 (13%) |

### Common trivial RCA values (exclude from training):
```
"manually updated", "ID deactivation", "ID creation", "under observation",
"ID correction in database", "Role changes", "na", "Not found yet",
"Facing Issue mentions", "Meeting", "RCA pending from dev"
```

### Sample substantive RCA entries:
```
"celery container restarted causing the issue" — backend infra pattern
"causing due to the web-socket disconnect" — network/infra pattern
"In the initial findings, we observed that the agents who are facing the issue
 have high CPU and memory utilization on their PCs..." — device-level issue
"this is a bug which is in admin panel backend and UI regarding the database
 query hence raised asana https://app.asana.com/..." — tracked bug
```

**RCA topic distribution** (across both XLSX files):
| Root Cause Category | RBL_RCA1 | RBL_RCA |
|---|---|---|
| Backend/Infrastructure | 34 | 77 |
| Video KYC issues | 14 | 38 |
| API-related | 7 | 27 |
| Config/Parameters | 7 | 9 |
| Network/Connectivity | 14 | 9 |
| Authentication | 1 | 6 |
| User error | 0 | 5 |
| OTP | 2 | 1 |

**Engineering note**: Only RCAs with length ≥ 50 characters and not matching the trivial-value list should be used as training content. This yields approximately **100–200 usable RCA entries** across both files.

---

## 7. Duplicate and Near-Duplicate Analysis

### Within-file duplicates:
| File | Total Rows | Unique Ticket IDs | Duplicate Rows |
|---|---|---|---|
| XLS | 1,993 | 1,993 | **0** |
| CSV | 1,920 | 1,920 | **0** |
| RBL_RCA1 | 869 | 869 | **0** |
| RBL_RCA | 2,339 | 2,339 | **0** |

No within-file ticket ID duplicates. Each file is a clean deduplicated export.

### Cross-file ticket ID overlap:

| Pair | Common IDs | % of A | % of B |
|---|---|---|---|
| XLS ↔ CSV | **1,917** | 96.2% | 99.8% |
| XLS ↔ RBL_RCA1 | 127 | 6.4% | 14.6% |
| CSV ↔ RBL_RCA1 | 126 | 6.6% | 14.5% |
| XLS ↔ RBL_RCA | 0 | 0% | 0% |
| CSV ↔ RBL_RCA | 0 | 0% | 0% |
| RBL_RCA1 ↔ RBL_RCA | 0 | 0% | 0% |

**Critical findings:**
1. **XLS and CSV are near-identical** (96–99% overlap). They are two export runs of essentially the same ticket batch (different timestamps in the filenames). **Only one should be used for training.** CSV is preferred (has reliable encoding; XLS requires custom parser).

2. **RBL_RCA1 and RBL_RCA have zero overlap** — they are entirely separate ticket datasets. Together they represent a distinct, richer corpus of 3,208 tickets with Description + RCA.

3. **RBL files have zero overlap with XLS/CSV** (except the 126–127 RBL_RCA1 entries) — they cover a different time period or client scope.

---

## 8. Timestamp Quality

| File | `Created time` valid | Date range | Notes |
|---|---|---|---|
| CSV | Parsed as strings | Need `pd.to_datetime()` conversion | Likely 2024–2026 range |
| XLS | Stored as strings in XML | Need custom parser | — |
| RBL_RCA1 | datetime64 (all valid) | 2024–2026 | Clean |
| RBL_RCA | datetime64 (all valid) | 2024–2026 | Clean |

---

## 9. Missing Value Map — Key Columns Only

| Column | CSV | RBL_RCA1 | RBL_RCA |
|---|---|---|---|
| Subject | 0.2% | 0.1% | 0.0% |
| Description | **100%** (absent) | 0.5% | 0.0% |
| RCA | **100%** (absent) | **72.7%** | **85.3%** |
| session_ids | **100%** (absent) | **55.5%** | 25% est. |
| Query Type | 10.9% | 8.4% | ~30% est. |
| Issue Area | 10.9% | 8.4% | ~30% est. |
| SOP Status | 10.9% | 28.1% | **~90%** |
| Resolution Classification | **87.3%** | **89.3%** | **96.1%** |
| StackOverflow Link | **36.9%** | **73.5%** | ~70% est. |
| Handling Time | absent | 7.7% | ~10% est. |

---

## 10. Remediation Pipeline Requirements

Before any AI ingestion, the following transformations are **mandatory**:

```
Priority  Action
────────────────────────────────────────────────────────────────
P0        Strip HTML from Description (html.parser / BeautifulSoup)
P0        Remove email signatures (regex-based, post-HTML-strip)
P0        Exclude Summary column (always "Not Set")
P0        Exclude 25+ 100%-null columns from embedding
P0        Deduplicate XLS vs CSV (keep CSV only)
P1        Filter out non-support tickets (Query Type = Test/Meeting/Internal)
P1        Filter RCA entries: length >= 50 chars and not in trivial-value list
P1        Normalize timestamps to UTC ISO8601
P2        Strip auto-reply boilerplate (Disclaimer blocks, legal notices)
P2        Resolve duplicate Tags column in XLS (rename second to contact_tags)
P2        Parse session_ids as list (comma-separated UUIDs)
P3        Impute missing Query Type from Subject (regex-based classification)
```
