"""
Supabase schema pre-migration inspection.
Run from service root: python tests/check_supabase_schema.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

print("=" * 64)
print("SUPABASE SCHEMA INSPECTION")
print("=" * 64)

# ── 1. Check B3 tables ───────────────────────────────────────────
b3_tables = [
    "rag_knowledge_articles",
    "rag_knowledge_chunks",
    "rag_review_queue",
]
print("\n[1] B3 Knowledge Tables:")
for t in b3_tables:
    r = sb.table(t).select("*").limit(0).execute()
    status = "EXISTS" if r is not None else "MISSING"
    try:
        # Count rows
        cnt = sb.table(t).select("*", count="exact").limit(0).execute()
        row_count = cnt.count if hasattr(cnt, "count") else "?"
    except Exception as e:
        row_count = f"err({e})"
    print(f"  {status:<8} {t}  (rows: {row_count})")

# ── 2. Check legacy SOP tables ───────────────────────────────────
sop_tables = ["rag_sop_library", "rag_sop_chunks"]
print("\n[2] Legacy SOP Tables:")
for t in sop_tables:
    try:
        r = sb.table(t).select("*", count="exact").limit(0).execute()
        row_count = r.count if hasattr(r, "count") else len(r.data)
        print(f"  EXISTS    {t}  (rows: {row_count})")
    except Exception as e:
        print(f"  ERROR     {t}  ({e})")

# ── 3. Check fumadocs SOP count ──────────────────────────────────
print("\n[3] Legacy fumadocs SOPs:")
try:
    r = sb.table("rag_sop_library").select("*", count="exact").eq("source", "fumadocs").limit(0).execute()
    print(f"  fumadocs active: {r.count}")
except Exception as e:
    print(f"  Error: {e}")

try:
    r = sb.table("rag_sop_library").select("*", count="exact").eq("source", "fumadocs").eq("is_active", True).limit(0).execute()
    print(f"  fumadocs active+enabled: {r.count}")
except Exception as e:
    print(f"  Error: {e}")

# ── 4. Check B1 ticket/SOP chunk tables ──────────────────────────
b1_tables = ["rag_ticket_chunks", "cases", "audit_events"]
print("\n[4] B1 Core Tables:")
for t in b1_tables:
    try:
        r = sb.table(t).select("*", count="exact").limit(0).execute()
        row_count = r.count if hasattr(r, "count") else "?"
        print(f"  EXISTS    {t}  (rows: {row_count})")
    except Exception as e:
        print(f"  ERROR     {t}  ({e})")

# ── 5. Check RPCs ────────────────────────────────────────────────
print("\n[5] RPC Functions:")
for rpc_name in ["match_all_b1_sources", "search_b1_sources_fts"]:
    try:
        # Call with minimal args to see if it exists (will error but differently if missing)
        import json
        r = sb.rpc(rpc_name, {
            "query_embedding": [0.0] * 1536,
            "filter_client": "test",
            "top_k": 1,
            "similarity_threshold": 0.99,
            "index_version": "v2"
        }).execute()
        print(f"  EXISTS    {rpc_name}  (returned {len(r.data)} rows)")
    except Exception as e:
        err_str = str(e)
        if "does not exist" in err_str.lower() or "function" in err_str.lower():
            print(f"  MISSING   {rpc_name}  ({err_str[:80]})")
        else:
            print(f"  EXISTS?   {rpc_name}  (error: {err_str[:80]})")

# ── 6. Check rag_knowledge_chunks columns for fts ────────────────
print("\n[6] rag_knowledge_chunks.fts column:")
try:
    # Try selecting fts column
    r = sb.table("rag_knowledge_chunks").select("fts").limit(1).execute()
    print(f"  fts column: EXISTS")
except Exception as e:
    if "fts" in str(e).lower():
        print(f"  fts column: MISSING ({e})")
    else:
        print(f"  fts column: UNKNOWN ({e})")

# ── 7. Check image_metadata column on articles ───────────────────
print("\n[7] rag_knowledge_articles.image_metadata column:")
try:
    r = sb.table("rag_knowledge_articles").select("image_metadata").limit(1).execute()
    print(f"  image_metadata column: EXISTS")
except Exception as e:
    if "image_metadata" in str(e).lower():
        print(f"  image_metadata column: MISSING ({e})")
    else:
        print(f"  image_metadata column: UNKNOWN ({e})")

# ── 8. Backup tables ─────────────────────────────────────────────
print("\n[8] Backup Tables:")
for t in ["rag_sop_library_backup_20260701", "rag_sop_library_backup_20260629"]:
    try:
        r = sb.table(t).select("*", count="exact").limit(0).execute()
        print(f"  EXISTS    {t}  (rows: {r.count})")
    except Exception as e:
        print(f"  MISSING   {t}")

print("\n" + "=" * 64)
print("Schema inspection complete.")
print("=" * 64)
