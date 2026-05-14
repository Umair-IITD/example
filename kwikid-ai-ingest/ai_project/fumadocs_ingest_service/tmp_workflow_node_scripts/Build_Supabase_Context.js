const base = $node["Parse Issue Output"].json;
const queryPayload = $json;

const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
const trimTo = (value, max = 650) => {
  const text = clean(value);
  return text.length > max ? `${text.slice(0, max)}...` : text;
};
const uniq = (arr) => arr.filter((v, i) => arr.findIndex((x) => String(x).toLowerCase() === String(v).toLowerCase()) === i);
const num = (v, d) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
};
const pickMatches = (payload) => {
  if (Array.isArray(payload?.matches)) return payload.matches;
  if (Array.isArray(payload?.data?.matches)) return payload.data.matches;
  if (Array.isArray(payload?.result?.matches)) return payload.result.matches;
  if (Array.isArray(payload?.documents)) return payload.documents;
  if (Array.isArray(payload)) return payload;
  return [];
};

const isStackUrl = (url) => /stackoverflowteams\.com|stackoverflow\.com\/questions/i.test(String(url || ''));
const isFreshdeskUrl = (url) => {
  const u = String(url || '');
  return /\/a\/tickets\/\d+|\/helpdesk\/tickets\/\d+/i.test(u) || /freshdesk\.com\/a\/tickets\//i.test(u);
};

const stopWords = new Set(['the','and','for','with','from','that','this','have','were','your','you','our','are','not','but','can','cannot','will','into','onto','about','issue','ticket','please']);
const tokenize = (text) => clean(text).toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length > 2 && !stopWords.has(w));
const focusPhrases = uniq([
  base.exactIssue,
  base.issueCategory,
  base.searchQuery,
  base.issueSummary,
  base.structuredSignalText
].map(clean).filter(Boolean));
const focusTokens = uniq(focusPhrases.flatMap(tokenize)).slice(0, 60);

const extractUrlCandidates = (m) => {
  const meta = m.metadata || {};
  const keys = ['post_link', 'link', 'url', 'ticket_link', 'ticket_url', 'permalink'];
  const out = keys.map((k) => clean(meta[k] || '')).filter(Boolean);
  const content = String(m.content || m.text || m.chunk || m.page_content || '');
  const urlRe = /https?:\/\/[^\s\)\]'"<>]+/g;
  let ma;
  while ((ma = urlRe.exec(content))) out.push(clean(ma[0]));
  return uniq(out);
};

const lexicalSignal = (text, title) => {
  const hay = `${clean(title)} ${clean(text)}`.toLowerCase();
  const tokenHits = focusTokens.reduce((acc, t) => acc + (hay.includes(t) ? 1 : 0), 0);
  const overlapScore = focusTokens.length ? tokenHits / focusTokens.length : 0;
  const phraseHits = focusPhrases.slice(0, 10).reduce((acc, p) => acc + (p && hay.includes(p.toLowerCase()) ? 1 : 0), 0);
  const phraseScore = Math.min(1, phraseHits / 3);
  return { overlapScore, phraseScore };
};

const rawMatches = pickMatches(queryPayload);
const normalized = rawMatches.map((m) => {
  const metadata = m?.metadata || m?.meta || {};
  const content = m?.content || m?.text || m?.chunk || m?.page_content || '';
  const title = metadata?.title || metadata?.heading || metadata?.file_path || metadata?.source || 'Untitled';
  const similarity = Number(m?.similarity ?? m?.score ?? m?.distance ?? 0);
  const st = String(metadata?.source_type || metadata?.source || 'unknown').toLowerCase();
  const wMd = num($env.RAG_WEIGHT_MD, 1.0);
  const wJson = num($env.RAG_WEIGHT_JSON, 1.0);
  const wExcel = num($env.RAG_WEIGHT_EXCEL, 1.0);
  const wFd = num($env.RAG_WEIGHT_FRESHDESK, 1.0);
  const wMap = { md: wMd, mdx: wMd, json: wJson, excel: wExcel, freshdesk: wFd };
  const w = wMap[st] ?? 1.0;
  const effectiveScore = similarity * w;
  const lex = lexicalSignal(content, title);
  const rerankScore = (effectiveScore * 0.65) + (lex.overlapScore * 0.25) + (lex.phraseScore * 0.10);
  return { metadata, content, title, similarity, effectiveScore, overlapScore: lex.overlapScore, phraseScore: lex.phraseScore, rerankScore };
});

normalized.sort((a, b) => b.rerankScore - a.rerankScore);

const top = normalized.filter((m) => clean(m.content)).slice(0, 5);
const sims = top.map((m) => Number(m?.similarity ?? 0));
const rerankScores = top.map((m) => Number(m?.rerankScore ?? 0));
const topSimilarity = sims.length ? Math.max(...sims) : 0;
const topRerankScore = rerankScores.length ? Math.max(...rerankScores) : 0;
const confidenceScore = top.length ? ((topSimilarity * 0.55) + (topRerankScore * 0.45)) : 0;

const insufficient_context = Boolean(queryPayload?.insufficient_context);
const queryClarification = clean(queryPayload?.clarification || '');
const queryDiagnostics = queryPayload?.diagnostics || {};

