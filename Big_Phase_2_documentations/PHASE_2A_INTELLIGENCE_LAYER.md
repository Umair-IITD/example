# Phase 2A — Intelligence Layer Design

## 1. Overview

Phase 2A adds intelligence to the existing Phase 1 retrieval-governance-generation pipeline. It does not replace anything — it enriches the pipeline at well-defined injection points.

The three primary components of Phase 2A are:

1. **IntentClassifier**: Accurately categorizes the support intent before retrieval, enabling targeted retrieval profiles
2. **MemoryRouter**: Loads and manages conversational, customer, and organizational memory
3. **WorkflowPlanner**: Combines intent + memory + governance output to plan the response strategy

**Non-goal**: Phase 2A does not execute actions (Phase 2B), does not build dashboards (Phase 2C), and does not abstract model providers (Phase 2D).

---

## 2. Current Routing Weakness — Root Cause Analysis

The production system exhibits this known failure:

```
Query: "Customer cannot receive OTP on Samsung device"
Result:
  query_route = GENERAL_KNOWLEDGE
  routing_confidence = 0.0
```

This is a fundamental failure. An OTP delivery query is one of the most common and highest-certainty support categories — it should route to `OTP_DELIVERY` with confidence >0.8.

### 2.1 Why It Fails

The current `QueryRouter` in `query_router/` uses a keyword-based taxonomy. The failure modes are:

1. **Keyword sparsity**: "OTP" may not be in the keyword list. "Samsung" is a device keyword, not an issue keyword. The query has high entropy across keyword categories.

2. **No semantic fallback**: When keyword matching fails (score = 0.0), the system defaults to `GENERAL_KNOWLEDGE` rather than attempting semantic classification.

3. **Rigid taxonomy**: The taxonomy does not account for compound queries ("OTP + Samsung device") that span multiple categories.

4. **No confidence floor for semantic routing**: A routing confidence of 0.0 should trigger a fallback path, not a route assignment.

### 2.2 Current Architecture Gaps

```python
# Current behavior (pseudocode):
def route(query: str) -> QueryRoute:
    keyword_matches = match_keywords(query, TAXONOMY)
    if max(keyword_matches.scores) > THRESHOLD:
        return QueryRoute(category=best_match, confidence=score)
    else:
        return QueryRoute(category=GENERAL_KNOWLEDGE, confidence=0.0)
        # ← WRONG: 0.0 confidence should not produce a route decision
```

### 2.3 Impact of Wrong Routing

When `query_route = GENERAL_KNOWLEDGE`:
- Retrieval may not apply SOP priority boost correctly
- The governance engine may not recognize the query as SOP-eligible
- The system may return a `weak_match` response where an `exact_match` SOP exists

---

## 3. IntentClassifier — Redesign

### 3.1 Architecture

The new `IntentClassifier` uses a three-tier approach:

```
Query
  │
  ▼
Tier 1: Keyword/Regex Fast Classifier
  (covers 70% of queries in <5ms)
  │
  ├── HIGH CONFIDENCE (>0.70): return immediately
  │
  └── LOW CONFIDENCE (<0.70): continue to Tier 2
         │
         ▼
    Tier 2: Semantic Similarity Classifier
    (embed query, compare to intent prototype embeddings)
    (covers additional 20% in <100ms including embedding cache)
         │
         ├── HIGH CONFIDENCE (>0.60): return immediately
         │
         └── LOW CONFIDENCE (<0.60): continue to Tier 3
                  │
                  ▼
             Tier 3: LLM Classification (gpt-4o-mini, structured output)
             (covers remaining 10% in ~1s)
             Returns: category, confidence, reasoning
```

**Design principle**: The LLM tier (Tier 3) is only invoked for genuinely ambiguous queries. This keeps P50 classification latency <100ms.

### 3.2 Intent Taxonomy (Revised)

