# Dataset Schema Analysis
## KwikID Freshdesk Export — Production Dataset Profiling

> **Phase**: READ-ONLY engineering discovery
> **Analysis Date**: 2026-05-12
> **Analyst role**: Senior AI Data Engineer

---

## 1. File Inventory

| File | Format | Size | Rows | Columns | Sheet |
|---|---|---|---|---|---|
| `84000794141_tickets-May-12-2026-09_53 (1).xls` | SpreadsheetML XML (disguised as .xls) | 12.5 MB | 1,993 | 84 | Single sheet |
| `84000794166_tickets-May-12-2026-10_29.csv` | UTF-8 CSV | 1.2 MB | 1,920 | 83 | N/A |
| `RBL_RCA 1.xlsx` | Excel OOXML | 1.1 MB | 869 | 86 | `Sheet1` |
| `RBL_RCA.xlsx` | Excel OOXML | 2.6 MB | 2,339 | 87 | `Unity` |

**Total ticket records across all files**: 7,121 (before deduplication)
**Unique ticket IDs estimated**: ~5,194 (after cross-file dedup analysis — see Section 4)

---

## 2. File Format Notes

### XLS File — Critical Discovery
The `84000794141_tickets-May-12-2026-09_53 (1).xls` file is **NOT a binary `.xls` (BIFF8) file**. It is a **SpreadsheetML XML file** (Microsoft Office 2003 XML format) with a `.xls` extension — a common Freshdesk export format. This means:
- `xlrd` library fails to parse it (expects BIFF binary)
- `html5lib` parser also fails
- Correct approach: `lxml` with namespace `urn:schemas-microsoft-com:office:spreadsheet`
- **No `Description` field** is present in this file (only Subject line metadata)

### CSV File
Standard UTF-8 CSV. No encoding issues detected. Missing `Description` and `RCA` columns. The `Summary` column contains only the placeholder value `"Not Set"` (exactly 10 chars) for all 1,920 rows — effectively useless.

### XLSX Files
Both XLSX files are valid OOXML and parse cleanly with openpyxl. Both contain the critical `Description`, `RCA`, `session_ids`, `Handling Time`, and `Asana Ticket Link` columns that are absent from the XLS/CSV exports.

---

## 3. Column Schema by File

### 3.1 Shared Core Columns (present in all 4 files)

| Column | Inferred Type | Description |
|---|---|---|
| `Ticket ID` | int64 | Primary key — Freshdesk ticket number |
| `Subject` | str | Customer-facing ticket subject line |
| `Status` | str (categorical) | Open / Pending / Resolved / Closed |
| `Priority` | str (categorical) | Low / Medium / High / Urgent |
| `Source` | str (categorical) | Email / Portal / Phone / Chat |
| `Type` | str (categorical) | Question / Problem / Incident / Feature Request |
| `Agent` | str | Assigned support agent name |
| `Group` | str | Support group/team name |
| `Created time` | datetime | Ticket creation timestamp |
| `Due by Time` | datetime | SLA deadline |
| `Resolved time` | datetime | Resolution timestamp |
| `Closed time` | datetime | Ticket close timestamp |
| `Last update time` | datetime | Most recent activity |
| `Initial response time` | datetime (partial) | First agent response timestamp |
| `Time tracked` | str | Formatted time string (HH:MM:SS) |
| `First response time (in hrs)` | str (numeric) | Hours to first response |
| `Resolution time (in hrs)` | str (numeric) | Total hours to resolution |
| `Agent interactions` | int64 | Count of agent replies |
| `Customer interactions` | int64 | Count of customer replies |
| `Resolution status` | str (categorical) | Within SLA / SLA Violated |
| `First response status` | str (categorical) | Within SLA / SLA Violated |
| `Tags` | str (multi-value) | Comma-separated tag list |
| `Clients` | str | Client name label |
| `BajajFin Azure Ticket Id` | float64 (sparse) | External Bajaj Finance ticket ID |
| `Summary` | str (placeholder) | ALWAYS "Not Set" — useless field |
| `StackOverflow Link` | str (sparse) | Link to internal SO Teams post |
| `Source Info` | float64 (all null) | Entirely empty — drop |
| `SOP Status` | str (categorical) | SOP Present / No SOP Required / No SOP Available / SOP Created |
| `Impact` | str (categorical) | Less Than 10% / More Than 10% / Others / Medium / High / DOWNTIME |
| `Issue Recurrence` | str (categorical) | Recurring issue / New/One-time issue |
| `RCA status` | str (categorical) | No RCA Needed / RCA Pending from dev / RCA Shared |
| `Query Type` | str (categorical) | Operational classification — 50+ distinct values |
| `Environment` | str (categorical) | Production / UAT / Staging |
| `Issue Area` | str (categorical) | Backend / Frontend / API / Database / DevOps / Network |
| `Resolution Classification` | str (sparse) | Solved by SOP / Permanent Fix / Temporary Workaround |
| Contact/Phone/Appointment/Signature cols | float64 (all null) | CRM template fields — all empty, drop |
| `BA Line Item Type` | str (sparse) | Business Analyst classification — largely empty |

