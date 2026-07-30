# Pending Updates to SUPPORT_OPERATIONS_BLUEPRINT.md and flow_diagram.mermaid

**Purpose of this document:** this is a change-briefing, not a rewrite. The
two files it's about — `SUPPORT_OPERATIONS_BLUEPRINT.md` and
`flow_diagram.mermaid` (both in this same `Architectural_truth/` folder) —
were left untouched (read-only) while producing this. Everything below is
the complete set of corrections and additions a fresh reader needs before
rewriting those two files, based on a full research pass into the real
codebase plus live testing against the real Unity admin portal and Grafana
Loki APIs. The companion document with the full technical detail behind
every item here is `Loki_Log_Tool_Blueprint/Unity_Loki_Integration_Findings.md`
— that file should be read alongside this one; this document is organized
around "what changes in the two SOT files and why," that one is organized
around "what we found and how we found it." The prototyped/tested code
referenced throughout (`loki_client.py`, `log_extractor.py`,
`investigate_session.py`, all in `Loki_Log_Tool_Blueprint/`) is scratch/test
code, not part of the production codebase — none of it has been wired in.

**How to use this document:** each item below names the exact file and
section/line it corrects, quotes or paraphrases the current text, states
the problem, and gives the correction with its supporting evidence. Item
numbers are not priority order — Section 0 (scope boundary) and Section 6
(the Loki gap itself) are the two most consequential and should be read
first regardless of where they land in the rewritten document.

---

## 0. The single most important gap: no scope boundary is written down anywhere

Neither SOT file states what "support" actually means for this system, and
this omission has been the root of real mistakes during this research pass
(including on my own part, before being corrected). **KwikID is a
third-party VKYC platform vendor — not the bank, and not the customer.**
"Support" here means platform/technical issues only. It does NOT mean KYC
business decisions.

Concretely:
- A session rejected for `"Any Other Non-Technical Issues"` (a real example
  found this pass, session `b96a55e6`) is **out of scope** — that's the
  bank's/auditor's business call, not a platform failure.
- A session rejected with auditor feedback like *"Customer is not audible
  (duration 1:19 to 2:40 technical issue), REJECT"* (real example, session
  `43f1b1cd`) **is in scope** — that's a platform/technical failure the
  support agent should investigate and potentially escalate.
- `session_status` alone cannot distinguish these two cases (see item 5
  below) — only the auditor's own feedback text can, and even then only by
  parsing it for technical-issue language, not by treating any rejection
  as equally investigable.

**Where this belongs:** `SUPPORT_OPERATIONS_BLUEPRINT.md` §7 "Investigation
Layer" / "Investigation Objective" (currently just "What happened? Why did
it happen? Can it be fixed automatically? Should it be escalated?" — no
scope statement at all). Recommend adding an explicit "Scope Boundary"
subsection there, and referencing it from §11 (Summary Analysis), §13
(Reasoning Engine), and §21 (Escalation Logic), since all three currently
read as if any negative outcome is equally actionable.

---

## 1. `flow_diagram.mermaid` — the Session Logs Tool is wired to the wrong upstream system

This is the second most important correction, and it's a direct fix to the
approved diagram itself, not just the prose document.

