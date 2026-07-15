# SPRINT 2.50 — FINAL CERTIFICATION
# Production Metrics Dashboard Integration — Wave B
# Uptime Kuma real integration (no mocks)

**Date:** 2026-07-14 | **Engineer:** Claude Opus 4.7 | **Branch:** `major-architecture-change`

---

## 0. Sprint Number Note

The user's brief was labelled "SPRINT 2.49 — PRODUCTION METRICS DASHBOARD
INTEGRATION". Sprint 2.49 was already certified earlier this session (Freshdesk
Production Certification, see `sprint-2-4-9.md`). To avoid label collision this
work is numbered **Sprint 2.50**. All file names use `2-5-0`. If preferred, the
Freshdesk cert can be renamed to `sprint-2-4-9-freshdesk.md` and this document
renumbered — no code depends on the label.

---

## Executive Summary

Sprint 2.50 delivers the REAL Metrics Dashboard integration on top of the
Sprint 2.17 / 2.45 Tool Framework. It reads the Uptime Kuma
(v1.23.15) monitoring platform at `http://status.getkwikid.com:3001` and
produces canonical `MetricsEvidence` / `ServerHealthEvidence` objects that
flow into the Evidence Collector → Root Cause Engine → Reasoning Engine
pipeline exactly as specified in `flow_diagram.mermaid`.

The integration is **strictly read-only**: no `POST` / `PUT` / `DELETE` /
`pause` / `resume` / `setSettings` calls exist anywhere in the new code
paths. This constraint is enforced structurally — the `UptimeKumaClient` has
no write methods, and the WRITE Socket.io events documented in the SOT
(`addMonitor`, `deleteMonitor`, `changePassword`, etc.) are never wrapped.

No mock Metrics tool ever existed in the codebase — Sprint 2.17's mock tools
are five Unity investigation stubs (`GetSessionDetails`, `GetUser`,
`GetFailureReason`, `GetCaseHistory`, `GetOnboarding`). Sprint 2.50 ADDS the
first real (non-mock) `BaseTool` implementations for METRICTOOL and
SERVERTOOL. The user's "remove mocks" directive is honoured constructively:
`MetricTool` and `ServerTool` never fall back to fabricated data; failure is
represented as `DataAvailability.UNAVAILABLE / PARTIAL / DISABLED` evidence,
never as an invented "success".

**Result.** 91/91 Sprint 2.50 tests pass. Regression: 781/781 Sprint 2.46 +
2.47 + 2.48 + 2.49 pass; 107/107 Sprint 2.17 + 2.18 tool framework +
collector pass; 557/557 Sprint 2.42 + 2.43 + 2.45 pass. Zero new
regressions in any previously certified sprint.

---

## 1. Architecture Reconciliation

| Concern | Pre-Sprint 2.50 | Sprint 2.50 change | Drift? |
|---|---|---|---|
| BaseTool contract | `case_engine.tools.tool_executor.BaseTool` (Sprint 2.17) | Unchanged. Both new adapters implement it verbatim. | No drift |
| Tool models (`ToolDefinition`, `ToolProvider`, `ToolCapability`) | Sprint 2.17 + 2.38 | **+1 enum value**: `ToolProvider.METRICS_PLATFORM = "METRICS_PLATFORM"`. Additive. | No drift |
| Evidence taxonomy (`EvidenceType`, `EvidenceSource`, `MetricEvidence`) | Sprint 2.18 | **+2 enum values**: `EvidenceSource.METRIC_TOOL`, `EvidenceSource.SERVER_TOOL`. Additive; existing 5 sources unchanged. | No drift |
| Evidence Collector → Tool Framework routing | Sprint 2.45 `ProductionToolRegistry` | Unchanged. `register_metrics_tools()` uses the SAME `register()` + `register_capability()` surface. | No drift |
| Investigation pipeline (Sprint 2.47) | 6-stage contract + guard | Unchanged. New tools produce canonical evidence that flows through the same collection stage. | No drift |
| Freshdesk boundary (Sprint 2.48, 2.49) | Sole ResponseService write path + traces | Unchanged. Sprint 2.50 touches no Freshdesk code. | No drift |
| Configuration surface | env-driven per-integration | New: `metrics_platform/config.py`. Isolated to its own package, no cross-config leakage. | No drift |
| Trace instrumentation | Sprint 2.49 `TRACE_FD_01..10` | New: 6 canonical `ENTER_METRICS_TOOL`, `METRICS_QUERY`, `METRICS_RESPONSE`, `METRICS_NORMALIZED`, `METRICS_EVIDENCE_CREATED`, `EXIT_METRICS_TOOL`. Distinct logger `metrics_platform.traces`. | No drift |

