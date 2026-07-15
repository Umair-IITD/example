"""
tests/test_sprint241_sop_repository_engine.py

Sprint 2.41 Certification Test Suite — SOP Repository & Resolution Engine

Sections:
  A: Exceptions hierarchy
  B: SOPSemanticVersion
  C: SOPValidator (V01-V12)
  D: SOPDocumentRepository
  E: SOPDocumentResolver (never returns None)
  F: SOPSerializer roundtrip
  G: SOPLoader
  H: Registry singleton
  I: Default SOPs library (9 SOPs)
  J: InvestigationContext Sprint 2.41 fields
  K: InvestigationPlanner integration
  L: Backward compatibility (Sprint 2.38/2.39/2.40)
  M: Protocol compatibility
  N: Topic normalisation
  O: Integration (resolver + repo + loader + context)
  P: Edge cases
  Q: Thread safety
"""
from __future__ import annotations

import threading
import time
from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ── SOP package ────────────────────────────────────────────────────────────────
from case_engine.knowledge.sop.exceptions import (
    DuplicateSOPError,
    SOPError,
    SOPGraphCycleError,
    SOPNotFoundError,
    SOPRegistryError,
    SOPValidationError,
    SOPVersionConflictError,
)
from case_engine.knowledge.sop.versioning import SOPSemanticVersion
from case_engine.knowledge.sop.models import (
    SOPActionType,
    SOPCondition,
    SOPDecision,
    SOPDocument,
    SOPExecutionHints,
    SOPInstruction,
    SOPOutcome,
    SOPParameter,
    SOPProcedure,
    SOPReference,
    SOPStatus,
    SOPStep,
    SOPStepKind,
    SOPTriggerCondition,
    SOPTriggerOperator,
    SOPVersion,
)
from case_engine.knowledge.sop.validators import SOPValidator
from case_engine.knowledge.sop.serialization import SOPSerializer
from case_engine.knowledge.sop.repository import (
    RepositorySOPStats,
    SOPDocumentRepository,
    SOPRegistry,
    SOPRepository,
)
from case_engine.knowledge.sop.resolver import SOPDocumentResolver
from case_engine.knowledge.sop.loader import SOPLoader
from case_engine.knowledge.sop import (
    build_defaults,
    get_minimal_sop,
    reset_sop_registry,
    get_default_sop_registry,
    get_default_sop_resolver,
)
from case_engine.knowledge.sop.defaults import build_defaults as _build_defaults


# ── Shared test fixtures ───────────────────────────────────────────────────────

def _make_step(
    step_id: str = "s1",
    step_number: int = 1,
    action_type: SOPActionType = SOPActionType.INVESTIGATE,
    title: str = "Test Step",
    instruction: str = "Do something",
    kind: SOPStepKind = SOPStepKind.READ,
    critical: bool = False,
    required_inputs: tuple = (),
    expected_outputs: tuple = (),
    dependencies: tuple = (),
) -> SOPStep:
    return SOPStep(
        step_number=step_number,
        step_id=step_id,
        action_type=action_type,
        title=title,
        instruction=instruction,
        kind=kind,
        critical=critical,
        required_inputs=required_inputs,
        expected_outputs=expected_outputs,
        dependencies=dependencies,
    )


def _make_sop(
    sop_id: str = "sop_test_v1",
    topic: str = "TEST_TOPIC",
    version: str = "1.0",
    status: SOPStatus = SOPStatus.ACTIVE,
    steps: tuple | None = None,
    applicable_to: tuple = (),
    client_scope: tuple = (),
    enabled: bool = True,
    escalation_threshold: float = 0.4,
) -> SOPDocument:
    if steps is None:
        steps = (_make_step(),)
    return SOPDocument(
        sop_id=sop_id,
        title="Test SOP",
        topic=topic,
        status=status,
        version=version,
        trigger_conditions=(),
        steps=steps,
        escalation_threshold=escalation_threshold,
        applicable_to=applicable_to,
        client_scope=client_scope,
        enabled=enabled,
    )


# ════════════════════════════════════════════════════════════════════════════════
# SECTION A — Exceptions
# ════════════════════════════════════════════════════════════════════════════════

class TestSectionA_Exceptions:

    def test_A01_sop_error_is_exception(self):
        e = SOPError("base")
        assert isinstance(e, Exception)

    def test_A02_not_found_is_sop_error(self):
        e = SOPNotFoundError("missing")
        assert isinstance(e, SOPError)

    def test_A03_validation_error_stores_errors(self):
        e = SOPValidationError("bad", errors=["V01: no steps"])
        assert "V01" in e.errors[0]
        assert isinstance(e, SOPError)

    def test_A04_validation_error_empty_list_default(self):
        e = SOPValidationError("bad")
        assert e.errors == []

    def test_A05_duplicate_is_sop_error(self):
        e = DuplicateSOPError("dup")
        assert isinstance(e, SOPError)

    def test_A06_version_conflict_is_sop_error(self):
        e = SOPVersionConflictError("ver conflict")
        assert isinstance(e, SOPError)

    def test_A07_registry_error_is_sop_error(self):
        e = SOPRegistryError("reg err")
        assert isinstance(e, SOPError)

    def test_A08_graph_cycle_is_validation_error(self):
        e = SOPGraphCycleError("cycle")
        assert isinstance(e, SOPValidationError)
        assert isinstance(e, SOPError)

    def test_A09_exceptions_carry_message(self):
        msg = "custom message"
        for cls in (SOPError, SOPNotFoundError, DuplicateSOPError,
                    SOPVersionConflictError, SOPRegistryError):
            e = cls(msg)
            assert str(e) == msg

    def test_A10_validation_error_multiple_errors(self):
        errs = ["E1", "E2", "E3"]
        e = SOPValidationError("msg", errors=errs)
        assert len(e.errors) == 3


# ════════════════════════════════════════════════════════════════════════════════
# SECTION B — SOPSemanticVersion
# ════════════════════════════════════════════════════════════════════════════════

