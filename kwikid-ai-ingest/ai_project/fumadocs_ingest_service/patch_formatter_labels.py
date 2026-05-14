import json
from pathlib import Path

path = Path(r"C:\Users\Dyaneshwar.Shekade\Desktop\raw_Data\codes\kwikid_ai_ingest_UI\kwikid_ai_ingest\ai_project\fumadocs_ingest_service\Telegram + Freshdesk -_ OpenAPI Issue Analysis -_ Supabase RAG -_ OpenAPI Response (8).json")
data = json.loads(path.read_text(encoding="utf-8"))

node = next(n for n in data["nodes"] if n.get("name") == "Freshdesk Public Reply (First Response)")
body = node["parameters"].get("body", "")

body = body.replace(
"    .replace(/\\r/g, '')\n    .replace(/\\n{3,}/g, '\\n\\n')\n    .trim();",
"    .replace(/\\r/g, '')\n    .replace(/^\\s*greeting\\s*:\\s*/gim, '')\n    .replace(/^\\s*acknowledgement\\s*:\\s*/gim, '')\n    .replace(/^\\s*possible\\s*reasons?\\s*:\\s*/gim, 'Possible reasons: ')\n    .replace(/^\\s*suggested\\s*checks?\\s*:\\s*/gim, 'Suggested checks: ')\n    .replace(/^\\s*next\\s*action\\s*:\\s*/gim, 'Next action: ')\n    .replace(/^\\s*closing\\s*:\\s*/gim, '')\n    .replace(/\\n{3,}/g, '\\n\\n')\n    .trim();"
)

node["parameters"]["body"] = body
path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print("patched formatter for section labels")
