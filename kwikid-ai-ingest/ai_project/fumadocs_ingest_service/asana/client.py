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
  create_task(title, description, priority, html_notes=, section_gid=) →
      {"gid": str, "project_id": str}
  get_task(task_gid) → {"gid": str, "completed": bool, ...}

Sprint 2.63.2: added create_section(), get_project_sections(), and
set_task_progress() for real Asana board organization (see
scripts/setup_asana_project.py) — Priority and Task Progress are now real
Asana custom fields on the "Support Escalation" project, not just tags.
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

# Sprint 2.63.2: real Asana custom fields on the "Support Escalation" project
# (workspace getkwikid.com), discovered via get_project(include_sections=true)
# and used to drive the project's native Priority / Task Progress columns
# instead of (in addition to) plain tags — see scripts/setup_asana_project.py
# for how to re-discover these if the project is ever recreated.
#
# "Priority" custom field (enum: High / Medium / Low). EngineeringPriority has
# 4 tiers (CRITICAL/HIGH/MEDIUM/LOW); Asana's field only has 3, so CRITICAL and
# HIGH both map to "High" — CRITICAL tickets additionally get a "[CRITICAL]"
# title prefix (see EngineeringEscalationService) so they're still visually
# distinguishable from ordinary HIGH tickets in list/board view.
ASANA_PRIORITY_FIELD_GID = "1217014318241281"
_PRIORITY_OPTION_GIDS: dict[str, str] = {
    "High":   "1217014318241282",
    "Medium": "1217014318241283",
    "Low":    "1217014318241284",
}
_PRIORITY_TO_ASANA_OPTION: dict[str, str] = {
    "CRITICAL": "High",
    "HIGH":     "High",
    "MEDIUM":   "Medium",
    "LOW":      "Low",
}

# "Task Progress" custom field (enum: Not Started / In Progress / Waiting /
# Deferred / Done). Set to "Not Started" at ticket creation and "Done" by the
# resolution webhook once the closure loop completes — independent of the
# section (which dev moves manually) and of the raw `completed` checkbox
# (which is what the webhook actually watches).
ASANA_TASK_PROGRESS_FIELD_GID = "1217014318241286"
_TASK_PROGRESS_OPTION_GIDS: dict[str, str] = {
    "Not Started": "1217014651940482",
    "In Progress": "1217014651940483",
    "Waiting":     "1217014651940484",
    "Deferred":    "1217014651940485",
    "Done":        "1217014651940486",
}


def priority_custom_field_payload(priority: str) -> dict[str, str]:
    """Build the {field_gid: option_gid} entry for the Priority custom field."""
    option_name = _PRIORITY_TO_ASANA_OPTION.get(priority.upper(), "Medium")
    return {ASANA_PRIORITY_FIELD_GID: _PRIORITY_OPTION_GIDS[option_name]}


