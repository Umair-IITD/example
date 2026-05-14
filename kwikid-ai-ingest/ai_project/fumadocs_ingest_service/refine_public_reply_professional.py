import json
from pathlib import Path

path = Path(r"C:\Users\Dyaneshwar.Shekade\Desktop\raw_Data\codes\kwikid_ai_ingest_UI\kwikid_ai_ingest\ai_project\fumadocs_ingest_service\Telegram + Freshdesk -_ OpenAPI Issue Analysis -_ Supabase RAG -_ OpenAPI Response (8).json")
data = json.loads(path.read_text(encoding="utf-8"))

new_body = r'''={{ JSON.stringify((() => {
  const issue = String($json.ticketSubject || $json.exactIssue || 'your request')
    .replace(/<[^>]*>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

  const fallbackCore = [
    `Thank you for contacting us. We have received your request regarding ${issue || 'your request'}.`,
    'Possible reasons:\n- The issue may be related to incomplete sync, client-side session interruption, or invalid/missing input details.',
    'Suggested checks:\n1) Re-validate the request details and retry from the same browser/session.\n2) Confirm stable internet and complete upload/submit flow before closing the session.\n3) Share Session ID and User ID so we can verify server-side records.',
    'Our support team is reviewing this and will share an update shortly.'
  ].join('\n\n');

  const llmReply = String($json.firstResponse || '').trim();
  let raw = (llmReply || fallbackCore)
    .replace(/<[^>]*>/g, ' ')
    .replace(/^\s*subject\s*:\s*.+$/gim, '')
    .replace(/^\s*ticket\s*subject\s*:\s*.+$/gim, '')
    .replace(/\r/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();

  // Remove any prior greeting/signature so we can rebuild once in correct order.
  raw = raw
    .replace(/^(?:hi|hello|dear)[^\n]*\n+/i, '')
    .replace(/\n*thanks\s*&\s*regards[\s\S]*$/i, '')
    .trim();

  const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const lines = raw.split('\n').map((l) => l.trim());

  const hasAck = /(thank\s*you\s*for\s*(contacting|reaching)|we\s*have\s*received\s*your\s*request|we\s*acknowledge)/i.test(raw);
  const hasReason = /(possible\s*reason|possible\s*reasons|likely\s*reason|root\s*cause)/i.test(raw);
  const hasFix = /(suggested\s*(check|checks|step|steps|fix|fixes)|next\s*steps|troubleshoot)/i.test(raw);
  const hasNextAction = /(our\s*support\s*team|we\s*will\s*(review|check|update|investigate)|internal\s*escalation)/i.test(raw);

  const extractSection = (regexStart, stopRegex) => {
    const idx = lines.findIndex((l) => regexStart.test(l));
    if (idx === -1) return '';
    const buff = [lines[idx]];
    for (let i = idx + 1; i < lines.length; i += 1) {
      if (stopRegex.test(lines[i])) break;
      buff.push(lines[i]);
    }
    return norm(buff.join('\n'));
  };

  const ack = hasAck
    ? (lines.find((l) => /(thank\s*you\s*for\s*(contacting|reaching)|we\s*have\s*received\s*your\s*request|we\s*acknowledge)/i.test(l)) || '')
    : `Thank you for contacting us. We have received your request regarding ${issue || 'your request'}.`;

  const reasons = hasReason
    ? extractSection(/^(possible\s*reason|possible\s*reasons|likely\s*reason|root\s*cause)/i, /^(suggested\s*(check|checks|step|steps|fix|fixes)|next\s*steps|our\s*support\s*team|we\s*will)/i)
    : 'Possible reasons:\n- The issue may be related to incomplete sync, client-side session interruption, or missing/invalid input details.';

  const fixes = hasFix
    ? extractSection(/^(suggested\s*(check|checks|step|steps|fix|fixes)|next\s*steps|troubleshoot)/i, /^(our\s*support\s*team|we\s*will)/i)
    : 'Suggested checks:\n1) Retry from the same browser/session.\n2) Ensure stable internet and complete flow before closing session.\n3) Share Session ID and User ID for backend verification.';

  const nextAction = hasNextAction
    ? (lines.find((l) => /(our\s*support\s*team|we\s*will\s*(review|check|update|investigate)|internal\s*escalation)/i.test(l)) || '')
    : 'Our support team is reviewing this and will update you shortly.';

  const professionalMsg = [
    'Hello,',
    ack,
    reasons,
    fixes,
    nextAction,
    'Thanks & Regards',
    'kwikid AI support'
  ]
    .filter((part) => norm(part))
    .join('\n\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();

  let msg = professionalMsg;
  if (msg.length > 6000) {
    msg = msg.slice(0, 5900).trim() + '\n\n[Response truncated for brevity. Please contact support for full details.]\n\nThanks & Regards\nkwikid AI support';
  }

  // Ensure signature remains terminal even in truncation path.
  msg = msg.replace(/\n*thanks\s*&\s*regards[\s\S]*$/i, '').trim() + '\n\nThanks & Regards\nkwikid AI support';

  const mode = String($json.replyVisibility || 'public').toLowerCase();
  const isPrivate = mode === 'private';
  const payload = { body: '<div><p>' + msg.replace(/\n/g, '<br/>') + '</p></div>' };

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
        node["parameters"]["body"] = new_body
        break

path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print("updated public reply formatter")
