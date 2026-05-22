"""
Deep dataset profiling for KwikID Freshdesk exports.
READ-ONLY — no data is modified or written to source files.
"""

import pandas as pd
import numpy as np
import os
import re
import json
import warnings
from collections import Counter, defaultdict
from datetime import datetime

warnings.filterwarnings("ignore")

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "backups")

# ── Load all datasets ──────────────────────────────────────────────────────────

print("=== LOADING DATASETS ===\n")

# 1. XLS (HTML-table format from Freshdesk)
xls_path = os.path.join(ROOT, "84000794141_tickets-May-12-2026-09_53 (1).xls")
try:
    xls_tables = pd.read_html(xls_path, encoding="utf-8", flavor="html5lib")
    df_xls = xls_tables[0]
    # First row may be extra header
    if df_xls.iloc[0].astype(str).str.strip().eq(df_xls.columns.astype(str).str.strip()).all():
        df_xls = df_xls.iloc[1:].reset_index(drop=True)
    print(f"XLS loaded: {df_xls.shape[0]} rows x {df_xls.shape[1]} cols")
except Exception as e:
    print(f"XLS load error: {e}")
    df_xls = None

# 2. CSV
csv_path = os.path.join(ROOT, "84000794166_tickets-May-12-2026-10_29.csv")
df_csv = pd.read_csv(csv_path, encoding="utf-8", low_memory=False)
print(f"CSV loaded: {df_csv.shape[0]} rows x {df_csv.shape[1]} cols")

# 3. RBL_RCA 1.xlsx  (Sheet1)
rca1_path = os.path.join(ROOT, "RBL_RCA 1.xlsx")
df_rca1 = pd.read_excel(rca1_path, sheet_name="Sheet1", engine="openpyxl")
print(f"RBL_RCA1 loaded: {df_rca1.shape[0]} rows x {df_rca1.shape[1]} cols")

# 4. RBL_RCA.xlsx (Unity)
rca_path = os.path.join(ROOT, "RBL_RCA.xlsx")
df_rca = pd.read_excel(rca_path, sheet_name="Unity", engine="openpyxl")
print(f"RBL_RCA loaded: {df_rca.shape[0]} rows x {df_rca.shape[1]} cols")

datasets = {
    "XLS_Tickets": df_xls,
    "CSV_Tickets": df_csv,
    "RBL_RCA1": df_rca1,
    "RBL_RCA": df_rca,
}

print("\n")


# ── Utility helpers ────────────────────────────────────────────────────────────

def null_pct(series):
    return round(series.isna().mean() * 100, 1)

def value_counts_top(series, n=15):
    vc = series.dropna().astype(str).str.strip().value_counts()
    return vc.head(n).to_dict()

def detect_html_in_col(series, sample=200):
    sample_vals = series.dropna().astype(str).head(sample)
    html_count = sample_vals.str.contains(r'<[a-zA-Z][^>]*>', regex=True).sum()
    return html_count, len(sample_vals)

def detect_signatures(series, sample=300):
    sig_patterns = [
        r'(?i)thanks\s*[&]\s*regards',
        r'(?i)best\s*regards',
        r'(?i)sincerely',
        r'(?i)warm\s*regards',
        r'(?i)kwikid\s+support',
        r'(?i)think360',
        r'(?i)this is an auto',
        r'(?i)do not reply',
    ]
    combo = '|'.join(sig_patterns)
    sample_vals = series.dropna().astype(str).head(sample)
    sig_count = sample_vals.str.contains(combo, regex=True).sum()
    return sig_count, len(sample_vals)

def detect_auto_messages(series, sample=300):
    auto_patterns = [
        r'(?i)auto.?generated',
        r'(?i)automatic reply',
        r'(?i)out of office',
        r'(?i)ticket.*created.*automatically',
        r'(?i)this is an automated',
        r'(?i)do not reply to this',
    ]
    combo = '|'.join(auto_patterns)
    sample_vals = series.dropna().astype(str).head(sample)
    auto_count = sample_vals.str.contains(combo, regex=True).sum()
    return auto_count, len(sample_vals)

def text_length_stats(series):
    lengths = series.dropna().astype(str).str.len()
    return {
        "min": int(lengths.min()),
        "max": int(lengths.max()),
        "mean": round(float(lengths.mean()), 1),
        "median": round(float(lengths.median()), 1),
        "p25": round(float(lengths.quantile(0.25)), 1),
        "p75": round(float(lengths.quantile(0.75)), 1),
        "p95": round(float(lengths.quantile(0.95)), 1),
    }


# ── SECTION 1: Per-dataset null/missing analysis ───────────────────────────────

print("=" * 70)
print("SECTION 1: NULL / MISSING ANALYSIS PER DATASET")
print("=" * 70)

