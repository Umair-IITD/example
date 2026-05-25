# Phase 2D — Enterprise Platform Design

## 1. Critical Evaluation: Do We Need Phase 2D?

Phase 2D is the most ambitious and most risky phase. Before describing what it includes, it must be honestly evaluated:

**Phase 2D is NOT required for:**
- Handling 50–500 tickets per day (Phase 2A-2C handles this)
- Serving 3–10 enterprise clients (single Supabase instance handles this)
- Achieving >70% automation rate (Phase 2A-2B handles this)
- Real-time analytics and governance audit (Phase 2C handles this)

**Phase 2D IS required for:**
- Handling >1000 tickets per day (OpenAI API rate limits become binding)
- Achieving <2s P50 response latency at scale (requires embedding service + async queuing)
- Serving 50+ enterprise clients with different model preferences
- Eliminating OpenAI as a single point of failure
- Fully autonomous support operations without n8n orchestration

**Recommendation**: Phase 2D should not begin until Phase 2A-2C are deployed, validated, and the throughput ceiling is actually being hit in production. Building enterprise infrastructure for traffic that doesn't exist is classic overengineering.

The patterns below are designed for when Phase 2D becomes genuinely necessary.

---

## 2. Multi-Model Provider Abstraction

### 2.1 The Problem

Currently the system depends on:
- OpenAI `text-embedding-3-small` for all embeddings
- OpenAI `gpt-4o-mini` for all generation

This creates:
- Single point of failure (OpenAI outage = service outage)
- Cost exposure (OpenAI pricing changes affect all workloads)
- Rate limit ceiling (org-level RPM cap)
- Vendor lock-in (switching models requires re-ingesting all documents)

### 2.2 Provider Abstraction Interface

```python
class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...
    
    @property
    def dimensions(self) -> int:
        ...
    
    @property  
    def model_id(self) -> str:
        ...

class GenerationProvider(Protocol):
    async def complete(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> GenerationOutput:
        ...
    
    @property
    def cost_per_1k_tokens(self) -> float:
        ...
    
    @property
    def avg_latency_ms(self) -> float:
        ...
```

**Concrete implementations** (Phase 2D):
- `OpenAIEmbeddingProvider` (existing code, wrapped)
- `AzureOpenAIEmbeddingProvider`
- `OllamaEmbeddingProvider` (self-hosted; existing support in Phase 1)
- `OpenAIGenerationProvider` (existing code, wrapped)
- `AzureOpenAIGenerationProvider`
- `AnthropicGenerationProvider` (Claude models)

**Critical constraint**: Switching embedding providers requires re-ingesting all documents with the new provider. The abstraction makes the code provider-neutral, but the data is not. The `ACTIVE_INDEX_VERSION` mechanism handles this: ingest under a new version with the new provider, then flip.

### 2.3 Cost-Aware Routing

```python
class CostAwareRouter:
    def select_generation_provider(
        self,
        intent: IntentResult,
        governance: GovernanceResult,
        request_context: RequestContext,
    ) -> GenerationProvider:
        
        # Simple requests (exact_match SOP, short answer) → use cheaper/faster model
        if (governance.workflow_match_type == "exact_match" 
                and governance.confidence == "high"
                and request_context.estimated_output_tokens < 200):
            return self._providers["fast_cheap"]  # e.g., gpt-4o-mini or claude-haiku
        
        # Complex/ambiguous requests → use more capable model
        if (governance.workflow_match_type in ["weak_match", "no_match"]
                or intent.category == IntentCategory.MULTI_ISSUE):
            return self._providers["capable"]  # e.g., gpt-4o or claude-sonnet
        
        # Default
        return self._providers["default"]  # gpt-4o-mini (current)
```

**Governance constraint on cost routing**: Cost routing must not degrade governance. The cheaper model selection is only applied when the governance engine has already confirmed high confidence. For uncertain cases, always use the more capable model.

### 2.4 Fallback Chain

