"""
query_router/thresholds.py

Per-category routing strategies and confidence thresholds.

Routing strategies define how retrieval behavior should change for each
query category. These are defaults — individual endpoints may override them.
"""
from __future__ import annotations

from query_router.taxonomy import IssueCategory

# ── Per-category routing strategies ───────────────────────────────────────────
# Keys match strategy fields in RoutingDecision.

ROUTING_STRATEGIES: dict[IssueCategory, dict] = {
    IssueCategory.KYC: {
        "prioritize_sop": True,         # KYC has extensive SOPs
        "top_k_override": 10,           # Fetch more — KYC context is nuanced
        "threshold_override": 0.22,     # Slightly lower — KYC vocab is specific
        "skip_retrieval": False,
        "force_escalation": False,
    },
    IssueCategory.TRANSACTION: {
        "prioritize_sop": True,         # Transaction failures have SOPs
        "top_k_override": 8,
        "threshold_override": 0.20,
        "skip_retrieval": False,
        "force_escalation": False,
    },
    IssueCategory.TECHNICAL: {
        "prioritize_sop": False,        # Technical issues → prefer past ticket resolutions
        "top_k_override": 10,
        "threshold_override": 0.20,
        "skip_retrieval": False,
        "force_escalation": False,
    },
    IssueCategory.BILLING: {
        "prioritize_sop": False,
        "top_k_override": 6,
        "threshold_override": 0.22,
        "skip_retrieval": False,
        "force_escalation": False,
    },
    IssueCategory.ONBOARDING: {
        "prioritize_sop": True,         # Onboarding is heavily SOP-driven
        "top_k_override": 8,
        "threshold_override": 0.22,
        "skip_retrieval": False,
        "force_escalation": False,
    },
    IssueCategory.SOP_LOOKUP: {
        "prioritize_sop": True,
        "top_k_override": 8,
        "threshold_override": 0.20,
        "skip_retrieval": False,
        "force_escalation": False,
    },
    IssueCategory.ESCALATION: {
        "prioritize_sop": True,
        "top_k_override": 5,
        "threshold_override": 0.25,
        "skip_retrieval": False,
        "force_escalation": True,       # Always require human review
    },
    IssueCategory.FAQ: {
        "prioritize_sop": False,
        "top_k_override": 5,
        "threshold_override": 0.20,
        "skip_retrieval": False,
        "force_escalation": False,
    },
    IssueCategory.CONVERSATIONAL: {
        "prioritize_sop": False,
        "top_k_override": 0,
        "threshold_override": None,
        "skip_retrieval": True,         # No retrieval for social chit-chat
        "force_escalation": False,
    },
    IssueCategory.SPAM: {
        "prioritize_sop": False,
        "top_k_override": 0,
        "threshold_override": None,
        "skip_retrieval": True,         # Reject spam immediately
        "force_escalation": False,
    },
}

# ── Minimum classifier confidence to trust routing ─────────────────────────────
# Below this threshold, routing strategy is still applied but the result should
# be treated as "best guess" — the retrieval layer should not hard-fail.
ROUTING_CONFIDENCE_THRESHOLD: float = 0.40

# ── Legacy helper (backward compat) ──────────────────────────────────────────

ROUTING_THRESHOLDS = {cat: 0.75 for cat in IssueCategory}
DEFAULT_THRESHOLD = 0.75


def get_threshold_for_category(category: str) -> float:
    return DEFAULT_THRESHOLD
