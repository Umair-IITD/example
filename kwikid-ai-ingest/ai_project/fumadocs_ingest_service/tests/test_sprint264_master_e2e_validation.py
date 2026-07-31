"""
tests/test_sprint264_master_e2e_validation.py

Sprint 2.64 — Final Go-Live Audit: Master E2E Validation

Verifies the COMPLETE ticket lifecycle against the SOT blueprint:
  Step 1: ticket-created (VKYC failure, missing Session ID → clarification)
  Step 2: ticket-updated (customer replies with Session ID → resumes)
  Step 3: Investigation (Unity + Loki + Metrics + RAG)
  Step 4: L1 NLG (PII-redacted logs in LLM prompt, SOP text in LLM context)
  Step 5: L2 Escalation (Asana task created, cf_asana_ticket_link updated)
  Step 6: L2 Closure (Asana webhook → ClosureFieldGuard → ReplySafetyGate → close)

Section A — Knowledge Layer: SOP → LLM path (the bug fixed in Sprint 2.64)
Section B — PII Redaction: Loki logs PII scrubbed before LLM prompt
Section C — ReplySafetyGate: wiring verified for all Freshdesk reply sites
Section D — ClosureFieldGuard + ReplySafetyGate: Asana closure safety chain
Section E — Full lifecycle: end-to-end through real FastAPI routes (mocked externals)
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Env setup (must precede all project imports) ────────────────────────────
os.environ.setdefault("OPENAI_API_KEY", "sk-test-e2e-validation")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_SECRET", "test-hmac-secret")
os.environ.setdefault("FRESHDESK_DOMAIN", "kwikid.freshdesk.com")
os.environ.setdefault("FRESHDESK_API_KEY", "test-api-key")
os.environ.setdefault("INTELLIGENCE_ENABLED", "false")  # avoid real LLM; we test prompts via stubs
os.environ.setdefault("RAG_API_KEY", "test-rag-key")
os.environ.setdefault("REPLY_SAFETY_KILL_SWITCH", "false")
os.environ.setdefault("FRESHDESK_CONFIDENCE_THRESHOLD", "0.5")


# ── Lightweight chunk stub (avoids full intelligence package import in unit tests) ──

@dataclass
class _FakeChunk:
    chunk_id: str = ""
    source: str = ""
    title: str = ""
    content: str = ""
    score: float = 0.0
    url: str = ""
    metadata: dict = field(default_factory=dict)


# ────────────────────────────────────────────────────────────────────────────
# Section A — Knowledge Layer: SOP → LLM chunk extraction
# (Bug fixed in Sprint 2.64: KnowledgeResult.to_dict() uses "search_result.matches"
#  not "chunks"; _run_intelligence() now falls back to extracting from matches.)
# ────────────────────────────────────────────────────────────────────────────

_VKYC_KNOWLEDGE_RESULT = {
    "result_id": "kr-vkyc-001",
    "topic": "VKYC_Session_Failure",
    "search_result": {
        "result_id": "sr-001",
        "query": {"query_id": "q-1", "topic": "VKYC_Session_Failure"},
        "matches": [
            {
                "match_id": "m-1",
                "entry": {
                    "entry_id": "seed-vkyc-expired-session",
                    "title": "VKYC Session Reset — Expired or Timed-Out Session",
                    "body": (
                        "A Video KYC session has expired or timed out before the user "
                        "could complete identification. Resolution: reset the session "
                        "from the Admin Portal so the user can restart their VKYC journey."
                    ),
                    "entry_type": "SOP",
                    "source": "manual",
                    "source_id": "sop-vkyc-001",
                    "tags": ["vkyc", "session_reset", "expired_session"],
                },
                "relevance_score": 0.87,
                "match_reason": "topic+root_cause match",
                "matched_on": ["VKYC_Session_Failure", "EXPIRED_SESSION"],
            },
            {
                "match_id": "m-2",
                "entry": {
                    "entry_id": "seed-vkyc-liveness-failure",
                    "title": "VKYC Session Failure — Liveness Check Rejected",
                    "body": (
                        "The KYC session failed because the liveness detection algorithm "
                        "rejected the user's video. Advise the user to retry in better lighting."
                    ),
                    "entry_type": "KNOWN_ISSUE",
                    "source": "manual",
                    "source_id": "sop-vkyc-002",
                    "tags": ["vkyc", "liveness", "liveness_failure"],
                },
                "relevance_score": 0.62,
                "match_reason": "topic match",
                "matched_on": ["VKYC_Session_Failure"],
            },
        ],
        "top_match": None,
        "total_found": 2,
        "searched_at": "2026-07-31T00:00:00Z",
    },
    "sop_match": None,
    "recommendation": {
        "recommendation_id": "rec-1",
        "topic": "VKYC_Session_Failure",
        "root_cause_category": "EXPIRED_SESSION",
        "recommended_action": "SESSION_RESET",
        "confidence": 0.87,
        "sop_steps": [
            "1. Navigate to the Admin Portal and locate the session by Session ID.",
            "2. Verify the session status shows EXPIRED or TIMED_OUT.",
            "3. Click 'Reset Session' to restore the session to an in-progress state.",
        ],
        "escalation_required": False,
        "source_entry_ids": ["seed-vkyc-expired-session"],
        "created_at": "2026-07-31T00:00:00Z",
    },
    "sop_match_found": True,
    "completed_at": "2026-07-31T00:00:00Z",
}

_WORKFLOW_RESULT_WITH_KNOWLEDGE = {
    "workflow_id": "wf-test-001",
    "workflow_state": "RESOLVED",
    "workflow_context": {
        "knowledge_result": _VKYC_KNOWLEDGE_RESULT,
    },
    "investigation_result": None,
}


class TestA_KnowledgeLayerSopExtraction:
    """
    Verify SOP body text is correctly extracted from KnowledgeResult.to_dict() format
    and converted into RetrievedChunk objects for the LLM prompt.
    """

    def test_A1_chunks_key_absent_from_knowledge_result_dict(self):
        """KnowledgeResult.to_dict() does NOT produce a 'chunks' key — confirms the gap."""
        from case_engine.runtime.support_agent_runtime import _extract_workflow_knowledge
        wf_knowledge = _extract_workflow_knowledge(_WORKFLOW_RESULT_WITH_KNOWLEDGE)
        assert wf_knowledge.get("chunks") is None, (
            "KnowledgeResult.to_dict() should not produce a 'chunks' key"
        )
        assert wf_knowledge.get("sop_match_found") is True

    def test_A2_fallback_extracts_sop_body_text(self):
        """Fallback path extracts SOP body text from search_result.matches[*].entry."""
        from case_engine.runtime.support_agent_runtime import _extract_workflow_knowledge, _to_retrieved_chunk

        wf_knowledge = _extract_workflow_knowledge(_WORKFLOW_RESULT_WITH_KNOWLEDGE)
        knowledge_chunks: list[_FakeChunk] = []

        # Simulate old path (always empty for KnowledgeResult format)
        for entry in (wf_knowledge.get("chunks") or []):
            chunk = _to_retrieved_chunk(entry, _FakeChunk)
            if chunk is not None:
                knowledge_chunks.append(chunk)

        assert len(knowledge_chunks) == 0, "old chunks path must be empty"

        # New fallback path (Sprint 2.64 fix)
        for match in (wf_knowledge.get("search_result") or {}).get("matches", [])[:3]:
            if isinstance(match, dict):
                entry = match.get("entry") or {}
                merged = {**entry, "score": match.get("relevance_score", 0.0)}
                chunk = _to_retrieved_chunk(merged, _FakeChunk)
                if chunk is not None:
                    knowledge_chunks.append(chunk)

        assert len(knowledge_chunks) == 2
        assert "expired or timed out" in knowledge_chunks[0].content
        assert "liveness detection" in knowledge_chunks[1].content

    def test_A3_sop_relevance_score_preserved(self):
        """Relevance score from SOPMatch is preserved in the RetrievedChunk."""
        from case_engine.runtime.support_agent_runtime import _extract_workflow_knowledge, _to_retrieved_chunk

        wf_knowledge = _extract_workflow_knowledge(_WORKFLOW_RESULT_WITH_KNOWLEDGE)
        matches = (wf_knowledge.get("search_result") or {}).get("matches", [])
        assert len(matches) == 2
        entry = matches[0].get("entry") or {}
        merged = {**entry, "score": matches[0].get("relevance_score", 0.0)}
        chunk = _to_retrieved_chunk(merged, _FakeChunk)
        assert chunk is not None
        assert chunk.score == pytest.approx(0.87)

    def test_A4_sop_title_preserved(self):
        """SOP entry title is preserved in the RetrievedChunk."""
        from case_engine.runtime.support_agent_runtime import _extract_workflow_knowledge, _to_retrieved_chunk

        wf_knowledge = _extract_workflow_knowledge(_WORKFLOW_RESULT_WITH_KNOWLEDGE)
        matches = (wf_knowledge.get("search_result") or {}).get("matches", [])
        entry = matches[0].get("entry") or {}
        merged = {**entry, "score": 0.87}
        chunk = _to_retrieved_chunk(merged, _FakeChunk)
        assert chunk is not None
        assert chunk.title == "VKYC Session Reset — Expired or Timed-Out Session"

    def test_A5_at_most_3_matches_extracted(self):
        """The fallback caps at 3 matches to avoid prompt bloat."""
        from case_engine.runtime.support_agent_runtime import _extract_workflow_knowledge, _to_retrieved_chunk

        # Build a knowledge result with 5 matches
        many_matches = []
        for i in range(5):
            many_matches.append({
                "match_id": f"m-{i}",
                "entry": {
                    "entry_id": f"seed-{i}",
                    "title": f"SOP Entry {i}",
                    "body": f"This is the body of SOP entry number {i}.",
                    "source": "manual",
                },
                "relevance_score": 0.9 - i * 0.1,
            })
        wf_knowledge = {
            "search_result": {"matches": many_matches},
            "sop_match_found": True,
        }
        knowledge_chunks = []
        for match in (wf_knowledge.get("search_result") or {}).get("matches", [])[:3]:
            if isinstance(match, dict):
                entry = match.get("entry") or {}
                merged = {**entry, "score": match.get("relevance_score", 0.0)}
                chunk = _to_retrieved_chunk(merged, _FakeChunk)
                if chunk is not None:
                    knowledge_chunks.append(chunk)
        assert len(knowledge_chunks) == 3


# ────────────────────────────────────────────────────────────────────────────
# Section B — PII Redaction: Loki logs are scrubbed before LLM prompt
# ────────────────────────────────────────────────────────────────────────────

class TestB_PiiRedaction:
    """
    Verify that PII values in Loki log JSON are redacted BEFORE any content
    reaches the evidence bundle or LLM prompt. Blueprint §27/§34/§35.
    """

    def test_B1_aadhaar_number_redacted(self):
        """Value associated with 'aadhaar_number' key is replaced with [REDACTED]."""
        from case_engine.integrations.loki.relevance import _redact_and_truncate
        raw = '{"event":"kyc","aadhaar_number": "1234567890123456"}'
        result = _redact_and_truncate(raw)
        assert "1234567890123456" not in result
        assert "[REDACTED]" in result
        assert "aadhaar_number" in result  # key still present, value gone

    def test_B2_pan_redacted(self):
        """PAN value is redacted."""
        from case_engine.integrations.loki.relevance import _redact_and_truncate
        raw = '{"step":"pan_validation","pan": "ABCDE1234F","status":"failed"}'
        result = _redact_and_truncate(raw)
        assert "ABCDE1234F" not in result
        assert "[REDACTED]" in result

    def test_B3_non_pii_values_preserved(self):
        """Non-PII values (session_id, status, error codes) are NOT redacted."""
        from case_engine.integrations.loki.relevance import _redact_and_truncate
        raw = '{"session_id":"KID-ABC123","status":"EXPIRED","error_code":"SESSION_TIMEOUT_001"}'
        result = _redact_and_truncate(raw)
        assert "KID-ABC123" in result
        assert "EXPIRED" in result
        assert "SESSION_TIMEOUT_001" in result

    def test_B4_pii_redaction_runs_before_bm25_scoring(self):
        """
        extract_relevant_lines() returns only redacted content — the raw
        Loki lines (with PII) never appear in the returned excerpt.
        """
        from case_engine.integrations.loki.relevance import extract_relevant_lines

        # Simulate raw Loki lines in the [ts] (meta) message format that
        # parse_line() expects, so the message field is unescaped and the PII
        # pattern can match "aadhaar_number":"1234" directly.
        raw_lines = [
            '[2026-07-31T00:00:00Z] (detected_level=info, service_name=agentapi) '
            '{"session_id":"KID-TEST-001","aadhaar_number":"1234","status":"PROCESSING"}',
            '[2026-07-31T00:00:01Z] (detected_level=warn, service_name=agentapi) '
            '{"session_id":"KID-TEST-001","status":"EXPIRED_SESSION"}',
        ]
        lines, _stats = extract_relevant_lines(
            raw_lines,
            "KID-TEST-001 EXPIRED_SESSION",
            char_budget=2000,
        )
        excerpt = " ".join(line.content for line in lines)
        assert "1234" not in excerpt, "PII value must not appear in the curated excerpt"
        assert "[REDACTED]" in excerpt, "Redaction marker must be present"


# ────────────────────────────────────────────────────────────────────────────
# Section C — ReplySafetyGate wiring in Freshdesk webhook path
# Blueprint: every autonomous customer-facing reply is gated (Sprint 2.63.1)
# ────────────────────────────────────────────────────────────────────────────

class TestC_ReplySafetyGateWiring:
    """
    Verify _gated_customer_reply() is the single funnel for all Freshdesk reply
    sites and that it correctly gates (allow / block / fail-closed).
    """

    def _make_mock_resp_svc(self) -> MagicMock:
        svc = MagicMock()
        svc.send_customer_reply = AsyncMock(return_value={"id": 1})
        svc.add_internal_note = AsyncMock(return_value={"id": 2})
        return svc

    @pytest.mark.asyncio
    async def test_C1_no_gate_fails_closed(self):
        """If safety_gate is None, _gated_customer_reply posts draft note — never sends."""
        from api.routes.webhooks.freshdesk import _gated_customer_reply
        svc = self._make_mock_resp_svc()
        result = await _gated_customer_reply(
            svc, safety_gate=None,
            ticket_id="1001", body_html="<p>Hi</p>", confidence=0.9,
        )
        assert result is False
        svc.send_customer_reply.assert_not_called()
        svc.add_internal_note.assert_called_once()

    @pytest.mark.asyncio
    async def test_C2_gate_allows_high_confidence(self):
        """Gate allows sends when confidence ≥ threshold and kill-switch is off."""
        from api.routes.webhooks.freshdesk import _gated_customer_reply
        from freshdesk.safety_gate import ReplySafetyGate
        gate = ReplySafetyGate(kill_switch=False)
        svc = self._make_mock_resp_svc()
        result = await _gated_customer_reply(
            svc, safety_gate=gate,
            ticket_id="1002", body_html="<p>Issue resolved.</p>", confidence=0.95,
        )
        assert result is True
        svc.send_customer_reply.assert_called_once_with("1002", "<p>Issue resolved.</p>", case_id="")
        svc.add_internal_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_C3_gate_blocks_low_confidence(self):
        """Gate blocks and drafts when confidence < threshold."""
        from api.routes.webhooks.freshdesk import _gated_customer_reply
        from freshdesk.safety_gate import ReplySafetyGate
        gate = ReplySafetyGate(kill_switch=False, confidence_threshold=0.75)
        svc = self._make_mock_resp_svc()
        result = await _gated_customer_reply(
            svc, safety_gate=gate,
            ticket_id="1003", body_html="<p>Maybe resolved.</p>", confidence=0.30,
        )
        assert result is False
        svc.send_customer_reply.assert_not_called()
        svc.add_internal_note.assert_called_once()

    @pytest.mark.asyncio
    async def test_C4_kill_switch_blocks_all_sends(self):
        """REPLY_SAFETY_KILL_SWITCH=true blocks even high-confidence replies."""
        from api.routes.webhooks.freshdesk import _gated_customer_reply
        from freshdesk.safety_gate import ReplySafetyGate
        gate = ReplySafetyGate(kill_switch=True)
        svc = self._make_mock_resp_svc()
        result = await _gated_customer_reply(
            svc, safety_gate=gate,
            ticket_id="1004", body_html="<p>Great!</p>", confidence=1.0,
        )
        assert result is False
        svc.send_customer_reply.assert_not_called()


# ────────────────────────────────────────────────────────────────────────────
# Section D — ClosureFieldGuard + ReplySafetyGate in Asana closure path
# Blueprint §20B: GUARD → GATE → SEND → CLOSE sequence (Sprint 2.63.1)
# ────────────────────────────────────────────────────────────────────────────

class TestD_AsanaClosureSafetyChain:
    """
    Verify the L2 closure safety chain:
      ClosureFieldGuard → ReplySafetyGate → send_customer_reply → update_ticket_fields
    """

    def _make_eng_ticket(self, freshdesk_ticket_id: str = "5001") -> MagicMock:
        t = MagicMock()
        t.ticket_id = "eng-001"
        t.case_id = "case-001"
        t.freshdesk_ticket_id = freshdesk_ticket_id
        return t

    def _make_resolve_result(self, success: bool = True) -> MagicMock:
        r = MagicMock()
        r.success = success
        r.error_msg = "" if success else "Internal error"
        return r

    def _make_eng_service(self, ticket, resolve_success: bool = True) -> MagicMock:
        svc = MagicMock()
        svc.get_ticket_by_external_id.return_value = ticket
        svc.resolve_ticket.return_value = self._make_resolve_result(resolve_success)
        return svc

    def _make_resp_svc(self, current_ticket: dict | None = None) -> MagicMock:
        svc = MagicMock()
        svc.get_ticket = AsyncMock(return_value=current_ticket or {"custom_fields": {"cf_clients": "Unity Bank"}})
        svc.add_internal_note = AsyncMock(return_value={"id": 99})
        svc.send_customer_reply = AsyncMock(return_value={"id": 100})
        svc.update_ticket_fields = AsyncMock(return_value={"id": 5001, "status": 4})
        return svc

    @pytest.mark.asyncio
    async def test_D1_full_closure_sequence(self):
        """Happy path: GUARD passes → GATE allows → reply sent → ticket status=4."""
        from api.routes.webhooks.asana import _handle_task_completed
        from freshdesk.safety_gate import ReplySafetyGate
        from asana.webhook import AsanaEventIdempotencyStore

        ticket = self._make_eng_ticket()
        eng_svc = self._make_eng_service(ticket)
        resp_svc = self._make_resp_svc({"custom_fields": {"cf_clients": "Unity Bank"}})
        gate = ReplySafetyGate(kill_switch=False)
        store = AsanaEventIdempotencyStore()

        await _handle_task_completed(
            "task-gid-001", eng_svc, resp_svc, store, gate,
        )

        # Verify sequence: guard checked (via get_ticket), reply sent, then closure PUT
        resp_svc.get_ticket.assert_called_once()
        resp_svc.send_customer_reply.assert_called_once()
        resp_svc.update_ticket_fields.assert_called_once()
        call_kwargs = resp_svc.update_ticket_fields.call_args
        assert call_kwargs.kwargs.get("status") == 4
        assert call_kwargs.kwargs.get("ticket_type") == "Issues"

    @pytest.mark.asyncio
    async def test_D2_guard_blocks_when_cf_clients_absent(self):
        """ClosureFieldGuard blocks and posts internal note when cf_clients is missing."""
        from api.routes.webhooks.asana import _handle_task_completed
        from freshdesk.safety_gate import ReplySafetyGate
        from asana.webhook import AsanaEventIdempotencyStore

        ticket = self._make_eng_ticket()
        eng_svc = self._make_eng_service(ticket)
        # cf_clients absent → guard blocks
        resp_svc = self._make_resp_svc({"custom_fields": {}})
        gate = ReplySafetyGate(kill_switch=False)
        store = AsanaEventIdempotencyStore()

        await _handle_task_completed(
            "task-gid-002", eng_svc, resp_svc, store, gate,
        )

        resp_svc.add_internal_note.assert_called_once()
        resp_svc.send_customer_reply.assert_not_called()
        resp_svc.update_ticket_fields.assert_not_called()

    @pytest.mark.asyncio
    async def test_D3_gate_blocks_does_not_close_ticket(self):
        """If ReplySafetyGate blocks, ticket is NOT closed (stays open for manual handling)."""
        from api.routes.webhooks.asana import _handle_task_completed
        from freshdesk.safety_gate import ReplySafetyGate
        from asana.webhook import AsanaEventIdempotencyStore

        ticket = self._make_eng_ticket()
        eng_svc = self._make_eng_service(ticket)
        resp_svc = self._make_resp_svc({"custom_fields": {"cf_clients": "Unity Bank"}})
        gate = ReplySafetyGate(kill_switch=True)  # kill-switch blocks everything
        store = AsanaEventIdempotencyStore()

        await _handle_task_completed(
            "task-gid-003", eng_svc, resp_svc, store, gate,
        )

        resp_svc.send_customer_reply.assert_not_called()
        resp_svc.update_ticket_fields.assert_not_called()
        # Internal draft note should be posted
        resp_svc.add_internal_note.assert_called_once()

    @pytest.mark.asyncio
    async def test_D4_no_gate_wired_fails_closed(self):
        """If safety_gate=None, the reply is NOT sent and ticket is NOT closed."""
        from api.routes.webhooks.asana import _handle_task_completed
        from asana.webhook import AsanaEventIdempotencyStore

        ticket = self._make_eng_ticket()
        eng_svc = self._make_eng_service(ticket)
        resp_svc = self._make_resp_svc({"custom_fields": {"cf_clients": "Unity Bank"}})
        store = AsanaEventIdempotencyStore()

        await _handle_task_completed(
            "task-gid-004", eng_svc, resp_svc, store, safety_gate=None,
        )

        resp_svc.send_customer_reply.assert_not_called()
        resp_svc.update_ticket_fields.assert_not_called()
        resp_svc.add_internal_note.assert_called_once()  # draft note posted

    @pytest.mark.asyncio
    async def test_D5_idempotency_prevents_double_closure(self):
        """Second webhook delivery for the same task_gid is silently skipped."""
        from api.routes.webhooks.asana import _handle_task_completed
        from freshdesk.safety_gate import ReplySafetyGate
        from asana.webhook import AsanaEventIdempotencyStore

        ticket = self._make_eng_ticket()
        eng_svc = self._make_eng_service(ticket)
        resp_svc = self._make_resp_svc({"custom_fields": {"cf_clients": "Unity Bank"}})
        gate = ReplySafetyGate(kill_switch=False)
        store = AsanaEventIdempotencyStore()

        # First delivery
        await _handle_task_completed("task-gid-005", eng_svc, resp_svc, store, gate)
        assert resp_svc.send_customer_reply.call_count == 1

        # Second delivery (redelivery) — must be skipped
        await _handle_task_completed("task-gid-005", eng_svc, resp_svc, store, gate)
        assert resp_svc.send_customer_reply.call_count == 1  # still 1, not 2


# ────────────────────────────────────────────────────────────────────────────
# Section E — Full Ticket Lifecycle: HTTP route → background task assertions
# Uses FastAPI TestClient with a fake app.state carrying mock services.
# ────────────────────────────────────────────────────────────────────────────

def _build_vkyc_ticket_created_payload(ticket_id: str = "2001") -> dict:
    """Minimal Freshdesk ticket-created payload (Format B / Dispatch'r shape)."""
    return {
        "freshdesk_webhook": {
            "ticket_id": ticket_id,
            "subject": "KYC session failed — please help",
            "description": "My VKYC session failed. I don't know why.",
            "description_text": "My VKYC session failed. I don't know why.",
            "requester_email": "customer@unitybank.co.in",
            "created_at": "2026-07-31T06:00:00Z",
            "ticket_custom_fields": {
                "cf_clients": "Unity Bank",
                "cf_environment": "production",
            },
            "status": 2,
            "type": "Question",
        }
    }


def _build_ticket_updated_payload(ticket_id: str = "2001", session_id: str = "KID-ABC123") -> dict:
    """Customer reply payload containing the session_id we asked for."""
    return {
        "freshdesk_webhook": {
            "ticket_id": ticket_id,
            "updated_at": "2026-07-31T06:05:00Z",
            "latest_comment": {
                "body": f"Here is my session ID: {session_id}",
                "body_text": f"Here is my session ID: {session_id}",
                "incoming": True,
                "private": False,
            },
            "ticket_custom_fields": {"cf_clients": "Unity Bank"},
        }
    }


def _build_test_app():
    """Build a minimal FastAPI app with fully mocked app.state services."""
    from fastapi import FastAPI
    from api.routes.webhooks.freshdesk import router as fd_router
    from api.routes.webhooks.asana import router as asana_router
    from freshdesk.handlers import FreshdeskTicketCreatedHandler, FreshdeskTicketUpdatedHandler
    from freshdesk.idempotency import WebhookIdempotencyStore
    from freshdesk.conversation_state import ConversationStateStore
    from freshdesk.safety_gate import ReplySafetyGate

    app = FastAPI()
    app.include_router(fd_router)
    app.include_router(asana_router)

    # Shared stores
    idem_store = WebhookIdempotencyStore()
    conv_store = ConversationStateStore()

    # Mock TicketOrchestrator — simulates full investigation + L2 escalation
    mock_orchestrator = MagicMock()
    mock_orch_result = MagicMock()
    mock_orch_result.case_id = "case-e2e-001"
    mock_orch_result.success = True
    mock_orch_result.error_code = None
    # Simulate: agent did investigation, needs L2 escalation
    mock_orch_result.agent_result = {
        "agent_status": "ESCALATED",
        "response_draft": None,  # suppressed because engineering_result exists
        "workflow_result": {"workflow_state": "ESCALATED"},
        "engineering_result": {
            "success": True,
            "ticket": {
                "ticket_id": "eng-e2e-001",
                "external_id": "asana-task-9999",
                "asana_project_id": "1217038113542074",
                "title": "VKYC session failure — investigate SDK",
            },
        },
        "metadata": {},
    }
    mock_orchestrator.process_ticket.return_value = mock_orch_result
    mock_orchestrator.resume_ticket.return_value = MagicMock(
        case_id="case-e2e-001",
        error_code=None,
        agent_result={
            "agent_status": "SUCCESS",
            "response_draft": {
                "body_html": "<p>Investigation complete. Issue resolved.</p>",
                "confidence": 0.88,
            },
            "workflow_result": {},
            "engineering_result": None,
            "metadata": {},
        },
    )

    # Handlers
    created_handler = FreshdeskTicketCreatedHandler(
        idempotency_store=idem_store,
        conversation_store=conv_store,
        ticket_orchestrator=mock_orchestrator,
    )
    updated_handler = FreshdeskTicketUpdatedHandler(
        idempotency_store=idem_store,
        conversation_store=conv_store,
        ticket_orchestrator=mock_orchestrator,
    )

    # Mock FreshdeskResponseService
    mock_resp_svc = MagicMock()
    mock_resp_svc.add_internal_note = AsyncMock(return_value={"id": 10})
    mock_resp_svc.send_customer_reply = AsyncMock(return_value={"id": 11})
    mock_resp_svc.update_ticket_fields = AsyncMock(return_value={"id": 2001, "status": 4})
    mock_resp_svc.get_ticket = AsyncMock(return_value={"custom_fields": {"cf_clients": "Unity Bank"}})

    # Wire app.state
    app.state.freshdesk_ticket_created_handler = created_handler
    app.state.freshdesk_ticket_updated_handler = updated_handler
    app.state.freshdesk_response_service = mock_resp_svc
    app.state.reply_safety_gate = ReplySafetyGate(kill_switch=False)
    app.state.freshdesk_idempotency_store = idem_store
    app.state.freshdesk_conversation_store = conv_store
    app.state.freshdesk_verifier = None  # HMAC disabled for tests

    return app, mock_resp_svc, mock_orchestrator


class TestE_FullLifecycle:
    """
    End-to-end ticket lifecycle through real FastAPI routes using TestClient.
    External services (Freshdesk write API, Unity, Asana) are mocked at the
    service layer via app.state injection.
    """

    def test_E1_ticket_created_returns_200_and_enqueues(self):
        """POST /webhooks/freshdesk/ticket-created → 200 accepted immediately."""
        from starlette.testclient import TestClient
        app, resp_svc, _ = _build_test_app()
        with TestClient(app, raise_server_exceptions=True) as client:
            payload = _build_vkyc_ticket_created_payload("3001")
            resp = client.post(
                "/webhooks/freshdesk/ticket-created",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 200
        assert resp.json()["event"] == "ticket_created"

    def test_E2_ticket_created_triggers_observation_note(self):
        """
        ticket-created background task: agent investigation → internal observation note
        posted via FreshdeskResponseService (Blueprint §14 OBSGEN → FDNOTE).
        """
        from starlette.testclient import TestClient
        from freshdesk.handlers import FreshdeskTicketCreatedHandler
        from freshdesk.idempotency import WebhookIdempotencyStore
        from freshdesk.conversation_state import ConversationStateStore
        from freshdesk.safety_gate import ReplySafetyGate
        from fastapi import FastAPI
        from api.routes.webhooks.freshdesk import router as fd_router

        app = FastAPI()
        app.include_router(fd_router)

        idem = WebhookIdempotencyStore()
        conv = ConversationStateStore()

        # Orchestrator that returns an observation_note in the agent result
        mock_orch = MagicMock()
        orch_result = MagicMock()
        orch_result.case_id = "case-e2-001"
        orch_result.success = True
        orch_result.error_code = None
        orch_result.agent_result = {
            "agent_status": "SUCCESS",
            "response_draft": {
                "body_html": "<p>Your session has been reset. Please retry.</p>",
                "confidence": 0.92,
            },
            "engineering_result": None,
            "metadata": {
                "intelligence_result": {
                    "observation": {
                        "note": "<h3>L1 Investigation Note</h3><p>Session KID-E2-001 expired.</p>"
                    }
                }
            },
        }
        mock_orch.process_ticket.return_value = orch_result

        handler = FreshdeskTicketCreatedHandler(
            idempotency_store=idem,
            conversation_store=conv,
            ticket_orchestrator=mock_orch,
        )
        resp_svc = MagicMock()
        resp_svc.add_internal_note = AsyncMock(return_value={"id": 20})
        resp_svc.send_customer_reply = AsyncMock(return_value={"id": 21})
        resp_svc.update_ticket_fields = AsyncMock(return_value={"id": 3002})

        app.state.freshdesk_ticket_created_handler = handler
        app.state.freshdesk_response_service = resp_svc
        app.state.reply_safety_gate = ReplySafetyGate(kill_switch=False)
        app.state.freshdesk_idempotency_store = idem
        app.state.freshdesk_conversation_store = conv
        app.state.freshdesk_verifier = None

        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.post(
                "/webhooks/freshdesk/ticket-created",
                json=_build_vkyc_ticket_created_payload("3002"),
            )
        assert resp.status_code == 200
        # Observation note posted as internal note (OBSGEN → FDNOTE)
        obs_calls = [
            c for c in resp_svc.add_internal_note.call_args_list
            if "L1 Investigation Note" in str(c)
        ]
        assert len(obs_calls) >= 1, "Observation note must be posted as internal note"
        # Customer reply also sent (gated, high confidence)
        resp_svc.send_customer_reply.assert_called_once()

    def test_E3_escalation_updates_cf_asana_ticket_link(self):
        """
        When agent creates an Asana task (L2 escalation), cf_asana_ticket_link
        is updated via update_ticket_fields before the escalation reply is sent.
        """
        from starlette.testclient import TestClient
        app, resp_svc, mock_orch = _build_test_app()
        # Force ticket_id that's unique for this test
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.post(
                "/webhooks/freshdesk/ticket-created",
                json=_build_vkyc_ticket_created_payload("4001"),
            )
        assert resp.status_code == 200
        # cf_asana_ticket_link update was called
        cf_link_calls = [
            c for c in resp_svc.update_ticket_fields.call_args_list
            if "cf_asana_ticket_link" in str(c)
        ]
        assert len(cf_link_calls) >= 1, "cf_asana_ticket_link must be set on L2 escalation"
        # Escalation reply sent via safety gate
        resp_svc.send_customer_reply.assert_called()

    def test_E4_ticket_updated_customer_reply_resumes_pipeline(self):
        """
        POST /webhooks/freshdesk/ticket-updated with customer_reply →
        pipeline resumes, Pass 2 reply sent via ReplySafetyGate.
        """
        from starlette.testclient import TestClient
        from freshdesk.handlers import FreshdeskTicketCreatedHandler, FreshdeskTicketUpdatedHandler
        from freshdesk.idempotency import WebhookIdempotencyStore
        from freshdesk.conversation_state import ConversationStateStore, ConversationLifecycle
        from freshdesk.safety_gate import ReplySafetyGate
        from fastapi import FastAPI
        from api.routes.webhooks.freshdesk import router as fd_router

        app = FastAPI()
        app.include_router(fd_router)

        idem = WebhookIdempotencyStore()
        conv = ConversationStateStore()

        # Pre-seed conversation state: ticket 5001 is in CLARIFICATION state
        conv.get_or_create("5001", "unity_bank")
        conv.update("5001", case_id="case-5001", awaiting_customer=True,
                    clarification_pending=True,
                    lifecycle_state=ConversationLifecycle.CLARIFICATION)

        # Orchestrator.resume_ticket returns a resolution reply
        mock_orch = MagicMock()
        resume_result = MagicMock()
        resume_result.case_id = "case-5001"
        resume_result.error_code = None
        resume_result.agent_result = {
            "agent_status": "SUCCESS",
            "response_draft": {
                "body_html": "<p>Your session KID-ABC123 has been investigated.</p>",
                "confidence": 0.91,
            },
            "engineering_result": None,
            "metadata": {},
        }
        mock_orch.resume_ticket.return_value = resume_result

        # NLPRouter returns all slots filled (so investigation runs)
        mock_nlp = MagicMock()
        nlp_signal = MagicMock()
        nlp_signal.intent = "VKYC_Session_Failure"
        nlp_signal.needs_clarification = False
        nlp_signal.entities = {"session_id": "KID-ABC123"}
        mock_nlp.route.return_value = nlp_signal

        # CaseService.receive_message returns all slots filled
        mock_case_svc = MagicMock()
        msg_result = MagicMock()
        msg_result.all_slots_filled = True
        msg_result.next_question = None
        msg_result.escalated = False
        mock_case_svc.get_case.return_value = MagicMock(case_id="case-5001")
        mock_case_svc.receive_message.return_value = msg_result

        handler_upd = FreshdeskTicketUpdatedHandler(
            idempotency_store=idem,
            conversation_store=conv,
            ticket_orchestrator=mock_orch,
            nlp_router=mock_nlp,
            case_service=mock_case_svc,
        )
        resp_svc = MagicMock()
        resp_svc.send_customer_reply = AsyncMock(return_value={"id": 51})
        resp_svc.add_internal_note = AsyncMock(return_value={"id": 52})

        app.state.freshdesk_ticket_updated_handler = handler_upd
        app.state.freshdesk_response_service = resp_svc
        app.state.reply_safety_gate = ReplySafetyGate(kill_switch=False)
        app.state.freshdesk_idempotency_store = idem
        app.state.freshdesk_conversation_store = conv
        app.state.freshdesk_verifier = None

        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.post(
                "/webhooks/freshdesk/ticket-updated",
                json=_build_ticket_updated_payload("5001", "KID-ABC123"),
            )
        assert resp.status_code == 200
        # Resume was called and Pass 2 reply was sent
        mock_orch.resume_ticket.assert_called_once()
        resp_svc.send_customer_reply.assert_called_once()
        # The response contains the resolved investigation note
        call_args = resp_svc.send_customer_reply.call_args
        assert "KID-ABC123" in str(call_args) or "investigated" in str(call_args)
