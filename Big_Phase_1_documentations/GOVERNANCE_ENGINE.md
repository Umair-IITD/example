# Governance Engine

The governance engine ensures that every AI-generated support response meets safety and quality thresholds before being delivered. It is the last line of defense against hallucination, over-automation, and missed escalations.

## Response Pipeline with Governance

```
Retrieved Chunks
      │
      ▼
Workflow Classification
      │
      ├──▶ exact_match (boosted_score ≥ 0.55)
      │    → Confident, direct response; automation eligible
      │
      ├──▶ related_match (0.35 ≤ boosted_score < 0.55)
      │    → Related procedure found; limitation statement required
      │
      └──▶ weak_match / no_match (boosted_score < 0.35)
           → No usable SOP; requires_human = true
      │
      ▼
Prompt Construction (RESPONSE MODE per match type)
      │
      ▼
LLM Generation
      │
      ▼
Branch Completeness Check
      │
      ▼
Automation Safety Gate
      │
      ▼
GenerationResult
```

## Workflow Classification

Classification uses the **boosted SOP score** (raw cosine similarity + `B1_SOP_SIMILARITY_BOOST` = 0.15 applied in the SQL RPC). This means a SOP chunk with raw cosine 0.43 has a boosted score of 0.58 → classified as `exact_match`.

```python
classification_sim = max(best_sop_boosted_score, best_raw_similarity)

if classification_sim >= WORKFLOW_EXACT_SIMILARITY:    # 0.55
    workflow_match_type = "exact_match"
elif classification_sim >= WORKFLOW_RELATED_SIMILARITY: # 0.35
    workflow_match_type = "related_match"
elif has_knowledge_context:
    workflow_match_type = "related_match"
else:
    workflow_match_type = "weak_match" or "no_match"
```

**`has_knowledge_context`**: True if any retrieved chunk has `source_table == "rag_knowledge_chunks"`. Knowledge articles are treated as `related_match` even without SOP hits, because KB articles represent curated but not procedure-level guidance.

## RESPONSE MODE Prompt Injection

Based on `workflow_match_type`, the prompt builder injects a specific RESPONSE MODE directive:

| Match Type | RESPONSE MODE | Behavior |
|------------|---------------|----------|
| `exact_match` | AUTHORITATIVE | Direct, confident response. No defensive qualifiers. Steps numbered precisely. |
| `related_match` | RELATED_PROCEDURE | Acknowledges the procedure is "related" not exact. Includes limitation statement. |
| `weak_match` | INSUFFICIENT_CONTEXT | Escalates to human. Provides general guidance only. |
| `no_match` | ESCALATION_REQUIRED | Immediate human escalation. No fabricated resolution steps. |

## SOP Branch Mandate

When SOP chunks are retrieved with `exact_match` or `related_match`, the `SopDocumentFlags` are parsed from the combined chunk content. For each True flag, a specific instruction is injected into the prompt:

| Flag | Injected Instruction |
|------|---------------------|
| `has_escalation_branches` | "Address the escalation path explicitly. State clearly when the issue must be escalated." |
| `has_denial_branches` | "Include the 'do not / must not' prohibitions from the SOP. State what agents must NOT do." |
| `has_security_freeze` | "Acknowledge the security freeze scenario. Clarify that front-line cannot resolve this without ops intervention." |
| `has_post_resolution` | "Include the post-resolution checklist. Steps after the unlock must be explicitly listed." |
| `has_mandatory_warnings` | "Include all mandatory warnings verbatim or with equivalent strength." |

This prevents the LLM from producing a condensed, branch-skipping response.

## Branch Completeness Check (`_check_answer_completeness`)

After LLM generation, the answer is checked against the flags that were injected into the prompt:

```python
absent_branches = []
if flags.has_escalation_branches and "escalat" not in answer_lower:
    absent_branches.append("escalation")
if flags.has_denial_branches and not any(kw in answer_lower for kw in ["do not", "must not", "never", "avoid"]):
    absent_branches.append("denial")
# ... similar for other flags

if len(absent_branches) >= 2:
    # Downgrade confidence: high → medium
    confidence = "medium"
    diagnostics["branch_completeness_warnings"] = absent_branches
```

This catches cases where the LLM omitted critical branches despite being instructed to include them.

## Automation Safety Gate

The automation gate applies to decisions about whether a response can be sent without human review:

```python
automation_safe = (
    not result.requires_human
    AND result.confidence == "high"
    AND workflow_match_type == "exact_match"
    AND result.retrieval_confidence == "high"
)
```

All four conditions must be True. A single failure sets `requires_human = True`:
- `requires_human` can be set by: `no_match`, escalation branch presence, SOP prohibitions, LLM refusal
- Confidence `"high"` requires: similarity above threshold AND rerank score above threshold
- `exact_match` requires boosted score ≥ 0.55
- `retrieval_confidence "high"` requires: ≥2 high-similarity chunks retrieved

## Confidence Scoring

```
confidence_score = weighted_average(
    semantic_similarity=0.5,
    rerank_score=0.3,
    workflow_match_quality=0.2
)

if confidence_score >= 0.65:    confidence = "high"
elif confidence_score >= 0.40:  confidence = "medium"
elif confidence_score >= 0.20:  confidence = "low"
else:                           confidence = "none"
```

## Webhook Min-Confidence Gate

The Freshdesk webhook (`/freshdesk/webhook`) has a separate minimum confidence gate controlled by `FRESHDESK_WEBHOOK_MIN_CONFIDENCE`:

- `low`: Reply if any context was found (confidence low or above)
- `medium`: Reply only if confidence is medium or high
- `high`: Reply only if confidence is high (strict automation)

Responses below the configured threshold are queued in `rag_review_queue` for human review.

## Diagnostic Fields

Every `/rag/chat` response includes a `diagnostics` object:

```json
{
  "workflow_match_type": "exact_match",
  "best_sop_boosted_score": 0.742,
  "best_raw_similarity": 0.592,
  "retrieval_confidence": "high",
  "sop_branch_flags": {
    "has_escalation_branches": true,
    "has_denial_branches": true,
    "has_security_freeze": false,
    "has_post_resolution": true,
    "has_mandatory_warnings": false
  },
  "branch_completeness_warnings": [],
  "query_route": "SOP",
  "routing_confidence": 0.92,
  "retrieval_strategy": "SOP_FIRST",
  "index_version": "v2",
  "hybrid_retrieval": true
}
```

`sop_branch_flags` fields are sanitized from the response when `DEBUG_RAG=false` (production default) to prevent SOP internals from being exposed to API callers.
