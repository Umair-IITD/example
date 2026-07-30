"""
case_engine/integrations/loki/client.py

Sprint 2.60: Multi-tenant Grafana Loki log retrieval client.

Ported from Loki_Log_Tool_Blueprint/loki_client.py with these changes:
- Credentials now come from LokiConfig (not from env/getpass directly)
- LOKI_REGISTRY populated with "saas" only (BOB/Canara present as comments)
- get_credentials() and __main__ blocks NOT ported (test script only)
- LokiClient.__init__ accepts optional transport= for test injection

Design:
  - NEVER raises from fetch_session_logs — callers catch at the tool layer
  - Sync httpx.Client — caller must use asyncio.to_thread() when in async context
  - Three-phase sweep: ingress / service_name union / server-label
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from case_engine.integrations.loki.config import LokiConfig


# ---------------------------------------------------------------------------
# 1. Tenant registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LokiDatasource:
    client_name: str
    datasource_name: str
    uid: str
    base_url: str
    via_grafana_proxy: bool = False


LOKI_REGISTRY: dict[str, LokiDatasource] = {
    "saas": LokiDatasource(
        client_name="SaaS",
        datasource_name="loki - saas",
        uid="bfm1x2kaaifb4c",
        base_url="https://utility-server-sfd.app.getkwikid.com",
    ),
    # Registered per tenant-provided datasource details (Sprint 2.60 rollout).
    # via_grafana_proxy left at the dataclass default (False): both Canara and
    # BOB are reached via direct Loki API access, not the Grafana datasource
    # proxy — confirmed by live testing, consistent with this entry's prior
    # (commented) form before it had credentials wired.
    "canara": LokiDatasource(
        client_name="Canara",
        datasource_name="loki-canara",
        uid="afm1z1tgkotmod",
        base_url="https://videokyc.canarabank.bank.in",
    ),
    # BOB entry intentionally excluded: base_url must use HTTPS before this
    # tenant can be registered. Shipping BasicAuth + VKYC log data over
    # http:// to a public IP violates mandatory TLS policy. Re-enable once
    # BOB provides an HTTPS endpoint or a mutually-authenticated VPN tunnel.
    #
    # "bob": LokiDatasource(
    #     client_name="BOB",
    #     datasource_name="BOB-PROD-LOKI",
    #     uid="eft1z1e0wzk00f",
    #     base_url="https://<tls-endpoint-required>",
    # ),
}


# ---------------------------------------------------------------------------
# 2. LogQL helpers
# ---------------------------------------------------------------------------

def to_ns(ts: datetime) -> str:
    """Convert a timezone-aware datetime to a Loki-compatible nanosecond epoch string."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return str(int(ts.timestamp() * 1_000_000_000))


def session_time_window(
    started_at: datetime, ended_at: datetime, buffer: timedelta = timedelta(minutes=5)
) -> tuple[datetime, datetime]:
    """Pad a VKYC session's start/end with a buffer for related error logs."""
    return started_at - buffer, ended_at + buffer


def build_session_id_query(
    session_id: str, stream_selector: str = '{job=~".+"}'
) -> str:
    """
    Broadest-possible LogQL query for a session_id: a plain substring line
    filter, which matches the UUID whether the underlying log line is JSON,
    logfmt, or plain text.
    """
    escaped = session_id.replace('"', '\\"')
    return f'{stream_selector} |= "{escaped}"'


# ---------------------------------------------------------------------------
# 3. Service-name hints (from live session analysis)
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
}

ALL_KNOWN_SERVICE_CANDIDATES: set[str] = {
    service
    for services in SERVICE_NAME_HINTS.values()
    for service in services.split("|")
}


def guess_service_candidates(request_lines: list[str]) -> set[str]:
    """Scan ingress/nginx log lines for known keywords and return service_name candidates."""
    candidates: set[str] = set()
    for line in request_lines:
        for keyword, services in SERVICE_NAME_HINTS.items():
            if keyword in line:
                candidates.update(services.split("|"))
    return candidates


# ---------------------------------------------------------------------------
# 4. Client
# ---------------------------------------------------------------------------