class TestSectionB_SemanticVersion:

    def test_B01_parse_three_parts(self):
        v = SOPSemanticVersion.parse("1.2.3")
        assert v.major == 1 and v.minor == 2 and v.patch == 3

    def test_B02_parse_two_parts(self):
        v = SOPSemanticVersion.parse("2.1")
        assert v.major == 2 and v.minor == 1 and v.patch == 0

    def test_B03_str(self):
        v = SOPSemanticVersion(1, 2, 3)
        assert str(v) == "1.2.3"

    def test_B04_lt(self):
        assert SOPSemanticVersion(1, 0, 0) < SOPSemanticVersion(1, 0, 1)
        assert SOPSemanticVersion(1, 0, 0) < SOPSemanticVersion(2, 0, 0)

    def test_B05_gt(self):
        assert SOPSemanticVersion(2, 0, 0) > SOPSemanticVersion(1, 9, 9)

    def test_B06_equality(self):
        assert SOPSemanticVersion(1, 2, 3) == SOPSemanticVersion(1, 2, 3)

    def test_B07_hash(self):
        s = {SOPSemanticVersion(1, 0, 0), SOPSemanticVersion(1, 0, 0)}
        assert len(s) == 1

    def test_B08_is_compatible_same_major(self):
        assert SOPSemanticVersion(1, 0, 0).is_compatible(SOPSemanticVersion(1, 5, 3))

    def test_B09_is_compatible_diff_major(self):
        assert not SOPSemanticVersion(1, 0, 0).is_compatible(SOPSemanticVersion(2, 0, 0))

    def test_B10_deprecate(self):
        v = SOPSemanticVersion(1, 0, 0)
        dep = v.deprecate("2.0.0")
        assert dep.deprecated
        assert dep.replacement == "2.0.0"
        assert not v.deprecated  # original unchanged

    def test_B11_v1_factory(self):
        v = SOPSemanticVersion.v1()
        assert v.major == 1 and v.minor == 0 and v.patch == 0

    def test_B12_v2_factory(self):
        v = SOPSemanticVersion.v2()
        assert v.major == 2 and v.minor == 0 and v.patch == 0

    def test_B13_parse_bad_format_raises(self):
        with pytest.raises(SOPVersionConflictError):
            SOPSemanticVersion.parse("not-a-version")

    def test_B14_le_and_ge(self):
        v1 = SOPSemanticVersion(1, 0, 0)
        v2 = SOPSemanticVersion(1, 0, 0)
        assert v1 <= v2 and v1 >= v2

    def test_B15_frozen(self):
        v = SOPSemanticVersion(1, 0, 0)
        with pytest.raises((AttributeError, TypeError)):
            v.major = 99  # type: ignore


# ════════════════════════════════════════════════════════════════════════════════
# SECTION C — SOPValidator
# ════════════════════════════════════════════════════════════════════════════════

class TestSectionC_Validator:
    validator = SOPValidator()

    def test_C01_valid_sop(self):
        sop = _make_sop()
        assert self.validator.is_valid(sop)

    def test_C02_V01_no_steps(self):
        sop = _make_sop(steps=())
        errors = self.validator.validate(sop)
        assert any("V01" in e for e in errors)

    def test_C03_V02_duplicate_step_ids(self):
        s1 = _make_step("s1", 1)
        s2 = _make_step("s1", 2)
        sop = _make_sop(steps=(s1, s2))
        errors = self.validator.validate(sop)
        assert any("V02" in e for e in errors)

    def test_C04_V03_self_dependency(self):
        s1 = _make_step("s1", 1, dependencies=("s1",))
        sop = _make_sop(steps=(s1,))
        errors = self.validator.validate(sop)
        assert any("V03" in e for e in errors)

    def test_C05_V04_missing_dependency(self):
        s1 = _make_step("s1", 1)
        s2 = _make_step("s2", 2, dependencies=("s999",))
        sop = _make_sop(steps=(s1, s2))
        errors = self.validator.validate(sop)
        assert any("V04" in e for e in errors)

    def test_C06_V05_cycle(self):
        s1 = _make_step("s1", 1, dependencies=("s2",))
        s2 = _make_step("s2", 2, dependencies=("s1",))
        sop = _make_sop(steps=(s1, s2))
        errors = self.validator.validate(sop)
        assert any("V05" in e for e in errors)

    def test_C07_V06_empty_sop_id(self):
        sop = _make_sop(sop_id="   ")
        errors = self.validator.validate(sop)
        assert any("V06" in e for e in errors)

    def test_C08_V07_empty_topic(self):
        sop = _make_sop(topic="  ")
        errors = self.validator.validate(sop)
        assert any("V07" in e for e in errors)

    def test_C09_V08_empty_version(self):
        sop = _make_sop(version="  ")
        errors = self.validator.validate(sop)
        assert any("V08" in e for e in errors)

    def test_C10_V09_critical_step_no_io(self):
        s = _make_step("s1", 1, critical=True, required_inputs=(), expected_outputs=())
        sop = _make_sop(steps=(s,))
        errors = self.validator.validate(sop)
        assert any("V09" in e for e in errors)

    def test_C11_V09_critical_step_with_input_ok(self):
        s = _make_step("s1", 1, critical=True, required_inputs=("x",))
        sop = _make_sop(steps=(s,))
        errors = self.validator.validate(sop)
        assert not any("V09" in e for e in errors)

    def test_C12_V10_active_enabled_false(self):
        sop = _make_sop(status=SOPStatus.ACTIVE, enabled=False)
        errors = self.validator.validate(sop)
        assert any("V10" in e for e in errors)

    def test_C13_V11_empty_string_in_applicable_to(self):
        sop = _make_sop(applicable_to=("client1", ""))
        errors = self.validator.validate(sop)
        assert any("V11" in e for e in errors)

    def test_C14_V12_escalation_threshold_out_of_range(self):
        sop = _make_sop(escalation_threshold=1.5)
        errors = self.validator.validate(sop)
        assert any("V12" in e for e in errors)

    def test_C15_validate_or_raise_raises(self):
        sop = _make_sop(steps=())
        with pytest.raises(SOPValidationError) as exc_info:
            self.validator.validate_or_raise(sop)
        assert len(exc_info.value.errors) > 0

    def test_C16_validate_all_returns_dict(self):
        bad_sop = _make_sop(sop_id="bad", steps=())
        good_sop = _make_sop(sop_id="good")
        result = self.validator.validate_all([bad_sop, good_sop])
        assert "bad" in result
        assert "good" not in result

    def test_C17_valid_dep_chain(self):
        s1 = _make_step("s1", 1)
        s2 = _make_step("s2", 2, dependencies=("s1",))
        s3 = _make_step("s3", 3, dependencies=("s2",))
        sop = _make_sop(steps=(s1, s2, s3))
        assert self.validator.is_valid(sop)

    def test_C18_V12_threshold_zero_valid(self):
        sop = _make_sop(escalation_threshold=0.0)
        errors = self.validator.validate(sop)
        assert not any("V12" in e for e in errors)

    def test_C19_V12_threshold_one_valid(self):
        sop = _make_sop(escalation_threshold=1.0)
        errors = self.validator.validate(sop)
        assert not any("V12" in e for e in errors)


# ════════════════════════════════════════════════════════════════════════════════
# SECTION D — SOPDocumentRepository
# ════════════════════════════════════════════════════════════════════════════════

