"""
tests/test_sprint12_schema_consistency.py

Sprint 1.2 Schema Consistency Tests.

Verifies the canonical type policy across the model layer:
  Internal system IDs  (case_id, transition_id, audit_id) → valid UUID strings
  External identifiers (ticket_id, client)                 → plain TEXT, not UUID

No database connection required — all tests operate on in-memory model instances.
"""
from __future__ import annotations

import uuid

import pytest

from case_engine.models import AuditEntry, AuditEventType, Case, CaseTransition
from case_engine.service import build_case_service


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


# ── Case model ────────────────────────────────────────────────────────────────

class TestCaseUUID:
    def test_case_id_is_valid_uuid(self):
        case = Case(ticket_id="TKT-001", client="unity_bank")
        assert _is_valid_uuid(case.case_id)

    def test_case_id_is_string_type(self):
        case = Case(ticket_id="TKT-001", client="unity_bank")
        assert isinstance(case.case_id, str)

    def test_each_case_gets_unique_case_id(self):
        c1 = Case(ticket_id="TKT-001", client="unity_bank")
        c2 = Case(ticket_id="TKT-002", client="unity_bank")
        assert c1.case_id != c2.case_id

    def test_ticket_id_is_not_uuid(self):
        case = Case(ticket_id="TKT-001", client="unity_bank")
        assert not _is_valid_uuid(case.ticket_id)

    def test_numeric_freshdesk_ticket_id_preserved_as_text(self):
        # Freshdesk ticket IDs are often numeric strings like "12345678"
        case = Case(ticket_id="12345678", client="unity_bank")
        assert case.ticket_id == "12345678"
        assert not _is_valid_uuid(case.ticket_id)

    def test_client_is_not_uuid(self):
        case = Case(ticket_id="TKT-001", client="unity_bank")
        assert not _is_valid_uuid(case.client)

    def test_case_id_and_ticket_id_are_different_fields(self):
        case = Case(ticket_id="TKT-001", client="unity_bank")
        assert case.case_id != case.ticket_id

    def test_to_db_row_case_id_is_valid_uuid(self):
        case = Case(ticket_id="TKT-001", client="unity_bank")
        row = case.to_db_row()
        assert _is_valid_uuid(row["case_id"])

    def test_to_db_row_ticket_id_is_plain_text(self):
        case = Case(ticket_id="TKT-EXTERNAL-999", client="unity_bank")
        row = case.to_db_row()
        assert row["ticket_id"] == "TKT-EXTERNAL-999"
        assert not _is_valid_uuid(row["ticket_id"])

    def test_to_db_row_has_all_required_columns(self):
        case = Case(ticket_id="TKT-001", client="unity_bank")
        row = case.to_db_row()
        required = {"case_id", "ticket_id", "client", "current_state",
                    "created_at", "updated_at", "closed_at", "sla_breach_at"}
        assert required.issubset(row.keys())

    def test_from_db_row_round_trip_preserves_case_id(self):
        original = Case(ticket_id="TKT-001", client="unity_bank")
        restored = Case.from_db_row(original.to_db_row())
        assert restored.case_id == original.case_id
        assert _is_valid_uuid(restored.case_id)

    def test_from_db_row_preserves_ticket_id(self):
        original = Case(ticket_id="TKT-EXTERNAL-999", client="axis_bank")
        restored = Case.from_db_row(original.to_db_row())
        assert restored.ticket_id == "TKT-EXTERNAL-999"

    def test_from_db_row_accepts_uuid_string_from_db(self):
        # supabase-py returns UUID columns as plain strings — ensure from_db_row handles them
        raw_uuid = str(uuid.uuid4())
        row = {
            "case_id":       raw_uuid,
            "ticket_id":     "TKT-001",
            "client":        "unity_bank",
            "current_state": "NEW",
            "created_at":    "2026-06-01T00:00:00+00:00",
            "updated_at":    "2026-06-01T00:00:00+00:00",
        }
        case = Case.from_db_row(row)
        assert case.case_id == raw_uuid
        assert _is_valid_uuid(case.case_id)


# ── CaseTransition model ──────────────────────────────────────────────────────

class TestCaseTransitionUUID:
    def test_transition_id_is_valid_uuid(self):
        t = CaseTransition(case_id=str(uuid.uuid4()))
        assert _is_valid_uuid(t.transition_id)

    def test_case_id_on_transition_is_valid_uuid(self):
        parent_id = str(uuid.uuid4())
        t = CaseTransition(case_id=parent_id)
        assert _is_valid_uuid(t.case_id)

    def test_each_transition_gets_unique_id(self):
        parent_id = str(uuid.uuid4())
        t1 = CaseTransition(case_id=parent_id)
        t2 = CaseTransition(case_id=parent_id)
        assert t1.transition_id != t2.transition_id

    def test_to_db_row_transition_id_is_valid_uuid(self):
        t = CaseTransition(case_id=str(uuid.uuid4()))
        row = t.to_db_row()
        assert _is_valid_uuid(row["transition_id"])

    def test_to_db_row_case_id_matches_parent(self):
        parent_id = str(uuid.uuid4())
        t = CaseTransition(case_id=parent_id)
        row = t.to_db_row()
        assert row["case_id"] == parent_id
        assert _is_valid_uuid(row["case_id"])