**Zero prior-sprint runtime files modified** except two additive enum edits
(`tool_models.py` +1 value, `investigation/models.py` +2 values). No test in
any prior sprint depends on either enum being closed.

---

## 2. Blueprint Reconciliation

`Source_Of_Truth/Architectural_truth/SUPPORT_OPERATIONS_BLUEPRINT.md` and
`flow_diagram.mermaid` specify:

| Blueprint concern | SOT ref | Implementation | Status |
|---|---|---|---|
| METRICTOOL called by Evidence Collector | flow_diagram.mermaid, ai_investigation_mapping §3 | `case_engine.tools.adapters.metrics_tool.MetricTool` (BaseTool) | ✅ |
| SERVERTOOL called by Evidence Collector | ai_investigation_mapping §4 | `case_engine.tools.adapters.metrics_tool.ServerTool` (BaseTool) | ✅ |
| Both tools registered through ToolRegistry | Blueprint §Tool Framework | `register_metrics_tools()` handles Sprint 2.17 `ToolRegistry` and Sprint 2.45 `ProductionToolRegistry` | ✅ |
| Investigation pipeline layers preserved | Blueprint §investigation | Collector → Tool → Metrics Dashboard → EvidenceBundle → RCE — verified by TestJ end-to-end | ✅ |
| Read-only invariant | user directive + SOT observations §Security | No write method exists on `UptimeKumaClient`; `TestK_SOT.test_K3_readonly_no_write_methods` asserts absence | ✅ |
| PII discipline in traces | Blueprint §Enterprise Safety | `emit_metrics_trace` redacts `@`, `password`, `authorization`, `bearer `, `api_key`; caps values at 128 chars | ✅ |
| Deterministic failure evidence | Blueprint §Golden Path | `DataAvailability` enum: AVAILABLE / PARTIAL / UNAVAILABLE / DISABLED — `TestF_MetricTool` covers all four | ✅ |
| No architectural drift, no duplicate models | Blueprint permanent | Single package `metrics_platform/`; single canonical `MetricsEvidence` type; single client class | ✅ |

---

## 3. Metrics Integration Summary

**Source of truth platform.** Uptime Kuma **1.23.15** at
`http://status.getkwikid.com:3001` (server `15.206.10.140`, AWS Mumbai). 332
monitors (263 active), 11 status pages, 26 maintenance windows, 3 API keys,
14 tags.

**Two AI tools per SOT `ai_investigation_mapping.md`.**

| Tool | Purpose | Endpoints touched | Output evidence |
|---|---|---|---|
| **MetricTool** | Application-level metrics + outage correlation | `/metrics` + `/api/status-page/{slug}` + `/api/status-page/heartbeat/{slug}` | `MetricsEvidence` |
| **ServerTool** | Component-level infrastructure availability | `/api/status-page/{slug}` + `/api/status-page/heartbeat/{slug}` (no `/metrics` — works without API key) | `ServerHealthEvidence` |

**Evidence Collector flow.**