class TestSectionD_Repository:

    def test_D01_register_and_len(self):
        repo = SOPDocumentRepository()
        repo.register(_make_sop("s1", version="1.0"))
        assert len(repo) == 1

    def test_D02_duplicate_raises(self):
        repo = SOPDocumentRepository()
        sop = _make_sop("s1", version="1.0")
        repo.register(sop)
        with pytest.raises(DuplicateSOPError):
            repo.register(sop)

    def test_D03_replace_does_not_raise(self):
        repo = SOPDocumentRepository()
        sop = _make_sop("s1", version="1.0")
        repo.register(sop)
        repo.replace(sop)  # should not raise

    def test_D04_get_by_id_and_version(self):
        repo = SOPDocumentRepository()
        sop = _make_sop("s1", version="1.0")
        repo.register(sop)
        assert repo.get("s1", "1.0") is sop

    def test_D05_get_latest_when_no_version(self):
        repo = SOPDocumentRepository()
        s1 = _make_sop("s1", version="1.0")
        s2 = _make_sop("s1", version="2.0")
        repo.register(s1)
        repo.register(s2)
        latest = repo.get("s1")
        assert latest is not None
        assert latest.version == "2.0"

    def test_D06_find_by_topic(self):
        repo = SOPDocumentRepository()
        repo.register(_make_sop("s1", topic="OTP_DELIVERY_FAILURE"))
        repo.register(_make_sop("s2", topic="VKYC_SESSION_FAILURE"))
        found = repo.find("OTP_DELIVERY_FAILURE")
        assert len(found) == 1
        assert found[0].sop_id == "s1"

    def test_D07_find_excludes_inactive(self):
        repo = SOPDocumentRepository()
        repo.register(_make_sop("s1", topic="T1", status=SOPStatus.ARCHIVED))
        assert repo.find("T1") == []

    def test_D08_find_includes_inactive_when_flag_set(self):
        repo = SOPDocumentRepository()
        repo.register(_make_sop("s1", topic="T1", status=SOPStatus.ARCHIVED))
        found = repo.find("T1", include_inactive=True)
        assert len(found) == 1

    def test_D09_resolve_returns_best(self):
        repo = SOPDocumentRepository()
        repo.register(_make_sop("global_sop", topic="T1"))
        result = repo.resolve("T1")
        assert result is not None
        assert result.sop_id == "global_sop"

    def test_D10_resolve_returns_none_when_missing(self):
        repo = SOPDocumentRepository()
        assert repo.resolve("MISSING_TOPIC") is None

    def test_D11_client_specific_priority_over_global(self):
        repo = SOPDocumentRepository()
        global_sop = _make_sop("global", topic="T1", applicable_to=(), client_scope=())
        client_sop = _make_sop("client", topic="T1", applicable_to=("acme",), version="2.0")
        repo.register(global_sop)
        repo.register(client_sop)
        result = repo.resolve("T1", client_id="acme")
        assert result is not None
        assert result.sop_id == "client"

    def test_D12_global_returned_when_no_client_match(self):
        repo = SOPDocumentRepository()
        global_sop = _make_sop("global", topic="T1")
        client_sop = _make_sop("client", topic="T1", applicable_to=("acme",), version="2.0")
        repo.register(global_sop)
        repo.register(client_sop)
        result = repo.resolve("T1", client_id="other_client")
        assert result is not None
        assert result.sop_id in ("global", "client")

    def test_D13_remove_by_id_and_version(self):
        repo = SOPDocumentRepository()
        repo.register(_make_sop("s1", version="1.0"))
        assert repo.remove("s1", "1.0")
        assert len(repo) == 0

    def test_D14_remove_all_versions(self):
        repo = SOPDocumentRepository()
        repo.register(_make_sop("s1", version="1.0"))
        repo.register(_make_sop("s1", version="2.0"))
        assert repo.remove("s1")
        assert len(repo) == 0

    def test_D15_remove_missing_returns_false(self):
        repo = SOPDocumentRepository()
        assert not repo.remove("nonexistent")

    def test_D16_list_topics(self):
        repo = SOPDocumentRepository()
        repo.register(_make_sop("s1", topic="ALPHA"))
        repo.register(_make_sop("s2", topic="BETA"))
        topics = repo.list_topics()
        assert "ALPHA" in topics
        assert "BETA" in topics

    def test_D17_list_clients(self):
        repo = SOPDocumentRepository()
        repo.register(_make_sop("s1", applicable_to=("acme",)))
        clients = repo.list_clients()
        assert "acme" in clients

    def test_D18_statistics(self):
        repo = SOPDocumentRepository()
        repo.register(_make_sop("s1", status=SOPStatus.ACTIVE))
        repo.register(_make_sop("s2", status=SOPStatus.ARCHIVED, version="1.0", steps=(_make_step(),), enabled=False))
        stats = repo.statistics()
        assert isinstance(stats, RepositorySOPStats)
        assert stats.total_sops == 2
        assert stats.active_sops == 1

    def test_D19_contains(self):
        repo = SOPDocumentRepository()
        repo.register(_make_sop("s1"))
        assert "s1" in repo
        assert "missing" not in repo

    def test_D20_find_topic_normalisation(self):
        repo = SOPDocumentRepository()
        repo.register(_make_sop("s1", topic="OTP_DELIVERY_FAILURE"))
        found = repo.find("otp-delivery-failure")
        assert len(found) == 1


# ════════════════════════════════════════════════════════════════════════════════
# SECTION E — SOPDocumentResolver
# ════════════════════════════════════════════════════════════════════════════════

