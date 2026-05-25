"""
rag_engine/sop/sop_parser.py

Structure-aware SOP branch detection using compiled regex patterns.

Replaces the fragile keyword-tuple substring approach in chat_generator.py.
Regex patterns are anchored to SOP prose structures (conditionals, imperatives,
section headings) rather than arbitrary keyword fragments, reducing false positives
and fixing bugs like "pending.*review" being treated as a literal substring.

Two public entry points:

  parse_sop_content(text: str)  -> SopDocumentFlags
      For generation-time use: scans the combined text of retrieved SOP chunks.

  parse_sop_sections(sections: list[dict])  -> SopDocumentFlags
      For ingestion-time use: scans section dicts from SopDocumentBuilder._split_sections().
      Each dict has keys "heading" and "content".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence


# ── Compiled regex patterns ───────────────────────────────────────────────────
# Each pattern targets a structural feature of SOP markdown prose. All are
# case-insensitive. Specificity is preferred over recall: a false negative
# (missed branch) is less dangerous than a false positive (spurious mandate).


# Conditional branches: bold **If ...** or plain "if only / if zero / if no"
_CONDITIONAL_BOLD_RE = re.compile(
    r"\*\*(?:If|When|In\s+case)\b[^*]{2,120}?\*\*",
    re.IGNORECASE,
)

# Plain-prose conditionals that signal a decision branch
_CONDITIONAL_PLAIN_RE = re.compile(
    r"\bif\s+(?:only|zero|no\s+|the\s+customer|status\s+is|account\s+is|verification\s+fails)",
    re.IGNORECASE,
)

# Denial / restriction: imperative "do NOT / never / must not / cannot" + action verb
_DENIAL_RE = re.compile(
    r"\b(?:do\s+not|never|must\s+not|cannot|do\s+NOT)\s+"
    r"(?:unlock|proceed|attempt|bypass|reveal|issue|remove|perform|reset|confirm)",
    re.IGNORECASE,
)

# Escalation triggers: named teams, ticket-priority language, takeover/fraud signals
_ESCALATION_RE = re.compile(
    r"\bescalate(?:\s+(?:to|this|the|immediately))?\b"
    r"|\bsecurity\s+team\b"
    r"|\bfraud\s+team\b"
    r"|\baccount\s+takeover\b"
    r"|\bsupervisor\b"
    r"|\bhigh.priority\s+ticket\b"
    r"|\bunauthorized\s+access\b"
    r"|\bcredential\s+stuffing\b"
    r"|\bforeign\s+ip\b",
    re.IGNORECASE,
)

# Security Freeze state: must be handled by security team, not front-line agents
# Note: "pending.*review" with regex quantifiers is the CORRECT form here — this
# is a regex, not a substring literal (the bug in the old keyword tuple).
_SECURITY_FREEZE_RE = re.compile(
    r"security\s+freeze"
    r"|not\s+resolvable\s+by\s+front.?line"
    r"|cannot\s+be\s+resolved\s+by\s+front.?line"
    r"|database.level\s+intervention"
    r"|pending\s+(?:a\s+)?(?:security\s+)?review"
    r"|clearance\s+required",
    re.IGNORECASE,
)

# Post-resolution / post-unlock mandatory steps
_POST_RESOLUTION_RE = re.compile(
    r"post.(?:unlock|resolution)\s+(?:checklist|steps?|requirements?)"
    r"|log\s+the\s+identity\s+verification"
    r"|identity\s+verification\s+(?:has\s+been\s+)?logged"
    r"|confirm\s+the\s+customer\s+(?:successfully\s+)?(?:logged|can\s+log)"
    r"|audit\s+trail"
    r"|before\s+(?:the\s+)?(?:ticket\s+)?(?:is\s+)?clos(?:ed|ing|ure)"
    r"|must\s+be\s+documented",
    re.IGNORECASE,
)

# Mandatory safety warnings: NEVER / Mandatory / "failure to do so"
_MANDATORY_RE = re.compile(
    r"\bNEVER\s+(?:unlock|bypass|skip|proceed|issue|allow|share|reveal)"
    r"|\bMandatory\b\s*[:\-]"
    r"|\bfailure\s+to\s+(?:do\s+so|comply)\b"
    r"|\bconstitutes?\s+a\s+security\s+violation\b"
    r"|\bunder\s+no\s+circumstances\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SopDocumentFlags:
    """Detected branch types in a set of SOP chunks or sections.

    All flags are boolean. True means at least one structural marker for that
    branch type was found in the scanned content. False means not detected
    (may still be present but not matched by the patterns).
    """
    has_escalation_branches: bool
    has_denial_branches: bool
    has_security_freeze: bool
    has_post_resolution: bool
    has_mandatory_warnings: bool

    def any_set(self) -> bool:
        """Return True if at least one flag is detected."""
        return (
            self.has_escalation_branches
            or self.has_denial_branches
            or self.has_security_freeze
            or self.has_post_resolution
            or self.has_mandatory_warnings
        )

    def to_dict(self) -> dict[str, bool]:
        return {
            "has_escalation_branches": self.has_escalation_branches,
            "has_denial_branches":     self.has_denial_branches,
            "has_security_freeze":     self.has_security_freeze,
            "has_post_resolution":     self.has_post_resolution,
            "has_mandatory_warnings":  self.has_mandatory_warnings,
        }


# Sentinel for empty / no-content case — all flags False
_EMPTY_FLAGS = SopDocumentFlags(
    has_escalation_branches=False,
    has_denial_branches=False,
    has_security_freeze=False,
    has_post_resolution=False,
    has_mandatory_warnings=False,
)


def parse_sop_content(text: str) -> SopDocumentFlags:
    """Scan combined SOP chunk text for structural branch markers (generation-time).

    Args:
        text: Combined content of retrieved SOP chunks. Caller is responsible
              for joining multiple chunks before passing (e.g. "\\n\\n".join(...)).

    Returns:
        SopDocumentFlags with detected branch types. Returns _EMPTY_FLAGS (all
        False) when text is empty or whitespace-only.
    """
    if not text or not text.strip():
        return _EMPTY_FLAGS

    return SopDocumentFlags(
        has_escalation_branches=bool(_ESCALATION_RE.search(text)),
        has_denial_branches=bool(_DENIAL_RE.search(text)) or bool(
            _CONDITIONAL_BOLD_RE.search(text)
        ) or bool(_CONDITIONAL_PLAIN_RE.search(text)),
        has_security_freeze=bool(_SECURITY_FREEZE_RE.search(text)),
        has_post_resolution=bool(_POST_RESOLUTION_RE.search(text)),
        has_mandatory_warnings=bool(_MANDATORY_RE.search(text)),
    )


def parse_sop_sections(sections: Sequence[dict]) -> SopDocumentFlags:
    """Scan SopDocumentBuilder section dicts for structural branch markers (ingestion-time).

    Args:
        sections: List of dicts with "heading" and "content" keys, as produced
                  by SopDocumentBuilder._split_sections().

    Returns:
        SopDocumentFlags with detected branch types across all sections.
    """
    if not sections:
        return _EMPTY_FLAGS

    combined = "\n\n".join(
        f"{s.get('heading', '')}\n{s.get('content', '')}"
        for s in sections
    )
    return parse_sop_content(combined)