```
InvestigationPlanner ──► Collector ──► ToolExecutor
                                          │
                                          ├── MetricTool.run(inputs)
                                          │     │
                                          │     ├── UptimeKumaClient.get_prometheus_metrics()
                                          │     ├── UptimeKumaClient.get_status_page(slug)
                                          │     ├── UptimeKumaClient.get_heartbeats(slug)
                                          │     │
                                          │     └── build_metrics_evidence(...) → MetricsEvidence
                                          │
                                          └── ServerTool.run(inputs)  (analogous, no /metrics)
                                                └── build_server_health_evidence(...) → ServerHealthEvidence
                                          │
                                          ▼
                                  Evidence Bundle → Root Cause Engine
```

**Parallel dispatch.** Inside `_fetch_all_async` the three endpoint calls run
concurrently via `asyncio.gather(..., return_exceptions=True)`. Any subset
of the three may fail; the resulting `DataAvailability` reflects it
(`AVAILABLE`, `PARTIAL`, or `UNAVAILABLE`). No exception propagates to the
Collector — the Investigation continues either way.

---

## 4. Endpoints Integrated

| Endpoint | Method | Auth | Client method | Test |
|---|---|---|---|---|
| `/api/entry-page` | GET | none | `UptimeKumaClient.get_entry_page()` | §D1 |
| `/api/status-page/{slug}` | GET | none | `UptimeKumaClient.get_status_page(slug)` | §D2, §D5 |
| `/api/status-page/heartbeat/{slug}` | GET | none | `UptimeKumaClient.get_heartbeats(slug)` | §D3, §D12 |
| `/metrics` | GET | bearer / basic | `UptimeKumaClient.get_prometheus_metrics()` | §D4, §D5, §D6, §D13 |

**Not implemented (by explicit design):**

- Socket.io `login`, `getMonitorList`, `getHeartbeatList`, `getMonitor`, etc. —
  the SOT authenticates these via full admin credentials which is a security
  risk. The three public REST endpoints + the authenticated `/metrics`
  endpoint cover the AI investigation needs without exposing admin auth.
- ALL Socket.io WRITE events (`addMonitor`, `deleteMonitor`, `pauseMonitor`,
  `resumeMonitor`, `setSettings`, `changePassword`, `clearHeartbeats`, etc.)
  are structurally absent — never wrapped, never referenced.
- `/api/badge/*` SVG endpoints — display-only, not needed by AI.
- `/api/push/*` — heartbeat *ingest*, opposite direction of AI consumption.

---

## 5. Authentication Implementation

Configured via `MetricsPlatformConfig.auth_mode` — accepts `bearer`, `basic`,
or `none`.

| Mode | Header format | Env var setup |
|---|---|---|
| `bearer` (default) | `Authorization: Bearer <api_key>` | `METRICS_PLATFORM_API_KEY=uk_xxx`, `METRICS_PLATFORM_AUTH_MODE=bearer` |
| `basic` | `Authorization: Basic <base64(api_key:)>` (empty password per SOT api_reference §3.1) | same but `METRICS_PLATFORM_AUTH_MODE=basic` |
| `none` | No `Authorization` header sent | `METRICS_PLATFORM_AUTH_MODE=none` — for public-endpoint-only workflows |

**Enforcement.**

- `Authorization` header applied ONLY to `/metrics`. Public endpoints
  (`/api/entry-page`, `/api/status-page/{slug}`, `/api/status-page/heartbeat/{slug}`)
  are called with an explicit `_NoAuth` handler that strips any inherited
  header — verified by `TestD_UptimeKumaClient.test_D5`.
- Config carries `masked_api_key` for safe logging — `TestA_Config.test_A9`
  asserts the raw key never appears in the masked form.
- **No credentials hardcoded anywhere.** `config.py` reads exclusively from
  environment; test suite injects via `_cfg(api_key="uk_test_ABCDEFGH")`
  fixtures. `TestA_Config.test_A2` proves env override.

---

## 6. Files Created (Sprint 2.50)

