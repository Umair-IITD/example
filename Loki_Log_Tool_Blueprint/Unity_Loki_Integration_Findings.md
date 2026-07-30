# Unity Admin Portal -> Loki Integration: Current State & Target Design

Read-only findings for the Claude Code implementation pass. Nothing in the
project codebase was modified to produce this document.

## 0. URGENT: raw customer PII is appearing in plaintext in the Loki logs

While reviewing a real sample pull (session `b96a55e6-...`, CTO-provided), the
`agentapi` application logs contained a full, unredacted KYC data dump for the
customer: Aadhaar number, Aadhaar address, full name, DOB, PAN number, a
**base64-encoded Aadhaar photo**, nominee name/address/DOB, income bracket,
marital status, occupation, and a live NSDL/Aadhaar-OTP verification payload -
all inside a client-side event ("JSON DATA: {...}") that gets shipped straight
into Loki alongside routine navigation events (language selection, camera
permission grants).

This has a direct, immediate implication for the log-fetch tool this document
describes: **raw Loki log text must never be passed to an LLM (or stored, or
displayed to an L1 agent) without a PII-redaction pass first.** This is a
bigger issue than the log-fetch feature itself - the fact that this data
reaches Loki at all (rather than being scrubbed before the client-side event
gets logged) is worth an urgent conversation with whoever owns the Unity
frontend/event-logging pipeline, independent of anything else in this
document. Recommend flagging this to the CTO/security lead directly and
treating it as a blocker for any design where log text flows into an LLM
prompt unfiltered.

## 0b. A second real, reproducible bug found in the same sample

Same session, a genuine AWS error, twice in a row:

```
error while setting the file to public mode An error occurred (AccessDenied)
when calling the PutObjectAcl operation: User: arn:aws:iam::410743434716:user/kwikid
is not authorized to perform: s3:PutObjectAcl on resource:
"arn:aws:s3:::kwikid-prod/videokyc/summary/unity/.../7578939805_aadhaar.xml"
because public ACLs
```

The `kwikid` IAM user is trying to make an S3 object public via `PutObjectAcl`,
and failing because the bucket has public-ACLs blocked (a standard AWS
security setting) - likely happening on every session that generates an
Aadhaar XML summary link. Non-fatal (the pipeline continues past it), but
worth reporting to the dev team alongside the `upload_video_single`
`'number_of_videos_uploaded'`/`'user_url'` KeyErrors found earlier - three
separate, real, reproducible bugs found just from sampling two ordinary
sessions, none of which were the actual reason either ticket was raised.

## 1. Current state: the admin-portal side is already fully built

This was the biggest surprise of this pass: the Unity Admin Portal integration
described in the "true logic" (generate_token -> getAllUserSession -> get_details)
is **not a gap** - it's a complete, production-grade module already sitting at
`kwikid-ai-ingest/ai_project/fumadocs_ingest_service/unity/` (Sprint 2.51):

- `unity/config.py` - `UnityConfig.from_env()`, reads all `UNITY_*` env vars.
- `unity/token_manager.py` - `UnityTokenManager`: calls `POST /v1/agent/generate_token`
  with `{username, password}`, extracts the `Token` field (capital T), caches it,
  decodes the JWT `exp` (unverified) to schedule proactive refresh, invalidates
  and retries once on 401.
- `unity/client.py` - `UnityClient` (async, httpx):
  - `get_all_user_sessions(phone_number)` -> `GET /api/v1/getAllUserSession/{domain}/{phone_number}`
    (singular "Session", `domain` is a path segment, e.g. `unity` - not
    `getAllUserSessions/{user_id}` as originally assumed; it's keyed by phone
    number, not a generic user_id).
  - `get_session_details(session_id)` -> `GET /v1/session/get_details/{session_id}`.
  - Auth uses a **custom `auth: <token>` header**, not `Authorization: Bearer`.
  - Full retry/backoff, typed exceptions (`unity/exceptions.py`), never raises
    anything except those typed exceptions.