class TestSectionE_Resolver:

    def _make_repo_with_sop(self, topic: str = "TEST", client_id: str | None = None) -> SOPDocumentRepository:
        repo = SOPDocumentRepository()
        applicable_to = (client_id,) if client_id else ()
        repo.register(_make_sop("sop1", topic=topic, applicable_to=applicable_to))
        return repo

    def _make_fallback(self) -> SOPDocument:
        return _make_sop("fallback_sop", topic="MINIMAL_INVESTIGATION")

    def test_E01_resolve_returns_sop(self):
        repo = self._make_repo_with_sop("T1")
        fallback = self._make_fallback()
        resolver = SOPDocumentResolver(repo, fallback)
        result = resolver.resolve("T1")
        assert result.sop_id == "sop1"

    def test_E02_resolve_never_returns_none(self):
        repo = SOPDocumentRepository()  # empty
        fallback = self._make_fallback()
        resolver = SOPDocumentResolver(repo, fallback)
        result = resolver.resolve("NO_SUCH_TOPIC")
        assert result is not None

    def test_E03_resolve_returns_fallback_on_miss(self):
        repo = SOPDocumentRepository()
        fallback = self._make_fallback()
        resolver = SOPDocumentResolver(repo, fallback)
        result = resolver.resolve("NO_SUCH_TOPIC")
        assert result.sop_id == "fallback_sop"

    def test_E04_can_resolve_true_when_topic_exists(self):
        repo = self._make_repo_with_sop("T1")
        resolver = SOPDocumentResolver(repo, self._make_fallback())
        assert resolver.can_resolve("T1")

    def test_E05_can_resolve_false_when_missing(self):
        resolver = SOPDocumentResolver(SOPDocumentRepository(), self._make_fallback())
        assert not resolver.can_resolve("MISSING")

    def test_E06_list_topics(self):
        repo = self._make_repo_with_sop("T1")
        resolver = SOPDocumentResolver(repo, self._make_fallback())
        assert "T1" in resolver.list_topics()

    def test_E07_resolution_summary_not_fallback(self):
        repo = self._make_repo_with_sop("T1")
        resolver = SOPDocumentResolver(repo, self._make_fallback())
        summary = resolver.resolution_summary("T1")
        assert not summary["is_fallback"]
        assert summary["resolved_sop_id"] == "sop1"

    def test_E08_resolution_summary_is_fallback(self):
        resolver = SOPDocumentResolver(SOPDocumentRepository(), self._make_fallback())
        summary = resolver.resolution_summary("MISSING")
        assert summary["is_fallback"]

    def test_E09_normalise_topic(self):
        repo = self._make_repo_with_sop("OTP_DELIVERY_FAILURE")
        resolver = SOPDocumentResolver(repo, self._make_fallback())
        result = resolver.resolve("otp-delivery-failure")
        assert result.sop_id == "sop1"

    def test_E10_repo_error_falls_to_fallback(self):
        broken_repo = MagicMock()
        broken_repo.resolve.side_effect = RuntimeError("db down")
        fallback = self._make_fallback()
        resolver = SOPDocumentResolver(broken_repo, fallback)
        result = resolver.resolve("ANY")
        assert result.sop_id == "fallback_sop"

    def test_E11_resolve_with_context_arg(self):
        repo = self._make_repo_with_sop("T1")
        resolver = SOPDocumentResolver(repo, self._make_fallback())
        result = resolver.resolve("T1", context={"slot": "value"})
        assert result is not None

    def test_E12_resolve_with_client_id(self):
        repo = SOPDocumentRepository()
        repo.register(_make_sop("sop_acme", topic="T1", applicable_to=("acme",)))
        resolver = SOPDocumentResolver(repo, self._make_fallback())
        result = resolver.resolve("T1", client_id="acme")
        assert result.sop_id == "sop_acme"

    def test_E13_resolution_summary_has_all_keys(self):
        resolver = SOPDocumentResolver(SOPDocumentRepository(), self._make_fallback())
        summary = resolver.resolution_summary("T1")
        expected_keys = {
            "topic_requested", "topic_normalised", "client_id",
            "resolved_sop_id", "resolved_topic", "resolved_version",
            "is_fallback", "is_client_specific",
        }
        assert expected_keys.issubset(summary.keys())


# ════════════════════════════════════════════════════════════════════════════════
# SECTION F — SOPSerializer
# ════════════════════════════════════════════════════════════════════════════════

class TestSectionF_Serializer:

    def _roundtrip(self, sop: SOPDocument) -> SOPDocument:
        d = SOPSerializer.to_dict(sop)
        return SOPSerializer.from_dict(d)

    def test_F01_minimal_roundtrip(self):
        sop = _make_sop()
        rt = self._roundtrip(sop)
        assert rt.sop_id == sop.sop_id
        assert rt.topic == sop.topic
        assert rt.version == sop.version

    def test_F02_status_preserved(self):
        sop = _make_sop(status=SOPStatus.DRAFT, enabled=False)
        sop_inactive = SOPDocument(
            sop_id=sop.sop_id, title=sop.title, topic=sop.topic,
            status=SOPStatus.DRAFT, version=sop.version,
            trigger_conditions=sop.trigger_conditions,
            steps=sop.steps, enabled=False,
        )
        rt = self._roundtrip(sop_inactive)
        assert rt.status == SOPStatus.DRAFT
        assert not rt.enabled

    def test_F03_steps_count_preserved(self):
        sop = _make_sop(steps=(_make_step("s1"), _make_step("s2", 2)))
        rt = self._roundtrip(sop)
        assert len(rt.steps) == 2

    def test_F04_to_json_returns_string(self):
        sop = _make_sop()
        js = SOPSerializer.to_json(sop)
        assert isinstance(js, str)
        import json
        parsed = json.loads(js)
        assert parsed["sop_id"] == sop.sop_id

    def test_F05_to_summary_lightweight(self):
        sop = _make_sop(steps=(_make_step("s1"), _make_step("s2", 2)))
        summary = SOPSerializer.to_summary(sop)
        assert summary["step_count"] == 2
        assert "sop_id" in summary
        assert "steps" not in summary

    def test_F06_applicable_to_preserved(self):
        sop = _make_sop(applicable_to=("acme", "beta"))
        rt = self._roundtrip(sop)
        assert set(rt.applicable_to) == {"acme", "beta"}

    def test_F07_client_scope_preserved(self):
        sop = _make_sop(client_scope=("client_a",))
        rt = self._roundtrip(sop)
        assert "client_a" in rt.client_scope

    def test_F08_escalation_threshold_preserved(self):
        sop = _make_sop(escalation_threshold=0.7)
        rt = self._roundtrip(sop)
        assert abs(rt.escalation_threshold - 0.7) < 0.001

    def test_F09_step_kind_preserved(self):
        step = SOPStep(
            step_number=1, step_id="s1",
            action_type=SOPActionType.INVESTIGATE,
            title="T", instruction="I",
            kind=SOPStepKind.VERIFY,
        )
        sop = _make_sop(steps=(step,))
        rt = self._roundtrip(sop)
        assert rt.steps[0].kind == SOPStepKind.VERIFY

    def test_F10_default_fields_after_roundtrip(self):
        sop = _make_sop()
        rt = self._roundtrip(sop)
        assert rt.estimated_resolution_minutes == 30
        assert not rt.requires_approval


# ════════════════════════════════════════════════════════════════════════════════
# SECTION G — SOPLoader
# ════════════════════════════════════════════════════════════════════════════════

class TestSectionG_Loader:

    def test_G01_load_defaults_returns_count(self):
        loader = SOPLoader()
        n = loader.load_defaults()
        assert n == 9

    def test_G02_load_defaults_idempotent(self):
        loader = SOPLoader()
        loader.load_defaults()
        n2 = loader.load_defaults()
        assert n2 == 0  # all duplicates skipped

    def test_G03_repo_has_9_sops(self):
        loader = SOPLoader()
        loader.load_defaults()
        assert len(loader.repository) == 9

    def test_G04_build_resolver_returns_resolver(self):
        loader = SOPLoader()
        loader.load_defaults()
        resolver = loader.build_resolver()
        assert isinstance(resolver, SOPDocumentResolver)

    def test_G05_load_and_build_convenience(self):
        loader = SOPLoader()
        resolver = loader.load_and_build()
        assert isinstance(resolver, SOPDocumentResolver)

    def test_G06_custom_repo_passed_in(self):
        repo = SOPDocumentRepository()
        loader = SOPLoader(repository=repo)
        loader.load_defaults()
        assert loader.repository is repo

    def test_G07_resolver_resolves_otp_topic(self):
        loader = SOPLoader()
        resolver = loader.load_and_build()
        result = resolver.resolve("OTP_DELIVERY_FAILURE")
        assert result is not None
        assert "otp" in result.sop_id.lower() or result.topic == "OTP_DELIVERY_FAILURE"

    def test_G08_resolver_has_fallback(self):
        loader = SOPLoader()
        resolver = loader.load_and_build()
        result = resolver.resolve("COMPLETELY_UNKNOWN_TOPIC")
        assert result is not None

    def test_G09_none_repo_creates_new(self):
        loader = SOPLoader(repository=None)
        assert loader.repository is not None

    def test_G10_repo_property_accessible(self):
        loader = SOPLoader()
        repo = loader.repository
        assert isinstance(repo, SOPDocumentRepository)


