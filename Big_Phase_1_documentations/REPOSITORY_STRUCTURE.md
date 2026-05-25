# Repository Structure

## Overview

```
kwikid_support_system/                         ← Git repository root
├── .github/
│   └── workflows/
│       └── main.yml                           ← Active CI (GitHub Actions reads this)
├── Big_Phase_1_documentations/                ← This documentation folder
├── docs/                                      ← Moved analysis + architecture docs
│   ├── analysis/                              ← Data quality, profiling, schema analysis
│   ├── architecture/                          ← System architecture, glossary, workflow
│   ├── audits/                                ← Observed issues, audit logs
│   ├── archive/                               ← Legacy code snapshots
│   ├── reports/                               ← Feasibility and research reports
│   └── research/                              ← Concept docs and research notes
├── postman/                                   ← API collections (TRACKED — team sharing)
│   ├── fumadocs_ingest_service_e2e.postman_collection.json
│   ├── globals/
│   └── resources.yaml
├── scripts/
│   └── dev/                                   ← Dev utilities (profiling, structure gen)
├── data/
│   ├── raw/                                   ← GITIGNORED — SO Teams exports, raw data
│   └── backups/                               ← GITIGNORED — Supabase backups, exports
├── docker-compose.yml                         ← Root compose (production-grade, persistent volumes)
├── README.md                                  ← Repository README
├── PERSONAL_REPORT.md                         ← Author's technical learning document
└── kwikid-ai-ingest/
    └── ai_project/
        └── fumadocs_ingest_service/           ← TRUE APPLICATION ROOT
```

## Application Root (`fumadocs_ingest_service/`)

