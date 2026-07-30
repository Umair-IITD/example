"""
case_engine/integrations/loki/relevance.py

Sprint 2.60: Log relevance extractor for Loki session logs.

Ported from Loki_Log_Tool_Blueprint/log_extractor.py with these changes:
- bm25_score_list imported from rag_engine.retrieval.bm25 (not inline)
- _load_sample, run_case, __main__ NOT ported (research script only)
- parse_auditor_duration_window added for auditor feedback parsing

CRITICAL ORDERING CONSTRAINT:
  _redact_and_truncate() is called in parse_line() BEFORE the line content
  is ever used for scoring or budget accounting. A 110,235-char Aadhaar photo
  line exists in real logs. If truncation runs after scoring, it consumes the
  entire 9KB budget.

Security:
  - .content on every LogLine is always redacted + truncated — never raw
  - raw_text from fetch_session_logs MUST NOT be logged or stored
  - Only curated_log_excerpt exits this module
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from rag_engine.retrieval.bm25 import bm25_score_list


# ---------------------------------------------------------------------------
# PII redaction + per-line size cap
# ---------------------------------------------------------------------------

_PII_KEY_PATTERN = re.compile(
    r'"(aadhaar\w*|pan\w*|dob|date_of_birth|address\w*|nominee\w*|'
    r'capture_image|selfie_image|\w*_image|\w*photo\w*|income\w*|'
    r'marital_status|occupation)"\s*:\s*"[^"]{0,200000}?"',
    re.IGNORECASE,
)

_MAX_MESSAGE_CHARS = 500


def _redact_and_truncate(message: str, max_chars: int = _MAX_MESSAGE_CHARS) -> str:
    """PII substitution THEN truncation — order is non-negotiable."""
    redacted = _PII_KEY_PATTERN.sub(lambda m: f'"{m.group(1)}":"[REDACTED]"', message)
    if len(redacted) > max_chars:
        return redacted[:max_chars] + f"...[+{len(redacted) - max_chars} chars truncated]"
    return redacted


# ---------------------------------------------------------------------------
# Known-issue annotation
# ---------------------------------------------------------------------------

KNOWN_ISSUE_NOTES: list[tuple[re.Pattern, str]] = [
    (
        re.compile(r"access forbidden by rule"),
        "[KNOWN RECURRING - nginx blocks routine event-tracking pings on many "
        "sessions; does not block session completion; see findings §0b]",
    ),
    (
        re.compile(r"ERROR:upload_video_single:"),
        "[KNOWN RECURRING - non-fatal KeyError in the video-upload metadata "
        "path; pipeline continues past it; see findings §0b]",
    ),
]


def _annotate_known_issues(content: str) -> str:
    for pattern, note in KNOWN_ISSUE_NOTES:
        if pattern.search(content):
            return f"{content}  {note}"
    return content


# ---------------------------------------------------------------------------
# Level weights
# ---------------------------------------------------------------------------

LEVEL_WEIGHT = {
    "error": 8.0,
    "err": 8.0,
    "critical": 8.0,
    "warn": 4.0,
    "warning": 4.0,
    "info": 0.0,
    "debug": -1.0,
    "unknown": 0.0,
}

# ---------------------------------------------------------------------------
# Service hints
# ---------------------------------------------------------------------------

SERVICE_HINTS: dict[str, tuple[str, ...]] = {
    "video": ("agentapi", "kwikid-celery-worker-make_seekable_video"),
    "upload": ("agentapi", "kwikid-celery-worker-make_seekable_video"),
    "audio": ("userapi", "agentapi"),
    "mic": ("userapi",),
    "call": ("userapi", "agentapi"),
    "disconnect": ("userapi",),
    "otp": ("userapi", "notificaion-service"),
    "sms": ("notificaion-service",),
    "reject": ("agentapi",),
    "kyc": ("agentapi",),
    "queue": ("userapi",),
    "aadhaar": ("notificaion-service", "agentapi"),
    "pan": ("agentapi",),
    "network": ("nginx",),
    "timeout": ("nginx", "agentapi"),
}

SERVICE_HINT_BOOST = 3.0


# ---------------------------------------------------------------------------
# LogLine dataclass + parser
# ---------------------------------------------------------------------------

@dataclass
class LogLine:
    raw_len: int
    index: int
    ts: datetime | None
    level: str
    service_name: str
    content: str   # already redacted + truncated — this is what leaves this stage
    score: float = 0.0
    forced: bool = False
    reasons: list[str] = field(default_factory=list)


_LINE_RE = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\s*\((?P<meta>[^)]*)\)\s*(?P<message>.*)$"
)


def parse_line(raw: str, index: int) -> LogLine:
    """Parse a formatted log line. _redact_and_truncate is called BEFORE scoring."""
    m = _LINE_RE.match(raw)
    if not m:
        # Not in the expected format — still redact and annotate
        cleaned = _annotate_known_issues(_redact_and_truncate(raw))
        return LogLine(
            raw_len=len(raw), index=index, ts=None, level="unknown",
            service_name="", content=cleaned,
        )
    ts_str, meta_str, message = m.group("ts"), m.group("meta"), m.group("message")
    try:
        ts = datetime.fromisoformat(ts_str)
    except ValueError:
        ts = None
    level = "unknown"
    service_name = ""
    for part in meta_str.split(","):
        part = part.strip()
        if part.startswith("detected_level="):
            level = part.split("=", 1)[1].strip()
        elif part.startswith("service_name="):
            service_name = part.split("=", 1)[1].strip()
    # PII redaction + truncation happens HERE, before content is ever used for scoring
    cleaned = _annotate_known_issues(_redact_and_truncate(message))
    return LogLine(
        raw_len=len(raw), index=index, ts=ts, level=level,
        service_name=service_name, content=cleaned,
    )


# ---------------------------------------------------------------------------
# Duplicate collapsing
# ---------------------------------------------------------------------------

def _content_signature(content: str) -> str:
    """Stable identity for a line, ignoring exact timestamps/ids inside it."""
    m = re.search(r"'event':\s*'([^']+)'", content)
    if m:
        return m.group(1)
    return re.sub(r"\d+", "#", content[:60])


def _collapse_duplicates(
    selected: list[LogLine], *, threshold: int = 3, keep_first: int = 1, keep_last: int = 1
) -> list[LogLine]:
    """
    Collapse runs of threshold+ consecutive non-forced lines sharing the same
    (service_name, event-signature) into first + last + a count marker.
    Never touches forced lines (error-level / auditor-window).
    """
    out: list[LogLine] = []
    i, n = 0, len(selected)
    while i < n:
        p = selected[i]
        if p.forced:
            out.append(p)
            i += 1
            continue
        sig = (p.service_name, _content_signature(p.content))
        j = i + 1
        while (
            j < n
            and not selected[j].forced
            and (selected[j].service_name, _content_signature(selected[j].content)) == sig
        ):
            j += 1
        run = selected[i:j]
        if len(run) < threshold:
            out.extend(run)
        else:
            out.extend(run[:keep_first])
            middle = run[keep_first: len(run) - keep_last] if keep_last else run[keep_first:]
            if middle:
                out.append(LogLine(
                    raw_len=0, index=middle[0].index, ts=middle[0].ts,
                    level=middle[0].level, service_name=middle[0].service_name,
                    content=(
                        f"[COLLAPSED - {len(middle)} more occurrence(s) of "
                        f"{sig[1]!r} on service={sig[0]} between "
                        f"{middle[0].ts} and {middle[-1].ts}, identical in shape, omitted]"
                    ),
                    score=middle[0].score, forced=False, reasons=["collapsed_duplicate"],
                ))
            if keep_last:
                out.extend(run[-keep_last:])
        i = j
    return out


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def guess_hinted_services(ticket_text: str) -> set[str]:
    text_lower = ticket_text.lower()
    hinted: set[str] = set()
    for kw, services in SERVICE_HINTS.items():
        if kw in text_lower:
            hinted.update(services)
    return hinted


def extract_relevant_lines(
    log_lines: list[str],
    ticket_query: str,
    *,
    auditor_window: tuple[datetime, datetime] | None = None,
    char_budget: int = 9000,
    top_k: int = 40,
    collapse_duplicates: bool = True,
) -> tuple[list[LogLine], dict]:
    """
    Returns (selected_lines_sorted_chronologically, stats_dict).

    Budget accounting uses .content (redacted + truncated), not raw line size.
    """
    parsed = [parse_line(l, i) for i, l in enumerate(log_lines)]
    contents = [p.content for p in parsed]
    bm25_scores = bm25_score_list(ticket_query, contents)
    hinted_services = guess_hinted_services(ticket_query)

    for p, bm25 in zip(parsed, bm25_scores):
        level_w = LEVEL_WEIGHT.get(p.level, 0.0)
        hint_w = SERVICE_HINT_BOOST if p.service_name in hinted_services else 0.0
        p.score = round(bm25 + level_w + hint_w, 4)
        if bm25 > 0:
            p.reasons.append(f"bm25={bm25}")
        if level_w > 0:
            p.reasons.append(f"level={p.level}")
        if hint_w:
            p.reasons.append(f"service_hint={p.service_name}")

        forced = False
        if p.level in ("error", "err", "critical"):
            forced = True
            p.reasons.append("forced:error_level")
        if auditor_window and p.ts is not None:
            lo, hi = auditor_window
            if lo <= p.ts <= hi:
                forced = True
                p.reasons.append("forced:auditor_window")
        p.forced = forced

    forced_lines = [p for p in parsed if p.forced]
    ranked = sorted(
        [p for p in parsed if not p.forced],
        key=lambda p: p.score, reverse=True,
    )

    selected: list[LogLine] = list(forced_lines)
    budget_used = sum(len(p.content) for p in selected)
    kept_by_score = 0
    for p in ranked:
        if kept_by_score >= top_k:
            break
        if p.score <= 0:
            break
        if budget_used + len(p.content) > char_budget:
            continue
        selected.append(p)
        budget_used += len(p.content)
        kept_by_score += 1

    selected.sort(key=lambda p: (p.ts is None, p.ts, p.index))
    pre_collapse_count = len(selected)
    if collapse_duplicates:
        selected = _collapse_duplicates(selected)

    stats = {
        "pre_collapse_lines": pre_collapse_count,
        "input_lines": len(parsed),
        "input_raw_chars": sum(p.raw_len for p in parsed),
        "output_lines": len(selected),
        "output_chars": sum(len(p.content) for p in selected),
        "forced_count": len(forced_lines),
        "scored_kept_count": kept_by_score,
        "hinted_services": sorted(hinted_services),
    }
    stats["line_reduction_pct"] = round(
        100 * (1 - stats["output_lines"] / max(1, stats["input_lines"])), 1
    )
    stats["char_reduction_pct_vs_raw"] = round(
        100 * (1 - stats["output_chars"] / max(1, stats["input_raw_chars"])), 1
    )
    return selected, stats


# ---------------------------------------------------------------------------
# Auditor duration window parser
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(r"duration\s+(\d+):(\d+)\s+to\s+(\d+):(\d+)", re.IGNORECASE)


def parse_auditor_duration_window(
    feedback_text: str | None, vkyc_start_epoch: float | None
) -> tuple[datetime, datetime] | None:
    """Parse 'duration X:XX to Y:YY' from auditor feedback into absolute UTC window."""
    if not feedback_text or not vkyc_start_epoch:
        return None
    m = _DURATION_RE.search(feedback_text)
    if not m:
        return None
    m1, s1, m2, s2 = (int(x) for x in m.groups())
    base = datetime.fromtimestamp(vkyc_start_epoch, tz=timezone.utc)
    return (base + timedelta(minutes=m1, seconds=s1), base + timedelta(minutes=m2, seconds=s2))
