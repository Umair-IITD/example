# Phase B2 Implementation — Chat Generation Layer

**Date**: 2026-05-18  
**Status**: COMPLETE — endpoint live, validation suite created  
**Engineer**: Lead AI Systems Architect  

---

## 1. Summary

Phase B2 implements the full chat generation layer on top of the Phase B1 retrieval system.
The pipeline is:

```
GenerationRequest (query_text + client)
  └─► TicketRetriever.retrieve()          B1 RAG retrieval (pgvector + SOP boost)
        └─► assemble_context()            token-aware context block
              └─► B1LLMClient.complete_json()  OpenAI gpt-4o-mini, json_object mode
                    └─► GenerationResult       answer + confidence + citations +
                                               requires_human + confidence_score
```

The `/rag/chat` FastAPI endpoint was already wired and live from the initial B2 scaffolding.
This pass upgrades the generation quality with an enterprise prompt, structured response schema,
and a comprehensive validation suite.

---

## 2. Files Modified in B2 Pass

| File | Change |
|---|---|
| `rag_engine/generation/prompt_builder.py` | Full rewrite — enterprise ROLE/TASK/CONTEXT/REASONING/STOP CONDITIONS/OUTPUT anatomy; `requires_human` added to output schema; `B1_SYSTEM_PROMPT` kept as backward-compat alias |
| `rag_engine/generation/context_assembler.py` | Added tiktoken token budgeting (`max_context_tokens=6000`); `total_tokens` and `skipped_chunks` added to `AssembledContext` |
| `rag_engine/generation/chat_generator.py` | `GenerationResult` gains `requires_human: bool` and `confidence_score: float`; helper functions `_derive_requires_human` and `_derive_confidence_score` added; diagnostics extended with `context_tokens` and `context_skipped_chunks` |
| `app/main.py` | `/rag/chat` response dict extended with `requires_human` and `confidence_score`; Freshdesk webhook now gates on `requires_human` before confidence threshold check |
| `scripts/validate_b2_generation.py` | NEW — 20-check validation suite (10 offline, 10 live) |

---

## 3. Enterprise Prompt Architecture

The system prompt follows the mandatory anatomy: **ROLE / TASK / CONTEXT / REASONING / STOP CONDITIONS / OUTPUT**.

```
ROLE
  KwikID Support AI — RAG drafting assistant for human support agents.
  All outputs are internal drafts, reviewed before any customer contact.

TASK
  Given query + retrieved chunks → produce structured JSON:
    answer, confidence, citations, requires_human, follow_up_question

CONTEXT
  Evidence hierarchy:
    1. SOP chunks (AUTHORITATIVE)
    2. RESOLUTION_RCA chunks (proven fix patterns)
    3. QUERY_BODY chunks (context only)
    4. ISSUE_HEADER chunks (metadata only)

REASONING
  Work hierarchy → synthesize → assign confidence → check STOP CONDITIONS
  → cite chunks → set follow_up if gaps remain.

STOP CONDITIONS (set requires_human=true when ANY apply)
  - No chunks retrieved
  - Explicit escalation requested
  - Contradictory chunks on same fact
  - Credentials / compliance / fraud scope
  - Low confidence with no SOP anchor

OUTPUT (strict JSON, 5 keys)
  { "answer", "confidence", "citations", "requires_human", "follow_up_question" }
```

---

## 4. Structured Response Schema

### `/rag/chat` API response

```json
{
  "session_id": "uuid",
  "message_id": "uuid",
  "answer": "string — grounded draft text",
  "confidence": "high | medium | low",
  "confidence_score": 0.87,
  "requires_human": false,
  "citations": [
    {"chunk_num": 1, "chunk_type": "SOP_STEPS", "source_id": "otp_delivery_failure"}
  ],
  "follow_up_question": null,
  "insufficient_context": false,
  "chunks": [...],
  "diagnostics": {
    "returned_count": 8,
    "total_candidates": 24,
    "context_tokens": 1840,
    "context_skipped_chunks": 0,
    "has_sop_context": true,
    "has_rca_context": true,
    "best_similarity": 0.74,
    ...
  }
}
```

### Field definitions

| Field | Type | Description |
|---|---|---|
| `confidence` | `"high"\|"medium"\|"low"` | LLM-assigned categorical confidence, with Python safety clamp |
| `confidence_score` | `float [0.0, 1.0]` | Deterministic numeric score derived from categorical + context signals |
| `requires_human` | `bool` | True = agent must review before any customer action |
| `insufficient_context` | `bool` | True = zero chunks retrieved |
| `context_tokens` | `int` (in diagnostics) | Token count of the assembled context block |
| `context_skipped_chunks` | `int` (in diagnostics) | Chunks dropped due to 6000-token context budget |

---

## 5. Confidence Score Derivation

`confidence_score` is derived deterministically — never asked of the LLM (floats are unreliable from LLMs).

