const base = $node["Normalize Input"].json;
const rows = Array.isArray($json) ? $json : [];

const conversations = rows
  .map((c, i) => {
    const author = c?.user_id || c?.incoming ? 'requester' : 'agent';
    const body = String(c?.body_text || c?.body || '').replace(/\s+/g, ' ').trim();
    if (!body) return null;
    return `Conversation ${i + 1} (${author}): ${body}`;
  })
  .filter(Boolean)
  .join('\\n');

const analysisText = [
  `Subject: ${base.ticketSubject || ''}`,
  `Ticket body: ${base.ticketBody || ''}`,
  conversations ? `Conversation thread:\\n${conversations}` : ''
].filter(Boolean).join('\\n\\n').trim();

return {
  ...base,
  conversationText: conversations,
  analysisText
};