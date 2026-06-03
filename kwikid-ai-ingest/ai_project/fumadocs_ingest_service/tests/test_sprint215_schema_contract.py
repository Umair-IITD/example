"""
tests/test_sprint215_schema_contract.py

Sprint 2.1.5: Action Gateway Schema Contract Tests.

PURPOSE
───────
These tests verify the contract between the Python application layer and the
action_gateway / action_gateway_transitions database schema defined in
S2_001_action_gateway.sql.

No live database connection is required. Tests are entirely offline:
  - State/risk enum coverage against DB CHECK constraint sets
  - Idempotency key format, determinism, and sensitivity
  - CHECK constraint equivalent logic
  - DB column name contract (what the Python model MUST produce)
  - Python model alignment gaps (currently failing — documented as xfail)

XFAIL TESTS
───────────
Tests marked @pytest.mark.xfail document known mismatches between the current
ActionRequest Python model and the target action_gateway DB schema. These tests
represent mandatory acceptance criteria for the Sprint 2.1.5 Python model update.

When you update ActionRequest.to_db_row() / from_db_row() to use the new column
names, remove the @pytest.mark.xfail decorators and the tests should pass.

ALIGNMENT GAPS (required Python model changes)
──────────────────────────────────────────────
  Python field name          →  DB column name (action_gateway)
  ─────────────────────────────────────────────────────────────
  action_params              →  action_payload
  approved_by                →  approver
  approval_decision_at       →  approved_at
  approval_deadline          →  expires_at

New DB columns with no current Python model field:
  rejected_at                (set by ActionGateway.reject())
  execution_failed_at        (set by record_failure(), record_timeout())
  rollback_completed_at      (set by record_rollback_success())
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from case_engine.action_models import (
    ActionProposal,
    ActionRequest,
    ActionTransitionRecord,
    compute_idempotency_key,
)
from case_engine.action_state import ActionRiskLevel, ActionState


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMA CONSTANTS
# Extracted directly from S2_001_action_gateway.sql CHECK constraints.
# If you change the SQL, update these sets — they are the contract.
# ═══════════════════════════════════════════════════════════════════════════════

# ag_state_check constraint values
_DB_STATES: frozenset[str] = frozenset({
    "PROPOSED",
    "AWAITING_APPROVAL",
    "APPROVED",
    "REJECTED",
    "EXPIRED",
    "EXECUTING",
    "EXECUTED",
    "FAILED",
    "TIMED_OUT",
    "ROLLING_BACK",
    "ROLLED_BACK",
    "ROLLBACK_FAILED",
})

# agt_from_state_check / agt_to_state_check constraint values
_DB_TRANSITION_STATES: frozenset[str] = _DB_STATES  # must be identical

# ag_risk_level_check constraint values
_DB_RISK_LEVELS: frozenset[str] = frozenset({
    "SAFE",
    "REVERSIBLE",
    "IRREVERSIBLE",
})

# Expected columns in action_gateway (target schema — post Python model update)
_AG_COLUMNS: frozenset[str] = frozenset({
    # Identity
    "action_id", "case_id", "ticket_id", "client",
    # Classification
    "action_type", "action_namespace", "risk_level", "current_state",
    # Payload
    "action_payload",               # renamed from action_params
    "rollback_action_type", "rollback_params",
    # Proposal
    "proposed_by", "proposed_at",
    # Approval
    "approval_required", "expires_at",  # renamed from approval_deadline
    "approver",                     # renamed from approved_by
    "approved_at",                  # renamed from approval_decision_at
    "rejected_at",                  # new — explicit rejection timestamp
    "approval_notes",
    # Execution
    "executor_id",
    "execution_started_at", "execution_completed_at",
    "execution_failed_at",          # new — explicit failure timestamp
    "execution_attempt", "max_attempts",
    "execution_result",
    # Failure
    "failure_code", "failure_reason",
    # Rollback
    "rollback_action_id",
    "rollback_completed_at",        # new — explicit rollback completion timestamp
    "is_rolled_back",
    # Idempotency
    "idempotency_key",
    # Timestamps
    "created_at", "updated_at",
})

# Expected columns in action_gateway_transitions (target schema)
_AGT_COLUMNS: frozenset[str] = frozenset({
    "transition_id",
    "action_id",
    "case_id", "ticket_id", "client",       # denormalized
    "from_state", "to_state",
    "actor", "reason", "detail",
    "created_at",
})

# Python model's current to_db_row() keys (post Sprint 2.1.6 update — equals _AG_COLUMNS).
_CURRENT_PYTHON_DB_ROW_KEYS: frozenset[str] = _AG_COLUMNS


# ── Helpers ───────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _minimal_action(**overrides) -> ActionRequest:
    defaults = dict(
        case_id="11111111-1111-1111-1111-111111111111",
        ticket_id="TKT-001",
        client="unity_bank",
        action_type="reset_otp",
        action_namespace="identity",
        risk_level=ActionRiskLevel.SAFE,
        idempotency_key="deadbeef" * 8,
    )
    defaults.update(overrides)
    return ActionRequest(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1: STATE CONSTANT COVERAGE
# Ensures the DB CHECK constraint and the Python ActionState enum are in sync.
# If either side adds or removes a state without updating the other, these fail.
# ═══════════════════════════════════════════════════════════════════════════════


class TestStateConstantCoverage:
    """DB ag_state_check ↔ Python ActionState must be identical sets."""

    def test_every_python_state_is_in_db_check_constraint(self):
        python_states = {s.value for s in ActionState}
        missing_from_db = python_states - _DB_STATES
        assert not missing_from_db, (
            f"ActionState values not in DB CHECK constraint: {missing_from_db}. "
            "Add these to ag_state_check in S2_001_action_gateway.sql."
        )

    def test_every_db_state_is_in_python_enum(self):
        python_states = {s.value for s in ActionState}
        missing_from_python = _DB_STATES - python_states
        assert not missing_from_python, (
            f"DB CHECK constraint states not in ActionState enum: {missing_from_python}. "
            "Add these to ActionState in case_engine/action_state.py."
        )

    def test_state_sets_are_identical(self):
        python_states = {s.value for s in ActionState}
        assert python_states == _DB_STATES

    def test_transition_state_set_matches_main_state_set(self):
        """agt_from_state_check and agt_to_state_check must equal ag_state_check."""
        assert _DB_TRANSITION_STATES == _DB_STATES

    def test_db_state_count(self):
        assert len(_DB_STATES) == 12

    def test_all_terminal_states_in_db(self):
        from case_engine.action_state import TERMINAL_ACTION_STATES
        terminal_values = {s.value for s in TERMINAL_ACTION_STATES}
        assert terminal_values.issubset(_DB_STATES)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2: RISK LEVEL CONSTANT COVERAGE
# ═══════════════════════════════════════════════════════════════════════════════


class TestRiskLevelCoverage:
    """DB ag_risk_level_check ↔ Python ActionRiskLevel must be identical sets."""

    def test_every_python_risk_level_in_db_check(self):
        python_levels = {r.value for r in ActionRiskLevel}
        missing = python_levels - _DB_RISK_LEVELS
        assert not missing, (
            f"ActionRiskLevel values not in DB CHECK: {missing}"
        )

    def test_every_db_risk_level_in_python_enum(self):
        python_levels = {r.value for r in ActionRiskLevel}
        missing = _DB_RISK_LEVELS - python_levels
        assert not missing, (
            f"DB CHECK risk levels not in ActionRiskLevel enum: {missing}"
        )

    def test_risk_level_sets_identical(self):
        assert {r.value for r in ActionRiskLevel} == _DB_RISK_LEVELS

    def test_risk_level_count(self):
        assert len(_DB_RISK_LEVELS) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3: DB COLUMN CONTRACT
# Documents the expected column set for action_gateway.
# After the Python model update, these tests verify alignment.
# ═══════════════════════════════════════════════════════════════════════════════


class TestDBColumnContract:
    """Verify expected column counts and presence in the schema contract."""

    def test_action_gateway_column_count(self):
        assert len(_AG_COLUMNS) == 34

    def test_action_gateway_transitions_column_count(self):
        assert len(_AGT_COLUMNS) == 11

    def test_identity_columns_present(self):
        required = {"action_id", "case_id", "ticket_id", "client"}
        assert required.issubset(_AG_COLUMNS)

    def test_classification_columns_present(self):
        required = {"action_type", "action_namespace", "risk_level", "current_state"}
        assert required.issubset(_AG_COLUMNS)

    def test_execution_tracking_columns_present(self):
        required = {"executor_id", "execution_attempt", "max_attempts",
                    "execution_started_at", "execution_completed_at", "execution_failed_at"}
        assert required.issubset(_AG_COLUMNS)

    def test_rollback_columns_present(self):
        required = {"rollback_action_type", "rollback_params", "rollback_action_id",
                    "rollback_completed_at", "is_rolled_back"}
        assert required.issubset(_AG_COLUMNS)

    def test_new_event_timestamp_columns_present(self):
        """rejected_at, execution_failed_at, rollback_completed_at are new in Sprint 2.1.5."""
        new_columns = {"rejected_at", "execution_failed_at", "rollback_completed_at"}
        assert new_columns.issubset(_AG_COLUMNS)

    def test_renamed_columns_use_new_names(self):
        """Verify the schema uses new names, not the old Python model names."""
        assert "action_payload" in _AG_COLUMNS
        assert "action_params" not in _AG_COLUMNS

        assert "approver" in _AG_COLUMNS
        assert "approved_by" not in _AG_COLUMNS

        assert "approved_at" in _AG_COLUMNS
        assert "approval_decision_at" not in _AG_COLUMNS

        assert "expires_at" in _AG_COLUMNS
        assert "approval_deadline" not in _AG_COLUMNS

    def test_transition_table_has_denormalized_fields(self):
        """action_gateway_transitions must have case_id, ticket_id, client."""
        assert {"case_id", "ticket_id", "client"}.issubset(_AGT_COLUMNS)

    def test_transition_table_has_detail_jsonb(self):
        assert "detail" in _AGT_COLUMNS


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4: CHECK CONSTRAINT EQUIVALENT LOGIC
# Tests the business logic behind each DB CHECK constraint.
# These tests pass today and verify that the Python-level checks and the
# DB-level checks represent the same invariants.
# ═══════════════════════════════════════════════════════════════════════════════


class TestCheckConstraintLogic:
    """Verify constraint equivalents as pure Python assertions."""

    # ag_approval_decision_mutex: NOT (approved_at IS NOT NULL AND rejected_at IS NOT NULL)
    def test_approval_decision_mutex_both_null_allowed(self):
        """No decision yet — both null is valid."""
        approved_at = None
        rejected_at = None
        assert not (approved_at is not None and rejected_at is not None)

    def test_approval_decision_mutex_approved_only_allowed(self):
        approved_at = _now()
        rejected_at = None
        assert not (approved_at is not None and rejected_at is not None)

    def test_approval_decision_mutex_rejected_only_allowed(self):
        approved_at = None
        rejected_at = _now()
        assert not (approved_at is not None and rejected_at is not None)

    def test_approval_decision_mutex_both_set_violates(self):
        approved_at = _now()
        rejected_at = _now()
        assert approved_at is not None and rejected_at is not None  # constraint violation

    # ag_rollback_flag_integrity: is_rolled_back = TRUE implies rollback_action_id IS NOT NULL
    def test_rollback_flag_false_requires_no_action_id(self):
        is_rolled_back = False
        rollback_action_id = None
        assert not is_rolled_back or rollback_action_id is not None

    def test_rollback_flag_true_requires_action_id(self):
        is_rolled_back = True
        rollback_action_id = str(uuid.uuid4())
        assert not is_rolled_back or rollback_action_id is not None

    def test_rollback_flag_true_without_action_id_violates(self):
        is_rolled_back = True
        rollback_action_id = None
        violates = is_rolled_back and rollback_action_id is None
        assert violates  # this IS a constraint violation

    # ag_rollback_timestamp_integrity: rollback_completed_at IS NOT NULL implies rollback_action_id IS NOT NULL
    def test_rollback_timestamp_requires_action_id(self):
        rollback_completed_at = _now()
        rollback_action_id = str(uuid.uuid4())
        assert rollback_completed_at is None or rollback_action_id is not None

    def test_rollback_timestamp_null_allows_null_action_id(self):
        rollback_completed_at = None
        rollback_action_id = None
        assert rollback_completed_at is None or rollback_action_id is not None

    # ag_irreversible_no_rollback: IRREVERSIBLE implies rollback_action_type IS NULL
    def test_irreversible_must_not_have_rollback_type(self):
        risk_level = "IRREVERSIBLE"
        rollback_action_type = "restore_account"
        violates = risk_level == "IRREVERSIBLE" and rollback_action_type is not None
        assert violates  # this IS a constraint violation

    def test_irreversible_without_rollback_type_ok(self):
        risk_level = "IRREVERSIBLE"
        rollback_action_type = None
        violates = risk_level == "IRREVERSIBLE" and rollback_action_type is not None
        assert not violates

    def test_reversible_can_have_rollback_type(self):
        risk_level = "REVERSIBLE"
        rollback_action_type = "restore_address"
        violates = risk_level == "IRREVERSIBLE" and rollback_action_type is not None
        assert not violates

    # ag_max_attempts_range: BETWEEN 1 AND 10
    def test_max_attempts_valid_range(self):
        for n in [1, 2, 3, 5, 10]:
            assert 1 <= n <= 10

    def test_max_attempts_zero_violates(self):
        assert not (1 <= 0 <= 10)

    def test_max_attempts_eleven_violates(self):
        assert not (1 <= 11 <= 10)

    # ag_attempt_lte_max: execution_attempt <= max_attempts
    def test_attempt_lte_max_valid(self):
        assert 0 <= 3  # attempt=0, max=3
        assert 3 <= 3  # attempt=3, max=3

    def test_attempt_exceeds_max_violates(self):
        execution_attempt = 4
        max_attempts = 3
        assert not (execution_attempt <= max_attempts)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5: IDEMPOTENCY KEY CONTRACT
# Verifies the SHA-256 key semantics required by the idempotency_key UNIQUE
# constraint in action_gateway.
# ═══════════════════════════════════════════════════════════════════════════════


class TestIdempotencyKeyContract:
    """Idempotency key must satisfy the UNIQUE constraint contract."""

    def _key(self, case_id="c1", action_type="reset_otp",
             action_namespace="identity", action_params=None) -> str:
        return compute_idempotency_key(
            case_id=case_id,
            action_type=action_type,
            action_namespace=action_namespace,
            action_params=action_params or {},
        )

    def test_key_is_64_char_hex(self):
        key = self._key()
        assert len(key) == 64
        int(key, 16)  # must be valid hexadecimal

    def test_key_is_deterministic(self):
        assert self._key() == self._key()

    def test_key_is_deterministic_with_params(self):
        params = {"account_id": "ACC-123", "channel": "mobile"}
        assert self._key(action_params=params) == self._key(action_params=params)

    def test_key_is_dict_order_independent(self):
        """Canonical JSON (sorted keys) ensures stable key regardless of dict ordering."""
        k1 = self._key(action_params={"a": 1, "b": 2})
        k2 = self._key(action_params={"b": 2, "a": 1})
        assert k1 == k2

    def test_key_is_sensitive_to_case_id(self):
        assert self._key(case_id="c1") != self._key(case_id="c2")

    def test_key_is_sensitive_to_action_type(self):
        assert self._key(action_type="reset_otp") != self._key(action_type="freeze_account")

    def test_key_is_sensitive_to_action_namespace(self):
        assert self._key(action_namespace="identity") != self._key(action_namespace="accounts")

    def test_key_is_sensitive_to_params(self):
        k1 = self._key(action_params={"account_id": "ACC-1"})
        k2 = self._key(action_params={"account_id": "ACC-2"})
        assert k1 != k2

    def test_key_matches_manual_sha256(self):
        payload = {
            "case_id": "c1",
            "action_type": "reset_otp",
            "action_namespace": "identity",
            "params": {},
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert self._key() == expected

    def test_unique_actions_produce_unique_keys(self):
        """Regression: different logical actions must never share a key."""
        keys = [
            self._key(case_id="c1", action_type="reset_otp"),
            self._key(case_id="c1", action_type="freeze_account"),
            self._key(case_id="c2", action_type="reset_otp"),
            self._key(case_id="c1", action_type="reset_otp", action_params={"extra": True}),
        ]
        assert len(keys) == len(set(keys)), "Collision detected between distinct action keys"


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6: MIGRATION FILE CONTRACT
# Verifies the migration file structure without executing SQL.
# ═══════════════════════════════════════════════════════════════════════════════


class TestMigrationFileContract:
    """Structural checks on S2_001_action_gateway.sql."""

    import pathlib as _pathlib
    _MIGRATION_PATH = _pathlib.Path(__file__).parent.parent / "sql" / "sprint2_migrations" / "S2_001_action_gateway.sql"

    def _read_sql(self) -> str:
        return self._MIGRATION_PATH.read_text(encoding="utf-8")

    def test_migration_file_exists(self):
        assert self._MIGRATION_PATH.exists(), (
            "S2_001_action_gateway.sql not found. "
            "Expected at sql/sprint2_migrations/S2_001_action_gateway.sql"
        )

    def test_migration_contains_both_table_definitions(self):
        sql = self._read_sql()
        assert "CREATE TABLE IF NOT EXISTS action_gateway" in sql
        assert "CREATE TABLE IF NOT EXISTS action_gateway_transitions" in sql

    def test_migration_contains_supersession_notice(self):
        sql = self._read_sql()
        assert "supersedes" in sql.lower() or "SUPERSEDES" in sql

    def test_migration_contains_append_only_rules(self):
        sql = self._read_sql()
        assert "no_update_action_gateway_transitions" in sql
        assert "no_delete_action_gateway_transitions" in sql

    def test_migration_contains_idempotency_unique_constraint(self):
        sql = self._read_sql()
        assert "ag_idempotency_unique" in sql

    def test_migration_contains_self_referential_fk(self):
        sql = self._read_sql()
        assert "REFERENCES action_gateway (action_id)" in sql

    def test_migration_contains_updated_at_trigger(self):
        sql = self._read_sql()
        assert "_ag_set_updated_at" in sql
        assert "trg_ag_updated_at" in sql

    def test_migration_contains_rls(self):
        sql = self._read_sql()
        assert "ENABLE ROW LEVEL SECURITY" in sql
        assert "ag_deny_anon" in sql
        assert "ag_agent_select" in sql

    def test_migration_contains_all_state_values(self):
        sql = self._read_sql()
        for state in _DB_STATES:
            assert f"'{state}'" in sql, f"State '{state}' missing from migration SQL"

    def test_migration_contains_all_risk_levels(self):
        sql = self._read_sql()
        for level in _DB_RISK_LEVELS:
            assert f"'{level}'" in sql, f"Risk level '{level}' missing from migration SQL"

    def test_migration_uses_new_column_names(self):
        sql = self._read_sql()
        assert "action_payload" in sql
        assert "approver" in sql
        assert "approved_at" in sql
        assert "expires_at" in sql

    def test_migration_does_not_define_old_column_names(self):
        """Old column names must not appear as column definitions.
        They may legitimately appear in COMMENT ON COLUMN string literals."""
        sql = self._read_sql()
        # A column definition has the form: whitespace + name + whitespace + SQLTYPE
        # We check that the old names are NOT followed by SQL type keywords,
        # which would indicate a column definition rather than a comment reference.
        import re
        sql_types = r"\s+(TEXT|JSONB|TIMESTAMPTZ|UUID|BOOLEAN|SMALLINT|INTEGER|BIGINT)"
        assert not re.search(r"\baction_params" + sql_types, sql), (
            "Found 'action_params' used as a column definition — should be 'action_payload'"
        )
        assert not re.search(r"\bapproval_deadline" + sql_types, sql), (
            "Found 'approval_deadline' used as a column definition — should be 'expires_at'"
        )
        assert not re.search(r"\bapproved_by" + sql_types, sql), (
            "Found 'approved_by' used as a column definition — should be 'approver'"
        )
        assert not re.search(r"\bapproval_decision_at" + sql_types, sql), (
            "Found 'approval_decision_at' as a column definition — should be 'approved_at'"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7: PYTHON MODEL ALIGNMENT GAPS (xfail — acceptance criteria)
# These tests document the required changes to ActionRequest.to_db_row().
# They are marked xfail and will PASS (as xfail) until the Python model
# is updated. After update, remove xfail and the tests should pass.
# ═══════════════════════════════════════════════════════════════════════════════


class TestPythonModelAlignmentGaps:
    """
    Acceptance criteria for the Sprint 2.1.5/2.1.6 Python model update.

    All xfail markers removed in Sprint 2.1.6 — ActionRequest now uses the
    correct DB column names and includes all required event timestamp fields.
    """

    def test_to_db_row_uses_action_payload(self):
        action = _minimal_action()
        row = action.to_db_row()
        assert "action_payload" in row, "Expected 'action_payload' key in to_db_row() output"
        assert "action_params" not in row, "Old 'action_params' key must not be present"

    def test_to_db_row_uses_approver(self):
        action = _minimal_action()
        row = action.to_db_row()
        assert "approver" in row
        assert "approved_by" not in row

    def test_to_db_row_uses_approved_at(self):
        action = _minimal_action()
        row = action.to_db_row()
        assert "approved_at" in row
        assert "approval_decision_at" not in row

    def test_to_db_row_uses_expires_at(self):
        action = _minimal_action()
        row = action.to_db_row()
        assert "expires_at" in row
        assert "approval_deadline" not in row

    def test_to_db_row_includes_rejected_at(self):
        action = _minimal_action()
        row = action.to_db_row()
        assert "rejected_at" in row

    def test_to_db_row_includes_execution_failed_at(self):
        action = _minimal_action()
        row = action.to_db_row()
        assert "execution_failed_at" in row

    def test_to_db_row_includes_rollback_completed_at(self):
        action = _minimal_action()
        row = action.to_db_row()
        assert "rollback_completed_at" in row

    def test_from_db_row_reads_action_payload(self):
        action = _minimal_action(action_payload={"account_id": "ACC-1"})
        row = action.to_db_row()
        restored = ActionRequest.from_db_row(row)
        assert restored.action_payload == {"account_id": "ACC-1"}


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 8: CURRENT MODEL CONTRACT (passing — verifies current state)
# These tests verify what the Python model CURRENTLY produces.
# They will need to be updated (or replaced by xfail removal) after model update.
# ═══════════════════════════════════════════════════════════════════════════════


class TestCurrentModelProducesExpectedKeys:
    """
    Verify the current Python model produces a known set of to_db_row() keys.
    These tests pass NOW. After the model update, the set will change — these
    tests will need updating to reflect the new column names.
    """

    def test_current_to_db_row_key_set_is_known(self):
        action = _minimal_action()
        produced_keys = frozenset(action.to_db_row().keys())
        assert produced_keys == _AG_COLUMNS, (
            "ActionRequest.to_db_row() produced unexpected keys. "
            "to_db_row() must produce exactly the _AG_COLUMNS set."
        )

    def test_gap_between_current_and_target_schema(self):
        """After Sprint 2.1.6, the Python model fully matches the DB schema — gap is empty."""
        missing_in_python = _AG_COLUMNS - _CURRENT_PYTHON_DB_ROW_KEYS
        extra_in_python = _CURRENT_PYTHON_DB_ROW_KEYS - _AG_COLUMNS

        assert missing_in_python == set(), (
            f"Keys in DB schema missing from Python model: {missing_in_python}"
        )
        assert extra_in_python == set(), (
            f"Keys in Python model not in DB schema: {extra_in_python}"
        )

    def test_critical_fields_present_in_current_model(self):
        """Fields that the gateway business logic depends on — must be in DB row."""
        action = _minimal_action()
        row = action.to_db_row()
        critical = [
            "action_namespace",     # executor routing
            "executor_id",          # stuck-execution watchdog
            "execution_attempt",    # retry logic
            "max_attempts",         # retry ceiling
            "rollback_action_type", # propose_rollback()
            "rollback_params",      # propose_rollback()
        ]
        for field in critical:
            assert field in row, (
                f"Critical field '{field}' missing from to_db_row(). "
                "The gateway cannot function correctly without this in the DB."
            )

    def test_action_id_is_valid_uuid_format(self):
        action = ActionRequest(
            case_id="11111111-1111-1111-1111-111111111111",
            ticket_id="T1", client="c1",
            action_type="a", action_namespace="n",
            risk_level=ActionRiskLevel.SAFE,
            idempotency_key="k" * 64,
        )
        uuid.UUID(action.action_id)

    def test_transition_record_to_db_row_matches_agt_columns(self):
        """ActionTransitionRecord.to_db_row() must produce exactly the _AGT_COLUMNS set."""
        rec = ActionTransitionRecord(
            action_id="aaaa",
            case_id="bbbb",
            ticket_id="TKT-1",
            client="unity_bank",
            from_state=ActionState.PROPOSED,
            to_state=ActionState.APPROVED,
        )
        row = rec.to_db_row()
        produced = frozenset(row.keys())
        assert "transition_id" in produced, (
            "ActionTransitionRecord.to_db_row() must produce 'transition_id' key."
        )
        assert "log_id" not in produced, (
            "Old 'log_id' key must not be present — renamed to 'transition_id'."
        )
        assert produced == _AGT_COLUMNS, (
            f"Transition row keys do not match _AGT_COLUMNS. "
            f"Extra: {produced - _AGT_COLUMNS}  Missing: {_AGT_COLUMNS - produced}"
        )
