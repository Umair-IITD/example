import json
from pathlib import Path

fn = Path(r"C:\Users\Dyaneshwar.Shekade\Desktop\raw_Data\codes\kwikid_ai_ingest_UI\kwikid_ai_ingest\ai_project\fumadocs_ingest_service\Telegram + Freshdesk -_ OpenAPI Issue Analysis -_ Supabase RAG -_ OpenAPI Response (8).json")
with fn.open("r", encoding="utf-8") as f:
    data = json.load(f)

body_expr = r'''={{ JSON.stringify((() => {
  const issue = String($json.ticketSubject || $json.exactIssue || 'your request')
    .replace(/<[^>]*>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

  const fallback = `Hi Team,\n\nThank you for contacting us. We have received your request regarding ${issue || 'your request'}.\n\nPossible reasons:\n- The issue may be related to incomplete sync, client-side session interruption, or invalid/missing input details.\n\nSuggested checks:\n1) Re-validate the request details and retry from the same browser/session.\n2) Confirm stable internet and complete upload/submit flow before closing the session.\n3) Share Session ID and User ID so we can verify server-side records.\n\nOur support team is reviewing this and will share an update shortly.\n\nThanks & Regards\nkwikid AI support`;

  const llmReply = String($json.firstResponse || '').trim();
  let msg = llmReply || fallback;

  msg = msg
    .replace(/^\s*subject\s*:\s*.+$/gim, '')
    .replace(/^\s*ticket\s*subject\s*:\s*.+$/gim, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();

  const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const hasGreeting = /^(hi|hello|dear)\b/i.test(msg);
  const hasAck = /(thank\s*you\s*for\s*(contacting|reaching)|we\s*have\s*received\s*your\s*request|we\s*acknowledge)/i.test(msg);
  const hasReason = /(possible\s*reason|possible\s*reasons|likely\s*reason|root\s*cause)/i.test(msg);
  const hasFix = /(suggested\s*(check|checks|step|steps|fix|fixes)|next\s*steps|troubleshoot)/i.test(msg);
  const hasNextAction = /(our\s*support\s*team|we\s*will\s*(review|check|update|investigate)|internal\s*escalation)/i.test(msg);

  if (!hasGreeting) msg = `Hi Team,\n\n${msg}`;
  if (!hasAck) msg = `Hi Team,\n\nThank you for contacting us.\n\n${msg}`;

  if (!hasReason || !hasFix || !hasNextAction) {
    const firstLine = norm(msg.split('\n')[0]);
    const ackLine = hasAck ? '' : 'Thank you for contacting us. We have received your request.';
    const reasonBlock = hasReason ? '' : 'Possible reasons:\n- The issue may be related to incomplete sync, client-side session interruption, or missing/invalid input details.';
    const fixBlock = hasFix ? '' : 'Suggested checks:\n1) Retry from the same browser/session.\n2) Ensure stable internet and complete flow before closing session.\n3) Share Session ID and User ID for backend verification.';
    const nextBlock = hasNextAction ? '' : 'Our support team is reviewing this and will update you shortly.';
    msg = [firstLine || 'Hi Team,', ackLine, msg, reasonBlock, fixBlock, nextBlock]
      .filter((part) => norm(part))
      .join('\n\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }

  if (msg.length > 6000) {
    msg = msg.slice(0, 5900).trim() + '\n\n[Response truncated for brevity. Please contact support for full details.]';
  }

  const hasSignature = /thanks\s*&\s*regards[\s\S]*kwikid\s*ai\s*support/i.test(msg);
  if (!hasSignature) {
    msg = `${msg}\n\nThanks & Regards\nkwikid AI support`.trim();
  }

  const mode = String($json.replyVisibility || 'public').toLowerCase();
  const isPrivate = mode === 'private';
  const payload = {
    body: '<div><p>' + msg.replace(/\n/g, '<br/>') + '</p></div>'
  };

  if (isPrivate) {
    payload.private = true;
  } else {
    const cc = Array.isArray($json.ccEmails)
      ? [...new Set($json.ccEmails.map((v) => String(v || '').trim().toLowerCase()).filter(Boolean))]
      : [];
    if (cc.length > 0) payload.cc_emails = cc;
  }

  return payload;
})()) }}'''

for node in data.get("nodes", []):
    if node.get("name") == "Freshdesk Public Reply (First Response)":
        node.setdefault("parameters", {})["body"] = body_expr
        break

with fn.open("w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("patched public reply body")
