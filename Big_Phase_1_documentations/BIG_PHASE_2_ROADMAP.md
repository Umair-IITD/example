# BIG PHASE 2 ROADMAP

## Executive Summary

Big Phase 1 delivered a production-ready RAG-based support automation system with hybrid retrieval, governance enforcement, multi-tenant isolation, and n8n orchestration. Phase 2 focuses on **intelligence amplification** — making the system learn from its own outputs, handle more complex queries, scale to higher loads, and extend into agentic operation.

This document is a living roadmap organized into four time horizons and nine capability domains.

---

## Time Horizons

| Horizon | Timeline | Theme |
|---------|----------|-------|
| **Sprint 0** | Immediately post-launch (week 1) | Critical stabilization — zero feature work |
| **Near** | 1–2 months | Core quality and reliability improvements |
| **Mid** | 3–6 months | Intelligence and agentic expansion |
| **Far** | 6–12 months | Platform maturity and ML sophistication |

---

## Sprint 0 — Stabilization (Do These First)

These are not features; they are release completions that must happen before any user traffic:

### S0.1 — Supabase Key Rotation
- Go to Supabase dashboard → Project Settings → API
- Generate a new service-role key
- Update production `.env` (never commit the new key)
- Verify old key is rejected: `SUPABASE_KEY=old_key python -c "from rag_engine.supabase_client import get_client; get_client().table('documents').select('id').limit(1).execute()"`
- **Risk if skipped**: the old key is in git history in a deleted file. Anyone who clones the repo history can extract it.

### S0.2 — GitHub Actions Secrets Wiring
- Add `SUPABASE_URL`, `SUPABASE_KEY`, `OPENAI_API_KEY` as repository secrets in GitHub
- Remove `|| true` from the pytest step in `.github/workflows/main.yml`
- Verify CI passes on a test push
- **Risk if skipped**: CI currently does not fail on test failures — broken code can reach production

### S0.3 — Webhook HMAC Enforcement
- Set `FRESHDESK_WEBHOOK_ENFORCE_HMAC=true` in production `.env`
- Set `FRESHDESK_WEBHOOK_SECRET` to a strong random value (`python -c "import secrets; print(secrets.token_urlsafe(32))"`)
- Configure Freshdesk to sign webhooks with this secret
- **Risk if skipped**: anyone who discovers the webhook URL can send fake ticket events

### S0.4 — First Live Validation
- Send a real Freshdesk ticket after deployment
- Verify the n8n workflow triggers, calls `/rag/chat`, and posts both public reply and private note
- Confirm Telegram agent queries work
- Document baseline response latency

---

## Domain 1 — Evaluation and Quality Tracking

**Problem**: There is no systematic way to know if answer quality is improving or degrading over time.

### 1.1 Gold Dataset Construction (Near)

Build a ground-truth evaluation set:

```
evaluation/
├── gold_dataset.json          # 50+ query/expected_answer pairs
├── evaluate.py                # Automated evaluation runner
└── results/                   # Timestamped evaluation reports
```

Each gold item:
```json
{
  "id": "gold_001",
  "query": "Customer cannot complete liveness check on iOS 16",
  "expected_themes": ["camera_permissions", "ios_update", "retry_steps"],
  "expected_citations": ["sop_liveness_ios.md"],
  "min_confidence": "high",
  "should_not_escalate": true
}
```

Evaluation metrics:
- **Retrieval recall**: Did at least one expected citation appear in chunks?
- **Theme coverage**: Did the answer address all expected themes?
- **Confidence accuracy**: Was confidence correctly predicted?
- **Escalation accuracy**: Did the system correctly route to human vs. auto-resolve?

### 1.2 Regression Gate in CI (Near)

Add a CI step that runs the gold dataset and fails if:
- Recall drops below 80%
- Confidence accuracy drops below 85%
- Any previously passing test now fails

This prevents silent quality regressions when prompts, chunking, or retrieval changes.

### 1.3 A/B Testing Framework (Mid)

Allow two retrieval or generation configurations to run simultaneously on different ticket slots:
- Config A: current production
- Config B: experimental (new prompt, new model, different reranking)

Compare outcomes on: confidence distribution, requires_human rate, agent thumbs-up rate.

---

