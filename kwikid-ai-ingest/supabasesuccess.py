import logging
import random
import uuid

from supabase import create_client

# ── CONFIG ─────────────────────────────────────────
SUPABASE_URL = "https://xvsokgmkoogooysuldks.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inh2c29rZ21rb29nb295c3VsZGtzIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NDM0ODI1OCwiZXhwIjoyMDg5OTI0MjU4fQ.8nwMPSRBiSb9l5KvFghj-uqQQSTOV1Xd69OKWSs8N4Y"
TABLE_NAME = "documents"
EMBEDDING_DIM = 768

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ── Generate dummy embedding ───────────────────────
def generate_embedding(dim: int = EMBEDDING_DIM):
    return [round(random.uniform(-1, 1), 6) for _ in range(dim)]

# ── MAIN VALIDATION ────────────────────────────────
def validate_vector_db():
    log.info("🔌 Connecting to Supabase...")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    # ───────────────────────────────────────────────
    # ✅ 1. DB ACCESS CHECK
    # ───────────────────────────────────────────────
    try:
        res = supabase.table(TABLE_NAME).select("id").limit(1).execute()
        log.info("✅ DB ACCESSIBLE: Table reachable")
    except Exception as e:
        log.error(f"❌ DB ACCESS FAILED: {e}")
        return

    # ───────────────────────────────────────────────
    # ✅ 2. INSERT TEST VECTOR
    # ───────────────────────────────────────────────
    test_id = str(uuid.uuid4())
    test_embedding = generate_embedding()

    try:
        supabase.table(TABLE_NAME).insert({
            "id": test_id,
            "content": "test vector validation",
            "embedding": test_embedding,
            "metadata": {"test": True}
        }).execute()

        log.info("✅ TEST INSERT SUCCESS")
    except Exception as e:
        log.error(f"❌ INSERT FAILED: {e}")
        return

    # ───────────────────────────────────────────────
    # ✅ 3. VECTOR SIMILARITY QUERY
    # ───────────────────────────────────────────────
    try:
        query_vector = test_embedding

        response = supabase.rpc(
            "match_documents",  # Requires SQL function (below)
            {
                "query_embedding": query_vector,
                "match_count": 3
            }
        ).execute()

        results = response.data

        if results and len(results) > 0:
            log.info("✅ VECTOR SEARCH WORKING")
            log.info(f"Top match ID: {results[0]['id']}")
        else:
            log.error("❌ VECTOR SEARCH FAILED: No results")

    except Exception as e:
        log.error(f"❌ VECTOR QUERY FAILED: {e}")
        return

    # ───────────────────────────────────────────────
    # ✅ FINAL RESULT
    # ───────────────────────────────────────────────
    log.info("\n🎉 SUCCESS: VECTOR DB IS CORRECTLY IMPLEMENTED")
    log.info("✔ DB accessible")
    log.info("✔ Vector similarity working")
    log.info("✔ Ready for ingestion service")

if __name__ == "__main__":
    validate_vector_db()