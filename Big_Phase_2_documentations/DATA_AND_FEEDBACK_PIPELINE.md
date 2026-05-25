# Data and Feedback Pipeline — Big Phase 2

## 1. Why Feedback Is the Core Asset

The system's value compounds over time only if it learns from its mistakes. Without a feedback pipeline:
- Miscalibrated thresholds stay miscalibrated
- Poorly performing SOPs stay in the knowledge base
- Retrieval gaps accumulate silently
- Agent effort on corrections is wasted

With a feedback pipeline:
- Every thumbs-down becomes a training signal
- Every escalation reveals a retrieval gap
- Every correction improves future responses
- The system converges toward higher automation rates

Phase 2's feedback pipeline is designed to be **low-friction for agents** (two clicks to provide feedback), **high-fidelity for the system** (structured, actionable signals), and **governed at every step** (no automatic training from raw feedback).

---

## 2. Feedback Capture

### 2.1 Signal Types

```python
class FeedbackSignal(str, Enum):
    THUMBS_UP = "thumbs_up"           # Agent confirms response is correct
    THUMBS_DOWN = "thumbs_down"       # Agent says response is incorrect/incomplete
    CORRECTION = "correction"         # Agent provides the correct answer
    ESCALATION_CONFIRMED = "escalation_confirmed"  # Agent confirms requires_human was right
    ESCALATION_OVERRIDDEN = "escalation_overridden"  # Agent says AI could have handled it
    SOP_MISSING = "sop_missing"       # Agent flags that no SOP exists for this issue
    SOP_WRONG = "sop_wrong"           # Agent flags that the wrong SOP was used
    SOP_OUTDATED = "sop_outdated"     # Agent flags the SOP is no longer accurate
```

### 2.2 Feedback Table

```sql
CREATE TABLE response_feedback (
    feedback_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id TEXT NOT NULL,
    session_id TEXT,
    client TEXT NOT NULL,
    ticket_id TEXT,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    
    -- Signal
    signal FeedbackSignal NOT NULL,
    agent_email TEXT,
    agent_note TEXT,
    correction_text TEXT,        -- For CORRECTION signal: the correct answer
    
    -- Context at time of response (snapshot)
    intent_category TEXT,
    workflow_match_type TEXT,
    confidence TEXT,
    confidence_score FLOAT,
    top_similarity FLOAT,
    requires_human BOOLEAN,
    automation_safe BOOLEAN,
    chunk_ids_retrieved TEXT[],  -- Which chunks were used
    sop_ids_used TEXT[],
    
    -- Processing state
    processed BOOLEAN DEFAULT FALSE,  -- Has this been included in analytics?
    review_status TEXT DEFAULT 'pending',  -- pending / reviewed / included / rejected
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ
);

CREATE INDEX ON response_feedback(client, timestamp DESC);
CREATE INDEX ON response_feedback(processed, signal);
CREATE INDEX ON response_feedback(ticket_id);
```

### 2.3 Feedback API

```
POST /feedback
Body: {
    request_id: "...",
    signal: "thumbs_down",
    agent_note: "The SOP says to check Infobip but this response skipped that step",
    correction_text: null  # Optional: full correction
}
Auth: X-API-Key

POST /feedback/correction
Body: {
    request_id: "...",
    signal: "correction",
    correction_text: "The correct steps are: 1. Check Infobip gateway... 2. Verify phone number format..."
}
```

### 2.4 Feedback Capture in n8n

The n8n workflow gains two new nodes after "Freshdesk Public Reply":
- **Feedback Button Node**: Posts a Freshdesk private note with two links: "👍 Mark as Correct" / "👎 Mark as Incorrect" — each is a webhook call to `/feedback`
- **Agent Review Node**: If agent clicks 👎, opens a Telegram inline keyboard for quick correction or category selection

This keeps feedback within the agent's existing workflow (Freshdesk + Telegram) — no new interface needed.

---

## 3. Feedback Processing Pipeline

### 3.1 Real-Time Processing (Immediate)

When feedback is received:

```python
async def process_feedback_immediately(feedback: Feedback):
    # 1. Update chunk feedback weights (for thumbs_up/down signals)
    if feedback.signal in [FeedbackSignal.THUMBS_UP, FeedbackSignal.THUMBS_DOWN]:
        for chunk_id in feedback.chunk_ids_retrieved:
            delta = 1 if feedback.signal == FeedbackSignal.THUMBS_UP else -1
            await update_chunk_feedback_weights(
                chunk_id=chunk_id,
                client=feedback.client,
                intent_category=feedback.intent_category,
                delta=delta,
            )
    
    # 2. Update memory episode (mark resolution as confirmed/failed)
    if feedback.session_id:
        await update_episode_feedback(feedback.session_id, feedback.signal)
    
    # 3. Flag for review if THUMBS_DOWN + correction provided
    if feedback.signal == FeedbackSignal.THUMBS_DOWN and feedback.correction_text:
        await enqueue_for_review(feedback)
    
    # 4. Alert if SOP_MISSING or SOP_OUTDATED
    if feedback.signal in [FeedbackSignal.SOP_MISSING, FeedbackSignal.SOP_OUTDATED]:
        await notify_sop_team(feedback)
```