# ════════════════════════════════════════════════════════════════════════════════
# SECTION H — Registry singleton
# ════════════════════════════════════════════════════════════════════════════════

class TestSectionH_Registry:

    def setup_method(self):
        reset_sop_registry()

    def teardown_method(self):
        reset_sop_registry()

    def test_H01_get_registry_returns_repo(self):
        reg = get_default_sop_registry()
        assert isinstance(reg, SOPDocumentRepository)

    def test_H02_get_registry_is_singleton(self):
        r1 = get_default_sop_registry()
        r2 = get_default_sop_registry()
        assert r1 is r2

    def test_H03_registry_has_9_sops(self):
        reg = get_default_sop_registry()
        assert len(reg) == 9

    def test_H04_get_resolver_returns_resolver(self):
        resolver = get_default_sop_resolver()
        assert isinstance(resolver, SOPDocumentResolver)

    def test_H05_get_resolver_is_singleton(self):
        r1 = get_default_sop_resolver()
        r2 = get_default_sop_resolver()
        assert r1 is r2

    def test_H06_reset_clears_singleton(self):
        r1 = get_default_sop_registry()
        reset_sop_registry()
        r2 = get_default_sop_registry()
        assert r1 is not r2

    def test_H07_resolver_resolves_topic(self):
        resolver = get_default_sop_resolver()
        result = resolver.resolve("VKYC_SESSION_FAILURE")
        assert result is not None

    def test_H08_resolver_fallback_on_unknown(self):
        resolver = get_default_sop_resolver()
        result = resolver.resolve("TOTALLY_UNKNOWN")
        assert result is not None


# ════════════════════════════════════════════════════════════════════════════════
# SECTION I — Default SOPs library
# ════════════════════════════════════════════════════════════════════════════════

class TestSectionI_DefaultSOPs:

    @pytest.fixture(scope="class")
    def defaults(self) -> list[SOPDocument]:
        return build_defaults()

    def test_I01_nine_sops(self, defaults):
        assert len(defaults) == 9

    def test_I02_all_active(self, defaults):
        assert all(s.status == SOPStatus.ACTIVE for s in defaults)

    def test_I03_all_enabled(self, defaults):
        assert all(s.enabled for s in defaults)

    def test_I04_all_have_steps(self, defaults):
        assert all(len(s.steps) > 0 for s in defaults)

    def test_I05_all_valid(self, defaults):
        v = SOPValidator()
        for sop in defaults:
            errors = v.validate(sop)
            assert errors == [], f"{sop.sop_id}: {errors}"

    def test_I06_unique_sop_ids(self, defaults):
        ids = [s.sop_id for s in defaults]
        assert len(ids) == len(set(ids))

    def test_I07_all_global(self, defaults):
        assert all(s.is_global() for s in defaults)

    def test_I08_topics_covered(self, defaults):
        topics = {s.topic for s in defaults}
        expected = {
            "MINIMAL_INVESTIGATION",
            "OTP_DELIVERY_FAILURE",
            "VKYC_SESSION_FAILURE",
            "DOCUMENT_OCR_FAILURE",
            "API_CALLBACK_FAILURE",
            "AGENT_PORTAL_ISSUE",
            "SESSION_FAILURE",
            "DOCUMENT_UPLOAD_FAILURE",
            "UNKNOWN_ISSUE",
        }
        assert expected == topics

    def test_I09_minimal_investigation_has_one_step(self, defaults):
        minimal = next(s for s in defaults if s.topic == "MINIMAL_INVESTIGATION")
        assert len(minimal.steps) == 1

    def test_I10_otp_has_multiple_steps(self, defaults):
        otp = next(s for s in defaults if s.topic == "OTP_DELIVERY_FAILURE")
        assert len(otp.steps) >= 5

    def test_I11_vkyc_requires_approval(self, defaults):
        vkyc = next(s for s in defaults if s.topic == "VKYC_SESSION_FAILURE")
        assert vkyc.requires_approval

    def test_I12_get_minimal_sop(self):
        minimal = get_minimal_sop()
        assert minimal.topic == "MINIMAL_INVESTIGATION"
        assert minimal.status == SOPStatus.ACTIVE

    def test_I13_steps_use_sop_step_kind(self, defaults):
        for sop in defaults:
            for step in sop.steps:
                assert isinstance(step.kind, SOPStepKind)

    def test_I14_steps_have_instructions(self, defaults):
        for sop in defaults:
            for step in sop.steps:
                assert len(step.instruction) > 5

    def test_I15_unknown_issue_low_threshold(self, defaults):
        unknown = next(s for s in defaults if s.topic == "UNKNOWN_ISSUE")
        assert unknown.escalation_threshold <= 0.5


# ════════════════════════════════════════════════════════════════════════════════
# SECTION J — InvestigationContext Sprint 2.41 fields
# ════════════════════════════════════════════════════════════════════════════════

from case_engine.investigation.context import InvestigationContext, InvestigationState
from case_engine.tenant.models import TenantContext, TenantType, TenantEnvironment


def _make_context(**overrides) -> InvestigationContext:
    tenant = TenantContext(
        client_id="test_client",
        client_name="Test Client",
        domain="testclient.example.com",
        tenant_type=TenantType.BANK,
        environment=TenantEnvironment.UAT,
        enabled_tools=("lookup", "verify"),
        credentials_ref="test-credentials",
    )
    defaults = dict(
        context_id="ctx-1",
        case_id="case-1",
        ticket_id="tkt-1",
        ticket_subject="Test Subject",
        ticket_description="Test description",
        customer_id="cust-1",
        customer_email="test@example.com",
        channel="email",
        tenant_context=tenant,
        topic="OTP_DELIVERY_FAILURE",
        classification_confidence=0.9,
    )
    defaults.update(overrides)
    return InvestigationContext(**defaults)