def task_progress_custom_field_payload(progress: str) -> dict[str, str]:
    """Build the {field_gid: option_gid} entry for the Task Progress custom field."""
    option_gid = _TASK_PROGRESS_OPTION_GIDS.get(progress, _TASK_PROGRESS_OPTION_GIDS["Not Started"])
    return {ASANA_TASK_PROGRESS_FIELD_GID: option_gid}


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
    # Sprint 2.63.2: section new tickets are placed into at creation time.
    # Optional — if unset, tasks are created in the project with no section
    # (backward-compatible with pre-2.63.2 behavior). Set by
    # scripts/setup_asana_project.py on first run.
    new_ticket_section_gid: str = ""

    @classmethod
    def from_env(cls) -> "AsanaConfig":
        return cls(
            api_key=os.environ.get("ASANA_API_KEY", "").strip(),
            project_gid=os.environ.get("ASANA_PROJECT_ID", "").strip(),
            workspace_gid=os.environ.get("ASANA_WORKSPACE_ID", "").strip(),
            timeout_s=float(os.environ.get("ASANA_TIMEOUT_S", "15")),
            new_ticket_section_gid=os.environ.get("ASANA_NEW_TICKET_SECTION_ID", "").strip(),
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
        title:        str,
        description:  str,
        priority:     str = "MEDIUM",
        *,
        html_notes:   bool = False,
        section_gid:  str | None = None,
    ) -> dict[str, Any]:
        """
        Create an Asana task in the configured project.

        Args:
            title:       Task name (short, descriptive — see
                         EngineeringEscalationService for the "[PRIORITY] Topic
                         — Case <id>" convention that makes triage possible from
                         list/board view alone).
            description: Full task body — root cause, evidence, session IDs.
                         Plain text by default; pass html_notes=True if this is
                         already well-formed Asana rich-text HTML (see the
                         allowed-tag whitelist in
                         EngineeringEscalationService._build_html_description).
            priority:    EngineeringPriority.value string (CRITICAL/HIGH/MEDIUM/LOW).
                         Sets BOTH the real "Priority" custom field on the
                         project (Sprint 2.63.2) and a legacy tag for
                         backward-compat with any saved tag-based views.
            html_notes:  If True, `description` is sent as `html_notes` (real
                         Asana rich text — renders headings/bold/lists in the
                         UI) instead of plain-text `notes`.
            section_gid: Optional section to place the task into at creation
                         (falls back to AsanaConfig.new_ticket_section_gid,
                         then to no section at all — never fails on a missing
                         section, since section GIDs are workspace-specific and
                         may not be configured yet).

        Returns:
            {"gid": "<task_gid>", "project_id": "<project_gid>"}

        Raises:
            httpx.HTTPStatusError: on 4xx/5xx responses.
            httpx.TimeoutException: if Asana API does not respond in time.
        """
        data: dict[str, Any] = {"name": title}
        if html_notes:
            data["html_notes"] = description
        else:
            data["notes"] = description

        effective_section = section_gid or self._config.new_ticket_section_gid
        if effective_section:
            # memberships places the task in a specific section within a
            # specific project in one call — the plain "projects" list alone
            # only controls project membership, not section.
            data["memberships"] = [
                {"project": self._config.project_gid, "section": effective_section}
            ]
        else:
            data["projects"] = [self._config.project_gid]

        if self._config.workspace_gid:
            data["workspace"] = self._config.workspace_gid

        priority_tag = _PRIORITY_TO_TAG.get(priority)
        if priority_tag:
            data["tags"] = [priority_tag]

        custom_fields = dict(priority_custom_field_payload(priority))
        custom_fields.update(task_progress_custom_field_payload("Not Started"))
        data["custom_fields"] = custom_fields

        payload: dict[str, Any] = {"data": data}

        with httpx.Client(timeout=self._config.timeout_s) as http:
            response = http.post(
                f"{_ASANA_API_BASE}/tasks",
                json=payload,
                headers=self._headers,
            )
        response.raise_for_status()
        result = response.json().get("data", {})
        task_gid = result.get("gid", "")
        LOGGER.info(
            "asana.create_task gid=%s project=%s section=%s priority=%s",
            task_gid, self._config.project_gid, effective_section, priority,
        )
        return {
            "gid":        task_gid,
            "project_id": self._config.project_gid,
        }

    def create_section(self, name: str) -> dict[str, Any]:
        """
        Create a new section in the configured project.

        Idempotency note: Asana does NOT enforce unique section names — calling
        this twice with the same name creates two sections. Callers (see
        scripts/setup_asana_project.py) should check existing sections via
        get_project(include_sections=true) first and skip creation if a
        section with the same name already exists.

        Returns:
            {"gid": "<section_gid>", "name": "<name>"}

        Raises:
            httpx.HTTPStatusError: on 4xx/5xx responses.
            httpx.TimeoutException: if Asana API does not respond in time.
        """
        payload: dict[str, Any] = {"data": {"name": name}}
        with httpx.Client(timeout=self._config.timeout_s) as http:
            response = http.post(
                f"{_ASANA_API_BASE}/projects/{self._config.project_gid}/sections",
                json=payload,
                headers=self._headers,
            )
        response.raise_for_status()
        data = response.json().get("data", {})
        LOGGER.info("asana.create_section gid=%s name=%s", data.get("gid"), name)
        return data

    def get_project_sections(self) -> list[dict[str, Any]]:
        """
        List sections in the configured project.

        Returns a list of {"gid": ..., "name": ...} dicts (raw Asana section
        resources). Never raises falsely for an empty project — an empty list
        means zero sections, distinct from a request failure (which raises).
        """
        with httpx.Client(timeout=self._config.timeout_s) as http:
            response = http.get(
                f"{_ASANA_API_BASE}/projects/{self._config.project_gid}/sections",
                headers=self._headers,
            )
        response.raise_for_status()
        return response.json().get("data", [])

    def set_task_progress(self, task_gid: str, progress: str) -> dict[str, Any]:
        """
        Set the "Task Progress" custom field on an existing task.

        Args:
            task_gid: Asana task GID.
            progress: One of "Not Started" / "In Progress" / "Waiting" /
                      "Deferred" / "Done" (see _TASK_PROGRESS_OPTION_GIDS).

        Never raises — returns {} on failure (this is a non-critical UI nicety,
        not something that should block the resolution-closure flow).
        """
        payload: dict[str, Any] = {
            "data": {"custom_fields": task_progress_custom_field_payload(progress)}
        }
        try:
            with httpx.Client(timeout=self._config.timeout_s) as http:
                response = http.put(
                    f"{_ASANA_API_BASE}/tasks/{task_gid}",
                    json=payload,
                    headers=self._headers,
                )
            response.raise_for_status()
            data = response.json().get("data", {})
            LOGGER.info("asana.set_task_progress gid=%s progress=%s", task_gid, progress)
            return data
        except Exception as exc:
            LOGGER.warning(
                "asana.set_task_progress failed gid=%s progress=%s error=%s",
                task_gid, progress, exc,
            )
            return {}

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