### 3.2 Batch Processing (Daily)

A scheduled job runs nightly:

```python
async def run_nightly_feedback_processing():
    # 1. Recompute calibration metrics
    await calibration_analyzer.compute(window_days=30)
    
    # 2. Update retrieval quality summaries
    await retrieval_quality_analyzer.summarize_by_intent()
    
    # 3. Identify SOP coverage gaps (no-match rate > threshold)
    gaps = await gap_detector.find_gaps(threshold=0.20, window_days=7)
    for gap in gaps:
        await sop_suggestion_engine.trigger(gap)
    
    # 4. Update organizational memory (tenant_context table)
    for client in await get_active_clients():
        await org_memory_updater.refresh(client)
    
    # 5. Generate weekly quality report (if day_of_week == Monday)
    if datetime.utcnow().weekday() == 0:
        await quality_report_generator.generate_and_send()
```

---

## 4. Human Review Loop

### 4.1 Review Queue

All THUMBS_DOWN responses with corrections are queued for human review before any learning happens:

```
Agent marks response as 👎 + provides correction
    │
    ▼
response_feedback record created (review_status='pending')
    │
    ▼
Telegram notification to support lead:
"New correction available for review
 Category: otp_delivery_failure
 AI Response: [truncated]
 Correction: [truncated]
 Review: /review {feedback_id}"
    │
    ▼
Support lead reviews in admin panel or Telegram
    │
    ├── APPROVE: review_status='included' → added to training queue
    │   (the correction is considered a valid ground truth)
    │
    └── REJECT: review_status='rejected' → archived, not used for training
        (agent correction was itself incorrect, or out of scope)
```

**Why human review before learning?**: Agents can make mistakes. A correction that contradicts an existing SOP would be harmful if trained on directly. Human review catches:
- Corrections that are themselves incorrect
- Corrections that reflect a customer misunderstanding, not a system failure
- Corrections that should trigger SOP updates, not training data updates

### 4.2 Annotation Pipeline

After review, approved corrections form the annotation dataset:

```jsonl
{
  "query": "I cannot receive OTP on my Samsung A52",
  "correct_answer": "Please check: 1. Is your phone in DND mode? 2. Have you contacted your carrier to verify SMS delivery? 3. Check Infobip gateway configuration in admin panel...",
  "relevant_sop_ids": ["sop_otp_delivery_v2.md"],
  "intent_category": "otp_delivery_failure",
  "confidence_should_be": "high",
  "feedback_id": "fb_abc123",
  "reviewed_by": "agent@kwikid.com",
  "reviewed_at": "2026-05-22T14:00:00Z"
}
```

These annotations are stored in `evaluation/annotated_corrections.jsonl` and used for:
1. Gold dataset expansion (add to `evaluation/gold_dataset.json`)
2. Retrieval quality evaluation (did the system retrieve `sop_otp_delivery_v2.md`?)
3. Future fine-tuning training data (Phase 2D/Far)

---

## 5. Retrieval Quality Analytics

### 5.1 Per-Query Metrics (collected in real-time)

Already designed in `PHASE_2A_INTELLIGENCE_LAYER.md` (`retrieval_quality_log` table). This section covers analytics built on top.

### 5.2 Retrieval Quality Dashboard Queries

```sql
-- Automation rate by intent category (last 7 days)
SELECT 
    intent_category,
    COUNT(*) as total_requests,
    SUM(CASE WHEN automation_safe THEN 1 ELSE 0 END) as auto_resolved,
    ROUND(AVG(confidence_score)::numeric, 3) as avg_confidence,
    ROUND(AVG(top_similarity)::numeric, 3) as avg_similarity
FROM retrieval_quality_log
WHERE timestamp > NOW() - INTERVAL '7 days'
  AND client = $1
GROUP BY intent_category
ORDER BY total_requests DESC;

-- Retrieval failure rate (no_match or weak_match)
SELECT 
    DATE_TRUNC('day', timestamp) as day,
    COUNT(*) as total,
    SUM(CASE WHEN workflow_match_type IN ('no_match', 'weak_match') THEN 1 ELSE 0 END) as failures
FROM retrieval_quality_log
WHERE client = $1
GROUP BY 1
ORDER BY 1 DESC
LIMIT 30;

-- Feedback correlation: does high confidence predict thumbs-up?
SELECT 
    r.confidence,
    COUNT(f.feedback_id) as feedback_count,
    SUM(CASE WHEN f.signal = 'thumbs_up' THEN 1 ELSE 0 END) as positive,
    SUM(CASE WHEN f.signal = 'thumbs_down' THEN 1 ELSE 0 END) as negative
FROM retrieval_quality_log r
JOIN response_feedback f ON r.request_id = f.request_id
WHERE r.client = $1
GROUP BY r.confidence;
```