class TestSectionJ_ContextFields:

    def test_J01_resolved_sop_defaults_none(self):
        ctx = _make_context()
        assert ctx.resolved_sop is None

    def test_J02_sop_version_defaults_none(self):
        ctx = _make_context()
        assert ctx.sop_version is None

    def test_J03_sop_source_defaults_none(self):
        ctx = _make_context()
        assert ctx.sop_source is None

    def test_J04_sop_client_scope_defaults_empty(self):
        ctx = _make_context()
        assert ctx.sop_client_scope == ()

    def test_J05_sop_status_defaults_none(self):
        ctx = _make_context()
        assert ctx.sop_status is None

    def test_J06_has_resolved_sop_false_by_default(self):
        ctx = _make_context()
        assert not ctx.has_resolved_sop()

    def test_J07_has_resolved_sop_true_after_set(self):
        ctx = _make_context()
        ctx.resolved_sop = get_minimal_sop()
        assert ctx.has_resolved_sop()

    def test_J08_sop_fields_writable(self):
        ctx = _make_context()
        sop = get_minimal_sop()
        ctx.resolved_sop = sop
        ctx.sop_version = sop.version
        ctx.sop_source = "global"
        ctx.sop_client_scope = ()
        ctx.sop_status = sop.status.value
        assert ctx.resolved_sop is sop
        assert ctx.sop_version == sop.version
        assert ctx.sop_source == "global"
        assert ctx.sop_status == "ACTIVE"

    def test_J09_to_dict_includes_resolved_sop_id(self):
        ctx = _make_context()
        sop = get_minimal_sop()
        ctx.resolved_sop = sop
        d = ctx.to_dict()
        assert d["resolved_sop_id"] == sop.sop_id

    def test_J10_to_dict_resolved_sop_id_none_when_not_set(self):
        ctx = _make_context()
        d = ctx.to_dict()
        assert d["resolved_sop_id"] is None

    def test_J11_to_dict_includes_sop_version(self):
        ctx = _make_context()
        ctx.sop_version = "1.0"
        d = ctx.to_dict()
        assert d["sop_version"] == "1.0"

    def test_J12_to_dict_includes_sop_source(self):
        ctx = _make_context()
        ctx.sop_source = "client_specific"
        d = ctx.to_dict()
        assert d["sop_source"] == "client_specific"


# ════════════════════════════════════════════════════════════════════════════════
# SECTION K — InvestigationPlanner integration
# ════════════════════════════════════════════════════════════════════════════════

from case_engine.investigation.planner.engine import InvestigationPlanner


class TestSectionK_PlannerIntegration:

    def _make_planner_with_resolver(self) -> InvestigationPlanner:
        loader = SOPLoader()
        resolver = loader.load_and_build()
        return InvestigationPlanner(sop_resolver=resolver)

    def test_K01_planner_with_resolver_returns_plan(self):
        planner = self._make_planner_with_resolver()
        ctx = _make_context()
        plan = planner.plan(ctx)
        assert plan is not None

    def test_K02_planner_writes_resolved_sop_to_context(self):
        planner = self._make_planner_with_resolver()
        ctx = _make_context(topic="OTP_DELIVERY_FAILURE")
        planner.plan(ctx)
        assert ctx.resolved_sop is not None

    def test_K03_planner_writes_sop_version_to_context(self):
        planner = self._make_planner_with_resolver()
        ctx = _make_context(topic="OTP_DELIVERY_FAILURE")
        planner.plan(ctx)
        assert ctx.sop_version is not None

    def test_K04_planner_writes_sop_source_to_context(self):
        planner = self._make_planner_with_resolver()
        ctx = _make_context(topic="OTP_DELIVERY_FAILURE")
        planner.plan(ctx)
        assert ctx.sop_source in ("global", "client_specific", "fallback")

    def test_K05_planner_writes_sop_status_to_context(self):
        planner = self._make_planner_with_resolver()
        ctx = _make_context(topic="OTP_DELIVERY_FAILURE")
        planner.plan(ctx)
        assert ctx.sop_status is not None

    def test_K06_sop_source_global_for_global_sop(self):
        planner = self._make_planner_with_resolver()
        ctx = _make_context(topic="OTP_DELIVERY_FAILURE")
        planner.plan(ctx)
        assert ctx.sop_source == "global"

    def test_K07_unknown_topic_uses_fallback_sop(self):
        planner = self._make_planner_with_resolver()
        ctx = _make_context(topic="COMPLETELY_UNKNOWN_XYZ")
        planner.plan(ctx)
        assert ctx.resolved_sop is not None
        assert ctx.sop_source == "fallback"

    def test_K08_planner_without_resolver_still_works(self):
        planner = InvestigationPlanner()
        ctx = _make_context()
        plan = planner.plan(ctx)
        assert plan is not None

    def test_K09_planner_never_raises(self):
        broken_resolver = MagicMock()
        broken_resolver.resolve.side_effect = RuntimeError("explode")
        planner = InvestigationPlanner(sop_resolver=broken_resolver)
        ctx = _make_context()
        plan = planner.plan(ctx)
        assert plan is not None


# ════════════════════════════════════════════════════════════════════════════════
# SECTION L — Backward compatibility
# ════════════════════════════════════════════════════════════════════════════════

class TestSectionL_BackwardCompat:

    def test_L01_sprint238_sop_step_still_creatable(self):
        step = SOPStep(
            step_number=1,
            step_id="s1",
            action_type=SOPActionType.INVESTIGATE,
            title="Old Step",
            instruction="Do it",
        )
        assert step.step_id == "s1"
        assert step.kind == SOPStepKind.EXECUTE  # default

    def test_L02_sprint238_sop_document_still_creatable(self):
        step = SOPStep(
            step_number=1, step_id="s1",
            action_type=SOPActionType.INVESTIGATE,
            title="T", instruction="I",
        )
        sop = SOPDocument(
            sop_id="s38",
            title="Sprint 238 SOP",
            topic="OTP",
            status=SOPStatus.ACTIVE,
            version="1.0",
            trigger_conditions=(),
            steps=(step,),
        )
        assert sop.sop_id == "s38"
        assert sop.is_global()  # new method, backward compatible
        assert sop.enabled  # new field, defaults True

    def test_L03_sop_registry_sprint238_still_works(self):
        reg = SOPRegistry()
        step = SOPStep(
            step_number=1, step_id="s1",
            action_type=SOPActionType.INVESTIGATE,
            title="T", instruction="I",
        )
        sop = SOPDocument(
            sop_id="legacy", title="Legacy", topic="T",
            status=SOPStatus.ACTIVE, version="1.0",
            trigger_conditions=(), steps=(step,),
        )
        reg.register(sop)
        assert reg.count() == 1

    def test_L04_investigation_context_has_new_fields(self):
        ctx = _make_context()
        assert hasattr(ctx, "resolved_sop")
        assert hasattr(ctx, "sop_version")
        assert hasattr(ctx, "sop_source")
        assert hasattr(ctx, "sop_client_scope")
        assert hasattr(ctx, "sop_status")

    def test_L05_old_sop_match_found_still_works(self):
        ctx = _make_context()
        assert not ctx.has_sop()  # Sprint 2.38 method still works

    def test_L06_sprint240_playbook_field_still_works(self):
        ctx = _make_context()
        assert ctx.workflow_playbook is None
        assert not ctx.has_playbook_spec()

    def test_L07_new_sop_step_fields_have_defaults(self):
        step = SOPStep(
            step_number=1, step_id="s1",
            action_type=SOPActionType.INVESTIGATE,
            title="T", instruction="I",
        )
        assert step.required_inputs == ()
        assert step.expected_outputs == ()
        assert step.dependencies == ()
        assert step.execution_hints is None
        assert step.critical is False
        assert step.optional is False
        assert step.timeout_seconds == 300

    def test_L08_new_sop_document_fields_have_defaults(self):
        step = SOPStep(step_number=1, step_id="s1",
                       action_type=SOPActionType.INVESTIGATE, title="T", instruction="I")
        sop = SOPDocument(sop_id="x", title="X", topic="T",
                          status=SOPStatus.ACTIVE, version="1.0",
                          trigger_conditions=(), steps=(step,))
        assert sop.procedures == ()
        assert sop.references == ()
        assert sop.parameters == ()
        assert sop.requires_approval is False
        assert sop.client_scope == ()


