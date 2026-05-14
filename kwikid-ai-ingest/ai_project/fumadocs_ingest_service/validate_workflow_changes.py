import json
import pathlib
import sys

fn = pathlib.Path(r"C:\Users\Dyaneshwar.Shekade\Desktop\raw_Data\codes\kwikid_ai_ingest_UI\kwikid_ai_ingest\ai_project\fumadocs_ingest_service\Telegram + Freshdesk -_ OpenAPI Issue Analysis -_ Supabase RAG -_ OpenAPI Response (8).json")
d = json.loads(fn.read_text(encoding="utf-8"))
idx = {n["name"]: n for n in d["nodes"]}

checks = []
pub = idx["Freshdesk Public Reply (First Response)"]["parameters"]
body = pub.get("body", "")
checks.append(("public-route-reply-notes", "mode === 'private' ? 'notes' : 'reply'" in pub.get("url", "")))
checks.append(("has-fallback", "const fallback = `Hi Team,\\n\\n" in body))
checks.append(("has-greeting-enforcement", "hasGreeting" in body))
checks.append(("has-ack-enforcement", "hasAck" in body))
checks.append(("has-reason-enforcement", "hasReason" in body))
checks.append(("has-fix-enforcement", "hasFix" in body))
checks.append(("has-next-action-enforcement", "hasNextAction" in body))
checks.append(("has-max-length-guard", "msg.length > 6000" in body))
checks.append(("has-signature-enforcement", "Thanks & Regards\\nkwikid AI support" in body))
checks.append(("has-cc-handling", "payload.cc_emails = cc" in body))

priv = idx["Freshdesk Private Ask Missing Note"]["parameters"]
checks.append(("private-note-endpoint", "/notes" in priv.get("url", "")))
checks.append(("private-note-flag", "private: true" in priv.get("body", "")))

collect = idx["Collect Final Response"]["parameters"].get("jsCode", "")
checks.append(("diagnostics-added", "responseDiagnostics" in collect and "confidenceScore" in collect and "needsClarification" in collect))

failed = [c for c in checks if not c[1]]
for name, ok in checks:
    print(f"{name}: {'PASS' if ok else 'FAIL'}")
print(f"total={len(checks)} failed={len(failed)}")
if failed:
    sys.exit(1)