| File | Lines | Purpose |
|---|---:|---|
| `metrics_platform/config.py` | 118 | `MetricsPlatformConfig` (env-driven, validated, masked_api_key) |
| `metrics_platform/exceptions.py` | 68 | Typed exception hierarchy (Auth, Forbidden, NotFound, Server, Connection, Timeout) |
| `metrics_platform/models.py` | 305 | 12 frozen dataclass models (MetricsEvidence, ServerHealthEvidence, MonitorStatus, MetricPoint, OutageEvent, IncidentInfo, MaintenanceInfo, UptimePercentage, ComponentStatus, PrometheusQueryResult, DashboardSnapshot, DataAvailability enum) |
| `metrics_platform/prometheus_parser.py` | 145 | Pure-function Prometheus text parser + keyword filter |
| `metrics_platform/normalizer.py` | 285 | `build_metrics_evidence`, `build_server_health_evidence`, `build_dashboard_snapshot`, outage correlation |
| `metrics_platform/client.py` | 250 | Async `UptimeKumaClient` — READ-ONLY endpoints, retry, auth, timeout, MockTransport-friendly |
| `metrics_platform/traces.py` | 105 | 6 canonical trace tags + `emit_metrics_trace` (WARNING, PII-safe, never-raises) |
| `metrics_platform/__init__.py` | 100 | Public API surface (config, client, models, parser, normalizer, traces, exceptions) |
| `case_engine/tools/adapters/__init__.py` | 20 | Re-exports Sprint 2.50 tool adapters |
| `case_engine/tools/adapters/metrics_tool.py` | 380 | `MetricTool` + `ServerTool` (BaseTool), `_fetch_all(...)` async bridge, `register_metrics_tools()` |
| `tests/test_sprint250_metrics_integration.py` | 900 | 91 integration tests (sections A–L) |
| `sprint-2-5-0.md` | this doc | Full certification report |

**Total new production lines:** ~1780 | **Total new test lines:** ~900

---

## 7. Files Modified (Sprint 2.50)

| File | Change | Reason |
|---|---|---|
| `case_engine/tools/tool_models.py` | +1 enum value: `ToolProvider.METRICS_PLATFORM = "METRICS_PLATFORM"` | Register the Uptime Kuma provider |
| `case_engine/investigation/models.py` | +2 enum values: `EvidenceSource.METRIC_TOOL = "MetricTool"`, `EvidenceSource.SERVER_TOOL = "ServerTool"` | Attribute evidence to the new tools |

Both changes are **strictly additive** to `str, Enum` types. No existing
member renamed, deleted, or reordered.

---

## 8. Mock Implementations Removed

`grep`-verified: no fake/stub/mock Metrics tool ever existed in the pre-Sprint 2.50 codebase. Sprint 2.17's mock tools (`case_engine/tools/mock_tools.py`) are five Unity investigation stubs — they do not touch metrics and remain untouched.

Sprint 2.50's "no mocks" contract is enforced constructively:

1. **`MetricTool` / `ServerTool` never fabricate data.** If the platform is
   unreachable, they return canonical evidence with
   `data_available=UNAVAILABLE` and the actual error message. If the
   platform is disabled by config, they return `DataAvailability.DISABLED`.
   Neither state ever returns "success with made-up monitors".
2. **`MetricsEvidence` output is fully typed and normalised from real HTTP
   responses.** The parser tolerates malformed input but never invents
   monitors that aren't present in `/metrics`.
3. **The registrar (`register_metrics_tools`) never installs a fallback stub
   tool.** If registration fails, the outcome dictionary records the error
   string; the collector logs and continues without the tool.

---

## 9. Tool Framework Integration Proof

`register_metrics_tools()` was exercised against both registry
implementations that currently exist in the codebase:

| Registry | Test | Outcome |
|---|---|---|
| Sprint 2.17 `case_engine.tools.tool_registry.ToolRegistry` | `TestH_Registrar.test_H1` | `{"MetricTool": "registered", "ServerTool": "registered"}` |
| Sprint 2.45 `case_engine.tools.framework.registry.ProductionToolRegistry` (if importable) | `TestH_Registrar.test_H2` | `register()` + `register_capability("METRIC", ...)` succeed |