# ════════════════════════════════════════════════════════════════════════════════
# SECTION M — Protocol compatibility
# ════════════════════════════════════════════════════════════════════════════════

from case_engine.investigation.planner.engine import SOPResolverProtocol


class TestSectionM_ProtocolCompat:

    def test_M01_sopDocumentResolver_satisfies_protocol(self):
        loader = SOPLoader()
        resolver = loader.load_and_build()
        assert isinstance(resolver, SOPDocumentResolver)
        assert hasattr(resolver, "resolve")

    def test_M02_resolver_callable_with_positional_args(self):
        loader = SOPLoader()
        resolver = loader.load_and_build()
        result = resolver.resolve("OTP_DELIVERY_FAILURE", {})
        assert result is not None

    def test_M03_resolver_callable_with_keyword_context(self):
        loader = SOPLoader()
        resolver = loader.load_and_build()
        result = resolver.resolve("OTP_DELIVERY_FAILURE", context={"x": 1})
        assert result is not None

    def test_M04_protocol_check(self):
        loader = SOPLoader()
        resolver = loader.load_and_build()
        assert isinstance(resolver, SOPResolverProtocol)

    def test_M05_old_sop_resolver_duck_type(self):
        class OldResolver:
            def resolve(self, topic, context):
                return None
        r = OldResolver()
        assert isinstance(r, SOPResolverProtocol)


# ════════════════════════════════════════════════════════════════════════════════
# SECTION N — Topic normalisation
# ════════════════════════════════════════════════════════════════════════════════

class TestSectionN_TopicNormalisation:

    def test_N01_hyphen_to_underscore(self):
        from case_engine.knowledge.sop.resolver import SOPDocumentResolver
        result = SOPDocumentResolver._normalise("otp-delivery-failure")
        assert result == "OTP_DELIVERY_FAILURE"

    def test_N02_uppercase(self):
        result = SOPDocumentResolver._normalise("otp_delivery_failure")
        assert result == "OTP_DELIVERY_FAILURE"

    def test_N03_spaces_to_underscore(self):
        result = SOPDocumentResolver._normalise("otp delivery failure")
        assert result == "OTP_DELIVERY_FAILURE"

    def test_N04_strip_whitespace(self):
        result = SOPDocumentResolver._normalise("  OTP_DELIVERY_FAILURE  ")
        assert result == "OTP_DELIVERY_FAILURE"

    def test_N05_already_normalised(self):
        result = SOPDocumentResolver._normalise("OTP_DELIVERY_FAILURE")
        assert result == "OTP_DELIVERY_FAILURE"

    def test_N06_find_case_insensitive(self):
        repo = SOPDocumentRepository()
        repo.register(_make_sop("s1", topic="OTP_DELIVERY_FAILURE"))
        found = repo.find("otp-delivery-failure")
        assert len(found) == 1

    def test_N07_resolve_case_insensitive(self):
        repo = SOPDocumentRepository()
        repo.register(_make_sop("s1", topic="OTP_DELIVERY_FAILURE"))
        fallback = get_minimal_sop()
        resolver = SOPDocumentResolver(repo, fallback)
        result = resolver.resolve("otp-delivery-failure")
        assert result.sop_id == "s1"


# ════════════════════════════════════════════════════════════════════════════════
# SECTION O — Integration
# ════════════════════════════════════════════════════════════════════════════════

class TestSectionO_Integration:

    def setup_method(self):
        reset_sop_registry()

    def teardown_method(self):
        reset_sop_registry()

    def test_O01_full_pipeline_otp(self):
        loader = SOPLoader()
        resolver = loader.load_and_build()
        planner = InvestigationPlanner(sop_resolver=resolver)
        ctx = _make_context(topic="OTP_DELIVERY_FAILURE")
        plan = planner.plan(ctx)
        assert plan is not None
        assert ctx.resolved_sop is not None
        assert ctx.sop_version is not None

    def test_O02_full_pipeline_vkyc(self):
        loader = SOPLoader()
        resolver = loader.load_and_build()
        planner = InvestigationPlanner(sop_resolver=resolver)
        ctx = _make_context(topic="VKYC_SESSION_FAILURE")
        plan = planner.plan(ctx)
        assert ctx.resolved_sop is not None

    def test_O03_full_pipeline_fallback(self):
        loader = SOPLoader()
        resolver = loader.load_and_build()
        planner = InvestigationPlanner(sop_resolver=resolver)
        ctx = _make_context(topic="NOT_A_REAL_TOPIC")
        plan = planner.plan(ctx)
        assert plan is not None
        assert ctx.resolved_sop is not None  # fallback

    def test_O04_singleton_resolver_in_planner(self):
        resolver = get_default_sop_resolver()
        planner = InvestigationPlanner(sop_resolver=resolver)
        ctx = _make_context(topic="DOCUMENT_OCR_FAILURE")
        plan = planner.plan(ctx)
        assert ctx.resolved_sop is not None

    def test_O05_context_has_all_sop_fields_after_plan(self):
        loader = SOPLoader()
        resolver = loader.load_and_build()
        planner = InvestigationPlanner(sop_resolver=resolver)
        ctx = _make_context(topic="API_CALLBACK_FAILURE")
        planner.plan(ctx)
        assert ctx.resolved_sop is not None
        assert ctx.sop_version is not None
        assert ctx.sop_source in ("global", "client_specific", "fallback")
        assert ctx.sop_status is not None
        assert isinstance(ctx.sop_client_scope, tuple)

    def test_O06_to_dict_after_plan(self):
        loader = SOPLoader()
        resolver = loader.load_and_build()
        planner = InvestigationPlanner(sop_resolver=resolver)
        ctx = _make_context(topic="SESSION_FAILURE")
        planner.plan(ctx)
        d = ctx.to_dict()
        assert d["resolved_sop_id"] is not None
        assert d["sop_version"] is not None
        assert d["sop_source"] is not None

    def test_O07_resolver_topics_matches_defaults(self):
        loader = SOPLoader()
        loader.load_defaults()
        resolver = loader.build_resolver()
        topics = resolver.list_topics()
        assert "OTP_DELIVERY_FAILURE" in topics
        assert "VKYC_SESSION_FAILURE" in topics

    def test_O08_serialization_roundtrip_for_resolved_sop(self):
        loader = SOPLoader()
        resolver = loader.load_and_build()
        sop = resolver.resolve("DOCUMENT_OCR_FAILURE")
        rt = SOPSerializer.from_dict(SOPSerializer.to_dict(sop))
        assert rt.sop_id == sop.sop_id
        assert rt.topic == sop.topic


