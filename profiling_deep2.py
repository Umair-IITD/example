"""
Deep dataset profiling — Part 2: Cross-file analysis, category deep-dives, automation feasibility
READ-ONLY — no data is modified.
"""

import pandas as pd
import numpy as np
import os
import re
import warnings
from collections import Counter
warnings.filterwarnings("ignore")

ROOT = r"C:\Users\HP\Desktop\Think360"

# ── Reload datasets ────────────────────────────────────────────────────────────
df_csv  = pd.read_csv(os.path.join(ROOT, "84000794166_tickets-May-12-2026-10_29.csv"), encoding="utf-8", low_memory=False)
df_rca1 = pd.read_excel(os.path.join(ROOT, "RBL_RCA 1.xlsx"), sheet_name="Sheet1", engine="openpyxl")
df_rca  = pd.read_excel(os.path.join(ROOT, "RBL_RCA.xlsx"),   sheet_name="Unity",  engine="openpyxl")

# ── XLS: try reading as raw XML ────────────────────────────────────────────────
xls_path = os.path.join(ROOT, "84000794141_tickets-May-12-2026-09_53 (1).xls")
with open(xls_path, "rb") as f:
    raw_bytes = f.read(200)
print("XLS raw header (first 200 bytes):")
print(repr(raw_bytes))
print()

# Attempt to parse as XML SpreadsheetML (Office 2003 XML)
try:
    import xml.etree.ElementTree as ET
    tree = ET.parse(xls_path)
    root = tree.getroot()
    print(f"XLS parsed as XML. Root tag: {root.tag}")
    print(f"  Children: {[c.tag for c in root][:10]}")
except Exception as e:
    print(f"XLS not valid strict XML: {e}")

# Attempt with lxml
try:
    from lxml import etree
    with open(xls_path, "rb") as f:
        tree = etree.parse(f)
    root = tree.getroot()
    print(f"XLS (lxml): root tag = {root.tag}")
    # Look for worksheet/row/cell structure
    ns = {"ss": "urn:schemas-microsoft-com:office:spreadsheet"}
    rows = root.findall(".//ss:Row", ns)
    print(f"  Found {len(rows)} rows via SpreadsheetML namespace")
    if rows:
        # Extract header row
        first_cells = rows[0].findall(".//ss:Cell/ss:Data", ns)
        headers = [c.text for c in first_cells]
        print(f"  Header row ({len(headers)} cols): {headers[:20]}")
        print(f"  Full header: {headers}")
        # Count data rows
        data_rows = rows[1:]
        print(f"  Data rows: {len(data_rows)}")

        # Parse to DataFrame
        records = []
        for row in data_rows:
            cells = row.findall(".//ss:Cell/ss:Data", ns)
            records.append([c.text for c in cells])

        df_xls = pd.DataFrame(records, columns=headers[:len(records[0])] if records else headers)
        print(f"  DataFrame shape: {df_xls.shape}")
        print(f"  All columns: {list(df_xls.columns)}")
    else:
        df_xls = None
except Exception as e:
    print(f"XLS lxml parse error: {e}")
    df_xls = None

print("\n")

datasets = {
    "XLS_Tickets": df_xls,
    "CSV_Tickets": df_csv,
    "RBL_RCA1": df_rca1,
    "RBL_RCA": df_rca,
}


# ── Cross-file Ticket ID overlap ───────────────────────────────────────────────

print("=" * 70)
print("CROSS-FILE TICKET ID OVERLAP")
print("=" * 70)

id_sets = {}
for name, df in datasets.items():
    if df is not None and "Ticket ID" in df.columns:
        id_sets[name] = set(df["Ticket ID"].dropna().astype(str))
        print(f"  {name}: {len(id_sets[name])} unique ticket IDs")

names = list(id_sets.keys())
for i in range(len(names)):
    for j in range(i+1, len(names)):
        a, b = names[i], names[j]
        overlap = id_sets[a] & id_sets[b]
        pct_a = round(len(overlap)/max(len(id_sets[a]),1)*100, 1)
        pct_b = round(len(overlap)/max(len(id_sets[b]),1)*100, 1)
        print(f"\n  [{a}] intersect [{b}]: {len(overlap)} common IDs ({pct_a}% of A, {pct_b}% of B)")
        if 0 < len(overlap) <= 20:
            print(f"    IDs: {sorted(overlap)}")
        elif len(overlap) > 20:
            print(f"    Sample IDs: {sorted(list(overlap))[:10]}")


# ── Issue Area & Query Type deep dive ─────────────────────────────────────────

