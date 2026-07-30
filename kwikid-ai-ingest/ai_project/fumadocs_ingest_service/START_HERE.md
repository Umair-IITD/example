# START_HERE.md

**For: AI Engineer Onboarding to KwikID Support Agent**

This document tells you exactly how to orient yourself and begin contributing to this project. Read this file first, before touching any code.

---

## 1. What You Are Working On

You are working on an **Enterprise Support Agent** that automates the L1 and L2 support workflows for KwikID, a Video KYC platform used by Indian banks.

The system receives Freshdesk support tickets from bank agents, investigates them by querying the bank's admin portal APIs, reasons about evidence using an LLM, writes internal investigation notes, proposes and executes resolution actions, escalates to engineering when needed, and closes tickets.

This is NOT a chatbot. It is an autonomous backend investigation and resolution agent.

**Current position**: Sprint 2.5.6 certified. System is pre-production. Branch: `major-architecture-change`.

---

## 2. Read These Files First (in order)

Before writing a single line of code or making any implementation decision, read these files. Each one will save you from a significant mistake.

### Day 1 — Business Understanding

| Order | File | Why |
|---|---|---|
| 1 | `PROJECT_CONTEXT.md` | Complete project overview — every concept, all architecture, all sprint history |
| 2 | `Source_Of_Truth/Architectural_truth/SUPPORT_OPERATIONS_BLUEPRINT.md` | The business rules that govern EVERYTHING. When in doubt, this document is the answer. |
| 3 | `Source_Of_Truth/Architectural_truth/flow_diagram.mermaid` | The canonical system diagram. Your code must stay within this diagram's boundaries. |
| 4 | `CURRENT_STATE.md` | Exactly where the project is today — what's done, what's not, what's blocked |
| 5 | `ROADMAP.md` | What comes next and in what order |
| 6 | `CLAUDE.md` | Project rules, sprint workflow, testing conventions, permanent safety rules — binding |

### Day 2 — Architecture Deep-Dive

| Order | File | Why |
|---|---|---|
| 7 | `Source_Of_Truth/Freshdesk_discovery/` (all files) | How Freshdesk actually works — not assumptions, discovered facts |
| 8 | `Source_Of_Truth/Unity_discovery/` (all files) | How Unity Bank's portal actually works — auth header, phone_number vs URN |
| 9 | `docs/GOLDEN_PATH_ARCHITECTURE.md` | The production entry point and service assembly requirements |
| 10 | `Big_Phase_2_documentations/01_MASTER_ARCHITECTURE.md` | Master architecture |
| 11 | `Big_Phase_2_documentations/02_IMPLEMENTATION_ROADMAP.md` | Three-level maturity model |

### Before Touching Any Freshdesk Code

| File | Why |
|---|---|
| `freshdesk/responses.py` | Sole approved write path — understand what it enforces |
| `freshdesk/closure_guard.py` | Prevents HTTP 422 — understand when it fires |
| `freshdesk/reply_safety_gate.py` | Gates all public replies — understand the confidence threshold |
| `freshdesk/idempotency.py` | Prevents double-processing |

### Before Touching Case Engine Code

| File | Why |
|---|---|
| `case_engine/models.py` | Case, TopicKey, ClassificationResult, NLPSignal |
| `case_engine/topic_registry.py` | Required vs optional slots per topic — CRITICAL to understand |
| `case_engine/service.py` | CaseService — slot filling, state transitions |
| `case_engine/nlp_router.py` | NLPRouter and NLPSignal |

### Before Touching Workflow / Playbook Code

| File | Why |
|---|---|
| `case_engine/workflows/models.py` | WorkflowDefinition, WorkflowStep, WorkflowStepType |
| `case_engine/workflows/playbooks/*.yml` | Read all 5 playbooks — understand the OTP v3.0 difference |
| `case_engine/workflows/workflow_engine.py` | Deterministic step executor |
| `case_engine/workflows/playbook_registry.py` | How playbooks are loaded and validated |

### Before Touching Investigation Code

| File | Why |
|---|---|
| `case_engine/investigation/service.py` | Investigation planner |
| `case_engine/tools/adapters/unity_tools.py` | Unity Bank API adapters (real production) |
| `case_engine/tools/adapters/` | All tool adapters |

---

## 3. Repository Navigation