const context = top.map((m, i) => {
  const sourceType = m.metadata?.source_type || m.metadata?.source || 'unknown';
  const postLink = clean(m.metadata?.post_link || m.metadata?.link || m.metadata?.url || '');
  const ticketLink = clean(m.metadata?.ticket_link || m.metadata?.ticket_url || '');
  return [
    `Result ${i + 1}`,
    `score=${Number(m?.similarity ?? 0).toFixed(3)} effective=${Number(m?.effectiveScore ?? 0).toFixed(3)} rerank=${Number(m?.rerankScore ?? 0).toFixed(3)}`,
    `source_type=${sourceType}`,
    `title=${m.title}`,
    postLink ? `post_link=${postLink}` : '',
    ticketLink ? `ticket_link=${ticketLink}` : '',
    `content=${trimTo(m.content, 650)}`
  ].filter(Boolean).join('\\n');
}).join('\\n\\n---\\n\\n');

const stackPostUrls = uniq(top.flatMap((m) => extractUrlCandidates(m).filter((u) => isStackUrl(u))));
const freshdeskUrls = uniq(top.flatMap((m) => extractUrlCandidates(m).filter((u) => isFreshdeskUrl(u))));

const referenceResults = top.map((m, i) => ({
  result: i + 1,
  score: Number(m?.similarity ?? 0),
  effectiveScore: Number(m?.effectiveScore ?? 0),
  rerankScore: Number(m?.rerankScore ?? 0),
  sourceType: m.metadata?.source_type || m.metadata?.source || 'unknown',
  title: m.title,
  postLink: clean(m.metadata?.post_link || m.metadata?.link || m.metadata?.url || ''),
  ticketLink: clean(m.metadata?.ticket_link || m.metadata?.ticket_url || ''),
  excerpt: trimTo(m.content, 280)
}));

const text = String(base.analysisText || '').toLowerCase();
const issueCategory = String(base.issueCategory || 'other').toLowerCase();
const hasSessionId = Boolean(clean(base?.structuredSignals?.sessionId)) || /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i.test(text);
const hasSymptomKeywords = /(error|failed|failure|not working|issue|problem|unable|can't|cannot|blank|black screen|stuck|timeout|otp|crash|reject|decline)/i.test(text);
const looksLikeInformationalOnly = /(test call|call done|session id|agent portal)/i.test(text);

const aiMissing = Array.isArray(base.missingFromTicket)
  ? base.missingFromTicket.map((x) => String(x || '').trim()).filter(Boolean)
  : [];

const baseThreshold = num($env.SOP_MIN_SIMILARITY, 0.2);
const categoryThresholdMap = {
  auth: Math.max(baseThreshold, 0.32),
  video_not_available: Math.max(baseThreshold, 0.30),
  network: Math.max(baseThreshold, 0.28),
  other: Math.max(baseThreshold, 0.24)
};
const categoryThreshold = categoryThresholdMap[issueCategory] || Math.max(baseThreshold, 0.26);
const hasActionableEvidence = top.length >= 2 && confidenceScore >= categoryThreshold;

const structured = base.structuredSignals || {};
const missingCriticalSignals = [];
if (issueCategory === 'video_not_available' && !clean(structured.sessionId)) missingCriticalSignals.push('sessionId');
if (issueCategory === 'auth' && !clean(structured.phoneNumber) && !clean(structured.pan)) missingCriticalSignals.push('phoneNumber_or_pan');
if (issueCategory === 'network' && !clean(structured.appVersion) && !clean(structured.platform)) missingCriticalSignals.push('appVersion_or_platform');

const needsClarification = Boolean(
  insufficient_context ||
  aiMissing.length > 0 ||
  missingCriticalSignals.length > 0 ||
  !hasActionableEvidence ||
  (looksLikeInformationalOnly && !hasSymptomKeywords) ||
  (hasSessionId && !hasSymptomKeywords)
);

const missingFromTicketHints = uniq([
  ...aiMissing,
  ...missingCriticalSignals
].map((q) => String(q || '').trim()).filter(Boolean)).slice(0, 8);

let needsClarificationReason = '';
if (needsClarification) {
  if (insufficient_context && queryClarification) needsClarificationReason = queryClarification;
  else if (insufficient_context) needsClarificationReason = 'Backend confidence gate (insufficient_context) — narrow query or add ticket details.';
  else if (!hasActionableEvidence) needsClarificationReason = `Top evidence confidence (${confidenceScore.toFixed(3)}) is below category threshold (${categoryThreshold.toFixed(3)}).`;
  else if (missingCriticalSignals.length) needsClarificationReason = `Critical diagnostic fields missing: ${missingCriticalSignals.join(', ')}.`;
  else needsClarificationReason = 'Ticket content does not include a clear symptom/root issue from client side.';
}

return {
  ...base,
  ragContext: context,
  relevantTroubleshootingContext: context,
  hasContext: top.length > 0,
  ragCount: top.length,
  topSimilarity,
  topRerankScore,
  confidenceScore,
  categoryThreshold,
  ragSampleTitles: top.map((m) => m.title).slice(0, 3),
  stackPostUrls,
  freshdeskUrls,
  referenceResults,
  insufficient_context,
  queryClarification,
  queryDiagnostics,
  needsClarification,
  needsClarificationReason,
  missingFromTicketHints,
  rerankDiagnostics: {
    focusPhrases: focusPhrases.slice(0, 8),
    focusTokenCount: focusTokens.length,
    hasActionableEvidence
  }
};