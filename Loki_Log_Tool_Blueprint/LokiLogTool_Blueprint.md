# LokiLogTool — Technical Blueprint

**Purpose:** enable the KwikID AI Support Agent to pull backend application logs for a failed Video KYC session directly from each client's Grafana/Loki stack, across multiple tenants (SaaS, Canara, BOB, and future clients), and hand a clean text excerpt to the LLM for root-cause analysis.

---

## 1. Authentication Mechanism

Grafana's HTTP API supports two authentication methods for self-hosted (OSS/Enterprise on-prem) instances:

- **Basic Auth** (username + password) — enabled by default on self-hosted Grafana, authenticates against a Grafana user account (or LDAP, if configured).
- **Service Account Bearer token** — a generated token used as `Authorization: Bearer <token>`. This is Grafana's recommended method for programmatic/automated access since Grafana 9.1+ (fine-grained permissions, revocable, no password rotation coupling).

All three URLs your CTO gave you point to **self-hosted Grafana instances**, not Grafana Cloud — Grafana Cloud only supports the Bearer-token method, so Basic Auth wouldn't even be an option there. The BOB URL (`http://43.204.15.162:3000`) is the strongest signal: port `3000` is Grafana's default HTTP port, and it's a raw IP with no reverse proxy/TLS — a classic self-hosted deployment. This means **Basic Auth is available and is the standard method to start with**, exactly as you planned.

One important nuance: because we're calling Loki **through the Grafana datasource proxy** (see Section 2), the credentials you'll supply authenticate you *to Grafana*, not to Loki itself. Grafana then uses whatever backend auth is configured on that datasource (Loki basic auth, an API key, or none) to reach the actual Loki instance on your behalf. So the username/password you'll give me should be a **Grafana login** (ideally a dedicated read-only "support-agent" service account, not a personal admin login) — not raw Loki credentials.

Recommended production hardening once this is validated: switch from a personal username/password to a Grafana **Service Account token** scoped to `Viewer` on just these three datasources. This avoids storing a human's password anywhere and makes the credential independently revocable. Given `data/sop` context aside — this specific codebase has already had one incident of a committed live credential (a Supabase service-role key) needing rotation — so treat any Loki credential with the same discipline: never in git, never logged, never in a Slack message.

**How I will handle the credentials you provide:** I will not write them to any file, `.env`, or shell history. They'll be entered interactively via `getpass` (password is masked and never echoed to terminal/logs) purely to run one live test connection, held only in local Python process memory for the duration of that test, and discarded (dereferenced) immediately after. If the test call is being executed in this sandboxed environment for you, I will not persist the credentials anywhere in the repo or in this conversation's saved outputs.

> **Please provide the Username and Password for the Loki/Grafana APIs so I can test the connection.**

---

> **Update after live testing (2026-07-27):** the Grafana-proxy assumption below was wrong. Live testing against SaaS returned a plain `404` on the `/api/datasources/proxy/uid/...` path, and the CTO's own working reference command for BOB (`http://43.204.15.162:3000/loki/api/v1/labels`) hits the Loki HTTP API **directly, with no proxy prefix at all**. Section 2 has been corrected below and the code now defaults every tenant to direct-Loki mode, with `via_grafana_proxy` kept as a per-tenant opt-in for any future client where it turns out to genuinely be needed. The `uid` field is retained in the registry as reference metadata only (it's how these datasources are identified inside the CTO's own Grafana Explore UI) - it is not used to build the query URL unless `via_grafana_proxy=True`.

## 2. Endpoint Architecture

**We are not hitting the raw Loki HTTP API directly.** We're routing through the **Grafana datasource proxy**, using the UID your CTO gave you. This is confirmed by Grafana's own API reference:

```
GET /api/datasources/proxy/uid/:uid/*
```

> "Proxies all calls to the actual data source identified by the `uid`."

This is the correct approach here because: (1) your CTO gave you Grafana datasource UIDs, not raw Loki endpoint credentials — UIDs only make sense in the context of Grafana's proxy layer; (2) the "Upstream URLs" all look like Grafana instance hosts, not bare Loki hosts (Loki's own API typically runs on port `3100`, not `3000`/443 behind a named domain); and (3) proxying through Grafana means you only ever need one set of credentials per client (their Grafana login) rather than separately negotiating auth with each Loki backend.

The wildcard `*` after the UID means: whatever Loki API path you'd normally call directly (e.g. `/loki/api/v1/query_range`), you instead append after the proxy prefix. So the final URL construction is:

```
{Upstream URL}/api/datasources/proxy/uid/{UID}/loki/api/v1/query_range
```

Applied to your three clients:

**Corrected after live testing** — these three "Upstream URLs" are the direct Loki HTTP API hosts, no Grafana proxy in front of them:

| Client | Final query_range URL |
|---|---|
| SaaS | `https://utility-server-sfd.app.getkwikid.com/loki/api/v1/query_range` |
| Canara | `https://videokyc.canarabank.bank.in/loki/api/v1/query_range` |
| BOB | `http://43.204.15.162:3000/loki/api/v1/query_range` |

Same pattern applies for the discovery endpoints from Section 3: `.../loki/api/v1/labels` and `.../loki/api/v1/label/<name>/values` (confirmed directly by the CTO's own BOB example).

**What actually happened:** the original proxy-URL hypothesis was a reasonable read of the evidence available at the time (UID given, port 3000 = Grafana's default) but turned out wrong once tested — the SaaS call to the proxy path came back `404 NOT FOUND` (route doesn't exist on that host), and the CTO's own working BOB command confirmed the direct-path shape. The `uid` values are still useful — they're how the CTO's team finds these same datasources inside their own Grafana Explore UI — but they don't belong in the API URL for these three clients. `LokiClient` now defaults to the direct path and exposes `via_grafana_proxy=True` as a one-line per-tenant override, in case some future client's Loki genuinely does sit behind Grafana's proxy only.

---

## 3. LogQL Query Construction

**Stream selector first — this is non-negotiable in LogQL.** Every Loki query must start with a label matcher, e.g. `{job="video-kyc-service"}`. LogQL will reject a truly empty `{}`. Since each client's log-shipping setup (Promtail/Alloy config) is unknown to us right now, and label schemas can differ per tenant, the query should be built in two stages:

**Stage 0 — one-time discovery per client** (not needed per-ticket, just once when onboarding a new tenant): call `GET /loki/api/v1/labels` to see what label names exist (e.g. `job`, `app`, `namespace`, `container`, `pod`, `env`), then `GET /loki/api/v1/label/<name>/values` to see the actual values, so you can build a precise selector like `{app="vkyc-worker"}` instead of guessing. The `LokiClient.labels()` / `label_values()` methods below do this.

**Stage 1 — the actual session_id search.** Since a session_id like `329c5f9e-9406-4f47-b717-5c87f04054d2` is a UUID that will appear verbatim in the log line regardless of whether the app emits plain text or structured JSON, the most robust and format-agnostic query is a **line-contains filter**:

```logql
{job=~".+"} |= "329c5f9e-9406-4f47-b717-5c87f04054d2"
```

`{job=~".+"}` is a broad "match anything with a job label" selector used as a placeholder until Stage 0 gives you a tighter, real selector (swap in the real label once known — a real selector is both faster and avoids scanning irrelevant streams). `|=` is LogQL's substring line filter (exact match, not regex) — cheapest and safest choice for a UUID.

If you confirm the app logs are JSON-structured, a stricter/faster variant using LogQL's `json` parser stage is available and pulls out `session_id` as a first-class field rather than a substring:

```logql
{job=~".+"} | json | session_id="329c5f9e-9406-4f47-b717-5c87f04054d2"
```

**Time range filtering** is not part of the LogQL string itself — it's passed as separate `start` / `end` query parameters on the `query_range` call, as nanosecond Unix epoch strings (Loki also accepts RFC3339, but nanosecond-epoch is unambiguous and avoids timezone bugs):

```
start = <session_start_time as ns epoch>
end   = <session_end_time as ns epoch>
```

In practice, pad the window by a few minutes on each side (`session_time_window()` in the code below defaults to ±5 minutes) — related error logs (async retries, webhook callbacks, timeout handlers) often land just outside the literal session start/end timestamps recorded by the app.

---

## 4. Python Infrastructure Design

Full working module: **`loki_client.py`** (delivered alongside this report). Key design points:

- **`LOKI_REGISTRY`** — a plain `dict[str, LokiDatasource]` keyed by a lowercase tenant key (`"saas"`, `"canara"`, `"bob"`). Each entry is an immutable `@dataclass` holding `client_name`, `datasource_name`, `uid`, and `base_url`. **Adding "Unity" later is a single new dict entry** — nothing else in the file changes:

  ```python
  "unity": LokiDatasource(
      client_name="Unity",
      datasource_name="loki - unity",
      uid="<uid-from-cto>",
      base_url="<grafana-base-url-from-cto>",
  ),
  ```

- **`LokiClient(tenant_key, username, password)`** — looks up the registry entry for `tenant_key`, builds an `httpx.Client` pinned to that tenant's `base_url` with `httpx.BasicAuth(username, password)`, and exposes `.query_range()`, `.labels()`, `.label_values()`. It's a context manager (`with LokiClient(...) as client:`) so the underlying HTTP connection (and, by extension, anything referencing the credentials) is cleanly closed afterward.

- **Credential flow** — `get_credentials()` checks explicit args, then `LOKI_GRAFANA_USERNAME` / `LOKI_GRAFANA_PASSWORD` env vars (for non-interactive/service contexts where a secrets manager injects them at deploy time), then falls back to an interactive `getpass` prompt for manual testing. Nothing is written to disk by this module.

- **Query builders** — `build_session_id_query()` (line-filter, format-agnostic) and `build_session_id_query_json()` (JSON-parsed, stricter) per Section 3. `to_ns()` and `session_time_window()` handle the datetime → nanosecond-epoch conversion and the padding buffer.

- **Extensibility beyond tenants** — because `query_range`, `labels`, and `label_values` all construct their path from `self.ds.uid`, adding new Loki-backed capabilities (e.g. a `series()` method for stream discovery, or a `tail()` for live-following) is just another method following the same `f"/api/datasources/proxy/uid/{self.ds.uid}/loki/api/v1/<endpoint>"` pattern.

---

## 5. Response Parsing

A `query_range` call against a log (non-metric) query returns `resultType: "streams"`:

```json
{
  "status": "success",
  "data": {
    "resultType": "streams",
    "result": [
      {
        "stream": {
          "job": "vkyc-worker",
          "pod": "vkyc-worker-7",
          "level": "error"
        },
        "values": [
          ["1753606421201000000", "session 329c5f9e-9406-4f47-b717-5c87f04054d2 liveness check timed out after 30000ms"],
          ["1753606422750000000", "session 329c5f9e-9406-4f47-b717-5c87f04054d2 retry attempt 2/3 failed: upstream 504"]
        ]
      }
    ],
    "stats": { "...": "query execution stats, safe to ignore for this use case" }
  }
}
```

Key structural facts: `data.result` is a **list of streams**, each with a `stream` object (the label set for that group of log lines) and a `values` array of `[timestamp_ns_string, log_line_string]` pairs (optionally a third element with structured metadata, which we ignore here). Timestamps are **strings**, not numbers, and are nanosecond Unix epoch. Within one stream's `values`, order depends on `direction` (`backward` = newest first, `forward` = oldest first) — but across *multiple* streams there's no guaranteed global order, so you must flatten and re-sort yourself if you want one clean chronological narrative.

`parse_loki_response()` in the code does exactly that: flattens every stream's `values` into `(timestamp_ns, labels, line)` tuples, sorts globally by timestamp, converts each timestamp to ISO-8601 UTC, caps the output at `max_lines` (default 500, to keep the LLM context bounded), and renders each entry as:

```
[2026-07-27T10:03:41.201000+00:00] (job=vkyc-worker, level=error, pod=vkyc-worker-7) session 329c5f9e-9406-4f47-b717-5c87f04054d2 liveness check timed out after 30000ms
[2026-07-27T10:03:42.750000+00:00] (job=vkyc-worker, level=error, pod=vkyc-worker-7) session 329c5f9e-9406-4f47-b717-5c87f04054d2 retry attempt 2/3 failed: upstream 504
```

That block is what gets dropped straight into the support-agent's prompt as grounding context for root-cause analysis.

---

## Suggested next step

1. You provide a Grafana username/password (ideally a scoped service account) for one client — BOB is the simplest first test given the plain HTTP/IP setup.
2. We run `LokiClient.labels()` against it once to confirm real label names (replacing the `{job=~".+"}` placeholder with something precise).
3. We run one live `query_range` call with a known real `session_id` from a recent BOB ticket and confirm the proxy-URL assumption in Section 2 holds (vs. the raw-Loki fallback noted in the caveat).
4. Repeat for SaaS and Canara, then wire this into the support-agent's ticket-investigation flow.

**Please provide the Username and Password for the Loki/Grafana APIs so I can test the connection.** I'll use them only in-memory for this live test and discard them immediately after — nothing gets written to disk, logged, or committed.