print("\n" + "=" * 70)
print("DEEP DIVE: QUERY TYPE x ISSUE AREA x STATUS cross-tab")
print("=" * 70)

for name, df in [("CSV_Tickets", df_csv), ("RBL_RCA1", df_rca1), ("RBL_RCA", df_rca)]:
    print(f"\n[{name}]")
    if "Query Type" in df.columns and "Issue Area" in df.columns:
        # Query Type x Issue Area crosstab
        sub = df[["Query Type", "Issue Area"]].dropna()
        print(f"  Query Type distribution:")
        for val, cnt in sub["Query Type"].value_counts().head(20).items():
            print(f"      {cnt:5d}  {val}")
        print(f"\n  Issue Area distribution:")
        for val, cnt in sub["Issue Area"].value_counts().head(25).items():
            print(f"      {cnt:5d}  {val}")

    if "Status" in df.columns and "Query Type" in df.columns:
        print(f"\n  Query Type x Status:")
        try:
            ct = pd.crosstab(df["Query Type"], df["Status"])
            print(ct.to_string())
        except Exception:
            pass


# ── RCA analysis ──────────────────────────────────────────────────────────────

print("\n" + "=" * 70)
print("RCA ANALYSIS — content quality and categorization")
print("=" * 70)

for name, df in [("RBL_RCA1", df_rca1), ("RBL_RCA", df_rca)]:
    print(f"\n[{name}]")
    if "RCA" not in df.columns:
        print("  No RCA column")
        continue
    rca = df["RCA"].dropna()
    print(f"  Non-null RCA: {len(rca)} / {len(df)} ({round(len(rca)/len(df)*100,1)}%)")
    short = rca[rca.astype(str).str.len() < 30]
    print(f"  Very short (<30 chars): {len(short)}")
    print(f"  Sample short RCAs: {list(short.head(10).values)}")

    # Common RCA phrases
    rca_lower = rca.astype(str).str.lower()
    patterns = {
        "backend/infra": r"backend|server|database|infra|deploy",
        "user_error": r"user error|user mistake|incorrect input|wrong|misuse",
        "known_issue": r"known issue|known bug|existing issue",
        "config": r"config|configuration|setting|parameter",
        "network": r"network|connectivity|timeout|latency",
        "otp": r"otp|one.?time",
        "kyc/video": r"kyc|video kyc|vkyc|video call",
        "api": r"\bapi\b|endpoint|integration",
        "auth": r"auth|authentication|login|token",
    }
    print(f"\n  RCA topic patterns:")
    for label, pat in patterns.items():
        cnt = rca_lower.str.contains(pat, regex=True, na=False).sum()
        print(f"      {cnt:4d}  {label}")

    # Print 10 sample RCAs
    print(f"\n  Sample RCA entries (first 10 non-empty, >30 chars):")
    long_rcas = rca[rca.astype(str).str.len() >= 30].head(10)
    for i, (idx, val) in enumerate(long_rcas.items()):
        print(f"    [{i}] {str(val)[:200]}")


# ── Description quality deep dive ────────────────────────────────────────────

print("\n" + "=" * 70)
print("DESCRIPTION FIELD DEEP DIVE")
print("=" * 70)

for name, df in [("RBL_RCA1", df_rca1), ("RBL_RCA", df_rca)]:
    print(f"\n[{name}]")
    if "Description" not in df.columns:
        continue
    desc = df["Description"].dropna().astype(str)

    # HTML content
    html_rows = desc.str.contains(r"<[a-zA-Z][^>]*>", regex=True)
    print(f"  Contains HTML tags: {html_rows.sum()} / {len(desc)} ({round(html_rows.sum()/len(desc)*100,1)}%)")

    # Email patterns in description
    email_patterns = {
        "Signatures": r"(?i)thanks\s*[&]\s*regards|best regards|sincerely",
        "From/To headers": r"(?i)^from:|^to:|^subject:",
        "Auto-reply markers": r"(?i)auto.?generated|do not reply|automatic reply",
        "Internal notes": r"(?i)\[note\]|\[internal\]|private note",
        "HTML images": r"<img\s",
        "External URLs": r"https?://[^\s<>\"]+",
    }
    for label, pat in email_patterns.items():
        cnt = desc.str.contains(pat, regex=True, na=False).sum()
        print(f"  {label}: {cnt} ({round(cnt/len(desc)*100,1)}%)")

    # Sample descriptions
    print(f"\n  Sample description (first 500 chars each), first 3 rows:")
    for i, (idx, val) in enumerate(desc.head(3).items()):
        print(f"  --- Row {idx} ---")
        print(f"  {val[:500]}")
        print()


