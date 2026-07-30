"""
LokiLogTool - Multi-tenant Grafana Loki log retrieval client for the KwikID
Enterprise AI Support Agent.

Design goals:
  1. Route each query to the correct client's Grafana instance + Loki
     datasource UID, via a simple extensible registry.
  2. Never persist credentials to disk or process env permanently -
     credentials are supplied at runtime, held only in memory for the
     lifetime of an httpx.Client, and discarded on close.
  3. Produce a clean, LLM-ready text block from Loki's raw JSON response
     so it can be dropped straight into a support-agent prompt.

Requires: httpx  (pip install httpx)
"""

from __future__ import annotations

import getpass
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx


# ---------------------------------------------------------------------------
# 1. Tenant registry
#
# CORRECTED (per live testing): "base_url" is the direct Loki HTTP API host
# for each client - NOT reached via Grafana's /api/datasources/proxy/uid/...
# route. Confirmed by two pieces of evidence: (1) the CTO's own working BOB
# example calls 'http://43.204.15.162:3000/loki/api/v1/labels' directly, no
# proxy prefix at all; (2) the SaaS proxy-prefixed call returned a plain
# 404 (route not found), not a 401 (auth rejected) - consistent with that
# path simply not existing on this host. "uid" is kept here purely as
# reference metadata (it's how the CTO's own Grafana Explore UI identifies
# the datasource) - it is NOT used to build the query URL by default.
#
# `via_grafana_proxy` is left as a per-client escape hatch: if a future
# client's Loki genuinely is only reachable through Grafana's datasource
# proxy (unlike these three), flip it to True for that entry only and the
# client will build the /api/datasources/proxy/uid/<uid>/... path instead.
#
# To onboard a new client (e.g. "Unity"), add one entry below. Nothing
# else in this file changes.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LokiDatasource:
    client_name: str                 # human-readable client / tenant name
    datasource_name: str             # name of the datasource as configured in Grafana (reference only)
    uid: str                         # Grafana datasource UID (reference only, unless via_grafana_proxy=True)
    base_url: str                    # the actual Loki HTTP API host ("upstream") for this client
    via_grafana_proxy: bool = False  # True only if this client's Loki must be reached through Grafana's proxy


LOKI_REGISTRY: dict[str, LokiDatasource] = {
    "saas": LokiDatasource(
        client_name="SaaS",
        datasource_name="loki - saas",
        uid="bfm1x2kaaifb4c",
        base_url="https://utility-server-sfd.app.getkwikid.com",
    ),
    "canara": LokiDatasource(
        client_name="Canara",
        datasource_name="loki - canara",
        uid="afmlz1tgkotmod",
        base_url="https://videokyc.canarabank.bank.in",
    ),
    "bob": LokiDatasource(
        client_name="BOB",
        datasource_name="BOB - PROD - LOKI",
        uid="eft1zle0wzk00f",
        base_url="http://43.204.15.162:3000",
    ),
    # "unity": LokiDatasource(
    #     client_name="Unity",
    #     datasource_name="loki - unity",
    #     uid="<uid-from-cto>",
    #     base_url="<upstream-loki-url-from-cto>",
    #     via_grafana_proxy=False,  # flip to True only if CTO says Unity needs the Grafana proxy
    # ),
}


# ---------------------------------------------------------------------------
# 2. Credential handling
#
# SECURITY RULE: credentials are never hardcoded and never written to disk.
# Preferred order at call time:
#   a) explicit args passed by the caller (e.g. pulled from a secrets
#      manager such as AWS Secrets Manager / Vault at request time)
#   b) LOKI_GRAFANA_USERNAME / LOKI_GRAFANA_PASSWORD env vars, if a
#      deployment chooses to inject them as short-lived process env
#      (e.g. via ECS task secrets) rather than a literal .env file
#   c) interactive prompt (local/manual testing only) - getpass hides
#      the password from the terminal and scrollback
# ---------------------------------------------------------------------------

def get_credentials(
    username: Optional[str] = None, password: Optional[str] = None
) -> tuple[str, str]:
    username = username or os.environ.get("LOKI_GRAFANA_USERNAME")
    password = password or os.environ.get("LOKI_GRAFANA_PASSWORD")

    if not username:
        username = input("Grafana Username: ").strip()
    if not password:
        password = getpass.getpass("Grafana Password: ")

    return username, password