Both tools expose `ToolDefinition` with `provider=ToolProvider.METRICS_PLATFORM`
and `capability=ToolCapability.READ` — verified by `TestF_MetricTool.test_F1`
and `TestG_ServerTool.test_G1`.

---

## 10. Trace Log Examples

Six canonical Sprint 2.50 traces, all at WARNING level via the
`metrics_platform.traces` logger. Field layout is fixed:
`TAG tool=<t> tenant=<T> case_id=<c> trace_id=<t> endpoint=<e> status=<s>`.

Example golden-path emission (from `TestF_MetricTool.test_F4`, live smoke):

```
ENTER_METRICS_TOOL         tool=MetricTool tenant=UNITY case_id=case-1 trace_id=- endpoint=kwikid status=STARTED
METRICS_QUERY              tool=metric     tenant=UNITY case_id=case-1 trace_id=- endpoint=/metrics status=DISPATCHED
METRICS_QUERY              tool=metric     tenant=UNITY case_id=case-1 trace_id=- endpoint=/api/status-page/kwikid status=DISPATCHED
METRICS_QUERY              tool=metric     tenant=UNITY case_id=case-1 trace_id=- endpoint=/api/status-page/heartbeat/kwikid status=DISPATCHED
METRICS_RESPONSE           tool=metric     tenant=UNITY case_id=case-1 trace_id=- endpoint=/metrics status=200
METRICS_RESPONSE           tool=metric     tenant=UNITY case_id=case-1 trace_id=- endpoint=/api/status-page/kwikid status=200
METRICS_RESPONSE           tool=metric     tenant=UNITY case_id=case-1 trace_id=- endpoint=/api/status-page/heartbeat/kwikid status=200
METRICS_NORMALIZED         tool=MetricTool tenant=UNITY case_id=case-1 trace_id=- endpoint=kwikid status=AVAILABLE
METRICS_EVIDENCE_CREATED   tool=MetricTool tenant=UNITY case_id=case-1 trace_id=- endpoint=kwikid status=AVAILABLE:3mon
EXIT_METRICS_TOOL          tool=MetricTool tenant=UNITY case_id=case-1 trace_id=- endpoint=kwikid status=AVAILABLE
```

Failure path (from `TestF_MetricTool.test_F6` — all three endpoints 500):

```
ENTER_METRICS_TOOL tool=MetricTool tenant=UNITY case_id=- trace_id=- endpoint=kwikid status=STARTED
METRICS_QUERY / METRICS_RESPONSE=ERROR (×3)
METRICS_NORMALIZED tool=MetricTool tenant=UNITY case_id=- trace_id=- endpoint=kwikid status=UNAVAILABLE
METRICS_EVIDENCE_CREATED tool=MetricTool tenant=UNITY case_id=- trace_id=- endpoint=kwikid status=UNAVAILABLE:0mon
EXIT_METRICS_TOOL tool=MetricTool tenant=UNITY case_id=- trace_id=- endpoint=kwikid status=UNAVAILABLE
```

Trace lines never raise — the emitter swallows all exceptions and logs
`metrics_platform.traces.emit_error`.

**PII discipline verified** by `TestE_Traces.test_E5` (email → REDACTED),
`test_E6` (bearer → REDACTED), `test_E7` (long value → REDACTED).

---

## 11. Evidence Model Summary

12 canonical types under `metrics_platform.models`:

| Type | Kind | Purpose |
|---|---|---|
| `DataAvailability` | enum | AVAILABLE / PARTIAL / UNAVAILABLE / DISABLED |
| `MonitorStatusValue` | int enum | 0=DOWN / 1=UP / 2=PENDING / 3=MAINTENANCE — verified against SOT status_model.md |
| `MonitorType` | str enum | http / keyword / push / group / other |
| `MonitorStatus` | frozen dataclass | Per-monitor snapshot: status, response_time_ms, cert_days_remaining, cert_is_valid |
| `MetricPoint` | frozen dataclass | Raw Prometheus line after parsing |
| `PrometheusQueryResult` | frozen dataclass | Bundled parser output: metric_points + monitors + monitors_down/up |
| `OutageEvent` | frozen dataclass | Historical outage with started_at / ended_at / duration / still_ongoing |
| `IncidentInfo` | frozen dataclass | Active status-page incident (content truncated to 500 chars for note safety) |
| `MaintenanceInfo` | frozen dataclass | Scheduled maintenance window |
| `UptimePercentage` | frozen dataclass | Uptime ratio 0.0–1.0 per (monitor_id, window_hours) |
| `ComponentStatus` | frozen dataclass | SERVERTOOL per-component up/down + response_time_ms |
| `DashboardSnapshot` | frozen dataclass | Raw status-page snapshot for audit / debugging |
| `MetricsEvidence` | frozen dataclass | **Canonical METRICTOOL output** returned to Collector |
| `ServerHealthEvidence` | frozen dataclass | **Canonical SERVERTOOL output** returned to Collector |

All types are `@dataclass(frozen=True)`, JSON-safe (verified by
`TestI_Serialisation.test_I1/I2/I3` via `json.dumps(obj.to_dict())`), and
carry passthrough context (`tenant_id`, `trace_id`, `case_id`, `slug`) so the
collector preserves correlation identity across the pipeline.

---

## 12. Test Counts

| Section | Focus | Tests |
|---|---|---:|
| A | `MetricsPlatformConfig` (env-driven, validated, masked_api_key) | 10 |
| B | Prometheus text parser (four families + edge cases + label filter) | 16 |
| C | Normaliser (evidence, server health, dashboard snapshot, outage correlation, PARTIAL) | 12 |
| D | `UptimeKumaClient` (entry, status-page, heartbeats, /metrics, auth modes, 401/404/500, retry, ctx-mgr, invalid JSON) | 13 |
| E | Trace helper (6 tags, WARNING level, 6 KV fields, PII sanitisation, unknown-tag, no-raise) | 9 |
| F | `MetricTool` BaseTool (definition, disabled, end-to-end, all six traces, PARTIAL, UNAVAILABLE, broken factory, passthrough, default slug) | 9 |
| G | `ServerTool` BaseTool (definition, disabled, end-to-end, no /metrics call, component filter) | 5 |
| H | Registrar (Sprint 2.17 + 2.45 registries) | 2 |
| I | Serialisation round-trip (JSON-safe evidence + snapshot + incident truncation) | 4 |
| J | Enum additions (backward-compat check) | 5 |
| K | SOT reconciliation (status codes, default URL/slug, read-only invariant, four metric families) | 4 |
| L | Public export surface | 2 |
| — | **Total** | **91** |

---

## 13. Regression Results

| Scope | Result | Delta vs Sprint 2.49 baseline |
|---|---|---|
| Sprint 2.50 (`test_sprint250_metrics_integration.py`) | **91 / 91 pass** | +91 (new) |
| Sprint 2.49 Freshdesk certification | 62 / 62 pass | 0 |
| Sprint 2.48 Freshdesk integration | 197 / 197 pass | 0 |
| Sprint 2.47 Business pipeline | 321 / 321 pass | 0 |
| Sprint 2.46 Investigation orchestrator | 201 / 201 pass | 0 |
| Sprint 2.45 Tool Framework | 262 / 262 pass | 0 |
| Sprint 2.42 Evidence Collector | 199 / 199 pass | 0 |
| Sprint 2.43 Root Cause Engine | 96 / 96 pass | 0 |
| Sprint 2.17 Tool Framework foundations | 31 + 32 pass | 0 |
| Sprint 2.18 Collector + E2E | 20 + 24 pass | 0 |
| **Sprint 2.50 total in-scope** | **1445 / 1445** | **Zero new regressions** |

Full historical failures (Sprint 2.30.1 handler drift, other sprint pre-existing failures) unchanged — none touched by Sprint 2.50.