class LokiClient:
    """
    Multi-tenant Loki client. Sync httpx.Client — must use asyncio.to_thread()
    when called from an async context.

    Credentials are read from LokiConfig (not env vars directly).
    """

    def __init__(
        self,
        config: LokiConfig,
        tenant_key: str = "saas",
        transport: Optional[httpx.BaseTransport] = None,
    ):
        tenant_key = tenant_key.lower()
        if tenant_key not in LOKI_REGISTRY:
            raise ValueError(
                f"Unknown tenant '{tenant_key}'. "
                f"Known tenants: {sorted(LOKI_REGISTRY)}"
            )
        self.tenant_key = tenant_key
        self.ds = LOKI_REGISTRY[tenant_key]

        # Credentials come from LokiConfig, per tenant.
        if tenant_key == "saas":
            base_url = config.saas_base_url
            username = config.saas_username
            password = config.saas_password
        elif tenant_key == "canara":
            base_url = config.canara_base_url or self.ds.base_url
            username = config.canara_username
            password = config.canara_password
        elif tenant_key == "bob":
            base_url = config.bob_base_url or self.ds.base_url
            username = config.bob_username
            password = config.bob_password
        else:
            # Future tenants can extend LokiConfig — not yet wired
            base_url = self.ds.base_url
            username = ""
            password = ""

        if base_url.startswith("http://"):
            raise ValueError(
                f"LokiClient refused to connect to {base_url!r}: "
                "plaintext HTTP is forbidden. BasicAuth credentials and VKYC log data "
                "must transit over HTTPS. Provide an HTTPS endpoint or VPN tunnel."
            )

        client_kwargs: dict = {
            "base_url": base_url,
            "auth": httpx.BasicAuth(username, password),
            "timeout": config.timeout,
        }
        if transport is not None:
            client_kwargs["transport"] = transport

        self._client = httpx.Client(**client_kwargs)

    def _endpoint(self, loki_path: str) -> str:
        """Build the correct path for this tenant."""
        if self.ds.via_grafana_proxy:
            return f"/api/datasources/proxy/uid/{self.ds.uid}/loki/api/v1/{loki_path}"
        return f"/loki/api/v1/{loki_path}"

    @staticmethod
    def _raise_friendly(resp: httpx.Response) -> None:
        if resp.status_code == 404:
            raise RuntimeError(
                f"404 at {resp.request.url} - the path wasn't found. "
                "Check via_grafana_proxy setting or base_url prefix."
            )
        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"{resp.status_code} at {resp.request.url} - credentials rejected "
                "(wrong username/password or insufficient permissions)."
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

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "LokiClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# ---------------------------------------------------------------------------
# 5. Cross-service session fetch (three-phase sweep)
# ---------------------------------------------------------------------------

def fetch_session_logs(
    client: LokiClient,
    session_id: str,
    start: datetime,
    end: datetime,
    ingress_selector: str = '{job=~".+"}',
) -> str:
    """
    Three-phase cross-service pull for ONE session_id.

    Phase 1: Ingress/nginx sweep with session_id substring filter.
    Phase 2: Service_name union sweep (ALWAYS runs — not conditional on phase 1).
    Phase 3: Server-label scoped queries for each physical host found in phases 1+2.

    Returns one merged, chronologically-sorted, LLM-ready text block.
    """
    all_raw: list[dict] = []

    # Phase 1: ingress/nginx sweep
    phase1_query = build_session_id_query(session_id, stream_selector=ingress_selector)
    phase1 = client.query_range(phase1_query, start=start, end=end, limit=1000)
    all_raw.append(phase1)

    request_lines = [
        line
        for stream in phase1.get("data", {}).get("result", [])
        for _, line in stream.get("values", [])
    ]
    candidates = guess_service_candidates(request_lines) | ALL_KNOWN_SERVICE_CANDIDATES

    # Phase 2: service_name union sweep (ALWAYS runs)
    selector = '{service_name=~"' + "|".join(sorted(candidates)) + '"}'
    phase2_query = build_session_id_query(session_id, stream_selector=selector)
    phase2 = client.query_range(phase2_query, start=start, end=end, limit=1000)
    all_raw.append(phase2)

    # Phase 3: server-label scoped queries for each physical host
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
# 6. Response parsing → LLM-ready text
# ---------------------------------------------------------------------------

def parse_loki_response(raw: dict, max_lines: int = 500) -> str:
    """
    Flattens Loki's streams response into a single chronologically-sorted,
    human/LLM-readable block.

    Format: [ISO ts] (labels) message
    Deduplicates by (ts_ns, line) since phases overlap.
    """
    data = raw.get("data", {})
    if data.get("resultType") not in ("streams", None):
        return (
            f"(unexpected resultType={data.get('resultType')!r}; "
            "this parser only handles log streams)"
        )

    entries: list[tuple[int, dict, str]] = []
    seen: set[tuple[int, str]] = set()
    for stream in data.get("result", []):
        labels = stream.get("stream", {})
        for value in stream.get("values", []):
            ts_ns, line = value[0], value[1]
            dedup_key = (int(ts_ns), line)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            entries.append((int(ts_ns), labels, line))

    if not entries:
        return "No matching log lines found for the given query and time range."

    entries.sort(key=lambda e: e[0])      # chronological order
    entries = entries[-max_lines:]         # cap payload size

    lines_out = []
    for ts_ns, labels, line in entries:
        ts_iso = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).isoformat()
        label_str = ", ".join(f"{k}={v}" for k, v in sorted(labels.items()))
        lines_out.append(f"[{ts_iso}] ({label_str}) {line}")

    return "\n".join(lines_out)
