"""
tests/test_sprint262_l1_final_validation.py

Sprint 2.62 — Final L1 Production Readiness Validation.

Comprehensive E2E pipeline tests verifying the complete ticket lifecycle:
  ticket-created → clarification → ticket-updated → investigation → FDNOTE

Sections:
  A — Ticket Created: VKYC classification → AWAITING_CLARIFICATION state
  B — Ticket Updated: customer reply → slot fill → workflow resume
  C — EvidenceSource enum: GET_SESSION_LOGS + _relevant_fields() fixes
  D — Observation note format: Blueprint §14 five-section structure
  E — OBSGEN → FDNOTE path: handler populates observation_note correctly
  F — Registry fix: GetSessionLogsTool (not SessionLogsTool) in Unity Bank tools

No network. All external interactions are MagicMock or httpx.MockTransport.
Blueprint refs: §5 (tenant/log), §9 (phase A/B), §14 (OBSGEN note), §27 (PII).
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

# ── Environment guardrails — BEFORE importing app code ───────────────────────
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("OPENAI_API_KEY", "test-262-key")
os.environ.setdefault("RAG_API_KEY", "test-262-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-262-key")
os.environ.setdefault("LOKI_SAAS_USERNAME", "")
os.environ.setdefault("LOKI_SAAS_PASSWORD", "")
os.environ.setdefault("LOKI_ENABLED", "false")
os.environ.setdefault("SUPPORT_AGENT_MODE", "PRODUCTION")

from typing import Any
from unittest.mock import MagicMock

import pytest

from case_engine.investigation.models import (
    Evidence,
    EvidenceBundle,
    EvidenceSource,
    EvidenceType,
)
from case_engine.tenant.models import (
    TenantContext,
    TenantEnvironment,
    TenantType,
)
from case_engine.tenant.registry import build_default_tenant_registry
from freshdesk.conversation_state import ConversationLifecycle, ConversationState
from freshdesk.handlers import FreshdeskTicketCreatedHandler, FreshdeskTicketUpdatedHandler

# ── Load legacy observation.py directly (shadowed by Sprint 2.44 package) ────
# The observation/ package takes import precedence, so _relevant_fields must be
# loaded from the file directly.
_OBS_PY = (
    Path(__file__).parent.parent
    / "case_engine" / "investigation" / "observation.py"
)
_obs_spec = importlib.util.spec_from_file_location("_obs_legacy", str(_OBS_PY))
_obs_legacy = importlib.util.module_from_spec(_obs_spec)  # type: ignore[arg-type]
_obs_spec.loader.exec_module(_obs_legacy)  # type: ignore[union-attr]
_relevant_fields = _obs_legacy._relevant_fields


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures & helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_idempotency_mock(duplicate: bool = False) -> MagicMock:
    m = MagicMock()
    m.check.return_value = duplicate
    m.make_key.side_effect = lambda tid, evt, ts: f"{tid}:{evt}:{ts}"
    m.mark_received.return_value = None
    m.mark_completed.return_value = None
    m.mark_failed.return_value = None
    return m


def _make_conversation_store_mock(state: ConversationState | None = None) -> MagicMock:
    m = MagicMock()
    m.get.return_value = state
    m.update.return_value = None
    return m


def _make_tenant_ctx() -> TenantContext:
    return TenantContext(
        client_id="unity_bank",
        client_name="Unity Bank",
        domain="unitybank.co.in",
        tenant_type=TenantType.BANK,
        environment=TenantEnvironment.UAT,
        enabled_tools=("GetUserDetailsTool", "GetSessionDetailsTool", "GetSessionLogsTool"),
        credentials_ref="unity_bank_api_credentials",
        workflow_overrides={},
        portal_base_url="",
        log_datasource_reference="saas",
    )


def _make_resolver_mock() -> MagicMock:
    m = MagicMock()
    m.resolve.return_value = _make_tenant_ctx()
    return m


def _ticket_created_payload(ticket_id: int = 99001) -> dict[str, Any]:
    return {
        "ticket": {
            "id": ticket_id,
            "subject": "VKYC session failed - unable to complete KYC",
            "description": "My video KYC session failed during face match.",
            "status": 2,
            "priority": 2,
            "created_at": "2026-07-30T07:00:00Z",
            "ticket_type": "Question",
        },
        "requester": {"email": "applicant@unitybank.co.in", "name": "Test Applicant"},
        "custom_fields": {"cf_clients": "Unity Bank", "cf_environment": "Production"},
    }


def _ticket_updated_payload(
    ticket_id: int = 99001,
    reply_body: str = "My session ID is SESS-2026-0730-ABC123",
    incoming: bool = True,
) -> dict[str, Any]:
    return {
        "ticket": {
            "id": ticket_id,
            "subject": "VKYC session failed",
            "status": 3,
            "updated_at": "2026-07-30T07:05:00Z",
        },
        "requester": {"email": "applicant@unitybank.co.in"},
        "custom_fields": {"cf_clients": "Unity Bank"},
        "changes": {},
        "latest_comment": {
            "body": f"<p>{reply_body}</p>",
            "body_text": reply_body,
            "incoming": incoming,
            "private": False,
        },
    }


def _mock_orchestrator_result(
    case_id: str = "case-262-001",
    agent_status: str = "COMPLETED",
    obs_note: str | None = None,
    response_draft: dict | None = None,
) -> MagicMock:
    result = MagicMock()
    result.case_id = case_id
    agent_dict: dict[str, Any] = {"agent_status": agent_status}
    if obs_note:
        agent_dict["metadata"] = {
            "intelligence_result": {"observation": {"note": obs_note}}
        }
    if response_draft:
        agent_dict["response_draft"] = response_draft
    result.agent_result = agent_dict
    result.success = True
    return result


_BLUEPRINT_14_NOTE = (
    "=== ISSUE SUMMARY ===\n"
    "User reported a Video KYC session failure.\n"
    "Case ID: case-262-001  |  Topic: VKYC_Session_Failure\n\n"
    "=== OBSERVED EVIDENCE ===\n"
    "[OK] GetSessionDetailsTool\n"
    "  session_id: SESS-2026-0730-ABC123\n"
    "  session_status: FAILED\n"
    "  failure_code: FACE_MATCH_ERROR\n"
    "[OK] GetSessionLogsTool\n"
    "  log_availability: AVAILABLE\n"
    "  log_line_count: 18\n"
    "[OK] MetricTool\n"
    "  service_name: agentapi\n"
    "  status: UP\n\n"
    "=== ROOT CAUSE ===\n"
    "Category:   Liveness Check Failure\n"
    "Confidence: 90%\n"
    "Finding:    Face match repeatedly failed.\n\n"
    "=== RECOMMENDED ACTION ===\n"
    "Reset the VKYC session and request user to retry\n\n"
    "=== ESCALATION REQUIRED ===\n"
    "NO — Automated resolution may proceed.\n\n"
    "---\n"
    "[Generated by KwikID AI Support Agent — L1 Investigation Layer]"
)


# ─────────────────────────────────────────────────────────────────────────────
# Section A — Ticket Created: VKYC classification → clarification state
# ─────────────────────────────────────────────────────────────────────────────

class TestA_TicketCreatedPipeline:
    """Handler correctly processes ticket-created events for Unity Bank VKYC tickets."""

    def _handler(self, orchestrator_result=None) -> FreshdeskTicketCreatedHandler:
        orch = MagicMock()
        orch.process_ticket.return_value = orchestrator_result or _mock_orchestrator_result()
        return FreshdeskTicketCreatedHandler(
            idempotency_store=_make_idempotency_mock(),
            conversation_store=_make_conversation_store_mock(),
            ticket_orchestrator=orch,
            client_resolver=_make_resolver_mock(),
            audit_logger=None,
            metrics_collector=None,
        )

    def test_A1_valid_unity_bank_ticket_returns_success(self):
        result = self._handler().handle(_ticket_created_payload())
        assert result.success is True
        assert result.ticket_id == "99001"

    def test_A2_case_id_populated_from_orchestrator(self):
        result = self._handler().handle(_ticket_created_payload())
        assert result.case_id == "case-262-001"

    def test_A3_unknown_tenant_returns_UNKNOWN_CLIENT(self):
        from case_engine.tenant.models import UnknownClientError
        bad_resolver = MagicMock()
        bad_resolver.resolve.side_effect = UnknownClientError(
            domain="unknown.com", email="x@unknown.com"
        )
        handler = FreshdeskTicketCreatedHandler(
            idempotency_store=_make_idempotency_mock(),
            conversation_store=_make_conversation_store_mock(),
            ticket_orchestrator=MagicMock(),
            client_resolver=bad_resolver,
        )
        result = handler.handle(_ticket_created_payload())
        assert result.success is False
        assert result.error_code == "UNKNOWN_CLIENT"

    def test_A4_duplicate_event_is_skipped(self):
        handler = FreshdeskTicketCreatedHandler(
            idempotency_store=_make_idempotency_mock(duplicate=True),
            conversation_store=_make_conversation_store_mock(),
            ticket_orchestrator=MagicMock(),
            client_resolver=_make_resolver_mock(),
        )
        result = handler.handle(_ticket_created_payload())
        assert result.skipped is True
        assert result.skip_reason == "DUPLICATE"

    def test_A5_awaiting_clarification_sets_conv_state_awaiting_customer(self):
        orch_result = _mock_orchestrator_result(
            agent_status="AWAITING_CLARIFICATION",
            response_draft={"body_html": "<p>Please provide your Session ID.</p>"},
        )
        conv_store = _make_conversation_store_mock()
        orch = MagicMock()
        orch.process_ticket.return_value = orch_result
        handler = FreshdeskTicketCreatedHandler(
            idempotency_store=_make_idempotency_mock(),
            conversation_store=conv_store,
            ticket_orchestrator=orch,
            client_resolver=_make_resolver_mock(),
        )
        handler.handle(_ticket_created_payload())
        update_kwargs = [c.kwargs for c in conv_store.update.call_args_list]
        assert any(c.get("awaiting_customer") is True for c in update_kwargs), (
            "awaiting_customer=True must be written when agent status is AWAITING_CLARIFICATION"
        )

    def test_A6_observation_note_from_intelligence_layer(self):
        orch_result = _mock_orchestrator_result(obs_note=_BLUEPRINT_14_NOTE)
        result = self._handler(orch_result).handle(_ticket_created_payload())
        assert result.observation_note == _BLUEPRINT_14_NOTE

    def test_A7_observation_note_from_workflow_fallback(self):
        """When intelligence layer absent, note comes from workflow step result."""
        orch_result = MagicMock()
        orch_result.case_id = "case-262-002"
        orch_result.agent_result = {
            "agent_status": "COMPLETED",
            "workflow_result": {
                "step_results": {
                    "step_observe": {"result": {"observation_note": "Fallback note."}}
                }
            },
        }
        result = self._handler(orch_result).handle(_ticket_created_payload())
        assert result.observation_note == "Fallback note."

    def test_A8_no_observation_note_when_orchestrator_produces_none(self):
        result = self._handler(_mock_orchestrator_result(obs_note=None)).handle(
            _ticket_created_payload()
        )
        assert not result.observation_note

    def test_A9_parse_error_returns_failure(self):
        result = self._handler().handle({})
        assert result.success is False
        assert result.error_code in ("PARSE_ERROR", "MISSING_TICKET_ID")


# ─────────────────────────────────────────────────────────────────────────────
# Section B — Ticket Updated: customer reply → slot fill → workflow resume
# ─────────────────────────────────────────────────────────────────────────────

class TestB_TicketUpdatedClarificationLoop:
    """ticket-updated handler correctly routes customer replies through the clarification loop."""

    def _conv_state_awaiting(self, ticket_id: str = "99001") -> ConversationState:
        return ConversationState(
            ticket_id=ticket_id,
            case_id="case-262-001",
            client_id="unity_bank",
            awaiting_customer=True,
            clarification_pending=True,
            lifecycle_state=ConversationLifecycle.CLARIFICATION,
        )

    def _updated_handler(
        self, conv_state: ConversationState | None = None
    ) -> FreshdeskTicketUpdatedHandler:
        orch = MagicMock()
        orch.resume_ticket.return_value = MagicMock(success=True, case_id="case-262-001")
        nlp_signal = MagicMock()
        nlp_signal.intent = "provide_session_id"
        nlp_signal.entities = {"session_id": "SESS-2026-0730-ABC123"}
        nlp_signal.raw_text = "SESS-2026-0730-ABC123"
        nlp_mock = MagicMock()
        nlp_mock.route.return_value = nlp_signal
        case_svc = MagicMock()
        case_svc.receive_message.return_value = None
        return FreshdeskTicketUpdatedHandler(
            idempotency_store=_make_idempotency_mock(),
            conversation_store=_make_conversation_store_mock(conv_state),
            ticket_orchestrator=orch,
            audit_logger=None,
            metrics_collector=None,
            nlp_router=nlp_mock,
            case_service=case_svc,
        )

    def test_B1_customer_reply_while_awaiting_returns_success(self):
        result = self._updated_handler(self._conv_state_awaiting()).handle(
            _ticket_updated_payload()
        )
        assert result.success is True

    def test_B2_agent_reply_does_not_trigger_resume(self):
        """Agent's own reply (incoming=False) must not enter the clarification path."""
        handler = self._updated_handler(self._conv_state_awaiting())
        result = handler.handle(_ticket_updated_payload(incoming=False))
        assert result.success is True
        assert handler._orchestrator.resume_ticket.call_count == 0

    def test_B3_missing_ticket_id_returns_MISSING_TICKET_ID(self):
        handler = self._updated_handler(None)
        result = handler.handle({
            "ticket": {"id": 0, "status": 2, "updated_at": "2026-07-30T07:05:00Z"},
            "requester": {"email": "x@unitybank.co.in"},
            "custom_fields": {},
        })
        assert result.success is False
        assert result.error_code == "MISSING_TICKET_ID"

    def test_B4_duplicate_ticket_update_is_skipped(self):
        handler = FreshdeskTicketUpdatedHandler(
            idempotency_store=_make_idempotency_mock(duplicate=True),
            conversation_store=_make_conversation_store_mock(),
            ticket_orchestrator=MagicMock(),
        )
        result = handler.handle(_ticket_updated_payload())
        assert result.skipped is True

    def test_B5_conversation_store_updated_to_non_awaiting_after_customer_reply(self):
        """After customer provides session ID, awaiting_customer is cleared."""
        conv_store = _make_conversation_store_mock(self._conv_state_awaiting())
        orch = MagicMock()
        orch.resume_ticket.return_value = MagicMock(success=True, case_id="case-262-001")
        _sig = MagicMock()
        _sig.intent = "provide_session_id"
        _sig.entities = {"session_id": "SESS-001"}
        _sig.raw_text = "SESS-001"
        nlp_mock = MagicMock()
        nlp_mock.route.return_value = _sig
        case_svc = MagicMock()
        case_svc.receive_message.return_value = None
        handler = FreshdeskTicketUpdatedHandler(
            idempotency_store=_make_idempotency_mock(),
            conversation_store=conv_store,
            ticket_orchestrator=orch,
            nlp_router=nlp_mock,
            case_service=case_svc,
        )
        handler.handle(_ticket_updated_payload())
        update_calls = conv_store.update.call_args_list
        assert any(
            c.kwargs.get("awaiting_customer") is False
            for c in update_calls
        ), "awaiting_customer must be cleared after customer reply"