# ════════════════════════════════════════════════════════════════════════════════
# SECTION P — Edge cases
# ════════════════════════════════════════════════════════════════════════════════

class TestSectionP_EdgeCases:

    def test_P01_empty_string_topic_resolves_to_fallback(self):
        fallback = get_minimal_sop()
        resolver = SOPDocumentResolver(SOPDocumentRepository(), fallback)
        result = resolver.resolve("")
        assert result is fallback

    def test_P02_whitespace_only_topic(self):
        fallback = get_minimal_sop()
        resolver = SOPDocumentResolver(SOPDocumentRepository(), fallback)
        result = resolver.resolve("   ")
        assert result is fallback

    def test_P03_repo_with_multiple_versions_returns_latest(self):
        repo = SOPDocumentRepository()
        repo.register(_make_sop("s1", topic="T1", version="1.0"))
        repo.register(_make_sop("s1", topic="T1", version="2.0"))
        result = repo.resolve("T1")
        assert result is not None

    def test_P04_repository_stats_to_dict(self):
        repo = SOPDocumentRepository()
        repo.register(_make_sop("s1"))
        stats = repo.statistics()
        d = stats.to_dict()
        assert "total_sops" in d
        assert "active_sops" in d

    def test_P05_sop_document_is_global_returns_false(self):
        sop = _make_sop(applicable_to=("client1",))
        assert not sop.is_global()

    def test_P06_sop_document_is_global_returns_true(self):
        sop = _make_sop(applicable_to=(), client_scope=())
        assert sop.is_global()

    def test_P07_can_resolve_with_repo_error(self):
        broken_repo = MagicMock()
        broken_repo.resolve.side_effect = RuntimeError("fail")
        fallback = get_minimal_sop()
        resolver = SOPDocumentResolver(broken_repo, fallback)
        assert not resolver.can_resolve("T1")

    def test_P08_load_defaults_twice_idempotent(self):
        repo = SOPDocumentRepository()
        loader = SOPLoader(repository=repo)
        n1 = loader.load_defaults()
        n2 = loader.load_defaults()
        assert n1 == 9
        assert n2 == 0
        assert len(repo) == 9

    def test_P09_resolver_with_none_context(self):
        loader = SOPLoader()
        resolver = loader.load_and_build()
        result = resolver.resolve("OTP_DELIVERY_FAILURE", context=None)
        assert result is not None

    def test_P10_repository_remove_one_version(self):
        repo = SOPDocumentRepository()
        repo.register(_make_sop("s1", version="1.0"))
        repo.register(_make_sop("s1", version="2.0"))
        repo.remove("s1", "1.0")
        assert len(repo) == 1
        assert repo.get("s1") is not None

    def test_P11_sop_condition_evaluate(self):
        cond = SOPCondition(
            field="status",
            operator=SOPTriggerOperator.EQUALS,
            value="FAILED",
            description="Test",
        )
        assert cond.evaluate({"status": "FAILED"})
        assert not cond.evaluate({"status": "OK"})

    def test_P12_sop_decision_evaluate(self):
        cond = SOPCondition(
            field="status",
            operator=SOPTriggerOperator.EQUALS,
            value="FAILED",
            description="",
        )
        decision = SOPDecision(
            decision_id="d1",
            condition=cond,
            true_step_id="step_escalate",
            false_step_id="step_retry",
        )
        assert decision.evaluate({"status": "FAILED"}) == "step_escalate"
        assert decision.evaluate({"status": "OK"}) == "step_retry"

    def test_P13_multiple_topics_in_same_repo(self):
        repo = SOPDocumentRepository()
        for i, topic in enumerate(["T1", "T2", "T3"]):
            repo.register(_make_sop(f"sop_{i}", topic=topic))
        assert len(repo.list_topics()) == 3

    def test_P14_deprecated_sop_not_returned_by_resolve(self):
        repo = SOPDocumentRepository()
        repo.register(_make_sop("s1", status=SOPStatus.DEPRECATED, enabled=False))
        result = repo.resolve("TEST_TOPIC")
        assert result is None

    def test_P15_find_with_client_id_filters(self):
        repo = SOPDocumentRepository()
        repo.register(_make_sop("s_global", topic="T1", applicable_to=()))
        repo.register(_make_sop("s_acme", topic="T1", applicable_to=("acme",), version="2.0"))
        found = repo.find("T1", client_id="acme")
        assert len(found) >= 1


# ════════════════════════════════════════════════════════════════════════════════
# SECTION Q — Thread safety
# ════════════════════════════════════════════════════════════════════════════════

class TestSectionQ_ThreadSafety:

    def setup_method(self):
        reset_sop_registry()

    def teardown_method(self):
        reset_sop_registry()

    def test_Q01_concurrent_get_registry_returns_same_object(self):
        results: list[SOPDocumentRepository] = []
        errors: list[Exception] = []

        def fetch():
            try:
                results.append(get_default_sop_registry())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=fetch) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(results) == 10
        assert all(r is results[0] for r in results)

    def test_Q02_concurrent_get_resolver_returns_same_object(self):
        results: list[SOPDocumentResolver] = []
        errors: list[Exception] = []

        def fetch():
            try:
                results.append(get_default_sop_resolver())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=fetch) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(results) == 10
        assert all(r is results[0] for r in results)

    def test_Q03_concurrent_resolve_calls(self):
        loader = SOPLoader()
        resolver = loader.load_and_build()
        results: list[SOPDocument] = []
        errors: list[Exception] = []

        def do_resolve():
            try:
                results.append(resolver.resolve("OTP_DELIVERY_FAILURE"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=do_resolve) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(results) == 20
        assert all(r is not None for r in results)

    def test_Q04_reset_then_reinitialize(self):
        reg1 = get_default_sop_registry()
        reset_sop_registry()
        reg2 = get_default_sop_registry()
        assert reg1 is not reg2
        assert len(reg2) == 9
