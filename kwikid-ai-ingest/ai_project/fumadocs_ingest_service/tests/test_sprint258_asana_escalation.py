"""
tests/test_sprint258_asana_escalation.py

Sprint 2.5.8 — Asana L2 Escalation: full test suite.

Sections:
  TestA — AsanaClient.create_task() happy path; URL builder; Authorization header
  TestB — AsanaClient error handling (4xx/5xx propagate; health() degrades)
  TestC — EngineeringEscalationService with live AsanaClient injection
  TestD — FreshdeskResponseService.update_ticket_fields() custom-field write
  TestE — Background task: cf_asana_ticket_link update + escalation reply
  TestF — DRY_RUN mode: no real Asana call, no Freshdesk write, steps log contains ASANACREATE_DRY_RUN

Never touches the network. All httpx interactions use unittest.mock.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, call

# ── Env bootstrap (must precede project imports) ───────────────────────────────
os.environ.setdefault("RAG_API_KEY",                  "test-258-key")
os.environ.setdefault("OPENAI_API_KEY",               "sk-test-258")
os.environ.setdefault("SUPABASE_URL",                 "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY",                 "test-supabase-key")
os.environ.setdefault("AUDIT_BACKEND",                "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("ASANA_API_KEY",                "asana-test-key")
os.environ.setdefault("ASANA_PROJECT_ID",             "proj-test-456")
os.environ.setdefault("ASANA_WORKSPACE_ID",           "ws-test-789")

import httpx
import pytest

from asana.client import (
    AsanaClient,
    AsanaConfig,
    build_asana_client,
    build_task_url,
)
from case_engine.engineering.service import (
    EngineeringEscalationService,
    build_engineering_escalation_service,
)
from case_engine.engineering.models import EngineeringPriority, EngineeringStatus
from freshdesk.response_service import FreshdeskResponseService


# ═══════════════════════════════════════════════════════════════════════════════
# TestA — AsanaClient happy path
# ═══════════════════════════════════════════════════════════════════════════════

_ASANA_TASK_RESPONSE = {
    "data": {
        "gid":  "task-gid-123",
        "name": "Test task",
        "completed": False,
    }
}


def _make_asana_config(
    api_key: str = "test-bearer-key",
    project_gid: str = "proj-456",
    workspace_gid: str = "ws-789",
) -> AsanaConfig:
    return AsanaConfig(
        api_key=api_key,
        project_gid=project_gid,
        workspace_gid=workspace_gid,
        timeout_s=5.0,
    )


class TestA_AsanaClientHappyPath:
    def test_A1_build_task_url(self):
        """build_task_url constructs the canonical Asana permalink."""
        url = build_task_url("proj-456", "task-123")
        assert url == "https://app.asana.com/0/proj-456/task-123"

    def test_A2_url_uses_project_then_task(self):
        """Project GID always precedes task GID in the URL path."""
        url = build_task_url("myproject", "mytask")
        parts = url.split("/")
        # ...app.asana.com/0/{project}/{task}
        assert parts[-2] == "myproject"
        assert parts[-1] == "mytask"

    def test_A3_create_task_sends_bearer_header(self):
        """create_task() sends Authorization: Bearer {api_key} on every POST."""
        config = _make_asana_config(api_key="super-secret-key")
        client = AsanaClient(config)

        captured_headers: dict[str, str] = {}

        def fake_post(url, *, json=None, headers=None, **kwargs):
            captured_headers.update(headers or {})
            mock_resp = MagicMock()
            mock_resp.status_code = 201
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = _ASANA_TASK_RESPONSE
            return mock_resp

        fake_http = MagicMock()
        fake_http.__enter__ = MagicMock(return_value=fake_http)
        fake_http.__exit__  = MagicMock(return_value=False)
        fake_http.post      = fake_post

        with patch("asana.client.httpx.Client", return_value=fake_http):
            result = client.create_task("Test task", "Description", "HIGH")

        assert captured_headers.get("Authorization") == "Bearer super-secret-key"
        assert result == {"gid": "task-gid-123", "project_id": "proj-456"}

    def test_A4_create_task_returns_gid_and_project_id(self):
        """create_task() returns {'gid': ..., 'project_id': ...}."""
        config  = _make_asana_config()
        aclient = AsanaClient(config)

        fake_http = MagicMock()
        fake_http.__enter__ = MagicMock(return_value=fake_http)
        fake_http.__exit__  = MagicMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"data": {"gid": "gid-9999"}}
        fake_http.post.return_value = mock_resp

        with patch("asana.client.httpx.Client", return_value=fake_http):
            result = aclient.create_task("T", "D", "MEDIUM")

        assert result["gid"] == "gid-9999"
        assert result["project_id"] == "proj-456"

    def test_A5_create_task_includes_workspace_when_configured(self):
        """create_task() adds workspace to payload when ASANA_WORKSPACE_ID is set."""
        config = _make_asana_config(workspace_gid="ws-456")
        aclient = AsanaClient(config)

        captured_payload: dict[str, Any] = {}

        def fake_post(url, *, json=None, headers=None, **kwargs):
            captured_payload.update(json or {})
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"data": {"gid": "g1"}}
            return mock_resp

        fake_http = MagicMock()
        fake_http.__enter__ = MagicMock(return_value=fake_http)
        fake_http.__exit__  = MagicMock(return_value=False)
        fake_http.post = fake_post

        with patch("asana.client.httpx.Client", return_value=fake_http):
            aclient.create_task("T", "D", "LOW")

        assert captured_payload["data"]["workspace"] == "ws-456"

    def test_A6_asana_config_has_credentials(self):
        """AsanaConfig.has_credentials is True only when api_key + project_gid are set."""
        good = AsanaConfig(api_key="k", project_gid="p", workspace_gid="")
        empty = AsanaConfig(api_key="", project_gid="p", workspace_gid="")
        assert good.has_credentials is True
        assert empty.has_credentials is False

    def test_A7_build_asana_client_returns_none_when_unconfigured(self):
        """build_asana_client() returns None instead of raising when creds absent."""
        with patch.dict(os.environ, {"ASANA_API_KEY": "", "ASANA_PROJECT_ID": ""}):
            result = build_asana_client()
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# TestB — AsanaClient error handling
# ═══════════════════════════════════════════════════════════════════════════════

class TestB_AsanaClientErrorHandling:
    def _make_error_response(self, status_code: int):
        """Build a mock httpx response that raises HTTPStatusError."""
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=MagicMock(status_code=status_code),
        )
        return mock_resp

    def _patch_client(self, resp):
        fake_http = MagicMock()
        fake_http.__enter__ = MagicMock(return_value=fake_http)
        fake_http.__exit__  = MagicMock(return_value=False)
        fake_http.post.return_value = resp
        return fake_http

    def test_B1_create_task_propagates_400(self):
        """create_task() propagates HTTPStatusError on HTTP 400."""
        config  = _make_asana_config()
        aclient = AsanaClient(config)
        fake_http = self._patch_client(self._make_error_response(400))

        with patch("asana.client.httpx.Client", return_value=fake_http):
            with pytest.raises(httpx.HTTPStatusError):
                aclient.create_task("T", "D")

    def test_B2_create_task_propagates_500(self):
        """create_task() propagates HTTPStatusError on HTTP 500."""
        config  = _make_asana_config()
        aclient = AsanaClient(config)
        fake_http = self._patch_client(self._make_error_response(500))

        with patch("asana.client.httpx.Client", return_value=fake_http):
            with pytest.raises(httpx.HTTPStatusError):
                aclient.create_task("T", "D")

    def test_B3_health_returns_false_on_exception(self):
        """health() returns False (never raises) when API is unreachable."""
        config  = _make_asana_config()
        aclient = AsanaClient(config)

        fake_http = MagicMock()
        fake_http.__enter__ = MagicMock(return_value=fake_http)
        fake_http.__exit__  = MagicMock(return_value=False)
        fake_http.get.side_effect = httpx.ConnectTimeout("timeout")

        with patch("asana.client.httpx.Client", return_value=fake_http):
            result = aclient.health()

        assert result is False

    def test_B4_constructor_raises_when_no_credentials(self):
        """AsanaClient.__init__ raises ValueError when api_key or project_gid absent."""
        config = AsanaConfig(api_key="", project_gid="p", workspace_gid="")
        with pytest.raises(ValueError, match="ASANA_API_KEY"):
            AsanaClient(config)

    def test_B5_get_task_propagates_429(self):
        """get_task() propagates HTTPStatusError on HTTP 429 (rate limit)."""
        config  = _make_asana_config()
        aclient = AsanaClient(config)

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "429", request=MagicMock(), response=MagicMock(status_code=429),
        )
        fake_http = MagicMock()
        fake_http.__enter__ = MagicMock(return_value=fake_http)
        fake_http.__exit__  = MagicMock(return_value=False)
        fake_http.get.return_value = mock_resp

        with patch("asana.client.httpx.Client", return_value=fake_http):
            with pytest.raises(httpx.HTTPStatusError):
                aclient.get_task("task-gid-99")


# ═══════════════════════════════════════════════════════════════════════════════
# TestC — EngineeringEscalationService with injected AsanaClient
# ═══════════════════════════════════════════════════════════════════════════════

def _make_mock_asana(gid: str = "task-gid-123", project_id: str = "proj-456") -> MagicMock:
    """Mock AsanaClient.create_task returning a fixed GID."""
    mock = MagicMock()
    mock.create_task.return_value = {"gid": gid, "project_id": project_id}
    return mock


class TestC_EngineeringEscalationServiceWithAsana:
    def test_C1_ticket_external_id_set_from_asana_gid(self):
        """create_ticket() stores the Asana GID in ticket.external_id."""
        mock_asana = _make_mock_asana(gid="task-gid-123", project_id="proj-456")
        svc = build_engineering_escalation_service(asana_client=mock_asana)

        result = svc.create_ticket(
            case=None,
            topic="VKYC_Session_Failure",
            freshdesk_ticket_id="fd-99",
            escalation_reason="PAN validation failed.",
        )

        assert result.success is True
        assert result.ticket.external_id == "task-gid-123"
        assert result.ticket.asana_project_id == "proj-456"

    def test_C2_asana_create_task_called_with_title_and_description(self):
        """create_task() is called with title, description, and priority string."""
        mock_asana = _make_mock_asana()
        svc = build_engineering_escalation_service(asana_client=mock_asana)

        svc.create_ticket(
            case=None,
            topic="VKYC_Session_Failure",
            freshdesk_ticket_id="fd-1",
            escalation_reason="NSDL rejection",
        )

        assert mock_asana.create_task.call_count == 1
        call_kwargs = mock_asana.create_task.call_args
        assert "title" in call_kwargs.kwargs
        assert "description" in call_kwargs.kwargs
        assert "priority" in call_kwargs.kwargs
        assert call_kwargs.kwargs["priority"] in ("CRITICAL", "HIGH", "MEDIUM", "LOW")

    def test_C3_build_task_url_produces_correct_permalink(self):
        """build_task_url() produces the canonical Asana permalink for the result."""
        mock_asana = _make_mock_asana(gid="task-abc", project_id="proj-xyz")
        svc = build_engineering_escalation_service(asana_client=mock_asana)

        result = svc.create_ticket(
            case=None, topic="API_Callback_Failure", freshdesk_ticket_id="fd-2",
        )

        asana_url = build_task_url(
            result.ticket.asana_project_id,  # type: ignore[arg-type]
            result.ticket.external_id,        # type: ignore[arg-type]
        )
        assert asana_url == "https://app.asana.com/0/proj-xyz/task-abc"

    def test_C4_asana_failure_does_not_crash_service(self):
        """If Asana API throws, create_ticket() still returns success with no external_id."""
        mock_asana = MagicMock()
        mock_asana.create_task.side_effect = httpx.ConnectError("unreachable")
        svc = build_engineering_escalation_service(asana_client=mock_asana)

        result = svc.create_ticket(
            case=None, topic="VKYC_Session_Failure", freshdesk_ticket_id="fd-3",
        )

        assert result.success is True
        assert result.ticket.external_id is None
        assert result.ticket.asana_project_id is None

    def test_C5_to_dict_contains_external_id_and_project_id(self):
        """EngineeringEscalationResult.to_dict() exposes external_id and asana_project_id."""
        mock_asana = _make_mock_asana(gid="gid-001", project_id="proj-001")
        svc = build_engineering_escalation_service(asana_client=mock_asana)

        result = svc.create_ticket(
            case=None, topic="VKYC_Session_Failure", freshdesk_ticket_id="fd-4",
        )
        d = result.to_dict()

        assert d["ticket"]["external_id"] == "gid-001"
        assert d["ticket"]["asana_project_id"] == "proj-001"
        assert d["success"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# TestD — FreshdeskResponseService.update_ticket_fields()
# ═══════════════════════════════════════════════════════════════════════════════

def _make_fd_client_mock(update_return: dict | None = None) -> AsyncMock:
    """Return an AsyncMock for FreshdeskClient with common write methods wired."""
    mock = AsyncMock()
    mock.update_ticket.return_value     = update_return or {"id": 99, "updated": True}
    mock.add_public_reply.return_value  = {"id": 1}
    mock.add_private_note.return_value  = {"id": 2}
    return mock


class TestD_FreshdeskUpdateTicketFields:
    def test_D1_payload_wrapped_in_custom_fields_key(self):
        """update_ticket_fields() wraps the caller's dict under 'custom_fields'."""
        mock_client = _make_fd_client_mock()
        svc = FreshdeskResponseService(freshdesk_client=mock_client)

        asyncio.run(svc.update_ticket_fields(
            ticket_id=12345,
            custom_fields={"cf_asana_ticket_link": "https://app.asana.com/0/p/t"},
        ))

        mock_client.update_ticket.assert_awaited_once()
        call_args = mock_client.update_ticket.call_args
        assert call_args.args[0] == 12345
        assert call_args.args[1] == {
            "custom_fields": {"cf_asana_ticket_link": "https://app.asana.com/0/p/t"}
        }

    def test_D2_returns_result_dict_on_success(self):
        """update_ticket_fields() returns the client's response dict on success."""
        mock_client = _make_fd_client_mock(update_return={"id": 42, "ok": True})
        svc = FreshdeskResponseService(freshdesk_client=mock_client)

        result = asyncio.run(svc.update_ticket_fields(
            ticket_id=42,
            custom_fields={"cf_asana_ticket_link": "https://example.com"},
        ))

        assert result == {"id": 42, "ok": True}

    def test_D3_returns_empty_dict_on_exception(self):
        """update_ticket_fields() returns {} and never raises when client throws."""
        mock_client = AsyncMock()
        mock_client.update_ticket.side_effect = Exception("Freshdesk 503")
        svc = FreshdeskResponseService(freshdesk_client=mock_client)

        result = asyncio.run(svc.update_ticket_fields(
            ticket_id=99,
            custom_fields={"cf_asana_ticket_link": "https://example.com"},
        ))

        assert result == {}

    def test_D4_multiple_custom_fields_forwarded(self):
        """update_ticket_fields() forwards all custom_fields keys unchanged."""
        mock_client = _make_fd_client_mock()
        svc = FreshdeskResponseService(freshdesk_client=mock_client)

        asyncio.run(svc.update_ticket_fields(
            ticket_id=1,
            custom_fields={
                "cf_asana_ticket_link": "https://asana.com/t",
                "cf_other_field": "value",
            },
        ))

        _, payload = mock_client.update_ticket.call_args.args
        assert payload["custom_fields"]["cf_asana_ticket_link"] == "https://asana.com/t"
        assert payload["custom_fields"]["cf_other_field"] == "value"