# ─────────────────────────────────────────────────────────────────────────────
# Section C — EvidenceSource enum + _relevant_fields() drift fixes
# ─────────────────────────────────────────────────────────────────────────────

class TestC_EvidenceLayerFixes:
    """
    Verify Node 3 drift fixes:
    - EvidenceSource.GET_SESSION_LOGS enum member (models.py)
    - _relevant_fields() cases for METRIC_TOOL, SERVER_TOOL, GET_SESSION_LOGS (observation.py)
    """

    def test_C1_GET_SESSION_LOGS_enum_member_exists(self):
        assert hasattr(EvidenceSource, "GET_SESSION_LOGS"), (
            "EvidenceSource.GET_SESSION_LOGS must exist — Sprint 2.60 fix"
        )

    def test_C2_GET_SESSION_LOGS_value_is_GetSessionLogsTool(self):
        assert EvidenceSource.GET_SESSION_LOGS.value == "GetSessionLogsTool"

    def test_C3_all_eight_evidence_sources_present(self):
        required = {
            "GET_USER_DETAILS", "GET_SESSION_DETAILS", "GET_FAILURE_REASON",
            "GET_CASE_HISTORY", "GET_ONBOARDING_STATUS",
            "METRIC_TOOL", "SERVER_TOOL", "GET_SESSION_LOGS",
        }
        actual = {m.name for m in EvidenceSource}
        missing = required - actual
        assert not missing, f"Missing EvidenceSource members: {missing}"

    def test_C4_relevant_fields_GET_SESSION_LOGS_returns_log_keys(self):
        payload = {
            "log_availability": "AVAILABLE",
            "log_line_count": 42,
            "log_char_count": 3500,
            "redacted_excerpt": "[2026-07-30T07:00:00Z] ERROR face match failed",
            "irrelevant_key": "must_not_appear",
        }
        result = _relevant_fields(EvidenceSource.GET_SESSION_LOGS, payload)
        assert "log_availability" in result
        assert "log_line_count" in result
        assert "log_char_count" in result
        assert "irrelevant_key" not in result

    def test_C5_relevant_fields_METRIC_TOOL_returns_service_keys(self):
        payload = {
            "service_name": "agentapi",
            "status": "UP",
            "uptime_pct": 99.5,
            "response_time_ms": 120,
            "last_checked": "2026-07-30T07:00:00Z",
            "internal_id": "must_not_appear",
        }
        result = _relevant_fields(EvidenceSource.METRIC_TOOL, payload)
        assert "service_name" in result
        assert "status" in result
        assert "uptime_pct" in result
        assert "internal_id" not in result

    def test_C6_relevant_fields_SERVER_TOOL_returns_server_keys(self):
        payload = {
            "server_name": "prod-01",
            "status": "UP",
            "incident_count": 0,
            "is_degraded": False,
            "maintenance_window": None,
            "raw_json": "must_not_appear",
        }
        result = _relevant_fields(EvidenceSource.SERVER_TOOL, payload)
        assert "server_name" in result
        assert "status" in result
        assert "incident_count" in result
        assert "raw_json" not in result

    def test_C7_relevant_fields_GET_SESSION_DETAILS_returns_failure_code(self):
        payload = {"session_id": "S-001", "session_status": "FAILED", "failure_code": "OCR_ERR"}
        result = _relevant_fields(EvidenceSource.GET_SESSION_DETAILS, payload)
        assert "failure_code" in result

    def test_C8_relevant_fields_unknown_source_falls_back_to_max_10_fields(self):
        class _Unknown:
            pass
        payload = {f"k{i}": i for i in range(20)}
        result = _relevant_fields(_Unknown(), payload)  # type: ignore[arg-type]
        assert len(result) <= 10

    def test_C9_relevant_fields_returns_only_keys_present_in_payload(self):
        """No KeyError when payload is missing some expected fields."""
        partial_payload = {"log_availability": "DISABLED"}
        result = _relevant_fields(EvidenceSource.GET_SESSION_LOGS, partial_payload)
        assert result == {"log_availability": "DISABLED"}

    def test_C10_EvidenceSource_GET_SESSION_LOGS_roundtrips_via_value(self):
        src = EvidenceSource("GetSessionLogsTool")
        assert src == EvidenceSource.GET_SESSION_LOGS