```
kwikid_support_system/                  ← Repo root (git root)
│
├── Source_Of_Truth/                    ← AUTHORITATIVE TRUTH (read before coding)
│   ├── Architectural_truth/            ← Blueprint + flow diagram
│   ├── Freshdesk_discovery/            ← Freshdesk API facts
│   ├── Unity_discovery/                ← Unity Bank portal API facts
│   └── Metrics_discovery/              ← Uptime Kuma facts
│
├── Big_Phase_2_documentations/         ← Architecture design docs
└── kwikid-ai-ingest/
    └── ai_project/
        └── fumadocs_ingest_service/    ← APPLICATION ROOT (this is where you work)
            │
            ├── CLAUDE.md               ← Project rules (binding)
            ├── PROJECT_CONTEXT.md      ← Full knowledge transfer
            ├── CURRENT_STATE.md        ← Today's state
            ├── ROADMAP.md              ← Future
            ├── START_HERE.md           ← This file
            ├── .remember/remember.md   ← Session handoff state
            ├── .env.example            ← All env vars documented
            ├── ontology.json           ← NLP intent + slot definitions
            │
            ├── api/routes/             ← FastAPI route handlers
            │   └── webhooks/freshdesk.py  ← Webhook entry point
            │
            ├── case_engine/            ← Core reasoning layer
            │   ├── models.py           ← Case, TopicKey, ClassificationResult
            │   ├── classifier.py       ← LLM-only topic classifier
            │   ├── nlp_router.py       ← NLP Semantic Router (Sprint 2.5.6)
            │   ├── topic_registry.py   ← Slot definitions per topic
            │   ├── service.py          ← CaseService orchestrator
            │   ├── case_state.py       ← CaseState enum (8 states)
            │   ├── clarification/      ← ClarificationEngine
            │   ├── investigation/      ← Planner, Collector, RCA, Observation
            │   ├── knowledge/          ← SOP/knowledge retrieval service
            │   ├── runtime/            ← SupportAgentRuntime
            │   ├── ticket_orchestration/ ← TicketOrchestrator
            │   ├── tools/adapters/     ← Unity + Metrics tool adapters
            │   └── workflows/          ← Engine + models + playbooks/
            │
            ├── freshdesk/              ← Freshdesk integration
            │   ├── client.py           ← FreshdeskClient (API wrapper)
            │   ├── responses.py        ← FreshdeskResponseService (SOLE write path)
            │   ├── handlers.py         ← Webhook handling + normalization
            │   ├── closure_guard.py    ← ClosureFieldGuard
            │   ├── reply_safety_gate.py ← ReplySafetyGate
            │   └── idempotency.py      ← WebhookIdempotencyStore
            │
            ├── intelligence/           ← LLM reasoning orchestrator
            │   └── orchestrator.py     ← IntelligenceOrchestrator
            │
            ├── unity/                  ← Unity Bank integration package
            ├── metrics_platform/       ← Uptime Kuma integration
            ├── rag_engine/             ← RAG / knowledge retrieval
            ├── runtime/                ← RuntimeAssembly (startup wiring)
            │   └── assembly.py
            │
            ├── docs/                   ← Sprint reports + architecture docs
            ├── docs_internal/          ← Internal analysis docs
            ├── Big_Phase_2_documentations/ ← Phase 2 architecture docs
            ├── sql/                    ← Supabase migrations
            └── tests/                  ← test_sprint*.py test files
```

---

## 4. Architectural Rules (Non-Negotiable)

These rules are from `CLAUDE.md`. They exist because violations caused production incidents. They are permanently binding.

### Rule A — Sole Freshdesk Write Path
**ALL Freshdesk writes go through `FreshdeskResponseService` in `freshdesk/responses.py`.**

Never call `FreshdeskClient.add_private_note()`, `FreshdeskClient.add_public_reply()`, or `FreshdeskClient.update_ticket()` directly from workflow code, handlers, or adapters. The `FreshdeskResponseService` is the only approved path.

Why: Safety gates (ClosureFieldGuard, ReplySafetyGate, idempotency, audit logging) are only applied through this service. Bypassing it silently skips all safety checks.

