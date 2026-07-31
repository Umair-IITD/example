"""
tests/test_sprint2632_asana_ticket_content.py

Sprint 2.63.2 — Asana ticket content, sections, and custom fields.

Context: Umair deleted the placeholder Asana tasks and asked for the "Support
Escalation" project to be organized into proper sections, with tickets that
carry full observation/evidence content, proper headings, and real priority —
enough for a dev to fix an issue from the Asana ticket alone.

Scope:
  - asana/client.py: create_section(), get_project_sections(),
    set_task_progress(), custom_fields on create_task(), html_notes support,
    section placement via memberships.
  - case_engine/engineering/service.py: _build_description() rewritten to use
    InvestigationResult's real shape (was silently empty before — read a
    "summary" key that never existed) and produce valid Asana rich-text HTML.
  - case_engine/engineering/service.py: notify_asana_progress().

Sections:
  A — AsanaClient section/custom-field/html_notes plumbing
  B — _build_description() content + XML validity
  C — notify_asana_progress()
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from asana.client import (
    ASANA_PRIORITY_FIELD_GID,
    ASANA_TASK_PROGRESS_FIELD_GID,
    AsanaClient,
    AsanaConfig,
    priority_custom_field_payload,
    task_progress_custom_field_payload,
)
from case_engine.engineering.models import EngineeringPriority
from case_engine.engineering.service import EngineeringEscalationService, _build_description


def _make_config(**overrides: Any) -> AsanaConfig:
    base = dict(api_key="k", project_gid="proj-456", workspace_gid="")
    base.update(overrides)
    return AsanaConfig(**base)


def _fake_http(captured: dict[str, Any], response_data: dict[str, Any]):
    fake = MagicMock()
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=False)

    def fake_post(url, *, json=None, headers=None, **kw):
        captured["method"] = "POST"
        captured["url"] = url
        captured["json"] = json
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"data": response_data}
        return resp

    def fake_get(url, *, headers=None, **kw):
        captured["method"] = "GET"
        captured["url"] = url
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"data": response_data}
        return resp

    def fake_put(url, *, json=None, headers=None, **kw):
        captured["method"] = "PUT"
        captured["url"] = url
        captured["json"] = json
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"data": response_data}
        return resp

    fake.post = fake_post
    fake.get = fake_get
    fake.put = fake_put
    return fake


# ---------------------------------------------------------------------------
# Section A — AsanaClient plumbing
# ---------------------------------------------------------------------------

class TestA_AsanaClientPlumbing:

    def test_A1_priority_custom_field_payload_maps_critical_to_high(self):
        payload = priority_custom_field_payload("CRITICAL")
        assert payload == {ASANA_PRIORITY_FIELD_GID: "1217014318241282"}  # High option gid

    def test_A2_priority_custom_field_payload_maps_low(self):
        payload = priority_custom_field_payload("LOW")
        assert payload == {ASANA_PRIORITY_FIELD_GID: "1217014318241284"}  # Low option gid

    def test_A3_priority_custom_field_unknown_defaults_medium(self):
        payload = priority_custom_field_payload("NOT_A_REAL_PRIORITY")
        assert payload == {ASANA_PRIORITY_FIELD_GID: "1217014318241283"}  # Medium option gid

    def test_A4_task_progress_custom_field_payload(self):
        payload = task_progress_custom_field_payload("Done")
        assert payload == {ASANA_TASK_PROGRESS_FIELD_GID: "1217014651940486"}

    def test_A5_create_task_sends_html_notes_when_requested(self):
        config = _make_config()
        client = AsanaClient(config)
        captured: dict[str, Any] = {}
        fake_http = _fake_http(captured, {"gid": "t1"})

        with patch("asana.client.httpx.Client", return_value=fake_http):
            client.create_task("Title", "<body><h1>Hi</h1></body>", "HIGH", html_notes=True)

        data = captured["json"]["data"]
        assert data["html_notes"] == "<body><h1>Hi</h1></body>"
        assert "notes" not in data

    def test_A6_create_task_sends_plain_notes_by_default(self):
        config = _make_config()
        client = AsanaClient(config)
        captured: dict[str, Any] = {}
        fake_http = _fake_http(captured, {"gid": "t1"})

        with patch("asana.client.httpx.Client", return_value=fake_http):
            client.create_task("Title", "plain text", "MEDIUM")

        data = captured["json"]["data"]
        assert data["notes"] == "plain text"
        assert "html_notes" not in data

    def test_A7_create_task_includes_real_priority_custom_field(self):
        config = _make_config()
        client = AsanaClient(config)
        captured: dict[str, Any] = {}
        fake_http = _fake_http(captured, {"gid": "t1"})

        with patch("asana.client.httpx.Client", return_value=fake_http):
            client.create_task("Title", "D", "CRITICAL")

        cf = captured["json"]["data"]["custom_fields"]
        assert cf[ASANA_PRIORITY_FIELD_GID] == "1217014318241282"  # High
        assert cf[ASANA_TASK_PROGRESS_FIELD_GID] == "1217014651940482"  # Not Started

    def test_A8_create_task_places_in_section_via_memberships(self):
        config = _make_config()
        client = AsanaClient(config)
        captured: dict[str, Any] = {}
        fake_http = _fake_http(captured, {"gid": "t1"})

        with patch("asana.client.httpx.Client", return_value=fake_http):
            client.create_task("Title", "D", "MEDIUM", section_gid="sec-1")

        data = captured["json"]["data"]
        assert data["memberships"] == [{"project": "proj-456", "section": "sec-1"}]
        assert "projects" not in data

    def test_A9_create_task_falls_back_to_config_section_gid(self):
        config = _make_config(new_ticket_section_gid="sec-default")
        client = AsanaClient(config)
        captured: dict[str, Any] = {}
        fake_http = _fake_http(captured, {"gid": "t1"})

        with patch("asana.client.httpx.Client", return_value=fake_http):
            client.create_task("Title", "D", "MEDIUM")

        data = captured["json"]["data"]
        assert data["memberships"] == [{"project": "proj-456", "section": "sec-default"}]

    def test_A10_create_task_no_section_falls_back_to_projects_list(self):
        config = _make_config()
        client = AsanaClient(config)
        captured: dict[str, Any] = {}
        fake_http = _fake_http(captured, {"gid": "t1"})

        with patch("asana.client.httpx.Client", return_value=fake_http):
            client.create_task("Title", "D", "MEDIUM")

        data = captured["json"]["data"]
        assert data["projects"] == ["proj-456"]
        assert "memberships" not in data

    def test_A11_create_section_posts_to_sections_endpoint(self):
        config = _make_config()
        client = AsanaClient(config)
        captured: dict[str, Any] = {}
        fake_http = _fake_http(captured, {"gid": "sec-99", "name": "In Progress"})

        with patch("asana.client.httpx.Client", return_value=fake_http):
            result = client.create_section("In Progress")

        assert captured["url"].endswith("/projects/proj-456/sections")
        assert captured["json"]["data"]["name"] == "In Progress"
        assert result["gid"] == "sec-99"

    def test_A12_get_project_sections_returns_list(self):
        config = _make_config()
        client = AsanaClient(config)
        captured: dict[str, Any] = {}
        fake_http = _fake_http(captured, [{"gid": "s1", "name": "New"}])

        with patch("asana.client.httpx.Client", return_value=fake_http):
            result = client.get_project_sections()

        assert result == [{"gid": "s1", "name": "New"}]

    def test_A13_set_task_progress_puts_custom_field(self):
        config = _make_config()
        client = AsanaClient(config)
        captured: dict[str, Any] = {}
        fake_http = _fake_http(captured, {"gid": "t1"})

        with patch("asana.client.httpx.Client", return_value=fake_http):
            result = client.set_task_progress("t1", "Done")

        assert captured["method"] == "PUT"
        assert captured["json"]["data"]["custom_fields"][ASANA_TASK_PROGRESS_FIELD_GID] == "1217014651940486"
        assert result["gid"] == "t1"

    def test_A14_set_task_progress_never_raises_on_failure(self):
        config = _make_config()
        client = AsanaClient(config)
        fake_http = MagicMock()
        fake_http.__enter__ = MagicMock(return_value=fake_http)
        fake_http.__exit__ = MagicMock(return_value=False)
        fake_http.put.side_effect = httpx.ConnectError("down")

        with patch("asana.client.httpx.Client", return_value=fake_http):
            result = client.set_task_progress("t1", "Done")  # must not raise

        assert result == {}


# ---------------------------------------------------------------------------
# Section B — _build_description() content + XML validity
# ---------------------------------------------------------------------------

class TestB_DescriptionBuilder:

    def _full_investigation(self) -> dict[str, Any]:
        return {
            "observation": "Customer reported OTP not received.\nSession shows 3 failed attempts.",
            "evidence": {
                "total_items": 2,
                "success_count": 1,
                "items": [
                    {
                        "tool_name": "get_session_details",
                        "success": True,
                        "payload": {"session_id": "SESS-123", "status": "FAILED"},
                    },
                    {
                        "tool_name": "get_failure_reason",
                        "success": False,
                        "error_message": "timeout after 30s",
                    },
                ],
            },
        }

    def _full_root_cause(self) -> dict[str, Any]:
        return {
            "category": "OTP_GATEWAY_TIMEOUT",
            "confidence": 0.82,
            "explanation": "Gateway did not respond within SLA.",
            "recommended_action": "ESCALATE_TO_ENGINEERING",
        }

    def _build(self, **overrides: Any) -> str:
        args = dict(
            case_id="case-abc123456789",
            topic="OTP_DELIVERY_FAILURE",
            freshdesk_ticket_id="197416",
            priority=EngineeringPriority.CRITICAL,
            investigation_result=self._full_investigation(),
            root_cause=self._full_root_cause(),
            sop_steps=["Checked gateway status page"],
            escalation_reason="L1 could not resolve automatically.",
        )
        args.update(overrides)
        return _build_description(**args)

    def test_B1_output_is_valid_xml(self):
        body = self._build()
        ET.fromstring(body)  # raises on malformed XML

    def test_B2_minimal_input_is_still_valid_xml(self):
        body = _build_description(
            case_id="c1", topic="UNKNOWN", freshdesk_ticket_id="1",
            priority=EngineeringPriority.MEDIUM, investigation_result=None,
            root_cause=None, sop_steps=None, escalation_reason="",
        )
        ET.fromstring(body)

    def test_B3_contains_priority_and_topic_in_heading(self):
        body = self._build()
        assert "[CRITICAL] OTP_DELIVERY_FAILURE" in body
        assert "<h1>" in body

    def test_B4_contains_real_observation_text_not_empty(self):
        """Regression guard: the old 'summary' key never existed in
        InvestigationResult.to_dict(), so this section always rendered empty."""
        body = self._build()
        assert "Customer reported OTP not received." in body
        assert "Session shows 3 failed attempts." in body

    def test_B5_contains_evidence_items_with_payload_values(self):
        body = self._build()
        assert "get_session_details" in body
        assert "SESS-123" in body
        assert "get_failure_reason" in body
        assert "timeout after 30s" in body

    def test_B6_contains_root_cause_explanation_and_confidence(self):
        body = self._build()
        assert "OTP_GATEWAY_TIMEOUT" in body
        assert "82%" in body
        assert "Gateway did not respond within SLA." in body
        assert "ESCALATE_TO_ENGINEERING" in body

    def test_B7_contains_sop_steps(self):
        body = self._build()
        assert "Checked gateway status page" in body

    def test_B8_escapes_html_special_characters(self):
        body = self._build(escalation_reason="L1 could not resolve <script> & \"quote\".")
        assert "<script>" not in body  # must be escaped, not literal
        assert "&lt;script&gt;" in body
        ET.fromstring(body)  # still valid XML despite special chars

    def test_B9_only_uses_asana_allowed_tags(self):
        """No <p> or <br> — Asana's rich text has neither (developers.asana.com/docs/rich-text)."""
        body = self._build()
        assert "<p>" not in body
        assert "<br" not in body
        allowed = {"body", "h1", "h2", "hr", "strong", "em", "ul", "ol", "li", "pre", "a"}
        root = ET.fromstring(body)
        for el in root.iter():
            tag = el.tag.split("}")[-1]
            assert tag in allowed, f"disallowed tag: {tag}"

    def test_B10_freshdesk_ticket_id_included(self):
        body = self._build()
        assert "197416" in body


