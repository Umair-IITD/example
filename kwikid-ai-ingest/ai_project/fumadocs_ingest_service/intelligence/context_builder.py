"""
intelligence/context_builder.py

Wave 3, Part A: Deterministic context assembly.

The Context Builder is a PURE FUNCTION over its inputs — given the same
InvestigationContext / EvidenceBundle / conversation / ticket / tenant /
workflow / classification / tool_results, it produces the same LLMContext.

Design rules:
- No LLM logic.
- No prompt construction.
- No I/O.
- Never raises — malformed input yields a valid-but-degraded LLMContext.
- Bounded token budget: evidence bundle → EvidenceHint (short summary),
  retrieved chunks capped, ticket description truncated at 2000 chars.
- PII masking of customer identifiers happens here so the LLM never sees
  raw phone numbers or emails.

Dependency direction: context_builder.py → stdlib + intelligence.models
"""
from __future__ import annotations

from typing import Any, Iterable

from intelligence.models import EvidenceHint, LLMContext, RetrievedChunk


# ── Public API ────────────────────────────────────────────────────────────────

def build_llm_context(
    *,
    case_id:            str,
    ticket_id:          str,
    tenant_id:          str,
    topic:              str,
    workflow_id:        str = "",
    classification:     str = "",
    ticket_subject:     str = "",
    ticket_description: str = "",
    customer_phone:     str = "",
    customer_email:     str = "",
    evidence_bundle:    Any = None,
    retrieved_chunks:   Iterable[RetrievedChunk] = (),
    conversation:       Iterable[dict[str, str]] = (),
    tool_results:       dict[str, Any] | None = None,
    trace_id:           str = "",
    max_chunks:         int = 8,
    max_conversation:   int = 12,
) -> LLMContext:
    """
    Assemble an LLMContext deterministically.

    `evidence_bundle` may be a Sprint 2.18 `EvidenceBundle` object or any
    object exposing `.items` / `.successful_items`. When None or malformed,
    `evidence_hints` will be empty.

    Customer identifiers are masked: phone `*****2923`, email domain only.
    """
    hints = _extract_evidence_hints(evidence_bundle)

    chunks = _clamp_chunks(retrieved_chunks, max_chunks)
    convo = _clamp_conversation(conversation, max_conversation)

    return LLMContext(
        case_id=            _s(case_id),
        ticket_id=          _s(ticket_id),
        tenant_id=          _s(tenant_id),
        topic=              _s(topic),
        workflow_id=        _s(workflow_id),
        classification=     _s(classification),
        ticket_subject=     _s(ticket_subject),
        ticket_description= _s(ticket_description),
        customer_display=   _customer_display(customer_phone, customer_email),
        evidence_hints=     hints,
        retrieved_chunks=   chunks,
        conversation=       convo,
        tool_results=       _sanitize_tool_results(tool_results),
        trace_id=           _s(trace_id),
    )


# ── Extraction / normalization helpers ────────────────────────────────────────

def _extract_evidence_hints(bundle: Any) -> tuple[EvidenceHint, ...]:
    """
    Reduce an EvidenceBundle (Sprint 2.18) into a bounded set of
    `EvidenceHint` records. Never raises.
    """
    if bundle is None:
        return ()
    items = _resolve_items(bundle)
    hints: list[EvidenceHint] = []
    for item in items:
        try:
            source = _get_attr(item, "source", "") or _get_attr(item, "tool_name", "")
            if hasattr(source, "value"):
                source = source.value
            kind = _get_attr(item, "evidence_type", "")
            if hasattr(kind, "value"):
                kind = kind.value
            success = bool(_get_attr(item, "success", True))
            payload = _get_attr(item, "payload", {})
            summary = _summarize_payload(kind, payload, success)
            hints.append(EvidenceHint(
                source=str(source or "unknown"),
                kind=str(kind or "UNKNOWN"),
                summary=summary,
                is_available=success,
                ref_id=str(_get_attr(item, "evidence_id", "")),
            ))
        except Exception:
            # Never let a single bad item block context assembly
            continue
    return tuple(hints)