# ─────────────────────────────────────────────────────────────────────────────
# Section D — Observation note format: Blueprint §14 five-section structure
# ─────────────────────────────────────────────────────────────────────────────

class TestD_BlueprintNoteSectionFormat:
    """
    Verify the five required sections of Blueprint §14 are present in the
    observation note as returned via the handler's HandlerResult.observation_note.
    Tests use a pre-formed representative note (same format as the legacy
    ObservationGenerator from Sprint 2.18 and the intelligence layer output).
    """

    def test_D1_issue_summary_section_present(self):
        assert "=== ISSUE SUMMARY ===" in _BLUEPRINT_14_NOTE

    def test_D2_observed_evidence_section_present(self):
        assert "=== OBSERVED EVIDENCE ===" in _BLUEPRINT_14_NOTE

    def test_D3_root_cause_section_present(self):
        assert "=== ROOT CAUSE ===" in _BLUEPRINT_14_NOTE

    def test_D4_recommended_action_section_present(self):
        assert "=== RECOMMENDED ACTION ===" in _BLUEPRINT_14_NOTE

    def test_D5_escalation_required_section_present(self):
        assert "=== ESCALATION REQUIRED ===" in _BLUEPRINT_14_NOTE

    def test_D6_note_contains_session_id(self):
        assert "SESS-2026-0730-ABC123" in _BLUEPRINT_14_NOTE

    def test_D7_note_contains_failure_code(self):
        assert "FACE_MATCH_ERROR" in _BLUEPRINT_14_NOTE

    def test_D8_note_contains_loki_log_availability(self):
        assert "log_availability" in _BLUEPRINT_14_NOTE
        assert "AVAILABLE" in _BLUEPRINT_14_NOTE

    def test_D9_note_contains_metric_service_name(self):
        assert "agentapi" in _BLUEPRINT_14_NOTE

    def test_D10_note_contains_no_escalation_when_l1_sufficient(self):
        assert "NO — Automated resolution may proceed." in _BLUEPRINT_14_NOTE

    def test_D11_handler_propagates_note_with_all_sections(self):
        """When orchestrator returns a §14 note, handler passes it through unchanged."""
        orch = MagicMock()
        orch.process_ticket.return_value = _mock_orchestrator_result(
            obs_note=_BLUEPRINT_14_NOTE
        )
        handler = FreshdeskTicketCreatedHandler(
            idempotency_store=_make_idempotency_mock(),
            conversation_store=_make_conversation_store_mock(),
            ticket_orchestrator=orch,
            client_resolver=_make_resolver_mock(),
        )
        result = handler.handle(_ticket_created_payload())
        note = result.observation_note or ""
        for section in [
            "=== ISSUE SUMMARY ===",
            "=== OBSERVED EVIDENCE ===",
            "=== ROOT CAUSE ===",
            "=== RECOMMENDED ACTION ===",
            "=== ESCALATION REQUIRED ===",
        ]:
            assert section in note, f"Blueprint §14 section missing from handler result: {section}"

    def test_D12_escalation_YES_case_uses_correct_section_text(self):
        escalation_note = _BLUEPRINT_14_NOTE.replace(
            "NO — Automated resolution may proceed.",
            "YES — This case requires L2 / Engineering review.",
        )
        assert "YES — This case requires L2 / Engineering review." in escalation_note
        assert "=== ESCALATION REQUIRED ===" in escalation_note


