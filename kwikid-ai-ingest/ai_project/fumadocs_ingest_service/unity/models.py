"""
unity/models.py

Sprint 2.51: Canonical, strongly-typed domain models for Unity Admin Portal
evidence.

Every model:
- @dataclass(frozen=True) — immutable
- to_dict() — JSON-safe serialization
- No `Optional` overuse — sentinel values (empty tuple / "") where semantically stable
- PII fields are stored as-received; masking happens at rendering (traces, notes)

Dependency direction: models.py → stdlib only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Enums ────────────────────────────────────────────────────────────────────

class SessionStatus(str, Enum):
    """
    Confirmed session_status enum from getAllUserSession + get_details
    (SOT api_reference.md §2.session_status enum values).
    """
    KYC_RESULT_APPROVED       = "kyc_result_approved"
    KYC_RESULT_REJECTED       = "kyc_result_rejected"
    KYC_REJECTED              = "kyc_rejected"
    KYC_RESULT_PARTIAL_UPDATE = "kyc_result_partial_update"
    SESSION_EXPIRED           = "session_expired"
    USER_ABANDONED            = "user_abandoned"
    WAITING                   = "waiting"
    UNKNOWN                   = "unknown"

    @classmethod
    def from_str(cls, value: str | None) -> "SessionStatus":
        if not value:
            return cls.UNKNOWN
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            return cls.UNKNOWN

    @property
    def is_terminal(self) -> bool:
        return self in {
            SessionStatus.KYC_RESULT_APPROVED,
            SessionStatus.KYC_RESULT_REJECTED,
            SessionStatus.KYC_REJECTED,
            SessionStatus.SESSION_EXPIRED,
            SessionStatus.USER_ABANDONED,
        }

    @property
    def is_success(self) -> bool:
        return self == SessionStatus.KYC_RESULT_APPROVED

    @property
    def is_rejected(self) -> bool:
        return self in {SessionStatus.KYC_RESULT_REJECTED, SessionStatus.KYC_REJECTED}


class EvidenceAvailability(str, Enum):
    AVAILABLE   = "AVAILABLE"
    PARTIAL     = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    DISABLED    = "DISABLED"


# ── Fine-grained models ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class AgentInfo:
    """VKYC agent who handled the session."""
    agent_id:  str = ""
    region:    str = ""
    tl:        str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"agent_id": self.agent_id, "region": self.region, "tl": self.tl}


@dataclass(frozen=True)
class AuditorInfo:
    """Auditor who reviewed the session."""
    auditor_name: str = ""
    result:       str = ""
    feedback:     str = ""
    fdbk_code:    str = ""
    lock:         bool = False
    init_time:    str = ""
    end_time:     str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "auditor_name": self.auditor_name,
            "result":       self.result,
            "feedback":     self.feedback,
            "fdbk_code":    self.fdbk_code,
            "lock":         self.lock,
            "init_time":    self.init_time,
            "end_time":     self.end_time,
        }


@dataclass(frozen=True)
class FeedbackInfo:
    """Parsed feedback field (JSON-encoded string in raw API)."""
    type:             str = ""      # Approve | Reject | Reopen
    comment:          str = ""
    feedback_comment: str = ""

    @classmethod
    def from_str(cls, raw: Any) -> "FeedbackInfo":
        import json
        if not raw or not isinstance(raw, str):
            return cls()
        try:
            d = json.loads(raw)
        except (ValueError, TypeError):
            return cls()
        if not isinstance(d, dict):
            return cls()
        return cls(
            type=            str(d.get("type", "")),
            comment=         str(d.get("comment", "")),
            feedback_comment=str(d.get("feedbackComment", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "comment": self.comment,
                "feedback_comment": self.feedback_comment}


@dataclass(frozen=True)
class TimelineInfo:
    """Consolidated timestamps for one session."""
    init_time:                     float = 0.0
    start_time:                    float = 0.0
    end_time:                      float = 0.0
    vkyc_start_time:               float = 0.0
    last_active_timestamp:         float = 0.0
    agent_assignment_time:         float = 0.0
    removed_from_queue_timestamp:  float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "init_time":                     self.init_time,
            "start_time":                    self.start_time,
            "end_time":                      self.end_time,
            "vkyc_start_time":               self.vkyc_start_time,
            "last_active_timestamp":         self.last_active_timestamp,
            "agent_assignment_time":         self.agent_assignment_time,
            "removed_from_queue_timestamp":  self.removed_from_queue_timestamp,
        }


@dataclass(frozen=True)
class JourneyStep:
    """One row of overall_summary."""
    title:   str
    success: bool

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "success": self.success}


@dataclass(frozen=True)
class VerificationDoc:
    """
    One document verification record (from summary_data.docs[]).
    PII fields (pan_number, dob, etc.) are stored as-received but rendering
    layers must mask before emitting anywhere except audit logs.
    """
    name:              str
    facematch_score:   int | None = None
    validator_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":              self.name,
            "facematch_score":   self.facematch_score,
            "validator_summary": self.validator_summary,
        }


@dataclass(frozen=True)
class QnA:
    """One question/answer row from summary_data.qna."""
    q: str
    a: str

    def to_dict(self) -> dict[str, Any]:
        return {"q": self.q, "a": self.a}


@dataclass(frozen=True)
class CustomerInfo:
    """Customer-level identifiers (masked at rendering, not stored)."""
    phone_number: str = ""
    user_id:      str = ""

    @property
    def masked_phone(self) -> str:
        if not self.phone_number or len(self.phone_number) < 4:
            return "***"
        return "*" * (len(self.phone_number) - 4) + self.phone_number[-4:]

    def to_dict(self) -> dict[str, Any]:
        return {
            "phone_number": self.phone_number,
            "user_id":      self.user_id,
        }


@dataclass(frozen=True)
class MediaInfo:
    """Media URLs from the session — never emitted to customer notes."""
    agent_video_url:  str = ""
    agent_screen_url: str = ""
    user_video_url:   str = ""
    selfie_url:       str = ""
    pan_url:          str = ""
    aadhaar_pdf_url:  str = ""
    aadhaar_xml_url:  str = ""
    summary_pdf_url:  str = ""
    summary_json_url: str = ""
    zip_url:          str = ""
    video_duration:   str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_video_url":  self.agent_video_url,
            "agent_screen_url": self.agent_screen_url,
            "user_video_url":   self.user_video_url,
            "selfie_url":       self.selfie_url,
            "pan_url":          self.pan_url,
            "aadhaar_pdf_url":  self.aadhaar_pdf_url,
            "aadhaar_xml_url":  self.aadhaar_xml_url,
            "summary_pdf_url":  self.summary_pdf_url,
            "summary_json_url": self.summary_json_url,
            "zip_url":          self.zip_url,
            "video_duration":   self.video_duration,
        }


@dataclass(frozen=True)
class MetadataInfo:
    """Non-PII metadata about the session."""
    session_type:            str = ""
    product_code:            str = ""
    lang:                    str = ""
    stage:                   str = ""
    stage1_valid:            bool = False
    is_video_uploaded:       bool = False
    app_version_number:      str = ""
    link_id:                 str = ""
    device_id:               str = ""
    queue:                   str = ""
    queue_id:                str = ""
    queue_mode:              str = ""
    queue_position_at_init:  str = ""
    inserted_in_queue_at:    str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_type":           self.session_type,
            "product_code":           self.product_code,
            "lang":                   self.lang,
            "stage":                  self.stage,
            "stage1_valid":           self.stage1_valid,
            "is_video_uploaded":      self.is_video_uploaded,
            "app_version_number":     self.app_version_number,
            "link_id":                self.link_id,
            "device_id":              self.device_id,
            "queue":                  self.queue,
            "queue_id":               self.queue_id,
            "queue_mode":             self.queue_mode,
            "queue_position_at_init": self.queue_position_at_init,
            "inserted_in_queue_at":   self.inserted_in_queue_at,
        }


# ── Session ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class UnitySession:
    """
    Canonical Unity session — output of parsing either a getAllUserSession list
    item or a get_details response into a single normalized shape.
    """
    session_id:        str
    session_status:    SessionStatus
    client_name:       str = ""
    customer:          CustomerInfo = field(default_factory=CustomerInfo)
    agent:             AgentInfo    = field(default_factory=AgentInfo)
    auditor:           AuditorInfo  = field(default_factory=AuditorInfo)
    feedback:          FeedbackInfo = field(default_factory=FeedbackInfo)
    timeline:          TimelineInfo = field(default_factory=TimelineInfo)
    metadata:          MetadataInfo = field(default_factory=MetadataInfo)
    media:             MediaInfo    = field(default_factory=MediaInfo)
    overall_summary:   tuple[JourneyStep, ...] = field(default_factory=tuple)
    qna:               tuple[QnA, ...]         = field(default_factory=tuple)
    docs:              tuple[VerificationDoc, ...] = field(default_factory=tuple)
    audit_id:          str = ""

    @property
    def failed_steps(self) -> tuple[str, ...]:
        return tuple(s.title for s in self.overall_summary if not s.success)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id":      self.session_id,
            "session_status":  self.session_status.value,
            "client_name":     self.client_name,
            "customer":        self.customer.to_dict(),
            "agent":           self.agent.to_dict(),
            "auditor":         self.auditor.to_dict(),
            "feedback":        self.feedback.to_dict(),
            "timeline":        self.timeline.to_dict(),
            "metadata":        self.metadata.to_dict(),
            "media":           self.media.to_dict(),
            "overall_summary": [s.to_dict() for s in self.overall_summary],
            "qna":             [q.to_dict() for q in self.qna],
            "docs":            [d.to_dict() for d in self.docs],
            "audit_id":        self.audit_id,
            "failed_steps":    list(self.failed_steps),
        }


@dataclass(frozen=True)
class SessionList:
    """Raw result of GET /api/v1/getAllUserSession/{domain}/{phone}."""
    phone_number:  str
    session_count: int
    sessions:      tuple[UnitySession, ...]
    fetched_at:    str

    def to_dict(self) -> dict[str, Any]:
        return {
            "phone_number":   self.phone_number,
            "session_count":  self.session_count,
            "sessions":       [s.to_dict() for s in self.sessions],
            "fetched_at":     self.fetched_at,
        }


# ── Canonical evidence bundle ────────────────────────────────────────────────

@dataclass(frozen=True)
class UnityEvidence:
    """
    Canonical evidence returned by every Unity BaseTool adapter.

    Uniform shape means: the Investigation Layer cannot tell whether the
    data came from a mock or the production Unity Admin Portal.

    Fields are optionally-populated depending on which adapter produced it:
      - GetSessionDetails / GetUser: populates `session` + `all_session_count`
      - GetFailureReason: populates `session` + `failure_summary`
      - GetCaseHistory: populates `all_sessions` + `recent_summary`
      - GetOnboardingStatus: populates `session` + `onboarding_stage`
    """
    tool_name:            str
    collected_at:         str
    source:               str                        = "unity_admin_portal"
    evidence_available:   EvidenceAvailability       = EvidenceAvailability.AVAILABLE
    error:                str                        = ""
    error_code:           str                        = ""

    session_found:        bool                       = False
    session:              UnitySession | None        = None
    all_session_count:    int                        = 0
    all_sessions:         tuple[UnitySession, ...]   = field(default_factory=tuple)

    # Passthrough correlation
    tenant_id:            str                        = ""
    trace_id:             str                        = ""
    case_id:              str                        = ""
    query_phone_number:   str                        = ""
    query_session_id:     str                        = ""

    # Derived hints (populated by adapters)
    failure_summary:      dict[str, Any]             = field(default_factory=dict)
    recent_summary:       dict[str, Any]             = field(default_factory=dict)
    onboarding_stage:     str                        = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name":          self.tool_name,
            "collected_at":       self.collected_at,
            "source":             self.source,
            "evidence_available": self.evidence_available.value,
            "error":              self.error,
            "error_code":         self.error_code,
            "session_found":      self.session_found,
            "session":            None if self.session is None else self.session.to_dict(),
            "all_session_count":  self.all_session_count,
            "all_sessions":       [s.to_dict() for s in self.all_sessions],
            "tenant_id":          self.tenant_id,
            "trace_id":           self.trace_id,
            "case_id":            self.case_id,
            "query_phone_number": _mask_phone(self.query_phone_number),
            "query_session_id":   self.query_session_id,
            "failure_summary":    dict(self.failure_summary),
            "recent_summary":     dict(self.recent_summary),
            "onboarding_stage":   self.onboarding_stage,
        }


# ── PII helpers (used at rendering) ─────────────────────────────────────────

def _mask_phone(phone: str) -> str:
    if not phone:
        return ""
    s = str(phone)
    if len(s) < 4:
        return "***"
    return "*" * (len(s) - 4) + s[-4:]
