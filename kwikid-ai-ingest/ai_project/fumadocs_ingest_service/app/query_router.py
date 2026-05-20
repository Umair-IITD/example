"""
app/query_router.py

Lightweight keyword-based query router for the B1 RAG pipeline.

Classifies incoming support queries into one of 8 routes based on lexical
signal matching — no ML inference, zero latency overhead, fully deterministic.

Routes (in priority order):
  ESCALATION          — high-urgency, requires_human signals
  RCA                 — root-cause analysis requests
  SOP                 — procedure / step-by-step requests
  POLICY_COMPLIANCE   — regulatory / KYC / compliance queries
  TECHNICAL_ERROR     — code-level / API / system errors
  BILLING_ACCOUNT     — payment, account, fee queries
  TROUBLESHOOTING     — general error / fix / not-working queries
  GENERAL_KNOWLEDGE   — fallback (no strong signal matched)

Integration in /rag/chat (gated by ENABLE_QUERY_ROUTER=true):
  router = QueryRouter()
  result = router.classify(query_text)
  # result.route, result.confidence, result.retrieval_strategy, result.matched_signals

The retrieval_strategy dict exposes hints for callers:
  boost_sop   — prefer SOP chunks in ranking
  boost_knowledge — prefer KNOWLEDGE chunks
  requires_human_review — flag for ESCALATION route
  chunk_types_hint — suggested chunk type filter (advisory, not enforced)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

# Master switch — set ENABLE_QUERY_ROUTER=true in .env to activate
QUERY_ROUTER_ENABLED: bool = os.getenv("ENABLE_QUERY_ROUTER", "false").strip().lower() in {
    "1", "true", "yes", "on"
}


# ── Route definitions ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RouteDefinition:
    name: str
    signals: tuple[str, ...]        # lowercase substring patterns to match
    priority: int                   # lower = checked first
    retrieval_strategy: dict        # hints for the retrieval pipeline


_ROUTES: list[RouteDefinition] = [
    RouteDefinition(
        name="ESCALATION",
        signals=(
            "escalat", "supervisor", "manager", "urgent complaint",
            "legal action", "regulatory authority", "ombudsman",
            "sue", "fraud complaint", "police", "criminal",
        ),
        priority=1,
        retrieval_strategy={
            "requires_human_review": True,
            "boost_sop": False,
            "boost_knowledge": False,
            "chunk_types_hint": ["QUERY_BODY"],
        },
    ),
    RouteDefinition(
        name="RCA",
        signals=(
            "root cause", "why did", "what caused", "reason for failure",
            "caused by", "incident analysis", "post mortem", "postmortem",
            "investigation report", " rca ", "failure analysis",
        ),
        priority=2,
        retrieval_strategy={
            "requires_human_review": False,
            "boost_sop": False,
            "boost_knowledge": True,
            "chunk_types_hint": ["RCA_SUMMARY", "QUERY_BODY"],
        },
    ),
    RouteDefinition(
        name="SOP",
        signals=(
            "what are the steps", "step by step", "how do i", "how to",
            "procedure for", "process to", "guide to", "walkthrough",
            "instructions for", "sop for", "workflow for",
            "show me the process", "what is the procedure",
        ),
        priority=3,
        retrieval_strategy={
            "requires_human_review": False,
            "boost_sop": True,
            "boost_knowledge": False,
            "chunk_types_hint": ["SOP_STEPS"],
        },
    ),
    RouteDefinition(
        name="POLICY_COMPLIANCE",
        signals=(
            "kyc", "know your customer", "aml", "anti-money laundering",
            "compliance", "regulation", "regulatory", "rbi guideline",
            "gdpr", "data protection", "privacy policy", "legal requirement",
            "audit", "vkyc", "video kyc", "ckyc",
        ),
        priority=4,
        retrieval_strategy={
            "requires_human_review": False,
            "boost_sop": False,
            "boost_knowledge": True,
            "chunk_types_hint": ["KNOWLEDGE", "SOP_STEPS"],
        },
    ),
    RouteDefinition(
        name="TECHNICAL_ERROR",
        signals=(
            "error code", "http 5", "http 4", "api error", "api failure",
            "timeout error", "connection refused", "stack trace", "exception",
            "null pointer", "500 error", "503 error", "404 not found",
            "integration error", "webhook failed", "callback failed",
        ),
        priority=5,
        retrieval_strategy={
            "requires_human_review": False,
            "boost_sop": True,
            "boost_knowledge": True,
            "chunk_types_hint": ["QUERY_BODY", "SOP_STEPS"],
        },
    ),
    RouteDefinition(
        name="BILLING_ACCOUNT",
        signals=(
            "billing", "invoice", "payment failed", "charge", "refund",
            "debit", "credit reversal", "account block", "account frozen",
            "transaction fee", "emi", "loan repayment", "neft", "rtgs", "imps",
        ),
        priority=6,
        retrieval_strategy={
            "requires_human_review": False,
            "boost_sop": False,
            "boost_knowledge": False,
            "chunk_types_hint": ["QUERY_BODY", "KNOWLEDGE"],
        },
    ),
    RouteDefinition(
        name="TROUBLESHOOTING",
        signals=(
            "not working", "doesn't work", "does not work", "not able to",
            "unable to", "cannot", "can't", "issue with", "problem with",
            "error", "failed", "failure", "broken", "fix", "resolve", "stuck",
        ),
        priority=7,
        retrieval_strategy={
            "requires_human_review": False,
            "boost_sop": True,
            "boost_knowledge": False,
            "chunk_types_hint": ["QUERY_BODY", "SOP_STEPS"],
        },
    ),
    RouteDefinition(
        name="GENERAL_KNOWLEDGE",
        signals=(),      # fallback — always matches with confidence 0.0
        priority=99,
        retrieval_strategy={
            "requires_human_review": False,
            "boost_sop": False,
            "boost_knowledge": True,
            "chunk_types_hint": ["QUERY_BODY", "KNOWLEDGE"],
        },
    ),
]

# Sort once at import time for deterministic priority ordering
_SORTED_ROUTES = sorted(_ROUTES, key=lambda r: r.priority)


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class RouteResult:
    route: str
    confidence: float                   # [0.0, 1.0] — signal match ratio
    matched_signals: list[str]          # which patterns were matched
    retrieval_strategy: dict            # hints for the retrieval pipeline
    router_enabled: bool = True         # False when ENABLE_QUERY_ROUTER=false

    @property
    def is_escalation(self) -> bool:
        return self.route == "ESCALATION"

    @property
    def boost_sop(self) -> bool:
        return bool(self.retrieval_strategy.get("boost_sop"))

    @property
    def boost_knowledge(self) -> bool:
        return bool(self.retrieval_strategy.get("boost_knowledge"))

    def to_dict(self) -> dict:
        return {
            "route": self.route,
            "confidence": round(self.confidence, 4),
            "matched_signals": self.matched_signals,
            "retrieval_strategy": self.retrieval_strategy,
        }


_DISABLED_RESULT = RouteResult(
    route="GENERAL_KNOWLEDGE",
    confidence=0.0,
    matched_signals=[],
    retrieval_strategy=_ROUTES[-1].retrieval_strategy,
    router_enabled=False,
)


# ── Classifier ────────────────────────────────────────────────────────────────

class QueryRouter:
    """
    Keyword-based query classifier.

    Thread-safe (stateless after construction). Intended to be instantiated
    once per process and reused across requests.

    Usage:
        router = QueryRouter()
        result = router.classify("Customer cannot complete KYC verification")
        # RouteResult(route='POLICY_COMPLIANCE', confidence=0.5, ...)
    """

    def __init__(self) -> None:
        self._enabled = QUERY_ROUTER_ENABLED

    def classify(self, query_text: str) -> RouteResult:
        if not self._enabled:
            return _DISABLED_RESULT

        normalized = query_text.lower()

        for route in _SORTED_ROUTES:
            if not route.signals:
                # Fallback route — always matches with 0.0 confidence
                return RouteResult(
                    route=route.name,
                    confidence=0.0,
                    matched_signals=[],
                    retrieval_strategy=route.retrieval_strategy,
                )

            matched = [sig for sig in route.signals if sig in normalized]
            if matched:
                confidence = min(1.0, len(matched) / max(1, len(route.signals) // 2))
                return RouteResult(
                    route=route.name,
                    confidence=round(confidence, 4),
                    matched_signals=matched,
                    retrieval_strategy=route.retrieval_strategy,
                )

        # Should never reach here because GENERAL_KNOWLEDGE has no signals (always matches)
        return _DISABLED_RESULT
