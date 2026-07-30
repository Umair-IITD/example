"""
investigate_session.py - SCRATCH TEST, not part of the project codebase.

End-to-end interactive test harness for the full proposed L1 pipeline, exactly
as designed in Unity_Loki_Integration_Findings.md §3/§6/§7:

    session_id
      -> unity.get_session_details(session_id)            [ALREADY BUILT]
      -> derive timeline window + auditor feedback         [ALREADY BUILT]
      -> loki_client.fetch_session_logs(...)               [built earlier this
         (raw, complete backend log pull for that window)   research pass]
      -> log_extractor.extract_relevant_lines(...)         [built earlier this
         (BM25 + error/warn + auditor-window + service      research pass]
          hints, with PII redaction + truncation)
      -> print the final, curated, ticket-relevant excerpt
         (this is what would actually be handed to an LLM)

Run from this folder (Loki_Log_Tool_Blueprint/), on YOUR machine, inside the
real project's Python environment (needs httpx installed, same as
unity_to_loki_test.py):

    python investigate_session.py

You will be prompted for:
  1. Session ID (Unity) - required.
  2. Ticket description - optional. Paste the bank agent's actual ticket
     text here if you have it (e.g. "customer says video did not upload").
     If you leave this blank, the script auto-derives a query from what
     Unity's own get_details response already knows (session_status +
     auditor.feedback + derive_failure_summary) - useful for a quick test,
     but a real ticket's own wording will usually rank better, since BM25
     is a lexical match against whatever text you give it.

This script does not modify the real project or either log sample; it only
reads the real `unity` package (via sys.path, same as unity_to_loki_test.py)
and this folder's own loki_client.py / log_extractor.py.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Point at the REAL project code so we can import the already-built unity/
# package, exactly as unity_to_loki_test.py does. Nothing there is modified.
# Adjust this path if your checkout differs.
# ---------------------------------------------------------------------------
FUMADOCS_SERVICE_DIR = Path(
    r"C:\Users\Umair.Alam\Desktop\kwikid_support_system\kwikid-ai-ingest\ai_project\fumadocs_ingest_service"
)
sys.path.insert(0, str(FUMADOCS_SERVICE_DIR))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(FUMADOCS_SERVICE_DIR / ".env")
except ImportError:
    env_path = FUMADOCS_SERVICE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from unity.client import UnityClient                      # noqa: E402
from unity.config import UnityConfig                       # noqa: E402
from unity.normalizer import parse_session_details, derive_failure_summary  # noqa: E402

# This folder's own tools, built earlier in this research pass.
from loki_client import LokiClient, fetch_session_logs, get_credentials  # noqa: E402
from log_extractor import extract_relevant_lines                          # noqa: E402


# ---------------------------------------------------------------------------
# Auditor "duration X:XX to Y:YY" sub-window parser (Unity_Loki_Integration_
# Findings.md §6.3). Converts a relative on-call duration mentioned in the
# auditor's own feedback text into an absolute UTC window, anchored to the
# session's vkyc_start_time, so those exact log lines are force-included by
# extract_relevant_lines() regardless of their BM25/level score - this is the
# one place a human auditor already pinpointed the moment of a technical
# failure, so the extraction should never risk scoring it out.
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(
    r"duration\s+(\d+):(\d+)\s+to\s+(\d+):(\d+)", re.IGNORECASE
)


def parse_auditor_duration_window(
    feedback_text: str | None, vkyc_start_epoch: float | None
) -> tuple[datetime, datetime] | None:
    if not feedback_text or not vkyc_start_epoch:
        return None
    m = _DURATION_RE.search(feedback_text)
    if not m:
        return None
    m1, s1, m2, s2 = (int(x) for x in m.groups())
    start_offset = timedelta(minutes=m1, seconds=s1)
    end_offset = timedelta(minutes=m2, seconds=s2)
    base = datetime.fromtimestamp(vkyc_start_epoch, tz=timezone.utc)
    return (base + start_offset, base + end_offset)


def build_fallback_query(session, raw: dict) -> str:
    """
    When the agent doesn't paste real ticket text, derive a reasonable
    BM25 query from what Unity's own get_details response already knows.
    Not a substitute for real ticket wording (see module docstring) - just
    makes the script usable for a quick test with no ticket text at hand.
    """
    parts: list[str] = []
    if session.session_status:
        parts.append(session.session_status.value.replace("_", " "))
    if session.auditor and session.auditor.feedback:
        parts.append(session.auditor.feedback)
    if session.auditor and session.auditor.result:
        parts.append(session.auditor.result)
    fs = derive_failure_summary(session)
    if fs.get("failure_category") not in (None, "NONE"):
        parts.append(str(fs.get("failure_message") or fs.get("failure_category")))
    return ". ".join(p for p in parts if p) or "video KYC session issue"


async def _get_unity_session(session_id: str):
    config = UnityConfig.from_env()
    print(
        f"UnityConfig: base_url={config.base_url} domain={config.domain} "
        f"username={config.masked_username} has_credentials={config.has_credentials}"
    )
    async with UnityClient(config) as client:
        raw = await client.get_session_details(session_id)
        session = parse_session_details(raw)
        return session, raw


# IST display only - all internal computation (Loki queries, BM25 scoring,
# the auditor-window match) stays in UTC throughout, exactly as before. This
# is purely so the printed output can be sanity-checked directly against the
# Unity admin portal UI, which displays IST. Confirmed this session: a
# derived window of 04:09:55 -> 04:15:28 UTC is EXACTLY 09:39:55 -> 09:45:28
# IST (UTC+5:30) - i.e. the derivation was already correct, the UTC-only
# printout just made it look like a mismatch against the portal's IST
# display. Printing both removes that ambiguity going forward.
_IST = timezone(timedelta(hours=5, minutes=30))


def _epoch_to_dt(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def _fmt_both(dt: datetime) -> str:
    return f"{dt.isoformat()} UTC  ({dt.astimezone(_IST).strftime('%Y-%m-%d %H:%M:%S')} IST)"


def main() -> None:
    session_id = input("Session ID (Unity): ").strip()
    ticket_text = input(
        "Ticket description (paste the bank agent's ticket text, or press "
        "Enter to auto-derive one from Unity's own session data): "
    ).strip()

    # ---- Step 1: get_session_details (admin portal - ALREADY BUILT) ------
    print(f"\n{'=' * 90}\nSTEP 1: unity.get_session_details({session_id})\n{'=' * 90}")
    try:
        session, raw = asyncio.run(_get_unity_session(session_id))
    except Exception as exc:  # noqa: BLE001 - diagnostic script
        print(f"FAILED calling Unity admin portal: {type(exc).__name__}: {exc}")
        return

    if session is None:
        print("Unity returned no session_data for this session_id (empty/malformed body).")
        print("Raw response:", raw)
        return

    print(f"session_status = {session.session_status.value}")
    print(f"client_name     = {session.client_name!r}")
    if session.auditor and (session.auditor.result or session.auditor.feedback):
        print(f"auditor.result   = {session.auditor.result!r}")
        print(f"auditor.feedback = {session.auditor.feedback!r}")
    fs = derive_failure_summary(session)
    print(f"derive_failure_summary() = {fs}")

    t = session.timeline
    print(
        f"\ntimeline (epoch seconds): init_time={t.init_time} start_time={t.start_time} "
        f"end_time={t.end_time} vkyc_start_time={t.vkyc_start_time} "
        f"last_active_timestamp={t.last_active_timestamp}"
    )

    # Same fallback chain as unity_to_loki_test.py: not every session status
    # populates every timeline field (e.g. abandoned pre-video sessions).
    start_epoch = t.start_time or t.vkyc_start_time or t.init_time
    end_epoch = t.end_time or t.last_active_timestamp or start_epoch
    if not start_epoch:
        print("\nWARNING: no usable start time in the timeline fields above - stopping.")
        return

    start_dt, end_dt = _epoch_to_dt(start_epoch), _epoch_to_dt(end_epoch)
    print(f"Derived session window:")
    print(f"  start: {_fmt_both(start_dt)}")
    print(f"  end:   {_fmt_both(end_dt)}")
    print(
        "  (compare the IST values above directly against the Unity admin "
        "portal UI - it displays IST, not UTC. If they don't match, that's "
        "a real bug; if only the UTC values look 'off' by 5:30, that's "
        "expected - see the comment above _epoch_to_dt.)"
    )

    # ---- Ticket query (typed, or auto-derived from Unity data) -----------
    if not ticket_text:
        ticket_text = build_fallback_query(session, raw)
        print(f"\n(no ticket text entered - auto-derived query: {ticket_text!r})")
    else:
        print(f"\nUsing entered ticket text as the query: {ticket_text!r}")

    # Auditor sub-window (e.g. "duration 1:19 to 2:40") - force-included below
    # regardless of BM25/level score, if the auditor's feedback mentions one.
    feedback_text = session.auditor.feedback if session.auditor else ""
    auditor_window = parse_auditor_duration_window(
        feedback_text, t.vkyc_start_time or t.start_time
    )
    if auditor_window:
        print(
            f"Auditor-flagged sub-window detected in feedback text: "
            f"{_fmt_both(auditor_window[0])} -> {_fmt_both(auditor_window[1])} "
            f"(these lines will always be included regardless of score)"
        )

    # ---- Step 2: fetch_session_logs (raw pull - NEW, this research pass) -
    print(f"\n{'=' * 90}\nSTEP 2: fetch_session_logs against 'saas' Loki\n{'=' * 90}")
    tenant = input("Loki tenant to query [saas]: ").strip() or "saas"
    username, password = get_credentials()
    buffer = timedelta(minutes=5)
    try:
        with LokiClient(tenant, username, password) as loki:
            raw_text = fetch_session_logs(
                loki, session_id, start_dt - buffer, end_dt + buffer
            )
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED querying Loki: {type(exc).__name__}: {exc}")
        return

    raw_lines = [l for l in raw_text.splitlines() if l.startswith("[20")]
    raw_chars = sum(len(l) for l in raw_lines)
    print(f"Raw pull: {len(raw_lines)} lines / {raw_chars} chars")
    if not raw_lines:
        print(
            "No matching log lines. This can mean either (a) genuinely no "
            "backend activity in this window, or (b) the data has already "
            "aged out of Loki's retention window / been cleared - see "
            "Unity_Loki_Integration_Findings.md for a real reproduced "
            "example of (b). Do not assume 'no technical issue' from this "
            "alone."
        )
        return

    # ---- Step 3: extract_relevant_lines (NEW, this research pass) --------
    print(f"\n{'=' * 90}\nSTEP 3: extract_relevant_lines (BM25 + level + auditor-window + service hints)\n{'=' * 90}")
    selected, stats = extract_relevant_lines(
        raw_lines, ticket_text, auditor_window=auditor_window
    )
    print(
        f"input={stats['input_lines']} lines / {stats['input_raw_chars']} raw chars   "
        f"-> output={stats['output_lines']} lines / {stats['output_chars']} chars   "
        f"(-{stats['line_reduction_pct']}% lines, "
        f"-{stats['char_reduction_pct_vs_raw']}% chars vs raw)"
    )
    print(f"hinted_services from query: {stats['hinted_services']}")
    print(
        f"forced (error-level/auditor-window) lines: {stats['forced_count']}, "
        f"scored-in lines: {stats['scored_kept_count']}"
    )

    print(f"\n{'=' * 90}\nFINAL CURATED LOG EXCERPT - this is what would be fed to the LLM\n{'=' * 90}")
    for p in selected:
        ist_str = p.ts.astimezone(_IST).strftime("%H:%M:%S") if p.ts else "??:??:??"
        print(f"[{p.ts} | {ist_str} IST] lvl={p.level:<7} svc={p.service_name:<12} score={p.score:<7} :: {p.content}")


if __name__ == "__main__":
    main()