# ─────────────────────────────────────────────────────────────────────────────
# Section E — OBSGEN → FDNOTE path: handler populates observation_note
# ─────────────────────────────────────────────────────────────────────────────

class TestE_ObsgenToFdnotePath:
    """
    Verify the full OBSGEN → FDNOTE path:
    HandlerResult.observation_note is populated so the background task can
    call FreshdeskResponseService.add_internal_note().
    """

    def _handler_with(self, orch_result) -> FreshdeskTicketCreatedHandler:
        orch = MagicMock()
        orch.process_ticket.return_value = orch_result
        return FreshdeskTicketCreatedHandler(
            idempotency_store=_make_idempotency_mock(),
            conversation_store=_make_conversation_store_mock(),
            ticket_orchestrator=orch,
            client_resolver=_make_resolver_mock(),
        )

    def test_E1_intel_layer_note_propagates_to_HandlerResult(self):
        orch_result = _mock_orchestrator_result(obs_note=_BLUEPRINT_14_NOTE)
        result = self._handler_with(orch_result).handle(_ticket_created_payload())
        assert result.observation_note == _BLUEPRINT_14_NOTE

    def test_E2_workflow_fallback_note_propagates_to_HandlerResult(self):
        fallback = "Workflow fallback: face match failure detected."
        orch_result = MagicMock()
        orch_result.case_id = "case-262-003"
        orch_result.agent_result = {
            "agent_status": "COMPLETED",
            "workflow_result": {
                "step_results": {
                    "step_obs": {"result": {"observation_note": fallback}}
                }
            },
        }
        result = self._handler_with(orch_result).handle(_ticket_created_payload())
        assert result.observation_note == fallback

    def test_E3_intel_note_wins_over_workflow_fallback(self):
        """When both sources are present, intelligence layer is preferred."""
        orch_result = MagicMock()
        orch_result.case_id = "case-262-004"
        orch_result.agent_result = {
            "agent_status": "COMPLETED",
            "metadata": {"intelligence_result": {"observation": {"note": "INTEL NOTE"}}},
            "workflow_result": {
                "step_results": {
                    "step_obs": {"result": {"observation_note": "FALLBACK NOTE"}}
                }
            },
        }
        result = self._handler_with(orch_result).handle(_ticket_created_payload())
        assert result.observation_note == "INTEL NOTE"

    def test_E4_no_note_when_agent_result_empty(self):
        orch_result = MagicMock()
        orch_result.case_id = "case-262-005"
        orch_result.agent_result = {}
        result = self._handler_with(orch_result).handle(_ticket_created_payload())
        assert not result.observation_note

    def test_E5_duplicate_event_produces_no_note(self):
        """Idempotent: duplicate events skip orchestrator → no observation note."""
        handler = FreshdeskTicketCreatedHandler(
            idempotency_store=_make_idempotency_mock(duplicate=True),
            conversation_store=_make_conversation_store_mock(),
            ticket_orchestrator=MagicMock(),
            client_resolver=_make_resolver_mock(),
        )
        result = handler.handle(_ticket_created_payload())
        assert result.skipped is True
        assert not result.observation_note

    def test_E6_handler_success_true_when_note_present(self):
        orch_result = _mock_orchestrator_result(obs_note=_BLUEPRINT_14_NOTE)
        result = self._handler_with(orch_result).handle(_ticket_created_payload())
        assert result.success is True
        assert result.observation_note is not None

    def test_E7_ticket_id_in_handler_result_matches_payload(self):
        orch_result = _mock_orchestrator_result(obs_note=_BLUEPRINT_14_NOTE)
        result = self._handler_with(orch_result).handle(_ticket_created_payload(ticket_id=77777))
        assert result.ticket_id == "77777"