### Rule B — ClosureFieldGuard on Every Status=4/5 Write
Before any ticket PUT with `status=4` (Resolved) or `status=5` (Closed), `ClosureFieldGuard` must run. It validates: `ticket_type`, `cf_sop_status`, `cf_resolution_classification`, and `cf_clients` are all populated. Freshdesk returns HTTP 422 if any are missing.

This is already enforced inside `FreshdeskResponseService` — but if you write new code that directly calls PUT on a ticket, add the guard manually.

### Rule C — ReplySafetyGate on Every Public Reply
Before any `POST /tickets/{id}/reply` (which immediately sends email to customer), `ReplySafetyGate` must evaluate: confidence ≥ threshold, impact not in force-escalation set, body non-empty, not duplicate, kill-switch clear. If blocked → post as draft private note instead, set `cf_review_ticket=Yes`.

This is already enforced inside `FreshdeskResponseService.post_reply()`.

### Rule D — cf_clients and cf_environment Are READ-ONLY
The AI must NEVER write `cf_clients` or `cf_environment`. These fields are set by Freshdesk Dispatch'r rules before the AI receives the ticket. Overwriting them breaks multi-tenant routing.

### Rule E — ticket_type Writes Are Restricted
When writing `ticket_type`, only use one of the 8 SOT-approved values in `freshdesk.ALLOWED_TICKET_TYPES`. On reads, accept any value (tolerance is broad). On writes, be strict.

