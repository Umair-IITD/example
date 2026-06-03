"""
tests/test_sprint21_action_models.py

Sprint 2.1: ActionRequest, ActionTransitionRecord, ActionProposal model tests.

Covers:
- ActionRequest.to_db_row() / from_db_row() round-trip fidelity
- Idempotency key: determinism (same inputs → same key)
- Idempotency key: sensitivity (any field change → different key)
- ActionProposal.validate() accepts valid proposals
- ActionProposal.validate() rejects self-inconsistent proposals
- ActionRequest derived properties: is_terminal, can_retry, is_approval_overdue
- ActionTransitionRecord.to_db_row() serialization
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from case_engine.action_models import (
    APPROVAL_DEADLINE_IRREVERSIBLE,
    APPROVAL_DEADLINE_REVERSIBLE,
    ActionProposal,
    ActionRequest,
    ActionTransitionRecord,
    compute_idempotency_key,
)
from case_engine.action_state import ActionRiskLevel, ActionState


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _minimal_action(**overrides) -> ActionRequest:
    defaults = dict(
        case_id="11111111-1111-1111-1111-111111111111",
        ticket_id="TKT-001",
        client="unity_bank",
        action_type="reset_otp",
        action_namespace="identity",
        risk_level=ActionRiskLevel.SAFE,
        idempotency_key="deadbeef",
    )
    defaults.update(overrides)
    return ActionRequest(**defaults)


def _full_action() -> ActionRequest:
    now = _now()
    return ActionRequest(
        case_id="11111111-1111-1111-1111-111111111111",
        ticket_id="TKT-999",
        client="alpha_bank",
        action_type="freeze_account",
        action_namespace="accounts",
        risk_level=ActionRiskLevel.IRREVERSIBLE,
        current_state=ActionState.EXECUTING,
        proposed_by="agent_007",
        proposed_at=now,
        action_payload={"account_id": "ACC-123", "reason": "fraud"},
        rollback_action_type=None,
        rollback_params=None,
        approval_required=True,
        expires_at=now + timedelta(hours=24),
        approver="senior_agent",
        approved_at=now + timedelta(minutes=30),
        approval_notes="Confirmed fraud signal",
        executor_id="worker-42",
        execution_started_at=now + timedelta(minutes=45),
        execution_completed_at=None,
        execution_attempt=1,
        max_attempts=1,
        execution_result=None,
        failure_code=None,
        failure_reason=None,
        idempotency_key="abc" * 20,
        is_rolled_back=False,
        rollback_action_id=None,
        created_at=now,
        updated_at=now,
    )


# ── Idempotency key ───────────────────────────────────────────────────────────


class TestIdempotencyKey:
    """compute_idempotency_key must be deterministic and sensitive to all inputs."""

    def _key(self, case_id="c1", action_type="reset_otp",
             action_namespace="identity", action_params=None) -> str:
        return compute_idempotency_key(
            case_id=case_id,
            action_type=action_type,
            action_namespace=action_namespace,
            action_params=action_params or {},
        )

    def test_returns_hex_string(self):
        key = self._key()
        assert isinstance(key, str)
        assert len(key) == 64
        int(key, 16)  # must be valid hex

    def test_deterministic_same_inputs(self):
        assert self._key() == self._key()

    def test_deterministic_with_params(self):
        params = {"amount": 100, "currency": "INR"}
        k1 = self._key(action_params=params)
        k2 = self._key(action_params=params)
        assert k1 == k2

    def test_dict_ordering_does_not_affect_key(self):
        k1 = self._key(action_params={"a": 1, "b": 2})
        k2 = self._key(action_params={"b": 2, "a": 1})
        assert k1 == k2

    def test_sensitive_to_case_id(self):
        assert self._key(case_id="c1") != self._key(case_id="c2")

    def test_sensitive_to_action_type(self):
        assert self._key(action_type="reset_otp") != self._key(action_type="freeze_account")

    def test_sensitive_to_action_namespace(self):
        assert self._key(action_namespace="identity") != self._key(action_namespace="accounts")

    def test_sensitive_to_params(self):
        k1 = self._key(action_params={"account_id": "ACC-1"})
        k2 = self._key(action_params={"account_id": "ACC-2"})
        assert k1 != k2

    def test_sensitive_to_extra_param(self):
        k1 = self._key(action_params={"a": 1})
        k2 = self._key(action_params={"a": 1, "b": 2})
        assert k1 != k2

    def test_matches_manual_sha256(self):
        payload = {
            "case_id": "c1",
            "action_type": "reset_otp",
            "action_namespace": "identity",
            "params": {},
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert self._key() == expected


# ── ActionRequest serialization ───────────────────────────────────────────────


class TestActionRequestSerialization:
    """to_db_row / from_db_row must be a lossless round-trip."""

    def test_minimal_round_trip(self):
        action = _minimal_action()
        row = action.to_db_row()
        restored = ActionRequest.from_db_row(row)
        assert restored.action_id == action.action_id
        assert restored.case_id == action.case_id
        assert restored.ticket_id == action.ticket_id
        assert restored.client == action.client
        assert restored.action_type == action.action_type
        assert restored.action_namespace == action.action_namespace
        assert restored.risk_level == action.risk_level
        assert restored.current_state == action.current_state
        assert restored.idempotency_key == action.idempotency_key

    def test_full_round_trip(self):
        action = _full_action()
        restored = ActionRequest.from_db_row(action.to_db_row())
        assert restored.execution_attempt == action.execution_attempt
        assert restored.max_attempts == action.max_attempts
        assert restored.approver == action.approver
        assert restored.approval_notes == action.approval_notes
        assert restored.executor_id == action.executor_id
        assert restored.action_payload == action.action_payload
        assert restored.is_rolled_back == action.is_rolled_back

    def test_to_db_row_risk_level_is_string(self):
        action = _minimal_action(risk_level=ActionRiskLevel.REVERSIBLE)
        row = action.to_db_row()
        assert row["risk_level"] == "REVERSIBLE"

    def test_to_db_row_current_state_is_string(self):
        action = _minimal_action(current_state=ActionState.AWAITING_APPROVAL)
        row = action.to_db_row()
        assert row["current_state"] == "AWAITING_APPROVAL"

    def test_to_db_row_datetimes_are_iso_strings(self):
        action = _minimal_action()
        row = action.to_db_row()
        # proposed_at defaults to _now() — must be an ISO string or None
        assert isinstance(row["proposed_at"], str)
        # Round-trip: parseable
        datetime.fromisoformat(row["proposed_at"])

    def test_to_db_row_none_timestamps_are_none(self):
        action = _minimal_action()
        row = action.to_db_row()
        assert row["expires_at"] is None
        assert row["execution_started_at"] is None
        assert row["execution_completed_at"] is None

    def test_from_db_row_restores_risk_level_enum(self):
        row = _minimal_action().to_db_row()
        row["risk_level"] = "IRREVERSIBLE"
        restored = ActionRequest.from_db_row(row)
        assert restored.risk_level == ActionRiskLevel.IRREVERSIBLE

    def test_from_db_row_restores_state_enum(self):
        row = _minimal_action().to_db_row()
        row["current_state"] = "FAILED"
        restored = ActionRequest.from_db_row(row)
        assert restored.current_state == ActionState.FAILED

    def test_from_db_row_handles_missing_optional_fields(self):
        row = _minimal_action().to_db_row()
        del row["approval_notes"]
        del row["rollback_action_id"]
        restored = ActionRequest.from_db_row(row)
        assert restored.approval_notes is None
        assert restored.rollback_action_id is None

    def test_action_params_preserved(self):
        params = {"account_id": "ACC-789", "channel": "mobile", "amount": 5000}
        action = _minimal_action(action_payload=params)
        restored = ActionRequest.from_db_row(action.to_db_row())
        assert restored.action_payload == params

    def test_action_id_is_valid_uuid(self):
        action = ActionRequest(
            case_id="11111111-1111-1111-1111-111111111111",
            ticket_id="T1", client="c1",
            action_type="a", action_namespace="n",
            risk_level=ActionRiskLevel.SAFE,
            idempotency_key="k1",
        )
        uuid.UUID(action.action_id)  # raises if not valid


# ── ActionRequest derived properties ──────────────────────────────────────────


class TestActionRequestProperties:

    def test_is_terminal_true_for_rejected(self):
        assert _minimal_action(current_state=ActionState.REJECTED).is_terminal

    def test_is_terminal_true_for_expired(self):
        assert _minimal_action(current_state=ActionState.EXPIRED).is_terminal

    def test_is_terminal_true_for_rolled_back(self):
        assert _minimal_action(current_state=ActionState.ROLLED_BACK).is_terminal

    def test_is_terminal_true_for_rollback_failed(self):
        assert _minimal_action(current_state=ActionState.ROLLBACK_FAILED).is_terminal

    def test_is_terminal_false_for_executed(self):
        assert not _minimal_action(current_state=ActionState.EXECUTED).is_terminal

    def test_is_terminal_false_for_proposed(self):
        assert not _minimal_action(current_state=ActionState.PROPOSED).is_terminal

    def test_can_retry_true_when_failed_and_attempts_remain(self):
        action = _minimal_action(
            current_state=ActionState.FAILED,
            execution_attempt=1,
            max_attempts=3,
        )
        assert action.can_retry

    def test_can_retry_false_when_exhausted(self):
        action = _minimal_action(
            current_state=ActionState.FAILED,
            execution_attempt=3,
            max_attempts=3,
        )
        assert not action.can_retry

    def test_can_retry_false_when_not_failed(self):
        action = _minimal_action(
            current_state=ActionState.EXECUTING,
            execution_attempt=1,
            max_attempts=3,
        )
        assert not action.can_retry

    def test_is_approval_overdue_true_past_deadline(self):
        past = _now() - timedelta(hours=1)
        action = _minimal_action(
            current_state=ActionState.AWAITING_APPROVAL,
            expires_at=past,
        )
        assert action.is_approval_overdue

    def test_is_approval_overdue_false_before_deadline(self):
        future = _now() + timedelta(hours=4)
        action = _minimal_action(
            current_state=ActionState.AWAITING_APPROVAL,
            expires_at=future,
        )
        assert not action.is_approval_overdue

    def test_is_approval_overdue_false_when_no_deadline(self):
        action = _minimal_action(
            current_state=ActionState.AWAITING_APPROVAL,
            expires_at=None,
        )
        assert not action.is_approval_overdue

    def test_is_approval_overdue_false_when_not_awaiting(self):
        past = _now() - timedelta(hours=1)
        action = _minimal_action(
            current_state=ActionState.APPROVED,
            expires_at=past,
        )
        assert not action.is_approval_overdue


# ── ActionTransitionRecord ────────────────────────────────────────────────────


class TestActionTransitionRecord:

    def test_to_db_row_fields(self):
        rec = ActionTransitionRecord(
            action_id="aaaa-bbbb",
            case_id="cccc-dddd",
            ticket_id="TKT-1",
            client="unity_bank",
            from_state=ActionState.PROPOSED,
            to_state=ActionState.APPROVED,
            actor="auto_approval",
            reason="auto_approved_safe",
            detail={"risk_level": "SAFE"},
        )
        row = rec.to_db_row()
        assert row["action_id"] == "aaaa-bbbb"
        assert row["case_id"] == "cccc-dddd"
        assert row["from_state"] == "PROPOSED"
        assert row["to_state"] == "APPROVED"
        assert row["actor"] == "auto_approval"
        assert row["reason"] == "auto_approved_safe"
        assert row["detail"] == {"risk_level": "SAFE"}
        assert isinstance(row["created_at"], str)

    def test_log_id_is_auto_generated(self):
        rec = ActionTransitionRecord()
        uuid.UUID(rec.transition_id)  # valid UUID


# ── ActionProposal.validate() ─────────────────────────────────────────────────


class TestActionProposalValidate:

    def test_safe_minimal_valid(self):
        p = ActionProposal(
            action_type="send_otp",
            action_namespace="identity",
            risk_level=ActionRiskLevel.SAFE,
        )
        p.validate()  # must not raise

    def test_reversible_with_rollback_type_valid(self):
        p = ActionProposal(
            action_type="update_address",
            action_namespace="profile",
            risk_level=ActionRiskLevel.REVERSIBLE,
            rollback_action_type="restore_address",
            rollback_params={"old_address": "123 Main St"},
        )
        p.validate()

    def test_irreversible_without_rollback_valid(self):
        p = ActionProposal(
            action_type="close_account",
            action_namespace="accounts",
            risk_level=ActionRiskLevel.IRREVERSIBLE,
        )
        p.validate()

    def test_empty_action_type_raises(self):
        p = ActionProposal(
            action_type="",
            action_namespace="identity",
            risk_level=ActionRiskLevel.SAFE,
        )
        with pytest.raises(ValueError, match="action_type"):
            p.validate()

    def test_whitespace_action_type_raises(self):
        p = ActionProposal(
            action_type="   ",
            action_namespace="identity",
            risk_level=ActionRiskLevel.SAFE,
        )
        with pytest.raises(ValueError, match="action_type"):
            p.validate()

    def test_empty_action_namespace_raises(self):
        p = ActionProposal(
            action_type="reset_otp",
            action_namespace="",
            risk_level=ActionRiskLevel.SAFE,
        )
        with pytest.raises(ValueError, match="action_namespace"):
            p.validate()

    def test_reversible_without_rollback_type_raises(self):
        p = ActionProposal(
            action_type="update_phone",
            action_namespace="profile",
            risk_level=ActionRiskLevel.REVERSIBLE,
            rollback_action_type=None,
        )
        with pytest.raises(ValueError, match="rollback_action_type"):
            p.validate()

    def test_irreversible_with_rollback_type_raises(self):
        p = ActionProposal(
            action_type="delete_account",
            action_namespace="accounts",
            risk_level=ActionRiskLevel.IRREVERSIBLE,
            rollback_action_type="restore_account",  # must NOT be set for IRREVERSIBLE
        )
        with pytest.raises(ValueError, match="rollback_action_type"):
            p.validate()

    def test_safe_with_rollback_type_is_allowed(self):
        # SAFE does not mandate rollback, but may have one
        p = ActionProposal(
            action_type="send_notification",
            action_namespace="comms",
            risk_level=ActionRiskLevel.SAFE,
            rollback_action_type="cancel_notification",
        )
        p.validate()  # must not raise


# ── Approval deadline constants ───────────────────────────────────────────────


class TestApprovalDeadlineConstants:
    def test_reversible_deadline_is_4_hours(self):
        assert APPROVAL_DEADLINE_REVERSIBLE == timedelta(hours=4)

    def test_irreversible_deadline_is_24_hours(self):
        assert APPROVAL_DEADLINE_IRREVERSIBLE == timedelta(hours=24)