# ---------------------------------------------------------------------------
# Section C — notify_asana_progress()
# ---------------------------------------------------------------------------

class TestC_NotifyAsanaProgress:

    def test_C1_calls_set_task_progress_with_external_id(self):
        mock_asana = MagicMock()
        svc = EngineeringEscalationService(asana_client=mock_asana)
        result = svc.create_ticket(case=None, topic="X", freshdesk_ticket_id="fd-1")
        ticket = result.ticket

        svc.notify_asana_progress(ticket, "Done")

        mock_asana.set_task_progress.assert_called_once_with(ticket.external_id, "Done")

    def test_C2_noop_when_no_external_id(self):
        mock_asana = MagicMock()
        svc = EngineeringEscalationService(asana_client=None)  # mock mode, no external_id
        result = svc.create_ticket(case=None, topic="X", freshdesk_ticket_id="fd-2")
        ticket = result.ticket
        assert ticket.external_id is None

        svc.notify_asana_progress(ticket, "Done")  # must not raise
        mock_asana.set_task_progress.assert_not_called()

    def test_C3_never_raises_when_asana_client_throws(self):
        mock_asana = MagicMock()
        mock_asana.create_task.return_value = {"gid": "g1", "project_id": "p1"}
        mock_asana.set_task_progress.side_effect = Exception("boom")
        svc = EngineeringEscalationService(asana_client=mock_asana)
        result = svc.create_ticket(case=None, topic="X", freshdesk_ticket_id="fd-3")

        svc.notify_asana_progress(result.ticket, "Done")  # must not raise