### 3.2 Additional Columns in XLSX Files Only (RBL_RCA 1 + RBL_RCA)

| Column | Present In | Inferred Type | Importance |
|---|---|---|---|
| `Description` | XLSX only | str (HTML) | **CRITICAL** — Full ticket body text |
| `RCA` | XLSX only | str | Root Cause Analysis text — key training signal |
| `session_ids` | XLSX only | str | Comma-separated KwikID session UUIDs linked to ticket |
| `Handling Time` | XLSX only | str (numeric mins) | Agent time spent in minutes |
| `Asana Ticket Link` | XLSX only | str | Escalation link to Asana task |
| `QA Review Results` | RBL_RCA1 | str (sparse) | QA feedback text |
| `Challenges encountered...` | RBL_RCA1 | str (very sparse) | Weekly challenge log — 99.5% null |
| `URL` | RBL_RCA only | str | Supplementary URL field |
| `Tags_1` / `Tags.1` | XLSX / CSV | str (sparse) | Secondary/duplicate tag column |
| `CR status` | XLSX only | str | Change Request status |

### 3.3 Entirely Useless Columns (100% null across all files)

The following columns appear in the Freshdesk export schema template but contain **zero data** across all files. These should be excluded from any pipeline:

```
BajajFin Azure Ticket Id, Source Info, QA Status, QA Reviewer, QA Review Date,
QA Score, Contact, Phone number, Service location, Appointment start time,
Appointment end time, Customer's signature, Current Stage of CR,
Agreed upon per man day cost (for this client), Efforts in man days,
OneDrive link for BRD, Date of the Initial Client's mail,
OneDrive link for Upload Mail Screenshot, Twitter ID, Unique External ID,
Facebook ID, Onboarding date, Health score, Renewal date,
State (CSV only), Discontinued date (CSV only)
```

**Count of effectively empty columns**: 25–28 per file (30–35% of schema width is dead weight)

---

## 4. Cross-File Schema Comparison

### Column Presence Matrix

| Column | XLS (84 cols) | CSV (83 cols) | RBL_RCA1 (86 cols) | RBL_RCA (87 cols) |
|---|:---:|:---:|:---:|:---:|
| Ticket ID | ✅ | ✅ | ✅ | ✅ |
| Subject | ✅ | ✅ | ✅ | ✅ |
| Description | ❌ | ❌ | ✅ | ✅ |
| RCA | ❌ | ❌ | ✅ | ✅ |
| session_ids | ❌ | ❌ | ✅ | ✅ |
| Handling Time | ❌ | ❌ | ✅ | ✅ |
| Asana Ticket Link | ❌ | ❌ | ✅ | ✅ |
| QA Review Results | ❌ | ❌ | ✅ | ❌ |
| URL | ❌ | ❌ | ❌ | ✅ |
| Review Ticket | ✅ | ✅ | ✅ | ❌ |
| Sub Industry | ✅ | ✅ | ❌ | ❌ |
| Tags (duplicated) | ⚠️ × 2 | ⚠️ Tags.1 | ⚠️ Tags_1 | ⚠️ Tags_1 |