```python
class IntentCategory(str, Enum):
    # High-volume support categories
    OTP_DELIVERY_FAILURE = "otp_delivery_failure"
    OTP_EXPIRY = "otp_expiry"
    LIVENESS_CHECK_STUCK = "liveness_check_stuck"
    LIVENESS_CHECK_FAILED = "liveness_check_failed"
    CAMERA_PERMISSION_DENIED = "camera_permission_denied"
    VKYC_SESSION_EXPIRED = "vkyc_session_expired"
    VKYC_BANDWIDTH_ISSUE = "vkyc_bandwidth_issue"
    KYC_STATUS_QUERY = "kyc_status_query"
    KYC_DOCUMENT_UPLOAD_FAILED = "kyc_document_upload_failed"
    ACCOUNT_ACCESS_LOCKED = "account_access_locked"
    API_INTEGRATION_ISSUE = "api_integration_issue"
    SDK_CRASH = "sdk_crash"
    
    # Meta-categories (routing signals)
    ESCALATION_REQUEST = "escalation_request"          # Customer explicitly wants human
    FEEDBACK_COMPLAINT = "feedback_complaint"          # Complaint about prior response
    MULTI_ISSUE = "multi_issue"                        # Multiple problems in one ticket
    AMBIGUOUS = "ambiguous"                            # Cannot classify confidently
    OUT_OF_SCOPE = "out_of_scope"                      # Not a KwikID support issue
```

### 3.3 Keyword/Regex Fast Classifier (Tier 1)

```python
INTENT_PATTERNS: dict[IntentCategory, list[re.Pattern]] = {
    IntentCategory.OTP_DELIVERY_FAILURE: [
        re.compile(r"\botp\b.{0,30}\b(not|didn't|doesn't|never)\b.{0,20}\b(receiv|arriv|come|deliver)", re.I),
        re.compile(r"\b(receiv|arriv|get|got|send|sent)\b.{0,20}\botp\b.{0,15}\b(not|no|never|fail)", re.I),
        re.compile(r"\botp\b.{0,30}\b(missing|absent|pending)\b", re.I),
    ],
    IntentCategory.LIVENESS_CHECK_STUCK: [
        re.compile(r"\bliveness\b.{0,30}\b(stuck|hang|loop|fail|not work|won't|wont|can't|cant)\b", re.I),
        re.compile(r"\b(face|facial)\b.{0,20}\b(verify|check|scan|detect)\b.{0,20}\b(fail|stuck|loop)\b", re.I),
    ],
    IntentCategory.CAMERA_PERMISSION_DENIED: [
        re.compile(r"\bcamera\b.{0,30}\b(permission|access|allow|blocked|denied|disable)\b", re.I),
        re.compile(r"\b(cannot|can't|won't|wont)\b.{0,20}\b(open|access|use)\b.{0,10}\bcamera\b", re.I),
    ],
    # ... (full taxonomy: ~15 categories × 2–4 patterns each)
}

def tier1_classify(query: str) -> tuple[IntentCategory | None, float]:
    scores: dict[IntentCategory, int] = defaultdict(int)
    for category, patterns in INTENT_PATTERNS.items():
        for pattern in patterns:
            if pattern.search(query):
                scores[category] += 1
    
    if not scores:
        return None, 0.0
    
    best = max(scores, key=scores.__getitem__)
    match_count = scores[best]
    total_patterns = len(INTENT_PATTERNS[best])
    confidence = min(0.5 + (match_count / total_patterns) * 0.5, 0.95)
    return best, confidence
```

### 3.4 Semantic Classifier (Tier 2)

Pre-compute prototype embeddings for each intent category from canonical example queries. Store in a small in-memory dict at startup:

```python
# intent_prototypes.py — generated once, stored as JSON
INTENT_PROTOTYPES = {
    "otp_delivery_failure": [
        "Customer not receiving OTP",
        "OTP not delivered to phone",
        "SMS OTP not arriving",
        "One-time password not received",
        "Verification code not coming"
    ],
    # 5 canonical examples per category
}

class SemanticIntentClassifier:
    def __init__(self):
        # Load pre-computed embeddings from file at startup
        self._prototype_embeddings: dict[str, np.ndarray] = load_prototype_embeddings()
    
    def classify(self, query_embedding: list[float]) -> tuple[str, float]:
        q = np.array(query_embedding)
        best_cat, best_score = "ambiguous", 0.0
        for category, prototype_embs in self._prototype_embeddings.items():
            # Average cosine similarity to all prototypes for this category
            sims = [cosine_similarity(q, p) for p in prototype_embs]
            score = np.mean(sorted(sims, reverse=True)[:3])  # top-3 average
            if score > best_score:
                best_score, best_cat = score, category
        return best_cat, float(best_score)
```