## Domain 2 — Feedback Learning Loop

**Problem**: Agents give thumbs up/down but the signals are not used to improve retrieval.

### 2.1 Feedback Signal Storage (Already Partially Built)

Current state: thumbs up/down stored in feedback table.

Missing: actually using those signals.

### 2.2 Retrieval Re-Ranking from Feedback (Near)

For every chunk that was cited in a thumbs-up response, increment its retrieval score in a `chunk_feedback_weights` table:

```sql
CREATE TABLE chunk_feedback_weights (
    chunk_id UUID REFERENCES documents(id),
    client TEXT,
    positive_count INTEGER DEFAULT 0,
    negative_count INTEGER DEFAULT 0,
    last_updated TIMESTAMPTZ DEFAULT NOW()
);
```

At retrieval time, multiply the final RRF score by `(1 + 0.1 * log(1 + positive_count))`.

This is a lightweight collaborative filtering signal — no ML training required.

### 2.3 Review Queue Processing (Near)

Thumbs-down responses land in `rag_review_queue`. Build a simple UI (or Telegram command) that lets agents:
1. See the failed response
2. Write the correct answer
3. Optionally mark a SOP that should have been retrieved

The correct answer becomes a training example for future fine-tuning.

### 2.4 Human Annotation Pipeline (Mid)

Connect review queue to a labeling workflow:
- Export queued items to Label Studio or a Google Sheet
- Annotators provide: correct answer, relevant SOP sections, confidence label
- Import annotations back as fine-tuning data (`evaluation/annotated_dataset.jsonl`)

---

## Domain 3 — Conversational Memory

**Problem**: Each ticket is handled in isolation. If the same customer has opened 5 tickets about VKYC failures, the system does not know.

### 3.1 Session History (Already Built)

`chat_messages` table and `CHAT_HISTORY_TURNS=6` already provide within-session memory.

### 3.2 Cross-Session Customer Context (Mid)

When a new ticket arrives, check if this customer (by email or Freshdesk contact ID) has recent tickets:

```python
recent_context = await supabase.table("chat_messages") \
    .select("role, content, created_at") \
    .eq("session_id", customer_session_prefix) \
    .order("created_at", desc=True) \
    .limit(10) \
    .execute()
```

Summarize the recent context and inject it into the system prompt:
> "This customer has previously reported: OTP not received (resolved), liveness check failing on iOS (escalated). Current issue: camera permissions."

### 3.3 Entity Memory (Far)

Build a lightweight entity store:
- Customer → device (iOS 14, Samsung A52), preferred language (English), escalation history
- Tenant → custom SOP sections, specific product configuration
- Issue category → historical resolution rate, average resolution time

---

## Domain 4 — Agentic Tool Use

**Problem**: The LLM currently only reads static context. It cannot fetch real-time data, look up a ticket's current status, or call a troubleshooting API during generation.

### 4.1 Tool Registry Design (Mid)

Define a tool registry that the LLM can call during generation:

```python
AVAILABLE_TOOLS = [
    Tool(
        name="get_ticket_details",
        description="Fetch a Freshdesk ticket's current status, priority, and conversation history",
        parameters={"ticket_id": "string"},
        handler=freshdesk_client.get_ticket
    ),
    Tool(
        name="check_service_status",
        description="Check if KwikID services are currently operational",
        parameters={},
        handler=uptime_kuma_client.get_summary
    ),
    Tool(
        name="get_customer_profile",
        description="Fetch customer's device, plan, and support history",
        parameters={"customer_email": "string"},
        handler=freshdesk_client.get_customer
    ),
    Tool(
        name="lookup_knowledge_article",
        description="Search the knowledge base for a specific article",
        parameters={"query": "string"},
        handler=rag_engine.search
    ),
]
```

### 4.2 ReAct Loop (Mid)

Implement a basic Reason-Act-Observe loop:

```
1. LLM sees query + tools
2. LLM reasons: "I need to check if the service is down"
3. LLM calls: check_service_status()
4. Observation injected: "Liveness API: OPERATIONAL. Uptime: 99.9%"
5. LLM continues reasoning with updated context
6. LLM generates final answer
```

Maximum iterations: 3 (prevent infinite loops). Timeout: same as current `CHAT_TIMEOUT_S`.

