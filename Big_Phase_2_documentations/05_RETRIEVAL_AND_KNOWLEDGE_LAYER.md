# 05 — Retrieval and Knowledge Layer

## 1. Retrieval as a Support Layer, Not the Control Plane

The Phase 1 RAG engine is production-stable and is not replaced by Phase 2. It is called as a bounded stateless service from within workflow nodes. The relationship is strictly:

```
Workflow Engine  →  calls  →  RAG Service  →  returns SOP-grounded context
```

The RAG service does not decide what the workflow does next. It does not know whether it is being called from step 2 or step 5 of a playbook. It receives a query and a topic scope, retrieves relevant chunks, generates a grounded response, and returns it. The workflow engine decides what to do with that response.

This boundary matters for two reasons:

**Maintainability:** SOP authors update SOP text. If retrieval governed case execution, every SOP text change would be a potential change to business logic. By keeping retrieval as a bounded service, SOP updates affect answer quality only, not execution paths.

**Compliance:** The execution path of a support case must be deterministic and auditable. Retrieval is probabilistic by nature (similarity scores, re-ranking). A retrieval system as the control plane produces non-deterministic execution paths that cannot be audited or reproduced.

---

## 2. Tenant Isolation — ABAC Predicate Pushdown (Level 1 Priority)

This is a security fix, not a feature enhancement. It must be implemented before Level 1 goes to production.

### Current Vulnerability

The current hybrid search (pgvector cosine similarity + BM25 full-text) executes without enforcing tenant predicates at the database scan level. The post-retrieval filter checks tenant ownership after results are returned. The vulnerability: a high-similarity cross-tenant SOP chunk can appear in the top-k results before the post-retrieval filter runs. If the filter has any logic error or race condition, cross-tenant SOP content can appear in a response.

In a multi-bank deployment (Bank A and Bank B sharing the same pgvector instance), this means Bank A's sensitive internal SOPs could appear in Bank B's diagnostic note. This is a compliance violation regardless of whether the note is seen externally.

### Required Fix

ABAC predicates must be applied inside the SQL query, before any row is returned to the application layer:

```sql
CREATE OR REPLACE FUNCTION match_gated_sop_chunks (
  query_embedding    vector(1536),
  match_threshold    float,
  match_count        int,
  filter_tenant_id   uuid,
  filter_requires_internal boolean
)
RETURNS TABLE (
  chunk_id      uuid,
  sop_id        text,
  content       text,
  similarity    float
)
LANGUAGE sql
STABLE
AS $$
  SELECT
    chunks.chunk_id,
    chunks.sop_id,
    chunks.content,
    1 - (chunks.embedding <=> query_embedding) AS similarity
  FROM sop_chunks AS chunks
  WHERE
    chunks.tenant_id = filter_tenant_id
    AND (chunks.requires_internal = FALSE OR filter_requires_internal = TRUE)
    AND (1 - (chunks.embedding <=> query_embedding)) > match_threshold
  ORDER BY similarity DESC
  LIMIT match_count;
$$;
```

The BM25 full-text search function must have equivalent tenant predicates applied in the same manner.

### Why Post-Retrieval Filtering Is Insufficient

Post-retrieval filters run in application code after the database returns results. They are subject to:
- Application bugs that skip the filter
- Race conditions in async execution paths
- Incorrect tenant context propagation through async call chains
- Developer error in future code changes that call the raw retrieval function directly

Predicate pushdown at the database layer is enforced by the database engine regardless of application code correctness.

---

## 3. Retrieval Roles in the Three-Level Model

### Level 1
RAG is called once per case, immediately after topic classification. The classified topic is passed as a retrieval scope parameter. Phase 1 RAG retrieves SOP chunks scoped to the topic, generates a grounded diagnostic payload, and returns it. The payload is posted as a Freshdesk private note. No multi-turn retrieval. No workflow-conditional retrieval.

### Level 2
RAG is called from specific workflow nodes where SOP grounding is needed. Not every workflow step calls RAG — read-only tool calls (get_vkyc_session_status) do not need SOP context. RAG is called when:
- A workflow node needs to generate the human-readable diagnostic step in the Transfer Context Payload
- A workflow node is building the clarification question for AWAITING_INPUT state
- A workflow node needs to identify the correct SOP branch for an error code

Topic-scoped retrieval profile: each topic has a configured retrieval profile (which SOP collections to search, what match_threshold to apply, what max_chunks to return). The profile is static and version-controlled.

### Level 3
Optional Graph RAG for complex multi-hop SOP dependency chains. Described in Section 4 below.