### 5.3 Calibration Report Structure

Generated weekly by `nightly_feedback_processing`:

```json
{
  "report_date": "2026-05-25",
  "client": "unity_bank",
  "window_days": 30,
  "total_requests": 487,
  "automation_rate": 0.71,
  "feedback_coverage": 0.34,    // 34% of requests received feedback
  "calibration": {
    "high_confidence": {
      "count": 298,
      "thumbs_up_rate": 0.88,
      "target": 0.85,
      "calibration_status": "GOOD"
    },
    "medium_confidence": {
      "count": 121,
      "thumbs_up_rate": 0.62,
      "target": 0.65,
      "calibration_status": "SLIGHTLY_LOW"
    },
    "low_confidence": {
      "count": 68,
      "thumbs_up_rate": 0.31,
      "target": 0.40,
      "calibration_status": "UNDER_PERFORMING"
    }
  },
  "sop_gaps_detected": [
    {"intent_category": "sdk_crash", "no_match_rate": 0.45, "ticket_count": 22}
  ],
  "recommended_actions": [
    "Create SOP for sdk_crash category (22 unresolved tickets in 30 days)",
    "Review low_confidence calibration — thumbs-up rate is 31% vs 40% target"
  ]
}
```

---

## 6. Response Evaluation Framework

### 6.1 Automated Evaluation (No Human Required)

For every response, compute offline evaluation metrics:

```python
class ResponseEvaluator:
    def evaluate(
        self,
        query: str,
        response: GenerationResult,
        chunks: list[Chunk],
    ) -> EvaluationMetrics:
        return EvaluationMetrics(
            # Retrieval metrics
            retrieval_coverage=self._compute_retrieval_coverage(query, chunks),
            sop_chunk_ratio=len([c for c in chunks if c.source_type == 'sop']) / max(len(chunks), 1),
            top_similarity=max(c.similarity for c in chunks) if chunks else 0.0,
            
            # Response metrics
            response_length_words=len(response.answer.split()),
            cites_sources=len(response.citations) > 0,
            contains_step_numbers=bool(re.search(r'\bStep \d+', response.answer)),
            contains_escalation_language=bool(re.search(
                r'(escalat|contact|reach out|human|agent|team)', response.answer, re.I
            )),
            
            # Agreement metrics
            agreement_score=self._check_answer_context_agreement(response.answer, chunks),
        )
```

These metrics are stored per-request and aggregated in the quality dashboard.

### 6.2 Gold Dataset Evaluation

Run against the gold dataset weekly:

```python
def evaluate_against_gold_dataset(gold_dataset: list[GoldItem]) -> GoldEvalResults:
    results = []
    for item in gold_dataset:
        response = call_rag_chat(item.query, item.client)
        results.append(GoldItemResult(
            id=item.id,
            retrieval_recall=item.expected_citation in [c.source for c in response.chunks],
            theme_coverage=_check_themes(item.expected_themes, response.answer),
            confidence_match=response.confidence == item.expected_confidence,
            escalation_match=response.requires_human == (not item.should_not_escalate),
        ))
    
    return GoldEvalResults(
        retrieval_recall=mean(r.retrieval_recall for r in results),
        theme_coverage=mean(r.theme_coverage for r in results),
        confidence_accuracy=mean(r.confidence_match for r in results),
        escalation_accuracy=mean(r.escalation_match for r in results),
    )
```

A CI step (Phase 2A) runs this weekly and fails if any metric drops >5% from the previous week's baseline.

---

## 7. Learning Dataset Management

### 7.1 Dataset Files

```
evaluation/
├── gold_dataset.json          # Ground truth (maintained manually)
├── annotated_corrections.jsonl  # Agent-reviewed corrections (auto-grown)
├── retrieval_negatives.jsonl    # Examples where wrong chunks were retrieved
└── calibration_history.json    # Weekly calibration snapshots
```

### 7.2 Data Governance

All training data must satisfy:
- **PII-free**: No customer names, emails, phone numbers (automated PII scan before inclusion)
- **Reviewed**: Every entry in `annotated_corrections.jsonl` has `reviewed_by` and `reviewed_at`
- **Versioned**: Each dataset file is tagged with `data_version` in the header
- **Auditable**: Every entry traces to a `feedback_id` and original `request_id`

### 7.3 What Data Is NOT Used for Learning

- Raw ticket content (PII risk)
- Unreviewed agent corrections
- Feedback from requests where `client` is a test/development tenant
- Feedback where the agent note is empty (signal is too weak without context)
- Any data from production incidents (may contain anomalous patterns)