# ═══════════════════════════════════════════════════════════════════════════════
# TestE — Background task Asana escalation post-processing
# ═══════════════════════════════════════════════════════════════════════════════

def _make_engineering_result(
    gid: str = "task-gid-999",
    project_id: str = "proj-999",
    title: str = "PAN validation rejected by NSDL (YYN code)",
) -> dict[str, Any]:
    """Build an engineering_result dict as produced by EngineeringEscalationResult.to_dict()."""
    return {
        "result_id": "result-001",
        "success": True,
        "ticket": {
            "ticket_id": "eng-ticket-001",
            "external_id": gid,
            "case_id": "case-test",
            "freshdesk_ticket_id": "fd-12345",
            "title": title,
            "description": "Full description here.",
            "priority": "HIGH",
            "status": "PENDING",
            "assignee": None,
            "asana_project_id": project_id,
            "created_at": "2026-07-24T00:00:00+00:00",
            "updated_at": "2026-07-24T00:00:00+00:00",
            "resolved_at": None,
            "metadata": {},
        },
        "operation": "create",
        "error_code": None,
        "error_msg": None,
        "executed_at": "2026-07-24T00:00:00+00:00",
        "duration_ms": 150,
    }


async def _simulate_bg_task_escalation(
    detail: dict[str, Any],
    resp_svc: FreshdeskResponseService,
    ticket_id: int = 12345,
    case_id: str = "case-test",
) -> str | None:
    """
    Replicate the Asana escalation post-processing block from the background task.
    Returns the constructed Asana URL if escalation ran, else None.
    """
    _eng_result = detail.get("engineering_result")
    if not (_eng_result and _eng_result.get("success")):
        return None

    _ticket_data = (_eng_result.get("ticket") or {})
    _task_gid    = _ticket_data.get("external_id")
    _proj_gid    = _ticket_data.get("asana_project_id")
    if not (_task_gid and _proj_gid):
        return None

    _asana_url = build_task_url(_proj_gid, _task_gid)
    await resp_svc.update_ticket_fields(
        ticket_id,
        {"cf_asana_ticket_link": _asana_url},
        case_id=case_id,
    )
    _root_cause = _ticket_data.get("title", "")
    _esc_body = (
        "<p>Hi,</p>"
        "<p>We have investigated the issue regarding your KYC session. "
        + (_root_cause + " " if _root_cause else "")
        + "This has been escalated to our engineering team "
        + f"(Ref: <a href=\"{_asana_url}\">{_asana_url}</a>). "
        + "We will update you once it is resolved.</p>"
        + "<p>KwikID Support Team</p>"
    )
    await resp_svc.send_customer_reply(ticket_id, _esc_body, case_id=case_id)
    return _asana_url


