"""
tests/test_sprint240_workflow_playbook_system.py

Sprint 2.40: Workflow Playbook System — comprehensive test suite.

Sections:
  A  — Architecture / package structure
  B  — WorkflowVersion (versioning)
  C  — Models (enums, conditions, step, graph, playbook)
  D  — PlaybookValidator (V01–V13)
  E  — PlaybookGraph (DAG, topological order, cycle detection)
  F  — WorkflowPlaybookRepository (CRUD, find, resolve)
  G  — WorkflowPlaybookResolver (priority, fallback, client scope)
  H  — Serialization (to_dict / from_dict round-trip)
  I  — PlaybookLoader and PlaybookRegistry singleton
  J  — Default playbooks (9 topics)
  K  — PlaybookPlanningRule (playbook → InvestigationPlan)
  L  — InvestigationPlanner integration (playbook_resolver param)
  M  — InvestigationContext (workflow_playbook field, has_playbook_spec)
  N  — Client/topic override resolution
  O  — Parallel and dependency graph scenarios
  P  — Blueprint compliance
  Q  — Regression: Sprint 2.38/2.39 backward compatibility
"""
from __future__ import annotations

import pytest

# ── Imports ────────────────────────────────────────────────────────────────────

from case_engine.investigation.evidence.models import EvidencePriority
from case_engine.investigation.planner.models import (
    EvidenceKind,
    EvidenceRequirement,
    InvestigationPlan,
    RetryPolicy,
)
from case_engine.tools.tool_models import ToolCapability
from case_engine.workflows.playbooks.defaults import build_defaults, get_minimal_playbook
from case_engine.workflows.playbooks.exceptions import (
    DuplicatePlaybookError,
    PlaybookError,
    PlaybookGraphCycleError,
    PlaybookNotFoundError,
    PlaybookRegistryError,
    PlaybookValidationError,
    PlaybookVersionConflictError,
)
from case_engine.workflows.playbooks.loader import PlaybookLoader
from case_engine.workflows.playbooks.models import (
    PlaybookEntryCondition,
    PlaybookExitCondition,
    PlaybookGraph,
    PlaybookStatus,
    PlaybookStep,
    PlaybookStepKind,
    RepositoryStats,
    RiskLevel,
    ValidationRule,
    WorkflowPlaybook,
)
from case_engine.workflows.playbooks.registry import get_default_registry, reset_registry
from case_engine.workflows.playbooks.repository import WorkflowPlaybookRepository
from case_engine.workflows.playbooks.resolver import WorkflowPlaybookResolver
from case_engine.workflows.playbooks.serialization import PlaybookSerializer
from case_engine.workflows.playbooks.validators import PlaybookValidator
from case_engine.workflows.playbooks.versioning import WorkflowVersion


# ── Shared fixtures ────────────────────────────────────────────────────────────

def _req(req_id: str, kind: EvidenceKind = EvidenceKind.LOG) -> EvidenceRequirement:
    return EvidenceRequirement(
        requirement_id=req_id,
        kind=kind,
        title=f"Req {req_id}",
        description="Test requirement",
        priority=EvidencePriority.NORMAL,
        required=True,
        expected_fields=("field_a",),
        validation_hints=(),
        capability_hint=ToolCapability.READ,
    )


def _step(
    step_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    parallel_group: str | None = None,
    fallback: str | None = None,
    critical: bool = False,
    optional: bool = False,
    evidence: list[EvidenceRequirement] | None = None,
) -> PlaybookStep:
    if evidence is None:
        evidence = [_req(f"{step_id}_req")] if critical else []
    return PlaybookStep(
        step_id=step_id,
        name=f"Step {step_id}",
        description="Test step",
        kind=PlaybookStepKind.COLLECT_EVIDENCE,
        evidence_required=tuple(evidence),
        depends_on=depends_on,
        parallel_group=parallel_group,
        fallback=fallback,
        retry_policy=RetryPolicy(),
        required_capability=None,
        timeout_seconds=30.0,
        optional=optional,
        critical=critical,
        estimated_duration_seconds=5.0,
        validation_rules=(),
    )