> ⚠️ **The XLS file has a duplicate `Tags` column** (columns index 21 and 67 are both named `Tags`). This is a SpreadsheetML export bug from Freshdesk. The second occurrence contains contact-level tags, not ticket tags.

### Column Naming Inconsistencies

| Semantic Field | XLS/CSV Name | XLSX Name |
|---|---|---|
| Duplicate tag field | `Tags.1` (CSV) | `Tags_1` (XLSX) |
| RCA text | absent | `RCA` |
| Session references | absent | `session_ids` |

---

## 5. Data Type Analysis

### Timestamp Fields
- In XLSX files: correctly parsed as `datetime64[us]` by openpyxl
- In CSV: parsed as raw strings — require `pd.to_datetime()` conversion at pipeline stage
- In XLS: stored as string in SpreadsheetML format — require parsing

### Mixed-Type Columns (type inconsistency across files)
| Column | CSV type | XLSX-RCA1 type | XLSX-RCA type |
|---|---|---|---|
| `Resolved Date By Developer` | float64 (null) | datetime64 | str |
| `CR status` | float64 (null) | float64 (null) | str |
| `QA Review Date` | float64 (null) | float64 (null) | str |
| `Summary` | str ("Not Set") | str ("Not Set") | float64 (null — fully absent) |
| `Review Ticket` | str (sparse) | float64 (null) | float64 (null) |

These type inconsistencies mean a unified schema requires explicit type casting and null coercion at ingestion time.

---

## 6. The `Summary` Column — False Signal Warning

In all files where `Summary` is a str column (CSV, XLS, RBL_RCA1), **every single row has the value "Not Set"** (exactly 10 characters). This is a Freshdesk export default. The `Summary` column carries **zero information value** and must be excluded from any AI training pipeline.

---

## 7. Effective Schema for AI Pipeline

After removing dead columns and consolidating across files, the production-useful column set is:

**Identity & Routing**
```
Ticket ID, Created time, Resolved time, Closed time, Status, Priority,
Agent, Group, Source, Type, Clients
```

**Knowledge Content (primary AI training signals)**
```
Subject            — query intent signal (all files)
Description        — full ticket body (XLSX only — HTML, needs stripping)
RCA                — root cause (XLSX only — sparse but high value)
StackOverflow Link — SOP reference (all files — sparse)
Summary            — EXCLUDE (all values are "Not Set")
```

**Operational Classification**
```
Query Type, Issue Area, Environment, SOP Status,
Resolution Classification, RCA status, Issue Recurrence, Impact,
session_ids, Handling Time
```

**Metrics & SLA**
```
Agent interactions, Customer interactions,
First response time (in hrs), Resolution time (in hrs),
Resolution status, First response status, Tags
```

**Escalation Signals**
```
Asana Ticket Link, BajajFin Azure Ticket Id (sparse)
```

---

## 8. Engineering Recommendations for Schema Normalization

1. **Unify the four files into a single schema** with an 18-column production subset, dropping 65+ dead-weight columns.
2. **Mark Description as primary text field** and strip HTML before embedding — 57–84% of rows contain raw HTML markup.
3. **Treat XLS and CSV as near-duplicates** (96-99% ticket ID overlap) — ingest only one, use the other as validation cross-check.
4. **The RBL_RCA files are a separate, non-overlapping corpus** — treat as a distinct batch with richer schema (Description + RCA available).
5. **The `Tags` duplication in XLS** must be handled at parse time by renaming the second occurrence to `contact_tags`.
6. **Timestamp normalization**: enforce ISO8601 UTC normalization across all 4 datetime fields before pipeline ingestion.
7. **Drop all 25+ columns with >99% nullity** — they add no signal and bloat chunked content unnecessarily.