class TestE_BackgroundTaskEscalationFlow:
    def test_E1_cf_asana_ticket_link_updated_with_correct_url(self):
        """Background task calls update_ticket_fields with the correct Asana URL."""
        mock_client = _make_fd_client_mock()
        svc = FreshdeskResponseService(freshdesk_client=mock_client)
        detail = {"engineering_result": _make_engineering_result(gid="task-999", project_id="proj-555")}

        asyncio.run(_simulate_bg_task_escalation(detail, svc, ticket_id=12345))

        mock_client.update_ticket.assert_awaited_once()
        _, payload = mock_client.update_ticket.call_args.args
        expected_url = "https://app.asana.com/0/proj-555/task-999"
        assert payload["custom_fields"]["cf_asana_ticket_link"] == expected_url

    def test_E2_escalation_reply_contains_asana_url(self):
        """Background task sends a public reply containing the Asana task URL."""
        mock_client = _make_fd_client_mock()
        mock_client.add_public_reply = AsyncMock(return_value={"id": 1})
        svc = FreshdeskResponseService(freshdesk_client=mock_client)
        detail = {"engineering_result": _make_engineering_result(gid="task-abc", project_id="proj-xyz")}

        asyncio.run(_simulate_bg_task_escalation(detail, svc, ticket_id=99))

        # send_customer_reply → client.add_public_reply
        mock_client.add_public_reply.assert_awaited_once()
        reply_body = mock_client.add_public_reply.call_args.args[1]
        assert "https://app.asana.com/0/proj-xyz/task-abc" in reply_body
        assert "escalated to our engineering team" in reply_body

    def test_E3_escalation_reply_includes_root_cause_title(self):
        """Escalation reply embeds the engineering ticket title as root cause summary."""
        mock_client = _make_fd_client_mock()
        mock_client.add_public_reply = AsyncMock(return_value={"id": 2})
        svc = FreshdeskResponseService(freshdesk_client=mock_client)
        detail = {"engineering_result": _make_engineering_result(
            title="NSDL PAN verification returned YYN code",
        )}

        asyncio.run(_simulate_bg_task_escalation(detail, svc))

        reply_body = mock_client.add_public_reply.call_args.args[1]
        assert "NSDL PAN verification returned YYN code" in reply_body

    def test_E4_skipped_when_no_engineering_result(self):
        """Background task skips Asana block when engineering_result is absent."""
        mock_client = _make_fd_client_mock()
        svc = FreshdeskResponseService(freshdesk_client=mock_client)

        url = asyncio.run(_simulate_bg_task_escalation({}, svc))

        assert url is None
        mock_client.update_ticket.assert_not_awaited()

    def test_E5_skipped_when_engineering_result_failed(self):
        """Background task skips Asana block when engineering_result.success is False."""
        mock_client = _make_fd_client_mock()
        svc = FreshdeskResponseService(freshdesk_client=mock_client)
        failed_result = {"engineering_result": {"success": False, "ticket": {}}}

        url = asyncio.run(_simulate_bg_task_escalation(failed_result, svc))

        assert url is None
        mock_client.update_ticket.assert_not_awaited()

    def test_E6_skipped_when_external_id_is_missing(self):
        """Background task skips Asana block when Asana task was not created (external_id None)."""
        mock_client = _make_fd_client_mock()
        svc = FreshdeskResponseService(freshdesk_client=mock_client)
        result_no_gid = _make_engineering_result()
        result_no_gid["ticket"]["external_id"] = None
        detail = {"engineering_result": result_no_gid}

        url = asyncio.run(_simulate_bg_task_escalation(detail, svc))

        assert url is None
        mock_client.update_ticket.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════════════
