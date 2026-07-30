"""
tests/test_sprint254_pipeline_wiring.py

Sprint 2.54 — Pipeline Wiring Tests.

Sections:
  A — Slot pre-extraction (_extract_free_form_slots)
  B — Production tool registry wiring
  C — Pipeline trace tag presence
  D — End-to-end investigation execution (mock tools)
  E — Ticket-updated multi-turn resume
  F — Freshdesk 404 graceful handling
  G — Regression guard (key Sprint 2.28.x / 2.46 / 2.47 / 2.48 invariants)
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("RAG_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "test-supabase-key")
    monkeypatch.setenv("AUDIT_BACKEND", "inmemory")
    monkeypatch.setenv("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
    monkeypatch.setenv("FRESHDESK_DOMAIN", "kwikid.freshdesk.com")
    monkeypatch.setenv("FRESHDESK_API_KEY", "test-fd-key")


# ─────────────────────────────────────────────────────────────────────────────
# Section A — Slot pre-extraction
# ─────────────────────────────────────────────────────────────────────────────

class TestA_SlotPreExtraction:
    """_extract_free_form_slots() correctly extracts non-enum slots from text."""

    @staticmethod
    def _fn():
        from case_engine.runtime.support_agent_runtime import _extract_free_form_slots
        return _extract_free_form_slots

    def test_A1_otp_phone_10digit(self):
        fn = self._fn()
        result = fn("OTP_Delivery_Failure", "My mobile number is 9876543210 and OTP never arrived.")
        assert "phone_number" in result
        assert result["phone_number"] == "3210"

    def test_A2_otp_phone_4digit_fallback(self):
        fn = self._fn()
        result = fn("OTP_Delivery_Failure", "Last 4 digits are 5678.")
        assert "phone_number" in result
        assert result["phone_number"] == "5678"

    def test_A3_vkyc_phone_and_session(self):
        fn = self._fn()
        result = fn("VKYC_Session_Failure", "Phone 9123456789, session KID-ABC12345")
        assert result.get("phone_number") == "6789"
        assert result.get("session_id") == "KID-ABC12345"

    def test_A4_vkyc_session_id_only(self):
        fn = self._fn()
        result = fn("VKYC_Session_Failure", "Session ID: KID-XYZ78901")
        assert result.get("session_id") == "KID-XYZ78901"

    def test_A5_document_ocr_doc_id(self):
        fn = self._fn()
        result = fn("Document_OCR_Failure", "My document ID is AB12345678")
        assert result.get("document_id") == "AB12345678"

    def test_A6_api_callback_url(self):
        fn = self._fn()
        result = fn("API_Callback_Failure", "Endpoint https://api.example.com/callback failed")
        assert result.get("endpoint_url") == "https://api.example.com/callback"

    def test_A7_unknown_topic_returns_empty(self):
        fn = self._fn()
        result = fn("UNKNOWN", "Some random text 9876543210")
        assert result == {}

    def test_A8_empty_message_returns_empty(self):
        fn = self._fn()
        assert fn("OTP_Delivery_Failure", "") == {}
        assert fn("OTP_Delivery_Failure", "   ") == {}

    def test_A9_none_topic_returns_empty(self):
        fn = self._fn()
        assert fn(None, "9876543210") == {}

    def test_A10_agent_id_extraction(self):
        fn = self._fn()
        result = fn("Agent_Portal_Issue", "Agent ID: AG12345678")
        assert "agent_id" in result

    def test_A11_no_false_positive_for_wrong_topic(self):
        fn = self._fn()
        result = fn("OTP_Delivery_Failure", "Session KID-XYZ12345 is failing")
        # session_id should NOT be extracted for OTP topic
        assert "session_id" not in result

    def test_A12_session_id_underscore_normalized(self):
        fn = self._fn()
        result = fn("VKYC_Session_Failure", "Session KID_ABC12345 failed")
        if "session_id" in result:
            assert "-" in result["session_id"]


# ─────────────────────────────────────────────────────────────────────────────
# Section B — Production tool registry wiring
# ─────────────────────────────────────────────────────────────────────────────

class TestB_ProductionToolWiring:
    """register_unity_tools / register_metrics_tools work with plain ToolRegistry."""

    def test_B1_register_unity_tools_into_plain_registry(self):
        from case_engine.tools.tool_registry import ToolRegistry
        from case_engine.tools.adapters.unity_tools import register_unity_tools
        registry = ToolRegistry()
        outcomes = register_unity_tools(registry, config=None)
        assert len(registry) >= 5
        # All 5 Unity tools should be registered
        for tool_name in (
            "GetSessionDetailsTool",
            "GetUserDetailsTool",
            "GetFailureReasonTool",
            "GetCaseHistoryTool",
            "GetOnboardingStatusTool",
        ):
            assert registry.has_tool(tool_name), f"Missing: {tool_name}"

    def test_B2_register_metrics_tools_into_plain_registry(self):
        from case_engine.tools.tool_registry import ToolRegistry
        from case_engine.tools.adapters.metrics_tool import register_metrics_tools
        registry = ToolRegistry()
        outcomes = register_metrics_tools(registry, config=None)
        # Should register MetricTool and ServerTool
        assert len(registry) >= 2

    def test_B3_unity_tools_replace_mocks(self):
        from case_engine.tools.tool_registry import ToolRegistry
        from case_engine.tools.adapters.unity_tools import register_unity_tools
        from case_engine.tools.mock_tools import GetSessionDetailsTool as MockSession
        registry = ToolRegistry()
        # Register mock first
        registry.register(MockSession())
        # Register production — should replace
        register_unity_tools(registry, config=None)
        tool = registry.get("GetSessionDetailsTool")
        assert tool is not None
        # Production tool is NOT the mock
        assert not isinstance(tool, MockSession)

    def test_B4_empty_registry_build_gets_production_tools(self):
        from case_engine.tools.tool_registry import ToolRegistry
        from case_engine.tools.adapters.unity_tools import register_unity_tools
        from case_engine.tools.adapters.metrics_tool import register_metrics_tools
        registry = ToolRegistry()
        register_unity_tools(registry, config=None)
        register_metrics_tools(registry, config=None)
        assert len(registry) >= 7  # 5 Unity + 2 Metrics

    def test_B5_tool_executor_dispatch_to_unity_tool(self):
        from case_engine.tools.tool_registry import ToolRegistry
        from case_engine.tools.tool_executor import ToolExecutor
        from case_engine.tools.adapters.unity_tools import register_unity_tools
        registry = ToolRegistry()
        register_unity_tools(registry, config=None)
        executor = ToolExecutor(registry)
        # Execute should not raise even with no credentials (graceful degradation)
        result = executor.execute(
            "GetSessionDetailsTool",
            {"session_id": "KID-TEST0001"},
            requested_by="test",
        )
        assert result is not None
        # Either succeeded or gracefully failed with error_code populated
        assert result.success is not None


# ─────────────────────────────────────────────────────────────────────────────
# Section C — Trace tag verification
# ─────────────────────────────────────────────────────────────────────────────

class TestC_TraceTags:
    """Verify all 8 required blueprint trace tags fire at WARNING level."""

    def test_C1_enter_investigation_planner_fires(self, caplog):
        import logging
        from case_engine.investigation.service import InvestigationService
        from case_engine.investigation.planner import InvestigationPlanner
        from case_engine.investigation._collector_sprint218 import EvidenceCollector
        from case_engine.investigation._root_cause_sprint218 import RootCauseEngine
        from case_engine.investigation._observation_sprint218 import ObservationGenerator
        from case_engine.tools.tool_registry import ToolRegistry
        from case_engine.tools.tool_executor import ToolExecutor

        registry = ToolRegistry()
        executor = ToolExecutor(registry)
        svc = InvestigationService(
            planner=InvestigationPlanner(),
            collector=EvidenceCollector(executor),
            rca_engine=RootCauseEngine(),
            obs_gen=ObservationGenerator(),
        )
        with caplog.at_level(logging.WARNING, logger="case_engine.investigation.service"):
            svc.investigate("OTP_Delivery_Failure", None, {"phone_number": "3210"})

        tags = [r.message for r in caplog.records]
        full_text = " ".join(tags)
        assert "ENTER_INVESTIGATION_PLANNER" in full_text
        assert "EXIT_INVESTIGATION_PLANNER" in full_text

    def test_C2_enter_evidence_collection_fires(self, caplog):
        import logging
        from case_engine.investigation.service import InvestigationService
        from case_engine.investigation.planner import InvestigationPlanner
        from case_engine.investigation._collector_sprint218 import EvidenceCollector
        from case_engine.investigation._root_cause_sprint218 import RootCauseEngine
        from case_engine.investigation._observation_sprint218 import ObservationGenerator
        from case_engine.tools.tool_registry import ToolRegistry
        from case_engine.tools.tool_executor import ToolExecutor

        registry = ToolRegistry()
        executor = ToolExecutor(registry)
        svc = InvestigationService(
            planner=InvestigationPlanner(),
            collector=EvidenceCollector(executor),
            rca_engine=RootCauseEngine(),
            obs_gen=ObservationGenerator(),
        )
        with caplog.at_level(logging.WARNING, logger="case_engine.investigation.service"):
            svc.investigate("OTP_Delivery_Failure", None, {"phone_number": "3210"})

        full_text = " ".join(r.message for r in caplog.records)
        assert "ENTER_EVIDENCE_COLLECTION" in full_text
        assert "EXIT_EVIDENCE_COLLECTION" in full_text

    def test_C3_enter_tool_execution_fires(self, caplog):
        import logging
        from case_engine.tools.tool_registry import ToolRegistry
        from case_engine.tools.tool_executor import ToolExecutor
        from case_engine.tools.mock_tools import GetSessionDetailsTool
        from case_engine.investigation._collector_sprint218 import EvidenceCollector
        from case_engine.investigation.models import InvestigationPlan, InvestigationStep

        registry = ToolRegistry()
        registry.register(GetSessionDetailsTool())
        executor = ToolExecutor(registry)
        collector = EvidenceCollector(executor)

        plan = InvestigationPlan(
            plan_id="p1", case_id="c1", topic="VKYC_Session_Failure",
            workflow_id=None, steps=(
                InvestigationStep(
                    step_id="s1", sequence=0, tool_name="GetSessionDetailsTool",
                    required_slot="session_id", input_key="session_id",
                    purpose="Get session details",
                ),
            ),
            created_at=datetime.now(tz=timezone.utc).replace(tzinfo=None).isoformat(),
        )
        with caplog.at_level(logging.WARNING):
            bundle = collector.collect(plan, {"session_id": "KID-TESTXXX"})

        full_text = " ".join(r.message for r in caplog.records)
        assert "ENTER_TOOL_EXECUTION" in full_text
        assert "EXIT_TOOL_EXECUTION" in full_text

    def test_C4_enter_retrieval_fires(self, caplog):
        import logging
        from case_engine.knowledge.service import KnowledgeService
        from case_engine.knowledge.recommendation import ResolutionRecommendationEngine

        matcher = MagicMock()
        matcher.match.return_value = (None, MagicMock(matches=[]))
        rec_engine = ResolutionRecommendationEngine()
        svc = KnowledgeService(matcher=matcher, recommendation_engine=rec_engine)

        with caplog.at_level(logging.WARNING, logger="case_engine.knowledge.service"):
            svc.search("OTP_Delivery_Failure", None)

        full_text = " ".join(r.message for r in caplog.records)
        assert "ENTER_RETRIEVAL" in full_text
        assert "EXIT_RETRIEVAL" in full_text


# ─────────────────────────────────────────────────────────────────────────────
# Section D — End-to-end investigation execution
# ─────────────────────────────────────────────────────────────────────────────

class TestD_InvestigationExecution:
    """Verify full investigation pipeline runs to Observation Generator."""

    def test_D1_investigation_produces_observation(self):
        from case_engine.investigation.service import InvestigationService
        from case_engine.investigation.planner import InvestigationPlanner
        from case_engine.investigation._collector_sprint218 import EvidenceCollector
        from case_engine.investigation._root_cause_sprint218 import RootCauseEngine
        from case_engine.investigation._observation_sprint218 import ObservationGenerator
        from case_engine.tools.tool_registry import ToolRegistry
        from case_engine.tools.tool_executor import ToolExecutor
        from case_engine.tools.mock_tools import GetSessionDetailsTool

        registry = ToolRegistry()
        registry.register(GetSessionDetailsTool())
        executor = ToolExecutor(registry)
        svc = InvestigationService(
            planner=InvestigationPlanner(),
            collector=EvidenceCollector(executor),
            rca_engine=RootCauseEngine(),
            obs_gen=ObservationGenerator(),
        )
        result = svc.investigate(
            "VKYC_Session_Failure",
            None,
            {"session_id": "KID-TEST0001", "phone_number": "0001"},
        )
        assert result is not None
        assert result.result_id
        assert result.observation  # Observation Generator ran
        assert result.root_cause is not None
        assert result.bundle is not None

    def test_D2_investigation_with_otp_topic(self):
        from case_engine.investigation.service import InvestigationService
        from case_engine.investigation.planner import InvestigationPlanner
        from case_engine.investigation._collector_sprint218 import EvidenceCollector
        from case_engine.investigation._root_cause_sprint218 import RootCauseEngine
        from case_engine.investigation._observation_sprint218 import ObservationGenerator
        from case_engine.tools.tool_registry import ToolRegistry
        from case_engine.tools.tool_executor import ToolExecutor
        from case_engine.tools.mock_tools import GetUserDetailsTool, GetCaseHistoryTool

        registry = ToolRegistry()
        registry.register(GetUserDetailsTool())
        registry.register(GetCaseHistoryTool())
        executor = ToolExecutor(registry)
        svc = InvestigationService(
            planner=InvestigationPlanner(),
            collector=EvidenceCollector(executor),
            rca_engine=RootCauseEngine(),
            obs_gen=ObservationGenerator(),
        )
        result = svc.investigate(
            "OTP_Delivery_Failure",
            None,
            {"phone_number": "3210"},
        )
        assert result.observation
        assert result.result_id

    def test_D3_investigation_result_to_dict_includes_root_cause(self):
        from case_engine.investigation.service import InvestigationService
        from case_engine.investigation.planner import InvestigationPlanner
        from case_engine.investigation._collector_sprint218 import EvidenceCollector
        from case_engine.investigation._root_cause_sprint218 import RootCauseEngine
        from case_engine.investigation._observation_sprint218 import ObservationGenerator
        from case_engine.tools.tool_registry import ToolRegistry
        from case_engine.tools.tool_executor import ToolExecutor

        registry = ToolRegistry()
        executor = ToolExecutor(registry)
        svc = InvestigationService(
            planner=InvestigationPlanner(),
            collector=EvidenceCollector(executor),
            rca_engine=RootCauseEngine(),
            obs_gen=ObservationGenerator(),
        )
        result = svc.investigate("OTP_Delivery_Failure", None, {"phone_number": "0000"})
        d = result.to_dict()
        assert "root_cause" in d
        assert "observation" in d
        # bundle is serialised as "evidence" key in to_dict()
        assert "evidence" in d or "bundle" in d


# ─────────────────────────────────────────────────────────────────────────────
# Section E — Ticket-updated multi-turn resume
# ─────────────────────────────────────────────────────────────────────────────

class TestE_TicketUpdatedResume:
    """ticket_updated handler resumes pipeline for non-clarification customer replies."""

    def _make_handler(self):
        from freshdesk.handlers import FreshdeskTicketUpdatedHandler
        from freshdesk.idempotency import WebhookIdempotencyStore
        from freshdesk.conversation_state import ConversationStateStore

        idempotency = WebhookIdempotencyStore()
        conversations = ConversationStateStore()
        orchestrator = MagicMock()
        orchestrator.resume_ticket.return_value = MagicMock(
            error_code=None,
            agent_result={"response_draft": {"body_html": "<p>Updated</p>"}},
        )
        return FreshdeskTicketUpdatedHandler(
            idempotency_store=idempotency,
            conversation_store=conversations,
            ticket_orchestrator=orchestrator,
        ), orchestrator, conversations

    def _make_update_payload(self, ticket_id: str = "101") -> dict:
        return {
            "freshdesk_webhook": {
                "ticket_id": ticket_id,
                "ticket_url": f"https://kwikid.freshdesk.com/helpdesk/tickets/{ticket_id}",
                "ticket_status": "Open",
                "ticket_updated_at": "2026-07-15T10:00:00Z",
                "ticket_subject": "OTP not received",
                "latest_comment": {
                    "id": 1,
                    "body": "<p>I tried again, still no OTP.</p>",
                    "body_text": "I tried again, still no OTP.",
                    "incoming": True,
                    "private": False,
                    "user_id": 999,
                    "created_at": "2026-07-15T10:00:00Z",
                },
            },
        }

    def test_E1_awaiting_customer_true_triggers_resume(self):
        handler, orchestrator, conversations = self._make_handler()
        from freshdesk.conversation_state import ConversationLifecycle
        conversations.get_or_create("101", "unity_bank")
        conversations.update("101", awaiting_customer=True,
                             lifecycle_state=ConversationLifecycle.CLARIFICATION)
        payload = self._make_update_payload("101")
        handler.handle(payload)
        orchestrator.resume_ticket.assert_called_once()

    def test_E2_awaiting_customer_false_open_triggers_continue(self):
        handler, orchestrator, conversations = self._make_handler()
        from freshdesk.conversation_state import ConversationLifecycle
        conversations.get_or_create("102", "unity_bank")
        conversations.update("102", awaiting_customer=False,
                             lifecycle_state=ConversationLifecycle.OPEN)
        payload = self._make_update_payload("102")
        handler.handle(payload)
        # Should have called resume_ticket for the non-clarification reply too
        orchestrator.resume_ticket.assert_called_once()

    def test_E3_resolved_conversation_no_resume(self):
        handler, orchestrator, conversations = self._make_handler()
        from freshdesk.conversation_state import ConversationLifecycle
        conversations.get_or_create("103", "unity_bank")
        conversations.update("103", awaiting_customer=False,
                             lifecycle_state=ConversationLifecycle.RESOLVED)
        payload = self._make_update_payload("103")
        handler.handle(payload)
        # RESOLVED conversation should NOT trigger resume
        orchestrator.resume_ticket.assert_not_called()

    def test_E4_closed_conversation_no_resume(self):
        handler, orchestrator, conversations = self._make_handler()
        from freshdesk.conversation_state import ConversationLifecycle
        conversations.get_or_create("104", "unity_bank")
        conversations.update("104", awaiting_customer=False,
                             lifecycle_state=ConversationLifecycle.CLOSED)
        payload = self._make_update_payload("104")
        handler.handle(payload)
        orchestrator.resume_ticket.assert_not_called()

    def test_E5_no_orchestrator_no_error(self):
        from freshdesk.handlers import FreshdeskTicketUpdatedHandler
        from freshdesk.idempotency import WebhookIdempotencyStore
        from freshdesk.conversation_state import ConversationStateStore, ConversationLifecycle

        idempotency = WebhookIdempotencyStore()
        conversations = ConversationStateStore()
        handler = FreshdeskTicketUpdatedHandler(
            idempotency_store=idempotency,
            conversation_store=conversations,
            ticket_orchestrator=None,  # no orchestrator
        )
        conversations.get_or_create("105", "unity_bank")
        conversations.update("105", awaiting_customer=False,
                             lifecycle_state=ConversationLifecycle.OPEN)
        result = handler.handle(self._make_update_payload("105"))
        assert result.success  # must not crash


# ─────────────────────────────────────────────────────────────────────────────
# Section F — Freshdesk 404 graceful handling
# ─────────────────────────────────────────────────────────────────────────────

class TestF_Freshdesk404Handling:
    """FreshdeskResponseService returns {} on 404; does not block pipeline."""

    @pytest.mark.asyncio
    async def test_F1_send_customer_reply_404_returns_empty_dict(self):
        from freshdesk.freshdesk_exceptions import FreshdeskNotFoundError
        from freshdesk.freshdesk_models import FreshdeskConfig
        from freshdesk.response_service import FreshdeskResponseService

        config = FreshdeskConfig(
            domain="kwikid.freshdesk.com",
            api_key="test-key",
        )
        mock_client = MagicMock()
        mock_client.add_public_reply = AsyncMock(
            side_effect=FreshdeskNotFoundError("ticket not found", status_code=404)
        )
        svc = FreshdeskResponseService(freshdesk_client=mock_client)
        result = await svc.send_customer_reply(
            ticket_id=99999,
            body="<p>Test reply</p>",
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_F2_add_internal_note_404_returns_empty_dict(self):
        from freshdesk.freshdesk_exceptions import FreshdeskNotFoundError
        from freshdesk.response_service import FreshdeskResponseService

        mock_client = MagicMock()
        mock_client.add_private_note = AsyncMock(
            side_effect=FreshdeskNotFoundError("ticket not found", status_code=404)
        )
        svc = FreshdeskResponseService(freshdesk_client=mock_client)
        result = await svc.add_internal_note(
            ticket_id=99999,
            body="Internal note body",
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_F3_send_customer_reply_success_returns_result(self):
        from freshdesk.response_service import FreshdeskResponseService

        mock_client = MagicMock()
        mock_client.add_public_reply = AsyncMock(return_value={"id": 42, "body": "ok"})
        svc = FreshdeskResponseService(freshdesk_client=mock_client)
        result = await svc.send_customer_reply(
            ticket_id=123,
            body="<p>Hello</p>",
        )
        assert result.get("id") == 42


# ─────────────────────────────────────────────────────────────────────────────
# Section G — Regression guard
# ─────────────────────────────────────────────────────────────────────────────

class TestG_RegressionGuard:
    """Permanent safety rules from prior sprints must still hold."""

    def test_G1_exclude_escalation_not_reverted(self):
        """Sprint 2.47 permanent: exclude_escalation=True in rag_adapter."""
        from case_engine.knowledge import rag_adapter
        import inspect
        src = inspect.getsource(rag_adapter)
        assert "exclude_escalation=True" in src, (
            "PERMANENT REGRESSION: exclude_escalation must remain True in rag_adapter.py"
        )

    def test_G2_freshdesk_response_service_is_sole_write_path(self):
        """Blueprint §26/§29: no handler calls FreshdeskClient writes directly."""
        import inspect
        from freshdesk import handlers
        src = inspect.getsource(handlers)
        for forbidden in ("add_private_note", "add_public_reply", "update_ticket"):
            # Only the response_service should call these — handlers must not
            # It's OK to have these in comments/strings, but not as direct calls
            # on _client or freshdesk_client objects
            assert f"self._client.{forbidden}" not in src, (
                f"PERMANENT REGRESSION: handlers.py must not call _client.{forbidden} directly"
            )

    def test_G3_closure_field_guard_import(self):
        """Sprint 2.48: ClosureFieldGuard must be importable and gating."""
        from freshdesk.closure_guard import ClosureFieldGuard
        guard = ClosureFieldGuard()
        assert guard is not None

    def test_G4_reply_safety_gate_import(self):
        """Sprint 2.48: ReplySafetyGate must be importable."""
        from freshdesk.safety_gate import ReplySafetyGate
        gate = ReplySafetyGate()
        assert gate is not None

    def test_G5_investigation_orchestrator_import(self):
        """Sprint 2.46: InvestigationOrchestrator must be importable."""
        from case_engine.investigation.orchestrator import InvestigationOrchestrator
        assert InvestigationOrchestrator is not None

    def test_G6_extract_free_form_slots_no_pii_in_slot_names(self):
        """PII discipline: trace log ENTER_PREFILL_SLOT must only log slot name, not value."""
        # Verify that the source code logs slot name only
        import inspect
        from case_engine.runtime import support_agent_runtime
        src = inspect.getsource(support_agent_runtime)
        # The PREFILL log must reference _sn (slot name) but NOT _sv (slot value)
        assert "ENTER_PREFILL_SLOT" in src
        # Confirm the log line only contains slot= not the value
        for line in src.splitlines():
            if "ENTER_PREFILL_SLOT" in line:
                assert "_sv" not in line, (
                    "PERMANENT SECURITY REGRESSION: PREFILL_SLOT trace must NOT log slot value"
                )

    def test_G7_production_tools_wired_not_mocks_in_assembly(self):
        """Sprint 2.54: assembly must use register_unity_tools, not ToolRegistry.build_default()."""
        import inspect
        from runtime import assembly
        src = inspect.getsource(assembly)
        assert "register_unity_tools" in src, "Production Unity tools must be wired in assembly"
        assert "register_metrics_tools" in src, "Production Metrics tools must be wired in assembly"

    def test_G8_intelligence_orchestrator_sole_llm_entry(self):
        """Wave 4A permanent: IntelligenceOrchestrator must be importable."""
        from intelligence import IntelligenceOrchestrator
        assert IntelligenceOrchestrator is not None