**Startup cost**: Load prototype embeddings from `data/intent_prototypes.json` once at app startup (<10ms). If file is missing, skip Tier 2 and go directly to Tier 3.

**Critical note**: Prototype embeddings must be generated with the same model as query embeddings (`text-embedding-3-small`). If the embedding model changes, prototypes must be regenerated.

### 3.5 Retrieval Profiles

Intent classification produces not just a category but a **retrieval profile** — a set of retrieval parameters that optimize for this intent type:

```python
RETRIEVAL_PROFILES: dict[IntentCategory, RetrievalProfile] = {
    IntentCategory.OTP_DELIVERY_FAILURE: RetrievalProfile(
        sop_priority_boost=0.20,     # Extra boost beyond normal +0.15
        fts_weight=1.3,              # OTP queries benefit from exact keyword matching
        semantic_top_k=15,
        fts_top_k=15,
        min_similarity=0.20,
    ),
    IntentCategory.LIVENESS_CHECK_STUCK: RetrievalProfile(
        sop_priority_boost=0.15,     # Standard SOP boost
        fts_weight=1.0,
        semantic_top_k=20,           # More candidates — liveness issues are nuanced
        fts_top_k=20,
        min_similarity=0.18,         # Slightly lower threshold — edge cases matter
    ),
    IntentCategory.AMBIGUOUS: RetrievalProfile(
        sop_priority_boost=0.15,
        fts_weight=1.0,
        semantic_top_k=25,           # Broader retrieval for unclear queries
        fts_top_k=25,
        min_similarity=0.15,
    ),
}
```

---

## 4. WorkflowPlanner

The `WorkflowPlanner` synthesizes intent, memory context, and initial governance signals into a **plan** that guides response strategy:

```python
@dataclass(frozen=True)
class ResponsePlan:
    intent: IntentCategory
    retrieval_profile: RetrievalProfile
    memory_context: MemoryContext | None
    prior_resolution_exists: bool        # Has this customer seen this issue before?
    prior_resolution_worked: bool        # Did prior resolution succeed?
    suggest_escalation_check: bool       # Memory signals prior escalation needed
    inject_device_context: bool          # Should device-specific steps be prioritized?
    response_tone: str                   # "empathetic_repeat" | "standard" | "urgent"
```

**Planning logic**:

```python
def plan(intent: IntentCategory, memory: MemoryContext) -> ResponsePlan:
    prior_resolution = find_prior_resolution(intent, memory)
    
    suggest_escalation = (
        memory.escalation_count > 0
        and intent in memory.recurring_issues
    )
    
    response_tone = "standard"
    if intent in memory.recurring_issues and len(memory.recurring_issues) > 2:
        response_tone = "empathetic_repeat"  # Customer has had this problem before
    
    return ResponsePlan(
        intent=intent,
        retrieval_profile=RETRIEVAL_PROFILES[intent],
        memory_context=memory,
        prior_resolution_exists=prior_resolution is not None,
        prior_resolution_worked=prior_resolution.worked if prior_resolution else False,
        suggest_escalation_check=suggest_escalation,
        inject_device_context=bool(memory.known_device),
        response_tone=response_tone,
    )
```

---

## 5. Retrieval Quality Analytics

Phase 2A adds a `retrieval_quality_log` table to track retrieval performance per request:

```sql
CREATE TABLE retrieval_quality_log (
    log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id TEXT NOT NULL,
    session_id TEXT,
    client TEXT NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    intent_category TEXT,
    intent_confidence FLOAT,
    query_hash TEXT,           -- SHA256 of normalized query (not full query — PII)
    top_similarity FLOAT,
    chunk_count INTEGER,
    sop_chunk_count INTEGER,
    retrieval_latency_ms FLOAT,
    workflow_match_type TEXT,  -- exact_match / related_match / weak_match / no_match
    confidence TEXT,           -- high / medium / low
    requires_human BOOLEAN,
    automation_safe BOOLEAN,
    retrieval_profile_used TEXT,
    cache_hit BOOLEAN
);
```

This table enables the analytics in Phase 2C: "What percentage of `OTP_DELIVERY_FAILURE` intents result in `exact_match`?". If it's <80%, the SOP coverage is inadequate.

