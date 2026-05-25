# Generation Pipeline

## Overview

The generation pipeline (`rag_engine/generation/`) takes retrieved chunks and produces a grounded, governed support response. It uses GPT-4o-mini by default with configurable temperature, context window, and retry logic.

## Components

### 1. `ChatGenerator` (`chat_generator.py`)

The orchestrator. Manages the full cycle from retrieval request to `GenerationResult`.

**Key responsibilities:**
- Calls retrieval (HybridTicketRetriever or TicketRetriever based on config)
- Assembles context via ContextAssembler
- Runs workflow classification and SOP branch parsing
- Builds prompt via PromptBuilder
- Calls LLM via LlmClient
- Performs post-generation branch completeness check
- Applies automation safety gate
- Returns structured `GenerationResult`

### 2. `ContextAssembler` (`context_assembler.py`)

Selects and formats retrieved chunks into an LLM-consumable context string.

**Processing steps:**

1. **Source separation**: Splits chunks into SOP chunks, knowledge chunks, and general context chunks
2. **SOP annotation**: Runs `parse_sop_content()` on each SOP chunk, injects structural flags into headers:
   ```
   ##1 [SOP | sop_id=account_lockout | score=0.742 | AUTHORITATIVE | escalation:YES | mandatory:YES]
   ```
3. **Truncation**: Each chunk is truncated to `CHAT_CONTEXT_CHUNK_MAX_CHARS = 3500` characters
4. **Citation building**: Extracts metadata for the `citations` response field
5. **Char budget**: Total context stays within the LLM's context window

**Context structure:**
```
=== STANDARD OPERATING PROCEDURES ===

##1 [SOP | sop_id=account_lockout | score=0.742 | AUTHORITATIVE | escalation:YES]
[SOP content up to 3500 chars]

##2 [SOP | sop_id=otp_delivery | score=0.631 | AUTHORITATIVE]
[SOP content up to 3500 chars]

=== KNOWLEDGE BASE ===

##3 [KNOWLEDGE | quality=0.82 | score=0.541]
[Knowledge article content]

=== CONTEXT ===

##4 [freshdesk | RESOLUTION_RCA | score=0.498]
[Resolved ticket content]
```

### 3. `PromptBuilder` (`prompt_builder.py`)

Constructs the system prompt and user message for the LLM.

**System prompt sections (in order):**
1. Role definition — "You are KwikID's AI support assistant..."
2. RESPONSE MODE — injected based on `workflow_match_type`
3. SOP branch mandate — one instruction per True SopDocumentFlag
4. Formatting rules — numbered steps, markdown, tone
5. Safety rules — never fabricate, escalate when uncertain
6. Chat history (last N turns if `persist_history=true`)

**RESPONSE MODE examples:**

```
[AUTHORITATIVE MODE - exact_match]
You have an exact SOP match. Respond directly and confidently.
Do not use hedging language. Follow the SOP steps precisely.

[RELATED_PROCEDURE MODE - related_match]
A related procedure was found but may not be an exact match.
Acknowledge the limitation: "Based on our closest relevant procedure..."
Include all applicable steps but note if verification is needed.

[INSUFFICIENT_CONTEXT MODE - weak_match]
No relevant SOP was found. Do not fabricate resolution steps.
Acknowledge the limitation and escalate to a human agent.
Provide general troubleshooting guidance only if clearly applicable.
```

### 4. `LlmClient` (`llm_client.py`)

OpenAI chat completion wrapper with retry logic.

**Configuration:**
```
OPENAI_CHAT_MODEL=gpt-4o-mini
CHAT_TIMEOUT_S=60
CHAT_MAX_RETRIES=3
CHAT_RETRY_BASE_DELAY_S=0.8
CHAT_TEMPERATURE=0.2
CHAT_MAX_OUTPUT_TOKENS=800
```

**Retry policy:**
- Retries on: `APIConnectionError`, `RateLimitError`, `APIStatusError` (5xx)
- Does NOT retry on: `AuthenticationError`, `BadRequestError` (4xx)
- Backoff: exponential with ±30% jitter

**Why `temperature=0.2`?**
Support responses require consistency. Low temperature produces deterministic, step-following outputs. `0.0` would be too rigid (no natural variation in phrasing). `0.2` balances consistency with natural language.

## Chat History

When `persist_history=true`, the conversation is stored in Supabase's `chat_messages` table and retrieved for subsequent turns. The last `CHAT_HISTORY_TURNS = 6` turns are included in the prompt.

**Table schema:**
```sql
chat_messages (
    id          uuid PRIMARY KEY,
    session_id  text NOT NULL,
    role        text NOT NULL,  -- 'user' or 'assistant'
    content     text NOT NULL,
    created_at  timestamptz DEFAULT now()
)
```

History is included as an alternating message list:
```
[user]: Previous question
[assistant]: Previous answer
[user]: Current question
```

## GenerationResult

The output from the pipeline:

```python
@dataclass
class GenerationResult:
    session_id: str
    message_id: str
    answer: str
    confidence: str              # high / medium / low / none
    confidence_score: float      # 0.0–1.0
    requires_human: bool         # escalation required
    citations: list[Citation]    # source documents used
    follow_up_question: str      # suggested clarifying question
    insufficient_context: bool   # no relevant context found
    chunks: list[ChunkResult]   # raw retrieved chunks
    diagnostics: dict[str, Any] # classification, routing, flags
```

## Answer Sanitization

Before returning, the answer is sanitized:
- Removes any leaked diagnostic data (if `DEBUG_RAG=false`)
- Strips internal field names: `sop_branch_flags`, `index_version`, `retrieval_confidence`, etc.
- Removes XML-style tags that the LLM occasionally inserts (`<result>`, `<answer>`)
- Normalizes whitespace

The `_DIAG_FIELD_RE` regex pattern (`chat_generator.py`) matches and removes these fields from the answer text.

## Follow-Up Question Generation

When the workflow match is `related_match` or `insufficient_context`, the LLM is prompted to generate a clarifying question:
```
If the answer requires additional information from the customer,
include ONE follow-up question at the end, prefixed with:
"To help you further, could you confirm: "
```

The follow-up question is extracted from the answer and returned separately in `follow_up_question`, allowing the n8n workflow to handle it distinctly from the main response.

## Async Execution

The generation pipeline is CPU/IO bound. To avoid blocking the FastAPI event loop:

```python
result = await asyncio.to_thread(generator.generate, gen_request)
```

All LLM calls, embedding calls, and Supabase queries run in a thread pool via `asyncio.to_thread`. The FastAPI event loop remains responsive to other requests during the generation.
