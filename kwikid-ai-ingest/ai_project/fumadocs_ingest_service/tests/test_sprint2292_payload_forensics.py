"""
tests/test_sprint2292_payload_forensics.py

Sprint 2.29.2 PART 1: Payload forensics — prove FreshdeskTicketPayload.from_dict
handles every Dispatch'r template field-name variant.

Root cause documented here:
  Freshdesk Dispatch'r webhooks use a free-form JSON template.  Depending on
  how the rule was configured, the ticket ID may arrive as "id" (canonical) or
  "ticket_id" (Dispatch'r template variable name).  The original from_dict used
  only d.get("id", 0), producing ticket_id=0 and MISSING_TICKET_ID failures.

Variants tested:
  A. Canonical: id=197416, requester_email top-level, ticket_custom_fields
  B. ticket_id key variant
  C. Nested requester object: {"requester": {"email": "...", "name": "..."}}
  D. custom_fields (vs ticket_custom_fields)
  E. ticket_cf (third variant)
  F. Alternative subject/description keys
  G. ticket_created_at timestamp key
  H. Mixed: multiple alternatives in one payload
  I. FreshdeskUpdateEvent variants (updated_at / ticket_updated_at / ticket_id)
  J. Edge cases: empty id=0 rejected, string id parsed correctly
"""
from __future__ import annotations

import os

os.environ.setdefault("RAG_API_KEY", "test-sprint2292-forensics")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")

from freshdesk.freshdesk_models import (
    FreshdeskTicketPayload,
    FreshdeskWebhookPayload,
    FreshdeskUpdateEvent,
)


# ── Helper ────────────────────────────────────────────────────────────────────

def _wrap(inner: dict) -> dict:
    return {"freshdesk_webhook": inner}


# ── Variant A: Canonical format ───────────────────────────────────────────────

class TestCanonicalFormat:
    def test_id_field_extracted(self):
        p = FreshdeskTicketPayload.from_dict({
            "id": 197416,
            "subject": "Login issue",
            "requester_email": "agent@unitybank.co.in",
            "ticket_custom_fields": {"cf_clients": "Unity"},
            "created_at": "2026-06-25T10:00:00Z",
        })
        assert p.ticket_id == "197416"
        assert p.subject == "Login issue"
        assert p.requester_email == "agent@unitybank.co.in"
        assert p.custom_fields.cf_clients == "Unity"
        assert p.created_at == "2026-06-25T10:00:00Z"

    def test_string_id_converted_to_int(self):
        p = FreshdeskTicketPayload.from_dict({"id": "197416", "subject": "X"})
        assert p.ticket_id == "197416"

    def test_top_level_wrapper_handled(self):
        payload = FreshdeskWebhookPayload.from_dict(_wrap({
            "id": 100,
            "subject": "Test",
            "requester_email": "x@y.com",
        }))
        assert payload.ticket.ticket_id == "100"


# ── Variant B: ticket_id key ──────────────────────────────────────────────────

class TestTicketIdKeyVariant:
    def test_ticket_id_key_extracted(self):
        p = FreshdeskTicketPayload.from_dict({
            "ticket_id": 197416,
            "subject": "Login issue",
            "requester_email": "agent@unitybank.co.in",
        })
        assert p.ticket_id == "197416"

    def test_ticket_id_string(self):
        p = FreshdeskTicketPayload.from_dict({"ticket_id": "99999"})
        assert p.ticket_id == "99999"

    def test_id_takes_precedence_over_ticket_id(self):
        p = FreshdeskTicketPayload.from_dict({"id": 111, "ticket_id": 999})
        assert p.ticket_id == "111"

    def test_wrapped_webhook_with_ticket_id(self):
        payload = FreshdeskWebhookPayload.from_dict(_wrap({
            "ticket_id": 197416,
            "subject": "Via ticket_id key",
        }))
        assert payload.ticket.ticket_id == "197416"


# ── Variant C: Nested requester object ───────────────────────────────────────