```python
FALLBACK_CHAIN = [
    "openai_primary",        # Primary: OpenAI gpt-4o-mini
    "azure_openai_backup",   # Backup: Azure OpenAI (same model, different endpoint)
    "anthropic_fallback",    # Last resort: Claude Haiku
]

async def generate_with_fallback(messages, **kwargs) -> GenerationOutput:
    for provider_name in FALLBACK_CHAIN:
        try:
            provider = get_provider(provider_name)
            result = await asyncio.wait_for(
                provider.complete(messages, **kwargs),
                timeout=kwargs.get("timeout_s", 30)
            )
            if provider_name != FALLBACK_CHAIN[0]:
                metrics.increment("generation_fallback_used", labels={"provider": provider_name})
            return result
        except (TimeoutError, ProviderError) as e:
            log.warning(f"Provider {provider_name} failed: {e}. Trying next.")
            continue
    
    # All providers failed — return degraded result
    return GenerationOutput.degraded(reason="all_providers_failed")
```

---

## 3. Event-Driven Orchestration

### 3.1 Current vs. Event-Driven Architecture

**Current (synchronous)**:
```
HTTP Request → Processing → HTTP Response
```
All work happens synchronously within a single request-response cycle.

**Event-driven (Phase 2D)**:
```
HTTP Request → Accept (immediate)
    → Event published to queue
    → Worker consumes event
    → Processing (async, parallel)
    → Result delivered via webhook / polling
```

**When is event-driven necessary?**
- When request processing takes >30 seconds (current: ~10s — not needed yet)
- When the same work must be retried after failure without the client re-sending
- When multiple workers process different stages of the same request in parallel
- When ingestion webhooks need guaranteed delivery and retry

**When is event-driven NOT needed?**
- When Phase 1-2C architecture is handling traffic without queue buildup
- When the current synchronous model handles the ticket volume

**Phase 2D recommendation**: Implement an event queue only for the Freshdesk webhook ingestion path (where guaranteed delivery matters). Keep `/rag/chat` synchronous (latency is more important than guaranteed delivery for interactive queries).

### 3.2 Webhook Queue Design (Targeted Event-Driven)

```
Freshdesk → POST /freshdesk/webhook (FastAPI)
               │
               ├── HMAC verification (sync, fast)
               ├── Deduplication check (Redis, sync, fast)
               └── Enqueue to Redis queue (sync, fast)
               │
               → HTTP 202 Accepted (immediate)

Redis Queue: "freshdesk_events"
    │
    └── Worker process (separate Gunicorn worker or Celery)
           ├── Consume event
           ├── POST /rag/chat (internal)
           ├── POST Freshdesk public reply
           └── Mark event processed (remove from queue)
```

This provides:
- **Guaranteed processing**: If the worker crashes mid-processing, the event is re-queued
- **Deduplication**: Redis set tracks processed ticket IDs (Freshdesk retries webhooks)
- **Backpressure**: Queue depth metric alerts when processing can't keep up

---

## 4. Realtime Systems

### 4.1 Streaming Responses

Phase 2D enables Server-Sent Events (SSE) for `/rag/chat`:

```python
@router.post("/rag/chat/stream")
async def rag_chat_stream(request: ChatRequest, ...):
    async def event_generator():
        # Retrieval is synchronous (fast after optimizations)
        chunks = await retriever.retrieve(request.query_text, request.client)
        context = assembler.assemble(chunks)
        
        # Metadata event (immediate)
        yield f"data: {json.dumps({'type': 'metadata', 'citations': [c.url for c in chunks]})}\n\n"
        
        # Stream generation tokens
        async for token in llm_client.stream(prompt):
            yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
        
        # Final event (confidence, requires_human, etc.)
        yield f"data: {json.dumps({'type': 'done', **governance_result.dict()})}\n\n"
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

**n8n incompatibility**: n8n's HTTP Request node does not natively consume SSE streams. Options:
1. Deploy a lightweight SSE-to-JSON proxy (accumulates stream, returns complete JSON to n8n)
2. n8n fetches from `/rag/chat` (non-streaming) while browser clients use `/rag/chat/stream`
3. Accept that n8n uses non-streaming and streaming is for future web interfaces

Recommendation: Implement streaming for future web/mobile interfaces, keep n8n on non-streaming.

---

## 5. Critical Evaluation of Multi-Agent Systems

### 5.1 The Appeal

Multi-agent architectures (Retrieval Agent → Analysis Agent → Generation Agent → QA Agent) are popular in AI literature and have demonstrated quality improvements for complex tasks.

### 5.2 The Risks for This System

**Against multi-agent for Phase 2D**:

1. **Observability collapse**: In a 4-agent chain, when the final response is wrong, which agent is responsible? The Phase 1 single-pipeline architecture produces a clear audit trail. Multi-agent systems produce a distributed, harder-to-debug trace.

2. **Latency multiplication**: 4 LLM calls instead of 1 means 4× the latency floor. At current P50 of 10s, a 4-agent chain would be 20–30s — unacceptable for interactive support.

3. **Error propagation**: If Agent 2 (Analysis) produces a wrong analysis, Agents 3 and 4 build on that wrong foundation. The final response may be confidently wrong. Phase 1's single LLM call can only be wrong once; multi-agent can compound errors.

4. **Governance fragmentation**: Phase 1 governance happens in one place with clear inputs and outputs. Multi-agent governance requires governing each agent independently — much more complex to reason about and test.

5. **Operational overhead**: Each agent is a separate configurable system (prompt, model, temperature, timeout). Tuning one breaks another. Phase 1 has ~5 tunable parameters; a 4-agent system has ~20.

### 5.3 When Multi-Agent Might Be Justified

A specific, bounded multi-agent pattern IS justified for:

- **Decomposition + parallel retrieval** (Phase 2B MULTI_ISSUE): Split into sub-queries, retrieve in parallel, merge. This is not a general multi-agent pattern — it's a specific optimization for compound queries.
- **QA self-verification** (already in n8n QA Assessment node): One generation call + one evaluation call. This is 2 agents with a clear, bounded scope.
- **SOP draft generation** (auto-SOP pipeline): The generation is isolated, human-reviewed, never auto-deployed. Safe because the output doesn't affect live traffic.

### 5.4 Verdict

Phase 2D should NOT implement a general multi-agent orchestration framework. The specific bounded patterns (query decomposition, QA verification) are sufficient and are already designed in Phase 2B.

If multi-agent is revisited in Phase 2D, it must be:
- Gated by `MULTI_AGENT_ENABLED=false` by default
- Deployed on <5% of traffic initially (canary)
- Fully observable (each agent call logged with latency, input hash, output hash)
- Proven to improve quality on the gold dataset before full rollout

---

## 6. Distributed Workflow Coordination

### 6.1 When Is Distribution Necessary?

Distributed coordination (Celery, Temporal, Kafka) adds significant operational overhead: separate broker deployment, worker monitoring, dead-letter queues, exactly-once semantics.

This overhead is justified only when:
- A single process cannot handle the throughput (>100 RPM sustained)
- Long-running tasks (>60 seconds) would block the event loop
- Tasks must survive service restarts with guaranteed completion

At Phase 2D entry criteria (>1000 tickets/day = ~40 RPM peak), a single 4-worker Gunicorn deployment is likely sufficient.

### 6.2 Pragmatic Approach

Use FastAPI `BackgroundTasks` for:
- Memory writes (post-response, <500ms, can fail gracefully)
- Analytics events (post-response, <100ms)
- Feedback capture (post-response, <200ms)

Use Redis queues for:
- Freshdesk webhook events (guaranteed delivery, retry needed)
- Nightly batch processing jobs (scheduled, not interactive)

Reserve Celery/Temporal for:
- SOP summarization (can take 10–60s per batch, best run asynchronously)
- Gold dataset evaluation (can take minutes, run weekly)
- Memory pruning (runs nightly, long-running)

---

## 7. Enterprise Scaling Architecture (Phase 2D Target State)

```
                     Load Balancer (nginx / AWS ALB)
                            │
          ┌────────────────┬┴───────────────┐
          │                │                │
   FastAPI Pod 1     FastAPI Pod 2    FastAPI Pod 3
   (4 workers)       (4 workers)      (4 workers)
          │                │                │
          └────────────────┴────────────────┘
                           │
          ┌────────────────┴────────────────┐
          │                                 │
      Redis Cluster                    Supabase
      (rate limits,                    (dedicated plan
       sessions, cache)                 or self-hosted PG)
          │
     Redis Queue
          │
   ┌──────┴──────┐
   │             │
Worker 1      Worker 2
(Freshdesk    (Nightly
 webhooks)     batch jobs)
          │
   ┌──────┴──────────────┐
   │                     │
OpenAI API          Azure OpenAI
(primary)           (fallback)
```

**Target throughput**: 100 RPM sustained, 300 RPM burst  
**Target availability**: 99.5% (allows 43.8 hours downtime/year)  
**Target latency**: P50 <3s, P95 <6s (with embedding cache + parallel retrieval)
