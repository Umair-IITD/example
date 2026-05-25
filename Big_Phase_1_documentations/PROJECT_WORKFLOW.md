# Project Workflow

## Support Ticket Lifecycle

```
Customer submits Freshdesk ticket
          │
          ▼
Freshdesk automation trigger
  (ticket created → webhook OR n8n polling)
          │
          ▼
n8n Workflow: "KwikID Support — Freshdesk + Telegram + RAG/Chat"
          │
          ├──▶ Normalize ticket payload
          │    (subject, description, cc_emails, ticketId)
          │
          ├──▶ [Freshdesk] Fetch full conversation thread
          │
          ├──▶ Fetch OpenAPI spec from kd.app.getkwikid.com
          │
          ├──▶ LLM: Extract issue category, search query, tool hints
          │    (GPT-4o-mini with KwikID domain context)
          │
          ├──▶ POST /rag/chat (RAG service)
          │    • Hybrid retrieval: semantic + FTS + RRF
          │    • SOP matching with +0.15 boost
          │    • LLM generation with governance
          │    • Returns: answer, confidence, requires_human, citations
          │
          ├──▶ SOP Gate: hasContext AND topSimilarity ≥ 0.20?
          │    │
          │    ├── YES: Continue to response generation
          │    │
          │    └── NO: Escalation path
          │         • Create Asana task
          │         • Private note: "No SOP found — manual handling required"
          │         • STOP
          │
          ├──▶ [n8n] Generate customer-facing first response
          │    (GPT-4o-mini: formats RAG answer + OpenAPI context)
          │
          ├──▶ QA Assessment: Is response complete and accurate?
          │    • LLM self-evaluation
          │    • Regenerate if QA fails
          │
          ├──▶ Freshdesk: Post public reply (first response to customer)
          │    • To: requester + cc_emails
          │    • Content: Formatted resolution steps + support signature
          │
          └──▶ Freshdesk: Post private note (internal troubleshooting)
               • For: support agents
               • Content: Detailed troubleshooting + Uptime Kuma status
```

## n8n Workflow Architecture

The n8n workflow (`n8n/kwikid_support_workflow.json`) orchestrates the complete support flow:

### Trigger Nodes
- **Freshdesk Ticket Created**: Webhook receiver at `/freshdesk-ticket-created`
- **Telegram Trigger**: For internal agent queries via Telegram bot

### Processing Nodes
1. **Normalize Input**: Extracts ticket ID, subject, body, CC emails from any payload format
2. **Is Freshdesk Source?**: Routes Freshdesk vs Telegram payloads
3. **Fetch Freshdesk Conversations**: Gets full conversation thread for context
4. **Build Ticket+Conversation Text**: Combines subject + conversation for analysis
5. **Fetch OpenAPI Spec**: Gets current KwikID API specification (for issue analysis)
6. **Prepare Issue Detection Prompt**: Constructs LLM prompt for issue extraction
7. **OpenAPI Issue Extract**: LLM analysis → `exactIssue`, `issueCategory`, `searchQuery`, `shouldCallTroubleshootingApi`
8. **Parse Issue Output**: Extracts structured fields from LLM response
9. **Freshdesk Update Ticket Autofill**: Updates ticket category/priority based on LLM analysis

### RAG Integration Nodes
10. **RAG Chat Request**: `POST /rag/chat` — full RAG pipeline
11. **Build Supabase Context**: Processes `/rag/chat` response: `chunks` → `hasContext`, `topSimilarity`, `ragAnswer`, `ragConfidence`, `requiresHuman`, `ragContextText`, `ragContextLinks`

### Routing Nodes
12. **SOP Gate (RAG match?)**: `hasContext AND topSimilarity ≥ SOP_MIN_SIMILARITY`
13. **Prepare No SOP Escalation**: Formats escalation notification
14. **Format Asana Request**: Prepares Asana task for unresolvable tickets
15. **Create Asana Task**: Creates task in the support project
16. **No SOP Route By Source**: Routes escalation notification to Freshdesk or Telegram

### Response Generation Nodes
17. **Prepare First Response Prompt**: Builds customer-facing response prompt using RAG context
18. **First Response (OpenAPI + RAG)**: LLM generates formatted customer response
19. **Collect Final Response**: Extracts final answer text
20. **Prepare QA Assessment Prompt**: Builds QA evaluation prompt
21. **QA Assessment (First Response)**: LLM self-evaluates the response
22. **Parse QA Assessment**: Extracts pass/fail decision
23. **IF QA Pass**: Routes to regeneration if needed
24. **Prepare Regenerate First Response**: Regeneration prompt with QA feedback
25. **First Response After QA**: Regenerated response
26. **Apply QA Regenerated Response / After QA Unify**: Selects best response

### Delivery Nodes
27. **Freshdesk Public Reply (First Response)**: Posts formatted reply to customer
28. **Prepare Troubleshooting Prompt**: Builds internal troubleshooting note prompt
29. **Generate Troubleshooting Steps**: LLM generates detailed agent-facing guide
30. **Add Freshdesk Private Note**: Posts internal note with troubleshooting + Uptime Kuma data
31. **Send Telegram Response**: For Telegram-sourced queries

### Optional Nodes
32. **Build Uptime Kuma Check Plan**: Determines if infrastructure status is relevant
33. **Fetch Uptime Kuma Summary/Heartbeats**: Gets live server status
34. **Build Final Private Note HTML**: Formats private note with status data
35. **HTTP Kwik Fix API**: Calls troubleshooting API (if `shouldCallTroubleshootingApi=true`)
36. **HTTP Unity Old Prod SendLink**: Unity Bank VKYC integration (client-specific)

## Ingestion Workflow

```
Data Source Change
      │
      ├── Automatic (webhook): Git commit → /ingest endpoint
      │
      └── Manual: python -m rag_engine.cli.ingest_cli --mode full

Ingestion Pipeline
      │
      ├── Parse source documents
      ├── Delta detect (hash comparison)
      ├── Chunk (token-aware, 1200 token target)
      ├── Embed (OpenAI batch, circuit-breaker retry)
      ├── Upsert (Supabase, B1_INDEX_VERSION tag)
      └── Report (data/reports/)
```

## Data Flow for Multi-Tenant Queries

```
POST /rag/chat
  {query_text: "...", client: "tenant_a"}
           │
           ▼
  Validate: client required (ValueError if missing)
           │
           ▼
  SQL RPC: WHERE index_version='v2' AND client='tenant_a'
           │
           ▼
  Python: chunks filtered to client='tenant_a' only
           │
           ▼
  Supabase RLS: additional row-level check (defense in depth)
           │
           ▼
  Response: data scoped to tenant_a only
```

## Agent Feedback Loop

```
Agent reviews AI draft response
      │
      ├── Thumbs up: POST /feedback {rating: "thumbs_up"}
      │   → Positive signal stored in feedback table
      │   → Used for future retrieval re-ranking (Phase 2)
      │
      └── Thumbs down: POST /feedback {rating: "thumbs_down"}
          → Negative signal stored
          → Response queued in rag_review_queue for human annotation
          → Annotations used to improve training data
```