class TestNestedRequesterVariant:
    def test_nested_requester_email(self):
        p = FreshdeskTicketPayload.from_dict({
            "id": 200,
            "requester": {"email": "user@corp.com", "name": "User Name"},
        })
        assert p.requester_email == "user@corp.com"
        assert p.requester_name == "User Name"

    def test_top_level_requester_email_takes_precedence(self):
        p = FreshdeskTicketPayload.from_dict({
            "id": 200,
            "requester_email": "direct@corp.com",
            "requester": {"email": "nested@corp.com"},
        })
        assert p.requester_email == "direct@corp.com"

    def test_nested_requester_name_fallback(self):
        p = FreshdeskTicketPayload.from_dict({
            "id": 200,
            "requester": {"name": "Nested Name"},
        })
        assert p.requester_name == "Nested Name"


# ── Variant D: custom_fields key ─────────────────────────────────────────────

class TestCustomFieldsKeyVariant:
    def test_custom_fields_key(self):
        p = FreshdeskTicketPayload.from_dict({
            "id": 300,
            "custom_fields": {"cf_clients": "Unity", "cf_environment": "prod"},
        })
        assert p.custom_fields.cf_clients == "Unity"
        assert p.custom_fields.cf_environment == "prod"

    def test_ticket_custom_fields_canonical(self):
        p = FreshdeskTicketPayload.from_dict({
            "id": 300,
            "ticket_custom_fields": {"cf_clients": "HDFC"},
        })
        assert p.custom_fields.cf_clients == "HDFC"

    def test_ticket_custom_fields_takes_precedence(self):
        p = FreshdeskTicketPayload.from_dict({
            "id": 300,
            "ticket_custom_fields": {"cf_clients": "preferred"},
            "custom_fields": {"cf_clients": "fallback"},
        })
        assert p.custom_fields.cf_clients == "preferred"


# ── Variant E: ticket_cf key ─────────────────────────────────────────────────

class TestTicketCfKeyVariant:
    def test_ticket_cf_key(self):
        p = FreshdeskTicketPayload.from_dict({
            "id": 400,
            "ticket_cf": {"cf_clients": "AxisBank"},
        })
        assert p.custom_fields.cf_clients == "AxisBank"


# ── Variant F: Alternative subject / description keys ────────────────────────

class TestAlternativeSubjectDescription:
    def test_ticket_subject_key(self):
        p = FreshdeskTicketPayload.from_dict({
            "id": 500,
            "ticket_subject": "From ticket_subject key",
        })
        assert p.subject == "From ticket_subject key"

    def test_ticket_description_key(self):
        p = FreshdeskTicketPayload.from_dict({
            "id": 500,
            "ticket_description": "From ticket_description key",
            "ticket_description_text": "Plain text version",
        })
        assert p.description == "From ticket_description key"
        assert p.description_text == "Plain text version"

    def test_canonical_subject_takes_precedence(self):
        p = FreshdeskTicketPayload.from_dict({
            "id": 500,
            "subject": "canonical",
            "ticket_subject": "alternative",
        })
        assert p.subject == "canonical"


# ── Variant G: ticket_created_at timestamp key ───────────────────────────────

class TestTimestampKeyVariant:
    def test_ticket_created_at_key(self):
        p = FreshdeskTicketPayload.from_dict({
            "id": 600,
            "ticket_created_at": "2026-06-25T09:00:00Z",
        })
        assert p.created_at == "2026-06-25T09:00:00Z"

    def test_created_at_takes_precedence(self):
        p = FreshdeskTicketPayload.from_dict({
            "id": 600,
            "created_at": "2026-06-25T08:00:00Z",
            "ticket_created_at": "2026-06-25T09:00:00Z",
        })
        assert p.created_at == "2026-06-25T08:00:00Z"


# ── Variant H: Mixed alternatives in one payload ──────────────────────────────

