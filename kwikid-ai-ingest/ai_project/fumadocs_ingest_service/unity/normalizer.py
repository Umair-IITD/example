"""
unity/normalizer.py

Sprint 2.51: Parse raw Unity API JSON into canonical `UnitySession`,
`SessionList`, and `UnityEvidence` models.

Pure functions — no I/O, no exceptions. Malformed input yields a partial
UnitySession with as many fields as could be populated.

Dependency direction: normalizer.py → stdlib + unity.models
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable

from unity.models import (
    AgentInfo,
    AuditorInfo,
    CustomerInfo,
    EvidenceAvailability,
    FeedbackInfo,
    JourneyStep,
    MediaInfo,
    MetadataInfo,
    QnA,
    SessionList,
    SessionStatus,
    TimelineInfo,
    UnityEvidence,
    UnitySession,
    VerificationDoc,
)

LOGGER = logging.getLogger(__name__)


# ── Timestamp helper ─────────────────────────────────────────────────────────

def _iso_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _to_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _to_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── UnitySession parsing ─────────────────────────────────────────────────────

def parse_session(raw: dict[str, Any]) -> UnitySession:
    """
    Convert one raw session dict (from getAllUserSession[i] or
    get_details.session_data) into a canonical UnitySession.

    Robust to missing fields — every optional accessor uses safe defaults.
    """
    if not isinstance(raw, dict):
        return UnitySession(session_id="", session_status=SessionStatus.UNKNOWN)

    session_id = _to_str(raw.get("session_id"))
    status = SessionStatus.from_str(raw.get("session_status"))

    customer = CustomerInfo(
        phone_number=_to_str(raw.get("phone_number")),
        user_id=     _to_str(raw.get("user_id")),
    )
    agent = AgentInfo(
        agent_id=_to_str(raw.get("agent_id")),
        region=  _to_str(raw.get("agent_region")),
        tl=      _to_str(raw.get("agent_tl")),
    )
    auditor = AuditorInfo(
        auditor_name=_to_str(raw.get("auditor_name")),
        result=      _to_str(raw.get("audit_result")),
        feedback=    _to_str(raw.get("auditor_feedback")),
        fdbk_code=   _to_str(raw.get("auditor_fdbk_code")),
        lock=        _to_bool(raw.get("audit_lock")),
        init_time=   _to_str(raw.get("audit_init_time")),
        end_time=    _to_str(raw.get("audit_end_time")),
    )
    feedback = FeedbackInfo.from_str(raw.get("feedback"))
    timeline = TimelineInfo(
        init_time=                   _to_float(raw.get("init_time")),
        start_time=                  _to_float(raw.get("start_time")),
        end_time=                    _to_float(raw.get("end_time")),
        vkyc_start_time=             _to_float(raw.get("vkyc_start_time")),
        last_active_timestamp=       _to_float(raw.get("last_active_timestamp")),
        agent_assignment_time=       _to_float(raw.get("agent_assignment_time")),
        removed_from_queue_timestamp=_to_float(raw.get("removed_from_queue_timestamp")),
    )
    metadata = MetadataInfo(
        session_type=          _to_str(raw.get("session_type")),
        product_code=          _to_str(raw.get("productCode") or raw.get("product_code")),
        lang=                  _to_str(raw.get("lang")),
        stage=                 _to_str(raw.get("stage")),
        stage1_valid=          _to_bool(raw.get("stage1_valid")),
        is_video_uploaded=     _to_bool(raw.get("IS_VIDO_UPLOADED") or raw.get("is_video_uploaded")),
        app_version_number=    _to_str(raw.get("app_version_number")),
        link_id=               _to_str(raw.get("link_id")),
        device_id=             _to_str(raw.get("device_id")),
        queue=                 _to_str(raw.get("queue")),
        queue_id=              _to_str(raw.get("queue_id")),
        queue_mode=            _to_str(raw.get("queue_mode")),
        queue_position_at_init=_to_str(raw.get("queue_position_at_init")),
        inserted_in_queue_at=  _to_str(raw.get("inserted_in_queue_at")),
    )
    media = MediaInfo(
        agent_video_url= _to_str(raw.get("agent_video_url")),
        agent_screen_url=_to_str(raw.get("agent_screen_url")),
        user_video_url=  _to_str(raw.get("user_video_url")),
        selfie_url=      _to_str(raw.get("selfie_url")),
        pan_url=         _to_str(raw.get("pan_url")),
        aadhaar_pdf_url= _to_str(raw.get("aadhaarpdf_url") or raw.get("aadhaar_pdf_url")),
        aadhaar_xml_url= _to_str(raw.get("aadhaar_xml")),
        summary_pdf_url= _to_str(raw.get("summary_pdf_url")),
        summary_json_url=_to_str(raw.get("summary_json_url")),
        zip_url=         _to_str(raw.get("zip_url")),
        video_duration=  _to_str(raw.get("video_duration")),
    )

    # summary_data is already-parsed in get_details; absent in getAllUserSession.
    summary_data = raw.get("summary_data") or {}
    overall_summary: tuple[JourneyStep, ...] = ()
    qna: tuple[QnA, ...] = ()
    docs: tuple[VerificationDoc, ...] = ()
    if isinstance(summary_data, dict):
        overall_summary = tuple(
            JourneyStep(title=_to_str(s.get("title")), success=_to_bool(s.get("success")))
            for s in (summary_data.get("overall_summary") or [])
            if isinstance(s, dict)
        )
        qna = tuple(
            QnA(q=_to_str(q.get("q")), a=_to_str(q.get("a")))
            for q in (summary_data.get("qna") or [])
            if isinstance(q, dict)
        )
        docs = tuple(_parse_doc(d) for d in (summary_data.get("docs") or [])
                     if isinstance(d, dict))

    audit_id = _to_str(raw.get("audit_id"))
    client_name = _to_str(raw.get("client_name"))

    return UnitySession(
        session_id=      session_id,
        session_status=  status,
        client_name=     client_name,
        customer=        customer,
        agent=           agent,
        auditor=         auditor,
        feedback=        feedback,
        timeline=        timeline,
        metadata=        metadata,
        media=           media,
        overall_summary= overall_summary,
        qna=             qna,
        docs=            docs,
        audit_id=        audit_id,
    )


def _parse_doc(raw: dict[str, Any]) -> VerificationDoc:
    facematch = raw.get("facematch_score") or {}
    fm_score = None
    if isinstance(facematch, dict):
        # SOT: {"selfie_pan_match": 98}
        for _k, v in facematch.items():
            fm_score = _to_int_or_none(v)
            if fm_score is not None:
                break
    validator = raw.get("validator") or {}
    validator_summary = ""
    if isinstance(validator, dict):
        keys = sorted(validator.keys())
        validator_summary = ",".join(keys[:5])
    return VerificationDoc(
        name=              _to_str(raw.get("name")),
        facematch_score=   fm_score,
        validator_summary= validator_summary,
    )


# ── SessionList parsing ──────────────────────────────────────────────────────

def parse_session_list(
    raw: dict[str, Any],
    *,
    phone_number: str,
) -> SessionList:
    session_count = int(raw.get("session_count") or 0)
    raw_list = raw.get("session_list") or []
    if not isinstance(raw_list, list):
        raw_list = []
    sessions = tuple(parse_session(s) for s in raw_list if isinstance(s, dict))
    return SessionList(
        phone_number=phone_number,
        session_count=session_count,
        sessions=sessions,
        fetched_at=_iso_now(),
    )


def parse_session_details(raw: dict[str, Any]) -> UnitySession | None:
    """
    Parse a GET /v1/session/get_details/{id} response into a UnitySession.
    Returns None if the body has no session_data.
    """
    if not isinstance(raw, dict):
        return None
    session_data = raw.get("session_data")
    if not isinstance(session_data, dict):
        return None
    return parse_session(session_data)


# ── UnityEvidence builders ───────────────────────────────────────────────────

def build_unity_evidence(
    *,
    tool_name:          str,
    session:            UnitySession | None = None,
    all_sessions:       Iterable[UnitySession] = (),
    all_session_count:  int = 0,
    session_found:      bool = False,
    availability:       EvidenceAvailability = EvidenceAvailability.AVAILABLE,
    error:              str = "",
    error_code:         str = "",
    tenant_id:          str = "",
    trace_id:           str = "",
    case_id:            str = "",
    query_phone_number: str = "",
    query_session_id:   str = "",
    failure_summary:    dict[str, Any] | None = None,
    recent_summary:     dict[str, Any] | None = None,
    onboarding_stage:   str = "",
) -> UnityEvidence:
    return UnityEvidence(
        tool_name=          tool_name,
        collected_at=       _iso_now(),
        evidence_available= availability,
        error=              error,
        error_code=         error_code,
        session_found=      session_found,
        session=            session,
        all_session_count=  all_session_count,
        all_sessions=       tuple(all_sessions),
        tenant_id=          tenant_id,
        trace_id=           trace_id,
        case_id=            case_id,
        query_phone_number= query_phone_number,
        query_session_id=   query_session_id,
        failure_summary=    failure_summary or {},
        recent_summary=     recent_summary or {},
        onboarding_stage=   onboarding_stage,
    )


# ── Derivations for specific tool adapters ──────────────────────────────────

def derive_failure_summary(session: UnitySession | None) -> dict[str, Any]:
    """
    From a session, extract the operator-facing failure fields.
    Used by GetFailureReasonTool.
    """
    if session is None:
        return {
            "failure_category":   "UNKNOWN",
            "failure_code":       "NO_SESSION",
            "failure_message":    "no Unity session available",
            "is_transient":       False,
            "recommended_action": "MANUAL_REVIEW",
        }
    status = session.session_status
    failed = list(session.failed_steps)
    if status.is_success:
        category, code, msg, transient, action = (
            "NONE", "APPROVED", "session approved by auditor", False, "NONE",
        )
    elif status.is_rejected:
        category, code, msg, transient, action = (
            "REJECTED", "AUDITOR_REJECTED",
            f"rejected by auditor: {session.auditor.feedback}",
            False, "MANUAL_REVIEW",
        )
    elif status == SessionStatus.SESSION_EXPIRED:
        category, code, msg, transient, action = (
            "TIMEOUT", "SESSION_EXPIRED",
            "session link expired before completion",
            True, "RETRY",
        )
    elif status == SessionStatus.USER_ABANDONED:
        category, code, msg, transient, action = (
            "USER_ABANDONED", "USER_ABANDONED",
            "customer left session before completion",
            True, "RETRY",
        )
    elif status == SessionStatus.WAITING:
        category, code, msg, transient, action = (
            "STUCK_IN_QUEUE", "WAITING",
            "session still waiting in queue",
            True, "ESCALATE",
        )
    elif status == SessionStatus.KYC_RESULT_PARTIAL_UPDATE:
        category, code, msg, transient, action = (
            "PARTIAL", "PARTIAL_UPDATE",
            "partial update — needs manual review",
            False, "MANUAL_REVIEW",
        )
    else:
        category, code, msg, transient, action = (
            "UNKNOWN", "UNKNOWN_STATUS",
            f"unrecognized session_status: {status.value}",
            False, "MANUAL_REVIEW",
        )

    return {
        "failure_category":   category,
        "failure_code":       code,
        "failure_message":    msg,
        "is_transient":       transient,
        "recommended_action": action,
        "failed_steps":       failed,
    }


def derive_recent_summary(sessions: Iterable[UnitySession], phone_number: str) -> dict[str, Any]:
    """
    Aggregate summary of the customer's recent sessions.
    Used by GetCaseHistoryTool.
    """
    ss = tuple(sessions)
    if not ss:
        return {
            "phone_number":   _mask_phone(phone_number),
            "case_count":     0,
            "recent_cases":   [],
            "repeat_topic":   None,
            "escalation_rate": 0.0,
        }
    status_counts: dict[str, int] = {}
    recent: list[dict[str, Any]] = []
    for s in sorted(ss, key=lambda x: x.timeline.init_time or 0.0, reverse=True)[:5]:
        recent.append({
            "case_id":    s.session_id,
            "topic":      f"VKYC_{s.session_status.value}",
            "state":      s.session_status.value,
            "created_at": s.timeline.init_time,
        })
        status_counts[s.session_status.value] = status_counts.get(s.session_status.value, 0) + 1
    repeat = max(status_counts.items(), key=lambda x: x[1])[0] if status_counts else None
    escalated = sum(1 for s in ss if s.session_status == SessionStatus.WAITING)
    return {
        "phone_number":    _mask_phone(phone_number),
        "case_count":      len(ss),
        "recent_cases":    recent,
        "repeat_topic":    f"VKYC_{repeat}" if repeat else None,
        "escalation_rate": round(escalated / len(ss), 3),
    }


def derive_onboarding_stage(session: UnitySession | None) -> str:
    """
    Map Unity session_status to KwikID onboarding stage.
    Used by GetOnboardingStatusTool.
    """
    if session is None:
        return "REGISTRATION"
    status = session.session_status
    if status.is_success:
        return "COMPLETE"
    if status.is_rejected:
        return "KYC_REJECTED"
    if status == SessionStatus.WAITING:
        return "VKYC"
    if status == SessionStatus.SESSION_EXPIRED:
        return "VKYC_EXPIRED"
    if status == SessionStatus.USER_ABANDONED:
        return "VKYC_ABANDONED"
    if status == SessionStatus.KYC_RESULT_PARTIAL_UPDATE:
        return "VKYC_PARTIAL"
    return "VKYC"


# ── Utilities ─────────────────────────────────────────────────────────────

def _mask_phone(phone: str) -> str:
    if not phone:
        return ""
    s = str(phone)
    if len(s) < 4:
        return "***"
    return "*" * (len(s) - 4) + s[-4:]


# extras / stage_data are JSON-encoded strings — expose helper for callers
def parse_extras(raw: Any) -> dict[str, Any]:
    """Best-effort parse of the `extras` or `stage_data` string field."""
    if not raw or not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