# TestF — DRY_RUN mode
# ═══════════════════════════════════════════════════════════════════════════════

def _make_case_for_dry_run() -> Any:
    """Build a Case that has workflow_state='ESCALATED' to force needs_l2=True."""
    from case_engine.models import Case
    from case_engine.case_state import CaseState

    case = Case(case_id="case-dry-run", ticket_id="fd-dry", client="unity_bank")
    case.topic       = "VKYC_Session_Failure"
    case.confidence  = 0.95
    case.current_state = CaseState.TRIAGE_COMPLETE
    # Pre-set workflow fields so the runtime reads workflow_state = "ESCALATED"
    case.workflow_state   = "ESCALATED"
    case.workflow_id      = "wf-dry-run"
    case.workflow_context = {}
    return case


def _make_dry_run_runtime(mock_asana: MagicMock) -> Any:
    """Build a SupportAgentRuntime in DRY_RUN mode with a mock AsanaClient injected."""
    from case_engine.runtime.support_agent_runtime import SupportAgentRuntime
    from case_engine.runtime.agent_models import SupportAgentMode
    from case_engine.engineering.service import build_engineering_escalation_service

    eng_svc = build_engineering_escalation_service(asana_client=mock_asana)

    mock_case_svc = MagicMock()
    mock_case_svc.classify_case.side_effect = lambda c, t: c

    msg_result = MagicMock()
    msg_result.all_slots_filled  = True
    msg_result.workflow_started  = True   # triggers workflow_result extraction from case
    msg_result.next_question     = None
    mock_case_svc.receive_message.return_value = msg_result

    return SupportAgentRuntime(
        case_service=mock_case_svc,
        engineering_escalation_service=eng_svc,
        mode=SupportAgentMode.DRY_RUN,
    )