class TestMixedVariantsPayload:
    def test_all_alternatives_in_one_payload(self):
        p = FreshdeskTicketPayload.from_dict({
            "ticket_id": 197416,              # uses ticket_id
            "ticket_subject": "Mixed test",   # uses ticket_subject
            "ticket_description": "Desc",     # uses ticket_description
            "ticket_description_text": "Plain",
            "ticket_created_at": "2026-06-25T10:00:00Z",
            "requester": {                    # nested requester
                "email": "mixed@bank.com",
                "name": "Mixed User",
            },
            "custom_fields": {"cf_clients": "MixedBank"},   # uses custom_fields
            "status": 2,
            "priority": 2,
        })
        assert p.ticket_id == "197416"
        assert p.subject == "Mixed test"
        assert p.description == "Desc"
        assert p.description_text == "Plain"
        assert p.created_at == "2026-06-25T10:00:00Z"
        assert p.requester_email == "mixed@bank.com"
        assert p.requester_name == "Mixed User"
        assert p.custom_fields.cf_clients == "MixedBank"

    def test_full_wrapped_mixed_payload(self):
        payload = FreshdeskWebhookPayload.from_dict(_wrap({
            "ticket_id": 99000,
            "ticket_subject": "Wrapped mixed",
            "requester": {"email": "req@test.com"},
            "ticket_cf": {"cf_clients": "TestCorp"},
        }))
        assert payload.ticket.ticket_id == "99000"
        assert payload.ticket.requester_email == "req@test.com"
        assert payload.ticket.custom_fields.cf_clients == "TestCorp"


# ── Variant I: FreshdeskUpdateEvent variants ──────────────────────────────────

class TestUpdateEventVariants:
    def test_canonical_id_and_updated_at(self):
        event = FreshdeskUpdateEvent.from_dict(_wrap({
            "id": 197416,
            "updated_at": "2026-06-25T11:00:00Z",
            "changes": {},
        }))
        assert event.ticket_id == 197416
        assert event.updated_at == "2026-06-25T11:00:00Z"

    def test_ticket_id_key_in_update_event(self):
        event = FreshdeskUpdateEvent.from_dict(_wrap({
            "ticket_id": 888,
            "updated_at": "2026-06-25T11:00:00Z",
        }))
        assert event.ticket_id == 888

    def test_ticket_updated_at_key(self):
        event = FreshdeskUpdateEvent.from_dict(_wrap({
            "id": 777,
            "ticket_updated_at": "2026-06-25T12:00:00Z",
        }))
        assert event.updated_at == "2026-06-25T12:00:00Z"

    def test_updated_at_takes_precedence(self):
        event = FreshdeskUpdateEvent.from_dict(_wrap({
            "id": 777,
            "updated_at": "2026-06-25T11:00:00Z",
            "ticket_updated_at": "2026-06-25T12:00:00Z",
        }))
        assert event.updated_at == "2026-06-25T11:00:00Z"

    def test_ticket_id_key_in_update_event_via_ticket(self):
        event = FreshdeskUpdateEvent.from_dict(_wrap({
            "ticket_id": "7890",
            "updated_at": "2026-06-25T13:00:00Z",
        }))
        assert event.ticket_id == 7890


# ── Variant J: Edge cases ─────────────────────────────────────────────────────

class TestEdgeCases:
    def test_missing_id_gives_zero(self):
        p = FreshdeskTicketPayload.from_dict({"subject": "No id field"})
        assert p.ticket_id == "0"

    def test_both_id_and_ticket_id_absent_gives_zero(self):
        p = FreshdeskTicketPayload.from_dict({})
        assert p.ticket_id == "0"

    def test_empty_custom_fields_gives_defaults(self):
        p = FreshdeskTicketPayload.from_dict({"id": 1})
        assert p.custom_fields.cf_clients == ""

    def test_tags_csv_string_parsed(self):
        p = FreshdeskTicketPayload.from_dict({"id": 1, "tags": "client:Unity, prod"})
        assert "client:Unity" in p.tags
        assert "prod" in p.tags

    def test_tags_list_passthrough(self):
        p = FreshdeskTicketPayload.from_dict({"id": 1, "tags": ["a", "b"]})
        assert p.tags == ["a", "b"]

    def test_type_field_as_ticket_type_fallback(self):
        p = FreshdeskTicketPayload.from_dict({"id": 1, "type": "Issue"})
        assert p.ticket_type == "Issue"

    def test_ticket_type_canonical_takes_precedence(self):
        p = FreshdeskTicketPayload.from_dict({"id": 1, "ticket_type": "Bug", "type": "Issue"})
        assert p.ticket_type == "Bug"
