"""
scripts/register_asana_webhook.py

Sprint 2.6x: One-off script to register the Asana webhook against the
Support Escalation project so Asana starts pushing "task completed" events
to POST /webhooks/asana/task-completed.

Prerequisites (in this order — the handshake will fail otherwise):
  1. ASANA_API_KEY / ASANA_PROJECT_ID / ASANA_WORKSPACE_ID set in .env.
  2. The FastAPI app is RUNNING and reachable at the target URL you pass in
     (e.g. your ngrok tunnel) — Asana's POST /webhooks call blocks on a
     live handshake against that URL before returning.

Usage:
    python scripts/register_asana_webhook.py https://<your-ngrok-domain>/webhooks/asana/task-completed

What it does:
  - Builds an AsanaClient from env vars (fails loudly if credentials missing).
  - Calls AsanaClient.create_webhook(target_url) against the configured
    ASANA_PROJECT_ID (Support Escalation).
  - Prints the resulting webhook gid so you can find/delete it later if needed
    (via the Asana API — there is no UI for this).

This script does not touch any other Asana project. It only ever acts on the
project configured via ASANA_PROJECT_ID in your own .env.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Bootstrap: when this script is run directly (`python scripts/register_asana_webhook.py`),
# Python only puts scripts/ on sys.path, not the project root — so the local
# `asana` package can't be found. Add the project root explicitly, before any
# local imports, so this works regardless of cwd or how it's invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass  # python-dotenv not installed — fall back to whatever's already in the shell env

from asana.client import AsanaConfig, AsanaClient


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/register_asana_webhook.py <public-https-target-url>")
        return 1

    target_url = sys.argv[1]
    if not target_url.startswith("https://"):
        print(f"Refusing: target URL must be https:// (got {target_url!r}).")
        return 1

    config = AsanaConfig.from_env()
    if not config.has_credentials:
        print(
            "ASANA_API_KEY and ASANA_PROJECT_ID must be set in .env before running this script."
        )
        return 1

    print(f"Registering webhook: project={config.project_gid} target={target_url}")
    print("Waiting for Asana's handshake against your endpoint — make sure the app is running...")

    client = AsanaClient(config)
    try:
        webhook = client.create_webhook(target_url)
    except Exception as exc:
        print(f"FAILED: {exc}")
        print(
            "Common causes: the app isn't running / isn't reachable at that URL yet, "
            "or the handshake route (/webhooks/asana/task-completed) errored."
        )
        return 1

    print("Webhook registered successfully.")
    print(f"  gid:      {webhook.get('gid')}")
    print(f"  resource: {webhook.get('resource')}")
    print(f"  target:   {webhook.get('target')}")
    print(f"  active:   {webhook.get('active')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