---

## 4. Graph RAG — When It Is and Is Not Justified

### Not Justified When

**The SOP is a linear checklist.** Most OTP delivery failure SOPs are: check channel config → retry on alternate channel → escalate if limit hit. Three linear steps. Flat retrieval handles this correctly. Adding graph structure provides no benefit and requires SOP re-authoring to express the graph.

**The knowledge base is small enough for flat retrieval.** If the SOP collection fits within 10,000 chunks with reasonable chunk quality, pgvector cosine similarity + BM25 hybrid retrieval achieves high NDCG scores. Graph RAG adds latency and infrastructure complexity with no retrieval quality gain.

**Adding graph structure requires extensive SOP re-authoring.** If the current SOPs are written as prose documents, converting them to graph-compatible structured format (entities, causal edges, flow edges) requires significant manual effort. This effort is only justified if retrieval quality is measurably poor.

**Implementation would take 2+ weeks with unclear quality gain.** At Level 1 and early Level 2, engineer time is better spent on: classifier accuracy, ABAC fix, circuit breaker, idempotency — all of which have clear, measurable quality impact.

### Justified Only When

- Multiple SOPs have structural dependencies: SOP A explicitly requires SOP B to be completed as a prerequisite (e.g., Aadhaar XML verification must succeed before VKYC can proceed; VKYC liveliness cannot be retried if Hard Lock is active)
- Complex conditional branching logic cannot be preserved in flat chunks without context loss (e.g., a 40-step VKYC exception chain where early branch decisions affect all later steps)
- Retrieval quality on procedural queries is measurably poor (NDCG@10 < 0.6) despite prompt tuning, chunk size optimization, and metadata filtering
- A standalone Graph RAG evaluation has been conducted (on a held-out test set) and shows statistically significant NDCG improvement over flat retrieval

### Implementation If Justified (Level 3 Option)

SOPRAG-style mixture of experts:
- **Entity expert:** retrieves SOPs relevant to named entities in the query (session IDs, error codes, compliance flags)
- **Causal expert:** retrieves SOPs related to causal chains (bandwidth → liveliness failure → VKYC session timeout)
- **Flow expert:** retrieves procedural steps in dependency order (prerequisite checks before remediation steps)

Fusion layer combines expert outputs using adaptive RRF (already implemented in Phase 1). Graph is built offline from SOP metadata; not queried at runtime for graph traversal (too slow). Instead, graph-derived embeddings and metadata tags are stored in pgvector and retrieved via standard similarity search.

This is a Level 3 research track. It requires: standalone evaluation, dedicated infrastructure, and measurable NDCG improvement before production use. It is not a default Phase 2 component.

---

## 5. Synthetic SOP Generation

When knowledge gaps accumulate — recurring low-confidence retrievals for the same topic over 2+ weeks — a human-supervised SOP generation process is triggered.

### Process

1. **Signal detection:** weekly calibration report (see `09_OPERATIONAL_ANALYTICS_AND_EVALUATION.md`) identifies topics with recurring `no_match` or `weak_match` retrieval outcomes
2. **Aggregate source material:** collect escalated ticket transcripts, human resolution notes, and correction feedback for the affected topic
3. **Offline SOP draft:** LLM-based generator (offline batch job, not production system) produces a structured SOP draft:
   ```
   SOP Title: [topic]_[sub-issue]
   Applicability: [conditions]
   Steps: [numbered list]
   Escalation path: [conditions for human escalation]
   References: [related SOPs]
   ```
4. **Staging:** draft stored in `sop_suggestions` table with status `PENDING_REVIEW`
5. **Human review:** administrator reads the draft, edits for accuracy, compliance, and completeness, then marks `APPROVED` or `REJECTED`
6. **Ingestion:** approved SOP ingested via the existing Phase 1 knowledge pipeline (standard chunking, embedding, indexing)

### What This Process Is Not
- Not automated: human review at step 5 is mandatory; no auto-approve path exists
- Not real-time: runs on weekly cadence, not triggered per ticket
- Not a training feedback loop: the LLM generator uses existing knowledge as context, not production traffic embeddings
- Not a replacement for SOP authors: the generated draft is a starting point, not a final document

### Failure Mode if Skipped
If the SOP generation process is not run when knowledge gaps accumulate: the classifier continues to route cases to `VKYC_Session_Failure` (for example), but retrieval returns `no_match` on every such ticket, and every case escalates to human. The escalation rate for that topic will spike in the weekly KPI report, which is the detection mechanism.