# ---------------------------------------------------------------------------
# 3. LogQL helpers
# ---------------------------------------------------------------------------

def build_session_id_query(
    session_id: str, stream_selector: str = '{job=~".+"}'
) -> str:
    """
    Broadest-possible LogQL query for a session_id: a plain substring line
    filter, which matches the UUID whether the underlying log line is JSON,
    logfmt, or plain text. `stream_selector` must be tuned per-tenant once
    you know their actual label schema (see blueprint doc, section 3) -
    swap in something like '{app="video-kyc-service"}' once discovered via
    GET /loki/api/v1/labels.
    """
    escaped = session_id.replace('"', '\\"')
    return f'{stream_selector} |= "{escaped}"'


def build_session_id_query_json(
    session_id: str, stream_selector: str = '{job=~".+"}'
) -> str:
    """
    Stricter variant for JSON-structured logs: parses the JSON body and
    matches session_id as an exact structured field rather than a raw
    substring. Use once you've confirmed the app emits JSON logs with a
    `session_id` key.
    """
    escaped = session_id.replace('"', '\\"')
    return f'{stream_selector} | json | session_id="{escaped}"'


def to_ns(ts: datetime) -> str:
    """Convert a timezone-aware datetime to a Loki-compatible nanosecond
    Unix epoch string."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return str(int(ts.timestamp() * 1_000_000_000))


def session_time_window(
    started_at: datetime, ended_at: datetime, buffer: timedelta = timedelta(minutes=5)
) -> tuple[datetime, datetime]:
    """Pad a VKYC session's start/end with a buffer, since related error
    logs (retries, async callbacks, webhook confirmations) commonly land
    just outside the exact session window."""
    return started_at - buffer, ended_at + buffer


# ---------------------------------------------------------------------------
# 4. Client
# ---------------------------------------------------------------------------

class LokiClient:
    """
    Multi-tenant Loki client. By default queries the Loki HTTP API directly
    at the tenant's base_url (e.g. http://43.204.15.162:3000/loki/api/v1/...),
    per confirmed live behavior. If a given tenant's registry entry has
    via_grafana_proxy=True, it instead builds
    /api/datasources/proxy/uid/<uid>/loki/api/v1/... for that tenant only.

    Usage:
        username, password = get_credentials()
        with LokiClient("bob", username, password) as client:
            raw = client.query_range(
                logql=build_session_id_query(session_id),
                start=start_dt,
                end=end_dt,
            )
        text = parse_loki_response(raw)
    """

    def __init__(
        self,
        tenant_key: str,
        username: str,
        password: str,
        timeout: float = 30.0,
        verify_ssl: bool = True,
    ):
        tenant_key = tenant_key.lower()
        if tenant_key not in LOKI_REGISTRY:
            raise ValueError(
                f"Unknown tenant '{tenant_key}'. "
                f"Known tenants: {sorted(LOKI_REGISTRY)}"
            )
        self.tenant_key = tenant_key
        self.ds = LOKI_REGISTRY[tenant_key]

        self._client = httpx.Client(
            base_url=self.ds.base_url,
            auth=httpx.BasicAuth(username, password),
            timeout=timeout,
            verify=verify_ssl,
        )

    def _endpoint(self, loki_path: str) -> str:
        """Build the correct path for this tenant: direct Loki API by
        default, or Grafana-proxied if the registry entry opts into it."""
        if self.ds.via_grafana_proxy:
            return f"/api/datasources/proxy/uid/{self.ds.uid}/loki/api/v1/{loki_path}"
        return f"/loki/api/v1/{loki_path}"

    @staticmethod
    def _raise_friendly(resp: httpx.Response) -> None:
        if resp.status_code == 404:
            raise RuntimeError(
                f"404 at {resp.request.url} - the path itself wasn't found. "
                "Usually means via_grafana_proxy is set wrong for this tenant "
                "(try flipping it), or the base_url needs a path prefix. "
                "This is NOT a credentials problem."
            )
        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"{resp.status_code} at {resp.request.url} - credentials were "
                "rejected (wrong username/password, or account lacks Viewer "
                "permission on this datasource)."
            )
        resp.raise_for_status()

    def query_range(
        self,
        logql: str,
        start: datetime,
        end: datetime,
        limit: int = 1000,
        direction: str = "backward",
    ) -> dict:
        path = self._endpoint("query_range")
        params = {
            "query": logql,
            "start": to_ns(start),
            "end": to_ns(end),
            "limit": limit,
            "direction": direction,
        }
        resp = self._client.get(path, params=params)
        self._raise_friendly(resp)
        return resp.json()

    def labels(self, start: Optional[datetime] = None, end: Optional[datetime] = None) -> list[str]:
        """Discover available label names for this tenant's Loki instance -
        run this once per client to design an accurate stream_selector
        instead of guessing at label names. Cheap, safe, read-only - use
        this as the first connectivity/auth smoke test for any new tenant."""
        path = self._endpoint("labels")
        params = {}
        if start:
            params["start"] = to_ns(start)
        if end:
            params["end"] = to_ns(end)
        resp = self._client.get(path, params=params)
        self._raise_friendly(resp)
        return resp.json().get("data", [])

    def label_values(self, label: str, start: Optional[datetime] = None, end: Optional[datetime] = None) -> list[str]:
        path = self._endpoint(f"label/{label}/values")
        params = {}
        if start:
            params["start"] = to_ns(start)
        if end:
            params["end"] = to_ns(end)
        resp = self._client.get(path, params=params)
        self._raise_friendly(resp)
        return resp.json().get("data", [])

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "LokiClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# ---------------------------------------------------------------------------
# 4b. Cross-service session fetch (the actual end goal: given a session_id,
# pull the COMPLETE log trail across every service that touched it)
#
# Ground truth for this mapping comes from a real captured agent-side VKYC
# journey (RBL, 2026-07-17, session_id 2115a66d-bb9f-4306-92cf-21e2c309bcc2)
# saved at Source_Of_Truth/Xratch/kwikid_secure_telemetry_*.json. That
# capture showed the real endpoint surface a single session touches:
#   /api/v4/agent/login, /config/get/agent, /get_schedules, /sendLink,
#   /waiting, /match_face/<id>, /match_faces_redact/<id>, /verify_pan/<id>,
#   /is_user_dropped_call/<id>  (main agent-action API)
#   /ocr/login, /ocr/v1/pancard/ocr                     (separate OCR service)
#   /api/v1/download_content/.../<id>/...                (asset retrieval)
#   /v4/upload_speed                                     (a beacon call that
#       404s in EVERY session observed - looks like a real frontend bug
#       hitting a path missing "/api/", not a session-specific failure;
#       treat as known noise, not a signal, until confirmed otherwise)
#
# A single session therefore spans multiple distinct backend services, not
# one. This dict maps a keyword seen in the ingress/nginx URL to the
# service_name candidates (from the 'saas' datasource's real service_name
# list) most likely to hold that step's application-level logs. It is a
# living heuristic - extend it as new endpoints/clients/services turn up.
# ---------------------------------------------------------------------------

SERVICE_NAME_HINTS: dict[str, str] = {
    "agent/login": "agentapi",
    "config/get/agent": "agentapi|adminapi",
    "get_schedules": "agentapi",
    "manualAssigned": "agentapi",
    "sendLink": "notificaion-service",
    "waiting": "agentapi",
    "match_face": "kwikid-sumi-setup-sumi-1|kwikid-sumi-setup-sumi-liveness-1|kwikid-sumi-setup-deepfake-1",
    "verify_pan": "kwikid-verify-api-verify-1",
    "is_user_dropped_call": "signallingapi|lk-sfu-setup-livekit-1|lk-sfu-setup-sfu-wrapper-1|coturn-mr-frontend",
    "download_content": (
        "kwikid-celery-worker-merge_video|kwikid-celery-worker-merge_video_2|"
        "kwikid-celery-worker-make_seekable_video|"
        "kwikid-celery-worker-kwikid_vkyc_pdf_maker"
    ),
    "ckyc": "kwikid-ckyc-service-setup-kwikid-ckyc-service-1|ckycredis",
    "digilocker": "digilockerapi",
    "dkyc": "dkycapi",
    # "ocr" endpoints observed in the RBL capture had no obvious match in
    # the saas service_name list - unknown service, flag for CTO follow-up.
}


def guess_service_candidates(request_lines: list[str]) -> set[str]:
    """Scan a batch of ingress/nginx log lines (or raw URLs) for known
    keywords and return the set of service_name candidates worth querying
    next. Best-effort heuristic, not a guarantee - see SERVICE_NAME_HINTS."""
    candidates: set[str] = set()
    for line in request_lines:
        for keyword, services in SERVICE_NAME_HINTS.items():
            if keyword in line:
                candidates.update(services.split("|"))
    return candidates


# Every backend service_name known (so far) to plausibly touch a VKYC
# session, flattened from SERVICE_NAME_HINTS. Used as the phase-2 fallback
# when phase 1 (ingress/nginx) finds zero traces for a session - which
# turns out to be common: a session that nobody has manually re-pulled via
# the admin portal afterward (no agent re-opening it, no video re-download)
# may leave no nginx-visible footprint at all, since a lot of the real-time
# call/backend activity for a session doesn't go through that tier. Phase 2
# must not depend on phase 1 having found something - it needs to run
# regardless, or genuinely silent sessions never get investigated past
# "no matching lines found."
ALL_KNOWN_SERVICE_CANDIDATES: set[str] = {
    service
    for services in SERVICE_NAME_HINTS.values()
    for service in services.split("|")
}


def fetch_session_logs(
    client: LokiClient,
    session_id: str,
    start: datetime,
    end: datetime,
    ingress_selector: str = '{job=~".+"}',
) -> str:
    """
    Two-phase cross-service pull for ONE session_id - this is the core
    operation the L1/L2 pipeline needs.

    Phase 1 (cheap, broad): sweep the ingress/nginx layer for the literal
    session_id. This reliably catches the request-level timeline (which
    endpoints were hit, in what order, with what HTTP status) because the
    ingress layer is the one place we've confirmed the session_id always
    appears verbatim in the log line - regardless of which of the ~90
    backend services eventually handled it.

    Phase 2 (targeted, ALWAYS runs - not conditional on phase 1): query the
    union of (a) SERVICE_NAME_HINTS candidates guessed from whatever phase 1
    found, if anything, and (b) ALL_KNOWN_SERVICE_CANDIDATES as a fixed
    floor. This is where real application-level errors/exceptions/
    business-logic rejections will actually show up - phase 1 alone will
    mostly show 200s (or nothing at all - see note below), since nginx logs
    the HTTP status of its own response, not the downstream service's
    internal failure detail.

    IMPORTANT: phase 2 must not be skipped just because phase 1 found no
    lines. A session that nobody has manually re-opened via the admin
    portal since it happened (no agent re-pulling it, no video re-download)
    can leave literally zero nginx-visible trace, since the real-time
    call/backend activity for a session largely doesn't transit that tier -
    confirmed against a real successful Unity session this way. Skipping
    phase 2 in that case would silently under-report "no logs found" for a
    session that actually has plenty of application-layer log lines.

    Returns one merged, chronologically-sorted, LLM-ready text block
    covering every service that touched this session.
    """
    all_raw: list[dict] = []

    phase1_query = build_session_id_query(session_id, stream_selector=ingress_selector)
    phase1 = client.query_range(phase1_query, start=start, end=end, limit=1000)
    all_raw.append(phase1)

    request_lines = [
        line
        for stream in phase1.get("data", {}).get("result", [])
        for _, line in stream.get("values", [])
    ]
    candidates = guess_service_candidates(request_lines) | ALL_KNOWN_SERVICE_CANDIDATES

    selector = '{service_name=~"' + "|".join(sorted(candidates)) + '"}'
    phase2_query = build_session_id_query(session_id, stream_selector=selector)
    phase2 = client.query_range(phase2_query, start=start, end=end, limit=1000)
    all_raw.append(phase2)

    # Phase 3 (server-scoped, ALWAYS runs against whatever `server` label
    # values phase 1/2 actually turned up): a real side-by-side comparison
    # against a manually-run Grafana query using only {server="<host>"} (no
    # job/service_name constraint at all) turned up log lines - Redis queue
    # position polling, "getcurrentstatus" calls - that phases 1+2 missed
    # even though they fell inside the same window and contained the literal
    # session_id. Filtering by which physical/VM host handled the session
    # appears to be a more complete dimension than service_name for this
    # promtail-per-host setup: one host ships every container's logs, so
    # scoping to "the host(s) we know were involved" catches things whose
    # service_name isn't yet in ALL_KNOWN_SERVICE_CANDIDATES.
    server_values = {
        stream.get("stream", {}).get("server")
        for raw in all_raw
        for stream in raw.get("data", {}).get("result", [])
        if stream.get("stream", {}).get("server")
    }
    for server in sorted(server_values):
        phase3_selector = f'{{server="{server}"}}'
        phase3_query = build_session_id_query(session_id, stream_selector=phase3_selector)
        phase3 = client.query_range(phase3_query, start=start, end=end, limit=1000)
        all_raw.append(phase3)

    merged_result = [
        stream
        for raw in all_raw
        for stream in raw.get("data", {}).get("result", [])
    ]
    merged = {"data": {"resultType": "streams", "result": merged_result}}
    return parse_loki_response(merged)


# ---------------------------------------------------------------------------
# 5. Response parsing -> LLM-ready text
# ---------------------------------------------------------------------------

def parse_loki_response(raw: dict, max_lines: int = 500) -> str:
    """
    Flattens Loki's {"data": {"resultType": "streams", "result": [...]}}
    shape into a single chronologically-sorted, human/LLM-readable block:

        [2026-07-27T10:03:41.201Z] (service=vkyc-worker, pod=vkyc-worker-7)
        ERROR session 329c5f9e... liveness check timed out after 30000ms
    """
    data = raw.get("data", {})
    if data.get("resultType") not in ("streams", None):
        return (
            f"(unexpected resultType={data.get('resultType')!r}; "
            "this parser only handles log streams, not metric results)"
        )

    entries: list[tuple[int, dict, str]] = []
    seen: set[tuple[int, str]] = set()
    for stream in data.get("result", []):
        labels = stream.get("stream", {})
        for value in stream.get("values", []):
            ts_ns, line = value[0], value[1]
            # De-dup: fetch_session_logs runs multiple overlapping phases
            # (ingress, service_name sweep, server sweep) against the same
            # window - the same physical log line commonly matches more
            # than one phase and would otherwise show up 2-3x in the output.
            dedup_key = (int(ts_ns), line)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            entries.append((int(ts_ns), labels, line))

    if not entries:
        return "No matching log lines found for the given query and time range."

    entries.sort(key=lambda e: e[0])  # chronological order, oldest first
    entries = entries[-max_lines:]    # cap payload size going into the LLM

    lines_out = []
    for ts_ns, labels, line in entries:
        ts_iso = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).isoformat()
        label_str = ", ".join(f"{k}={v}" for k, v in sorted(labels.items()))
        lines_out.append(f"[{ts_iso}] ({label_str}) {line}")

    return "\n".join(lines_out)


# ---------------------------------------------------------------------------
# 6. Example end-to-end usage (manual test / CLI)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tenant = input("Tenant key (saas / canara / bob): ").strip().lower()
    session_id = input("Session ID to search for: ").strip()

    # Widen this if the session might be older - query_range only looks
    # back this far from "now".
    now = datetime.now(timezone.utc)
    default_start = now - timedelta(hours=1)

    username, password = get_credentials()
    # Note: typing the password shows nothing at all (no dots/asterisks) -
    # that's getpass masking working correctly, not a stuck terminal.

    try:
        with LokiClient(tenant, username, password) as client:
            # Step 1: cheap smoke test before attempting the real query.
            # Confirms connectivity + auth + which endpoint shape (direct
            # vs Grafana-proxied) actually works for this tenant.
            print(f"--- Testing connectivity for '{tenant}' via /loki/api/v1/labels ---")
            try:
                label_names = client.labels()
                print(f"OK - reachable. Available labels: {label_names}")
            except RuntimeError as e:
                print(f"FAILED: {e}")
                raise SystemExit(1)

            # Step 2: the real fetch - cross-service, two-phase.
            print(f"\n--- Fetching complete cross-service log trail for session_id={session_id} ---")
            text = fetch_session_logs(client, session_id, start=default_start, end=now)
            print(text)
    finally:
        # Best-effort scrub of credential locals from this frame.
        username = password = None
        del username, password