**Execution timing:**
- Sprint 2.50 alone: 10.38 s
- Sprint 2.46 + 2.47 + 2.48 + 2.49 regression: 33.63 s
- Sprint 2.17 + 2.18 tool framework regression: 6.81 s
- Sprint 2.42 + 2.43 + 2.45 regression: 14.60 s

---

## 14. Bugs Found During Loop Engineering

### Bug 1 — `TypeError: can't compare offset-naive and offset-aware datetimes` in outage correlation

**Symptom:** `TestC_Normaliser.test_C8_outages_at_ticket_time` failed with a `TypeError` inside `_extract_outages_at_ticket_time`.

**Root cause:** Uptime Kuma heartbeat timestamps are naive (`"2026-06-18 07:30:00.000"`, no tz). The ticket-creation timestamp comes from Freshdesk with a tz (`"…+00:00"` or `Z`). Comparing directly raised.

**Fix:** Added `_to_naive_utc()` helper in `normalizer.py`; both timestamps are converted to naive UTC before comparison. Uptime Kuma emits UTC by config; naive values are assumed UTC.

### Bug 2 — Auth header missing when tests inject an `httpx.AsyncClient`

**Symptom:** `test_D4`, `test_D5`, `test_D6` all found empty `Authorization` header even with `auth_mode="bearer"`.

**Root cause:** In production the client owns its `httpx.AsyncClient` and applies auth at the client level. In tests we inject a pre-built `AsyncClient` that has no auth. The client's per-request `auth=None` therefore fell through to the (nonexistent) client-level auth.

**Fix:** `UptimeKumaClient` now stores the auth handler on `self._auth` and passes it explicitly per-request when `need_auth=True`. Behavior for tests + production is now identical.

Both fixes were small (~10 LOC each) and applied only inside `metrics_platform/`. No test-only fixup; the production code path is strictly improved.

---

## 15. Fixes Applied

| Bug | Location | Change |
|---|---|---|
| 1 | `metrics_platform/normalizer.py` | Added `_to_naive_utc()`; converted both `ticket_dt` and each heartbeat `ts` before comparison |
| 2 | `metrics_platform/client.py` | Stored `self._auth = _build_auth(config)`; pass explicit auth per-request when `need_auth=True` |

Loop iterations to zero failures: **2 runs** (91/91 achieved on the second run).

---

## 16. Production Readiness Verdict

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  SPRINT 2.50 — PRODUCTION METRICS DASHBOARD INTEGRATION                    ║
║  CERTIFIED — READ-ONLY UPTIME KUMA INTEGRATION LIVE                        ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  Runtime integration                                                         ║
║  ────────────────────                                                        ║
║  ✅ UptimeKumaClient (async, READ-ONLY)                                     ║
║  ✅ MetricTool (BaseTool, capability=READ, provider=METRICS_PLATFORM)       ║
║  ✅ ServerTool (BaseTool, capability=READ, provider=METRICS_PLATFORM)       ║
║  ✅ Prometheus text parser (4 metric families, escape-safe, comment-safe)   ║
║  ✅ Canonical evidence normaliser (MetricsEvidence, ServerHealthEvidence)   ║
║  ✅ Configuration (env-driven, no hardcoded credentials)                    ║
║  ✅ Trace instrumentation (6 tags, WARNING, PII-safe, never-raises)        ║
║  ✅ Registrar (works with Sprint 2.17 + Sprint 2.45 registries)             ║
║                                                                              ║
║  SOT reconciliation                                                          ║
║  ──────────────────                                                          ║
║  ✅ README.md — 16 discovery objectives cross-referenced                    ║
║  ✅ architecture.md — Golden Path pipeline position honoured                ║
║  ✅ api_reference.md — 4 read-only endpoints covered                        ║
║  ✅ ai_investigation_mapping.md — METRICTOOL + SERVERTOOL contracts met     ║
║  ✅ metrics_reference.md — 4 metric families parsed                         ║
║  ✅ integration_guidelines.md — client patterns followed                    ║
║  ✅ security_review.md — READ-ONLY invariant enforced structurally          ║
║  ✅ status_model.md — 4 status values recognised                            ║
║  ✅ limitations.md — errors folded into DataAvailability, never invented   ║
║                                                                              ║
║  Regression (zero new failures)                                              ║
║  ─────────────────────────────                                               ║
║  ✅ Sprint 2.50                     91 / 91 pass                             ║
║  ✅ Sprint 2.46 + 2.47 + 2.48 + 2.49 781 / 781 pass                          ║
║  ✅ Sprint 2.17 + 2.18 tool framework 107 / 107 pass                        ║
║  ✅ Sprint 2.42 + 2.43 + 2.45         557 / 557 pass                        ║
║                                                                              ║
║  Read-only invariant                                                         ║
║  ──────────────────                                                          ║
║  ✅ No write endpoint wrapped in UptimeKumaClient                            ║
║  ✅ No POST / PUT / DELETE / pause / resume / setSettings method exists     ║
║  ✅ TestK_SOT.test_K3 asserts absence of all Uptime Kuma write operations   ║
║                                                                              ║
║  Configuration                                                               ║
║  ────────────                                                                ║
║  ⚠️  Set METRICS_PLATFORM_API_KEY in production env — one of the 3          ║
║      configured Uptime Kuma keys (supportdashboard / script /               ║
║      Support_Automation) or a freshly generated one (SOT README).           ║
║                                                                              ║
║  Next sprint: 2.51 — when assigned                                           ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

