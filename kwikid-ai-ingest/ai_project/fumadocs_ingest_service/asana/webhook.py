"""
asana/webhook.py

Sprint 2.6x: Asana webhook receiver support — handshake secret persistence,
signature verification, and event parsing.

Design (mirrors freshdesk/verifier.py + freshdesk/idempotency.py patterns):
  - hmac.compare_digest() for all signature comparisons — constant-time.
  - Secret persisted to a small local JSON file so it survives process
    restarts (Asana does NOT resend the secret after the initial handshake).
  - Never raises from parsing helpers — malformed payloads yield an empty
    result rather than propagating an exception into the webhook route.

Asana webhook lifecycle (see developers.asana.com/docs/webhooks-guide):
  1. We call AsanaClient.create_webhook(target_url) — POST /webhooks.
  2. Asana immediately POSTs a handshake request to target_url carrying an
     X-Hook-Secret header. Our route must echo that same header back with a
     200/204 before the POST /webhooks call (step 1) returns 201 Created.
  3. Every subsequent event delivery carries an X-Hook-Signature header —
     HMAC-SHA256(secret, raw_body) hex digest — which we verify here.
  4. A "task marked complete" event is: action="changed", resource.resource_type
     == "task", change.field == "completed", change.new_value == True.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

_DEFAULT_STORE_PATH = "./data/asana_webhook_secrets.json"


# ── Secret store ─────────────────────────────────────────────────────────────

class AsanaWebhookSecretStore:
    """
    Persists the Asana webhook handshake secret to a local JSON file.

    Keyed by resource_gid (the Asana project GID the webhook watches), so a
    single file can hold secrets for more than one registered webhook if
    needed later. Simple file-backed store — proportionate to a single
    internal project at current scale; can be swapped for Supabase later
    following the same graceful-degradation shape as
    freshdesk/idempotency.py::WebhookIdempotencyStore if that becomes useful.

    Never raises: read/write failures are logged and degrade to "no secret
    found", which causes signature verification to fail closed (reject).
    """

    def __init__(self, path: str | None = None) -> None:
        self._path = Path(path or os.getenv("ASANA_WEBHOOK_SECRET_STORE_PATH", _DEFAULT_STORE_PATH))

    def get(self, resource_gid: str) -> str | None:
        try:
            if not self._path.exists():
                return None
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data.get(resource_gid)
        except Exception as exc:
            LOGGER.warning("asana.webhook_secret_store.get failed error=%s", exc)
            return None

    def set(self, resource_gid: str, secret: str) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data: dict[str, str] = {}
            if self._path.exists():
                try:
                    data = json.loads(self._path.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            data[resource_gid] = secret
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            LOGGER.info("asana.webhook_secret_store.set resource_gid=%s", resource_gid)
        except Exception as exc:
            LOGGER.error("asana.webhook_secret_store.set failed error=%s", exc)


# ── Signature verification ────────────────────────────────────────────────────

def verify_signature(secret: str, raw_body: bytes, provided_signature: str | None) -> bool:
    """
    Verify an Asana X-Hook-Signature header.

    Asana computes: HMAC-SHA256(secret, raw_body) → hex digest.
    Never raises. Returns False on any malformed input (fail closed).
    """
    if not secret or not provided_signature:
        return False
    try:
        expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, provided_signature.strip())
    except Exception as exc:
        LOGGER.warning("asana.webhook.verify_signature failed error=%s", exc)
        return False


# ── Event parsing ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CompletedTaskEvent:
    task_gid: str
    resource_type: str


def extract_completed_task_events(payload: dict[str, Any]) -> list[CompletedTaskEvent]:
    """
    Parse an Asana webhook delivery body and return task GIDs whose
    'completed' field changed to True.

    Never raises — malformed/unexpected payload shapes yield an empty list.
    """
    results: list[CompletedTaskEvent] = []
    try:
        events = payload.get("events", [])
        if not isinstance(events, list):
            return results
        for event in events:
            if not isinstance(event, dict):
                continue
            resource = event.get("resource") or {}
            change = event.get("change") or {}
            if (
                resource.get("resource_type") == "task"
                and change.get("field") == "completed"
                and change.get("new_value") is True
            ):
                task_gid = resource.get("gid")
                if task_gid:
                    results.append(CompletedTaskEvent(task_gid=str(task_gid), resource_type="task"))
    except Exception as exc:
        LOGGER.warning("asana.webhook.extract_completed_task_events failed error=%s", exc)
    return results


# ── Event idempotency ─────────────────────────────────────────────────────────

class AsanaEventIdempotencyStore:
    """
    In-memory deduplication guard for Asana task-completed events.

    Asana redelivers events on transient failures. An in-memory set keyed by
    task_gid prevents duplicate Freshdesk writes within a single process
    lifetime. Post-restart redeliveries are safe — the engineering ticket will
    already be RESOLVED and the handler exits early without re-writing.

    Never raises.
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def has_processed(self, task_gid: str) -> bool:
        return task_gid in self._seen

    def mark_processed(self, task_gid: str) -> None:
        self._seen.add(task_gid)