```
fumadocs_ingest_service/
├── .env                                       ← NOT tracked (live secrets)
├── .env.example                               ← Production-ready template (TRACKED)
├── .gitignore                                 ← Service-level ignores
├── .dockerignore
├── Dockerfile                                 ← Multi-stage build (builder + runtime)
├── requirements.txt                           ← All packages pinned to == versions
│
├── app/                                       ← FastAPI application handlers
│   ├── main.py                                ← App entry point, all route definitions
│   ├── config.py                              ← Settings dataclass, env loading, validation
│   ├── security.py                            ← API key auth, rate limiting middleware
│   ├── rate_limiter.py                        ← Per-IP sliding window (Redis + in-process)
│   ├── chat.py                                ← Legacy /chat handler
│   ├── query.py                               ← Legacy /query handler
│   ├── ingest.py                              ← /ingest handler (fumadocs + JSON + Excel)
│   ├── freshdesk_webhook.py                   ← /freshdesk/webhook handler (inbound)
│   ├── feedback.py                            ← /feedback handler (thumbs up/down)
│   ├── suggestions.py                         ← /chat/suggestions handler
│   ├── train.py                               ← Knowledge card training endpoints
│   ├── git_sync.py                            ← Git clone/pull for fumadocs repo
│   ├── vector_store.py                        ← Supabase upsert/query abstraction
│   ├── chunker.py                             ← Legacy word-based chunker
│   ├── chunker_v2.py                          ← Token-aware chunker (Phase B1.5)
│   ├── embedder.py                            ← Legacy OpenAI embedding wrapper
│   ├── embeddings.py                          ← Embedding provider abstraction
│   ├── uploader.py                            ← Batch upsert to Supabase
│   ├── parser_md.py                           ← Markdown/MDX parser
│   ├── parser_json.py                         ← JSON document parser
│   ├── parser_excel.py                        ← Excel document parser
│   ├── parser_freshdesk.py                    ← Freshdesk ticket parser
│   ├── query_preprocessor.py                  ← Query normalization and expansion
│   └── query_router.py                        ← App-level query router shim
│
├── rag_engine/                                ← RAG pipeline (retrieval, generation, ingestion)
│   ├── config/
│   │   └── rag_settings.py                    ← RAG-specific settings dataclass
│   ├── database/
│   │   └── supabase_client.py                 ← Supabase client factory
│   ├── embedding/
│   │   ├── base.py                            ← EmbeddingProvider ABC
│   │   ├── openai_provider.py                 ← OpenAI embedding implementation
│   │   └── batch_processor.py                 ← Circuit-breaker batch embedder
│   ├── retrieval/
│   │   ├── ticket_retriever.py                ← Pure-semantic retriever
│   │   ├── hybrid_ticket_retriever.py         ← Hybrid (semantic + FTS + RRF) retriever
│   │   └── reranking_hook.py                  ← CrossEncoder reranking (optional)
│   ├── generation/
│   │   ├── chat_generator.py                  ← Main RAG generation orchestrator
│   │   ├── context_assembler.py               ← Chunk selection and context building
│   │   ├── prompt_builder.py                  ← System/user prompt construction
│   │   └── llm_client.py                      ← OpenAI chat completion client
│   ├── sop/
│   │   ├── __init__.py
│   │   └── sop_parser.py                      ← Structure-aware SOP regex parser
│   ├── ingestion/
│   │   ├── pipeline.py                        ← Main ingestion orchestration
│   │   ├── knowledge_pipeline.py              ← B3 knowledge article ingestion
│   │   ├── sop_pipeline.py                    ← SOP document ingestion
│   │   ├── knowledge_classifier.py            ← Quality scoring for KB articles
│   │   ├── deduplication.py                   ← Content hash deduplication
│   │   ├── delta_tracker.py                   ← Changed-file detection
│   │   ├── schema_mapper.py                   ← Ticket schema normalization
│   │   ├── tenant_mapper.py                   ← Tag → client/tenant mapping
│   │   └── parsers/
│   │       └── stackoverflow_parser.py        ← SO Teams JSON export parser
│   ├── feedback/
│   │   ├── feedback_loop.py                   ← Thumbs up/down feedback ingestion
│   │   └── review_queue.py                    ← Human review queue management
│   ├── chunking/
│   │   └── ticket_chunker.py                  ← Three-part ticket chunker (ISSUE/QUERY/RCA)
│   ├── document_builder/
│   │   ├── sop_builder.py                     ← SOP document construction
│   │   └── ticket_builder.py                  ← Ticket document construction
│   ├── schemas/
│   │   ├── chunk_schema.py                    ← ChunkResult, RetrievalResponse dataclasses
│   │   ├── ingestion_record.py                ← IngestionRecord schema
│   │   └── ticket_document.py                 ← TicketDocument schema
│   ├── observability/
│   │   ├── ingestion_logger.py                ← Structured ingestion run logging
│   │   └── metrics_collector.py               ← Run-level metrics accumulation
│   ├── utils/
│   │   └── tokens.py                          ← tiktoken token counting utilities
│   └── cli/
│       ├── ingest_cli.py                      ← CLI entrypoint for ingestion runs
│       └── sample_retrieval.py                ← CLI tool for retrieval testing
│
├── retrieval/                                 ← Legacy retrieval module (pre-B1)
│   ├── hybrid_search.py                       ← Hybrid search orchestration
│   ├── semantic_search.py                     ← pgvector semantic search
│   ├── keyword_search.py                      ← PostgreSQL FTS keyword search
│   ├── reranker.py                            ← BM25 + CrossEncoder reranking
│   ├── fusion.py                              ← RRF fusion algorithm
│   ├── filters.py                             ← Source-type and metadata filters
│   ├── models.py                              ← Retrieval result models
│   ├── metrics.py                             ← Retrieval quality metrics
│   └── config.py                              ← Retrieval configuration
│
├── query_router/                              ← Keyword-based query classification
│   ├── classifier.py                          ← Route classification logic
│   ├── models.py                              ← RouteResult dataclass
│   ├── taxonomy.py                            ← Query categories and definitions
│   └── thresholds.py                          ← Per-route similarity thresholds
│
├── observability/                             ← Application observability
│   ├── logger.py                              ← Structured JSON logger
│   ├── metrics.py                             ← Prometheus metrics (optional)
│   ├── models.py                              ← Trace/log models
│   └── tracer.py                              ← Request trace file writer
│
├── dataset_pipeline/                          ← Offline dataset processing pipeline
│   ├── loaders/                               ← CSV and XLSX loaders
│   ├── models/                                ← Ticket data model
│   ├── preprocessing/                         ← HTML cleaning, signature removal
│   └── stages/                                ← S1–S9 pipeline stages
│
├── n8n/                                       ← n8n workflow orchestration
│   ├── kwikid_support_workflow.json           ← Importable n8n workflow (UPDATED for /rag/chat)
│   ├── environment.example                    ← n8n environment variables template
│   └── node_scripts/                          ← Reference copies of n8n Code node scripts
│       ├── Build_Supabase_Context.js
│       ├── Build_TicketplusConversation_Text.js
│       ├── Collect_Final_Response.js
│       └── Prepare_First_Response_Prompt.js
│
├── sql/                                       ← Database migrations and RPC functions
│   ├── b1_migrations/                         ← B1 schema migrations (B1_001–B1_008)
│   └── support_conversation_state.sql         ← n8n session state table
│
├── scripts/                                   ← Validation and operational scripts
│   ├── validate_response_governance.py        ← Governance validation (48/48)
│   ├── validate_b1_tokens.py                  ← Token chunking validation (19/19)
│   ├── validate_b2_generation.py              ← Generation pipeline validation (41/41)
│   ├── validate_b3_knowledge.py               ← Knowledge pipeline validation (46/46)
│   ├── validate_b2_5_security.py              ← Security validation (18/20 — 2 local-only)
│   ├── validate_b1_infrastructure.py          ← Infrastructure validation
│   ├── validate_b1_retrieval.py               ← Retrieval quality validation
│   ├── validate_b1_db_integrity.py            ← Database integrity validation (requires live DB)
│   ├── reingest_v2.py                         ← v2 reingestion CLI script
│   ├── evaluate_rag.py                        ← RAG evaluation harness
│   ├── evaluate_retrieval.py                  ← Retrieval quality evaluation
│   ├── export_video_ticket_conversations.py   ← Freshdesk export utility
│   ├── export_supabase_schema.py              ← Schema documentation export
│   ├── freshdesk_ticket_fields_probe.py       ← Freshdesk field discovery
│   ├── freshdesk_export_ticket_fields.py      ← Ticket field export
│   ├── generate_video_issues_report.py        ← Video KYC issue reporting
│   ├── security_scan.py                       ← Static security pattern scanner
│   └── verify_index_integrity.py             ← Index integrity verification
│
├── scripts_dev/                               ← Developer utilities (not production)
│   ├── run_local.sh                           ← Local uvicorn launcher (Linux/Mac)
│   ├── run_local.ps1                          ← Local uvicorn launcher (Windows)
│   ├── recreate-container.ps1                 ← Docker container rebuild utility
│   ├── cleanup_logs.ps1                       ← Log directory cleanup
│   └── debug_trace.ps1                        ← Trace file viewer
│
├── tests/                                     ← Test suite
│   ├── test_ingest_context_improvements.py
│   ├── test_b1_embedding_resilience.py
│   ├── test_b1_token_chunking.py
│   └── ...
│
├── data/
│   ├── sop/                                   ← SOP markdown files (TRACKED — source documents)
│   │   ├── account_lockout_resolution.md
│   │   ├── otp_delivery_failure_resolution.md
│   │   └── video_kyc_session_failure.md
│   └── processed/                             ← GITIGNORED — generated datasets
│
├── evaluation/                                ← Offline evaluation datasets and scripts
│   ├── evaluate_response.py
│   ├── evaluate_retrieval.py
│   ├── gold_dataset.json
│   └── expected_outputs.json
│
└── .github/
    └── workflows/
        └── ci.yml                             ← Service-level CI reference (NOT read by GitHub)
                                                  (active CI is at repo root /.github/workflows/main.yml)
```

## Key Conventions

| Convention | Rule |
|------------|------|
| True app root | `kwikid-ai-ingest/ai_project/fumadocs_ingest_service/` |
| Active CI | `/.github/workflows/main.yml` (repo root) |
| Secrets | Never committed; `.env` is gitignored |
| Postman | `postman/` at REPO ROOT is tracked; `.postman/` is gitignored |
| Data | `data/raw/`, `data/backups/`, `data/exports/` are gitignored |
| n8n workflow | `n8n/kwikid_support_workflow.json` — import into n8n directly |
| SQL migrations | `sql/b1_migrations/` — must be run in Supabase before enabling FTS/hybrid retrieval |
