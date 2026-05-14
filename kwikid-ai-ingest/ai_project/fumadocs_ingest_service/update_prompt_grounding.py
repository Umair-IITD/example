import json
from pathlib import Path

path = Path(r"C:\Users\Dyaneshwar.Shekade\Desktop\raw_Data\codes\kwikid_ai_ingest_UI\kwikid_ai_ingest\ai_project\fumadocs_ingest_service\Telegram + Freshdesk -_ OpenAPI Issue Analysis -_ Supabase RAG -_ OpenAPI Response (8).json")
data = json.loads(path.read_text(encoding="utf-8"))

new_prompt_js = '''const data = $json;

const clean = (v) => String(v || "").replace(/\s+/g, " ").trim();
const category = clean(data.issueCategory || "other").toLowerCase();
const missingHints = Array.isArray(data.missingFromTicketHints)
  ? data.missingFromTicketHints.map(clean).filter(Boolean)
  : [];
const refs = Array.isArray(data.referenceResults) ? data.referenceResults : [];

const refText = refs.length
  ? refs
      .map((r) => {
        const resultId = Number(r.result || 0);
        const score = Number(r.score || 0).toFixed(3);
        const source = clean(r.sourceType || "unknown");
        const title = clean(r.title || "Untitled");
        const excerpt = clean(r.excerpt || "");
        return `Result ${resultId}: score=${score} source=${source} title=${title} excerpt=${excerpt}`;
      })
      .join("\\n")
  : "(none)";

const evidenceDigest = refs.length
  ? refs.slice(0, 5).map((r) => {
      const rid = Number(r.result || 0);
      const src = clean(r.sourceType || "unknown");
      const snippet = clean(r.excerpt || r.title || "");
      return `[Result ${rid}] (${src}) ${snippet}`;
    }).join("\\n")
  : "(no evidence found)";

const hasContext = Boolean(data.hasContext);
const confidence = Number(data.confidenceScore || 0);

const prompt = [
  "You are a production support assistant for KwikID handling Freshdesk first PUBLIC responses.",
  "Your response must be grounded in ticket text + Supabase RAG results only.",
  "Never use outside knowledge. Never invent causes, fixes, IDs, URLs, statuses, or timelines.",
  "",
  "Decision policy:",
  "1) Compare ticket facts against RAG evidence.",
  "2) Use only claims directly supported by references.",
  "3) If evidence is weak or missing, ask only essential missing details.",
  "4) Keep tone professional and concise.",
  "",
  "Hard constraints:",
  "- Do not include Subject:, Ticket Subject:, markdown headings, or internal-only text.",
  "- Do not mention model uncertainty language like 'as an AI'.",
  "- Do not provide backend/db/infra runbook steps to customer.",
  "- For every factual reason/fix line, append a reference tag like [Result 2].",
  "- If no reliable reference exists, do not present that claim as fact.",
  "",
  "Return plain text with EXACT sections in this order:",
  "Greeting:",
  "Acknowledgement:",
  "Possible reasons:",
  "Suggested checks:",
  "Next action:",
  "Closing:",
  "",
  "Section rules:",
  "- Greeting: one short line.",
  "- Acknowledgement: acknowledge ticket issue in one line using ticket facts.",
  "- Possible reasons: 1-3 bullet points, each with [Result N] if evidence exists.",
  "- Suggested checks: 2-4 numbered user-operable steps with [Result N] where applicable.",
  "- Next action: one line about support follow-up; if needed ask minimum details (e.g. Session ID, User ID).",
  "- Closing: exactly two lines: 'Thanks & Regards' then 'kwikid AI support'.",
  "",
  `Channel: ${clean(data.source)}`,
  `Exact issue: ${clean(data.exactIssue)}`,
  `Issue category: ${category}`,
  `Issue summary: ${clean(data.issueSummary)}`,
  `hasContext: ${hasContext}`,
  `confidenceScore: ${confidence.toFixed(3)}`,
  `needsClarification: ${Boolean(data.needsClarification)}`,
  `needsClarificationReason: ${clean(data.needsClarificationReason || "(none)")}`,
  `missingHints: ${missingHints.length ? missingHints.join(" | ") : "(none)"}`,
  "",
  "Ticket text:",
  clean(data.analysisText || "(none)"),
  "",
  "Supabase RAG context:",
  clean(data.ragContext || "(No context found)"),
  "",
  "Reference result summary:",
  refText,
  "",
  "Evidence digest (top matches):",
  evidenceDigest,
  "",
  "If hasContext=false OR confidenceScore<0.35, minimize assumptions and ask only essential missing fields.",
  "Generate the final customer-ready response now."
].join("\\n");

return {
  ...data,
  prompt,
  firstResponsePolicy: {
    evidenceRequired: true,
    hasContext,
    confidenceScore: confidence,
    referenceCount: refs.length,
    missingHintCount: missingHints.length
  }
};'''

updated = False
for node in data.get("nodes", []):
    if node.get("name") == "Prepare First Response Prompt":
        node.setdefault("parameters", {})["jsCode"] = new_prompt_js
        updated = True
        break

if not updated:
    raise SystemExit("Prepare First Response Prompt node not found")

path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print("updated Prepare First Response Prompt")