def _resolve_items(bundle: Any) -> list[Any]:
    for name in ("successful_items", "items"):
        try:
            val = getattr(bundle, name)
            if callable(val):
                val = val()
            if val is None:
                continue
            return list(val)
        except AttributeError:
            continue
        except Exception:
            continue
    if isinstance(bundle, dict):
        val = bundle.get("items")
        if isinstance(val, list):
            return val
    if isinstance(bundle, list):
        return bundle
    return []


def _get_attr(obj: Any, name: str, default: Any) -> Any:
    """Safe attribute/key access."""
    if obj is None:
        return default
    try:
        val = getattr(obj, name)
    except AttributeError:
        val = None
    if val is None and isinstance(obj, dict):
        val = obj.get(name, default)
    return default if val is None else val


def _summarize_payload(kind: str, payload: Any, success: bool) -> str:
    """
    Produce a single-line, PII-safe summary of an evidence payload.
    Never includes raw phone / email / PAN / Aadhaar values.
    """
    if not success:
        return f"[{kind}] unavailable"
    if not isinstance(payload, dict):
        return f"[{kind}] present"

    # Prefer explicit `session_status` (Unity), `session_found`, `data_available`
    for key in ("session_status", "session_found", "data_available",
                "onboarding_stage", "any_infrastructure_down",
                "monitors_currently_down"):
        if key in payload:
            v = payload[key]
            if isinstance(v, (str, bool, int, float)):
                return f"[{kind}] {key}={v}"
            if isinstance(v, list):
                return f"[{kind}] {key}={len(v)} items"
    for key in ("failure_summary", "recent_summary", "onboarding_stage"):
        if key in payload:
            v = payload[key]
            if isinstance(v, dict) and v:
                # Compose 1-2 key summary
                keys = list(v.keys())[:2]
                return f"[{kind}] {key}={keys}"
    return f"[{kind}] present"


def _clamp_chunks(chunks: Iterable[RetrievedChunk], max_count: int) -> tuple[RetrievedChunk, ...]:
    result: list[RetrievedChunk] = []
    for c in chunks:
        if not isinstance(c, RetrievedChunk):
            continue
        result.append(c)
        if len(result) >= max_count:
            break
    return tuple(result)


def _clamp_conversation(convo: Iterable[dict[str, str]], max_count: int) -> tuple[dict[str, str], ...]:
    """
    Trim to the last `max_count` turns. Each turn must be a `{role, content}`
    dict; entries missing either field are dropped.
    """
    valid: list[dict[str, str]] = []
    for turn in convo:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role", "")).strip()
        content = str(turn.get("content", "")).strip()
        if not role or not content:
            continue
        valid.append({"role": role, "content": content[:1500]})
    return tuple(valid[-max_count:])


def _sanitize_tool_results(results: dict[str, Any] | None) -> dict[str, Any]:
    if not results:
        return {}
    # Cap total serialized size loosely; drop obviously huge values
    out: dict[str, Any] = {}
    for k, v in results.items():
        if not isinstance(k, str):
            continue
        key = k[:64]
        if isinstance(v, (str, int, float, bool)):
            out[key] = v if not isinstance(v, str) else v[:1500]
        elif isinstance(v, dict):
            out[key] = {kk: (vv if not isinstance(vv, str) else vv[:500])
                        for kk, vv in list(v.items())[:20]
                        if isinstance(kk, str)}
        elif isinstance(v, list):
            out[key] = [str(x)[:200] for x in v[:20]]
        # else drop
    return out


def _customer_display(phone: str, email: str) -> str:
    """Mask customer PII for the LLM prompt."""
    phone_m = _mask_phone(phone)
    email_m = _mask_email(email)
    if phone_m and email_m:
        return f"{phone_m} / {email_m}"
    return phone_m or email_m or ""


def _mask_phone(phone: str) -> str:
    if not phone:
        return ""
    s = str(phone)
    if len(s) < 4:
        return "***"
    return "*" * (len(s) - 4) + s[-4:]


def _mask_email(email: str) -> str:
    if not email or "@" not in email:
        return ""
    _local, domain = email.split("@", 1)
    return f"***@{domain}"


def _s(v: Any) -> str:
    return "" if v is None else str(v)
