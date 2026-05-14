import json
from pathlib import Path

path = Path(r"C:\Users\Dyaneshwar.Shekade\Desktop\raw_Data\codes\kwikid_ai_ingest_UI\kwikid_ai_ingest\ai_project\fumadocs_ingest_service\Telegram + Freshdesk -_ OpenAPI Issue Analysis -_ Supabase RAG -_ OpenAPI Response (8).json")
data = json.loads(path.read_text(encoding="utf-8"))
node = next(n for n in data["nodes"] if n.get("name") == "Freshdesk Public Reply (First Response)")
body = node["parameters"]["body"]

body = body.replace(
"    .replace(/\\n{3,}/g, '\\n\\n')\\n    .trim();",
"    .replace(/\\n{3,}/g, '\\n\\n')\\n    .replace(/^\\s*\\d+\\s*:\\s*\\d+\\s*$/gm, '')\\n    .trim();"
)

body = body.replace(
"  const reasons = hasReason\\n    ? extractSection(/^(possible\\s*reason|possible\\s*reasons|likely\\s*reason|root\\s*cause)/i, /^(suggested\\s*(check|checks|step|steps|fix|fixes)|next\\s*steps|our\\s*support\\s*team|we\\s*will)/i)\\n    : 'Possible reasons:\\n- The issue may be related to incomplete sync, client-side session interruption, or missing/invalid input details.';",
"  const reasonsCandidate = hasReason\\n    ? extractSection(/^(possible\\s*reason|possible\\s*reasons|likely\\s*reason|root\\s*cause)/i, /^(suggested\\s*(check|checks|step|steps|fix|fixes)|next\\s*steps|our\\s*support\\s*team|we\\s*will)/i)\\n    : '';\\n  const reasons = norm(reasonsCandidate).length > 20\\n    ? reasonsCandidate\\n    : 'Possible reasons:\\n- The issue may be related to incomplete sync, client-side session interruption, or missing/invalid input details.';"
)

body = body.replace(
"  const fixes = hasFix\\n    ? extractSection(/^(suggested\\s*(check|checks|step|steps|fix|fixes)|next\\s*steps|troubleshoot)/i, /^(our\\s*support\\s*team|we\\s*will)/i)\\n    : 'Suggested checks:\\n1) Retry from the same browser/session.\\n2) Ensure stable internet and complete flow before closing session.\\n3) Share Session ID and User ID for backend verification.';",
"  const fixesCandidate = hasFix\\n    ? extractSection(/^(suggested\\s*(check|checks|step|steps|fix|fixes)|next\\s*steps|troubleshoot)/i, /^(our\\s*support\\s*team|we\\s*will)/i)\\n    : '';\\n  const fixes = norm(fixesCandidate).length > 20\\n    ? fixesCandidate\\n    : 'Suggested checks:\\n1) Retry from the same browser/session.\\n2) Ensure stable internet and complete flow before closing session.\\n3) Share Session ID and User ID for backend verification.';"
)

node["parameters"]["body"] = body
path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print("patched formatter to guarantee non-empty reasons/checks and remove id marker lines")