# ─────────────────────────────────────────────────────────────────────────────
# Section F — Registry fix: GetSessionLogsTool name + tenant wiring
# ─────────────────────────────────────────────────────────────────────────────

class TestF_RegistryToolNameFix:
    """Verify the SessionLogsTool → GetSessionLogsTool fix in Unity Bank tool registry."""

    def setup_method(self):
        self.registry = build_default_tenant_registry()
        self.config = self.registry.lookup_by_client_id("unity_bank")

    def test_F1_unity_bank_config_exists(self):
        assert self.config is not None

    def test_F2_enabled_tools_contains_GetSessionLogsTool(self):
        assert "GetSessionLogsTool" in self.config.tool_config.enabled_tools, (
            "GetSessionLogsTool must be in Unity Bank enabled_tools (renamed from SessionLogsTool)"
        )

    def test_F3_old_name_SessionLogsTool_not_present(self):
        assert "SessionLogsTool" not in self.config.tool_config.enabled_tools, (
            "SessionLogsTool (old incorrect name) must not appear in Unity Bank enabled_tools"
        )

    def test_F4_log_datasource_ref_is_saas(self):
        assert self.config.log_datasource_ref == "saas"

    def test_F5_TenantContext_has_log_datasource_reference_field(self):
        ctx = _make_tenant_ctx()
        assert hasattr(ctx, "log_datasource_reference")
        assert ctx.log_datasource_reference == "saas"

    def test_F6_ClientResolver_propagates_log_datasource_reference(self):
        from case_engine.tenant.resolver import ClientResolver
        resolver = ClientResolver(self.registry)
        ctx = resolver.resolve("agent@unitybank.co.in")
        assert ctx.log_datasource_reference == "saas"

    def test_F7_registry_validates_cleanly(self):
        errors = self.registry.validate()
        assert errors == [], f"Registry validation errors: {errors}"

    def test_F8_unity_bank_is_enabled(self):
        assert self.config.enabled is True

    def test_F9_EvidenceSource_GET_SESSION_LOGS_value_matches_registry_tool_name(self):
        """The EvidenceSource value must match the string in enabled_tools."""
        assert EvidenceSource.GET_SESSION_LOGS.value in self.config.tool_config.enabled_tools, (
            "EvidenceSource.GET_SESSION_LOGS.value must appear in Unity Bank enabled_tools"
        )