for name, df in datasets.items():
    if df is None:
        print(f"\n[{name}] NOT LOADED")
        continue
    print(f"\n[{name}]  {df.shape[0]} rows x {df.shape[1]} cols")
    nulls = [(col, null_pct(df[col])) for col in df.columns]
    nulls_sorted = sorted(nulls, key=lambda x: -x[1])
    heavy_nulls = [(c, p) for c, p in nulls_sorted if p > 50]
    partial_nulls = [(c, p) for c, p in nulls_sorted if 10 < p <= 50]
    low_nulls = [(c, p) for c, p in nulls_sorted if 1 < p <= 10]
    print(f"  Columns with >50% null: {len(heavy_nulls)}")
    for c, p in heavy_nulls:
        print(f"    {p:5.1f}%  {c}")
    print(f"  Columns with 10-50% null: {len(partial_nulls)}")
    for c, p in partial_nulls:
        print(f"    {p:5.1f}%  {c}")
    print(f"  Columns with 1-10% null: {len(low_nulls)}")
    for c, p in low_nulls:
        print(f"    {p:5.1f}%  {c}")


# ── SECTION 2: Key categorical distributions ──────────────────────────────────

print("\n" + "=" * 70)
print("SECTION 2: KEY CATEGORICAL DISTRIBUTIONS")
print("=" * 70)

key_cats = ["Status", "Priority", "Type", "Group", "Agent", "Query Type",
            "Issue Area", "Environment", "Resolution Classification",
            "SOP Status", "RCA status", "Issue Recurrence", "Impact",
            "Resolution status", "First response status"]

for name, df in datasets.items():
    if df is None:
        continue
    print(f"\n[{name}]")
    for col in key_cats:
        if col not in df.columns:
            continue
        vc = value_counts_top(df[col], n=20)
        total_non_null = df[col].notna().sum()
        print(f"  {col} (non-null: {total_non_null}):")
        for val, cnt in vc.items():
            pct = round(cnt / max(total_non_null, 1) * 100, 1)
            print(f"      {cnt:5d} ({pct:5.1f}%)  {val}")


# ── SECTION 3: Text quality analysis ─────────────────────────────────────────

print("\n" + "=" * 70)
print("SECTION 3: TEXT QUALITY ANALYSIS")
print("=" * 70)

text_cols_to_check = {
    "XLS_Tickets": ["Subject", "Description", "Summary", "RCA"] if df_xls is not None else [],
    "CSV_Tickets": ["Subject", "Summary"],
    "RBL_RCA1": ["Subject", "Description", "Summary", "RCA"],
    "RBL_RCA": ["Subject", "Description", "RCA"],
}

for name, cols in text_cols_to_check.items():
    df = datasets[name]
    if df is None:
        continue
    print(f"\n[{name}]")
    for col in cols:
        if col not in df.columns:
            continue
        series = df[col]
        non_null = series.notna().sum()
        if non_null == 0:
            print(f"  {col}: ALL NULL")
            continue
        html_n, html_total = detect_html_in_col(series)
        sig_n, sig_total = detect_signatures(series)
        auto_n, auto_total = detect_auto_messages(series)
        stats = text_length_stats(series)
        empty_string = (series.astype(str).str.strip() == "").sum()
        very_short = (series.astype(str).str.len() < 20).sum()
        print(f"  {col}:")
        print(f"    non-null: {non_null}  empty-string: {empty_string}  very-short(<20chars): {very_short}")
        print(f"    HTML detected: {html_n}/{html_total} ({round(html_n/max(html_total,1)*100,1)}%)")
        print(f"    Signatures detected: {sig_n}/{sig_total} ({round(sig_n/max(sig_total,1)*100,1)}%)")
        print(f"    Auto-messages: {auto_n}/{auto_total}")
        print(f"    Length stats: {stats}")


# ── SECTION 4: Ticket ID & Duplication Analysis ──────────────────────────────

print("\n" + "=" * 70)
print("SECTION 4: TICKET ID & DUPLICATION ANALYSIS")
print("=" * 70)

for name, df in datasets.items():
    if df is None:
        continue
    print(f"\n[{name}]")
    if "Ticket ID" in df.columns:
        total = len(df)
        unique_ids = df["Ticket ID"].nunique()
        dup_ids = df["Ticket ID"].duplicated().sum()
        dup_tickets = df[df["Ticket ID"].duplicated(keep=False)]["Ticket ID"].unique()
        print(f"  Total rows: {total}")
        print(f"  Unique Ticket IDs: {unique_ids}")
        print(f"  Duplicate Ticket ID rows: {dup_ids}  (IDs involved: {len(dup_tickets)})")
        if len(dup_tickets) > 0:
            print(f"  Sample duplicate IDs: {list(dup_tickets[:10])}")

    # Cross-file duplicate check
    # We'll do this after collecting all IDs


print("\n  [CROSS-FILE TICKET ID OVERLAP]")
id_sets = {}
for name, df in datasets.items():
    if df is not None and "Ticket ID" in df.columns:
        id_sets[name] = set(df["Ticket ID"].dropna().astype(str))

names = list(id_sets.keys())
for i in range(len(names)):
    for j in range(i+1, len(names)):
        a, b = names[i], names[j]
        overlap = id_sets[a] & id_sets[b]
        print(f"  {a} ∩ {b}: {len(overlap)} common ticket IDs")
        if len(overlap) > 0 and len(overlap) <= 10:
            print(f"    IDs: {sorted(overlap)}")


