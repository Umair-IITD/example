import os
import sys
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, r"c:\Users\Umair.Alam\Desktop\kwikid_support_system\kwikid-ai-ingest\ai_project\fumadocs_ingest_service")

env_path = Path(r"c:\Users\Umair.Alam\Desktop\kwikid_support_system\kwikid-ai-ingest\ai_project\fumadocs_ingest_service\.env")
load_dotenv(env_path)

from supabase import create_client

sb_url = os.getenv("SUPABASE_URL")
sb_key = os.getenv("SUPABASE_KEY")
sb = create_client(sb_url, sb_key)

print("Fetching v2 SOP chunks...")
resp = sb.table("rag_sop_chunks").select("chunk_heading, sop_id, content").eq("index_version", "v2").execute()
for r in resp.data:
    print(f"SOP: {r['sop_id']}, Heading: {r['chunk_heading']}")
    print(f"Content length: {len(r['content'])}")
    print("---")