- `unity/session_resolver.py` - `select_most_relevant_session()`: given the
  session list for a phone number, already automates the "read the subject of
  all sessions and use human intelligence to pick the right one" step you
  described - it scores by proximity to `ticket_created_at`, preferring
  sessions before the ticket time and non-terminal statuses.
- `unity/normalizer.py` / `unity/models.py` - parse raw JSON into a canonical
  `UnitySession`, with a `TimelineInfo` block:
  `init_time, start_time, end_time, vkyc_start_time, last_active_timestamp,
  agent_assignment_time, removed_from_queue_timestamp` - **all Unix epoch
  seconds (floats)**, not ISO strings, and not every field is populated for
  every session status (e.g. an abandoned session may have `init_time` but no
  `end_time`).
- Wired into 5 `BaseTool` adapters in `case_engine/tools/adapters/unity_tools.py`
  (`GetSessionDetailsTool`, `GetUserDetailsTool`, `GetFailureReasonTool`,
  `GetCaseHistoryTool`, `GetOnboardingStatusTool`), registered at startup in
  `runtime/assembly.py` (~line 385-401), with graceful fallback to the old
  Sprint 2.17 mock tools if Unity credentials/registration fail.

**System status:** still `DRY_RUN` per `CURRENT_STATE.md` - not yet serving
real Unity traffic (Freshdesk routing rule scope is the blocker, not this
module). `.env` currently has `UNITY_USERNAME=unity` / `UNITY_PASSWORD=unity`
- the literal placeholder value, flagged in `.env.example` as needing rotation
before production. Worth confirming with whoever owns this whether that's
actually a working credential (as unlikely as it looks) or genuinely inert,
before assuming a 401 in testing means something else is wrong.

The `Source_Of_Truth/Unity_discovery/` docs (the earlier manual API discovery
that this module was clearly built from) already anticipated exactly this next
step - `investigation_mapping.md` section 6 ("Loki Correlation") and
`limitations.md` section 4 ("What Requires Loki") both flag `session_id` as
the correlator and explicitly note *"Exact Loki job names for Unity VKYC
service are to be confirmed during Loki discovery phase"* - which is what this
whole conversation's Loki work has now done. Their working hypothesis was
`{job="unity-vkyc"}`; live testing this session showed the real shape is
different (see below) - that doc should be updated once the log-fetch piece
lands.

## 1b. `session_status` alone is not a reliable failure signal - and "failure" here has a narrower meaning than KYC rejection

Confirmed against a real example (`43f1b1cd-...`): `session_status` came back
`kyc_result_approved` even though the auditor's own review overrode the
agent's approval with `Rejected` / *"Redo/Reopen - Audio not available (#A1) -
Customer is not audible (duration 1:19 to 2:40 technical issue), REJECT"`.
The real signal here lives in the `auditor` block (`auditor.result`,
`auditor.feedback`/`fdbk_code`), which `unity/normalizer.py`'s
`derive_failure_summary()` currently does not consult at all - it branches
purely on `session.session_status`, so a case exactly like this one would be
categorized as "NONE / APPROVED" and never flagged, despite an explicit
auditor-recorded technical issue.

Important scope correction from the product side: KwikID is a third-party
platform provider, not the bank or the customer - "support" here means
platform/technical issues, not KYC decisions. A rejection for a legitimate
KYC reason (document mismatch, fraud suspicion, customer ineligibility) is
out of scope; a rejection whose reason text indicates a **platform/technical**
problem (audio/video unavailable, connection dropped, upload failure, etc.)
is exactly what this tool should catch. Whoever implements failure-detection
should treat `derive_failure_summary()`'s current status-only branching as
incomplete - it needs to also parse `auditor.feedback` text for
technical-issue language, separately from the KYC-outcome question.