### Rule F — PII Discipline
- Log email domains only (never full email addresses)
- Never log ticket body content
- Never log API keys or credentials
- `NLPSignal.raw_text` is NEVER logged (it contains the customer's ticket text)
- Phone numbers: log only last 4 digits
- Aadhaar: never log (mask before any processing)

### Rule G — exclude_escalation=True Is Permanent
In `case_engine/knowledge/rag_adapter.py:83`, the `exclude_escalation=True` parameter must never be removed or changed. This is a Sprint 2.47 security fix. A PreToolUse hook in `.claude/settings.json` will warn you if you try to modify this line.

### Rule H — InvestigationOrchestrator Is the Sole Investigation Entry Point
`InvestigationOrchestrator` (Sprint 2.46) is the only entry point for investigation. No parallel orchestrators. No direct calls to evidence collector or planner that bypass the orchestrator.

### Rule I — asyncio.to_thread() for All Blocking I/O
FastAPI runs on an async event loop. Blocking calls (Supabase, Unity API, Freshdesk, file I/O) MUST be wrapped in `asyncio.to_thread()`. Violations starve the event loop under load and cause random timeout behavior.

### Rule J — 10-Second Webhook Budget
The Freshdesk webhook receiver MUST return HTTP 200 within 10 seconds or Freshdesk will retry the webhook (causing double-processing). All AI processing runs in `BackgroundTasks`. Never await heavy processing in the webhook handler itself.

### Rule K — Idempotency Required
Every Freshdesk write is idempotent. The `WebhookIdempotencyStore` prevents double-processing the same webhook. The `ReplySafetyGate` prevents duplicate replies. Do not bypass these.

---

## 5. Business Rules (From the Blueprint)

These are from `Source_Of_Truth/Architectural_truth/SUPPORT_OPERATIONS_BLUEPRINT.md` — the single source of truth for business behavior.

**Rule 1 — Investigation Before Action**
The system MUST investigate before proposing any action. The `PROPOSE_ACTION` guard in the workflow engine enforces this: the workflow will not reach `PROPOSE_ACTION` until `INVESTIGATE` has completed and returned evidence.

**Rule 2 — Investigation Slots vs Resolution Slots**
Required slots are those needed for INVESTIGATION, not for resolution. For OTP tickets: the system asks for URN and Session ID (to look up logs). It does NOT ask for phone number at L1 — that is a resolution parameter only needed to execute an OTP resend, which is deferred to L2.

**Rule 3 — Clarification Is For Missing Investigation Data**
The clarification engine asks for URN and Session ID. It MUST NOT ask for customer contact details (phone number, email) to attempt premature resolution. Blueprint §6 is explicit: "The system MUST NOT ask for customer details like mobile number to attempt a resolution prematurely."

**Rule 4 — Unknown Tenant Stops Automation**
If the system cannot resolve which bank client sent the ticket, automation stops immediately. The ticket is routed to human review. Never continue with an unresolved tenant.

**Rule 5 — All Actions Through Action Gateway**
No action (OTP resend, session reset, OCR retry, etc.) may bypass the Action Gateway. SAFE actions auto-execute. REVERSIBLE/HIGH actions require human approval.

**Rule 6 — Audit Everything**
Every case creation, state transition, workflow decision, tool execution, action proposal, approval, escalation, and ticket closure must be written to the audit log.

**Rule 7 — OTP L1 Is Investigation-Only**
The OTP playbook (v3.0) stops at `RESOLVE_CASE` (write investigation findings to Freshdesk). It does NOT propose or execute an OTP resend. OTP resend is deferred to L2 because L1 cannot safely select the correct channel without additional information that requires L2 judgment.

---

## 6. Source-of-Truth Files (Always Consult Before Implementing)

When implementing or debugging anything that touches an external system, read the SOT doc FIRST.

| Before touching... | Read this SOT doc |
|---|---|
| Any Freshdesk API call | `Source_Of_Truth/Freshdesk_discovery/` (all files) |
| Any Unity Bank API call | `Source_Of_Truth/Unity_discovery/` (all files) |
| Any Uptime Kuma / metrics call | `Source_Of_Truth/Metrics_discovery/` (all files) |
| Any workflow or pipeline change | `Source_Of_Truth/Architectural_truth/SUPPORT_OPERATIONS_BLUEPRINT.md` |
| Any architecture change | `Source_Of_Truth/Architectural_truth/flow_diagram.mermaid` |

**Critical Unity Bank facts** (easily confused):
- Primary lookup key is `phone_number` (10-digit mobile), NOT URN. Unity portal has no concept of URN.
- Auth header is `auth: <token>` (custom header), NOT `Authorization: Bearer <token>`
- API runs on port 443. The SPA frontend is on port 9090 (browser-only, not accessible via API)
- Service account credentials: `{"username": "unity", "password": "unity"}`
- Token format: JWT HS256, TTL 12 hours, field name is `Token` (capital T)

**Critical Freshdesk facts** (easily confused):
- Dispatch'r rules run BEFORE the AI webhook fires. `cf_clients` is already set when you receive the ticket.
- No draft reply API exists. `POST /reply` immediately sends email to customer — no undo.
- Closure requires 4 fields: `cf_clients` (read-only for AI), `ticket_type`, `cf_sop_status`, `cf_resolution_classification`
- HTTP 422 means a required field is missing — always check ClosureFieldGuard is applied
- Rate limit: 40 req/min account-wide; self-limit to 30 req/min

---

## 7. Development Workflow (from CLAUDE.md)

Every sprint follows this exact sequence. Do not skip steps.

```
1. /sot-check <layer>     — Pull the relevant SOT doc for the layer being touched
2. Implement              — Work inside existing package boundaries; do not duplicate
3. Write tests            — tests/test_sprint<N>_<topic>.py (section-based A, B, C...)
4. /regression            — Run Sprint 2.28.x + 2.46 + 2.47 + 2.48 + prior + newest scope
5. freshdesk-safety-reviewer   — Dispatch if sprint touched any Freshdesk write path
6. Sentry check           — search_issues(period=7d); block cert if relevant open issue
7. sprint-auditor         — Dispatch for final Ready/Blocked verdict
8. /sprint-cert <X.Y.Z>  — Write the certification report
9. /remember              — Update .remember/remember.md handoff
10. Update auto-memory    — Add durable facts to ~/.claude/projects/.../memory/
```

**Slash commands available**:
- `/sprint-cert` — Start sprint certification report from template
- `/sot-check` — Pull SOT doc for a given layer
- `/regression` — Run regression suite and report deltas
- `/remember` — Save session handoff to `.remember/remember.md`

---

## 8. Testing Conventions

**File naming**: `tests/test_sprint<N>_<topic>.py` — one file per sprint/topic.

**Section naming**: `class TestA_SlotExtraction`, `class TestB_Clarification`, etc.

**Test naming**: `test_A1_extracts_urn_from_ticket_text`, `test_A2_returns_empty_when_no_urn`, etc.

**Network**: Never touch the network in tests. Use `httpx.MockTransport` for all HTTP calls.

**Datetime**: Use `datetime.now(tz=timezone.utc).replace(tzinfo=None)` — NOT `datetime.now()`. The dev host is IST (+05:30); `datetime.now()` will be 5.5 hours ahead of UTC and trigger the clock-skew guard in the idempotency store.

**Required env vars** in every test fixture:
```python
monkeypatch.setenv("RAG_API_KEY", "test-key")
monkeypatch.setenv("OPENAI_API_KEY", "test-key")
monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
monkeypatch.setenv("SUPABASE_KEY", "test-key")
monkeypatch.setenv("AUDIT_BACKEND", "inmemory")
monkeypatch.setenv("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
```

**Mocking NLPRouter**: Never call the real OpenAI API in tests. Use `MagicMock(spec=NLPRouter)` with `mock_router.route.side_effect = _mock_route_function`. See `tests/test_sprint1_classifier.py` for the canonical pattern.

**Pre-existing failures** (do not count as regressions):
- `tests/test_sprint2281_ticket_created_handler.py` — 2 tests (Sprint 2.30.1 interface drift)
- ~129 additional failures across sprint216/219/224/228x/229x/2292/golden/stackoverflow tests (documented in Sprint 2.47 handoff)

---

## 9. How to Approach Implementation

### Check the SOT first
Before implementing anything that touches an external system (Freshdesk, Unity Bank, Uptime Kuma), read the SOT doc. The SOT documents contain facts discovered from actual API calls and admin panel investigation — not documentation assumptions.

### Work within existing package boundaries
The package structure is intentional. Do not create new packages unless the sprint specifically requires it. Adding a new tool adapter? Put it in `case_engine/tools/adapters/`. Adding a new investigation step? Add it to the playbook YAML. Do not create parallel structures.

### Playbook changes > code changes
If you need the investigation to call a new tool or in a different order, update the YAML playbook's `investigation_steps` metadata. Do not add a new Python condition in the investigation planner.

### Prefer editing existing files
The `FreshdeskResponseService`, `WorkflowEngine`, and `SupportAgentRuntime` are central objects that many components depend on. Add capabilities there rather than creating parallel implementations.

### Sprint size
Each sprint should be a single focused deliverable that can be certified. If a sprint requires >3 new files, consider whether it can be split. Sprints with too many changes are hard to regress and hard to certify.

---

## 10. How to Approach Debugging

### Check the trace tags first
Every major pipeline boundary has trace tags: `TRACE_FD_01`–`10`, `TRACE_UNITY_01`–`10`, `TRACE_INTEL_01`–`22`, `TRACE_METRICS_01`–`06`, `ENTER_*/EXIT_*` runtime tags. If something is not happening, find the trace tag that should have fired and check why it didn't.

### Check the audit log
Every case state transition and tool execution is in the Supabase audit table. If a ticket got stuck, query `SELECT * FROM audit_events WHERE case_id = 'X' ORDER BY created_at` to see exactly what happened.

### Check Sentry
The Sentry org is `think360-n0`, project `python-fastapi`. Before debugging a production issue, run `search_issues(query="is:unresolved", period="7d")` — there may already be a captured exception with a stack trace.

### The Blueprint is the spec
If you don't know whether the system should do X, look it up in `SUPPORT_OPERATIONS_BLUEPRINT.md`. The Blueprint defines the expected behavior. If the code disagrees with the Blueprint, the code is wrong.

### Check the SOT before assuming Unity API behavior
If a Unity Bank API call fails, don't assume you know the correct endpoint or auth format. Read `Source_Of_Truth/Unity_discovery/` — it contains the exact endpoints, auth, and field names discovered from real API calls.

---

## 11. Important Warnings

### Warning 1 — NEVER set SUPPORT_AGENT_MODE=PRODUCTION without resolving the 4 blockers
The system is currently in DRY_RUN mode. Setting it to PRODUCTION without first resolving the 4 Freshdesk admin blockers will cause the system to process real Unity Bank tickets incorrectly (or not at all).

### Warning 2 — NEVER revert exclude_escalation=True
`case_engine/knowledge/rag_adapter.py:83` — the `exclude_escalation=True` parameter is a permanent security fix from Sprint 2.47. A PreToolUse hook will warn you if you attempt to modify it. Do not override the hook.

### Warning 3 — NEVER write to cf_clients or cf_environment
These fields are controlled by Freshdesk Dispatch'r rules. Overwriting them breaks multi-tenant routing for all subsequent processing.

### Warning 4 — NEVER call FreshdeskClient write methods directly
`add_private_note`, `add_public_reply`, and `update_ticket` on `FreshdeskClient` must never be called from anywhere except `FreshdeskResponseService`. See CLAUDE.md Rule A.

### Warning 5 — Freshdesk has no draft API
`POST /tickets/{id}/reply` sends email immediately and cannot be undone. Always go through `ReplySafetyGate`. Use private notes (`private=true`) as draft mechanism.

### Warning 6 — Two AuditLogger classes exist
`case_engine/audit.py` and a legacy audit service coexist in the codebase. Always use the one instantiated in `runtime/assembly.py` and injected via dependency injection. Never instantiate your own AuditLogger.

### Warning 7 — Unity API auth is `auth:` not `Authorization: Bearer`
The Unity Bank admin portal uses a custom header `auth: <token>`, not standard HTTP bearer auth. If you ever modify the Unity client, preserve this header name exactly.

### Warning 8 — OTP playbook v3.0 is intentionally action-free
The OTP playbook does not have PROPOSE_ACTION, ACTION_GATEWAY, or EXECUTE steps. This is by design. L1 investigates why OTP failed; it does NOT resend OTP. Do not "fix" this by adding execution steps to the OTP playbook.

### Warning 9 — Phone number is primary Unity lookup key, not URN
When looking up a customer in Unity Bank's admin portal, use `phone_number` as the identifier. URN does not exist in the Unity portal. The `getAllUserSession` endpoint takes `domain` and `phone_number` as path parameters.

### Warning 10 — Service role key is root access
`SUPABASE_KEY` (service_role key) bypasses all Row-Level Security. Never log it, return it in API responses, or commit it to git. Rotate immediately if exposed.

---

## 12. What Not to Change

These things must not be changed without explicit architecture review and a new sprint certification:

| What | Where | Why |
|---|---|---|
| `exclude_escalation=True` | `case_engine/knowledge/rag_adapter.py:83` | Security fix, Sprint 2.47 |
| OTP playbook v3.0 — no execution steps | `case_engine/workflows/playbooks/otp_delivery_failure.yml` | L1 is investigation-only for OTP |
| Required slots for OTP: `{urn, session_id}` | `case_engine/topic_registry.py` | Blueprint §6 compliance — fixed in Sprint 2.5.6 |
| `FreshdeskResponseService` as sole write path | `freshdesk/responses.py` | All safety gates enforced here |
| `ClosureFieldGuard` on status=4/5 | `freshdesk/closure_guard.py` | Prevents HTTP 422 |
| `cf_clients` and `cf_environment` as read-only | CLAUDE.md + `freshdesk/responses.py` | Multi-tenant routing |
| Index versioning (`ACTIVE_INDEX_VERSION`) | `.env` | Controls which knowledge base version is live |
| The `auth:` header for Unity Bank | `unity/client.py` | Non-standard but required by Unity |
| The golden path entry point | `api/routes/tickets.py` | Production entry point, don't add competing entry points |

---

## 13. Immediate Next Action

If this is your first session on the project, the pending work is:

1. **Write sprint-2-5-6.md** using `/sprint-cert 2.5.6` — this certification report was not written at the end of Sprint 2.5.6 and must be completed before starting the next sprint

2. **Then begin Wave 5**: Resolve the 4 production blockers (these require Freshdesk admin access, not code changes) — see `CURRENT_STATE.md` Section 2

3. **Reference session state** in `.remember/remember.md` at the project root for the most recent in-session handoff note

---

## 14. MCP Servers Available

From `CLAUDE.md`:

| Server | When to use |
|---|---|
| `sentry` (org: think360-n0, project: python-fastapi) | Every sprint: search for unresolved issues before cert. On any production bug, analyze with Seer. |
| `context7` | When touching FastAPI / httpx / Pydantic / Supabase-py — pull live docs |
| `supabase` | pgvector/FTS query prototyping, RLS review |
| `asana` | Phase 3 escalation work (Wave 8) |
| `chrome-devtools` / `playwright` | Frontend / Phase 5 approval UI (future) |

---

*Read this file once. Then read PROJECT_CONTEXT.md for full depth. Then read CLAUDE.md for binding rules. Then start.*