### 4.3 API Call Safety (Mid)

Tool calls must be constrained:
- Whitelist: only the tools above are callable
- No shell execution, no database writes, no email sending
- Tool results are sanitized before injection into prompt (no injection from tool outputs)
- Tool call log stored in `diagnostics` for audit

### 4.4 Kwik Fix API Integration (Near)

`shouldCallTroubleshootingApi` is already extracted by the n8n issue analysis. Wire it to actually call the Kwik Fix API and inject the result into the response context — this is already partially designed in the n8n workflow but not production-ready.

---

## Domain 5 — Streaming Responses

**Problem**: Long answers (troubleshooting guides) require waiting for the full generation before the user sees anything. LLM response time is 3–8 seconds.

### 5.1 Server-Sent Events for `/rag/chat` (Near)

Add `stream=true` parameter to `/rag/chat`:

```python
@router.post("/rag/chat")
async def rag_chat(request: ChatRequest):
    if request.stream:
        return StreamingResponse(
            _stream_rag_response(request),
            media_type="text/event-stream"
        )
    return await _rag_response(request)

async def _stream_rag_response(request):
    # Retrieval is still synchronous (fast)
    chunks = await retriever.retrieve(request.query_text)
    context = assembler.assemble(chunks)
    # Streaming generation
    async for token in llm_client.stream(prompt):
        yield f"data: {json.dumps({'token': token})}\n\n"
    # Final metadata event
    yield f"data: {json.dumps({'done': True, 'citations': citations, 'confidence': confidence})}\n\n"
```

### 5.2 n8n Streaming Support (Mid)

n8n's HTTP Request node does not natively support SSE. Options:
- Accumulate in n8n using a custom Code node that buffers the stream
- Use a WebSocket proxy
- Deploy a thin streaming proxy that n8n calls synchronously (proxy handles SSE, returns JSON to n8n)

### 5.3 Telegram Streaming (Mid)

Telegram bot API supports editing messages in-place. Approximate streaming:
- Send initial "Analyzing..." message
- Edit message every 500ms with accumulated tokens
- Final edit with complete response + citations

---

## Domain 6 — Infrastructure Scaling

**Problem**: The service runs as a single Docker container. High load will exhaust the Supabase connection pool and the in-process rate limiter cannot be shared across workers.

### 6.1 Redis Rate Limiting (Near)

Already implemented in `app/middleware/rate_limiting.py` — just needs `REDIS_RATE_LIMIT_ENABLED=true` and a Redis instance.

For production, add Redis as a Docker Compose service:

```yaml
redis:
  image: redis:7-alpine
  command: redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru
  volumes:
    - redis_data:/data
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]
```

### 6.2 Gunicorn with Multiple Workers (Near)

Current: `uvicorn app.main:app --workers 1`

Production: `gunicorn app.main:app --workers 4 --worker-class uvicorn.workers.UvicornWorker`

4 workers × 8 concurrent requests = 32 simultaneous connections. Requires Redis for shared rate limiting.

### 6.3 Connection Pooling (Mid)

The Supabase Python client creates one connection per call. With 4 workers and concurrent requests, this exhausts the Supabase connection pool at ~20 concurrent users.

Options:
- Enable Supabase's built-in connection pooler (PgBouncer in transaction mode)
- Set connection pool size: `SUPABASE_POOL_SIZE=10`
- Use `asyncpg` directly for high-throughput retrieval paths

### 6.4 Async Webhook Queue (Mid)

Current: webhook events are processed synchronously in the FastAPI request thread.

Problem: if the n8n workflow takes 10 seconds and Freshdesk retries the webhook after 5 seconds, the service processes duplicate tickets.

Solution:
```
Freshdesk → FastAPI webhook → Redis Queue → Worker (background) → n8n
```

FastAPI returns `{"status": "queued", "ticket_id": "..."}` immediately. A Celery or RQ worker processes the queue asynchronously. Provides: deduplication, retry, back-pressure.

### 6.5 PII Redaction at Ingest (Mid)

`app/train.py:535` has a TODO for PII redaction. Implement:
- Email regex: redact before chunk embedding
- Phone regex: redact before chunk embedding
- Government ID patterns: redact
- Store redaction audit log