def _playbook(
    playbook_id: str = "test_pb_v1",
    topic: str = "TEST_TOPIC",
    *,
    steps: list[PlaybookStep] | None = None,
    version: WorkflowVersion | None = None,
    status: PlaybookStatus = PlaybookStatus.ACTIVE,
    enabled: bool = True,
    client_scope: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
) -> WorkflowPlaybook:
    if steps is None:
        steps = [_step("s1")]
    if version is None:
        version = WorkflowVersion(1, 0, 0)
    graph = PlaybookGraph(steps=tuple(steps), parallel_groups=())
    r = _req("r1")
    return WorkflowPlaybook(
        playbook_id=playbook_id,
        name=f"Test Playbook {playbook_id}",
        version=version,
        topic=topic,
        description="A test playbook",
        required_slots=("slot_a",),
        optional_slots=("slot_b",),
        investigation_graph=graph,
        entry_conditions=(),
        exit_conditions=(),
        required_evidence=(r,),
        priority=EvidencePriority.NORMAL,
        risk_level=RiskLevel.SAFE,
        estimated_duration_seconds=60.0,
        requires_approval=False,
        client_scope=client_scope,
        enabled=enabled,
        metadata={},
        tags=tags,
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
        status=status,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section A — Architecture / package structure
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionA_Architecture:
    def test_A01_package_imports_cleanly(self):
        from case_engine.workflows import playbooks  # noqa: F401
        assert hasattr(playbooks, "WorkflowPlaybook")

    def test_A02_all_exceptions_importable(self):
        assert issubclass(PlaybookNotFoundError, PlaybookError)
        assert issubclass(PlaybookValidationError, PlaybookError)
        assert issubclass(PlaybookGraphCycleError, PlaybookError)
        assert issubclass(DuplicatePlaybookError, PlaybookError)
        assert issubclass(PlaybookVersionConflictError, PlaybookError)
        assert issubclass(PlaybookRegistryError, PlaybookError)

    def test_A03_PlaybookError_is_base_exception(self):
        assert issubclass(PlaybookError, Exception)

    def test_A04_all_models_importable(self):
        assert RiskLevel.SAFE
        assert PlaybookStatus.ACTIVE
        assert PlaybookStepKind.COLLECT_EVIDENCE

    def test_A05_WorkflowVersion_importable(self):
        v = WorkflowVersion(1, 2, 3)
        assert str(v) == "1.2.3"

    def test_A06_PlaybookValidator_importable(self):
        pv = PlaybookValidator()
        assert pv is not None

    def test_A07_PlaybookSerializer_importable(self):
        ps = PlaybookSerializer()
        assert ps is not None

    def test_A08_repository_importable(self):
        repo = WorkflowPlaybookRepository()
        assert len(repo) == 0

    def test_A09_resolver_importable(self):
        repo = WorkflowPlaybookRepository()
        minimal = get_minimal_playbook()
        resolver = WorkflowPlaybookResolver(repo, minimal)
        assert resolver is not None

    def test_A10_loader_importable(self):
        loader = PlaybookLoader()
        assert loader is not None

    def test_A11_registry_importable(self):
        reset_registry()
        reg = get_default_registry()
        assert reg is not None
        reset_registry()

    def test_A12_build_defaults_importable(self):
        defaults = build_defaults()
        assert isinstance(defaults, list)

    def test_A13_get_minimal_importable(self):
        minimal = get_minimal_playbook()
        assert isinstance(minimal, WorkflowPlaybook)


# ══════════════════════════════════════════════════════════════════════════════
# Section B — WorkflowVersion
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionB_WorkflowVersion:
    def test_B01_str_representation(self):
        assert str(WorkflowVersion(1, 2, 3)) == "1.2.3"

    def test_B02_equality(self):
        assert WorkflowVersion(1, 0, 0) == WorkflowVersion(1, 0, 0)

    def test_B03_inequality(self):
        assert WorkflowVersion(1, 0, 0) != WorkflowVersion(1, 0, 1)

    def test_B04_ordering_lt(self):
        assert WorkflowVersion(1, 0, 0) < WorkflowVersion(2, 0, 0)

    def test_B05_ordering_gt(self):
        assert WorkflowVersion(2, 0, 0) > WorkflowVersion(1, 0, 0)

    def test_B06_minor_ordering(self):
        assert WorkflowVersion(1, 1, 0) > WorkflowVersion(1, 0, 0)

    def test_B07_patch_ordering(self):
        assert WorkflowVersion(1, 0, 1) > WorkflowVersion(1, 0, 0)

    def test_B08_parse_valid(self):
        v = WorkflowVersion.parse("2.3.4")
        assert v == WorkflowVersion(2, 3, 4)

    def test_B09_parse_invalid_raises(self):
        with pytest.raises(ValueError):
            WorkflowVersion.parse("not_a_version")

    def test_B10_parse_non_numeric_raises(self):
        with pytest.raises(ValueError):
            WorkflowVersion.parse("a.b.c")

    def test_B11_is_compatible_same_major(self):
        v1 = WorkflowVersion(2, 0, 0)
        v2 = WorkflowVersion(2, 5, 3)
        assert v1.is_compatible_with(v2)

    def test_B12_not_compatible_different_major(self):
        v1 = WorkflowVersion(1, 0, 0)
        v2 = WorkflowVersion(2, 0, 0)
        assert not v1.is_compatible_with(v2)

    def test_B13_deprecate_creates_new_version(self):
        v = WorkflowVersion(1, 0, 0)
        deprecated = v.deprecate("2.0.0")
        assert deprecated.deprecated is True
        assert deprecated.replacement == "2.0.0"

    def test_B14_to_dict_round_trip(self):
        v = WorkflowVersion(3, 1, 4)
        d = v.to_dict()
        assert d["major"] == 3
        assert d["minor"] == 1
        assert d["patch"] == 4

    def test_B15_v1_factory(self):
        v = WorkflowVersion.v1()
        assert v == WorkflowVersion(1, 0, 0)

    def test_B16_v2_factory(self):
        v = WorkflowVersion.v2()
        assert v == WorkflowVersion(2, 0, 0)

    def test_B17_frozen_immutable(self):
        v = WorkflowVersion(1, 0, 0)
        with pytest.raises((AttributeError, TypeError)):
            v.major = 2  # type: ignore[misc]

    def test_B18_sort_list(self):
        versions = [WorkflowVersion(3, 0, 0), WorkflowVersion(1, 0, 0), WorkflowVersion(2, 0, 0)]
        assert sorted(versions) == [
            WorkflowVersion(1, 0, 0), WorkflowVersion(2, 0, 0), WorkflowVersion(3, 0, 0)
        ]

    def test_B19_deprecated_false_by_default(self):
        v = WorkflowVersion(1, 0, 0)
        assert v.deprecated is False

    def test_B20_replacement_none_by_default(self):
        v = WorkflowVersion(1, 0, 0)
        assert v.replacement is None


# ══════════════════════════════════════════════════════════════════════════════
# Section C — Models
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionC_Models:
    def test_C01_RiskLevel_values(self):
        assert RiskLevel.SAFE.value == "SAFE"
        assert RiskLevel.REVERSIBLE.value == "REVERSIBLE"
        assert RiskLevel.HIGH.value == "HIGH"
        assert RiskLevel.CRITICAL.value == "CRITICAL"

    def test_C02_PlaybookStatus_values(self):
        assert PlaybookStatus.DRAFT.value == "DRAFT"
        assert PlaybookStatus.ACTIVE.value == "ACTIVE"
        assert PlaybookStatus.DEPRECATED.value == "DEPRECATED"
        assert PlaybookStatus.ARCHIVED.value == "ARCHIVED"

    def test_C03_PlaybookStepKind_values(self):
        kinds = {k.value for k in PlaybookStepKind}
        assert "COLLECT_EVIDENCE" in kinds
        assert "ANALYZE" in kinds
        assert "VALIDATE" in kinds
        assert "CORRELATE" in kinds
        assert "ASSESS" in kinds

    def test_C04_PlaybookEntryCondition_evaluate_exists(self):
        c = PlaybookEntryCondition("c1", "test", "key_a", "exists")
        assert c.evaluate({"key_a": "val"}) is True
        assert c.evaluate({}) is False

    def test_C05_PlaybookEntryCondition_evaluate_not_exists(self):
        c = PlaybookEntryCondition("c1", "test", "key_a", "not_exists")
        assert c.evaluate({}) is True
        assert c.evaluate({"key_a": "x"}) is False

    def test_C06_PlaybookEntryCondition_evaluate_eq(self):
        c = PlaybookEntryCondition("c1", "test", "key_a", "eq", value="hello")
        assert c.evaluate({"key_a": "hello"}) is True
        assert c.evaluate({"key_a": "world"}) is False

    def test_C07_PlaybookEntryCondition_evaluate_neq(self):
        c = PlaybookEntryCondition("c1", "test", "key_a", "neq", value="hello")
        assert c.evaluate({"key_a": "world"}) is True
        assert c.evaluate({"key_a": "hello"}) is False

    def test_C08_PlaybookExitCondition_to_dict(self):
        ec = PlaybookExitCondition("e1", "desc", "evidence_key")
        d = ec.to_dict()
        assert d["condition_id"] == "e1"
        assert d["evidence_key"] == "evidence_key"

    def test_C09_ValidationRule_to_dict(self):
        vr = ValidationRule("r1", "desc", "field_x", "exists")
        d = vr.to_dict()
        assert d["rule_id"] == "r1"
        assert d["field"] == "field_x"

    def test_C10_PlaybookStep_is_root(self):
        s = _step("s1")
        assert s.is_root is True

    def test_C11_PlaybookStep_not_root_when_has_deps(self):
        s = _step("s2", depends_on=("s1",))
        assert s.is_root is False

    def test_C12_PlaybookStep_is_parallelizable(self):
        s = _step("s1", parallel_group="grp_a")
        assert s.is_parallelizable is True

    def test_C13_PlaybookStep_not_parallelizable_by_default(self):
        s = _step("s1")
        assert s.is_parallelizable is False

    def test_C14_PlaybookStep_required_evidence_count(self):
        r1 = _req("r1")
        r2 = EvidenceRequirement(
            "r2", EvidenceKind.LOG, "opt", "optional req",
            EvidencePriority.LOW, False, (), (), None,
        )
        s = _step("s1", evidence=[r1, r2])
        assert s.required_evidence_count() == 1

    def test_C15_PlaybookStep_to_dict(self):
        s = _step("s1")
        d = s.to_dict()
        assert d["step_id"] == "s1"
        assert "evidence_required" in d
        assert "depends_on" in d

    def test_C16_WorkflowPlaybook_is_active_true(self):
        pb = _playbook(status=PlaybookStatus.ACTIVE, enabled=True)
        assert pb.is_active is True

    def test_C17_WorkflowPlaybook_is_active_false_when_disabled(self):
        pb = _playbook(enabled=False)
        assert pb.is_active is False

    def test_C18_WorkflowPlaybook_is_active_false_when_draft(self):
        pb = _playbook(status=PlaybookStatus.DRAFT, enabled=False)
        assert pb.is_active is False

    def test_C19_WorkflowPlaybook_is_global_true(self):
        pb = _playbook(client_scope=())
        assert pb.is_global is True

    def test_C20_WorkflowPlaybook_is_global_false(self):
        pb = _playbook(client_scope=("client_a",))
        assert pb.is_global is False

    def test_C21_WorkflowPlaybook_applies_to_client_global(self):
        pb = _playbook(client_scope=())
        assert pb.applies_to_client("any_client") is True

    def test_C22_WorkflowPlaybook_applies_to_client_scoped(self):
        pb = _playbook(client_scope=("client_a",))
        assert pb.applies_to_client("client_a") is True
        assert pb.applies_to_client("client_b") is False

    def test_C23_WorkflowPlaybook_step_count(self):
        pb = _playbook(steps=[_step("s1"), _step("s2")])
        assert pb.step_count() == 2

    def test_C24_WorkflowPlaybook_version_str(self):
        pb = _playbook(version=WorkflowVersion(2, 3, 1))
        assert pb.version_str() == "2.3.1"

    def test_C25_WorkflowPlaybook_to_dict_keys(self):
        pb = _playbook()
        d = pb.to_dict()
        required_keys = {
            "playbook_id", "name", "version", "topic", "description",
            "status", "enabled", "is_active", "is_global", "step_count",
        }
        assert required_keys.issubset(d.keys())

    def test_C26_WorkflowPlaybook_has_vision_evidence_false(self):
        pb = _playbook()
        assert pb.has_vision_evidence() is False

    def test_C27_WorkflowPlaybook_has_vision_evidence_true(self):
        r = _req("vision_req", kind=EvidenceKind.VISION)
        s = _step("s1", evidence=[r])
        pb = _playbook(steps=[s])
        # required_evidence at playbook level
        from dataclasses import replace
        pb2 = replace(pb, required_evidence=(r,))
        assert pb2.has_vision_evidence() is True

    def test_C28_RepositoryStats_to_dict(self):
        stats = RepositoryStats(
            total_playbooks=5, active_playbooks=3, deprecated_playbooks=1,
            draft_playbooks=1, archived_playbooks=0,
            topics_covered=("A", "B"), client_scoped=2,
        )
        d = stats.to_dict()
        assert d["total_playbooks"] == 5
        assert d["active_playbooks"] == 3

    def test_C29_PlaybookGraph_step_count(self):
        g = PlaybookGraph(steps=(_step("s1"), _step("s2")), parallel_groups=())
        assert g.step_count() == 2

    def test_C30_PlaybookGraph_required_step_count(self):
        s_req = _step("s1")
        s_opt = _step("s2", optional=True)
        g = PlaybookGraph(steps=(s_req, s_opt), parallel_groups=())
        assert g.required_step_count() == 1


# ══════════════════════════════════════════════════════════════════════════════
# Section D — PlaybookValidator
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionD_PlaybookValidator:
    def _validate(self, pb):
        PlaybookValidator().validate(pb)

    def test_D01_valid_playbook_passes(self):
        pb = _playbook()
        self._validate(pb)

    def test_D02_V01_empty_steps_raises(self):
        graph = PlaybookGraph(steps=(), parallel_groups=())
        from dataclasses import replace
        pb = replace(_playbook(), investigation_graph=graph)
        with pytest.raises(PlaybookValidationError, match="at least one step"):
            self._validate(pb)

    def test_D03_V02_duplicate_step_ids_raises(self):
        s1a = _step("s1")
        s1b = _step("s1")
        from dataclasses import replace
        pb = replace(_playbook(), investigation_graph=PlaybookGraph(steps=(s1a, s1b), parallel_groups=()))
        with pytest.raises(PlaybookValidationError, match="duplicate"):
            self._validate(pb)

    def test_D04_V03_self_dep_raises(self):
        s = _step("s1", depends_on=("s1",))
        from dataclasses import replace
        pb = replace(_playbook(), investigation_graph=PlaybookGraph(steps=(s,), parallel_groups=()))
        with pytest.raises(PlaybookValidationError, match="depends on itself"):
            self._validate(pb)

    def test_D05_V04_missing_dep_raises(self):
        s = _step("s2", depends_on=("s_nonexistent",))
        from dataclasses import replace
        pb = replace(_playbook(), investigation_graph=PlaybookGraph(steps=(s,), parallel_groups=()))
        with pytest.raises(PlaybookValidationError, match="unknown step"):
            self._validate(pb)

    def test_D06_V05_cycle_raises(self):
        s1 = _step("s1", depends_on=("s2",))
        s2 = _step("s2", depends_on=("s1",))
        from dataclasses import replace
        pb = replace(_playbook(), investigation_graph=PlaybookGraph(steps=(s1, s2), parallel_groups=()))
        with pytest.raises(PlaybookGraphCycleError):
            self._validate(pb)

    def test_D07_V06_missing_fallback_raises(self):
        s = _step("s1", fallback="nonexistent")
        from dataclasses import replace
        pb = replace(_playbook(), investigation_graph=PlaybookGraph(steps=(s,), parallel_groups=()))
        with pytest.raises(PlaybookValidationError, match="fallback target"):
            self._validate(pb)

    def test_D08_V07_critical_no_evidence_raises(self):
        s = _step("s1", critical=True, evidence=[])
        from dataclasses import replace
        pb = replace(_playbook(), investigation_graph=PlaybookGraph(steps=(s,), parallel_groups=()))
        with pytest.raises(PlaybookValidationError, match="critical step"):
            self._validate(pb)

    def test_D09_V07_critical_with_evidence_passes(self):
        s = _step("s1", critical=True, evidence=[_req("r1")])
        from dataclasses import replace
        pb = replace(_playbook(), investigation_graph=PlaybookGraph(steps=(s,), parallel_groups=()))
        self._validate(pb)

    def test_D10_V09_empty_topic_raises(self):
        from dataclasses import replace
        pb = replace(_playbook(), topic="")
        with pytest.raises(PlaybookValidationError, match="topic"):
            self._validate(pb)

    def test_D11_V10_empty_playbook_id_raises(self):
        from dataclasses import replace
        pb = replace(_playbook(), playbook_id="")
        with pytest.raises(PlaybookValidationError, match="playbook_id"):
            self._validate(pb)

    def test_D12_V12_enabled_archived_raises(self):
        from dataclasses import replace
        pb = replace(_playbook(), status=PlaybookStatus.ARCHIVED, enabled=True)
        with pytest.raises(PlaybookValidationError, match="ARCHIVED"):
            self._validate(pb)

    def test_D13_V13_empty_client_scope_string_raises(self):
        from dataclasses import replace
        pb = replace(_playbook(), client_scope=("",))
        with pytest.raises(PlaybookValidationError, match="empty string"):
            self._validate(pb)

    def test_D14_is_valid_true(self):
        pb = _playbook()
        assert PlaybookValidator().is_valid(pb) is True

    def test_D15_is_valid_false(self):
        from dataclasses import replace
        pb = replace(_playbook(), topic="")
        assert PlaybookValidator().is_valid(pb) is False

    def test_D16_validate_all_returns_errors(self):
        from dataclasses import replace
        valid = _playbook("pb1", "TOPIC_A")
        invalid = replace(_playbook("pb2", "TOPIC_B"), topic="")
        errors = PlaybookValidator().validate_all([valid, invalid])
        assert len(errors) == 1

    def test_D17_validate_all_returns_empty_list_on_all_valid(self):
        defaults = build_defaults()
        errors = PlaybookValidator().validate_all(defaults)
        assert errors == []

    def test_D18_three_step_chain_passes_validation(self):
        s1 = _step("s1")
        s2 = _step("s2", depends_on=("s1",))
        s3 = _step("s3", depends_on=("s2",))
        from dataclasses import replace
        pb = replace(_playbook(), investigation_graph=PlaybookGraph(steps=(s1, s2, s3), parallel_groups=()))
        self._validate(pb)

    def test_D19_draft_disabled_passes_validation(self):
        from dataclasses import replace
        pb = replace(_playbook(), status=PlaybookStatus.DRAFT, enabled=False)
        self._validate(pb)

    def test_D20_deprecated_disabled_passes_validation(self):
        from dataclasses import replace
        pb = replace(_playbook(), status=PlaybookStatus.DEPRECATED, enabled=False)
        self._validate(pb)


# ══════════════════════════════════════════════════════════════════════════════
# Section E — PlaybookGraph
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionE_PlaybookGraph:
    def test_E01_topological_order_single_step(self):
        g = PlaybookGraph(steps=(_step("s1"),), parallel_groups=())
        order = g.topological_order()
        assert [s.step_id for s in order] == ["s1"]

    def test_E02_topological_order_chain(self):
        s1 = _step("s1")
        s2 = _step("s2", depends_on=("s1",))
        s3 = _step("s3", depends_on=("s2",))
        g = PlaybookGraph(steps=(s1, s2, s3), parallel_groups=())
        order = [s.step_id for s in g.topological_order()]
        assert order.index("s1") < order.index("s2") < order.index("s3")

    def test_E03_topological_order_diamond(self):
        s1 = _step("s1")
        s2 = _step("s2", depends_on=("s1",))
        s3 = _step("s3", depends_on=("s1",))
        s4 = _step("s4", depends_on=("s2", "s3"))
        g = PlaybookGraph(steps=(s1, s2, s3, s4), parallel_groups=())
        order = [s.step_id for s in g.topological_order()]
        assert order.index("s1") < order.index("s2")
        assert order.index("s1") < order.index("s3")
        assert order.index("s2") < order.index("s4")
        assert order.index("s3") < order.index("s4")

    def test_E04_has_cycle_false_for_dag(self):
        s1 = _step("s1")
        s2 = _step("s2", depends_on=("s1",))
        g = PlaybookGraph(steps=(s1, s2), parallel_groups=())
        assert g.has_cycle() is False

    def test_E05_is_linear_true_for_chain(self):
        s1 = _step("s1")
        s2 = _step("s2", depends_on=("s1",))
        g = PlaybookGraph(steps=(s1, s2), parallel_groups=())
        assert g.is_linear() is True

    def test_E06_is_linear_false_for_diamond(self):
        s1 = _step("s1")
        s2 = _step("s2", depends_on=("s1",))
        s3 = _step("s3", depends_on=("s1",))
        s4 = _step("s4", depends_on=("s2", "s3"))
        g = PlaybookGraph(steps=(s1, s2, s3, s4), parallel_groups=())
        assert g.is_linear() is False

    def test_E07_get_root_steps_single(self):
        g = PlaybookGraph(steps=(_step("s1"),), parallel_groups=())
        roots = g.get_root_steps()
        assert len(roots) == 1
        assert roots[0].step_id == "s1"

    def test_E08_get_root_steps_multiple_roots(self):
        s1 = _step("s1")
        s2 = _step("s2")
        s3 = _step("s3", depends_on=("s1", "s2"))
        g = PlaybookGraph(steps=(s1, s2, s3), parallel_groups=())
        roots = {s.step_id for s in g.get_root_steps()}
        assert roots == {"s1", "s2"}

    def test_E09_step_by_id_found(self):
        g = PlaybookGraph(steps=(_step("s1"),), parallel_groups=())
        assert g.step_by_id("s1") is not None

    def test_E10_step_by_id_not_found(self):
        g = PlaybookGraph(steps=(_step("s1"),), parallel_groups=())
        assert g.step_by_id("nonexistent") is None

    def test_E11_to_dict_contains_steps(self):
        g = PlaybookGraph(steps=(_step("s1"), _step("s2")), parallel_groups=())
        d = g.to_dict()
        assert d["step_count"] == 2
        assert len(d["steps"]) == 2

    def test_E12_parallel_groups_preserved(self):
        s1 = _step("s1", parallel_group="grp1")
        s2 = _step("s2", parallel_group="grp1")
        g = PlaybookGraph(steps=(s1, s2), parallel_groups=(frozenset({"s1", "s2"}),))
        assert len(g.parallel_groups) == 1


# ══════════════════════════════════════════════════════════════════════════════
# Section F — WorkflowPlaybookRepository
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionF_Repository:
    def _repo(self) -> WorkflowPlaybookRepository:
        return WorkflowPlaybookRepository()

    def test_F01_empty_repo_has_zero_len(self):
        assert len(self._repo()) == 0

    def test_F02_register_increases_len(self):
        repo = self._repo()
        repo.register(_playbook())
        assert len(repo) == 1

    def test_F03_contains_after_register(self):
        repo = self._repo()
        pb = _playbook("pb_x")
        repo.register(pb)
        assert "pb_x" in repo

    def test_F04_duplicate_raises_DuplicatePlaybookError(self):
        repo = self._repo()
        pb = _playbook()
        repo.register(pb)
        with pytest.raises(DuplicatePlaybookError):
            repo.register(pb)

    def test_F05_replace_does_not_raise_on_duplicate(self):
        repo = self._repo()
        pb = _playbook()
        repo.register(pb)
        repo.replace(pb)

    def test_F06_remove_by_id_returns_true(self):
        repo = self._repo()
        repo.register(_playbook("pb1"))
        assert repo.remove("pb1") is True

    def test_F07_remove_nonexistent_returns_false(self):
        repo = self._repo()
        assert repo.remove("nonexistent") is False

    def test_F08_get_returns_latest_version(self):
        repo = self._repo()
        pb1 = _playbook("pb1", version=WorkflowVersion(1, 0, 0))
        pb2 = _playbook("pb1", version=WorkflowVersion(2, 0, 0))
        repo.register(pb1)
        repo.register(pb2)
        got = repo.get("pb1")
        assert got is not None
        assert got.version == WorkflowVersion(2, 0, 0)

    def test_F09_get_specific_version(self):
        repo = self._repo()
        pb1 = _playbook("pb1", version=WorkflowVersion(1, 0, 0))
        pb2 = _playbook("pb1", version=WorkflowVersion(2, 0, 0))
        repo.register(pb1)
        repo.register(pb2)
        got = repo.get("pb1", WorkflowVersion(1, 0, 0))
        assert got is not None
        assert got.version == WorkflowVersion(1, 0, 0)

    def test_F10_get_returns_none_if_not_found(self):
        repo = self._repo()
        assert repo.get("nonexistent") is None

    def test_F11_find_by_topic(self):
        repo = self._repo()
        repo.register(_playbook("pb1", "TOPIC_A"))
        repo.register(_playbook("pb2", "TOPIC_B"))
        results = repo.find("TOPIC_A")
        assert len(results) == 1
        assert results[0].playbook_id == "pb1"

    def test_F12_find_is_case_insensitive(self):
        repo = self._repo()
        repo.register(_playbook("pb1", "VKYC_SESSION_FAILURE"))
        results = repo.find("vkyc_session_failure")
        assert len(results) == 1

    def test_F13_find_with_client_id_filters_globally(self):
        repo = self._repo()
        pb_global = _playbook("pb_global", "TOPIC_A", client_scope=())
        pb_scoped = _playbook("pb_scoped", "TOPIC_A", client_scope=("client_x",))
        repo.register(pb_global)
        repo.register(pb_scoped)
        results = repo.find("TOPIC_A", client_id="client_x")
        assert len(results) == 2

    def test_F14_find_excludes_inactive_by_default(self):
        repo = self._repo()
        from dataclasses import replace
        pb = replace(_playbook(), status=PlaybookStatus.DRAFT, enabled=False)
        repo.register(pb)
        results = repo.find("TEST_TOPIC")
        assert len(results) == 0

    def test_F15_find_includes_inactive_when_flagged(self):
        repo = self._repo()
        from dataclasses import replace
        pb = replace(_playbook(), status=PlaybookStatus.DRAFT, enabled=False)
        repo.register(pb)
        results = repo.find("TEST_TOPIC", include_inactive=True)
        assert len(results) == 1

    def test_F16_resolve_returns_playbook(self):
        repo = self._repo()
        repo.register(_playbook())
        result = repo.resolve("TEST_TOPIC")
        assert result is not None

    def test_F17_resolve_returns_none_for_missing_topic(self):
        repo = self._repo()
        assert repo.resolve("NO_SUCH_TOPIC") is None

    def test_F18_list_topics(self):
        repo = self._repo()
        repo.register(_playbook("pb1", "TOPIC_A"))
        repo.register(_playbook("pb2", "TOPIC_B"))
        topics = repo.list_topics()
        assert "TOPIC_A" in topics
        assert "TOPIC_B" in topics

    def test_F19_statistics_reflect_registration(self):
        repo = self._repo()
        repo.register(_playbook("pb1", "TOPIC_A"))
        stats = repo.statistics()
        assert stats.total_playbooks == 1
        assert stats.active_playbooks == 1

    def test_F20_remove_specific_version(self):
        repo = self._repo()
        pb1 = _playbook("pb1", version=WorkflowVersion(1, 0, 0))
        pb2 = _playbook("pb1", version=WorkflowVersion(2, 0, 0))
        repo.register(pb1)
        repo.register(pb2)
        repo.remove("pb1", WorkflowVersion(1, 0, 0))
        assert repo.get("pb1") is not None
        assert repo.get("pb1").version == WorkflowVersion(2, 0, 0)

    def test_F21_client_scoped_sorted_before_global(self):
        repo = self._repo()
        pb_global = _playbook("pb_global", "TOPIC_A", client_scope=())
        pb_client = _playbook("pb_client", "TOPIC_A", client_scope=("client_x",))
        repo.register(pb_global)
        repo.register(pb_client)
        results = repo.find("TOPIC_A", client_id="client_x")
        assert results[0].playbook_id == "pb_client"

    def test_F22_list_excludes_inactive_by_default(self):
        repo = self._repo()
        from dataclasses import replace
        pb_inactive = replace(_playbook("pb1"), status=PlaybookStatus.DRAFT, enabled=False)
        repo.register(_playbook("pb2"))
        repo.register(pb_inactive)
        listed = repo.list()
        assert all(p.is_active for p in listed)


# ══════════════════════════════════════════════════════════════════════════════
# Section G — WorkflowPlaybookResolver
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionG_Resolver:
    def _resolver_with(self, *playbooks) -> WorkflowPlaybookResolver:
        repo = WorkflowPlaybookRepository()
        for pb in playbooks:
            repo.register(pb)
        minimal = get_minimal_playbook()
        return WorkflowPlaybookResolver(repo, minimal)

    def test_G01_resolve_known_topic(self):
        pb = _playbook("pb1", "TOPIC_A")
        resolver = self._resolver_with(pb)
        result = resolver.resolve("TOPIC_A")
        assert result.playbook_id == "pb1"

    def test_G02_resolve_fallback_for_unknown_topic(self):
        resolver = self._resolver_with()
        result = resolver.resolve("NO_SUCH_TOPIC")
        assert result.topic == "MINIMAL_INVESTIGATION"

    def test_G03_resolve_never_returns_none(self):
        resolver = self._resolver_with()
        for topic in ["VKYC", "OTP", "UNKNOWN_XYZ", "", "garbage"]:
            assert resolver.resolve(topic) is not None

    def test_G04_can_resolve_true_when_topic_exists(self):
        pb = _playbook("pb1", "TOPIC_A")
        resolver = self._resolver_with(pb)
        assert resolver.can_resolve("TOPIC_A") is True

    def test_G05_can_resolve_false_for_missing_topic(self):
        resolver = self._resolver_with()
        assert resolver.can_resolve("NO_SUCH") is False

    def test_G06_can_resolve_false_for_minimal_topic(self):
        resolver = self._resolver_with()
        assert resolver.can_resolve("MINIMAL_INVESTIGATION") is False

    def test_G07_resolve_normalises_topic_case(self):
        pb = _playbook("pb1", "VKYC_SESSION_FAILURE")
        resolver = self._resolver_with(pb)
        result = resolver.resolve("vkyc_session_failure")
        assert result.playbook_id == "pb1"

    def test_G08_resolve_normalises_hyphens(self):
        pb = _playbook("pb1", "VKYC_SESSION_FAILURE")
        resolver = self._resolver_with(pb)
        result = resolver.resolve("VKYC-SESSION-FAILURE")
        assert result.playbook_id == "pb1"

    def test_G09_client_specific_preferred_over_global(self):
        global_pb  = _playbook("global_pb",  "TOPIC_A", client_scope=())
        client_pb  = _playbook("client_pb",  "TOPIC_A", client_scope=("unity",))
        resolver = self._resolver_with(global_pb, client_pb)
        result = resolver.resolve("TOPIC_A", client_id="unity")
        assert result.playbook_id == "client_pb"

    def test_G10_global_playbook_returned_when_no_client_match(self):
        global_pb = _playbook("global_pb", "TOPIC_A", client_scope=())
        client_pb = _playbook("client_pb", "TOPIC_A", client_scope=("unity",))
        resolver = self._resolver_with(global_pb, client_pb)
        result = resolver.resolve("TOPIC_A", client_id="other_client")
        assert result.playbook_id == "global_pb"

    def test_G11_list_topics(self):
        resolver = self._resolver_with(
            _playbook("pb1", "TOPIC_A"),
            _playbook("pb2", "TOPIC_B"),
        )
        topics = resolver.list_topics()
        assert "TOPIC_A" in topics
        assert "TOPIC_B" in topics

    def test_G12_resolution_summary_resolved_field(self):
        pb = _playbook("pb1", "TOPIC_A")
        resolver = self._resolver_with(pb)
        summary = resolver.resolution_summary("TOPIC_A")
        assert summary["resolved"] == "pb1"

    def test_G13_resolution_summary_is_minimal_false_when_resolved(self):
        pb = _playbook("pb1", "TOPIC_A")
        resolver = self._resolver_with(pb)
        summary = resolver.resolution_summary("TOPIC_A")
        assert summary["is_minimal"] is False

    def test_G14_resolution_summary_is_minimal_true_for_unknown(self):
        resolver = self._resolver_with()
        summary = resolver.resolution_summary("NO_SUCH_TOPIC")
        assert summary["is_minimal"] is True

    def test_G15_absolute_fallback_when_no_minimal_in_repo(self):
        repo = WorkflowPlaybookRepository()
        # No playbooks registered — absolute fallback from constructor
        minimal = get_minimal_playbook()
        resolver = WorkflowPlaybookResolver(repo, minimal)
        result = resolver.resolve("NO_SUCH")
        assert result.topic == "MINIMAL_INVESTIGATION"


# ══════════════════════════════════════════════════════════════════════════════
# Section H — Serialization
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionH_Serialization:
    def _roundtrip(self, pb: WorkflowPlaybook) -> WorkflowPlaybook:
        s = PlaybookSerializer()
        d = s.to_dict(pb)
        return s.from_dict(d)

    def test_H01_to_dict_is_json_compatible(self):
        import json
        pb = _playbook()
        d = PlaybookSerializer().to_dict(pb)
        json.dumps(d)  # must not raise

    def test_H02_from_dict_roundtrip_playbook_id(self):
        pb = _playbook("pb_roundtrip", "ROUND_TRIP_TOPIC")
        pb2 = self._roundtrip(pb)
        assert pb2.playbook_id == "pb_roundtrip"

    def test_H03_from_dict_roundtrip_topic(self):
        pb = _playbook(topic="MY_TOPIC")
        pb2 = self._roundtrip(pb)
        assert pb2.topic == "MY_TOPIC"

    def test_H04_from_dict_roundtrip_version(self):
        pb = _playbook(version=WorkflowVersion(3, 1, 4))
        pb2 = self._roundtrip(pb)
        assert pb2.version == WorkflowVersion(3, 1, 4)

    def test_H05_from_dict_roundtrip_status(self):
        pb = _playbook()
        pb2 = self._roundtrip(pb)
        assert pb2.status == PlaybookStatus.ACTIVE

    def test_H06_from_dict_roundtrip_steps_count(self):
        pb = _playbook(steps=[_step("s1"), _step("s2")])
        pb2 = self._roundtrip(pb)
        assert pb2.step_count() == 2

    def test_H07_from_dict_roundtrip_step_id(self):
        pb = _playbook(steps=[_step("my_step")])
        pb2 = self._roundtrip(pb)
        assert pb2.investigation_graph.steps[0].step_id == "my_step"

    def test_H08_from_dict_roundtrip_evidence_count(self):
        r = _req("r_ev1")
        s = _step("s1", evidence=[r])
        pb = _playbook(steps=[s])
        pb2 = self._roundtrip(pb)
        assert len(pb2.investigation_graph.steps[0].evidence_required) == 1

    def test_H09_to_summary_contains_required_keys(self):
        pb = _playbook()
        summary = PlaybookSerializer().to_summary(pb)
        assert "playbook_id" in summary
        assert "topic" in summary
        assert "version" in summary
        assert "step_count" in summary

    def test_H10_roundtrip_entry_conditions(self):
        from dataclasses import replace
        ec = PlaybookEntryCondition("ec1", "test", "slot_x", "exists")
        pb = replace(_playbook(), entry_conditions=(ec,))
        pb2 = self._roundtrip(pb)
        assert len(pb2.entry_conditions) == 1
        assert pb2.entry_conditions[0].condition_id == "ec1"

    def test_H11_roundtrip_exit_conditions(self):
        from dataclasses import replace
        xc = PlaybookExitCondition("xc1", "test", "evidence_key")
        pb = replace(_playbook(), exit_conditions=(xc,))
        pb2 = self._roundtrip(pb)
        assert len(pb2.exit_conditions) == 1
        assert pb2.exit_conditions[0].condition_id == "xc1"

    def test_H12_roundtrip_tags(self):
        pb = _playbook(tags=("tag_a", "tag_b"))
        pb2 = self._roundtrip(pb)
        assert "tag_a" in pb2.tags
        assert "tag_b" in pb2.tags

    def test_H13_roundtrip_client_scope(self):
        from dataclasses import replace
        pb = replace(_playbook(), client_scope=("client_x", "client_y"))
        pb2 = self._roundtrip(pb)
        assert "client_x" in pb2.client_scope
        assert "client_y" in pb2.client_scope

    def test_H14_roundtrip_risk_level(self):
        from dataclasses import replace
        pb = replace(_playbook(), risk_level=RiskLevel.HIGH)
        pb2 = self._roundtrip(pb)
        assert pb2.risk_level == RiskLevel.HIGH

    def test_H15_roundtrip_requires_approval(self):
        from dataclasses import replace
        pb = replace(_playbook(), requires_approval=True)
        pb2 = self._roundtrip(pb)
        assert pb2.requires_approval is True


# ══════════════════════════════════════════════════════════════════════════════
# Section I — PlaybookLoader and Registry
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionI_LoaderAndRegistry:
    def test_I01_loader_loads_all_defaults(self):
        loader = PlaybookLoader()
        count = loader.load_defaults()
        assert count == 9

    def test_I02_loader_idempotent_on_duplicate_load(self):
        loader = PlaybookLoader()
        loader.load_defaults()
        count2 = loader.load_defaults()
        assert count2 == 0

    def test_I03_loader_builds_resolver(self):
        loader = PlaybookLoader()
        loader.load_defaults()
        resolver = loader.build_resolver()
        assert resolver is not None

    def test_I04_loader_repository_has_defaults(self):
        loader = PlaybookLoader()
        loader.load_defaults()
        stats = loader.repository.statistics()
        assert stats.total_playbooks == 9

    def test_I05_registry_singleton(self):
        reset_registry()
        reg1 = get_default_registry()
        reg2 = get_default_registry()
        assert reg1 is reg2
        reset_registry()

    def test_I06_reset_clears_singleton(self):
        reset_registry()
        reg1 = get_default_registry()
        reset_registry()
        reg2 = get_default_registry()
        assert reg1 is not reg2
        reset_registry()

    def test_I07_registry_has_nine_playbooks(self):
        reset_registry()
        reg = get_default_registry()
        stats = reg.repository.statistics()
        assert stats.total_playbooks == 9
        reset_registry()

    def test_I08_registry_resolver_resolves_vkyc(self):
        reset_registry()
        reg = get_default_registry()
        pb = reg.resolver.resolve("VKYC_SESSION_FAILURE")
        assert pb.topic == "VKYC_SESSION_FAILURE"
        reset_registry()

    def test_I09_registry_resolver_fallback(self):
        reset_registry()
        reg = get_default_registry()
        pb = reg.resolver.resolve("TOTALLY_UNKNOWN_TOPIC_XYZ")
        assert pb.topic == "MINIMAL_INVESTIGATION"
        reset_registry()

    def test_I10_loader_with_custom_repo(self):
        repo = WorkflowPlaybookRepository()
        loader = PlaybookLoader(repository=repo)
        loader.load_defaults()
        assert len(repo) == 9


# ══════════════════════════════════════════════════════════════════════════════
# Section J — Default Playbooks
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionJ_DefaultPlaybooks:
    def setup_method(self):
        self.defaults = build_defaults()
        self.by_topic = {pb.topic: pb for pb in self.defaults}

    def test_J01_nine_default_playbooks(self):
        assert len(self.defaults) == 9

    def test_J02_all_defaults_active(self):
        assert all(pb.is_active for pb in self.defaults)

    def test_J03_all_defaults_valid(self):
        errors = PlaybookValidator().validate_all(self.defaults)
        assert errors == []

    def test_J04_minimal_investigation_present(self):
        assert "MINIMAL_INVESTIGATION" in self.by_topic

    def test_J05_otp_delivery_failure_present(self):
        assert "OTP_DELIVERY_FAILURE" in self.by_topic

    def test_J06_vkyc_session_failure_present(self):
        assert "VKYC_SESSION_FAILURE" in self.by_topic

    def test_J07_document_ocr_failure_present(self):
        assert "DOCUMENT_OCR_FAILURE" in self.by_topic

    def test_J08_api_callback_failure_present(self):
        assert "API_CALLBACK_FAILURE" in self.by_topic

    def test_J09_agent_portal_issue_present(self):
        assert "AGENT_PORTAL_ISSUE" in self.by_topic

    def test_J10_session_failure_present(self):
        assert "SESSION_FAILURE" in self.by_topic

    def test_J11_document_upload_failure_present(self):
        assert "DOCUMENT_UPLOAD_FAILURE" in self.by_topic

    def test_J12_unknown_issue_present(self):
        assert "UNKNOWN_ISSUE" in self.by_topic

    def test_J13_minimal_has_one_step(self):
        pb = self.by_topic["MINIMAL_INVESTIGATION"]
        assert pb.step_count() == 1

    def test_J14_vkyc_has_five_steps(self):
        pb = self.by_topic["VKYC_SESSION_FAILURE"]
        assert pb.step_count() == 5

    def test_J15_vkyc_requires_session_and_phone(self):
        pb = self.by_topic["VKYC_SESSION_FAILURE"]
        assert "session_id" in pb.required_slots
        assert "phone_number" in pb.required_slots

    def test_J16_vkyc_requires_approval(self):
        pb = self.by_topic["VKYC_SESSION_FAILURE"]
        assert pb.requires_approval is True

    def test_J17_vkyc_has_vision_evidence(self):
        pb = self.by_topic["VKYC_SESSION_FAILURE"]
        has_vision = any(r.kind == EvidenceKind.VISION for r in pb.required_evidence)
        assert has_vision is True

    def test_J18_ocr_has_vision_evidence(self):
        pb = self.by_topic["DOCUMENT_OCR_FAILURE"]
        has_vision = any(r.kind == EvidenceKind.VISION for r in pb.required_evidence)
        assert has_vision is True

    def test_J19_all_defaults_are_global(self):
        assert all(pb.is_global for pb in self.defaults)

    def test_J20_all_defaults_have_unique_topics(self):
        topics = [pb.topic for pb in self.defaults]
        assert len(topics) == len(set(topics))

    def test_J21_get_minimal_playbook_is_minimal(self):
        minimal = get_minimal_playbook()
        assert minimal.topic == "MINIMAL_INVESTIGATION"

    def test_J22_get_minimal_playbook_is_active(self):
        minimal = get_minimal_playbook()
        assert minimal.is_active is True

    def test_J23_otp_requires_phone_number_slot(self):
        pb = self.by_topic["OTP_DELIVERY_FAILURE"]
        assert "phone_number" in pb.required_slots

    def test_J24_ocr_requires_document_id_slot(self):
        pb = self.by_topic["DOCUMENT_OCR_FAILURE"]
        assert "document_id" in pb.required_slots

    def test_J25_agent_portal_requires_agent_id_slot(self):
        pb = self.by_topic["AGENT_PORTAL_ISSUE"]
        assert "agent_id" in pb.required_slots


# ══════════════════════════════════════════════════════════════════════════════
# Section K — PlaybookPlanningRule (conversion)
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionK_PlaybookPlanningRule:
    def setup_method(self):
        reset_registry()
        self.reg = get_default_registry()

    def teardown_method(self):
        reset_registry()

    def _rule_for(self, topic: str):
        from case_engine.investigation.planner.engine import PlaybookPlanningRule
        pb = self.reg.resolver.resolve(topic)
        return PlaybookPlanningRule(pb)

    def _plan(self, topic: str) -> InvestigationPlan:
        rule = self._rule_for(topic)
        return rule.generate_plan(
            plan_id="plan-001",
            case_id="case-001",
            client_id="unity",
            topic=topic,
            workflow_id=None,
            slots={},
            workflow=None,
            sop=None,
        )

    def test_K01_generate_plan_returns_InvestigationPlan(self):
        plan = self._plan("VKYC_SESSION_FAILURE")
        assert isinstance(plan, InvestigationPlan)

    def test_K02_plan_topic_matches(self):
        plan = self._plan("VKYC_SESSION_FAILURE")
        assert plan.topic == "VKYC_SESSION_FAILURE"

    def test_K03_plan_case_id_matches(self):
        from case_engine.investigation.planner.engine import PlaybookPlanningRule
        pb = self.reg.resolver.resolve("OTP_DELIVERY_FAILURE")
        rule = PlaybookPlanningRule(pb)
        plan = rule.generate_plan("p1", "CASE-XYZ", "client", "OTP_DELIVERY_FAILURE", None, {}, None, None)
        assert plan.case_id == "CASE-XYZ"

    def test_K04_plan_steps_count_matches_playbook(self):
        pb_topic = "VKYC_SESSION_FAILURE"
        pb = self.reg.resolver.resolve(pb_topic)
        plan = self._plan(pb_topic)
        assert len(plan.steps) == pb.step_count()

    def test_K05_plan_steps_are_ordered(self):
        plan = self._plan("VKYC_SESSION_FAILURE")
        orders = [s.order for s in plan.steps]
        assert orders == sorted(orders)

    def test_K06_plan_graph_nodes_match_steps(self):
        plan = self._plan("OTP_DELIVERY_FAILURE")
        assert len(plan.graph.nodes) == len(plan.steps)

    def test_K07_plan_expected_evidence_non_empty(self):
        plan = self._plan("VKYC_SESSION_FAILURE")
        assert len(plan.expected_evidence) > 0

    def test_K08_plan_completion_conditions_non_empty(self):
        plan = self._plan("VKYC_SESSION_FAILURE")
        assert len(plan.completion_conditions) > 0

    def test_K09_plan_root_steps_have_no_deps(self):
        plan = self._plan("VKYC_SESSION_FAILURE")
        root_steps = plan.graph.get_root_steps()
        assert len(root_steps) >= 1
        for rs in root_steps:
            assert len(rs.depends_on) == 0

    def test_K10_plan_topic_key_matches(self):
        from case_engine.investigation.planner.engine import PlaybookPlanningRule
        pb = self.reg.resolver.resolve("DOCUMENT_OCR_FAILURE")
        rule = PlaybookPlanningRule(pb)
        assert rule.topic_key() == "DOCUMENT_OCR_FAILURE"

    def test_K11_applies_to_correct_topic(self):
        from case_engine.investigation.planner.engine import PlaybookPlanningRule
        pb = self.reg.resolver.resolve("VKYC_SESSION_FAILURE")
        rule = PlaybookPlanningRule(pb)
        assert rule.applies_to("VKYC_SESSION_FAILURE") is True
        assert rule.applies_to("OTP_DELIVERY_FAILURE") is False

    def test_K12_plan_has_investigation_priority(self):
        from case_engine.investigation.planner.models import InvestigationPriority
        plan = self._plan("VKYC_SESSION_FAILURE")
        assert plan.investigation_priority in list(InvestigationPriority)

    def test_K13_critical_step_gets_escalate_strategy(self):
        from case_engine.investigation.planner.models import FailureStrategy
        plan = self._plan("VKYC_SESSION_FAILURE")
        escalating = [s for s in plan.steps if s.failure_strategy == FailureStrategy.ESCALATE]
        assert len(escalating) > 0

    def test_K14_optional_steps_preserved(self):
        plan = self._plan("VKYC_SESSION_FAILURE")
        optional_steps = [s for s in plan.steps if s.optional]
        assert len(optional_steps) > 0

    def test_K15_minimal_plan_has_one_step(self):
        plan = self._plan("MINIMAL_INVESTIGATION")
        assert len(plan.steps) == 1


# ══════════════════════════════════════════════════════════════════════════════
# Section L — InvestigationPlanner integration
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionL_PlannerIntegration:
    def setup_method(self):
        reset_registry()
        from case_engine.tenant.models import TenantContext, TenantType, TenantEnvironment
        self.tc = TenantContext(
            client_id="test_client",
            client_name="Test",
            domain="test.kwikid.ai",
            tenant_type=TenantType.BANK,
            environment=TenantEnvironment.PRODUCTION,
            enabled_tools=(),
            credentials_ref="creds",
        )

    def teardown_method(self):
        reset_registry()

    def _ctx(self, topic: str = "VKYC_SESSION_FAILURE"):
        from case_engine.investigation.context import InvestigationContext
        ctx = InvestigationContext.create(
            case_id="CASE-001",
            ticket_id="TKT-001",
            ticket_subject="Test",
            ticket_description="Test",
            customer_id="CUST-001",
            customer_email="test@test.com",
            channel="email",
            tenant_context=self.tc,
            topic=topic,
            classification_confidence=0.95,
        )
        return ctx

    def test_L01_planner_with_playbook_resolver_produces_plan(self):
        from case_engine.investigation.planner.engine import InvestigationPlanner
        reg = get_default_registry()
        planner = InvestigationPlanner(playbook_resolver=reg.resolver)
        ctx = self._ctx("VKYC_SESSION_FAILURE")
        plan = planner.plan(ctx)
        assert isinstance(plan, InvestigationPlan)

    def test_L02_planner_without_playbook_resolver_still_works(self):
        from case_engine.investigation.planner.engine import InvestigationPlanner
        planner = InvestigationPlanner()
        ctx = self._ctx("VKYC_SESSION_FAILURE")
        plan = planner.plan(ctx)
        assert isinstance(plan, InvestigationPlan)

    def test_L03_planner_uses_playbook_resolver_over_hardcoded_rules(self):
        from case_engine.investigation.planner.engine import InvestigationPlanner, PlaybookPlanningRule
        from case_engine.investigation.planner.rules import VKYCSessionFailurePlanningRule
        reg = get_default_registry()
        planner = InvestigationPlanner(playbook_resolver=reg.resolver)
        ctx = self._ctx("VKYC_SESSION_FAILURE")
        plan = planner.plan(ctx)
        # With playbook resolver, VKYC plan comes from PlaybookPlanningRule (5 steps from playbook)
        # Without: VKYCSessionFailurePlanningRule (4 steps from hardcoded)
        # The playbook-driven plan should have exactly 5 steps
        assert len(plan.steps) == 5

    def test_L04_planner_uses_hardcoded_rule_when_no_resolver(self):
        from case_engine.investigation.planner.engine import InvestigationPlanner
        planner = InvestigationPlanner()
        ctx = self._ctx("VKYC_SESSION_FAILURE")
        plan = planner.plan(ctx)
        assert isinstance(plan, InvestigationPlan)
        # Hardcoded VKYC rule produces 4 steps
        assert len(plan.steps) == 4

    def test_L05_planner_falls_back_on_resolver_failure(self):
        from case_engine.investigation.planner.engine import InvestigationPlanner

        class BrokenResolver:
            def resolve(self, *a, **kw):
                raise RuntimeError("resolver broken")
            def can_resolve(self, *a, **kw):
                raise RuntimeError("resolver broken")

        planner = InvestigationPlanner(playbook_resolver=BrokenResolver())  # type: ignore
        ctx = self._ctx("VKYC_SESSION_FAILURE")
        plan = planner.plan(ctx)
        assert isinstance(plan, InvestigationPlan)

    def test_L06_PlaybookResolverProtocol_satisfied_by_resolver(self):
        from case_engine.investigation.planner.engine import PlaybookResolverProtocol
        reg = get_default_registry()
        assert isinstance(reg.resolver, PlaybookResolverProtocol)

    def test_L07_planner_plan_never_raises(self):
        from case_engine.investigation.planner.engine import InvestigationPlanner
        reg = get_default_registry()
        planner = InvestigationPlanner(playbook_resolver=reg.resolver)
        for topic in ["GARBAGE", "VKYC_SESSION_FAILURE", "", "OTP_DELIVERY_FAILURE"]:
            ctx = self._ctx(topic)
            plan = planner.plan(ctx)
            assert plan is not None


# ══════════════════════════════════════════════════════════════════════════════
# Section M — InvestigationContext (workflow_playbook field)
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionM_Context:
    def setup_method(self):
        from case_engine.tenant.models import TenantContext, TenantType, TenantEnvironment
        self.tc = TenantContext(
            client_id="client",
            client_name="Test",
            domain="test.kwikid.ai",
            tenant_type=TenantType.BANK,
            environment=TenantEnvironment.PRODUCTION,
            enabled_tools=(),
            credentials_ref="creds",
        )

    def _ctx(self):
        from case_engine.investigation.context import InvestigationContext
        return InvestigationContext.create(
            case_id="C1", ticket_id="T1", ticket_subject="s", ticket_description="d",
            customer_id="cu1", customer_email="e@e.com", channel="email",
            tenant_context=self.tc, topic="VKYC_SESSION_FAILURE",
        )

    def test_M01_workflow_playbook_defaults_to_none(self):
        ctx = self._ctx()
        assert ctx.workflow_playbook is None

    def test_M02_has_playbook_spec_false_when_none(self):
        ctx = self._ctx()
        assert ctx.has_playbook_spec() is False

    def test_M03_has_playbook_spec_true_when_set(self):
        ctx = self._ctx()
        ctx.workflow_playbook = get_minimal_playbook()
        assert ctx.has_playbook_spec() is True

    def test_M04_to_dict_includes_playbook_id_when_set(self):
        ctx = self._ctx()
        ctx.workflow_playbook = get_minimal_playbook()
        d = ctx.to_dict()
        assert d["playbook_id"] == "minimal_investigation_v1"

    def test_M05_to_dict_playbook_id_is_none_when_not_set(self):
        ctx = self._ctx()
        d = ctx.to_dict()
        assert d["playbook_id"] is None

    def test_M06_workflow_playbook_assignable(self):
        ctx = self._ctx()
        pb = _playbook("test_pb_assign", "ASSIGN_TOPIC")
        ctx.workflow_playbook = pb
        assert ctx.workflow_playbook.playbook_id == "test_pb_assign"


# ══════════════════════════════════════════════════════════════════════════════
# Section N — Client/topic override resolution
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionN_ClientTopicOverride:
    def _resolver(self, *playbooks) -> WorkflowPlaybookResolver:
        repo = WorkflowPlaybookRepository()
        for pb in playbooks:
            repo.register(pb)
        minimal = get_minimal_playbook()
        return WorkflowPlaybookResolver(repo, minimal)

    def test_N01_client_override_beats_global(self):
        global_pb = _playbook("global_v1", "VKYC_SESSION_FAILURE", client_scope=())
        client_pb = _playbook("client_v1", "VKYC_SESSION_FAILURE", client_scope=("unity",))
        resolver = self._resolver(global_pb, client_pb)
        result = resolver.resolve("VKYC_SESSION_FAILURE", client_id="unity")
        assert result.playbook_id == "client_v1"

    def test_N02_global_fallback_when_client_not_in_scope(self):
        global_pb = _playbook("global_v1", "VKYC_SESSION_FAILURE", client_scope=())
        client_pb = _playbook("client_v1", "VKYC_SESSION_FAILURE", client_scope=("unity",))
        resolver = self._resolver(global_pb, client_pb)
        result = resolver.resolve("VKYC_SESSION_FAILURE", client_id="zenith")
        assert result.playbook_id == "global_v1"

    def test_N03_multi_client_scope(self):
        from dataclasses import replace
        pb = replace(_playbook("pb_multi", "TOPIC_A"), client_scope=("c1", "c2", "c3"))
        resolver = self._resolver(pb)
        assert resolver.resolve("TOPIC_A", client_id="c1").playbook_id == "pb_multi"
        assert resolver.resolve("TOPIC_A", client_id="c2").playbook_id == "pb_multi"
        assert resolver.resolve("TOPIC_A", client_id="c3").playbook_id == "pb_multi"

    def test_N04_multi_client_scope_excludes_others(self):
        from dataclasses import replace
        pb = replace(_playbook("pb_multi", "TOPIC_A"), client_scope=("c1", "c2"))
        resolver = self._resolver(pb)
        result = resolver.resolve("TOPIC_A", client_id="c_other")
        assert result.topic == "MINIMAL_INVESTIGATION"

    def test_N05_no_client_id_resolves_global(self):
        global_pb = _playbook("global", "TOPIC_X", client_scope=())
        resolver = self._resolver(global_pb)
        result = resolver.resolve("TOPIC_X", client_id=None)
        assert result.playbook_id == "global"

    def test_N06_newer_version_preferred_within_same_scope(self):
        pb_v1 = _playbook("pb_v1", "TOPIC_A", version=WorkflowVersion(1, 0, 0))
        pb_v2 = _playbook("pb_v1", "TOPIC_A", version=WorkflowVersion(2, 0, 0))
        repo = WorkflowPlaybookRepository()
        repo.register(pb_v1)
        repo.register(pb_v2)
        minimal = get_minimal_playbook()
        resolver = WorkflowPlaybookResolver(repo, minimal)
        result = resolver.resolve("TOPIC_A")
        assert result.version == WorkflowVersion(2, 0, 0)


# ══════════════════════════════════════════════════════════════════════════════
# Section O — Parallel and dependency graph scenarios
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionO_ParallelAndDependencies:
    def test_O01_parallel_steps_in_graph(self):
        s1 = _step("s1")
        s2 = _step("s2", depends_on=("s1",), parallel_group="grp1")
        s3 = _step("s3", depends_on=("s1",), parallel_group="grp1")
        s4 = _step("s4", depends_on=("s2", "s3"))
        g = PlaybookGraph(steps=(s1, s2, s3, s4), parallel_groups=(frozenset({"s2", "s3"}),))
        PlaybookValidator().validate(_playbook(steps=[s1, s2, s3, s4]))
        order = g.topological_order()
        assert order[0].step_id == "s1"
        assert order[-1].step_id == "s4"

    def test_O02_parallel_group_none_for_sequential_steps(self):
        s = _step("s1")
        assert s.parallel_group is None
        assert s.is_parallelizable is False

    def test_O03_diamond_dag_valid(self):
        s1 = _step("s1")
        s2 = _step("s2", depends_on=("s1",))
        s3 = _step("s3", depends_on=("s1",))
        s4 = _step("s4", depends_on=("s2", "s3"))
        pb = _playbook(steps=[s1, s2, s3, s4])
        PlaybookValidator().validate(pb)

    def test_O04_five_step_fan_out(self):
        root = _step("root")
        branches = [_step(f"b{i}", depends_on=("root",)) for i in range(4)]
        pb = _playbook(steps=[root] + branches)
        PlaybookValidator().validate(pb)

    def test_O05_PlaybookPlanningRule_preserves_parallel_info(self):
        from case_engine.investigation.planner.engine import PlaybookPlanningRule
        s1 = _step("s1")
        s2 = _step("s2", depends_on=("s1",), parallel_group="grp1")
        s3 = _step("s3", depends_on=("s1",), parallel_group="grp1")
        s4 = _step("s4", depends_on=("s2", "s3"))
        pb = _playbook(steps=[s1, s2, s3, s4])
        rule = PlaybookPlanningRule(pb)
        plan = rule.generate_plan("p1", "c1", "cl", "TEST_TOPIC", None, {}, None, None)
        parallel_steps = [s for s in plan.steps if s.parallelizable]
        assert len(parallel_steps) == 2

    def test_O06_dependency_edges_in_plan(self):
        from case_engine.investigation.planner.engine import PlaybookPlanningRule
        s1 = _step("s1")
        s2 = _step("s2", depends_on=("s1",))
        pb = _playbook(steps=[s1, s2])
        rule = PlaybookPlanningRule(pb)
        plan = rule.generate_plan("p1", "c1", "cl", "TEST_TOPIC", None, {}, None, None)
        assert len(plan.graph.edges) == 1
        edge = plan.graph.edges[0]
        assert edge.from_step_id == "s1"
        assert edge.to_step_id == "s2"


# ══════════════════════════════════════════════════════════════════════════════
# Section P — Blueprint compliance
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionP_BlueprintCompliance:
    def test_P01_resolver_always_deterministic(self):
        reset_registry()
        reg = get_default_registry()
        for _ in range(5):
            pb = reg.resolver.resolve("VKYC_SESSION_FAILURE")
            assert pb.playbook_id == "vkyc_session_failure_v1"
        reset_registry()

    def test_P02_resolver_never_returns_none(self):
        reset_registry()
        reg = get_default_registry()
        for topic in ["A", "B", "C", "UNKNOWN", ""]:
            assert reg.resolver.resolve(topic) is not None
        reset_registry()

    def test_P03_PlaybookStep_never_names_tools(self):
        defaults = build_defaults()
        for pb in defaults:
            for step in pb.investigation_graph.steps:
                assert "Tool" not in step.name
                assert "Tool" not in step.description

    def test_P04_EvidenceRequirement_uses_kind_not_tool_name(self):
        defaults = build_defaults()
        for pb in defaults:
            for req in pb.required_evidence:
                assert isinstance(req.kind, EvidenceKind)

    def test_P05_WorkflowPlaybook_is_frozen(self):
        pb = _playbook()
        with pytest.raises((AttributeError, TypeError)):
            pb.topic = "CHANGED"  # type: ignore[misc]

    def test_P06_PlaybookStep_is_frozen(self):
        s = _step("s1")
        with pytest.raises((AttributeError, TypeError)):
            s.step_id = "changed"  # type: ignore[misc]

    def test_P07_all_default_topics_are_uppercase(self):
        defaults = build_defaults()
        for pb in defaults:
            assert pb.topic == pb.topic.upper()

    def test_P08_investigation_plan_is_frozen(self):
        from case_engine.investigation.planner.engine import PlaybookPlanningRule
        reset_registry()
        reg = get_default_registry()
        pb = reg.resolver.resolve("VKYC_SESSION_FAILURE")
        rule = PlaybookPlanningRule(pb)
        plan = rule.generate_plan("p1", "c1", "cl", "VKYC_SESSION_FAILURE", None, {}, None, None)
        with pytest.raises((AttributeError, TypeError)):
            plan.topic = "CHANGED"  # type: ignore[misc]
        reset_registry()

    def test_P09_playbook_planning_rule_is_PlanningRule(self):
        from case_engine.investigation.planner.engine import PlaybookPlanningRule
        from case_engine.investigation.planner.rules import PlanningRule
        pb = _playbook()
        rule = PlaybookPlanningRule(pb)
        assert isinstance(rule, PlanningRule)

    def test_P10_PlaybookResolverProtocol_is_runtime_checkable(self):
        from case_engine.investigation.planner.engine import PlaybookResolverProtocol
        reset_registry()
        reg = get_default_registry()
        assert isinstance(reg.resolver, PlaybookResolverProtocol)
        reset_registry()


# ══════════════════════════════════════════════════════════════════════════════
# Section Q — Regression: Sprint 2.38/2.39 backward compatibility
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionQ_Regression:
    def test_Q01_Sprint239_InvestigationPlanner_still_importable(self):
        from case_engine.investigation.planner.engine import InvestigationPlanner
        assert InvestigationPlanner is not None

    def test_Q02_Sprint239_planner_works_without_playbook_resolver(self):
        from case_engine.investigation.planner.engine import InvestigationPlanner
        from case_engine.investigation.context import InvestigationContext
        from case_engine.tenant.models import TenantContext, TenantType, TenantEnvironment
        tc = TenantContext(
            client_id="client", client_name="Test", domain="test.kwikid.ai",
            tenant_type=TenantType.BANK, environment=TenantEnvironment.PRODUCTION,
            enabled_tools=(), credentials_ref="creds",
        )
        planner = InvestigationPlanner()
        ctx = InvestigationContext.create(
            case_id="C1", ticket_id="T1", ticket_subject="s", ticket_description="d",
            customer_id="cu1", customer_email="e@e.com", channel="email",
            tenant_context=tc, topic="OTP_DELIVERY_FAILURE",
        )
        plan = planner.plan(ctx)
        assert isinstance(plan, InvestigationPlan)

    def test_Q03_Sprint239_MinimalFallbackRule_still_works(self):
        from case_engine.investigation.planner.rules import MinimalFallbackRule
        rule = MinimalFallbackRule()
        plan = rule.generate_plan("p1", "c1", "cl", "GARBAGE", None, {}, None, None)
        assert isinstance(plan, InvestigationPlan)

    def test_Q04_Sprint239_VKYCRule_still_produces_4_steps(self):
        from case_engine.investigation.planner.rules import VKYCSessionFailurePlanningRule
        rule = VKYCSessionFailurePlanningRule()
        plan = rule.generate_plan("p1", "c1", "cl", "VKYC_SESSION_FAILURE", None, {}, None, None)
        assert len(plan.steps) == 4

    def test_Q05_InvestigationContext_still_creates_without_playbook(self):
        from case_engine.investigation.context import InvestigationContext
        from case_engine.tenant.models import TenantContext, TenantType, TenantEnvironment
        tc = TenantContext(
            client_id="client", client_name="Test", domain="test.kwikid.ai",
            tenant_type=TenantType.BANK, environment=TenantEnvironment.PRODUCTION,
            enabled_tools=(), credentials_ref="creds",
        )
        ctx = InvestigationContext.create(
            case_id="C1", ticket_id="T1", ticket_subject="s", ticket_description="d",
            customer_id="cu1", customer_email="e@e.com", channel="email",
            tenant_context=tc, topic="OTP",
        )
        assert ctx.workflow_playbook is None
        assert ctx.has_playbook_spec() is False

    def test_Q06_WorkflowPlaybookRepository_is_independent(self):
        repo1 = WorkflowPlaybookRepository()
        repo2 = WorkflowPlaybookRepository()
        repo1.register(_playbook("pb1", "TOPIC_A"))
        assert "pb1" not in repo2

    def test_Q07_DuplicatePlaybookError_carries_message(self):
        repo = WorkflowPlaybookRepository()
        pb = _playbook()
        repo.register(pb)
        with pytest.raises(DuplicatePlaybookError) as exc_info:
            repo.register(pb)
        assert "test_pb_v1" in str(exc_info.value)

    def test_Q08_Sprint240_does_not_break_Sprint238_context(self):
        from case_engine.investigation.context import InvestigationContext
        assert hasattr(InvestigationContext, "workflow_playbook")
        assert hasattr(InvestigationContext, "has_playbook_spec")

    def test_Q09_PlaybookPlanningRule_generate_plan_never_raises(self):
        from case_engine.investigation.planner.engine import PlaybookPlanningRule
        for pb in build_defaults():
            rule = PlaybookPlanningRule(pb)
            plan = rule.generate_plan("p1", "c1", "cl", pb.topic, None, {}, None, None)
            assert isinstance(plan, InvestigationPlan)

    def test_Q10_all_9_defaults_producible_via_registry(self):
        reset_registry()
        reg = get_default_registry()
        from case_engine.investigation.planner.engine import PlaybookPlanningRule
        for pb in build_defaults():
            resolved = reg.resolver.resolve(pb.topic)
            rule = PlaybookPlanningRule(resolved)
            plan = rule.generate_plan("p", "c", "cl", pb.topic, None, {}, None, None)
            assert isinstance(plan, InvestigationPlan)
        reset_registry()