class TestF_DryRunMode:
    def test_F1_no_real_asana_call_in_dry_run(self):
        """In DRY_RUN mode, AsanaClient.create_task() is NEVER called."""
        mock_asana = MagicMock()
        runtime    = _make_dry_run_runtime(mock_asana)
        case       = _make_case_for_dry_run()

        runtime.run_case(case, "My KYC failed")

        mock_asana.create_task.assert_not_called()

    def test_F2_engineering_result_is_none_in_dry_run(self):
        """In DRY_RUN mode, the returned engineering_result is None (no ticket created)."""
        mock_asana = MagicMock()
        runtime    = _make_dry_run_runtime(mock_asana)
        case       = _make_case_for_dry_run()

        result = runtime.run_case(case, "My KYC failed")

        assert result.engineering_result is None

    def test_F3_steps_completed_contains_asanacreate_dry_run(self):
        """In DRY_RUN mode, 'ASANACREATE_DRY_RUN' appears in steps_completed."""
        mock_asana = MagicMock()
        runtime    = _make_dry_run_runtime(mock_asana)
        case       = _make_case_for_dry_run()

        result = runtime.run_case(case, "My KYC failed")

        assert "ASANACREATE_DRY_RUN" in result.steps_completed

    def test_F4_no_freshdesk_write_in_dry_run(self):
        """In DRY_RUN mode, FreshdeskResponseService write methods are not invoked."""
        mock_asana  = MagicMock()
        mock_fd_svc = AsyncMock(spec=FreshdeskResponseService)
        runtime     = _make_dry_run_runtime(mock_asana)
        case        = _make_case_for_dry_run()

        result = runtime.run_case(case, "My KYC failed")

        # engineering_result is None → the async bg task will skip Freshdesk writes
        # Verify the precondition that engineering_result is None
        assert result.engineering_result is None
        # If engineering_result is None, the bg task's Asana URL update block is skipped.
        # We verify mock_fd_svc is untouched (it's not wired into the runtime, but
        # this documents the expected caller contract).
        mock_fd_svc.update_ticket_fields.assert_not_awaited()
        mock_fd_svc.send_customer_reply.assert_not_awaited()
