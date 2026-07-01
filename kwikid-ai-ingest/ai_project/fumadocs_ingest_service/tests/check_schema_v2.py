"""
Supabase schema v2 — correct parameter names, full column inspection.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from supabase import create_client

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

print("=" * 70)
print("SUPABASE SCHEMA INSPECTION v2 — correct RPC signatures")
print("=" * 70)

# ── 1. RPC check with CORRECT parameter names ────────────────────────────
print("\n[1] RPC Functions (correct params):")
try:
    r = sb.rpc("match_all_b1_sources", {
        "p_query_embedding": [0.0] * 1536,
        "p_client": "unity",
        "p_match_count": 1,
        "p_match_threshold": 0.99,
        "p_index_version": "v2"
    }).execute()
    print(f"  match_all_b1_sources: EXISTS  (returned {len(r.data)} rows for threshold=0.99)")
except Exception as e:
    print(f"  match_all_b1_sources: ERROR  ({str(e)[:120]})")

try:
    r = sb.rpc("search_b1_sources_fts", {
        "p_query_text": "OTP delivery failure",
        "p_client": "unity",
        "p_match_count": 1,
        "p_index_version": "v2"
    }).execute()
    print(f"  search_b1_sources_fts: EXISTS  (returned {len(r.data)} rows)")
except Exception as e:
    print(f"  search_b1_sources_fts: ERROR  ({str(e)[:120]})")

# ── 2. rag_knowledge_articles columns ───────────────────────────────────
print("\n[2] rag_knowledge_articles columns:")
try:
    r = sb.table("rag_knowledge_articles").select("*").limit(1).execute()
    if r.data:
        cols = list(r.data[0].keys())
        print(f"  Columns: {cols}")
        has_img = "image_metadata" in cols
        print(f"  image_metadata: {'PRESENT' if has_img else 'MISSING'}")
    else:
        # Table is empty — try select individual cols
        for col in ["article_id", "title", "knowledge_class", "quality_score", "image_metadata", "clients"]:
            try:
                sb.table("rag_knowledge_articles").select(col).limit(1).execute()
                print(f"  Column {col}: EXISTS")
            except Exception as ex:
                print(f"  Column {col}: MISSING ({str(ex)[:80]})")
except Exception as e:
    print(f"  Error: {e}")

# ── 3. rag_knowledge_chunks columns ─────────────────────────────────────
print("\n[3] rag_knowledge_chunks columns:")
try:
    r = sb.table("rag_knowledge_chunks").select("*").limit(1).execute()
    if r.data:
        cols = list(r.data[0].keys())
        print(f"  Columns: {cols}")
    else:
        for col in ["chunk_id", "article_id", "content", "embedding", "fts", "quality_score", "knowledge_class"]:
            try:
                sb.table("rag_knowledge_chunks").select(col).limit(1).execute()
                print(f"  Column {col}: EXISTS")
            except Exception as ex:
                print(f"  Column {col}: MISSING ({str(ex)[:80]})")
except Exception as e:
    print(f"  Error: {e}")

# ── 4. Embedding status ──────────────────────────────────────────────────
print("\n[4] Embedding status in rag_knowledge_chunks:")
try:
    # Count chunks WITH embeddings (non-null)
    r_total = sb.table("rag_knowledge_chunks").select("*", count="exact").limit(0).execute()
    total = r_total.count
    print(f"  Total chunks: {total}")
    # We can't easily count NULL vs non-NULL via the client API, so show sample
    r_sample = sb.table("rag_knowledge_chunks").select("chunk_id,knowledge_class,quality_score").limit(5).execute()
    print(f"  Sample chunks (first 5):")
    for ch in r_sample.data:
        print(f"    chunk_id={str(ch.get('chunk_id','?'))[:20]}  class={ch.get('knowledge_class')}  quality={ch.get('quality_score')}")
except Exception as e:
    print(f"  Error: {e}")

# ── 5. Article sample with image_metadata status ─────────────────────────
print("\n[5] Article sample:")
try:
    r = sb.table("rag_knowledge_articles").select(
        "article_id,title,knowledge_class,quality_score,clients"
    ).limit(5).execute()
    for a in r.data:
        print(f"  id={str(a.get('article_id','?'))[:20]}  class={a.get('knowledge_class')}  q={a.get('quality_score')}  clients={a.get('clients')}")
except Exception as e:
    print(f"  Error: {e}")

# ── 6. Quality score distribution ────────────────────────────────────────
print("\n[6] Knowledge chunk quality distribution (sample):")
try:
    r = sb.table("rag_knowledge_chunks").select("quality_score").limit(200).execute()
    scores = [row["quality_score"] for row in r.data if row.get("quality_score") is not None]
    if scores:
        above_gate = sum(1 for s in scores if s >= 0.55)
        below_gate = sum(1 for s in scores if s < 0.55)
        print(f"  Sample size: {len(scores)}")
        print(f"  Above quality gate (>=0.55): {above_gate}")
        print(f"  Below quality gate (<0.55):  {below_gate}")
        print(f"  Min: {min(scores):.3f}  Max: {max(scores):.3f}  Avg: {sum(scores)/len(scores):.3f}")
except Exception as e:
    print(f"  Error: {e}")

# ── 7. Existing match_all_b1_sources with low threshold ─────────────────
print("\n[7] match_all_b1_sources with threshold=0.0 (expect knowledge results):")
try:
    r = sb.rpc("match_all_b1_sources", {
        "p_query_embedding": [0.0] * 1536,
        "p_client": "unity",
        "p_match_count": 5,
        "p_match_threshold": 0.0,
        "p_index_version": "v2"
    }).execute()
    print(f"  Total rows returned: {len(r.data)}")
    for row in r.data[:3]:
        print(f"  source={row.get('source_table')}  score={row.get('boosted_score'):.4f}  "
              f"has_image_meta={'image_metadata' in (row.get('extra_metadata') or {})}")
except Exception as e:
    print(f"  Error: {str(e)[:120]}")

print("\n" + "=" * 70)
