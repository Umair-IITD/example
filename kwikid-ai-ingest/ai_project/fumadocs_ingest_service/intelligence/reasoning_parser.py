"""
intelligence/reasoning_parser.py

Wave 3, Part E: Strict, schema-driven parsers for every LLM response.

The LLM never returns free text. Every response is a JSON object whose
schema is validated here. Malformed / partial payloads raise
`ReasoningParseError` — callers treat that as a low-confidence draft and
fall back to human review, never as a hard error.

Parsers
-------
    parse_reasoning_result(...)     → ReasoningResult
    parse_clarification_decision(...) → ClarificationDecision
    parse_observation_draft(...)    → ObservationDraft
    parse_customer_reply(...)       → CustomerReplyDraft
    parse_action_proposals(...)     → tuple[ActionProposal, ...]

Dependency direction: reasoning_parser.py → stdlib + intelligence.{models, exceptions}
"""
from __future__ import annotations

import json
import re
from typing import Any

from intelligence.exceptions import ReasoningParseError
from intelligence.models import (
    ActionKind,
    ActionProposal,
    ClarificationDecision,
    ConfidenceLevel,
    CustomerReplyDraft,
    ObservationDraft,
    ReasoningOutcome,
    ReasoningResult,
    RiskLevel,
)


# ── Common helpers ────────────────────────────────────────────────────────────

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*(\{.*\})\s*```$", re.DOTALL)


def _extract_json_object(raw: str) -> dict[str, Any]:
    """
    Parse a JSON object from a raw LLM response, tolerating:
      - surrounding whitespace / newlines
      - code fences (```json ... ``` or ``` ... ```) that some models emit
        despite instruction otherwise
      - leading / trailing prose before / after the JSON block

    Raises `ReasoningParseError` on any failure.
    """
    if raw is None:
        raise ReasoningParseError("raw response is None", raw_response="")
    s = str(raw).strip()
    if not s:
        raise ReasoningParseError("empty response", raw_response="")

    # 1. Try direct parse
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
        raise ReasoningParseError(
            f"expected JSON object, got {type(obj).__name__}",
            raw_response=s,
        )
    except json.JSONDecodeError:
        pass

    # 2. Strip code fences if present
    fenced = _JSON_FENCE_RE.match(s)
    if fenced:
        try:
            obj = json.loads(fenced.group(1))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    # 3. Find the first `{...}` balanced block
    start = s.find("{")
    end   = s.rfind("}")
    if start >= 0 and end > start:
        candidate = s[start : end + 1]
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    raise ReasoningParseError("could not parse JSON from LLM response",
                              raw_response=s)


def _require(obj: dict[str, Any], keys: list[str], *, raw: str) -> None:
    missing = [k for k in keys if k not in obj]
    if missing:
        raise ReasoningParseError(
            f"missing required fields: {missing}",
            raw_response=raw, missing_fields=missing,
        )


def _s(v: Any, *, default: str = "") -> str:
    if v is None:
        return default
    return str(v)


def _f(v: Any, *, default: float = 0.0, min_value: float = 0.0,
       max_value: float = 1.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        f = default
    if f < min_value:
        return min_value
    if f > max_value:
        return max_value
    return f


def _bool(v: Any, *, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in {"true", "1", "yes", "on"}
    return default


def _str_list(v: Any) -> tuple[str, ...]:
    if v is None:
        return ()
    if isinstance(v, (list, tuple)):
        return tuple(str(x)[:500] for x in v if x is not None and str(x).strip())
    if isinstance(v, str):
        return (v,) if v.strip() else ()
    return ()


# ── ReasoningResult ───────────────────────────────────────────────────────────

_REASONING_REQUIRED = [
    "outcome", "summary", "root_cause", "confidence",
    "evidence_used", "missing_information", "clarification_required",
]


def parse_reasoning_result(
    raw: str,
    *,
    prompt_version: str = "",
    llm_model: str = "",
) -> ReasoningResult:
    obj = _extract_json_object(raw)
    _require(obj, _REASONING_REQUIRED, raw=raw)

    try:
        outcome = ReasoningOutcome(str(obj["outcome"]).strip().upper())
    except (KeyError, ValueError):
        outcome = ReasoningOutcome.INSUFFICIENT_EVIDENCE

    confidence = _f(obj.get("confidence"))
    return ReasoningResult(
        outcome=              outcome,
        summary=              _s(obj.get("summary"))[:1000],
        root_cause=           _s(obj.get("root_cause"))[:1000],
        confidence=           confidence,
        confidence_level=     ConfidenceLevel.from_float(confidence),
        evidence_used=        _str_list(obj.get("evidence_used")),
        missing_information=  _str_list(obj.get("missing_information")),
        clarification_required=_bool(obj.get("clarification_required")),
        reasoning_notes=      _s(obj.get("reasoning_notes"))[:1000],
        prompt_version=       prompt_version,
        llm_model=            llm_model,
    )


# ── ClarificationDecision ────────────────────────────────────────────────────

_CLARIFICATION_REQUIRED = ["should_clarify", "questions"]


def parse_clarification_decision(raw: str) -> ClarificationDecision:
    obj = _extract_json_object(raw)
    _require(obj, _CLARIFICATION_REQUIRED, raw=raw)
    return ClarificationDecision(
        should_clarify= _bool(obj.get("should_clarify")),
        questions=      _str_list(obj.get("questions"))[:3],   # cap at 3
        reason=         _s(obj.get("reason"))[:500],
        required_slots= _str_list(obj.get("required_slots")),
    )


# ── ObservationDraft ─────────────────────────────────────────────────────────

_OBSERVATION_REQUIRED = [
    "issue_summary", "evidence", "root_cause",
    "recommended_action", "escalation", "confidence_level", "body_html",
]


def parse_observation_draft(raw: str) -> ObservationDraft:
    obj = _extract_json_object(raw)
    _require(obj, _OBSERVATION_REQUIRED, raw=raw)
    try:
        conf_level = ConfidenceLevel(str(obj.get("confidence_level")).strip().upper())
    except (KeyError, ValueError):
        conf_level = ConfidenceLevel.LOW
    return ObservationDraft(
        issue_summary=      _s(obj.get("issue_summary"))[:500],
        evidence=           _s(obj.get("evidence"))[:2000],
        root_cause=         _s(obj.get("root_cause"))[:500],
        recommended_action= _s(obj.get("recommended_action"))[:500],
        escalation=         _s(obj.get("escalation"))[:100],
        confidence_level=   conf_level,
        body_html=          _s(obj.get("body_html"))[:8000],
    )


# ── CustomerReplyDraft ───────────────────────────────────────────────────────

_REPLY_REQUIRED = ["reply_kind", "body_html", "confidence_level", "confidence"]
_VALID_REPLY_KINDS = frozenset({"resolution", "clarification", "escalation"})


def parse_customer_reply(raw: str) -> CustomerReplyDraft:
    obj = _extract_json_object(raw)
    _require(obj, _REPLY_REQUIRED, raw=raw)
    try:
        conf_level = ConfidenceLevel(str(obj.get("confidence_level")).strip().upper())
    except (KeyError, ValueError):
        conf_level = ConfidenceLevel.LOW
    kind = _s(obj.get("reply_kind")).strip().lower()
    if kind not in _VALID_REPLY_KINDS:
        kind = "clarification"
    return CustomerReplyDraft(
        reply_kind=       kind,
        body_html=        _s(obj.get("body_html"))[:8000],
        confidence_level= conf_level,
        confidence=       _f(obj.get("confidence")),
        citations=        _str_list(obj.get("citations")),
    )


# ── ActionProposal list ──────────────────────────────────────────────────────

def parse_action_proposals(raw: str) -> tuple[ActionProposal, ...]:
    obj = _extract_json_object(raw)
    proposals = obj.get("proposals")
    if not isinstance(proposals, list) or not proposals:
        raise ReasoningParseError(
            "missing / empty `proposals` list",
            raw_response=raw, missing_fields=["proposals"],
        )
    out: list[ActionProposal] = []
    for i, item in enumerate(proposals):
        if not isinstance(item, dict):
            continue
        kind = ActionKind.from_str(item.get("action_kind"))
        try:
            risk = RiskLevel(_s(item.get("risk")).strip().upper())
        except ValueError:
            risk = RiskLevel.SAFE
        params = item.get("parameters") or {}
        if not isinstance(params, dict):
            params = {}
        out.append(ActionProposal(
            action_kind=       kind,
            parameters=        params,
            confidence=        _f(item.get("confidence")),
            risk=              risk,
            approval_required= _bool(item.get("approval_required")) or (
                                risk == RiskLevel.CRITICAL
                            ),
            rationale=         _s(item.get("rationale"))[:500],
        ))
    if not out:
        raise ReasoningParseError(
            "no valid proposals could be parsed",
            raw_response=raw, missing_fields=["proposals"],
        )
    return tuple(out)