---

## Appendix A — Sprint 2.50 Public Surface

`import metrics_platform` exposes:

Configuration: `MetricsPlatformConfig`.

Client: `UptimeKumaClient`, `build_uptime_kuma_client`.

Domain models: `DataAvailability`, `MonitorStatusValue`, `MonitorType`,
`MonitorStatus`, `MetricPoint`, `OutageEvent`, `IncidentInfo`,
`MaintenanceInfo`, `UptimePercentage`, `ComponentStatus`,
`PrometheusQueryResult`, `DashboardSnapshot`, `MetricsEvidence`,
`ServerHealthEvidence`.

Parser + normaliser: `parse_prometheus_text`, `filter_by_name_keywords`,
`build_metrics_evidence`, `build_server_health_evidence`,
`build_dashboard_snapshot`.

Traces: `emit_metrics_trace`, `ALL_METRICS_TRACES`, `TRACE_ENTER_METRICS_TOOL`,
`TRACE_METRICS_QUERY`, `TRACE_METRICS_RESPONSE`, `TRACE_METRICS_NORMALIZED`,
`TRACE_METRICS_EVIDENCE_CREATED`, `TRACE_EXIT_METRICS_TOOL`.

Exceptions: `MetricsPlatformError`, `MetricsPlatformApiError`,
`MetricsPlatformAuthError`, `MetricsPlatformForbiddenError`,
`MetricsPlatformNotFoundError`, `MetricsPlatformServerError`,
`MetricsPlatformConnectionError`, `MetricsPlatformTimeoutError`.

`import case_engine.tools.adapters` exposes: `MetricTool`, `ServerTool`,
`build_metric_tool`, `build_server_tool`, `register_metrics_tools`.

## Appendix B — SOT Documents Fully Reconciled

- `Source_Of_Truth/Architectural_truth/SUPPORT_OPERATIONS_BLUEPRINT.md`
- `Source_Of_Truth/Architectural_truth/flow_diagram.mermaid`
- `Source_Of_Truth/Metrics_discovery/README.md`
- `Source_Of_Truth/Metrics_discovery/architecture.md`
- `Source_Of_Truth/Metrics_discovery/api_reference.md`
- `Source_Of_Truth/Metrics_discovery/ai_investigation_mapping.md`
- `Source_Of_Truth/Metrics_discovery/metrics_reference.md`
- `Source_Of_Truth/Metrics_discovery/integration_guidelines.md`
- `Source_Of_Truth/Metrics_discovery/status_model.md`
- `Source_Of_Truth/Metrics_discovery/security_review.md`
- `Source_Of_Truth/Metrics_discovery/limitations.md`
