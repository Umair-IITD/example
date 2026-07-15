"""
freshdesk/safety_gate.py

Sprint 2.48: ReplySafetyGate — final safety boundary for irreversible
customer-facing writes to Freshdesk.

Source of truth
---------------
Freshdesk_discovery/notes_and_replies.md Sections 1 & 4:
    "Calling POST /reply sends an email to the customer immediately and
     cannot be undone. There is no draft endpoint."

Freshdesk_discovery/workflow_discovery.md Section 10 + observations.md:
    Every reply call must pass:
      - Safety Guardrails confidence check
      - Explicit Action Gateway approval
      - Idempotency protection

Blueprint SUPPORT_OPERATIONS_BLUEPRINT.md Section 24 & 29 (Enterprise
Safety Principles): "no component bypasses Action Gateway."

Responsibilities
----------------
The safety gate wraps FreshdeskResponseService.send_customer_reply() so that:
  1. Confidence must meet the configured threshold (default: 0.75).
  2. High-risk impact values force escalation-only (never autonomous reply).
  3. An idempotency key (ticket_id + intent + reply_hash) prevents double sends.
  4. Every rejection is auditable — nothing silently swallowed.

When the gate rejects a reply, the caller is expected to fall back to
add_internal_note() with a draft note (see freshdesk/templates.build_draft_reply_note).

Design rules
------------
- Never raises to the caller — returns a GateDecision.
- Never sends the reply itself — that is FreshdeskResponseService's job.
- Reads no environment / config — thresholds are injected at construction.

Dependency direction
--------------------
    safety_gate.py → stdlib only
    safety_gate.py → NO imports from case_engine/, api/, freshdesk client
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

LOGGER = logging.getLogger(__name__)

# ── Constants (SOT) ────────────────────────────────────────────────────────────

# Default confidence threshold below which autonomous reply is blocked.
# Source: observations.md Section 5 confidence gate discussion.
DEFAULT_CONFIDENCE_THRESHOLD = 0.75

# cf_impact values that MUST NOT trigger an autonomous customer reply.
# Source: observations.md Section 2.2 escalation criteria.
FORCE_ESCALATION_IMPACT_VALUES: frozenset[str] = frozenset({
    "DOWNTIME 100% impact",
    "Client Escalation",
})

# Retention window for the in-memory idempotency cache.
_IDEMPOTENCY_TTL_SECONDS = 24 * 3600


class GateOutcome(str, Enum):
    ALLOW              = "ALLOW"
    BLOCK_CONFIDENCE   = "BLOCK_CONFIDENCE"
    BLOCK_IMPACT       = "BLOCK_IMPACT"
    BLOCK_DUPLICATE    = "BLOCK_DUPLICATE"
    BLOCK_MISSING_BODY = "BLOCK_MISSING_BODY"
    BLOCK_KILL_SWITCH  = "BLOCK_KILL_SWITCH"


@dataclass(frozen=True)
class GateDecision:
    outcome:      GateOutcome
    reason:       str
    reply_hash:   str = ""
    confidence:   float | None = None
    should_draft: bool = False    # True when caller should post a draft note instead

    @property
    def allowed(self) -> bool:
        return self.outcome == GateOutcome.ALLOW


class ReplySafetyGate:
    """
    Enterprise safety gate for POST /reply calls.

    Usage inside the Execution Layer:

        gate = ReplySafetyGate(confidence_threshold=0.75)
        decision = gate.check(
            ticket_id="197416",
            body_html=reply_body,
            confidence=0.87,
            impact="",
        )
        if decision.allowed:
            await response_service.send_customer_reply(ticket_id, reply_body, ...)
        else:
            # Post as a draft note instead
            draft = build_draft_reply_note(reply_body, reason_not_auto_sent=decision.reason)
            await response_service.add_internal_note(ticket_id, draft, ...)
    """

    def __init__(
        self,
        *,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        force_escalation_impacts: Iterable[str] = FORCE_ESCALATION_IMPACT_VALUES,
        kill_switch: bool = False,
        ttl_seconds: int = _IDEMPOTENCY_TTL_SECONDS,
    ) -> None:
        if confidence_threshold < 0.0 or confidence_threshold > 1.0:
            raise ValueError(
                f"confidence_threshold must be in [0.0, 1.0], got {confidence_threshold}"
            )
        self._threshold = confidence_threshold
        self._force_escalation = frozenset(force_escalation_impacts)
        self._kill_switch = kill_switch
        self._ttl = ttl_seconds
        # {reply_hash: monotonic_timestamp}
        self._seen: dict[str, float] = {}

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def confidence_threshold(self) -> float:
        return self._threshold

    def check(
        self,
        *,
        ticket_id: str,
        body_html: str,
        confidence: float | None,
        impact: str | None = None,
    ) -> GateDecision:
        """
        Evaluate the gate for a single POST /reply intent.

        Order of checks:
          1. Global kill switch
          2. Empty / whitespace-only body
          3. Impact value on force-escalation list
          4. Confidence below threshold
          5. Idempotency (same ticket + body hash within TTL window)
        """
        reply_hash = _hash_reply(ticket_id, body_html)

        if self._kill_switch:
            return GateDecision(
                outcome=GateOutcome.BLOCK_KILL_SWITCH,
                reason="reply kill switch is engaged",
                reply_hash=reply_hash,
                confidence=confidence,
                should_draft=True,
            )

        if not (body_html and body_html.strip()):
            return GateDecision(
                outcome=GateOutcome.BLOCK_MISSING_BODY,
                reason="reply body is empty",
                reply_hash=reply_hash,
                confidence=confidence,
                should_draft=False,
            )

        if impact and impact in self._force_escalation:
            return GateDecision(
                outcome=GateOutcome.BLOCK_IMPACT,
                reason=(
                    f"cf_impact={impact!r} requires engineering escalation, "
                    "not autonomous reply"
                ),
                reply_hash=reply_hash,
                confidence=confidence,
                should_draft=True,
            )

        if confidence is None or confidence < self._threshold:
            conf_str = "unknown" if confidence is None else f"{confidence:.2f}"
            return GateDecision(
                outcome=GateOutcome.BLOCK_CONFIDENCE,
                reason=(
                    f"confidence {conf_str} is below auto-send threshold "
                    f"{self._threshold:.2f}"
                ),
                reply_hash=reply_hash,
                confidence=confidence,
                should_draft=True,
            )

        if self._is_duplicate(reply_hash):
            return GateDecision(
                outcome=GateOutcome.BLOCK_DUPLICATE,
                reason="identical reply for this ticket was already sent within TTL",
                reply_hash=reply_hash,
                confidence=confidence,
                should_draft=False,
            )

        # All checks pass — record and allow.
        self._remember(reply_hash)
        return GateDecision(
            outcome=GateOutcome.ALLOW,
            reason="OK",
            reply_hash=reply_hash,
            confidence=confidence,
            should_draft=False,
        )

    def engage_kill_switch(self) -> None:
        """Disable all autonomous replies until release_kill_switch() is called."""
        self._kill_switch = True
        LOGGER.warning("freshdesk.safety_gate: kill switch ENGAGED — all replies blocked")

    def release_kill_switch(self) -> None:
        """Re-enable autonomous replies subject to the normal gate checks."""
        self._kill_switch = False
        LOGGER.warning("freshdesk.safety_gate: kill switch RELEASED")

    def reset_idempotency(self) -> None:
        """Clear the in-memory idempotency cache (test helper / operational reset)."""
        self._seen.clear()

    # ── Internal ───────────────────────────────────────────────────────────────

    def _remember(self, reply_hash: str) -> None:
        self._evict()
        self._seen[reply_hash] = time.monotonic()

    def _is_duplicate(self, reply_hash: str) -> bool:
        self._evict()
        return reply_hash in self._seen

    def _evict(self) -> None:
        cutoff = time.monotonic() - self._ttl
        expired = [h for h, ts in self._seen.items() if ts < cutoff]
        for h in expired:
            del self._seen[h]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hash_reply(ticket_id: str, body_html: str) -> str:
    """Deterministic hash of (ticket_id, normalized_body) — never contains PII."""
    normalized = " ".join((body_html or "").split())
    digest = hashlib.sha256()
    digest.update(str(ticket_id).encode("utf-8"))
    digest.update(b"|")
    digest.update(normalized.encode("utf-8"))
    return digest.hexdigest()