**Current diagram (lines 66-81, 165-190):** `LOGTOOL[Session Logs Tool]` is
declared inside the same `Tools` subgraph as `SESSIONTOOL`, `FAILTOOL`,
`CASETOOL`, `ONBOARDTOOL`, and is wired: `ADMINSVC --> LOGTOOL` (line 173),
i.e. it's drawn as just another capability served by `ADMINSVC[Client
Support APIs]`, which is fed by `UNITYADMIN[Unity Bank Admin APIs]` (line
69, 166-167). In other words, the diagram currently assumes backend logs
come from the same admin-portal API family as session details.

**Why this is wrong:** confirmed by direct reading of the production
`unity/*.py` code and by live-testing the real Unity admin portal endpoints
this pass — `get_session_details` (`GET /v1/session/get_details/{session_id}`)
returns rich session evidence (status, timeline, auditor decision, docs/QnA)
but **no backend/infrastructure logs whatsoever**. There is no log endpoint
on the admin portal. Backend logs live in Teleport and are only reachable
through a completely separate system: Grafana Loki, with its own base URL,
its own datasource UID, and its own Basic-Auth credentials per tenant —
entirely independent of the Unity admin-portal credentials.

**Required diagram changes:**
1. Remove the edge `ADMINSVC --> LOGTOOL` (line 173).
2. Add a new subgraph (parallel to the existing `Tenant`/`Tools` ones), e.g.:
   ```
   subgraph LokiSystem [Multi-Tenant Log Platform - Grafana Loki]
       LOKIREG[(Per-Tenant Loki Registry: base_url + datasource UID + credentials)]
       LOKIAPI[Direct Loki HTTP API - /loki/api/v1/query_range]
   end
   ```
3. Wire it in parallel to the existing admin-portal path:
   `TENANTROUTER --> LOKIREG --> LOKIAPI --> LOGTOOL`
   (mirroring the existing `TENANTROUTER --> UNITYADMIN --> ADMINSVC -->
   SESSIONTOOL` pattern, but as a fully separate branch — the point of the
   diagram change is that `LOGTOOL` no longer descends from `ADMINSVC` at
   all).
4. Add a new node between `LOGTOOL` and `EVIDENCE`, e.g.
   `LOGEXTRACT[Log Relevance Extractor: PII redaction + BM25 ranking]`, and
   wire `LOGTOOL --> LOGEXTRACT --> EVIDENCE` (mirroring the existing
   explicit `METRICTOOL --> EVIDENCE` / `SERVERTOOL --> EVIDENCE` return-path
   pattern already in the diagram at lines 192-193). See item 6 below for
   why this stage must exist and cannot be skipped.
5. Add a comment block near the new subgraph, similar in style to the
   existing `%% Reserved for Future Use` comment on the `Execution`
   subgraph (lines 84-86), stating explicitly: *"Unity Bank Admin APIs
   provide session details, summary, and audit trail ONLY — no backend
   logs. Backend logs are served exclusively by the separate Multi-Tenant
   Log Platform (Grafana Loki) below."* This is worth being explicit and
   even a little redundant about, since it's the exact wrong assumption
   this research pass started from and had to be corrected out of.

**Confirmed real tenant registry so far** (for whoever fills in `LOKIREG`'s
actual contents): SaaS (fully validated, direct API, working end-to-end),
BOB (endpoint path corrected from an initial wrong Grafana-proxy
assumption, now validated), Canara (still returns 404 on the assumed path —
unresolved, deprioritized by explicit instruction), CBI (no connection
details received from engineering yet — blocked). Do not assume all
tenants share one URL pattern — SaaS and BOB use different direct hosts,
and Canara's correct path is still unknown.

---

## 2. `SUPPORT_OPERATIONS_BLUEPRINT.md` §9 "Session Lookup Process" conflates two different systems into one flat list

**Current text (lines 513-529):**
```
1. Open session.
2. Retrieve details.
3. Retrieve summary.
4. Retrieve logs.
5. Retrieve video.
6. Retrieve audit trail.

Output: Investigation context.
```

**Problem:** reads as if all six steps hit the same portal in one pass. In
reality, steps 2/3/6 (details, summary, audit trail) come from one call —
`unity.client.get_session_details(session_id)` — already fully built and
working (see item 4). Step 4 (logs) requires a **second, independent
call**, to a **different system** (Loki), using the **time window returned
by step 2** as input (`timeline.start_time` / `timeline.end_time`, falling
back to `vkyc_start_time`/`init_time`/`last_active_timestamp` when a
session status doesn't populate the primary pair — e.g. a session abandoned
before video start). Step 5 (video) has its own separate note — see item 9.

**Correction:** rewrite as an explicit two-phase process:
```
Phase A (Unity Admin Portal — one call, already built):
  1. get_session_details(session_id)
  2. -> session_status, timeline{start_time,end_time,...}, auditor{result,feedback},
        summary, audit trail  [see item 4 for what's already built]

Phase B (Multi-Tenant Log Platform — separate call, uses Phase A's timeline):
  3. fetch backend logs for [timeline.start_time, timeline.end_time] (+ buffer)
  4. -> raw merged log text (can be 500+ lines / ~470KB for one session — see item 6)
  5. -> extract only the lines relevant to the ticket's specific issue (see item 6)

Phase C (future — video, see item 9):
  6. locate + (eventually) analyze recorded video
```

---

## 3. `SUPPORT_OPERATIONS_BLUEPRINT.md` §8 "URN Lookup Process" — "URN" is not a real Unity concept

**Current text:** the section is titled "URN Lookup Process" and describes
searching the admin portal by URN.

**Problem:** already flagged in the project's own prior discovery docs
(`Source_Of_Truth/Unity_discovery/limitations.md` §2.3, confirmed again
this pass by reading the real `unity/client.py`): Unity's data model has
**no URN field at all**. The only customer identifiers are `phone_number`
(10-digit) and `session_id`. The real endpoint is
`GET /api/v1/getAllUserSession/{domain}/{phone_number}` — note singular
"Session," and it is keyed by phone number, not a generic user ID or URN.

**Correction:** rename/reframe this section to "Phone Number Lookup
Process" (or similar), and add the explicit normalization rule already
recommended in `limitations.md`: any ticket that mentions "URN" must be
treated as a request to normalize to `phone_number` or `session_id` — URN
is not a field the system can query on, ever, for this tenant. (Whether any
other tenant's admin portal genuinely has a URN concept is unconfirmed —
flag as an open item per client, don't assume Unity's model generalizes.)

---

## 4. `SUPPORT_OPERATIONS_BLUEPRINT.md` §33 "Future Integrations" undersells what's already built, and omits Loki entirely

**Current text (lines 989-998):**
```
- Freshdesk API
- Asana API
- Multi-Tenant Support Portal APIs
    * Current : Unity Bank
    * Future : Bank of Baroda, Central Bank, Additional Clients
- Session APIs
- Metrics APIs
- Video APIs
```

**Problems:**
1. "Current: Unity Bank" undersells reality — the Unity admin-portal
   integration is not a partial/in-progress item. It is a **complete,
   production-grade module** already sitting at
   `kwikid-ai-ingest/ai_project/fumadocs_ingest_service/unity/` (config,
   token manager with JWT-exp-aware refresh, async client with retry/backoff,
   normalizer, session resolver), wired into **5 registered `BaseTool`
   adapters** (`GetSessionDetailsTool`, `GetUserDetailsTool`,
   `GetFailureReasonTool`, `GetCaseHistoryTool`, `GetOnboardingStatusTool`)
   in `case_engine/tools/adapters/unity_tools.py`, registered at startup in
   `runtime/assembly.py`. The only reason it isn't serving live traffic yet
   is that the system is still in `DRY_RUN` pending a Freshdesk routing-rule
   scope decision — that's an operational gate, not a missing-code gap.
2. There is no mention anywhere of Grafana Loki, even though it's the
   system that provides the one investigation input (backend logs) the
   admin portal cannot. This pass validated real, working endpoint
   contracts, auth mechanism, and query patterns for at least one tenant
   (SaaS) — it is no longer a speculative "future" item, it's a validated,
   ready-to-build capability (see the companion findings doc for the full
   technical detail and `loki_client.py` for tested, working code).
3. "Session APIs" / "Metrics APIs" as bare bullets don't distinguish that
   "Metrics" here means Uptime Kuma (a **third**, separate system, already
   partially wired per `ToolProvider.METRICS_PLATFORM` in
   `case_engine/tools/tool_models.py`, Sprint 2.50) — three genuinely
   different backend systems (admin portal, Loki, Uptime Kuma) are being
   flattened into two vague bullets.

**Correction:** replace with something like:
```
- Freshdesk API (future)
- Asana API (active — Wave 8, see §20A)
- Multi-Tenant Support Portal APIs (Unity: COMPLETE, DRY_RUN pending Freshdesk
  routing scope; BOB/Central Bank/Additional Clients: future)
- Multi-Tenant Log Platform (Grafana Loki) — validated this pass for SaaS,
  endpoint-corrected for BOB, Canara unresolved (404), CBI blocked (no
  connection details yet) — see Unity_Loki_Integration_Findings.md
- Metrics Platform (Uptime Kuma) — separate system from the above, Sprint 2.50
- Video APIs (future capability — but see item 9: video *location* is
  already solvable via S3 keys embedded in existing Loki log lines, even
  though video *content analysis* remains a genuine future capability)
```

---

## 5. `SUPPORT_OPERATIONS_BLUEPRINT.md` §11 "Summary Analysis Process" — `session_status` is not a reliable signal on its own

**Current text (lines 561-581):** lists "VKYC Outcome" and "Rejection
reasons" as things the system evaluates, with no caveat about reliability.

**Problem, confirmed against a real example (`43f1b1cd`):** `session_status`
came back `kyc_result_approved` even though the auditor's own review
overrode the agent's approval to `Rejected`, with feedback text reading
*"Redo/Reopen - Audio not available (#A1) - Customer is not audible
(duration 1:19 to 2:40 technical issue), REJECT."* The real signal lives in
the `auditor` block (`auditor.result`, `auditor.feedback`, `auditor.fdbk_code`),
which can **disagree with** `session_status`. The production
`unity/normalizer.py`'s `derive_failure_summary()` was read this pass: it
does surface `auditor.feedback` text in its message when
`session_status.is_rejected` is true — but in this real example,
`session_status` itself reported success, so that rejected-branch logic
never even triggers. A session can look "approved" at the top level while
the auditor's actual recorded outcome — the one that matters for scope
determination (item 0) — says otherwise.

**Correction:** §11 needs an explicit note: *"`session_status` alone must
never be treated as the final outcome. `auditor.result` / `auditor.feedback`
must always be independently inspected, regardless of what `session_status`
reports, since the two can disagree — this is not a hypothetical, it has
been observed in real session data."* This should also inform whoever
extends `derive_failure_summary()` (or writes the Reasoning Engine's
consumption of it) that a supplementary, `session_status`-independent check
against `auditor.feedback` text is required to reliably classify
platform/technical vs. business-decision rejections per item 0's scope
boundary.

---

## 6. `SUPPORT_OPERATIONS_BLUEPRINT.md` §10 "Logs Analysis Process" — wrong example taxonomy, and missing the single biggest practical constraint

**Current text (lines 532-559):** lists example log types
(`GENERATE_OTP`, `VALIDATE_OTP`, `SEND_SMS`, `SEND_EMAIL`, `PAN_VALIDATION`,
`AADHAAR_VALIDATION`, `DMS_OPERATION_LOG`, `CALLBACK_EVENTS`) and states the
system must identify failures/timeouts/validation errors/callback
failures/network issues, outputting "Root Cause Candidates." No mention of
where logs come from, what they actually look like, or how large a pull
can get.

**Problem 1 — the example list doesn't match real observed data.** Live
Loki pulls this pass (two real sessions, 500+ lines each) show the actual
taxonomy is organized by `service_name` (`userapi`, `agentapi`, `nginx`,
`kwikid-celery-worker-make_seekable_video`, `notificaion-service`,
`celeryworker`, `agentcelery`, plus ~90 total distinct `service_name`
values seen in this tenant's label set) and `detected_level`
(`info`/`warn`/`error`/`debug`/`unknown`), with two structurally different
line shapes: (a) structured `"JSON DATA : {...}"` client-telemetry events
carrying an `event_data.event` name (e.g. `UP_PROC_MEDIA_LK_AUDIO_TRACK_UNMUTED`,
`UP_PROC_MEDIA_LK_ROOM_DISCONNECTED`, `KYC_REQUEST_REJECTED`,
`GETCURRENTSTATUS:...`), and (b) conventional Python-logger lines (e.g.
`INFO:set_kyc_result:...`, `ERROR:upload_video_single:...`). None of the
example event names in the current §10 text (`GENERATE_OTP`, `SEND_SMS`,
etc.) were observed in either real sample pulled this pass — they may exist
for other flows, but §10 should be grounded in confirmed data, or at least
labeled as illustrative/unconfirmed rather than presented as the taxonomy.

Three real, reproducible bugs were found this pass purely by reading real
log samples, worth citing as concrete evidence of what "Logs Analysis"
actually surfaces in practice: an `upload_video_single` KeyError chain
(`'number_of_videos_uploaded'`, `'user_url'`, `'agent_url'` — all
non-fatal, pipeline continues), an S3 `PutObjectAcl AccessDenied` error
(the `kwikid` IAM user attempting to make an object public against a bucket
with public ACLs blocked), and nginx "access forbidden by rule" 403s on
`OPTIONS`/`POST /v1/user/event/...` calls.

**Problem 2 — no mention of scale, and this is the constraint that
determines the whole design.** A real ~15-minute session pull is **500
lines / ~473KB of raw text**. §10 currently implies logs flow straight into
"Root Cause Candidates" with no intermediate step. That's not viable:
posting ~473KB into an LLM prompt on every ticket is expensive at volume,
dilutes the model's attention with routine noise (queue-polling heartbeats
firing every ~2 seconds, nginx preflight lines), and — critically — one
single line in that same real sample is **110,235 characters by itself**,
almost entirely a base64-encoded Aadhaar photo plus other PII fields in
plaintext (see item 7).

**Correction:** §10 needs a new, explicitly named sub-step between "Logs"
and "Root Cause Candidates": a **relevance extraction stage**, run locally
with no paid API (zero marginal cost per ticket), that:
- Redacts known PII-bearing fields and hard-truncates any oversized line
  (both must happen *before* anything else, or one giant line can consume
  an entire size budget by itself — a real failure mode hit and fixed
  during prototyping, see item 7).
- Scores every remaining line by a composite of: BM25 lexical match against
  the ticket's own text (reusing, not reinventing, the existing
  `hybrid_ticket_retriever.py` BM25 implementation already running
  elsewhere in this codebase for ticket-to-knowledge-base retrieval),
  a fixed boost for `error`/`warn`-level lines, and a boost for lines whose
  `service_name` matches a keyword guessed from the ticket text.
- Always force-includes every `error`-level line plus any lines falling
  inside a sub-window the auditor's own feedback text pinpoints (e.g.
  "duration 1:19 to 2:40" — parsed and anchored to `vkyc_start_time`).
- Keeps only the top-scoring lines within a size budget, then re-sorts
  chronologically before handoff to the Reasoning Engine.

This was prototyped and empirically tested this pass (`log_extractor.py`,
`investigate_session.py`, both in `Loki_Log_Tool_Blueprint/`) against the
real 500-line/473KB sample with three different simulated ticket queries:
each reduced to 26-46 lines (~8.5-9KB, 98%+ smaller), and each surfaced
genuinely different, query-specific diagnostic evidence rather than always
returning the same lines — see `Unity_Loki_Integration_Findings.md` §6.4
for the full results table. Why BM25 and not embeddings: the only
embedding provider in this codebase (`rag_engine/embedding/openai_provider.py`)
is OpenAI's — paid, per-call, would need to run per ticket — which
contradicts the goal of a near-zero-cost filter stage; there is no
local/free embedding provider built yet. BM25 already handles the
vocabulary-gap problem embeddings would solve, because the tokenizer splits
on underscores, so ticket words like "audio" or "disconnected" match real
event codes like `UP_PROC_MEDIA_LK_AUDIO_TRACK_UNMUTED` /
`UP_PROC_MEDIA_LK_ROOM_DISCONNECTED` directly, at zero cost.

---

## 7. `SUPPORT_OPERATIONS_BLUEPRINT.md` §27 "Security Guardrails" — "PII Protection" is a bare bullet with no teeth

**Current text (lines 856-868):** lists "PII Protection" as one bullet
among eight, with no specifics.

**Problem:** a real, concrete PII exposure was found this pass, not a
theoretical risk. A single Loki log line (client-side telemetry event,
fires on essentially every KYC submission, not a rare edge case) was found
to contain a base64-encoded Aadhaar photo plus Aadhaar number, full name,
DOB, PAN number, address, nominee details, income bracket, marital status,
and occupation, all in plaintext — 110,235 characters in that one line
alone. This flows from the client-side event pipeline into Loki unredacted
today, independent of anything this research pass built.

**Correction:** two things need to happen, at different levels:
1. **§27 needs a specific, non-negotiable rule added**, not just the bare
   bullet: *"No raw Loki log text — or any content derived from it — may
   reach an LLM prompt, a Freshdesk note, or an L1 agent's screen without a
   redaction pass first. This is not precautionary; a real, unredacted
   Aadhaar photo and associated PII fields were found flowing through
   production logs during this research pass."*
2. **Separately and urgently**, the fact that this PII reaches Loki at all
   (rather than being scrubbed at the point the client-side event is
   logged) is a bigger issue than anything this document's scope covers —
   it's a frontend/event-logging pipeline issue, independent of the
   support-agent system being designed here. This should be flagged to
   whoever owns that pipeline directly, as its own workstream — not solved
   by "redact it on the way out" alone, since that only protects this one
   consumer of the data, not every other system Loki logs already flow
   into today.

---

## 8. `SUPPORT_OPERATIONS_BLUEPRINT.md` §5 "Layer 1.5 — Client Resolution & Tenant Context" — Tenant Context is missing a field

**Current text (lines 206-214):**
```
tenant_id
tenant_name
portal_configuration
api_credentials_reference
enabled_tools
workflow_overrides
```

**Problem:** `api_credentials_reference` implicitly assumes one credential
set per tenant. In reality, per this pass's findings, each tenant has **two
independent credential sets** for two independent systems: the admin-portal
credentials (Unity: username/password -> JWT token) and the Loki/Grafana
credentials (Basic-Auth username/password, plus a per-tenant base URL and
datasource UID) — confirmed different per tenant (SaaS and BOB use
different hosts and different credentials; Canara's correct host is still
unresolved).

**Correction:** add a `log_datasource_reference` field (or generalize
`api_credentials_reference` into a small map keyed by system name) so the
Tenant Context can hold both credential sets side by side without conflating
them.

---

## 9. `SUPPORT_OPERATIONS_BLUEPRINT.md` §12 "Video Analysis Process" — half of this is already solvable today

**Current text (lines 584-604):** marked "Future Capability," lists
example issues (camera, audio, blank screen, blurry image, liveness
failures), no mention of where video files actually live.

**New finding from this pass:** while reading real Loki log samples for
the extraction algorithm, video file **locations** turned out to already be
directly visible in ordinary `agentapi`/celery-worker log lines — S3 object
keys following a consistent pattern:
`videokyc/videos/{client_name}/{phone_number}/{session_id}/{user|agent|agent_video_screen}.webm`,
bucket `kwikid-prod`. This means the "locate the recording" half of video
analysis is not blocked on new capability at all — it falls straight out of
the same log-extraction pipeline being built for text logs (these S3-key
lines would simply be among the lines the relevance extractor surfaces for
a video-related query). Only the "analyze the video content" half (camera
issues, audio issues, liveness failures — genuinely requiring a
vision/audio model) remains a real future capability.

**Correction:** split §12 into two: "Video Location" (near-term — no new
infrastructure needed, just surfacing the existing S3 key from the log
excerpt) and "Video Content Analysis" (future capability, unchanged from
current text).

---

## 10. `SUPPORT_OPERATIONS_BLUEPRINT.md` — missing: Loki data-retention / availability risk

**Not currently mentioned anywhere.** Loki's retention is nominally 7 days
per engineering, but this pass reproduced a case where manual disk-clearing
operations evicted data **earlier** than that nominal window: the exact
same `session_id` + time-window that returned 500+ real log lines earlier
in this pass returned **zero** lines on a later re-query, with the CTO
confirming a disk-clear had happened in between.

**Correction:** add a note (§9 "Session Lookup Process" and/or §21
"Escalation Logic" are both reasonable homes) stating that an empty log
pull is **ambiguous**, not conclusive: it can mean (a) genuinely no backend
activity occurred in that window, or (b) the data has already been evicted
from Loki, independent of the ticket's actual technical merit. The system
must not silently conclude "no technical issue found" purely from an empty
log result, and should surface this ambiguity explicitly in the generated
observation (§14) rather than treating empty-logs and no-issue-found as the
same outcome.

---

## 11. `SUPPORT_OPERATIONS_BLUEPRINT.md` §13 "Reasoning Engine" — input list should reflect the new curated excerpt, not raw logs

**Current text (line 612-618):** lists `Logs` as a bare input alongside
Ticket, Summary, Tool results, SOP knowledge.

**Correction:** per items 6 and 7 above, raw log text should never reach
this layer directly — only the redacted, extracted, ticket-relevant
excerpt should. Recommend renaming this input from `Logs` to something like
`Curated Log Excerpt (PII-redacted, ticket-relevant)` so it's clear at the
architecture level that raw logs are not a valid input here, closing off
the possibility of a future implementation accidentally routing raw Loki
text straight into an LLM prompt.

---

## 12. Summary table: already-built vs. net-new (useful context before rewriting either file)

So the rewritten SOT files don't imply either "everything described here
still needs building" or "the Loki piece is a minor addendum" — neither is
accurate:

| Component | Status |
|:---|:---|
| Unity admin-portal client, token manager, normalizer, session resolver (`unity/*.py`) | **Fully built, production-grade.** Not yet serving live traffic (`DRY_RUN`, blocked on a Freshdesk routing-rule decision, not a code gap). |
| 5 Unity `BaseTool` adapters + registration in `runtime/assembly.py` | **Fully built.** |
| `hybrid_ticket_retriever.py` BM25/RRF fusion (reused for log-relevance ranking) | **Fully built, already running in production** for a different purpose (ticket-to-KB retrieval); reusing its exact scoring logic for logs is new, the scoring logic itself is not. |
| Multi-tenant Loki client (`loki_client.py`): direct-API auth, three-phase cross-service fetch, dedup | **Built and tested this pass, as scratch code — not wired into the production codebase.** |
| PII redaction + BM25 log-relevance extraction (`log_extractor.py`) | **Prototyped and empirically validated this pass, as scratch code — not wired into the production codebase.** |
| End-to-end test harness (`investigate_session.py`) | **Built this pass, as scratch code** — for local testing only, not a production entry point. |
| `GetSessionLogsTool`, `case_engine/integrations/loki/` package, `ToolProvider.LOKI` enum value, `runtime/assembly.py` registration | **Not built. This is the actual net-new coding work** — see `Unity_Loki_Integration_Findings.md` §7 for the precise file-by-file implementation guidance. |
| Uptime Kuma / Metrics Platform integration | Separate system, partially wired (Sprint 2.50) — out of scope for this document, mentioned only to avoid conflating it with Loki. |
| Video content analysis (camera/audio/liveness) | Genuine future capability, unchanged. Video *location* is not (see item 9). |

---

## 13. Files this document assumes the rewriting LLM will also be given

- `Loki_Log_Tool_Blueprint/Unity_Loki_Integration_Findings.md` — full
  technical findings, the target flow diagram, and precise Claude Code
  implementation guidance (exact new files/classes/registration points).
- `Loki_Log_Tool_Blueprint/loki_client.py` — tested multi-tenant Loki
  client (scratch code, logic ready to port).
- `Loki_Log_Tool_Blueprint/log_extractor.py` — tested relevance-extraction
  algorithm (scratch code, logic ready to port), including the empirical
  proof run against a real 500-line session.
- `Loki_Log_Tool_Blueprint/investigate_session.py` — end-to-end interactive
  test harness tying the whole proposed pipeline together locally.
- `Source_Of_Truth/Unity_discovery/*.md` — the original manual API
  discovery docs the real `unity/*.py` module was built from (still
  accurate for the admin-portal side; superseded only where this document
  says so, e.g. item 3's URN correction, which those docs already got
  right).
