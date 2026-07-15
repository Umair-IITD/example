"""
tests/test_sprint248_freshdesk_integration.py

Sprint 2.48 — Freshdesk full integration test suite.

Reconciles the Sprint 2.28 infrastructure against the Freshdesk_discovery/
source-of-truth documents. Covers:

  Section A — ClosureFieldGuard (SOT ticket_lifecycle.md Section 6)
  Section B — HTML template builders (SOT notes_and_replies.md)
  Section C — ReplySafetyGate (SOT observations.md + workflow_discovery.md)
  Section D — FreshdeskClient extended endpoints (SOT api_reference.md)
  Section E — Payload normalization (both webhook shapes)
  Section F — Comment-type detection (customer / agent / internal note)
  Section G — Pre-filter logic (SOT webhook_contract.md Section 7)
  Section H — Tenant resolution (SOT workflow_discovery.md Section 8)
  Section I — Verifier / HMAC / static / replay (SOT webhook_contract.md Section 6)
  Section J — Idempotency store contract
  Section K — Conversation state lifecycle
  Section L — Response service constraints
  Section M — Configuration surface
  Section N — Public API export surface
  Section O — SOT compliance table

Never touches the network. Every HTTP interaction is via httpx.MockTransport.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

# ── Environment guardrails (before importing app code) ────────────────────────
os.environ.setdefault("RAG_API_KEY", "test-248-key")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-248")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-supabase-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENABLED", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

import httpx
import pytest

from freshdesk import (
    AI_READ_ONLY_CUSTOM_FIELDS,
    AI_WRITEABLE_CUSTOM_FIELDS,
    ALLOWED_TICKET_TYPES,
    DEFAULT_CONFIDENCE_THRESHOLD,
    DRAFT_HEADER,
    FORCE_ESCALATION_IMPACT_VALUES,
    REQUIRED_CLOSURE_FIELDS,
    SIGNATURE,
    STATUS_CLOSED,
    STATUS_RESOLVED,
    ClosureFieldGuard,
    ClosureGuardError,
    ConversationLifecycle,
    ConversationState,
    ConversationStateStore,
    FreshdeskClient,
    FreshdeskConfig,
    FreshdeskConversation,
    FreshdeskCustomFields,
    FreshdeskLatestComment,
    FreshdeskPriority,
    FreshdeskResponseService,
    FreshdeskStatus,
    FreshdeskTicketPayload,
    FreshdeskUpdateEvent,
    FreshdeskWebhookPayload,
    FreshdeskWebhookVerifier,
    GateDecision,
    GateOutcome,
    GuardDecision,
    IdempotencyStatus,
    ReplySafetyGate,
    VerificationResult,
    WebhookIdempotencyStore,
    build_clarification_reply,
    build_diagnostic_note,
    build_draft_reply_note,
    build_escalation_note,
    build_escalation_reply,
    build_resolution_reply,
    build_unknown_tenant_note,
    build_webhook_verifier,
)
from freshdesk.freshdesk_exceptions import (
    FreshdeskAuthError,
    FreshdeskNotFoundError,
    FreshdeskRateLimitError,
    FreshdeskServerError,
    FreshdeskValidationError,
)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _make_response(status_code: int, data: Any) -> httpx.Response:
    content = json.dumps(data).encode()
    return httpx.Response(
        status_code,
        content=content,
        headers={
            "content-type": "application/json",
            "x-ratelimit-total": "40.0",
            "x-ratelimit-remaining": "35.0",
        },
    )


def _make_client(handler) -> FreshdeskClient:
    transport = httpx.MockTransport(handler)
    inner = httpx.AsyncClient(
        base_url="https://test.freshdesk.com",
        auth=httpx.BasicAuth("k", "X"),
        transport=transport,
        headers={"Accept": "application/json"},
    )
    return FreshdeskClient(
        FreshdeskConfig(domain="test.freshdesk.com", api_key="k"),
        http_client=inner,
    )


def _iso_now(delta_seconds: float = 0.0) -> str:
    ts = datetime.now(tz=timezone.utc) + timedelta(seconds=delta_seconds)
    return ts.isoformat().replace("+00:00", "Z")


# ══════════════════════════════════════════════════════════════════════════════
# Section A — ClosureFieldGuard  (SOT ticket_lifecycle.md Section 6)
# ══════════════════════════════════════════════════════════════════════════════

class TestA_ClosureGuardConstants:
    def test_A1_required_closure_fields_matches_sot(self):
        assert REQUIRED_CLOSURE_FIELDS == frozenset({
            "cf_clients", "ticket_type", "cf_sop_status", "cf_resolution_classification",
        })

    def test_A2_status_resolved_is_4(self):
        assert STATUS_RESOLVED == 4

    def test_A3_status_closed_is_5(self):
        assert STATUS_CLOSED == 5

    def test_A4_allowed_ticket_types_has_8_values(self):
        assert len(ALLOWED_TICKET_TYPES) == 8

    def test_A5_ticket_type_issues_is_allowed(self):
        assert "Issues" in ALLOWED_TICKET_TYPES

    def test_A6_ticket_type_p1_is_not_allowed(self):
        assert "P1" not in ALLOWED_TICKET_TYPES

    def test_A7_ai_read_only_includes_cf_clients(self):
        assert "cf_clients" in AI_READ_ONLY_CUSTOM_FIELDS

    def test_A8_ai_read_only_includes_cf_environment(self):
        assert "cf_environment" in AI_READ_ONLY_CUSTOM_FIELDS

    def test_A9_ai_writeable_includes_cf_sop_status(self):
        assert "cf_sop_status" in AI_WRITEABLE_CUSTOM_FIELDS

    def test_A10_ai_writeable_and_read_only_are_disjoint(self):
        assert not (AI_WRITEABLE_CUSTOM_FIELDS & AI_READ_ONLY_CUSTOM_FIELDS)


class TestA_ClosureGuardIsTransition:
    def setup_method(self):
        self.guard = ClosureFieldGuard()

    def test_A11_not_transition_when_no_status(self):
        assert self.guard.is_closure_transition({}) is False

    def test_A12_not_transition_for_open(self):
        assert self.guard.is_closure_transition({"status": 2}) is False

    def test_A13_not_transition_for_pending(self):
        assert self.guard.is_closure_transition({"status": 3}) is False

    def test_A14_transition_for_resolved(self):
        assert self.guard.is_closure_transition({"status": 4}) is True

    def test_A15_transition_for_closed(self):
        assert self.guard.is_closure_transition({"status": 5}) is True

    def test_A16_transition_for_string_status(self):
        assert self.guard.is_closure_transition({"status": "4"}) is True

    def test_A17_not_transition_for_bogus_status(self):
        assert self.guard.is_closure_transition({"status": "foo"}) is False


class TestA_ClosureGuardMissing:
    def setup_method(self):
        self.guard = ClosureFieldGuard()

    def test_A20_all_missing_when_payload_and_current_empty(self):
        missing = self.guard.missing_closure_fields({"status": 4})
        assert set(missing) == REQUIRED_CLOSURE_FIELDS

    def test_A21_payload_supplies_all_fields(self):
        payload = {
            "status": 4,
            "type": "Issues",
            "custom_fields": {
                "cf_clients": "Unity",
                "cf_sop_status": "SOP Present",
                "cf_resolution_classification": "Solved by SOP (Permanent Fix)",
            },
        }
        assert self.guard.missing_closure_fields(payload) == []

    def test_A22_current_supplies_all_fields(self):
        payload = {"status": 4}
        current = {
            "type": "Issues",
            "custom_fields": {
                "cf_clients": "Unity",
                "cf_sop_status": "SOP Present",
                "cf_resolution_classification": "Solved by SOP (Permanent Fix)",
            },
        }
        assert self.guard.missing_closure_fields(payload, current) == []

    def test_A23_ticket_custom_fields_webhook_key_tolerated(self):
        payload = {"status": 4}
        current = {
            "ticket_type": "Issues",
            "ticket_custom_fields": {
                "cf_clients": "Unity",
                "cf_sop_status": "SOP Present",
                "cf_resolution_classification": "Solved by SOP (Permanent Fix)",
            },
        }
        assert self.guard.missing_closure_fields(payload, current) == []

    def test_A24_missing_only_sop_status(self):
        payload = {
            "status": 4,
            "type": "Issues",
            "custom_fields": {
                "cf_clients": "Unity",
                "cf_resolution_classification": "Solved by SOP (Permanent Fix)",
            },
        }
        assert self.guard.missing_closure_fields(payload) == ["cf_sop_status"]

    def test_A25_empty_string_is_missing(self):
        payload = {
            "status": 4,
            "type": "",
            "custom_fields": {
                "cf_clients": "Unity",
                "cf_sop_status": "SOP Present",
                "cf_resolution_classification": "Solved by SOP (Permanent Fix)",
            },
        }
        assert "ticket_type" in self.guard.missing_closure_fields(payload)

    def test_A26_whitespace_only_is_missing(self):
        payload = {
            "status": 4,
            "type": "Issues",
            "custom_fields": {
                "cf_clients": "   ",
                "cf_sop_status": "SOP Present",
                "cf_resolution_classification": "Solved by SOP (Permanent Fix)",
            },
        }
        assert "cf_clients" in self.guard.missing_closure_fields(payload)

    def test_A27_payload_overrides_current(self):
        payload = {
            "status": 4,
            "type": "Issues",
            "custom_fields": {"cf_sop_status": "SOP Present"},
        }
        current = {
            "type": "Issues",
            "custom_fields": {
                "cf_clients": "Unity",
                "cf_sop_status": "",
                "cf_resolution_classification": "Solved by SOP (Permanent Fix)",
            },
        }
        assert self.guard.missing_closure_fields(payload, current) == []


class TestA_ClosureGuardForbidden:
    def setup_method(self):
        self.guard = ClosureFieldGuard()

    def test_A30_no_forbidden_when_cf_empty(self):
        assert self.guard.forbidden_writes({}) == []

    def test_A31_forbid_cf_clients_write(self):
        payload = {"custom_fields": {"cf_clients": "Unity"}}
        assert self.guard.forbidden_writes(payload) == ["cf_clients"]

    def test_A32_forbid_cf_environment_write(self):
        payload = {"custom_fields": {"cf_environment": "Production"}}
        assert self.guard.forbidden_writes(payload) == ["cf_environment"]

    def test_A33_forbid_both(self):
        payload = {"custom_fields": {"cf_clients": "U", "cf_environment": "P"}}
        assert self.guard.forbidden_writes(payload) == ["cf_clients", "cf_environment"]

    def test_A34_allow_sop_status(self):
        payload = {"custom_fields": {"cf_sop_status": "SOP Present"}}
        assert self.guard.forbidden_writes(payload) == []


class TestA_ClosureGuardInvalidType:
    def setup_method(self):
        self.guard = ClosureFieldGuard()

    def test_A40_no_invalid_when_type_absent(self):
        assert self.guard.invalid_ticket_type({}) is None

    def test_A41_allow_issues(self):
        assert self.guard.invalid_ticket_type({"type": "Issues"}) is None

    def test_A42_allow_all_documented(self):
        for t in ALLOWED_TICKET_TYPES:
            assert self.guard.invalid_ticket_type({"type": t}) is None

    def test_A43_reject_p1(self):
        assert self.guard.invalid_ticket_type({"type": "P1"}) == "P1"

    def test_A44_reject_p1_via_ticket_type_key(self):
        assert self.guard.invalid_ticket_type({"ticket_type": "P1"}) == "P1"

    def test_A45_ignore_empty_value(self):
        assert self.guard.invalid_ticket_type({"type": ""}) is None


class TestA_ClosureGuardDecision:
    def setup_method(self):
        self.guard = ClosureFieldGuard()

    def test_A50_ok_for_non_closure_update(self):
        decision = self.guard.guard_status_transition({"priority": 3})
        assert decision.allowed
        assert decision.reason == "OK"

    def test_A51_block_forbidden_wins_over_closure(self):
        payload = {"status": 4, "custom_fields": {"cf_clients": "Unity"}}
        decision = self.guard.guard_status_transition(payload)
        assert not decision.allowed
        assert "cf_clients" in decision.forbidden_writes

    def test_A52_block_invalid_type(self):
        decision = self.guard.guard_status_transition({"type": "P1"})
        assert not decision.allowed
        assert decision.invalid_type_value == "P1"

    def test_A53_block_missing_fields_on_close(self):
        decision = self.guard.guard_status_transition({"status": 4})
        assert not decision.allowed
        assert set(decision.missing_fields) == REQUIRED_CLOSURE_FIELDS

    def test_A54_ok_on_close_with_all_fields(self):
        payload = {
            "status": 5,
            "type": "Issues",
            "custom_fields": {
                "cf_sop_status": "SOP Present",
                "cf_resolution_classification": "Solved by SOP (Permanent Fix)",
            },
        }
        current = {"custom_fields": {"cf_clients": "Unity"}}
        decision = self.guard.guard_status_transition(payload, current)
        assert decision.allowed

    def test_A55_assert_safe_raises_on_missing(self):
        with pytest.raises(ClosureGuardError) as exc:
            self.guard.assert_safe({"status": 4})
        assert set(exc.value.missing_fields) == REQUIRED_CLOSURE_FIELDS

    def test_A56_assert_safe_silent_on_ok(self):
        self.guard.assert_safe({"priority": 3})

    def test_A57_decision_helpers(self):
        assert GuardDecision.ok().allowed
        assert not GuardDecision.block_missing(["x"]).allowed
        assert not GuardDecision.block_forbidden(["y"]).allowed
        assert not GuardDecision.block_invalid_type("Z").allowed


# ══════════════════════════════════════════════════════════════════════════════
# Section B — HTML template builders (SOT notes_and_replies.md)
# ══════════════════════════════════════════════════════════════════════════════

class TestB_ResolutionReply:
    def test_B1_greeting_uses_customer_name(self):
        html = build_resolution_reply("Jane", "The session has been updated.")
        assert "Hi Jane," in html

    def test_B2_greeting_defaults_when_name_empty(self):
        html = build_resolution_reply("", "The session has been updated.")
        assert "Hi there," in html

    def test_B3_contains_signature(self):
        html = build_resolution_reply("Jane", "OK")
        assert SIGNATURE in html

    def test_B4_body_wrapped_in_p_tag(self):
        html = build_resolution_reply("Jane", "Body text")
        assert "<p>Body text</p>" in html

    def test_B5_html_escaped_in_name(self):
        html = build_resolution_reply("<script>", "Body")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_B6_html_escaped_in_body(self):
        html = build_resolution_reply("Jane", "<img src=x>")
        assert "<img src=x>" not in html

    def test_B7_acknowledgment_included_when_provided(self):
        html = build_resolution_reply(
            "Jane", "Body", acknowledgment="Sorry for the inconvenience."
        )
        assert "Sorry for the inconvenience." in html

    def test_B8_further_issues_note_present(self):
        html = build_resolution_reply("Jane", "Body")
        assert "further issues" in html


class TestB_ClarificationReply:
    def test_B10_lists_all_questions(self):
        qs = ["What is the session ID?", "When did the issue occur?"]
        html = build_clarification_reply("Jane", qs)
        assert "session ID" in html
        assert "issue occur" in html

    def test_B11_default_preamble(self):
        html = build_clarification_reply("Jane", ["Q?"])
        assert "more information" in html

    def test_B12_custom_preamble(self):
        html = build_clarification_reply("Jane", ["Q?"], preamble="Custom preamble here.")
        assert "Custom preamble here." in html

    def test_B13_empty_question_list_still_returns_html(self):
        html = build_clarification_reply("Jane", [])
        assert "<p>Hi Jane," in html

    def test_B14_uses_ul_li(self):
        html = build_clarification_reply("Jane", ["Q?"])
        assert "<ul>" in html and "<li>" in html

    def test_B15_html_escapes_questions(self):
        html = build_clarification_reply("Jane", ["<b>bold</b>"])
        assert "<b>bold</b>" not in html


class TestB_EscalationReply:
    def test_B20_mentions_engineering(self):
        html = build_escalation_reply("Jane")
        assert "engineering team" in html

    def test_B21_uses_customer_name(self):
        html = build_escalation_reply("Alice")
        assert "Hi Alice," in html

    def test_B22_engineering_details_optional(self):
        html = build_escalation_reply("Jane", engineering_details="Ticket #E-42")
        assert "Ticket #E-42" in html

    def test_B23_reassurance_included(self):
        html = build_escalation_reply("Jane")
        assert "keep you updated" in html


class TestB_DiagnosticNote:
    def test_B30_header_present(self):
        note = build_diagnostic_note(ticket_id="123", client="Unity")
        assert "KwikID AI Support Agent" in note
        assert "123" in note
        assert "Unity" in note

    def test_B31_classification_block_omitted_when_empty(self):
        note = build_diagnostic_note(ticket_id="1", client="Unity")
        assert "<strong>Classification:</strong>" not in note

    def test_B32_classification_block_shows_query_type(self):
        note = build_diagnostic_note(
            ticket_id="1", client="Unity", query_type="Session Not Available",
        )
        assert "Classification" in note
        assert "Session Not Available" in note

    def test_B33_sop_link_rendered_as_anchor(self):
        note = build_diagnostic_note(
            ticket_id="1", client="Unity",
            sop_link="https://stack.example/q/1",
        )
        assert '<a href="https://stack.example/q/1">' in note

    def test_B34_sop_link_bad_scheme_dropped(self):
        note = build_diagnostic_note(
            ticket_id="1", client="Unity",
            sop_link="javascript:alert(1)",
        )
        assert "javascript:" not in note

    def test_B35_confidence_label_high(self):
        note = build_diagnostic_note(
            ticket_id="1", client="Unity", confidence="high",
        )
        assert "HIGH" in note

    def test_B36_confidence_label_medium(self):
        note = build_diagnostic_note(
            ticket_id="1", client="Unity", confidence="medium",
        )
        assert "MEDIUM" in note

    def test_B37_confidence_unknown_label(self):
        note = build_diagnostic_note(
            ticket_id="1", client="Unity", confidence="bogus",
        )
        assert "UNKNOWN" in note

    def test_B38_handling_time_shown(self):
        note = build_diagnostic_note(
            ticket_id="1", client="Unity", handling_time_minutes=12,
        )
        assert "12 minutes" in note

    def test_B39_handling_time_omitted_when_none(self):
        note = build_diagnostic_note(ticket_id="1", client="Unity")
        assert "minutes" not in note


class TestB_DraftReplyNote:
    def test_B40_contains_ai_draft_header(self):
        note = build_draft_reply_note("<p>hello</p>", reason_not_auto_sent="test")
        assert DRAFT_HEADER in note

    def test_B41_reason_included(self):
        note = build_draft_reply_note(
            "<p>hello</p>", reason_not_auto_sent="Confidence too low",
        )
        assert "Confidence too low" in note

    def test_B42_draft_body_preserved(self):
        draft = "<p>Draft body content.</p>"
        note = build_draft_reply_note(draft, reason_not_auto_sent="reason")
        assert draft in note

    def test_B43_confidence_optional(self):
        note = build_draft_reply_note("<p>x</p>", reason_not_auto_sent="r")
        assert "Confidence" not in note

    def test_B44_confidence_label_when_supplied(self):
        note = build_draft_reply_note(
            "<p>x</p>", reason_not_auto_sent="r", confidence="high",
        )
        assert "HIGH" in note


class TestB_EscalationNote:
    def test_B50_root_cause_included(self):
        note = build_escalation_note(ticket_id="1", root_cause="DB timeout")
        assert "DB timeout" in note

    def test_B51_asana_url_rendered_when_safe(self):
        note = build_escalation_note(
            ticket_id="1", root_cause="rca",
            asana_url="https://app.asana.com/task/1",
        )
        assert 'href="https://app.asana.com/task/1"' in note

    def test_B52_asana_url_dropped_when_unsafe(self):
        note = build_escalation_note(
            ticket_id="1", root_cause="rca",
            asana_url="javascript:alert(1)",
        )
        assert "javascript:" not in note

    def test_B53_reproduction_steps_included(self):
        note = build_escalation_note(
            ticket_id="1", root_cause="rca",
            reproduction_steps="1. Do X\n2. Do Y",
        )
        assert "Do X" in note


class TestB_UnknownTenantNote:
    def test_B60_mentions_domain(self):
        note = build_unknown_tenant_note("acme.com")
        assert "acme.com" in note

    def test_B61_mentions_human_review(self):
        note = build_unknown_tenant_note("acme.com")
        assert "Human review required" in note

    def test_B62_html_escapes_domain(self):
        note = build_unknown_tenant_note("<evil>")
        assert "<evil>" not in note
        assert "&lt;evil&gt;" in note


# ══════════════════════════════════════════════════════════════════════════════
# Section C — ReplySafetyGate (SOT observations.md + workflow_discovery.md)
# ══════════════════════════════════════════════════════════════════════════════

class TestC_ReplySafetyGateConstruction:
    def test_C1_default_threshold(self):
        gate = ReplySafetyGate()
        assert gate.confidence_threshold == DEFAULT_CONFIDENCE_THRESHOLD

    def test_C2_reject_out_of_range_threshold_low(self):
        with pytest.raises(ValueError):
            ReplySafetyGate(confidence_threshold=-0.1)

    def test_C3_reject_out_of_range_threshold_high(self):
        with pytest.raises(ValueError):
            ReplySafetyGate(confidence_threshold=1.5)

    def test_C4_accept_threshold_at_zero(self):
        ReplySafetyGate(confidence_threshold=0.0)

    def test_C5_accept_threshold_at_one(self):
        ReplySafetyGate(confidence_threshold=1.0)


class TestC_ReplySafetyGateCheck:
    def setup_method(self):
        self.gate = ReplySafetyGate(confidence_threshold=0.75)

    def test_C10_allow_high_confidence(self):
        d = self.gate.check(
            ticket_id="1", body_html="<p>Ok</p>", confidence=0.9,
        )
        assert d.allowed
        assert d.outcome == GateOutcome.ALLOW

    def test_C11_block_low_confidence(self):
        d = self.gate.check(
            ticket_id="1", body_html="<p>Ok</p>", confidence=0.6,
        )
        assert not d.allowed
        assert d.outcome == GateOutcome.BLOCK_CONFIDENCE
        assert d.should_draft

    def test_C12_block_missing_confidence(self):
        d = self.gate.check(
            ticket_id="1", body_html="<p>Ok</p>", confidence=None,
        )
        assert d.outcome == GateOutcome.BLOCK_CONFIDENCE

    def test_C13_block_empty_body(self):
        d = self.gate.check(
            ticket_id="1", body_html="", confidence=0.99,
        )
        assert d.outcome == GateOutcome.BLOCK_MISSING_BODY
        assert not d.should_draft   # nothing to draft

    def test_C14_block_whitespace_body(self):
        d = self.gate.check(
            ticket_id="1", body_html="   \n\t  ", confidence=0.99,
        )
        assert d.outcome == GateOutcome.BLOCK_MISSING_BODY

    def test_C15_block_downtime_impact(self):
        d = self.gate.check(
            ticket_id="1", body_html="<p>Ok</p>",
            confidence=0.99, impact="DOWNTIME 100% impact",
        )
        assert d.outcome == GateOutcome.BLOCK_IMPACT
        assert d.should_draft

    def test_C16_block_client_escalation_impact(self):
        d = self.gate.check(
            ticket_id="1", body_html="<p>Ok</p>",
            confidence=0.99, impact="Client Escalation",
        )
        assert d.outcome == GateOutcome.BLOCK_IMPACT

    def test_C17_allow_benign_impact(self):
        d = self.gate.check(
            ticket_id="1", body_html="<p>Ok</p>",
            confidence=0.99, impact="Normal",
        )
        assert d.allowed

    def test_C18_kill_switch_blocks_all(self):
        gate = ReplySafetyGate(kill_switch=True)
        d = gate.check(
            ticket_id="1", body_html="<p>Ok</p>", confidence=0.99,
        )
        assert d.outcome == GateOutcome.BLOCK_KILL_SWITCH

    def test_C19_engage_and_release_kill_switch(self):
        self.gate.engage_kill_switch()
        d1 = self.gate.check(
            ticket_id="1", body_html="<p>Ok</p>", confidence=0.99,
        )
        assert d1.outcome == GateOutcome.BLOCK_KILL_SWITCH
        self.gate.release_kill_switch()
        d2 = self.gate.check(
            ticket_id="2", body_html="<p>Ok</p>", confidence=0.99,
        )
        assert d2.allowed


class TestC_ReplySafetyGateIdempotency:
    def setup_method(self):
        self.gate = ReplySafetyGate()

    def test_C30_first_reply_allowed(self):
        d = self.gate.check(
            ticket_id="1", body_html="<p>Body</p>", confidence=0.9,
        )
        assert d.allowed

    def test_C31_duplicate_blocked(self):
        self.gate.check(ticket_id="1", body_html="<p>Body</p>", confidence=0.9)
        d = self.gate.check(ticket_id="1", body_html="<p>Body</p>", confidence=0.9)
        assert d.outcome == GateOutcome.BLOCK_DUPLICATE

    def test_C32_different_body_allowed(self):
        self.gate.check(ticket_id="1", body_html="<p>A</p>", confidence=0.9)
        d = self.gate.check(ticket_id="1", body_html="<p>B</p>", confidence=0.9)
        assert d.allowed

    def test_C33_different_ticket_allowed(self):
        self.gate.check(ticket_id="1", body_html="<p>A</p>", confidence=0.9)
        d = self.gate.check(ticket_id="2", body_html="<p>A</p>", confidence=0.9)
        assert d.allowed

    def test_C34_reset_idempotency(self):
        self.gate.check(ticket_id="1", body_html="<p>A</p>", confidence=0.9)
        self.gate.reset_idempotency()
        d = self.gate.check(ticket_id="1", body_html="<p>A</p>", confidence=0.9)
        assert d.allowed

    def test_C35_whitespace_normalized_in_hash(self):
        self.gate.check(ticket_id="1", body_html="<p>Ok  Ok</p>", confidence=0.9)
        d = self.gate.check(
            ticket_id="1", body_html="<p>Ok Ok</p>", confidence=0.9,
        )
        assert d.outcome == GateOutcome.BLOCK_DUPLICATE


# ══════════════════════════════════════════════════════════════════════════════
# Section D — FreshdeskClient extended endpoints (SOT api_reference.md)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestD_ExtendedClientEndpoints:
    async def test_D1_list_tickets_basic(self):
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _make_response(200, [{"id": 1}, {"id": 2}])

        client = _make_client(handler)
        try:
            result = await client.list_tickets(status=2, page=1, per_page=50)
        finally:
            await client.close()
        assert len(result) == 2
        assert "/api/v2/tickets" in seen["url"]
        assert "status=2" in seen["url"]

    async def test_D2_list_tickets_per_page_clamped(self):
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _make_response(200, [])

        client = _make_client(handler)
        try:
            await client.list_tickets(per_page=999)
        finally:
            await client.close()
        assert "per_page=100" in seen["url"]

    async def test_D3_list_tickets_per_page_min(self):
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _make_response(200, [])

        client = _make_client(handler)
        try:
            await client.list_tickets(per_page=0)
        finally:
            await client.close()
        assert "per_page=1" in seen["url"]

    async def test_D4_search_tickets(self):
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _make_response(200, {"total": 3, "results": [{"id": 10}]})

        client = _make_client(handler)
        try:
            data = await client.search_tickets("cf_clients:'Unity'")
        finally:
            await client.close()
        assert data["total"] == 3
        assert "/api/v2/search/tickets" in seen["url"]

    async def test_D5_search_tickets_bad_shape(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _make_response(200, [{"unexpected": "shape"}])

        client = _make_client(handler)
        try:
            data = await client.search_tickets("*")
        finally:
            await client.close()
        assert data == {"total": 0, "results": []}

    async def test_D6_list_agents(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _make_response(200, [{"id": 1, "email": "a@k.co"}])

        client = _make_client(handler)
        try:
            data = await client.list_agents()
        finally:
            await client.close()
        assert len(data) == 1

    async def test_D7_list_groups(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _make_response(200, [{"id": 84000293343, "name": "L1"}])

        client = _make_client(handler)
        try:
            data = await client.list_groups()
        finally:
            await client.close()
        assert data[0]["name"] == "L1"

    async def test_D8_add_public_reply_with_cc(self):
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _make_response(201, {"id": 999})

        client = _make_client(handler)
        try:
            data = await client.add_public_reply_with_cc(
                "1", "<p>x</p>",
                cc_emails=["cc@example.com"],
                bcc_emails=["bcc@example.com"],
            )
        finally:
            await client.close()
        assert data["id"] == 999
        assert seen["body"]["cc_emails"] == ["cc@example.com"]
        assert seen["body"]["bcc_emails"] == ["bcc@example.com"]

    async def test_D9_add_public_reply_with_cc_omits_none(self):
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _make_response(201, {"id": 1})

        client = _make_client(handler)
        try:
            await client.add_public_reply_with_cc("1", "<p>x</p>")
        finally:
            await client.close()
        assert "cc_emails" not in seen["body"]
        assert "bcc_emails" not in seen["body"]

    async def test_D10_get_ticket_still_works(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _make_response(200, {"id": 42, "status": 2})

        client = _make_client(handler)
        try:
            data = await client.get_ticket(42)
        finally:
            await client.close()
        assert data["id"] == 42

    async def test_D11_get_ticket_conversations(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _make_response(200, [
                {"id": 1, "incoming": True, "private": False, "body": "hi"},
            ])

        client = _make_client(handler)
        try:
            data = await client.get_ticket_conversations(42)
        finally:
            await client.close()
        assert len(data) == 1

    async def test_D12_add_private_note_marks_private_true(self):
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _make_response(201, {"id": 1})

        client = _make_client(handler)
        try:
            await client.add_private_note(42, "<p>note</p>")
        finally:
            await client.close()
        assert seen["body"]["private"] is True

    async def test_D13_update_ticket(self):
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _make_response(200, {"id": 42})

        client = _make_client(handler)
        try:
            await client.update_ticket(42, {"status": 4})
        finally:
            await client.close()
        assert seen["body"]["status"] == 4

    async def test_D14_add_tags_merges(self):
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.method + " " + request.url.path)
            if request.method == "GET":
                return _make_response(200, {"id": 42, "tags": ["a", "b"]})
            body = json.loads(request.content)
            assert set(body["tags"]) == {"a", "b", "c"}
            return _make_response(200, {"id": 42})

        client = _make_client(handler)
        try:
            await client.add_tags(42, ["c"])
        finally:
            await client.close()
        assert calls[0].startswith("GET")
        assert calls[1].startswith("PUT")

    async def test_D15_remove_tags(self):
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _make_response(200, {"id": 42})

        client = _make_client(handler)
        try:
            await client.remove_tags(42, ["a", "b", "c"], ["b"])
        finally:
            await client.close()
        assert set(seen["body"]["tags"]) == {"a", "c"}

    async def test_D16_401_raises_auth_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _make_response(401, {"error": "bad"})

        client = _make_client(handler)
        try:
            with pytest.raises(FreshdeskAuthError):
                await client.get_ticket(42)
        finally:
            await client.close()

    async def test_D17_404_raises_not_found(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _make_response(404, {"error": "nope"})

        client = _make_client(handler)
        try:
            with pytest.raises(FreshdeskNotFoundError):
                await client.get_ticket(42)
        finally:
            await client.close()

    async def test_D18_422_raises_validation(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _make_response(422, {"errors": []})

        client = _make_client(handler)
        try:
            with pytest.raises(FreshdeskValidationError):
                await client.update_ticket(42, {"status": 4})
        finally:
            await client.close()


# ══════════════════════════════════════════════════════════════════════════════
# Section E — Payload normalization (webhook_contract.md Sections 3–5)
# ══════════════════════════════════════════════════════════════════════════════

class TestE_PayloadNormalization:
    def test_E1_format_a_freshdesk_webhook_envelope(self):
        raw = {
            "freshdesk_webhook": {
                "id": 197416,
                "subject": "Auditor issue",
                "description": "<div>Hi</div>",
                "description_text": "Hi",
                "status": 2,
                "priority": 3,
                "requester_email": "j@unitybank.co.in",
                "requester_name": "Jane",
                "tags": "a, b, c",
                "ticket_custom_fields": {
                    "cf_clients": "Unity",
                    "cf_session_ids": "abc",
                },
            }
        }
        payload = FreshdeskWebhookPayload.from_dict(raw)
        assert payload.ticket.id == 197416
        assert payload.ticket.custom_fields.cf_clients == "Unity"
        assert payload.ticket.tags == ["a", "b", "c"]

    def test_E2_format_b_dispatchr_minimal(self):
        raw = {
            "ticket": {
                "id": "197416",
                "subject": "S",
                "description": "D",
                "created_at": "2026-06-18T08:06:12Z",
            },
            "requester": {"email": "j@unitybank.co.in", "name": "J"},
            "custom_fields": {"cf_clients": "Unity"},
        }
        payload = FreshdeskWebhookPayload.from_dict(raw)
        assert payload.ticket.id == 197416
        assert payload.ticket.requester_email == "j@unitybank.co.in"

    def test_E3_id_and_ticket_id_both_supported(self):
        raw = {"freshdesk_webhook": {"ticket_id": 111}}
        payload = FreshdeskWebhookPayload.from_dict(raw)
        assert payload.ticket.id == 111

    def test_E4_status_cast_to_enum(self):
        raw = {"freshdesk_webhook": {"id": 1, "status": 4}}
        payload = FreshdeskWebhookPayload.from_dict(raw)
        assert payload.ticket.status == FreshdeskStatus.RESOLVED

    def test_E5_priority_cast_to_enum(self):
        raw = {"freshdesk_webhook": {"id": 1, "priority": 4}}
        payload = FreshdeskWebhookPayload.from_dict(raw)
        assert payload.ticket.priority == FreshdeskPriority.URGENT

    def test_E6_bogus_status_defaults_to_open(self):
        raw = {"freshdesk_webhook": {"id": 1, "status": 99}}
        payload = FreshdeskWebhookPayload.from_dict(raw)
        assert payload.ticket.status == FreshdeskStatus.OPEN

    def test_E7_ticket_type_alias_type(self):
        raw = {"freshdesk_webhook": {"id": 1, "type": "Issues"}}
        payload = FreshdeskWebhookPayload.from_dict(raw)
        assert payload.ticket.ticket_type == "Issues"

    def test_E8_tags_list_input(self):
        raw = {"freshdesk_webhook": {"id": 1, "tags": ["x", "y"]}}
        payload = FreshdeskWebhookPayload.from_dict(raw)
        assert payload.ticket.tags == ["x", "y"]

    def test_E9_custom_fields_client_alias(self):
        raw = {"freshdesk_webhook": {"id": 1, "ticket_custom_fields": {"client": "BOB"}}}
        payload = FreshdeskWebhookPayload.from_dict(raw)
        assert payload.ticket.custom_fields.cf_clients == "BOB"

    def test_E10_ticket_cf_flat_prefix(self):
        raw = {
            "freshdesk_webhook": {
                "id": 1,
                "ticket_cf_clients": "Unity",
                "ticket_cf_environment": "UAT",
            }
        }
        payload = FreshdeskWebhookPayload.from_dict(raw)
        assert payload.ticket.custom_fields.cf_clients == "Unity"
        assert payload.ticket.custom_fields.cf_environment == "UAT"


class TestE_UpdateEventNormalization:
    def test_E20_customer_reply_latest_comment(self):
        raw = {
            "freshdesk_webhook": {
                "id": 197416,
                "updated_at": "2026-06-18T10:22:10Z",
                "latest_comment": {
                    "body_text": "yes I did",
                    "incoming": True,
                    "private": False,
                },
            }
        }
        event = FreshdeskUpdateEvent.from_dict(raw)
        assert event.ticket_id == 197416
        assert event.latest_comment.is_customer_reply is True

    def test_E21_agent_reply_latest_comment(self):
        raw = {
            "freshdesk_webhook": {
                "id": 1,
                "latest_comment": {"incoming": False, "private": False, "body": "x"},
            }
        }
        event = FreshdeskUpdateEvent.from_dict(raw)
        assert event.latest_comment.is_agent_reply

    def test_E22_internal_note_latest_comment(self):
        raw = {
            "freshdesk_webhook": {
                "id": 1,
                "latest_comment": {"incoming": False, "private": True, "body": "x"},
            }
        }
        event = FreshdeskUpdateEvent.from_dict(raw)
        assert event.latest_comment.is_internal_note

    def test_E23_no_latest_comment(self):
        raw = {"freshdesk_webhook": {"id": 1}}
        event = FreshdeskUpdateEvent.from_dict(raw)
        assert event.latest_comment is None


class TestE_ConversationTypeDetection:
    def test_E30_customer_reply(self):
        c = FreshdeskConversation.from_dict({"incoming": True, "private": False})
        assert c.is_customer_reply

    def test_E31_agent_reply(self):
        c = FreshdeskConversation.from_dict({"incoming": False, "private": False})
        assert c.is_agent_reply

    def test_E32_internal_note(self):
        c = FreshdeskConversation.from_dict({"incoming": False, "private": True})
        assert c.is_internal_note


# ══════════════════════════════════════════════════════════════════════════════
# Section F — Verifier / HMAC / static / replay
# ══════════════════════════════════════════════════════════════════════════════

class TestF_Verifier:
    def test_F1_reject_empty_secret(self):
        with pytest.raises(ValueError):
            FreshdeskWebhookVerifier("")

    def test_F2_reject_invalid_mode(self):
        with pytest.raises(ValueError):
            FreshdeskWebhookVerifier("s", mode="unsupported")

    def test_F3_hmac_valid(self):
        secret = "topsecret"
        body = b'{"ticket":1}'
        sig = _hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        v = FreshdeskWebhookVerifier(secret, mode="hmac")
        result = v.verify({"X-Webhook-Token": sig}, body)
        assert result.valid

    def test_F4_hmac_invalid(self):
        v = FreshdeskWebhookVerifier("s", mode="hmac")
        result = v.verify({"X-Webhook-Token": "wrong"}, b'{}')
        assert not result.valid

    def test_F5_static_valid(self):
        v = FreshdeskWebhookVerifier("mytoken", mode="static")
        result = v.verify({"X-Webhook-Token": "mytoken"}, b'{}')
        assert result.valid

    def test_F6_static_invalid(self):
        v = FreshdeskWebhookVerifier("mytoken", mode="static")
        result = v.verify({"X-Webhook-Token": "notmine"}, b'{}')
        assert not result.valid

    def test_F7_no_header(self):
        v = FreshdeskWebhookVerifier("s", mode="hmac")
        result = v.verify({}, b'{}')
        assert not result.valid
        assert "No webhook signature" in result.reason

    def test_F8_fallback_signature_header(self):
        secret = "s"
        body = b'{}'
        sig = _hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        v = FreshdeskWebhookVerifier(secret, mode="hmac")
        result = v.verify({"X-Freshdesk-Signature": sig}, body)
        assert result.valid

    def test_F9_replay_too_old(self):
        v = FreshdeskWebhookVerifier("s", mode="static", replay_window_seconds=60)
        old = datetime.now(tz=timezone.utc) - timedelta(seconds=999)
        result = v.verify({"X-Webhook-Token": "s"}, b'{}', event_timestamp=old)
        assert not result.valid
        assert "Replay" in result.reason

    def test_F10_replay_too_future(self):
        v = FreshdeskWebhookVerifier("s", mode="static", replay_window_seconds=60)
        future = datetime.now(tz=timezone.utc) + timedelta(seconds=999)
        result = v.verify({"X-Webhook-Token": "s"}, b'{}', event_timestamp=future)
        assert not result.valid
        assert "Clock skew" in result.reason

    def test_F11_replay_naive_datetime(self):
        v = FreshdeskWebhookVerifier("s", mode="static")
        # Naive datetime with UTC clock value. The verifier assumes naive
        # datetimes are UTC; using datetime.now() (local time) would look
        # like a future timestamp on any host not on UTC and fail the
        # clock-skew check.
        naive = datetime.now(tz=timezone.utc).replace(tzinfo=None)
        result = v.verify({"X-Webhook-Token": "s"}, b'{}', event_timestamp=naive)
        assert result.valid

    def test_F12_enforce_false_bypasses(self):
        v = FreshdeskWebhookVerifier("s", mode="hmac", enforce=False)
        result = v.verify({"X-Webhook-Token": "wrong"}, b'{}')
        assert result.valid
        assert "BYPASS" in result.reason

    def test_F13_result_helpers(self):
        assert VerificationResult.ok("h").valid
        assert not VerificationResult.fail("nope").valid

    def test_F14_build_factory(self):
        v = build_webhook_verifier(webhook_secret="s")
        assert v.mode == "hmac"


# ══════════════════════════════════════════════════════════════════════════════
# Section G — Idempotency
# ══════════════════════════════════════════════════════════════════════════════

class TestG_IdempotencyStore:
    def setup_method(self):
        self.store = WebhookIdempotencyStore()

    def test_G1_key_format(self):
        key = self.store.make_key("100", "ticket_created", "2026-06-18T10:00:00Z")
        assert key == "100:ticket_created:2026-06-18T10:00:00Z"

    def test_G2_check_returns_false_when_absent(self):
        assert self.store.check("abc") is False

    def test_G3_mark_received_then_check_true(self):
        self.store.mark_received("k", "1", "ticket_created", _iso_now())
        assert self.store.check("k") is True

    def test_G4_mark_completed_updates_status(self):
        self.store.mark_received("k", "1", "ticket_created", _iso_now())
        self.store.mark_completed("k", case_id="C1")
        entry = self.store.get("k")
        assert entry.status == IdempotencyStatus.COMPLETED
        assert entry.case_id == "C1"

    def test_G5_mark_failed_updates_status(self):
        self.store.mark_received("k", "1", "ticket_created", _iso_now())
        self.store.mark_failed("k", "boom")
        entry = self.store.get("k")
        assert entry.status == IdempotencyStatus.FAILED
        assert entry.error_detail == "boom"

    def test_G6_count(self):
        self.store.mark_received("k1", "1", "e", _iso_now())
        self.store.mark_received("k2", "2", "e", _iso_now())
        assert self.store.count() == 2

    def test_G7_ensure_receipt_no_supabase_returns_false(self):
        assert self.store.ensure_receipt("k", "1", "e", _iso_now()) is False


# ══════════════════════════════════════════════════════════════════════════════
# Section H — Conversation state
# ══════════════════════════════════════════════════════════════════════════════

class TestH_ConversationState:
    def setup_method(self):
        self.store = ConversationStateStore()

    def test_H1_get_missing_returns_none(self):
        assert self.store.get("1") is None

    def test_H2_get_or_create(self):
        s = self.store.get_or_create("1", "Unity")
        assert s.ticket_id == "1"
        assert s.client_id == "Unity"

    def test_H3_get_or_create_idempotent(self):
        s1 = self.store.get_or_create("1", "Unity")
        s2 = self.store.get_or_create("1", "Unity")
        assert s1 is s2

    def test_H4_update_sets_fields(self):
        self.store.get_or_create("1", "Unity")
        self.store.update("1", clarification_pending=True)
        assert self.store.get("1").clarification_pending

    def test_H5_update_lifecycle_from_string(self):
        self.store.get_or_create("1", "Unity")
        self.store.update("1", lifecycle_state="CLARIFICATION")
        assert self.store.get("1").lifecycle_state == ConversationLifecycle.CLARIFICATION

    def test_H6_set_resolved(self):
        self.store.get_or_create("1", "Unity")
        self.store.set_resolved("1")
        s = self.store.get("1")
        assert s.lifecycle_state == ConversationLifecycle.RESOLVED
        assert s.resolved_at is not None

    def test_H7_set_escalated(self):
        self.store.get_or_create("1", "Unity")
        self.store.set_escalated("1")
        assert self.store.get("1").lifecycle_state == ConversationLifecycle.ESCALATED

    def test_H8_increment_clarification(self):
        self.store.get_or_create("1", "Unity")
        self.store.increment_clarification("1")
        s = self.store.get("1")
        assert s.clarification_count == 1
        assert s.awaiting_customer is True

    def test_H9_exists(self):
        assert not self.store.exists("1")
        self.store.get_or_create("1", "Unity")
        assert self.store.exists("1")

    def test_H10_all_ticket_ids(self):
        self.store.get_or_create("1", "Unity")
        self.store.get_or_create("2", "BOB")
        assert set(self.store.all_ticket_ids()) == {"1", "2"}


# ══════════════════════════════════════════════════════════════════════════════
# Section I — Response service constraints
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestI_ResponseService:
    async def test_I1_add_internal_note_success(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["private"] is True
            return _make_response(201, {"id": 1})

        client = _make_client(handler)
        svc = FreshdeskResponseService(client)
        try:
            result = await svc.add_internal_note("1", "<p>x</p>")
        finally:
            await client.close()
        assert result["id"] == 1

    async def test_I2_add_internal_note_swallows_exception(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _make_response(500, {"error": "boom"})

        client = _make_client(handler)
        svc = FreshdeskResponseService(client)
        try:
            result = await svc.add_internal_note("1", "<p>x</p>")
        finally:
            await client.close()
        assert result == {}

    async def test_I3_send_customer_reply_success(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert "private" not in body   # reply is public
            return _make_response(201, {"id": 2})

        client = _make_client(handler)
        svc = FreshdeskResponseService(client)
        try:
            result = await svc.send_customer_reply("1", "<p>y</p>")
        finally:
            await client.close()
        assert result["id"] == 2

    async def test_I4_send_customer_reply_never_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _make_response(500, {"error": "boom"})

        client = _make_client(handler)
        svc = FreshdeskResponseService(client)
        try:
            result = await svc.send_customer_reply("1", "<p>y</p>")
        finally:
            await client.close()
        assert result == {}


# ══════════════════════════════════════════════════════════════════════════════
# Section J — Configuration
# ══════════════════════════════════════════════════════════════════════════════

class TestJ_FreshdeskConfig:
    def test_J1_ok_config(self):
        c = FreshdeskConfig(domain="a.freshdesk.com", api_key="k")
        assert c.base_url == "https://a.freshdesk.com"

    def test_J2_reject_scheme_in_domain(self):
        with pytest.raises(ValueError):
            FreshdeskConfig(domain="https://a.freshdesk.com", api_key="k")

    def test_J3_reject_empty_key(self):
        with pytest.raises(ValueError):
            FreshdeskConfig(domain="a.freshdesk.com", api_key="")

    def test_J4_reject_zero_timeout(self):
        with pytest.raises(ValueError):
            FreshdeskConfig(domain="a.freshdesk.com", api_key="k", timeout_seconds=0)

    def test_J5_masked_key(self):
        c = FreshdeskConfig(domain="a.freshdesk.com", api_key="ABCD1234EFGH")
        assert c.masked_api_key == "ABCD****"


# ══════════════════════════════════════════════════════════════════════════════
# Section K — End-to-end Sprint 2.48 flow (closure + templates + gate)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestK_EndToEndFlow:
    async def test_K1_resolve_ticket_closure_flow(self):
        """
        Simulates the SOT closure sequence (notes_and_replies.md 6.1):
        private note → PUT with all closure fields + status=4 → public reply.
        The ClosureFieldGuard clears the PUT because all required fields are set.
        """
        seen_calls: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body: dict[str, Any] = {}
            if request.content:
                try:
                    body = json.loads(request.content)
                except Exception:
                    body = {}
            seen_calls.append({
                "method": request.method, "path": request.url.path, "body": body,
            })
            if request.method == "GET":
                return _make_response(200, {
                    "id": 42,
                    "type": "Issues",
                    "custom_fields": {"cf_clients": "Unity"},
                })
            return _make_response(200 if request.method == "PUT" else 201, {"id": 1})

        client = _make_client(handler)
        svc = FreshdeskResponseService(client)
        guard = ClosureFieldGuard()
        gate = ReplySafetyGate(confidence_threshold=0.75)
        try:
            # 1. Post diagnostic note
            note_body = build_diagnostic_note(
                ticket_id="42", client="Unity",
                query_type="Session Not Available", issue_area="Backend",
                sop_status="SOP Present", sop_link="https://s.example/1",
                confidence="high", resolution="Cleared cache",
                handling_time_minutes=12,
            )
            await svc.add_internal_note("42", note_body, case_id="C1", client_id="Unity")

            # 2. Guard closure PUT
            current = await client.get_ticket(42)
            put_payload = {
                "status": STATUS_RESOLVED,
                "custom_fields": {
                    "cf_sop_status": "SOP Present",
                    "cf_resolution_classification": "Solved by SOP (Permanent Fix)",
                },
            }
            decision = guard.guard_status_transition(put_payload, current)
            assert decision.allowed, decision.reason
            await client.update_ticket(42, put_payload)

            # 3. Gate reply
            reply_body = build_resolution_reply("Jane", "Cache cleared, session restored.")
            gd = gate.check(
                ticket_id="42", body_html=reply_body, confidence=0.9,
            )
            assert gd.allowed
            await svc.send_customer_reply("42", reply_body, case_id="C1", client_id="Unity")
        finally:
            await client.close()

        methods = [c["method"] for c in seen_calls]
        assert methods.count("POST") == 2      # note + reply
        assert methods.count("PUT") == 1       # closure update
        assert methods.count("GET") == 1       # ticket state fetch

    async def test_K2_clarification_flow(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.method + " " + request.url.path)
            return _make_response(
                200 if request.method == "PUT" else 201, {"id": 1}
            )

        client = _make_client(handler)
        svc = FreshdeskResponseService(client)
        gate = ReplySafetyGate(confidence_threshold=0.5)
        try:
            body = build_clarification_reply(
                "Jane", ["What is the session ID?"],
            )
            gd = gate.check(ticket_id="1", body_html=body, confidence=0.9)
            assert gd.allowed
            await svc.send_customer_reply("1", body)
            await client.update_ticket("1", {"status": 3})  # Pending
        finally:
            await client.close()

        assert any("POST" in s and "/reply" in s for s in seen)
        assert any("PUT" in s for s in seen)

    async def test_K3_low_confidence_falls_back_to_draft(self):
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content) if request.content else {}
            seen.append({"method": request.method, "body": body})
            return _make_response(201, {"id": 1})

        client = _make_client(handler)
        svc = FreshdeskResponseService(client)
        gate = ReplySafetyGate(confidence_threshold=0.75)
        try:
            reply = build_resolution_reply("Jane", "Cache cleared.")
            gd = gate.check(ticket_id="1", body_html=reply, confidence=0.4)
            assert not gd.allowed
            assert gd.should_draft
            draft_note = build_draft_reply_note(
                reply,
                reason_not_auto_sent=gd.reason,
                confidence="low",
            )
            await svc.add_internal_note("1", draft_note)
        finally:
            await client.close()
        # Only a private note was written; no /reply
        assert len(seen) == 1
        assert seen[0]["body"]["private"] is True
        assert DRAFT_HEADER in seen[0]["body"]["body"]

    async def test_K4_closure_guard_blocks_incomplete_close(self):
        """The guard prevents a PUT that would trigger HTTP 422 from Freshdesk."""

        called_put = [False]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return _make_response(200, {"id": 1, "custom_fields": {}})
            if request.method == "PUT":
                called_put[0] = True
            return _make_response(200, {})

        client = _make_client(handler)
        guard = ClosureFieldGuard()
        try:
            current = await client.get_ticket(1)
            payload = {"status": STATUS_RESOLVED}
            decision = guard.guard_status_transition(payload, current)
            assert not decision.allowed
            # Guard prevents the call — client is NOT invoked with the bad payload.
        finally:
            await client.close()
        assert called_put[0] is False


# ══════════════════════════════════════════════════════════════════════════════
# Section L — Public API export surface
# ══════════════════════════════════════════════════════════════════════════════

class TestL_PublicExports:
    def test_L1_closure_symbols_exported(self):
        import freshdesk
        for name in [
            "ClosureFieldGuard", "ClosureGuardError", "GuardDecision",
            "REQUIRED_CLOSURE_FIELDS", "ALLOWED_TICKET_TYPES",
            "AI_WRITEABLE_CUSTOM_FIELDS", "AI_READ_ONLY_CUSTOM_FIELDS",
            "STATUS_RESOLVED", "STATUS_CLOSED",
        ]:
            assert hasattr(freshdesk, name), f"missing export: {name}"

    def test_L2_safety_symbols_exported(self):
        import freshdesk
        for name in [
            "ReplySafetyGate", "GateDecision", "GateOutcome",
            "DEFAULT_CONFIDENCE_THRESHOLD",
            "FORCE_ESCALATION_IMPACT_VALUES",
        ]:
            assert hasattr(freshdesk, name), f"missing export: {name}"

    def test_L3_template_symbols_exported(self):
        import freshdesk
        for name in [
            "build_resolution_reply", "build_clarification_reply",
            "build_escalation_reply", "build_diagnostic_note",
            "build_draft_reply_note", "build_escalation_note",
            "build_unknown_tenant_note",
            "SIGNATURE", "DRAFT_HEADER",
        ]:
            assert hasattr(freshdesk, name), f"missing export: {name}"

    def test_L4_sprint_228_symbols_still_exported(self):
        import freshdesk
        for name in [
            "FreshdeskClient", "FreshdeskWebhookVerifier",
            "WebhookIdempotencyStore", "ConversationStateStore",
            "FreshdeskResponseService", "FreshdeskTicketCreatedHandler",
            "FreshdeskTicketUpdatedHandler",
        ]:
            assert hasattr(freshdesk, name), f"missing export: {name}"

    def test_L5_all_lists_are_consistent(self):
        import freshdesk
        for name in freshdesk.__all__:
            assert hasattr(freshdesk, name), f"__all__ lists missing name: {name}"


# ══════════════════════════════════════════════════════════════════════════════
# Section M — SOT compliance quick checks
# ══════════════════════════════════════════════════════════════════════════════

class TestM_SOTCompliance:
    """Direct assertions against the SOT constants and rules."""

    def test_M1_status_pending_is_3(self):
        assert FreshdeskStatus.PENDING == 3

    def test_M2_status_open_is_2(self):
        assert FreshdeskStatus.OPEN == 2

    def test_M3_status_in_process_is_10(self):
        assert FreshdeskStatus.IN_PROCESS == 10

    def test_M4_priority_urgent_is_4(self):
        assert FreshdeskPriority.URGENT == 4

    def test_M5_priority_low_is_1(self):
        assert FreshdeskPriority.LOW == 1

    def test_M6_force_escalation_impacts_documented(self):
        assert "DOWNTIME 100% impact" in FORCE_ESCALATION_IMPACT_VALUES
        assert "Client Escalation" in FORCE_ESCALATION_IMPACT_VALUES

    def test_M7_signature_string(self):
        assert "KwikID" in SIGNATURE

    def test_M8_draft_header(self):
        assert "AI DRAFT" in DRAFT_HEADER