This prevents PII from being stored in the vector store and returned in RAG contexts.

---

## Domain 7 — Multi-Language Support

**Problem**: KwikID serves customers who may communicate in Hindi, Tamil, or other regional languages. The system currently only works in English.

### 7.1 Language Detection (Near)

Use `langdetect` or `lingua` to detect query language at `/rag/chat` time:

```python
from lingua import Language, LanguageDetectorBuilder
detector = LanguageDetectorBuilder.from_languages(Language.ENGLISH, Language.HINDI).build()
lang = detector.detect_language_of(query_text)
```

### 7.2 Translation Layer (Mid)

For non-English queries:
1. Translate query to English (using OpenAI: "Translate to English, preserve technical terms")
2. Retrieve using English query (all SOPs are in English)
3. Translate response back to detected language
4. Return both: `answer` (translated) and `answer_en` (original English for audit)

### 7.3 Native Language SOPs (Far)

If sufficient Hindi/Tamil SOPs exist, embed them separately with a `language` metadata field. Route queries to language-specific SOP sets before falling back to English translation.

---

## Domain 8 — Analytics and Observability

**Problem**: There is no dashboard showing support automation effectiveness, common issues, or model quality trends.

### 8.1 Prometheus + Grafana (Near)

Prometheus endpoint already exists (`PROMETHEUS_ENABLED=true`). Add metrics:
- `rag_confidence_high_total`, `rag_confidence_medium_total`, `rag_confidence_low_total`
- `rag_requires_human_total`
- `rag_retrieval_latency_seconds` (histogram)
- `rag_generation_latency_seconds` (histogram)
- `rag_exact_match_total`, `rag_related_match_total`, `rag_no_match_total`
- `rag_feedback_positive_total`, `rag_feedback_negative_total`

Grafana dashboard (import JSON):
- Automation rate: `rag_confidence_high_total / (rag_confidence_high_total + rag_requires_human_total)`
- SOP match rate: `rag_exact_match_total / total_requests`
- P95 latency: `histogram_quantile(0.95, rag_retrieval_latency_seconds_bucket)`

### 8.2 Support Quality Report (Mid)

Weekly automated report (Telegram or email):
- Total tickets processed
- Automation rate (no human needed)
- Most common issue categories
- Average confidence score
- Tickets escalated (and why)
- Feedback: thumbs up / thumbs down ratio

### 8.3 Real-Time Issue Spike Detection (Far)

If ticket rate for a specific category spikes (3× above baseline in 1 hour), alert the support team on Telegram:
> "Alert: 12 tickets about 'Liveness check failing' in the last hour (baseline: 3/hour). Possible service degradation."

This closes the loop between support ticket signals and infrastructure monitoring.

---

## Domain 9 — Knowledge Graph and Entity Linking

**Problem**: SOPs, tickets, and KB articles are stored and retrieved independently. There is no understanding that "OTP failure" → triggers → "resend_otp SOP" → is documented in → "KwikID_Mobile_FAQ.md".

### 9.1 Entity Extraction (Mid)

At ingest time, extract entities from each chunk:
- Issues: "OTP delivery failure", "liveness check stuck", "camera permission denied"
- Products: "KwikID Mobile", "VKYC", "KYC SDK"
- Resolutions: "restart app", "clear cache", "update iOS"
- Systems: "Freshdesk", "S3", "liveness API"

Store in `chunk_entities` table.

### 9.2 Graph Construction (Mid)

Build an entity relationship graph:
- Issue → has_resolution → Resolution
- Issue → documented_in → SOP
- SOP → related_to → SOP (via shared entities)

Store as an adjacency list in Supabase or a lightweight graph store.

### 9.3 Graph-Augmented Retrieval (Far)

When retrieving for "OTP not received":
1. Retrieve top semantic + FTS chunks (current)
2. Find entities in those chunks (new)
3. Follow entity links to related SOPs not captured by semantic search (new)
4. Add linked chunks to context with a "graph_linked" source label

This is the "multi-hop retrieval" capability absent in Phase 1.

---

## Domain 10 — Advanced ML

### 10.1 CrossEncoder Reranking in Production (Near)

`ms-marco-MiniLM-L-6-v2` is already supported but disabled by default (BM25 is default).