# ── SECTION 5: Temporal Analysis ─────────────────────────────────────────────

print("\n" + "=" * 70)
print("SECTION 5: TEMPORAL DISTRIBUTION")
print("=" * 70)

for name, df in datasets.items():
    if df is None:
        continue
    print(f"\n[{name}]")
    for tcol in ["Created time", "Resolved time", "Closed time"]:
        if tcol not in df.columns:
            continue
        try:
            ts = pd.to_datetime(df[tcol], errors="coerce")
            valid = ts.dropna()
            if len(valid) == 0:
                print(f"  {tcol}: no valid timestamps")
                continue
            print(f"  {tcol}:  min={valid.min().date()}  max={valid.max().date()}  "
                  f"valid={len(valid)}/{len(ts)}  null={ts.isna().sum()}")
            # Year distribution
            year_dist = valid.dt.year.value_counts().sort_index().to_dict()
            print(f"    Year distribution: {year_dist}")
        except Exception as e:
            print(f"  {tcol}: error — {e}")


# ── SECTION 6: Agent & Group Analysis ────────────────────────────────────────

print("\n" + "=" * 70)
print("SECTION 6: AGENT & GROUP DISTRIBUTION")
print("=" * 70)

for name, df in datasets.items():
    if df is None:
        continue
    print(f"\n[{name}]")
    for col in ["Agent", "Group"]:
        if col in df.columns:
            vc = value_counts_top(df[col], n=15)
            total = df[col].notna().sum()
            print(f"  {col} (non-null: {total}, unique: {df[col].nunique()}):")
            for val, cnt in vc.items():
                print(f"      {cnt:4d}  {val}")


# ── SECTION 7: Interaction & Resolution Quality ───────────────────────────────

print("\n" + "=" * 70)
print("SECTION 7: INTERACTION & RESOLUTION METRICS")
print("=" * 70)

for name, df in datasets.items():
    if df is None:
        continue
    print(f"\n[{name}]")
    for col in ["Agent interactions", "Customer interactions",
                "First response time (in hrs)", "Resolution time (in hrs)"]:
        if col not in df.columns:
            continue
        series = df[col]
        if series.dtype == object:
            # Try numeric conversion
            num = pd.to_numeric(series.astype(str).str.replace(",", ""), errors="coerce")
        else:
            num = pd.to_numeric(series, errors="coerce")
        valid = num.dropna()
        if len(valid) > 0:
            print(f"  {col}:  count={len(valid)}  "
                  f"min={valid.min():.1f}  mean={valid.mean():.1f}  "
                  f"median={valid.median():.1f}  max={valid.max():.1f}  "
                  f"p95={valid.quantile(0.95):.1f}")


# ── SECTION 8: Summary Dataset—Level Key Stats ────────────────────────────────

print("\n" + "=" * 70)
print("SECTION 8: QUICK SUMMARY STATS PER DATASET")
print("=" * 70)

for name, df in datasets.items():
    if df is None:
        continue
    print(f"\n[{name}]  {df.shape}")
    cols_with_data = [c for c in df.columns if df[c].notna().sum() > 0]
    cols_all_null = [c for c in df.columns if df[c].isna().all()]
    print(f"  Columns with ANY data: {len(cols_with_data)}")
    print(f"  Columns that are ALL null: {len(cols_all_null)} → {cols_all_null}")
    has_desc = "Description" in df.columns
    has_summary = "Summary" in df.columns
    has_rca = "RCA" in df.columns
    has_sessions = "session_ids" in df.columns
    print(f"  Has Description col: {has_desc}  |  Summary: {has_summary}  |  RCA: {has_rca}  |  session_ids: {has_sessions}")


# ── SECTION 9: Sample rows preview ────────────────────────────────────────────

print("\n" + "=" * 70)
print("SECTION 9: SAMPLE ROW PREVIEWS (Subject + Query Type + RCA)")
print("=" * 70)

for name, df in datasets.items():
    if df is None:
        continue
    print(f"\n[{name}] — 10 sample rows:")
    preview_cols = [c for c in ["Ticket ID", "Subject", "Query Type", "Issue Area",
                                 "Status", "Priority", "RCA", "Summary"]
                    if c in df.columns]
    sample = df[preview_cols].dropna(subset=["Subject"]).sample(
        min(10, len(df)), random_state=42
    )
    for _, row in sample.iterrows():
        print(f"  ---")
        for col in preview_cols:
            val = str(row.get(col, ""))[:120]
            print(f"    {col}: {val}")


# ── SECTION 10: XLS-specific: check for Description column ──────────────────

print("\n" + "=" * 70)
print("SECTION 10: XLS FILE — RAW COLUMN NAMES & FIRST ROW")
print("=" * 70)
if df_xls is not None:
    print(f"Columns ({df_xls.shape[1]}):")
    for i, c in enumerate(df_xls.columns):
        print(f"  [{i:02d}] {repr(c)}")
    print("\nFirst 3 rows (transposed):")
    print(df_xls.head(3).T.to_string())
else:
    print("XLS NOT LOADED")


print("\n\n=== PROFILING COMPLETE ===")