Note also: the two `upload_video_single` KeyErrors found in this same
session's logs (see prior findings) are unrelated to this "audio not
available" issue - they're a separate bug in the video-upload path. The
actual audio-dropout root cause was not found in the sampled logs (`agentapi`,
nginx, and the Celery workers) - it may only be detectable from the recording
itself (which is how the human auditor caught it), or may require checking a
LiveKit/WebRTC-specific log source not yet identified. Worth setting
expectations accordingly: not every auditor-flagged "technical issue" will
have a corresponding backend log entry.

## 2. The actual gap

Nowhere in `unity/*` or `case_engine/tools/adapters/unity_tools.py` is there
any reference to Loki, Grafana, or log fetching. `get_session_details` returns
rich evidence (status, timeline, media URLs, auditor decision, docs/QnA) but
stops short of the underlying application/infra log trail - exactly the gap
you described ("we thought the failure logs would be in the admin portal;
they're actually in Teleport, accessed via the secondary Loki-backed platform").
**This is the one piece that needs building**, not the admin-portal side.

## 3. Target flow (confirmed against real code + real data this session)

```
ticket (user_id and/or session_id from bank agent)
  |
  +-- if only user_id/phone given:
  |     unity.client.get_all_user_sessions(phone_number)          [BUILT]
  |     -> unity.session_resolver.select_most_relevant_session()  [BUILT]
  |
  +-- unity.client.get_session_details(session_id)                [BUILT]
        -> unity.normalizer.parse_session_details(raw)            [BUILT]
        -> UnitySession.timeline.{start_time, end_time, ...}       [BUILT]
              |
              v
        NEW: loki_client.fetch_session_logs(client, session_id,   [BUILT THIS SESSION,
             start, end)                                           not yet wired in]
              -> three-phase cross-service Loki pull (ingress sweep,
                 always-on service_name sweep, server-scoped sweep),
                 de-duplicated -> merged, chronological log text
                 (500+ lines / ~480KB raw for a real 15-minute session -
                 see §0/§6, this is NOT LLM-ready yet, it's PII-bearing
                 and far too large to prompt with directly)
              |
              v
        NEW: extract_relevant_lines(log_text, ticket_query,       [PROTOTYPED THIS
             auditor_feedback, session)                            SESSION, see §6 -
              -> redact PII + truncate oversized lines               not yet ported
              -> composite-score every line (BM25 + error/warn       into the project]
                 force-include + auditor-window force-include +
                 service-hint boost), keep top-K within a char
                 budget, re-sort chronologically
              -> compact, redacted, ticket-relevant excerpt
                 (~8-9KB from a ~480KB session in real testing)
              |
              v
        L1 / L2 pipeline: excerpt + get_session_details evidence +
        ticket text -> LLM reasons -> notes/observations in Freshdesk
```

Key correction to the original plan: the time range does **not** need to be
manually read off the admin portal UI (as was done for `25d747cf-...` to get
this session working end to end) - it's already a first-class field
(`timeline.start_time` / `timeline.end_time`, in Unix epoch seconds) in the
`get_session_details` response that the existing `unity/normalizer.py`
already parses. Once wired, no human/manual timestamp lookup is needed per
ticket.

## 4. Recommended implementation shape (for the Claude Code pass)

- Add a new tool adapter, e.g. `GetSessionLogsTool`, in
  `case_engine/tools/adapters/unity_tools.py` (or a new `log_tools.py` next to
  it, since the Loki-fetch mechanism is bank-agnostic and will eventually
  serve CBI/BOB/others too, not just Unity) - registered in `runtime/assembly.py`
  alongside the existing 5.
- It should accept the `UnitySession` (or just the two epoch timestamps + `session_id`)
  already produced by `GetSessionDetailsTool` in the same investigation run,
  to avoid a redundant Unity API call - `get_session_details` was already
  called once upstream.
- The Loki-side mechanism (tenant registry, direct-vs-proxy endpoint handling,
  two-phase cross-service query, response parser) is already built and tested
  this session in `Loki_Log_Tool_Blueprint/loki_client.py` - port that logic in,
  it doesn't need to be redesigned.
- `SERVICE_NAME_HINTS` in `loki_client.py` was derived from exactly one real
  session's endpoint trail (RBL) plus the `saas` datasource's actual
  `service_name` list - treat it as a starting point to extend, not a finished
  mapping, especially once CBI/BOB data starts flowing through this same path.
- Fallback path: if `timeline.start_time`/`end_time` come back as `0.0` (some
  session statuses don't populate them, e.g. sessions abandoned pre-video),
  fall back to `vkyc_start_time`/`init_time` and `last_active_timestamp` -
  implemented already in `unity_to_loki_test.py`'s `_epoch_to_dt` handling as
  a reference for how the real tool should degrade gracefully rather than fail.

## 5. Open items

- CBI's Loki/Grafana connection details (base URL, datasource UID/name,
  credentials) haven't been provided yet - can't extend the registry to CBI
  until they arrive.
- Whether CBI or BOB have an equivalent "admin portal" (like Unity's) for
  session lookup, or whether the ticket itself must carry the time range for
  those clients, is unconfirmed.
- `UNITY_USERNAME`/`UNITY_PASSWORD=unity/unity` in `.env` needs confirming as
  real-vs-placeholder before relying on it for anything beyond this one test.
- `ToolProvider` (`case_engine/tools/tool_models.py`) has no `LOKI` value yet -
  needs a one-line addition before `GetSessionLogsTool` (§7) can declare its
  provider correctly.
- `_bm25_rerank_inplace` currently lives only inside
  `rag_engine/retrieval/hybrid_ticket_retriever.py` as a private staticmethod.
  Recommend extracting it (plus `_tokenize`) into a small shared
  `rag_engine/retrieval/bm25.py` (or similar) that both the existing
  retriever and the new log-relevance module import, rather than letting a
  second private copy drift out of sync - see §7 for exactly where the new
  code would import it from.

## 6. Cost-free log-relevance extraction - the problem, the design, and empirical proof

### 6.1 The problem, quantified on a real session

You flagged this yourself: session `b96a55e6` alone pulled back 509 lines of
merged Loki text. Concretely, once converted from `loki_client.py`'s output
into UTF-8: **500 real log lines, 483,926 characters (~473KB)** for a single
~15-minute VKYC session. Posting that whole blob into an LLM prompt on every
single ticket is wrong on three independent grounds, not just one:

1. **Cost/latency** - ~473KB is roughly 120-150K tokens of raw text (before
   the model even starts reasoning), on *every* investigation, regardless of
   ticket volume. That's a real, recurring, per-ticket bill.
2. **Compliance** - §0 already flagged that this same session's logs contain
   an unredacted Aadhaar photo and other PII. Concretely: **one single log
   line** (a `[LOG_DECORATOR] Request is form: ...` event dumping
   `summary_data`) is **110,235 characters by itself** - almost entirely a
   base64-encoded Aadhaar image. That line alone is worth confirming: it is
   not a rare/edge event, it fires on essentially every KYC submission,
   which means every single full-log pull carries this risk, not just this
   one sample.
3. **Signal dilution** - even ignoring cost and PII, 500 lines of mostly
   queue-polling heartbeats (`getcurrentstatus` fires every ~2 seconds) and
   routine navigation telemetry drown out the handful of lines that actually
   explain what went wrong. An LLM asked to "find the bug in these 500
   lines" performs worse than one handed 30 pre-filtered, relevant lines -
   this is a retrieval-quality problem, not just a cost problem.

### 6.2 The design: a zero-marginal-cost composite relevance score

Per-line (not per-chunk - see §6.4 for why line-granularity was chosen),
computed entirely in local Python with no network call and no paid API:

```
score(line) = bm25(ticket_query_text, line.content)      # lexical match
            + LEVEL_WEIGHT[line.level]                    # +8 error, +4 warn, 0 info/unknown, -1 debug
            + SERVICE_HINT_BOOST if line.service_name
              is among services guessed from ticket text   # via a keyword->service map

force_include(line) = True if line.level in {error, critical}
                    or line.timestamp falls inside the
                       auditor-feedback sub-window (see §6.3), if derivable
```

Every line is **redacted and truncated to 500 characters before scoring or
selection** (not after) - this matters twice over: it stops the one
110KB PII-bearing line from single-handedly consuming the entire output
budget (confirmed as a real failure mode during prototyping - see §6.4),
and it means the same transform that fixes the size problem also fixes the
PII problem, for free, as one step.

Force-included lines (errors, auditor window) are always kept; the
remaining budget is filled by descending composite score up to a character
budget (prototyped at 9,000 chars - tune per model context budget), then the
final selection is re-sorted chronologically so the excerpt still reads
top-to-bottom the way a human would.

**Why BM25 and not embeddings:** the project already has an embedding
provider (`rag_engine/embedding/openai_provider.py`), but it is
OpenAI-only - every embedding call costs money and a network round-trip,
and would need to run **per ticket, per session, on every line**, which
directly contradicts the "cost-effective, ideally free" requirement. There
is no local/free embedding provider in the codebase today (`base.py`'s
docstring even lists a future `LocalEmbeddingProvider (sentence-transformers)`
that was never built). BM25 has none of that cost, and empirically (§6.4)
it already bridges the exact vocabulary gap embeddings would be used for:
ticket text like "audio" or "disconnected" matched real event codes like
`UP_PROC_MEDIA_LK_AUDIO_TRACK_UNMUTED` and `UP_PROC_MEDIA_LK_ROOM_DISCONNECTED`
because the tokenizer (borrowed as-is from `hybrid_ticket_retriever.py`)
splits on non-alphanumeric characters, so `UP_PROC_MEDIA_LK_AUDIO_...`
tokenizes into `up`, `proc`, `media`, `lk`, `audio`, ... - the underscore-
delimited event-code convention this codebase already uses turns out to
make lexical matching work almost as well as semantic matching would, at
zero cost. Recommendation: ship BM25-only for v1; only revisit local
embeddings (which *would* be genuinely free - `sentence-transformers` runs
fully offline once the model weights are downloaded once) if live
production evaluation later surfaces real queries BM25 misses.

The BM25 scoring itself is not new design - it's a direct reuse of
`HybridTicketRetriever._bm25_rerank_inplace` (`rag_engine/retrieval/
hybrid_ticket_retriever.py`, ~lines 656-679): proper IDF + BM25 length
normalization (k1=1.5, b=0.75), already tested and running in production
for ticket-to-knowledge-base retrieval. This reuses the exact same math for
ticket-to-log-line retrieval instead - same problem shape (rank documents
against a short natural-language query), same zero-dependency
implementation, no new library, no new cost.

### 6.3 The auditor-feedback sub-window

§1b already found that real technical-issue rejections are only visible in
`auditor.feedback` text, e.g. *"Customer is not audible (duration 1:19 to
2:40 technical issue)"*. That duration is relative to the video start, not
an absolute timestamp - so it needs converting via the same
`timeline.vkyc_start_time` (or `start_time`) the admin portal already
returns: `window = (vkyc_start_time + 79s, vkyc_start_time + 160s)` for
"1:19 to 2:40". A small regex (`duration\s+(\d+):(\d+)\s+to\s+(\d+):(\d+)`)
against `auditor.feedback` is enough to extract this automatically whenever
it's present, and every log line whose timestamp falls in that window
should be force-included regardless of its BM25/level score - this is the
one place a human auditor already pinpointed the exact moment of a
technical failure, so the extraction should never risk scoring it out.

### 6.4 Empirical proof (real data, not a synthetic example)

Prototyped in `Loki_Log_Tool_Blueprint/log_extractor.py` (scratch test
script, not part of the project) and run against the real 500-line/473KB
`b96a55e6` sample (`logs1.txt` in this same folder) with three different
simulated ticket queries, to prove the algorithm actually discriminates by
query rather than always returning the same "generically important" lines:

| Ticket query (simulated) | Output | Reduction | What got surfaced (query-specific) |
|:---|:---|:---|:---|
| "video did not upload after the KYC call" | 46 lines / 8,578 chars | -90.8% lines / -98.2% chars | The exact `upload_video_single` KeyError chain (`'number_of_videos_uploaded'`, `'user_url'`, `'agent_url'`) plus the Celery worker's S3 retry warnings (`File does not exist locally. Downloading...`, `Uploading seekable video to S3...`) - i.e. the real, reproducible upload bug already flagged in §0b, surfaced automatically. |
| "bank agent rejected the KYC, customer disputing the reason" | 26 lines / 8,988 chars | -94.8% lines / -98.1% chars | The `KYC_REQUEST_REJECTED` / `GETCURRENTSTATUS:..._KYC_RESULT_REJECTED` event pair and the `set_kyc_result` "Feedback processing completed - feedback_reason: Any Other Non-Technical Issues" line - exactly the rejection-reason evidence a human would need, and (per §1b/scope note) exactly the kind of feedback text that determines in/out of scope. |
| "customer says audio was not working and the call disconnected" | 42 lines / 8,953 chars | -91.6% lines / -98.1% chars | `UP_PROC_MEDIA_LK_AUDIO_TRACK_UNMUTED`, `UP_PROC_MEDIA_AGENT_AUDIO_ELEMENT_MUTED_CHANGED`, `UP_PROC_MEDIA_LK_ROOM_DISCONNECTED` - the actual media-state event trail, none of which were prioritized in the other two runs. |

All three runs also correctly kept every genuine `detected_level=error` line
(the two nginx "access forbidden by rule" errors and the three
`upload_video_single` KeyErrors) regardless of query, since those are
force-included - and all three runs redacted the 110,235-character PII line
down to its first ~500 characters (session/agent/journey IDs survive;
the Aadhaar photo and downstream PII fields do not).

One real bug surfaced *during this prototyping itself*, worth recording:
the first version force-included all error-level lines but used the
**original, un-redacted** line length for budget accounting - since the one
`[LOG_DECORATOR]` line is 110KB, it alone blew through the entire char
budget and silently starved every other line (every run showed "0 lines
kept by score" until this was fixed). Fixed by redacting/truncating each
line *before* it enters budget accounting, not after selection - this is
now reflected in the design in §6.2 and is worth calling out explicitly to
Claude Code, since it's an easy mistake to reintroduce if the redaction
step is ever refactored to run "just before the LLM call" instead of "as
part of the line's canonical representation."

## 7. Precise implementation guidance for Claude Code

Everything below is a recommendation for a future coding pass - nothing in
this section has been implemented in the real project; `log_extractor.py`
and `loki_client.py` are both scratch/test code living outside it.

1. **Add `ToolProvider.LOKI = "LOKI"`** to the enum in
   `case_engine/tools/tool_models.py` (one line, alongside `UNITY`,
   `ADMIN_PORTAL`, etc.) - needed for the new tool's `definition.provider`.

2. **New package `case_engine/integrations/loki/`** (sibling to the existing
   top-level `unity/` package, same reasoning: it's a client library, not a
   tool adapter) containing the logic already built/tested in
   `Loki_Log_Tool_Blueprint/loki_client.py` this session: `LokiDatasource`,
   `LOKI_REGISTRY`, `LokiClient`, `fetch_session_logs`,
   `SERVICE_NAME_HINTS`/`guess_service_candidates`, `parse_loki_response`.
   Port logic, don't redesign it - it's already been fixed through three
   real bugs this session (proxy-vs-direct endpoints, the phase-2 gating
   bug, missing phase-3 server-scoped sweep + dedup).

3. **New module `case_engine/integrations/loki/relevance.py`** implementing
   the §6.2 algorithm, ported from `Loki_Log_Tool_Blueprint/log_extractor.py`
   this session. Specifically:
   - Import BM25 from the project rather than duplicating it. Recommend
     extracting `_tokenize` + `_bm25_rerank_inplace` out of
     `rag_engine/retrieval/hybrid_ticket_retriever.py` into a small shared
     `rag_engine/retrieval/bm25.py`, and have both the existing retriever
     and this new module import from there - avoids two private BM25
     copies silently drifting apart over time.
   - Keep the PII-redaction + 500-char truncation step exactly as
     prototyped, and keep it *first* (before scoring/budget accounting) -
     see the §6.4 bug for why order matters here.
   - Keep the auditor-feedback sub-window parser (§6.3) as a small,
     independently testable function, e.g.
     `parse_auditor_duration_window(feedback_text, vkyc_start_epoch) ->
     tuple[datetime, datetime] | None` - it has a narrow, regex-based
     contract that's easy to unit test in isolation.

4. **New tool adapter `GetSessionLogsTool`** in a new
   `case_engine/tools/adapters/log_tools.py` (sibling to `unity_tools.py`,
   same file layout/conventions: `BaseTool` subclass, `TOOL_NAME` constant,
   `definition` property, never-raising `run()` returning an evidence dict).
   - `provider=ToolProvider.LOKI`, `capability=ToolCapability.READ`.
   - Inputs: `session_id` (required), plus optional passthrough
     `start_epoch`/`end_epoch`, `ticket_query_text`, `tenant` (default
     `"saas"`), `auditor_feedback_text`.
   - Should accept the `UnitySession` (or just its two timeline epochs +
     `auditor.feedback`) already produced by `GetSessionDetailsTool` earlier
     in the same investigation run, to avoid calling Unity's admin portal a
     second time for information it already returned - the investigation
     layer should thread that through rather than have this tool re-fetch
     it.
   - Internally: `fetch_session_logs()` (raw merged text) ->
     `extract_relevant_lines()` (§6.2/§7.3) -> return the compact, redacted
     excerpt as the tool's payload, never the raw pull. The raw pull should
     not be persisted, traced, or returned anywhere - only the redacted
     excerpt should ever leave this tool, consistent with the PII rule in
     §0.
   - `evidence_available`: `AVAILABLE` (excerpt produced), `PARTIAL` (Loki
     reachable but zero matching lines - a real, already-observed outcome
     documented earlier this session as sometimes meaning genuine data
     eviction, not a bug), `UNAVAILABLE` (Loki call failed), `DISABLED`
     (config flag off) - same four-state contract the Unity tools already
     use, so the investigation layer treats this tool identically to the
     other five.

5. **Register in `runtime/assembly.py`** alongside the existing
   `register_unity_tools()` call (~line 385-401): add a matching
   `register_loki_tools(registry, config=...)` following the exact same
   registration pattern (try/except per tool, `outcomes` dict, optional
   `register_capability` call) already used for the five Unity tools.

## 8. Summary of what's new vs. what already existed, going into a coding pass

- **Already built, do not redesign:** the entire `unity/*` package and its
  5 `BaseTool` adapters (§1); `hybrid_ticket_retriever.py`'s BM25/RRF
  fusion (§6.2); the `BaseTool`/`ToolDefinition`/`ToolResult` framework
  contract (§7) - all confirmed by direct reading of the real code, not
  assumed.
- **Built and tested this session as scratch/test code, ready to port
  as-is:** `loki_client.py` (direct-Loki multi-tenant client, three-phase
  fetch, dedup) and `log_extractor.py` (relevance extraction, empirically
  validated in §6.4 against a real 500-line/473KB session).
- **Net new, needs writing:** `case_engine/integrations/loki/` package,
  `relevance.py`, `GetSessionLogsTool` + `log_tools.py`, the
  `ToolProvider.LOKI` enum value, the `rag_engine/retrieval/bm25.py`
  extraction (optional but recommended), and the `runtime/assembly.py`
  registration call - all specified precisely in §7.
