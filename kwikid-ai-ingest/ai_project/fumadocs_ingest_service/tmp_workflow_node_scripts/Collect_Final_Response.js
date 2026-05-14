const base = $node["Build Supabase Context"].json;
const firstResponse = $node["First Response (OpenAPI + RAG)"].json?.output?.[0]?.content?.[0]?.text || '';
return {
  ...base,
  firstResponse: String(firstResponse).trim()
};