# ── SOP Status analysis ───────────────────────────────────────────────────────

print("=" * 70)
print("SOP STATUS & RESOLUTION CLASSIFICATION")
print("=" * 70)

for name, df in [("CSV_Tickets", df_csv), ("RBL_RCA1", df_rca1), ("RBL_RCA", df_rca)]:
    print(f"\n[{name}]")
    for col in ["SOP Status", "Resolution Classification", "RCA status"]:
        if col not in df.columns:
            continue
        vc = df[col].dropna().value_counts()
        print(f"  {col}:")
        for val, cnt in vc.items():
            pct = round(cnt/len(df)*100, 1)
            print(f"      {cnt:5d} ({pct}%)  {val}")


# ── Automation Feasibility Classification ────────────────────────────────────

print("\n" + "=" * 70)
print("AUTOMATION FEASIBILITY SIGNAL ANALYSIS")
print("=" * 70)

# Use the largest dataset (RBL_RCA) for this analysis
df = df_rca

print(f"\nBased on RBL_RCA ({len(df)} tickets)")

# Criteria:
# AUTO_RESOLVABLE: Status=resolved/closed, SOP Status exists, short resolution time,
#                  low agent interactions, recurring issue, high impact field known
# HUMAN_REVIEW: medium complexity, some SOP
# ESCALATION: high priority, SLA violated, complex RCA

status_closed = df["Status"].isin(["Resolved", "Closed"]) if "Status" in df.columns else pd.Series([False]*len(df))
sla_ok = df["Resolution status"].eq("Within SLA") if "Resolution status" in df.columns else pd.Series([True]*len(df))
sla_violated = df["Resolution status"].eq("SLA Violated") if "Resolution status" in df.columns else pd.Series([False]*len(df))
priority_high = df["Priority"].isin(["High", "Urgent"]) if "Priority" in df.columns else pd.Series([False]*len(df))
recurring = df["Issue Recurrence"].astype(str).str.contains("Recurring", na=False) if "Issue Recurrence" in df.columns else pd.Series([False]*len(df))
has_rca = df["RCA"].notna() if "RCA" in df.columns else pd.Series([False]*len(df))
has_sop = df["SOP Status"].astype(str).str.lower().isin(["done", "completed", "yes"]) if "SOP Status" in df.columns else pd.Series([False]*len(df))

low_agent = pd.Series([False]*len(df))
if "Agent interactions" in df.columns:
    ai = pd.to_numeric(df["Agent interactions"], errors="coerce").fillna(99)
    low_agent = ai <= 3

print(f"\n  Signal stats:")
print(f"    Status closed/resolved: {status_closed.sum()} ({round(status_closed.mean()*100,1)}%)")
print(f"    SLA Within: {sla_ok.sum()} ({round(sla_ok.mean()*100,1)}%)")
print(f"    SLA Violated: {sla_violated.sum()} ({round(sla_violated.mean()*100,1)}%)")
print(f"    Priority High/Urgent: {priority_high.sum()} ({round(priority_high.mean()*100,1)}%)")
print(f"    Recurring issues: {recurring.sum()} ({round(recurring.mean()*100,1)}%)")
print(f"    Has RCA: {has_rca.sum()} ({round(has_rca.mean()*100,1)}%)")
print(f"    Has SOP: {has_sop.sum()} ({round(has_sop.mean()*100,1)}%)")
print(f"    Low agent interactions (<=3): {low_agent.sum()} ({round(low_agent.mean()*100,1)}%)")

# Auto-resolvable: recurring + closed + SLA OK + low interactions
auto_mask = recurring & status_closed & sla_ok & low_agent
escalation_mask = priority_high | sla_violated
human_mask = ~auto_mask & ~escalation_mask

print(f"\n  Automation feasibility estimate:")
print(f"    AUTO_RESOLVABLE (recurring+closed+SLA_OK+low_interactions): {auto_mask.sum()} ({round(auto_mask.mean()*100,1)}%)")
print(f"    ESCALATION_REQUIRED (high/urgent priority OR SLA violated): {escalation_mask.sum()} ({round(escalation_mask.mean()*100,1)}%)")
print(f"    HUMAN_REVIEW_REQUIRED (everything else): {human_mask.sum()} ({round(human_mask.mean()*100,1)}%)")