```python
_CONFIDENCE_BASE = {"high": 0.82, "medium": 0.50, "low": 0.18}

score = base[categorical_confidence]
if has_sop_context:    score += 0.05    # SOP is authoritative evidence
if has_rca_context:    score += 0.03    # proven resolution pattern
if chunk_count >= 5:   score += 0.02    # rich retrieval pool
if requires_human:     score = min(score, 0.75)    # cap when human gate is active
if insufficient_context: return 0.10    # hard floor for no retrieval
return clamp(score, 0.05, 0.95)
```

This guarantees: `score("high") > score("medium") > score("low")` for identical context signals.

---

## 6. requires_human Logic

`requires_human` is determined by combining the LLM's own flag with Python-side safety overrides:

```python
def _derive_requires_human(llm_flag, insufficient_context, confidence, has_sop_context):
    if llm_flag is True:           return True    # honor LLM escalation intent
    if insufficient_context:       return True    # zero chunks → always escalate
    if confidence == "low" and not has_sop_context:  return True    # no anchor
    return False
```

Python overrides can only raise `requires_human` to True — they never suppress a True returned by the LLM.

In the Freshdesk webhook path, `requires_human=True` gates before the confidence threshold check, ensuring escalation-flagged responses are never auto-posted.

---

## 7. Token Budget in Context Assembler

The context assembler now enforces a 6,000-token budget using `tiktoken` (`cl100k_base`):

```
Total context window (gpt-4o-mini): ~16,384 tokens
  System prompt (B2_SYSTEM_PROMPT): ~400 tokens
  User prompt wrapper:               ~80 tokens
  Diagnostics JSON:                  ~100 tokens
  Context budget:                    6,000 tokens  ← enforced
  Output budget:                     800 tokens    ← max_output_tokens
  Headroom:                          ~9,004 tokens
```

At the default `top_k=8` with `per_chunk_max_chars=1400`, each chunk is ~350 tokens,
so 8 chunks ≈ 2,800 tokens — well within the 6,000 token budget. The budget is a safety
guard for larger `top_k` values or longer chunk content.

SOPs are assembled first, ensuring they are prioritized when the budget is tight.
`AssembledContext.skipped_chunks` and `AssembledContext.total_tokens` are surfaced in
the API response `diagnostics` block for observability.

---

## 8. Validation Suite

```bash
# Offline unit tests (no DB/LLM required)
python scripts/validate_b2_generation.py

# Full end-to-end tests
python scripts/validate_b2_generation.py --live --client unity_bank

# Write report to data/reports/b2_generation_report.md
python scripts/validate_b2_generation.py --live --report
```

| Check | Mode | What it validates |
|---|---|---|
| G1 | offline | `GenerationResult` has all 11 required B2 fields |
| G2 | offline | `confidence_score` in correct range for each categorical |
| G3 | offline | `high > medium > low` score monotonicity |
| G4 | offline | `insufficient_context=True` → score = 0.10 |
| G5 | offline | `insufficient_context=True` → `requires_human=True` (LLM=False override) |
| G6 | offline | LLM `requires_human=True` honored even with high confidence + SOP |
| G7 | offline | `requires_human=False` when all conditions clear |
| G8 | offline | `B2_SYSTEM_PROMPT` has all 6 anatomy sections |
| G9 | offline | `B2_SYSTEM_PROMPT` includes `requires_human` in OUTPUT schema |
| G10 | offline | `assemble_context([])` returns sentinel, zero stats; full chunk assembly correct |
| G11 | live | Basic generation returns valid `GenerationResult` |
| G12 | live | `requires_human` is Python `bool` |
| G13 | live | `confidence_score` is `float` in `[0.0, 1.0]` |
| G14 | live | `confidence` is valid categorical value |
| G15 | live | Citations are well-formed (chunk_num, chunk_type, source_id) |
| G16 | live | Nonsense query at high threshold → `insufficient_context=True`, `requires_human=True` |
| G17 | live | P95 latency < 30,000ms over 5 diverse queries (embed + search + LLM) |
| G18 | live | Specific query has higher `confidence_score` than vague high-threshold query |
| G19 | live | `session_id` is non-empty string |
| G20 | live | `diagnostics.context_tokens` and `context_skipped_chunks` present and typed |

---

## 9. Remaining Blockers (Unchanged from B1 Audit)

| Blocker | Impact | Fix |
|---|---|---|
| **No API authentication** | Any caller can invoke `/rag/chat`, `/rag/ingest`, `/rag/retrieve` | Add `X-API-Key` middleware to `app/main.py` (~2 hours) |
| **No rate limiting** | Abuse of `/rag/chat` incurs unbounded OpenAI costs | Add `slowapi` `@limiter.limit("20/minute")` to endpoint (~2 hours) |

Neither B2 functionality nor this validation pass addresses these blockers — they are security requirements that must be resolved before public internet exposure.

---

## 10. Phase B3 Entry Plan

B3 (Freshdesk auto-reply) can proceed once:
1. API authentication is implemented (P0 blocker)
2. Rate limiting is implemented (P0 blocker)
3. `FRESHDESK_WEBHOOK_ENABLED=true` is set in production `.env`
4. At least 10 real webhook payloads are tested in shadow mode (generate but do not post)
5. Confidence threshold (`FRESHDESK_WEBHOOK_MIN_CONFIDENCE`) is calibrated against agent feedback
