import json
from pathlib import Path

path = Path(r"C:\Users\Dyaneshwar.Shekade\Desktop\raw_Data\codes\kwikid_ai_ingest_UI\kwikid_ai_ingest\ai_project\fumadocs_ingest_service\Telegram + Freshdesk -_ OpenAPI Issue Analysis -_ Supabase RAG -_ OpenAPI Response (8).json")
data = json.loads(path.read_text(encoding="utf-8"))

node = next(n for n in data["nodes"] if n.get("name") == "Freshdesk Public Reply (First Response)")
body = node["parameters"]["body"]

needle = "    .replace(/\\r/g, '')\n    .replace(/^\\s*greeting\\s*:\\s*/gim, '')"
replacement = "    .replace(/\\r/g, '')\n    .replace(/\\[\\s*Result\\s*\\d+\\s*\\]/gi, '')\n    .replace(/^\\s*greeting\\s*:\\s*/gim, '')"

if needle not in body:
    raise SystemExit("expected formatter block not found")

body = body.replace(needle, replacement, 1)

# Cleanup accidental extra spaces before punctuation after tag removal.
cleanup_needle = "  const norm = (s) => String(s || '').replace(/\\s+/g, ' ').trim();"
cleanup_replacement = "  raw = raw.replace(/\\s+([,.;:!?])/g, '$1').replace(/\\s{2,}/g, ' ').trim();\n\n  const norm = (s) => String(s || '').replace(/\\s+/g, ' ').trim();"
if cleanup_needle in body:
    body = body.replace(cleanup_needle, cleanup_replacement, 1)

node["parameters"]["body"] = body
path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print("removed internal [Result N] markers from public reply")