# ── AuditEntry model ──────────────────────────────────────────────────────────

class TestAuditEntryUUID:
    def test_audit_id_is_valid_uuid(self):
        entry = AuditEntry(
            case_id=str(uuid.uuid4()),
            ticket_id="TKT-001",
            client="unity_bank",
            action_type=AuditEventType.RAG_CALLED,
        )
        assert _is_valid_uuid(entry.audit_id)

    def test_each_entry_gets_unique_audit_id(self):
        cid = str(uuid.uuid4())
        e1 = AuditEntry(case_id=cid, ticket_id="TKT-001", client="c")
        e2 = AuditEntry(case_id=cid, ticket_id="TKT-001", client="c")
        assert e1.audit_id != e2.audit_id

    def test_case_id_on_audit_entry_is_valid_uuid(self):
        cid = str(uuid.uuid4())
        entry = AuditEntry(case_id=cid, ticket_id="TKT-001", client="unity_bank")
        assert _is_valid_uuid(entry.case_id)

    def test_ticket_id_in_audit_entry_is_plain_text(self):
        entry = AuditEntry(
            case_id=str(uuid.uuid4()),
            ticket_id="TKT-EXTERNAL-123",
            client="unity_bank",
        )
        assert entry.ticket_id == "TKT-EXTERNAL-123"
        assert not _is_valid_uuid(entry.ticket_id)

    def test_action_detail_defaults_to_empty_dict(self):
        entry = AuditEntry(
            case_id=str(uuid.uuid4()),
            ticket_id="TKT-001",
            client="unity_bank",
        )
        assert entry.action_detail == {}

    def test_to_db_row_audit_id_is_valid_uuid(self):
        entry = AuditEntry(
            case_id=str(uuid.uuid4()),
            ticket_id="TKT-001",
            client="unity_bank",
            action_type=AuditEventType.RAG_CALLED,
        )
        row = entry.to_db_row()
        assert _is_valid_uuid(row["audit_id"])

    def test_to_db_row_case_id_is_valid_uuid(self):
        entry = AuditEntry(
            case_id=str(uuid.uuid4()),
            ticket_id="TKT-001",
            client="unity_bank",
        )
        row = entry.to_db_row()
        assert _is_valid_uuid(row["case_id"])

    def test_to_db_row_ticket_id_is_plain_text(self):
        entry = AuditEntry(
            case_id=str(uuid.uuid4()),
            ticket_id="TKT-001",
            client="unity_bank",
        )
        row = entry.to_db_row()
        assert row["ticket_id"] == "TKT-001"

    def test_to_db_row_action_detail_is_dict_not_none(self):
        entry = AuditEntry(
            case_id=str(uuid.uuid4()),
            ticket_id="TKT-001",
            client="unity_bank",
        )
        row = entry.to_db_row()
        assert row["action_detail"] is not None
        assert isinstance(row["action_detail"], dict)


# ── Cross-cutting canonical type policy ───────────────────────────────────────

class TestCanonicalTypePolicy:
    """End-to-end assertions: UUID for system IDs, TEXT for external IDs, at all layers."""

    def test_case_service_produces_valid_uuid_case_id(self):
        svc = build_case_service(supabase_client=None)
        case = svc.open_case("TKT-999", "unity_bank")
        assert _is_valid_uuid(case.case_id)

    def test_case_service_case_id_differs_from_ticket_id(self):
        svc = build_case_service(supabase_client=None)
        case = svc.open_case("TKT-001", "unity_bank")
        assert case.case_id != case.ticket_id

    def test_case_service_ticket_id_is_not_uuid(self):
        svc = build_case_service(supabase_client=None)
        case = svc.open_case("TKT-001", "unity_bank")
        assert not _is_valid_uuid(case.ticket_id)

    def test_uuid_format_version_4(self):
        case = Case(ticket_id="TKT-001", client="unity_bank")
        parsed = uuid.UUID(case.case_id)
        assert parsed.version == 4

    def test_two_cases_same_ticket_get_different_case_ids(self):
        # Simulates retry: same ticket creates a new in-memory case each time
        c1 = Case(ticket_id="TKT-001", client="unity_bank")
        c2 = Case(ticket_id="TKT-001", client="unity_bank")
        assert c1.case_id != c2.case_id
        assert _is_valid_uuid(c1.case_id)
        assert _is_valid_uuid(c2.case_id)
