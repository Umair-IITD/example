"""
case_engine/investigation/root_cause/registry.py

Sprint 2.43: RuleRegistry — thread-safe registry for Root Cause Rules.

Features:
  register()             — register a RuleEvaluator by rule_id
  remove()               — remove a rule by rule_id
  resolve()              — get a rule by rule_id (raises RuleNotFoundError if missing)
  list_rules()           — sorted by priority (ascending = highest priority first)
  statistics()           — RegistryStatistics snapshot
  set_fallback()         — register a catch-all fallback rule
  has_fallback()         — True if a fallback is registered
  reset()                — clear all registrations (test isolation only)

Never returns None for rules — raises RuleNotFoundError instead.
Fallback rule is always present in build_default_registry().

Dependency direction:
  registry.py → root_cause/contracts.py (RuleEvaluator, RuleEvaluationContext)
  registry.py → root_cause/models.py (RuleMatchStatus)
  registry.py → root_cause/exceptions.py (RuleNotFoundError, RootCauseConfigurationError)
  registry.py → stdlib (threading)
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from case_engine.investigation.models import RootCauseCategory
from case_engine.investigation.root_cause.contracts import (
    RuleEvaluationContext,
    RuleEvaluator,
    RuleResult,
)
from case_engine.investigation.root_cause.exceptions import (
    RootCauseConfigurationError,
    RuleNotFoundError,
)
from case_engine.investigation.root_cause.models import (
    ConfidenceAdjustment,
    RuleMatchStatus,
)
from case_engine.investigation.root_cause.versioning import CURRENT_REGISTRY_VERSION


@dataclass(frozen=True)
class RegistryStatistics:
    """Snapshot of RuleRegistry state at a point in time."""
    total_rules:    int
    has_fallback:   bool
    rule_ids:       tuple[str, ...]
    priorities:     tuple[int, ...]
    version:        str

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_rules":  self.total_rules,
            "has_fallback": self.has_fallback,
            "rule_ids":     list(self.rule_ids),
            "priorities":   list(self.priorities),
            "version":      self.version,
        }


class RuleRegistry:
    """
    Thread-safe registry mapping rule_id → RuleEvaluator.

    Rules are stored and returned in ascending priority order
    (priority=1 is evaluated before priority=10).
    """

    def __init__(self) -> None:
        self._lock:     threading.Lock = threading.Lock()
        self._rules:    dict[str, RuleEvaluator] = {}
        self._fallback: RuleEvaluator | None = None

    def register(self, rule: RuleEvaluator) -> None:
        """Register a rule. Overwrites any existing rule with the same rule_id."""
        with self._lock:
            self._rules[rule.rule_id] = rule

    def remove(self, rule_id: str) -> None:
        """
        Remove a rule by rule_id.

        Raises RuleNotFoundError if rule_id is not registered.
        Does not affect the fallback rule.
        """
        with self._lock:
            if rule_id not in self._rules:
                raise RuleNotFoundError(rule_id)
            del self._rules[rule_id]

    def resolve(self, rule_id: str) -> RuleEvaluator:
        """
        Return the registered rule for rule_id.

        Raises RuleNotFoundError if not found.
        Does not fall back to the fallback rule — use get_ordered_rules() for that.
        """
        with self._lock:
            rule = self._rules.get(rule_id)
        if rule is None:
            raise RuleNotFoundError(rule_id)
        return rule

    def set_fallback(self, rule: RuleEvaluator) -> None:
        """Register the catch-all fallback rule."""
        with self._lock:
            self._fallback = rule

    def has_fallback(self) -> bool:
        """Return True if a fallback rule is registered."""
        with self._lock:
            return self._fallback is not None

    def get_fallback(self) -> RuleEvaluator:
        """
        Return the fallback rule.

        Raises RootCauseConfigurationError if no fallback is registered.
        """
        with self._lock:
            fb = self._fallback
        if fb is None:
            raise RootCauseConfigurationError(
                "RuleRegistry has no fallback rule. "
                "Call set_fallback() or use build_default_registry()."
            )
        return fb

    def is_registered(self, rule_id: str) -> bool:
        with self._lock:
            return rule_id in self._rules

    def list_rules(self) -> list[RuleEvaluator]:
        """Return all registered rules sorted by priority (ascending)."""
        with self._lock:
            rules = list(self._rules.values())
        return sorted(rules, key=lambda r: r.priority)

    def rule_count(self) -> int:
        with self._lock:
            return len(self._rules)

    def statistics(self) -> RegistryStatistics:
        """Return a snapshot of the registry's current state."""
        with self._lock:
            sorted_rules = sorted(self._rules.values(), key=lambda r: r.priority)
            return RegistryStatistics(
                total_rules=len(self._rules),
                has_fallback=self._fallback is not None,
                rule_ids=tuple(r.rule_id for r in sorted_rules),
                priorities=tuple(r.priority for r in sorted_rules),
                version=CURRENT_REGISTRY_VERSION,
            )

    def reset(self) -> None:
        """Clear all registrations. For test isolation only."""
        with self._lock:
            self._rules.clear()
            self._fallback = None


# ── Built-in fallback rule ─────────────────────────────────────────────────────

class _FallbackRule:
    """
    Universal fallback rule — always returns UNKNOWN with INSUFFICIENT_EVIDENCE.

    This is registered as the fallback in build_default_registry().
    It ensures the engine always produces a result even when no rule matches.
    """
    rule_id     = "fallback_unknown"
    name        = "FallbackRule"
    priority    = 9999
    description = "Catch-all fallback: returns UNKNOWN when no specific rule matches."

    def evaluate(
        self, bundle: Any, context: RuleEvaluationContext
    ) -> RuleResult:
        successful = [e for e in getattr(bundle, "items", []) if getattr(e, "success", False)]
        evidence_ids = tuple(
            getattr(e, "evidence_id", "") for e in successful
        )
        return RuleResult(
            rule_id=self.rule_id,
            status=RuleMatchStatus.UNKNOWN,
            confidence=0.25,
            evidence_ids_used=evidence_ids,
            explanation=(
                "No specific rule matched the available evidence. "
                "Root cause is indeterminate — manual review recommended."
            ),
            category_hint=RootCauseCategory.UNKNOWN,
            confidence_adjustments=(
                ConfidenceAdjustment(
                    reason="Fallback rule activated — no primary rule matched",
                    delta=-0.10,
                    component="fallback_rule",
                ),
            ),
        )


def build_default_registry() -> RuleRegistry:
    """
    Build a RuleRegistry pre-populated with all standard rules.

    Imports rules lazily to avoid circular imports at module load time.
    """
    from case_engine.investigation.root_cause.rules import (
        NetworkFailureRule,
        SessionExpiredRule,
        LivenessFailureRule,
        DocumentFailureRule,
        KycRejectedRule,
        SmsDeliveryFailureRule,
        CallbackFailureRule,
        QuotaExceededRule,
        PortalUnavailableRule,
        OnboardingBlockedRule,
        RepeatedFailureRule,
    )

    registry = RuleRegistry()

    for rule_cls in [
        NetworkFailureRule,
        SessionExpiredRule,
        LivenessFailureRule,
        DocumentFailureRule,
        KycRejectedRule,
        SmsDeliveryFailureRule,
        CallbackFailureRule,
        QuotaExceededRule,
        PortalUnavailableRule,
        OnboardingBlockedRule,
        RepeatedFailureRule,
    ]:
        registry.register(rule_cls())

    registry.set_fallback(_FallbackRule())
    return registry
