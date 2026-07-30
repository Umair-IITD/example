"""
log_extractor.py - SCRATCH TEST, not part of the project codebase.

Purpose: prototype and empirically validate a zero-marginal-cost algorithm
that shrinks a full Loki session-log pull (hundreds of lines) down to the
handful of lines actually relevant to the specific issue described in a
bank agent's Freshdesk ticket, BEFORE any of it is handed to an LLM.

Why this exists: a real full-session pull (session b96a55e6, saved in
logs1.txt this session) is 500 real log lines / ~473KB once decoded from
the UTF-16 file PowerShell saved it as. Posting that whole blob into an LLM
prompt on every ticket is slow, expensive (large input token cost on every
single investigation), and dilutes the model's attention with mostly
irrelevant noise (queue-polling heartbeats, nginx OPTIONS preflight lines,
routine navigation telemetry) - on top of a separate, serious problem: one
single line in that same sample is 110,235 characters by itself, almost
entirely a base64-encoded Aadhaar photo (see Unity_Loki_Integration_Findings.md
§0 / §6.1). We need a filter stage that runs BEFORE the LLM call, using no
paid API, so the cost of "understanding what's relevant" does not scale
with ticket volume - and that filter has to redact/truncate oversized,
PII-bearing lines as a first-class part of its job, not an afterthought.

Design (composite score per log line, all computed locally, no network call):

    score(line) = bm25(ticket_query, line.content)      <- lexical match
                + LEVEL_WEIGHT[line.level]               <- error/warn boost
                + SERVICE_HINT_BOOST if line.service_name
                  is among the services guessed from the
                  ticket text via SERVICE_NAME_HINTS      <- from loki_client.py

    force_include(line) = True if line.level == "error"
                        or line.timestamp falls inside the auditor's
                           flagged sub-window (e.g. "duration 1:19 to 2:40"
                           from auditor.feedback text), if one was given.

Top-K lines by composite score, UNIONED with the force-included set, are
kept up to a character budget; the rest are dropped. Kept lines are then
re-sorted chronologically before being handed to the LLM, so the narrative
still reads top-to-bottom the way a human would triage it.

The BM25 implementation below (_tokenize, _bm25_rerank_inplace) is a
byte-for-byte port of the existing, already-battle-tested, dependency-free
BM25 reranker in the REAL project at:
    rag_engine/retrieval/hybrid_ticket_retriever.py (lines ~90-105, ~656-679)
It is copied here (not imported) because this script intentionally lives
outside the project so nothing in the real codebase is touched during this
research pass - Claude Code should import the real one, not this copy, when
this is actually wired in (see the guidance doc for where).

This script is read-only with respect to the project and the two log
samples it loads from disk; it writes nothing back to either.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _bm25_rerank(query: str, contents: list[str]) -> list[float]:
    """
    Ported verbatim (logic-for-logic) from
    HybridTicketRetriever._bm25_rerank_inplace in
    rag_engine/retrieval/hybrid_ticket_retriever.py, generalised to return
    a parallel list of scores instead of mutating row dicts in place.
    Zero external dependencies: stdlib `math` + `re` only. Zero cost.
    """
    q_tokens = _tokenize(query)
    n = len(contents)
    if not q_tokens or not n:
        return [0.0] * n
    doc_token_sets = [_tokenize(c) for c in contents]
    doc_lengths = [len(dt) for dt in doc_token_sets]
    avg_dl = sum(doc_lengths) / n if n else 1.0
    df = {t: sum(1 for dt in doc_token_sets if t in dt) for t in q_tokens}
    k1, b = 1.5, 0.75
    scores: list[float] = []
    for content, dl in zip(contents, doc_lengths):
        content_lower = content.lower()
        score = 0.0
        for term in q_tokens:
            tf = content_lower.count(term)
            if tf == 0:
                continue
            idf = math.log((n - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5) + 1.0)
            tf_norm = tf * (k1 + 1.0) / (tf + k1 * (1.0 - b + b * dl / max(1.0, avg_dl)))
            score += idf * tf_norm
        scores.append(round(score, 6))
    return scores


# ---------------------------------------------------------------------------
# Parsing loki_client.py's merged-text output format:
#   [ISO8601 ts] (key=val, key=val, ...) message text
# ---------------------------------------------------------------------------

_LINE_RE = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\s*\((?P<meta>[^)]*)\)\s*(?P<message>.*)$"
)

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

# Same idea as loki_client.py's SERVICE_NAME_HINTS: keyword -> service_name(s)
# most likely to contain evidence for that kind of complaint. Kept small and
# deliberately extensible (see findings doc §"open items").
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
# PII redaction + per-line size cap.
#
# Real finding from this project (see Unity_Loki_Integration_Findings.md
# §0): a single "JSON DATA" / "[LOG_DECORATOR] Request is form" log line can
# be 100,000+ characters because it embeds a base64 Aadhaar photo plus
# Aadhaar/PAN/DOB/address/nominee fields in plaintext. Two problems follow:
#   1. Compliance: this must never reach an LLM prompt, a Freshdesk note, or
#      an L1 agent's screen unredacted.
#   2. Practical: if left un-truncated, ONE such line can consume the entire
#      extraction char budget by itself, starving every other genuinely
#      relevant line (confirmed below - see the "before" run of this script).
# Both are fixed by the same two-step transform, applied once per line
# before scoring OR selection, so the budget accounting reflects what will
# actually be sent downstream.
# ---------------------------------------------------------------------------

_PII_KEY_PATTERN = re.compile(
    r'"(aadhaar\w*|pan\w*|dob|date_of_birth|address\w*|nominee\w*|'
    r'capture_image|selfie_image|\w*_image|\w*photo\w*|income\w*|'
    r'marital_status|occupation)"\s*:\s*"[^"]{0,200000}?"',
    re.IGNORECASE,
)

_MAX_MESSAGE_CHARS = 500


def _redact_and_truncate(message: str, max_chars: int = _MAX_MESSAGE_CHARS) -> str:
    redacted = _PII_KEY_PATTERN.sub(lambda m: f'"{m.group(1)}":"[REDACTED]"', message)
    if len(redacted) > max_chars:
        return redacted[:max_chars] + f"...[+{len(redacted) - max_chars} chars truncated]"
    return redacted


# ---------------------------------------------------------------------------
# Known-issue annotation.
#
# Real finding (Unity_Loki_Integration_Findings.md §0b + this session's live
# test against session 93c1910e): the nginx "access forbidden by rule" 403
# on POST/OPTIONS /v1/user/event/... calls fires on essentially every
# session's routine event-tracking pings, independent of whatever the ticket
# is actually about. Because it's `detected_level=error`, it is always
# force-included (correctly - it IS a real, error-level bug) but without a
# label an LLM reasoning over the excerpt could easily mistake it for the
# ticket-specific root cause rather than known, session-agnostic platform
# noise. A short, cheap annotation fixes this without hiding the line.
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


@dataclass
class LogLine:
    raw_len: int
    index: int
    ts: datetime | None
    level: str
    service_name: str
    content: str   # already redacted + truncated - this is what leaves this stage
    score: float = 0.0
    forced: bool = False
    reasons: list[str] = field(default_factory=list)


def parse_line(raw: str, index: int) -> LogLine:
    m = _LINE_RE.match(raw)
    if not m:
        cleaned = _annotate_known_issues(_redact_and_truncate(raw))
        return LogLine(raw_len=len(raw), index=index, ts=None, level="unknown",
                        service_name="", content=cleaned)
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
    cleaned = _annotate_known_issues(_redact_and_truncate(message))
    return LogLine(raw_len=len(raw), index=index, ts=ts, level=level,
                    service_name=service_name, content=cleaned)


# ---------------------------------------------------------------------------
# Duplicate collapsing.
#
# Real finding from this session's live test (session 93c1910e, "audio
# issue" ticket): the same WebRTC client-telemetry event (e.g.
# UP_PROC_MEDIA_ALERT_SHOWN_FOR_AUDIO) can legitimately recur 5+ times
# across a session - the repetition itself is a signal (a sustained issue,
# not a one-off blip) but each occurrence is near-identical content, so
# keeping all 5 verbatim burns budget that could go toward more diverse
# evidence. This collapses runs of near-identical, SCORE-SELECTED lines
# (never forced/error lines - see docstring below) into first + last +
# a count, preserving the "this happened repeatedly, from T1 to T2" signal
# without paying for every duplicate's full content.
# ---------------------------------------------------------------------------

def _content_signature(content: str) -> str:
    """Stable-ish identity for a line, ignoring exact timestamps/ids inside it."""
    m = re.search(r"'event':\s*'([^']+)'", content)
    if m:
        return m.group(1)
    return re.sub(r"\d+", "#", content[:60])


def _collapse_duplicates(
    selected: list[LogLine], *, threshold: int = 3, keep_first: int = 1, keep_last: int = 1
) -> list[LogLine]:
    """
    Collapses runs of `threshold`+ consecutive, non-forced lines sharing the
    same (service_name, event-signature) into first + last + a "+N more"
    summary marker. Never touches forced lines (error-level / auditor-window)
    - those are a safety guarantee (see extract_relevant_lines docstring),
    not a budget trade-off, so they are always passed through untouched and
    never counted toward a collapsible run.
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

    NOTE: char_budget / raw-size accounting is done against `.content`
    (already redacted + truncated to _MAX_MESSAGE_CHARS), not the original
    raw line - that's the size that actually matters, since `.content` is
    what would be handed to the LLM. `raw_len` is kept on each LogLine only
    for reporting the "how much did redaction/truncation itself save" figure.
    """
    parsed = [parse_line(l, i) for i, l in enumerate(log_lines)]
    contents = [p.content for p in parsed]
    bm25_scores = _bm25_rerank(ticket_query, contents)
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
            break  # no more lexical/hint signal at all - stop
        if budget_used + len(p.content) > char_budget:
            continue  # this one doesn't fit, but a smaller later one might
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
# Demo / empirical test against the real saved sample (session b96a55e6)
# ---------------------------------------------------------------------------

def _load_sample(path: str) -> list[str]:
    """
    Loads one of this folder's saved log samples. Handles the fact that
    PowerShell's `Tee-Object`/redirection (used to capture these samples on
    Windows) writes UTF-16LE with a BOM, not UTF-8 - auto-detects either.
    """
    raw_bytes = open(path, "rb").read()
    if raw_bytes.startswith(b"\xff\xfe") or raw_bytes.startswith(b"\xfe\xff"):
        text = raw_bytes.decode("utf-16")
    else:
        text = raw_bytes.decode("utf-8", errors="replace")
    lines = text.splitlines()
    # Skip the script's own diagnostic header lines (see unity_to_loki_test.py
    # output format) - real log lines all start with "[20" (an ISO year).
    return [l for l in lines if l.startswith("[20")]


def run_case(label: str, log_lines: list[str], query: str, **kwargs) -> None:
    selected, stats = extract_relevant_lines(log_lines, query, **kwargs)
    print(f"\n{'=' * 90}\nCASE: {label}\nTicket query: {query!r}")
    print(
        f"input={stats['input_lines']} lines / {stats['input_raw_chars']} raw chars   "
        f"-> output={stats['output_lines']} lines / {stats['output_chars']} chars   "
        f"(-{stats['line_reduction_pct']}% lines, "
        f"-{stats['char_reduction_pct_vs_raw']}% chars vs raw)"
    )
    print(f"hinted_services from query: {stats['hinted_services']}")
    print(f"forced (error-level/auditor-window) lines: {stats['forced_count']}, "
          f"scored-in lines: {stats['scored_kept_count']}, "
          f"collapsed {stats['pre_collapse_lines']} -> {stats['output_lines']} after dedup")
    print("-- selected lines (chronological) --")
    for p in selected:
        preview = p.content[:160]
        print(f"  [{p.ts}] lvl={p.level:<7} svc={p.service_name:<12} "
              f"score={p.score:<7} reasons={p.reasons} :: {preview}")


if __name__ == "__main__":
    from pathlib import Path
    sample_path = Path(__file__).parent / "logs1.txt"
    log_lines = _load_sample(str(sample_path))

    run_case(
        "A) Video upload failure complaint",
        log_lines,
        "customer says the video did not upload after the KYC call, agent could not submit",
    )

    run_case(
        "B) Agent rejection reason dispute",
        log_lines,
        "bank agent rejected the KYC and customer is disputing the reason given",
    )

    run_case(
        "C) Audio / call disconnect complaint",
        log_lines,
        "customer says audio was not working and the call disconnected during the video KYC",
    )
