"""
asana/client.py

Sprint 2.5.8: Live Asana REST API client for L2 engineering task creation.

Design:
  - All methods are synchronous (blocking httpx.Client).
  - Callers MUST wrap with asyncio.to_thread() — FastAPI runs on an async
    event loop and blocking calls must not run on it directly.
  - Never raises to the caller — exceptions propagate so callers can decide
    whether to log-and-continue or fail fast.
  - Auth: Authorization: Bearer <ASANA_API_KEY> (standard Asana PAT/OAuth).
  - Task URL: https://app.asana.com/0/{project_gid}/{task_gid}

Interface expected by EngineeringEscalationService._create_ticket_internal():
  create_task(title, description, priority) → {"gid": str, "project_id": str}
  get_task(task_gid) → {"gid": str, "completed": bool, ...}
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

LOGGER = logging.getLogger(__name__)

_ASANA_API_BASE = "https://app.asana.com/api/1.0"

# Priority label mapping: EngineeringPriority.value → Asana task "tags" hint
_PRIORITY_TO_TAG: dict[str, str] = {
    "CRITICAL": "P0-critical",
    "HIGH":     "P1-high",
    "MEDIUM":   "P2-medium",
    "LOW":      "P3-low",
}


# ── Config ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AsanaConfig:
    """
    Asana client configuration.

    All values are read from environment variables. Missing or empty values
    result in a config with has_credentials=False, which causes AsanaClient
    to raise ValueError on construction (detected at startup, not at call time).
    """
    api_key:      str
    project_gid:  str
    workspace_gid: str
    timeout_s:    float = 15.0

    @classmethod
    def from_env(cls) -> "AsanaConfig":
        return cls(
            api_key=os.environ.get("ASANA_API_KEY", "").strip(),
            project_gid=os.environ.get("ASANA_PROJECT_ID", "").strip(),
            workspace_gid=os.environ.get("ASANA_WORKSPACE_ID", "").strip(),
            timeout_s=float(os.environ.get("ASANA_TIMEOUT_S", "15")),
        )

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.project_gid)


# ── URL builder (pure, no I/O) ────────────────────────────────────────────────

def build_task_url(project_gid: str, task_gid: str) -> str:
    """
    Construct the Asana task permalink.

    Standard Asana task URL format: https://app.asana.com/0/{project_gid}/{task_gid}

    Args:
        project_gid: The GID of the Asana project the task was created in.
        task_gid:    The GID returned by the Asana task creation response.

    Returns:
        Full HTTPS URL string.
    """
    return f"https://app.asana.com/0/{project_gid}/{task_gid}"


# ── Client ────────────────────────────────────────────────────────────────────

class AsanaClient:
    """
    Synchronous Asana REST API v1 client.

    IMPORTANT: All methods are blocking. Always call via asyncio.to_thread()
    when used from async code (FastAPI handlers, SupportAgentRuntime).

    Usage:
        client = AsanaClient(config)
        result = await asyncio.to_thread(client.create_task, title, desc, priority)
    """

    def __init__(self, config: AsanaConfig) -> None:
        if not config.has_credentials:
            raise ValueError(
                "AsanaClient: ASANA_API_KEY and ASANA_PROJECT_ID are required. "
                "Check .env / environment."
            )
        self._config = config
        self._headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        }

    def create_task(
        self,
        title:       str,
        description: str,
        priority:    str = "MEDIUM",
    ) -> dict[str, Any]:
        """
        Create an Asana task in the configured project.

        Args:
            title:       Task name (short, descriptive).
            description: Full task body — root cause, evidence, session IDs.
            priority:    EngineeringPriority.value string (CRITICAL/HIGH/MEDIUM/LOW).

        Returns:
            {"gid": "<task_gid>", "project_id": "<project_gid>"}

        Raises:
            httpx.HTTPStatusError: on 4xx/5xx responses.
            httpx.TimeoutException: if Asana API does not respond in time.
        """
        payload: dict[str, Any] = {
            "data": {
                "name":     title,
                "notes":    description,
                "projects": [self._config.project_gid],
            }
        }
        if self._config.workspace_gid:
            payload["data"]["workspace"] = self._config.workspace_gid

        priority_tag = _PRIORITY_TO_TAG.get(priority)
        if priority_tag:
            payload["data"]["tags"] = [priority_tag]

        with httpx.Client(timeout=self._config.timeout_s) as http:
            response = http.post(
                f"{_ASANA_API_BASE}/tasks",
                json=payload,
                headers=self._headers,
            )
        response.raise_for_status()
        data = response.json().get("data", {})
        task_gid = data.get("gid", "")
        LOGGER.info(
            "asana.create_task gid=%s project=%s",
            task_gid, self._config.project_gid,
        )
        return {
            "gid":        task_gid,
            "project_id": self._config.project_gid,
        }

    def get_task(self, task_gid: str) -> dict[str, Any]:
        """
        Fetch an existing Asana task by GID.

        Used by EngineeringEscalationService.sync_status() for status polling.

        Args:
            task_gid: The Asana task GID returned by create_task().

        Returns:
            Raw Asana task data dict (includes "gid", "completed", "name", etc.)

        Raises:
            httpx.HTTPStatusError: on 4xx/5xx responses.
            httpx.TimeoutException: if Asana API does not respond in time.
        """
        with httpx.Client(timeout=self._config.timeout_s) as http:
            response = http.get(
                f"{_ASANA_API_BASE}/tasks/{task_gid}",
                headers=self._headers,
            )
        response.raise_for_status()
        data = response.json().get("data", {})
        LOGGER.info(
            "asana.get_task gid=%s completed=%s",
            task_gid, data.get("completed"),
        )
        return data

    def create_webhook(self, target_url: str, resource_gid: str | None = None) -> dict[str, Any]:
        """
        Register a webhook against an Asana resource (defaults to the
        configured project).

        IMPORTANT — ordering: Asana's POST /webhooks call blocks until it has
        successfully completed a handshake against target_url (it POSTs an
        X-Hook-Secret header to target_url and expects that header echoed
        back within the same request). The receiving route MUST already be
        deployed and reachable before calling this method, or the handshake
        will fail and this call will raise.

        Args:
            target_url:   Publicly reachable HTTPS URL of the webhook receiver
                           (e.g. an ngrok URL in dev, or the real deployment
                           URL in production).
            resource_gid: Asana object to watch (project GID). Defaults to
                           the configured project.

        Returns:
            Raw Asana webhook resource dict (includes "gid", "resource",
            "target", "active").

        Raises:
            httpx.HTTPStatusError: on 4xx/5xx responses (including handshake
                failure surfaced by Asana as a 4xx on this call).
            httpx.TimeoutException: if the handshake round-trip does not
                complete within the timeout window.
        """
        payload: dict[str, Any] = {
            "data": {
                "resource": resource_gid or self._config.project_gid,
                "target":   target_url,
            }
        }
        with httpx.Client(timeout=self._config.timeout_s) as http:
            response = http.post(
                f"{_ASANA_API_BASE}/webhooks",
                json=payload,
                headers=self._headers,
            )
        response.raise_for_status()
        data = response.json().get("data", {})
        LOGGER.info(
            "asana.create_webhook gid=%s resource=%s target=%s",
            data.get("gid"), payload["data"]["resource"], target_url,
        )
        return data

    def health(self) -> bool:
        """
        Check connectivity to Asana API.

        Makes a lightweight GET to /users/me. Returns True on 2xx, False otherwise.
        Never raises.
        """
        try:
            with httpx.Client(timeout=5.0) as http:
                response = http.get(
                    f"{_ASANA_API_BASE}/users/me",
                    headers=self._headers,
                )
            return response.status_code == 200
        except Exception as exc:
            LOGGER.warning("asana.health check failed: %s", exc)
            return False


# ── Factory ───────────────────────────────────────────────────────────────────

def build_asana_client() -> AsanaClient | None:
    """
    Build an AsanaClient from environment variables.

    Returns None if ASANA_API_KEY or ASANA_PROJECT_ID is not configured,
    so callers can degrade gracefully to mock/DRY_RUN mode.

    Never raises.
    """
    try:
        config = AsanaConfig.from_env()
        if not config.has_credentials:
            LOGGER.info(
                "asana.build_asana_client: credentials not configured — "
                "EngineeringEscalationService will run in mock mode"
            )
            return None
        client = AsanaClient(config)
        LOGGER.info(
            "asana.build_asana_client: client built project_gid=%s",
            config.project_gid,
        )
        return client
    except Exception as exc:
        LOGGER.warning("asana.build_asana_client failed error=%s", exc)
        return None