# More nuanced: check issue areas
print(f"\n  Issue Area breakdown for AUTO_RESOLVABLE tickets:")
if "Issue Area" in df.columns:
    auto_areas = df[auto_mask]["Issue Area"].value_counts().head(15)
    for val, cnt in auto_areas.items():
        print(f"      {cnt:4d}  {val}")

print(f"\n  Issue Area breakdown for ESCALATION tickets:")
if "Issue Area" in df.columns:
    esc_areas = df[escalation_mask]["Issue Area"].value_counts().head(15)
    for val, cnt in esc_areas.items():
        print(f"      {cnt:4d}  {val}")


# ── Subject line patterns (query topic mining) ────────────────────────────────

print("\n" + "=" * 70)
print("SUBJECT LINE PATTERN MINING")
print("=" * 70)

all_subjects = pd.concat([
    df_csv["Subject"].dropna() if "Subject" in df_csv.columns else pd.Series(dtype=str),
    df_rca1["Subject"].dropna() if "Subject" in df_rca1.columns else pd.Series(dtype=str),
    df_rca["Subject"].dropna() if "Subject" in df_rca.columns else pd.Series(dtype=str),
]).astype(str)

print(f"Total subjects (across all files): {len(all_subjects)}")

# Top n-gram patterns
import re
patterns_to_check = {
    "OTP": r"(?i)\botp\b",
    "Video KYC / VKYC": r"(?i)video\s*kyc|vkyc|video\s*call",
    "Authentication/Login": r"(?i)\bauth\b|login|sign.?in|session",
    "PAN": r"(?i)\bpan\b",
    "Aadhaar": r"(?i)aadhaar|aadhar",
    "API": r"(?i)\bapi\b|integration|endpoint",
    "Network/Connectivity": r"(?i)network|connect|timeout|latency",
    "Camera/Video not working": r"(?i)camera|video.?not|screen.?not|not.?visible",
    "Error/Failed": r"(?i)\berror\b|fail(ed|ure)?|crash",
    "Not working/Issue": r"(?i)not.?work|not.?load|not.?display|issue|problem|unable",
    "Download/Upload": r"(?i)download|upload",
    "Report/Export": r"(?i)report|export",
    "RBL Bank": r"(?i)\brbl\b",
    "Bajaj": r"(?i)bajaj",
    "ICICI": r"(?i)icici",
    "SBI": r"(?i)\bsbi\b",
    "Slow/Performance": r"(?i)slow|performance|lag",
    "Document": r"(?i)document|doc\b|kyc.?doc",
    "Face match": r"(?i)face.?match|liveness|liveliness",
    "Customer onboarding": r"(?i)onboard|new.?customer",
}

print(f"\n  Topic keyword hit rates (across all subjects):")
for label, pat in patterns_to_check.items():
    cnt = all_subjects.str.contains(pat, regex=True, na=False).sum()
    pct = round(cnt / len(all_subjects) * 100, 1)
    print(f"      {cnt:5d} ({pct:5.1f}%)  {label}")


# ── Handling Time analysis ────────────────────────────────────────────────────

print("\n" + "=" * 70)
print("HANDLING TIME ANALYSIS (RBL_RCA1)")
print("=" * 70)

if "Handling Time" in df_rca1.columns:
    ht = df_rca1["Handling Time"].dropna().astype(str)
    print(f"  Non-null Handling Time: {len(ht)}")
    print(f"  Sample values: {list(ht.head(20).values)}")
    # Try to parse as timedelta
    vc = ht.value_counts().head(20)
    print(f"\n  Value distribution (top 20):")
    for val, cnt in vc.items():
        print(f"      {cnt:4d}  {val}")


# ── Tags analysis ─────────────────────────────────────────────────────────────

print("\n" + "=" * 70)
print("TAGS ANALYSIS")
print("=" * 70)

for name, df in [("CSV_Tickets", df_csv), ("RBL_RCA1", df_rca1), ("RBL_RCA", df_rca)]:
    print(f"\n[{name}]")
    tag_col = "Tags" if "Tags" in df.columns else None
    if tag_col is None:
        continue
    all_tags = []
    for val in df[tag_col].dropna().astype(str):
        # Tags may be comma-separated
        parts = [t.strip() for t in re.split(r"[,;|]", val) if t.strip()]
        all_tags.extend(parts)
    tc = Counter(all_tags)
    print(f"  Total tag occurrences: {len(all_tags)}  Unique tags: {len(tc)}")
    print(f"  Top 25 tags:")
    for tag, cnt in tc.most_common(25):
        print(f"      {cnt:5d}  {tag}")


print("\n\n=== PROFILING COMPLETE (Part 2) ===")