Enable it: `RETRIEVAL_RERANKER=cross_encoder`

This requires ~250ms additional latency per request but significantly improves reranking accuracy for long queries.

### 10.2 Fine-Tuned Embedding Model (Far)

Current: `text-embedding-3-small` (general-purpose).

After 6 months of feedback data, fine-tune a domain-specific embedding model:
- Training data: (query, positive_chunk, negative_chunk) triplets from feedback
- Method: contrastive learning (InfoNCE loss)
- Evaluation: recall@5 on gold dataset

A domain-specific embedding model trained on KwikID support data should outperform the general model by 10–20% recall.

### 10.3 RLHF Fine-Tuning (Far)

After accumulating 1000+ annotated examples:
- Positive examples: thumbs-up responses
- Negative examples: thumbs-down + corrected responses

Fine-tune `gpt-4o-mini` equivalent (or open-source: Mistral 7B) using DPO (Direct Preference Optimization).

This produces a model that has internalized KwikID support style, SOP structure, and escalation criteria — no longer relying on prompt engineering alone.

### 10.4 Multi-Agent Orchestration (Far)

Replace the single `/rag/chat` call with a multi-agent pipeline:

```
Orchestrator Agent
├── Retrieval Agent: semantic + FTS + graph traversal
├── Analysis Agent: classify issue, determine SOP match
├── Generation Agent: produce customer-facing response
├── QA Agent: validate completeness and accuracy
└── Escalation Agent: decide requires_human, create Asana tasks
```

Each agent is a separate LLM call with a focused prompt. The orchestrator coordinates them and handles failures/retries. This produces better accuracy than a single monolithic prompt but at higher latency and cost.

---

## Dependency Map

```
Sprint 0 (all required) ──┐
                           ▼
              1.1 Gold Dataset
                    │
         ┌──────────┴──────────┐
         ▼                     ▼
    1.2 CI Gate            2.1 Feedback Storage
                                   │
              ┌────────────────────┤
              ▼                    ▼
        2.2 Retrieval         2.3 Review Queue
          Re-Ranking                │
                                    ▼
                             2.4 Annotation Pipeline
                                    │
                                    ▼
                             10.2 Fine-Tuned Embeddings
                                    │
                                    ▼
                             10.3 RLHF Fine-Tuning
```

---

## Migration Risk Register

| Feature | Risk | Mitigation |
|---------|------|------------|
| Redis rate limiting | Redis failure stops all requests | Graceful fallback to in-process (already coded) |
| Gunicorn multi-worker | Race conditions in in-process state | Redis shared state required first |
| CrossEncoder reranking | +250ms latency per request | A/B test on 10% of traffic first |
| Streaming responses | n8n cannot consume SSE natively | Proxy approach or n8n 1.x SSE plugin |
| Agentic tool use | LLM may hallucinate tool arguments | Input validation + whitelist enforcement |
| Fine-tuned embeddings | Model not compatible with existing vectors | Re-ingest all documents with new model under new `ACTIVE_INDEX_VERSION` |
| RLHF fine-tuning | Catastrophic forgetting of general knowledge | Mix domain data with general data (80/20) |

---

## Resource Estimates

| Horizon | Engineering Effort | Infrastructure Cost Delta |
|---------|--------------------|--------------------------|
| Sprint 0 | 1 day | Zero |
| Near (1–2 months) | 2–3 engineers × 6 weeks | +$50–150/month (Redis, Prometheus) |
| Mid (3–6 months) | 3–4 engineers × 3 months | +$200–500/month (larger DB, more API calls) |
| Far (6–12 months) | 4–5 engineers × 6 months | +$1000–3000/month (fine-tuning, GPU inference) |

---

## Success Metrics

| Metric | Phase 1 Baseline | Phase 2 Target |
|--------|-----------------|----------------|
| Automation rate (no human) | Unknown (not measured) | >60% of tickets |
| High-confidence response rate | Unknown | >70% |
| Retrieval recall@5 on gold dataset | Unknown | >85% |
| P95 response latency | ~5–8s | <3s (with streaming) |
| Feedback positive rate | Not yet collected | >80% thumbs-up |
| PII in vector store | Unquantified | Zero new PII (redaction active) |
