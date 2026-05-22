# Enterprise AI Support Automation Architecture
## Architectural Engineering Analysis and Production Recommendations for the KwikID AI Support Automation Platform

> **Context:** KwikID is a product of Think360.ai operating in the fintech and KYC (Know Your Customer) domain. This document provides a detailed engineering analysis and final architectural recommendations to evolve KwikID from a prototype into a robust, scalable, and self-improving support automation platform.

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Current System Analysis](#current-system-analysis)
3. [Root Causes of Existing Problems](#root-causes-of-existing-problems)
4. [Evaluation of Proposed Workflow](#evaluation-of-proposed-workflow)
5. [Architectural Comparison](#architectural-comparison)
6. [Recommended Final Architecture](#recommended-final-architecture)
7. [Recommended Retrieval System](#recommended-retrieval-system)
8. [Recommended Query Routing Strategy](#recommended-query-routing-strategy)
9. [Recommended RAG Architecture](#recommended-rag-architecture)
10. [Recommended Orchestration Strategy](#recommended-orchestration-strategy)
11. [Recommended Learning Strategy](#recommended-learning-strategy)
12. [Recommended Evaluation Framework](#recommended-evaluation-framework)
13. [Recommended Observability Stack](#recommended-observability-stack)
14. [Recommended Security Architecture](#recommended-security-architecture)
15. [Scalability Analysis](#scalability-analysis)
16. [Reliability Analysis](#reliability-analysis)
17. [Maintainability Analysis](#maintainability-analysis)
18. [Cost Optimization Strategy](#cost-optimization-strategy)
19. [Production Risks](#production-risks)
20. [Final Technical Recommendations](#final-technical-recommendations)
21. [Implementation Roadmap](#implementation-roadmap)
22. [Engineering Priorities](#engineering-priorities)
23. [Works Cited](#works-cited)

---

## Executive Summary

The modernization of the KwikID support platform necessitates a strategic shift from a distributed, "glue-code" architecture to a **centralized, code-first intelligence layer**. The existing fragmentation of logic across n8n workflows, FastAPI services, and disparate prompt repositories has introduced significant maintenance debt and observability gaps.

**Three pillars of transformation:**

1. **Orchestration Consolidation** — Migrate to a durable framework (LangGraph or Temporal) for state persistence and error recovery required for complex, multi-turn fintech inquiries.
2. **Retrieval Engineering** — Move beyond naive vector search to a hybrid retrieval system (BM25 + pgvector) integrated within PostgreSQL/Supabase, with an intent-based routing strategy directing 70% of routine queries toward a "fast-path" semantic router.
3. **Resolution Learning Loop** — Leverage historical Freshdesk ticket data to continuously refine the knowledge corpus, with zero-trust ingestion and row-level security (RLS) for tenant isolation and PII protection.

---

## Current System Analysis

The current KwikID architecture prioritized speed-to-market over long-term structural integrity:

- **Freshdesk** → triggers asynchronous **n8n workflows** → orchestrates **FastAPI service** + **Supabase vector store** + **OpenAI generative models**

### Critical Failure Modes

**1. The Distributed Logic Trap**
Significant portions of system intelligence (e.g., ticket handling rules, escalation logic) are defined within the n8n visual canvas. n8n lacks the versioning, unit testing, and type safety required for core business logic in a regulated environment. The FastAPI layer acts as a "dumb" retrieval pipe while the system's "brain" is scattered across a low-code platform that is difficult to monitor and debug at scale.

**2. Retrieval Inadequacy**
The data layer uses a standard pgvector implementation for semantic search. In fintech, where customers query specific document IDs, regulatory acronyms (e.g., "AML/CFT"), or error strings, pure semantic similarity often fails. Semantic search finds *meaning*, but support tickets often require finding *exact technical matches*. The absence of a robust reranking stage means the LLM is frequently provided with "plausible" but factually irrelevant context.

---

## Root Causes of Existing Problems

| **Symptom** | **Root Cause** | **Engineering Impact** |
|---|---|---|
| Fragmented Intelligence | Logic split between n8n and FastAPI | Inconsistent state management; impossible to perform global rollbacks |
| Retrieval Instability | Pure Vector Search (Dense only) | Poor handling of technical symbols, acronyms, and error codes |
| Observability Blind Spots | No centralized tracing across nodes | MTTR increases as failures are hard to isolate |
| Accuracy Variance | Lack of Grounding Verification | Hallucinations only caught after delivery to end-user |
| Maintenance Overhead | Manual knowledge card updates | Corpus lags behind product updates, leading to stale responses |

> **Key Gap:** Changes to prompts or retrieval parameters are currently evaluated through "vibe checks." In production AI, every change must be benchmarked against a ground-truth dataset using metrics such as faithfulness and answer relevancy.

---

## Evaluation of Proposed Workflow

The proposed new workflow introduces query classification and hybrid retrieval — a step in the right direction. However, three areas require refinement:

**1. Classification Strategy**
- Current proposal: "hard routing" (force query into single category) — brittle for multi-faceted queries.
- Better approach: **Soft routing / multi-label classification** — allows the agent to draw from multiple specialized tools simultaneously.

**2. Durable State for Escalations**
The "confidence low" path (sending acknowledgment + creating Asana ticket) requires durable state management. Without a persistent state store (e.g., LangGraph's checkpointer), an Asana escalation may lose connection to the original Freshdesk thread if the workflow terminates.

**3. Governed Continuous Learning**
Direct, unmediated ingestion of ticket data into the RAG corpus can introduce "noise" and "context drift." The learning loop must include a **Human-in-the-Loop (HITL)** verification step where high-quality resolutions are curated into structured "knowledge cards" before being indexed.

---

## Architectural Comparison

### Orchestration: n8n vs. LangGraph vs. Temporal

| **Feature** | **n8n** | **LangGraph** | **Temporal** |
|---|---|---|---|
| Logic Representation | Visual Nodes | Code-defined Graph | Code-defined Workflow |
| State Persistence | Database Records | Built-in State Snapshots | Event History Replay |
| Error Handling | Node Retries | Edge Logic / Persistence | Global Retry Policies |
| AI Integration | Community Nodes | Native LangChain/Python | Framework Agnostic |
| Observability | Visual Logs | LangSmith / Phoenix | Temporal Web UI |

**Recommendation:** Use **LangGraph** for orchestrating the AI "brain" (code-first, advanced state management for agentic workflows). Transition **n8n** to a supporting role handling simple, stateless post-process integrations (e.g., Slack notifications, dashboard updates).

### Service Boundaries: Monolithic vs. Modular

**Recommended: Modular Monolith** for the FastAPI service — centralize all AI logic (retrieval, routing, generation) into a single service with clear internal boundaries between:
- **Ingestion** module
- **Reasoning** module
- **Verification** module

This eliminates "logic leak" between systems and simplifies the CI/CD pipeline.

---

## Recommended Final Architecture

The recommended architecture is a **"Durable Agentic RAG"** system with four layers:

### 1. Gateway and Ingestion Layer
- FastAPI backend acts as secure entry point
- On receiving a Freshdesk webhook, immediately pushes event to a **task queue (Redis/Celery)**
- Decouples ingestion to respect Freshdesk webhook timeout limits and handle traffic spikes

### 2. Core Orchestrator (LangGraph)
The task worker initializes a LangGraph state machine managing the "Support Agent" lifecycle:

- **Context Assembly** — Fetch last 5 interactions from Supabase `conversations` table for conversational memory
- **Semantic Router** — Lightweight embedding-based classifier mapping queries to one of 8–10 primary intents
- **Agentic Sub-graphs** — Supervisor agent delegates to specialized sub-graphs (e.g., "KYC Technical Support" vs. "Billing")

### 3. Retrieval and Knowledge Layer
Unified PostgreSQL/Supabase database as the "Source of Truth." Multi-stage retrieval pipeline:

- **Recall** — Parallel execution of BM25 (keyword) and pgvector/HNSW (semantic) search
- **Fusion** — Merge results using Reciprocal Rank Fusion (RRF) to produce single ranked list
- **Rerank** — Apply cross-encoder model (e.g., BGE-Reranker) to top 20 candidates to select 5 most relevant chunks

### 4. Verification and Safeguard Layer
Before any response is sent to Freshdesk, it passes a "Self-Correction" node:

- **Faithfulness Check** — LLM-based evaluator verifies every claim is grounded in retrieved context
- **Citation Verification** — Ensures references correctly point to provided documents
- **Confidence Logic** — If grounding score < 0.85, trigger "Asana Escalation" path instead of posting the response

---

## Recommended Retrieval System

### Hybrid Retrieval Implementation

Leverage PostgreSQL integrated full-text search alongside pgvector for a single-query hybrid search. **Reciprocal Rank Fusion (RRF)** score formula:

```
RRF_score(d) = Σ 1 / (k + rank(d))
```

Where `k = 60` (prevents first-ranked document from disproportionately dominating). A document appearing at rank #3 in keyword search and rank #5 in semantic search will often outrank a document appearing at rank #1 in only one list.

### Indexing and Chunking Strategy — Hierarchical Chunking

| **Chunk Type** | **Size** | **Purpose** |
|---|---|---|
| Leaf Chunks | 300–500 tokens | Initial vector similarity search |
| Parent Chunks | 1,500+ tokens | Retrieved when a leaf chunk is matched; provides full context for complex KYC procedures |

**Metadata Enrichment:** Each chunk must be tagged with `category`, `last_updated`, and `access_role` for "Filter-First" retrieval (prevents general queries from accessing "Internal Admin" documents).

### Search Performance Optimization

For datasets of several hundred thousand segments: use **HNSW (Hierarchical Navigable Small World)** index over IVFFlat. HNSW provides faster query times and higher recall, though it requires more memory during index construction.

---

## Recommended Query Routing Strategy

### The Semantic Router Pattern

Pre-compute embeddings for "gold-standard" examples for each category. When a new ticket arrives, compare its embedding against category centroids.

| **Route** | **Example Utterance** | **Specialized Handler** |
|---|---|---|
| Identity Verification | "Why is my Aadhaar card rejected?" | KYC-specific RAG + Doc Analysis Tool |
| Account Access | "I forgot my password and my email is old." | Auth-Policy RAG + Human Escalation |
| Billing | "Where can I find my invoice for March?" | Billing RAG + Stripe API Tool |
| General | "What does your platform do?" | High-level Marketing RAG |

### Hierarchical Routing Logic

- If semantic router's highest similarity score < **0.7** → escalate to **LLM-based Classifier** ("Slow Path")
- Slow Path uses a smaller model (GPT-4o-mini) to reason about intent when embedding similarity is ambiguous
- **Result:** Maximizes accuracy for out-of-distribution queries while maintaining sub-second routing for the 70% of repeated categories

---

## Recommended RAG Architecture

### Agentic RAG and Self-Correction — CRAG Pattern

Implement **Corrective RAG (CRAG)**:
1. Agent first evaluates quality of retrieved documents
2. If documents are irrelevant or insufficient, agent autonomously triggers a web search (Tavily / Google Search API) or flags for human intervention
3. Never attempts a hallucinated response

### Context Compression and Management

- After RRF fusion, apply cross-encoder model re-scoring
- "Context Filter" removes chunks with relevance score < 0.5
- Prevents LLM context window from being cluttered with "semantically similar noise"

### Deterministic Post-Processing

The LLM should output a JSON schema containing:

```json
{
  "answer": "Grounded response for the user",
  "citations": ["doc_id_1", "doc_id_2"],
  "confidence_score": 0.92,
  "suggested_action": "Post to Freshdesk | Internal Note Only | Escalate"
}
```

---

## Recommended Orchestration Strategy

### Durable Execution with LangGraph

Every step of agent reasoning is stored as a checkpoint in the database, enabling:

- **Time-Travel Debugging** — Engineers can replay a failed trace to see exactly where the agent went wrong
- **Human-in-the-Loop Interruption** — For "High Severity" tickets, the graph can "pause" and wait for human review before posting to Freshdesk

### Handling Long-Running Tasks

Fintech support often involves tasks that cannot complete in a single request-response cycle (e.g., waiting for a backend KYC re-scan). The durable workflow engine ensures long-running tasks survive server restarts without burning CPU cycles while idling.

---

## Recommended Learning Strategy

### Automated Knowledge Harvesting Pipeline

Monitor resolved Freshdesk tickets through a four-step pipeline:

1. **Selection** — Identify tickets resolved by human agents with positive CSAT scores
2. **Extraction** — LLM extracts the core Q&A pair and formats it into a "Knowledge Card"
3. **Deduplication** — System checks if this information already exists in the RAG corpus using vector similarity
4. **Curation** — High-quality cards presented to a support lead in a "Knowledge Dashboard" for one-click approval

### Reinforcement from Feedback

User feedback (e.g., "Was this helpful?") adjusts retrieval weights. If a specific document is frequently marked as unhelpful for a query type, its "priority score" in the reranking stage is programmatically decreased for similar future queries.

---

## Recommended Evaluation Framework

### The Evaluation Stack: DeepEval + RAGAS

| **Metric** | **Mechanism** | **Production Threshold** |
|---|---|---|
| Faithfulness | LLM-as-a-judge (RAGAS pattern) | > 0.90 |
| Answer Correctness | Comparison against "Golden Dataset" | > 0.85 |
| Hallucination Rate | Negative constraint check | < 0.02 |
| Latency (p95) | End-to-end tracing | < 5 seconds |

### Continuous Benchmarking

- Maintain a "Golden Dataset" of 500+ hand-verified support interactions
- Every deployment triggers an automated "RAG Benchmark" run
- If scores for any query type drop significantly → deployment is **automatically blocked**

---

## Recommended Observability Stack

### Tracing with LangSmith
- Recommended for debugging the LangGraph orchestrator
- Provides nested view of every execution step
- Identifies failures at classification, retrieval, or generation stage

### Monitoring with Arize Phoenix
- Superior for detecting "Retrieval Drift" in the retrieval layer
- Visualizes embedding space and identifies clusters of queries returning low-relevance results
- Alerts team to knowledge gaps before they impact customer satisfaction

### Operational Dashboards (Grafana/PostgreSQL)

| **KPI** | **Definition** |
|---|---|
| Automation Resolution Rate | % of tickets resolved without human intervention |
| Cost per Resolution | Total LLM token costs ÷ number of resolved tickets |
| Rate Limit Health | Freshdesk API usage tracking to avoid 429 errors |

---

## Recommended Security Architecture

### Zero-Trust Data Ingestion
- All data entering the AI pipeline passes through a **PII Redaction Layer**
- Named Entity Recognition (NER) masks sensitive data (Aadhaar numbers, birth dates) before sending to external LLM providers or storing in vector database

### Tenant Isolation and RBAC

- **Database Level** — Supabase Row Level Security (RLS) ensures queries can only access vectors and documents associated with the current user's `org_id`
- **Application Level** — JWT from the authenticated support agent is used to inject `org_id` into all SQL and vector queries, preventing cross-tenant data leakage

### Security Auditing
Every LLM interaction (prompt, retrieved context, generated response) is logged in an **immutable audit table** in Supabase for regulatory traceability.

---

## Scalability Analysis

| **Layer** | **Strategy** | **Expected Outcome** |
|---|---|---|
| Retrieval | pgvector + HNSW, partitioned by `category` or `org_id` | Sub-100ms retrieval at millions of segments |
| Throughput | FastAPI + Celery task queue | Hundreds of concurrent tickets without blocking |
| Cost | Model tiering + semantic caching | Costs grow sub-linearly with ticket volume |

---

## Reliability Analysis

- **Durable Orchestration** — LangGraph persists agent state; if the FastAPI worker crashes mid-execution, the agent can resume automatically, eliminating the "lost ticket" problem
- **Resilient API Consumption** — Exponential backoff retry logic for both Freshdesk and OpenAI APIs; Circuit Breaker patterns prevent cascading failures during third-party outages
- **Safe Failover** — If AI detects low confidence or retrieval failure, performs "Silent Handoff": user receives a standard acknowledgment, ticket routes to human — user never knows AI attempted it first

---

## Maintainability Analysis

- **Code-Centric Logic** — Moving prompt engineering and business rules into Python allows for unit testing, code reviews, and semantic versioning
- **Unified Tooling** — Consolidating on the LangChain/LangGraph ecosystem avoids "vendor spaghetti" of multiple low-code platforms
- **Extensible Design** — Tool-based LangGraph supervisor architecture makes it easy to add new capabilities (e.g., "Document Verification" tool, "AML Check" tool) without rewriting core routing logic

---

## Cost Optimization Strategy

| **Optimization Layer** | **Strategy** | **Rationale** |
|---|---|---|
| Retrieval | Semantic Caching | Avoids expensive vector search and LLM calls for the top 30% of repeated questions |
| Orchestration | Model Tiering | Use GPT-4o-mini for 70% of routine classifications and simple RAG |
| Generation | Prompt Compression | Reducing context window through reranking reduces input token costs by ~40% |
| Storage | Batch Embeddings | Bulk embedding APIs during ingestion reduces overhead and token waste |

> Semantic caching can reduce cost per resolution by an estimated **30–50%** for organizations where support volume is driven by a small set of recurring issues.

---

## Production Risks

| **Risk** | **Description** | **Mitigation** |
|---|---|---|
| Hallucination Drift | Even a grounded system can hallucinate if retrieved context is contradictory | "Self-Correction" node as primary defense |
| Prompt Regression | System prompt updates can have unintended side effects | Mandatory automated benchmarking on every PR |
| Regulatory Non-Compliance | Changes in fintech regulations (RBI, GDPR) may make RAG corpus inaccurate | Quarterly "Content Freshness" audit |

---

## Final Technical Recommendations

### ✅ Implement Immediately

- **Centralize Orchestration** — Migrate all reasoning and business logic out of n8n into a LangGraph-managed FastAPI service
- **Enable Hybrid Search** — Implement `pg_textsearch` and GIN indexes in Supabase for keyword-plus-vector retrieval
- **Establish Evaluation Baseline** — Deploy DeepEval and run initial benchmark against the current "Golden Dataset" to establish a performance floor
- **PII Guardrails** — Implement always-on NER redaction layer before the retrieval or generation stage

### ⏳ Delay Until Ready

- **Fine-Tuning** — Do not pursue fine-tuning of foundation models until the RAG pipeline achieves a consistent 90%+ faithfulness score
- **Autonomous Account Changes** — Do not allow the AI to perform destructive or sensitive account actions (e.g., closing accounts) without human oversight in the initial 6 months

### ❌ Do Not Implement

- **Pure Vector Search** — Abandon dense-only vector retrieval; it is a fundamental cause of KwikID's technical inaccuracy
- **Logic-Heavy Workflows in n8n** — Stop using n8n for any logic involving "thinking"; reserve it for purely mechanical data motion

---

## Implementation Roadmap

### Milestone 1 — Weeks 1–4: The Core Pivot
- Build the LangGraph orchestrator within the FastAPI service
- Migrate top 3 most common ticket types to the new engine
- Implement basic pgvector + BM25 hybrid search

### Milestone 2 — Weeks 5–8: Evaluation & Observability
- Integrate LangSmith for tracing and DeepEval for CI/CD gates
- Finalize 8–10 query routing logic and model tiering

### Milestone 3 — Weeks 9–12: The Learning Loop
- Launch automated "Knowledge Card" extraction pipeline from resolved Freshdesk tickets
- Implement human-review dashboard

### Milestone 4 — Weeks 13+: Advanced Capabilities
- Explore multi-modal RAG for analyzing KYC document images (IDs, utility bills)
- Implement more sophisticated Corrective RAG patterns

---

## Engineering Priorities

**Priority 1: Retrieval Stability**
The current inconsistency in KwikID is a direct result of weak context retrieval. Implementing hybrid search with cross-encoder reranking will provide the most immediate and visible improvement in answer quality.

**Priority 2: Architectural Consolidation**
Eliminating the "split-brain" between n8n and the backend is the only way to achieve the observability and maintainability required for a production fintech system.

> By treating the AI support agent as a first-class software citizen — subject to the same rigorous engineering standards as the core KYC platform — Think360.ai will build a truly world-class automation platform.

---

## Works Cited

1. [Top 10 LLM + RAG Architectures for FinTech Operations — Tericsoft](https://www.tericsoft.com/blogs/top-10-llm-rag-architectures-for-fintech-operations)
2. [AI in Fintech: A 2026 Builder's Guide — Saigon Technology](https://saigontechnology.com/blog/ai-in-fintech/)
3. [n8n vs Temporal vs ZenML: Choosing the Right Workflow Engine — ZenML](https://www.zenml.io/blog/n8n-vs-temporal)
4. [LangGraph vs n8n: A Comprehensive Guide — Peliqan](https://peliqan.io/blog/langgraph-vs-n8n/)
5. [n8n vs LangGraph: A Practical Guide — Tops Infosolutions](https://www.topsinfosolutions.com/blog/n8n-vs-langgraph/)
6. [Hybrid search with HNSW and BM25 reranking — Reddit r/Rag](https://www.reddit.com/r/Rag/comments/1t6cmqf/hybrid_search_with_hnsw_and_bm25_reranking/)
7. [Building Hybrid Search for RAG: pgvector + Full-Text Search with RRF — DEV Community](https://dev.to/lpossamai/building-hybrid-search-for-rag-combining-pgvector-and-full-text-search-with-reciprocal-rank-fusion-6nk)
8. [Semantic Routing and Intent Classification in AI Agent Systems](https://notes.muthu.co/2025/11/semantic-routing-and-intent-classification-in-ai-agent-systems/)
9. [Intent Recognition and Auto-Routing in Multi-Agent Systems — GitHub Gist](https://gist.github.com/mkbctrl/a35764e99fe0c8e8c00b2358f55cd7fa)
10. [Breaking through the automation glass ceiling with the Resolution Learning Loop™ — Zendesk](https://www.zendesk.com/blog/product-news/breaking-through-the-automation-glass-ceiling-with-the-resolution-learning-loop/)
11. [Architecture pattern to protect sensitive data in RAG applications — Medium](https://medium.com/@satadru1998/architecture-pattern-to-protect-sensitive-data-in-rag-applications-5e6f2d783774)
12. [Supabase for Agents](https://supabase.com/solutions/agents)
13. [LLMOps Observability: LangSmith vs Arize vs Langfuse vs W&B — Medium](https://medium.com/@kanerika/llmops-observability-langsmith-vs-arize-vs-langfuse-vs-w-b-f1baeabd1bbf)
14. [Optimizing RAG with Hybrid Search & Reranking — Superlinked](https://superlinked.com/blog/optimizing-rag-with-hybrid-search-reranking)
15. [RAGAS, TruLens, DeepEval: LLM Evaluation Frameworks — Atlan](https://atlan.com/know/llm-evaluation-frameworks-compared/)
16. [RAG Evaluation Quickstart — DeepEval by Confident AI](https://deepeval.com/docs/getting-started-rag)
17. [Multi-LLM routing strategies for generative AI — AWS](https://aws.amazon.com/blogs/machine-learning/multi-llm-routing-strategies-for-generative-ai-applications-on-aws/)
18. [Choosing the Right Multi-Agent Architecture — LangChain](https://www.langchain.com/blog/choosing-the-right-multi-agent-architecture)
19. [A practical guide to Freshdesk API rate limits — eesel AI](https://www.eesel.ai/blog/freshdesk-api-rate-limits)
20. [The Complete Guide to Hybrid Search — Alibaba Cloud](https://www.alibabacloud.com/blog/the-complete-guide-to-hybrid-search-the-perfect-blend-of-full-text-and-vector-search_602921)
21. [pgvector Hybrid Search: Benefits, Use Cases & Quick Tutorial — Instaclustr](https://www.instaclustr.com/education/vector-database/pgvector-hybrid-search-benefits-use-cases-and-quick-tutorial/)
22. [RAG Cost Optimization Strategies — Zen van Riel](https://zenvanriel.com/ai-engineer-blog/rag-cost-optimization-strategies/)
23. [DeepEval vs. RAGAS vs. LangSmith: Choosing the Right Evaluation Framework — Descope](https://www.descope.com/blog/post/deepeval-vs-ragas-vs-langsmith)
24. [Supabase RLS Advanced Patterns — Dev.to](https://dev.to/kanta13jp1/supabase-rls-advanced-patterns-team-sharing-multi-tenancy-and-admin-access-n5g)
25. [Enterprise RAG Guide 2026: Modular, GraphRAG & Agentic Patterns — Synvestable](https://www.synvestable.com/enterprise-rag.html)
