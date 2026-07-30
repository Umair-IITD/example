"""
unity_to_loki_test.py - SCRATCH TEST, not part of the project codebase.

Purpose: validate the real end-to-end chain BEFORE anyone wires it into
case_engine/tools/adapters/. Does not modify, add to, or import-side-effect
the fumadocs_ingest_service project in any way other than reading its
existing, already-tested unity/ package via sys.path.

Chain under test:
    session_id
      -> unity.client.UnityClient.get_session_details(session_id)   [ALREADY BUILT]
      -> unity.normalizer.parse_session_details(raw)                [ALREADY BUILT]
      -> UnitySession.timeline.{start_time,end_time,...}            [ALREADY BUILT]
      -> loki_client.fetch_session_logs(...)                        [built this session]

Run from this folder (Loki_Log_Tool_Blueprint/):
    python unity_to_loki_test.py

Requires: the real project's venv (httpx already present there), and this
folder's loki_client.py alongside this script.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Point at the REAL project code so we can import the already-built unity/
# package. This only reads from that directory - nothing is written there,
# nothing there is modified. Adjust this path if your checkout differs.
# ---------------------------------------------------------------------------
FUMADOCS_SERVICE_DIR = Path(
    r"C:\Users\Umair.Alam\Desktop\kwikid_support_system\kwikid-ai-ingest\ai_project\fumadocs_ingest_service"
)
sys.path.insert(0, str(FUMADOCS_SERVICE_DIR))

# Load the project's real .env so UnityConfig.from_env() sees the same
# UNITY_* values the actual service would use. Falls back to a manual
# parse if python-dotenv isn't installed in this environment.
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv(FUMADOCS_SERVICE_DIR / ".env")
except ImportError:
    env_path = FUMADOCS_SERVICE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from unity.client import UnityClient          # noqa: E402  (import after sys.path insert)
from unity.config import UnityConfig          # noqa: E402
from unity.normalizer import parse_session_details  # noqa: E402

# This folder's own tool, built earlier this session.
from loki_client import LokiClient, fetch_session_logs, get_credentials  # noqa: E402


async def _get_unity_session(session_id: str):
    config = UnityConfig.from_env()
    print(
        f"UnityConfig: base_url={config.base_url} domain={config.domain} "
        f"username={config.masked_username} has_credentials={config.has_credentials}"
    )
    # if config.username == "unity" and config.password == "unity":
    #     print(
    #         "WARNING: UNITY_USERNAME/UNITY_PASSWORD in .env are still the "
    #         "literal placeholder 'unity'/'unity'. If that's not actually a "
    #         "valid credential, generate_token will fail with 401/403 below - "
    #         "that's a credential problem, not a bug in this script."
    #     )

    async with UnityClient(config) as client:
        raw = await client.get_session_details(session_id)
        session = parse_session_details(raw)
        return session, raw


def _epoch_to_dt(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def main() -> None:
    session_id = input("Session ID (Unity): ").strip()

    print(f"\n--- Step 1: unity.get_session_details({session_id}) ---")
    try:
        session, raw = asyncio.run(_get_unity_session(session_id))
    except Exception as exc:  # noqa: BLE001 - this is a diagnostic script
        print(f"FAILED calling Unity admin portal: {type(exc).__name__}: {exc}")
        return

    if session is None:
        print("Unity returned no session_data for this session_id (empty/malformed body).")
        print("Raw response:", raw)
        return

    print(f"session_status={session.session_status.value}  client_name={session.client_name!r}")

    t = session.timeline
    print(
        f"timeline (raw epoch seconds): init_time={t.init_time} start_time={t.start_time} "
        f"end_time={t.end_time} vkyc_start_time={t.vkyc_start_time} "
        f"last_active_timestamp={t.last_active_timestamp} "
        f"agent_assignment_time={t.agent_assignment_time}"
    )

    # Prefer start_time/end_time; fall back to vkyc_start_time/init_time and
    # last_active_timestamp respectively, since not every session status
    # populates every field (e.g. a session abandoned before video start may
    # have init_time but no vkyc_start_time/end_time).
    start_epoch = t.start_time or t.vkyc_start_time or t.init_time
    end_epoch = t.end_time or t.last_active_timestamp or start_epoch

    if not start_epoch:
        print(
            "\nWARNING: could not derive any usable start time from the "
            "timeline fields above - all were 0.0. Dumping session_data "
            "fields containing 'time' for manual inspection:"
        )
        for k, v in (raw.get("session_data") or {}).items():
            if "time" in k.lower():
                print(f"  {k}: {v}")
        return

    start_dt = _epoch_to_dt(start_epoch)
    end_dt = _epoch_to_dt(end_epoch)
    print(f"\nDerived window (UTC): {start_dt.isoformat()} -> {end_dt.isoformat()}")

    print("\n--- Step 2: fetch_session_logs against 'saas' Loki for this window ---")
    username, password = get_credentials()
    buffer = timedelta(minutes=5)
    try:
        with LokiClient("saas", username, password) as loki:
            text = fetch_session_logs(loki, session_id, start_dt - buffer, end_dt + buffer)
            print(text)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED querying Loki: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
