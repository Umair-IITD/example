# Known Limitations and Future Work

## Current Limitations

### Retrieval

| Limitation | Impact | Workaround |
|------------|--------|------------|
| No multi-hop retrieval | Cannot answer "what causes X which leads to Y?" | Increase RETRIEVAL_FINAL_TOP_K |
| No query expansion | Typos or unusual phrasing may miss relevant chunks | `query_preprocessor.py` applies basic normalization |
| FTS requires exact language | Cross-lingual queries not supported | English-only SOPs currently |
| HNSW is approximate | May miss the absolute closest vector in rare cases | Accepted trade-off for speed |
| Chunk boundary artifacts | Phrases split at chunk boundaries have lower recall | CHUNK_OVERLAP_TOKENS=150 mitigates this |

### Generation

| Limitation | Impact | Workaround |
|------------|--------|------------|
| Single LLM call | No chain-of-thought or self-verification | QA Assessment node in n8n workflow |
| Context window limit | Long SOPs may be truncated at 3500 chars per chunk | Chunk size tuning; CHAT_CONTEXT_CHUNK_MAX_CHARS |
| No image/video understanding | Cannot analyze screenshots or video KYC recordings | OCR pre-processing (Phase 2) |
| Static prompts | Prompts not personalized per agent or client | Dynamic prompt templates (Phase 2) |
| No output format validation | Response may not follow exact SOP step format | Branch completeness check catches partial failures |

### Infrastructure

| Limitation | Impact | Workaround |
|------------|--------|------------|
| Single Supabase project | All tenants share one vector store | RLS + app-layer isolation provides logical separation |
| No persistent queue | Webhook events processed synchronously | Rate limiting prevents overload |
| In-process rate limiting default | State not shared across workers | Enable Redis for multi-worker deployments |
| Supabase connection pooling | High concurrency may exhaust connections | PgBouncer or Supabase connection pooler |
| No streaming responses | Full answer generated before sending | `asyncio.to_thread` prevents event loop blocking |

### Security

| Limitation | Impact | Workaround |
|------------|--------|------------|
| Supabase key in git history (deleted file) | Key should be considered compromised | Rotate key immediately before production push |
| Prompt injection possible | Malicious ticket content could influence LLM | Human-in-the-loop review; input sanitization |
| No output scanning | LLM could hallucinate external URLs | Citation grounding reduces fabrication risk |
| PII in ticket chunks | Historical PII in vector store | `DEBUG_RAG=false` prevents API exposure |

## Phase 2 Roadmap

See `BIG_PHASE_2_ROADMAP.md` for detailed planning.

### Immediate (next sprint)

- Wire GitHub Actions CI secrets → pytest becomes blocking
- Rotate Supabase service-role key
- Update local `.env` `CHAT_CONTEXT_CHUNK_MAX_CHARS` to 3500
- Enable `FRESHDESK_WEBHOOK_ENFORCE_HMAC=true` before webhook activation

### Short-term (1–2 months)

- **PII redaction** (`app/train.py:535` TODO): Automatic redaction of emails and phone numbers from committed content
- **Streaming responses**: Server-Sent Events for `/rag/chat` — better UX for long answers
- **Evaluation harness**: Systematic quality tracking with gold dataset (`evaluation/gold_dataset.json`)
- **Multi-language support**: Support for non-English tickets and SOPs

### Medium-term (3–6 months)

- **Agentic tool use**: LLM can call Freshdesk API, Supabase RPC, or internal tools to fetch real-time data
- **Conversational memory**: Cross-session memory for recurring clients
- **Feedback learning loop**: Use thumbs up/down to re-rank future retrievals
- **Knowledge graph**: Entity linking between SOPs, tickets, and KB articles
- **Async webhook queue**: Queue webhook events for retry and load leveling

### Long-term (6–12 months)

- **RLHF fine-tuning**: Use agent feedback to fine-tune a domain-specific LLM
- **Multi-agent orchestration**: Separate agents for retrieval, generation, QA, and escalation
- **Real-time analytics**: Dashboard for support quality trends, common issues, automation rate
