import pandas as pd
import os
import warnings
warnings.filterwarnings("ignore")

ROOT = r"C:\Users\HP\Desktop\Think360"

files = {
    "xls_tickets": os.path.join(ROOT, "84000794141_tickets-May-12-2026-09_53 (1).xls"),
    "csv_tickets": os.path.join(ROOT, "84000794166_tickets-May-12-2026-10_29.csv"),
    "rbl_rca1":    os.path.join(ROOT, "RBL_RCA 1.xlsx"),
    "rbl_rca":     os.path.join(ROOT, "RBL_RCA.xlsx"),
}

for key, path in files.items():
    print(f"\n{'='*60}")
    print(f"FILE: {key}")
    print(f"SIZE_BYTES: {os.path.getsize(path)}")
    ext = os.path.splitext(path)[1].lower()

    if ext == ".csv":
        df = pd.read_csv(path, encoding="utf-8", low_memory=False)
        print(f"TYPE: CSV")
        print(f"ROWS: {df.shape[0]}  COLS: {df.shape[1]}")
        print(f"COLUMNS: {list(df.columns)}")
        print(f"DTYPES:\n{df.dtypes.to_string()}")

    elif ext == ".xls":
        # May be XML-based (HTML table exported as .xls from Freshdesk)
        try:
            # Try openpyxl first
            xl = pd.ExcelFile(path, engine="openpyxl")
            engine_used = "openpyxl"
        except Exception:
            try:
                # Try xlrd
                xl = pd.ExcelFile(path, engine="xlrd")
                engine_used = "xlrd"
            except Exception:
                # Try reading as HTML table (Freshdesk exports as XML/HTML disguised as .xls)
                try:
                    tables = pd.read_html(path, encoding="utf-8")
                    print(f"TYPE: XLS (HTML/XML table format)")
                    print(f"TABLES_FOUND: {len(tables)}")
                    for i, df in enumerate(tables):
                        print(f"  Table[{i}]: {df.shape[0]} rows x {df.shape[1]} cols")
                        print(f"  Columns: {list(df.columns)}")
                        print(f"  Dtypes:\n{df.dtypes.to_string()}")
                    continue
                except Exception as e3:
                    print(f"ERROR reading XLS: {e3}")
                    continue

        print(f"TYPE: XLS (engine={engine_used})")
        print(f"SHEETS: {xl.sheet_names}")
        for sh in xl.sheet_names:
            df = xl.parse(sh)
            print(f"  Sheet [{sh}]: {df.shape[0]} rows x {df.shape[1]} cols")
            print(f"  Columns: {list(df.columns)}")
            print(f"  Dtypes:\n{df.dtypes.to_string()}")

    elif ext == ".xlsx":
        xl = pd.ExcelFile(path, engine="openpyxl")
        print(f"TYPE: XLSX")
        print(f"SHEETS: {xl.sheet_names}")
        for sh in xl.sheet_names:
            df = xl.parse(sh)
            print(f"  Sheet [{sh}]: {df.shape[0]} rows x {df.shape[1]} cols")
            print(f"  Columns: {list(df.columns)}")
            print(f"  Dtypes:\n{df.dtypes.to_string()}")