---

## 6. Confidence Calibration Framework

Phase 1 confidence levels (`high`, `medium`, `low`) are categorical thresholds. Phase 2A introduces a calibration mechanism to detect when these thresholds are miscalibrated.

### 6.1 Calibration Signal

After every agent feedback event (thumbs up/down), record:
- The `confidence` level assigned
- The `feedback` received
- The `intent_category`

Over 100+ samples, compute calibration curves:
- "When confidence=high, what fraction of responses get thumbs-up?"
- Target: >85% thumbs-up for high confidence

### 6.2 Threshold Adjustment

If calibration shows systematic over/under-confidence, adjust thresholds in `.env`:

```
WORKFLOW_EXACT_SIMILARITY=0.55   # Current
# If exact_match responses get <70% thumbs-up → raise to 0.60
# If exact_match responses get >95% thumbs-up → lower to 0.50
```

The calibration framework provides the data to make this decision evidence-based, not intuition-based.

### 6.3 Per-Intent Calibration

Some intent categories may require different thresholds. `LIVENESS_CHECK_STUCK` may have more variability in SOP applicability than `OTP_DELIVERY_FAILURE`. Phase 2A collects the data; Phase 2B/2C enables per-intent threshold tuning.

---

## 7. SessionManager Design

```python
class SessionManager:
    def __init__(self, redis: Redis, supabase: Client, config: Config):
        self._redis = redis
        self._db = supabase
        self._ttl = config.session_ttl_s  # default: 1800
    
    async def resolve(
        self,
        session_id: str,
        client: str,
        customer_id: str | None = None
    ) -> SessionContext:
        cache_key = f"session:{session_id}:{client}"
        
        # 1. Try Redis cache
        cached = await asyncio.to_thread(self._redis.get, cache_key)
        if cached:
            return SessionContext(**json.loads(cached))
        
        # 2. Load from Supabase
        context = await self._load_from_db(session_id, client, customer_id)
        
        # 3. Cache result
        await asyncio.to_thread(
            self._redis.setex, cache_key, self._ttl, json.dumps(context.dict())
        )
        return context
    
    async def _load_from_db(self, session_id, client, customer_id) -> SessionContext:
        # Fetch recent episodes, customer profile, org context in parallel
        episodes_task = asyncio.to_thread(
            self._db.table("memory_episodes")
            .select("*")
            .eq("session_id", session_id)
            .eq("client", client)
            .order("timestamp", desc=True)
            .limit(6)
            .execute
        )
        
        profile_task = None
        if customer_id:
            profile_task = asyncio.to_thread(
                self._db.table("customer_profiles")
                .select("*")
                .eq("customer_id", customer_id)
                .eq("client", client)
                .single()
                .execute
            )
        
        results = await asyncio.gather(episodes_task, profile_task, return_exceptions=True)
        # ... build SessionContext from results
```

---

## 8. Phase 2A Integration Points

Phase 2A connects to Phase 1 at exactly three points:

### Point A: Pre-retrieval (intent + memory enrichment)

```python
# In app/routers/rag.py, at the start of the /rag/chat handler:
session_context = await session_manager.resolve(session_id, client, customer_id)
intent_result = await intent_classifier.classify(query_text, session_context)
response_plan = workflow_planner.plan(intent_result, session_context)

# Pass retrieval_profile to retriever
chunks = await retriever.retrieve(
    query_text=query_text,
    client=client,
    profile=response_plan.retrieval_profile,  # NEW: intent-driven profile
)
```

### Point B: Context assembly (memory injection)

```python
# In rag_engine/generation/context_assembler.py:
context_block = assembler.assemble(
    chunks=chunks,
    memory_context=response_plan.memory_context,  # NEW: inject summary
    response_plan=response_plan,                  # NEW: inject tone/device hints
)
```

### Point C: Post-response (memory write + analytics)

```python
# In app/routers/rag.py, after response is built:
background_tasks.add_task(
    memory_writer.write, session_id, client, request, response
)
background_tasks.add_task(
    retrieval_quality_log.record, request_id, intent_result, response
)
```

**Invariant**: All three integration points are optional. If `MEMORY_ENABLED=false` or `INTENT_CLASSIFICATION_ENABLED=false`, these lines are no-ops and Phase 1 behavior is identical.
