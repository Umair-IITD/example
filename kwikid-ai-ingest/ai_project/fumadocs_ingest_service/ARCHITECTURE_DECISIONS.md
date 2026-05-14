# Architecture Decisions (ADR)

## ADR-001: Local Foundation & Observability
**Date**: 2026-05-12
**Status**: Accepted

### Context
The system lacked a structured development foundation, isolated evaluation space, and granular observability.

### Decision
We are introducing a standardized directory structure and a non-intrusive tracing layer to capture RAG/LLM execution metadata.

### Consequences
- Improved debuggability of retrieval failures.
- Faster onboarding for new developers.
- Established path for systematic evaluation.
```
