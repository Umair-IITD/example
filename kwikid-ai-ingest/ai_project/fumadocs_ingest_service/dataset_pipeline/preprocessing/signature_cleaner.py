"""
Email signature and legal disclaimer stripper for Freshdesk Description fields.
Operates on already-HTML-stripped plain text.
"""
from __future__ import annotations

import re
from typing import Optional

# Signature boundary markers — everything from the first match onward is stripped
_SIG_BOUNDARY_PATTERNS: list[re.Pattern] = [
    # Common sign-off phrases
    re.compile(r"^\s*thanks\s*[&and]*\s*regards?\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*best\s*regards?\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*warm\s*regards?\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*kind\s*regards?\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*yours?\s*(sincerely|faithfully|truly)\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*sincerely\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*with\s*regards?\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*rgds\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*thank\s*you[,.]?\s*$", re.IGNORECASE | re.MULTILINE),
    # Email thread separators
    re.compile(r"^\s*-{3,}.*?original message.*?-{3,}", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*on\s+.{10,80}\s+wrote:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*from:\s+\S+@\S+", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*sent:\s+\w+,?\s+\w+\s+\d", re.IGNORECASE | re.MULTILINE),
    # Legal disclaimers
    re.compile(r"^\s*disclaimer\s*[:.]", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*confidentiality\s+(notice|statement)\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*this\s+(e-?mail|message)\s+(and\s+)?its?\s+attachments?", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*this\s+communication\s+(is\s+)?intended", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*if\s+you\s+(are\s+not|have\s+received\s+this)", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*please\s+do\s+not\s+(print|forward)\s+this\s+(e-?mail|message)", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*the\s+information\s+contained\s+in\s+this", re.IGNORECASE | re.MULTILINE),
    # Common bank boilerplate
    re.compile(r"^\s*note\s*:\s*the\s+information\s+in\s+this", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*this\s+email\s+does\s+not\s+constitute", re.IGNORECASE | re.MULTILINE),
    re.compile(r"caution\s*:\s*this\s+email", re.IGNORECASE),
]

# Inline cleanup — remove auto-reply noise without truncating the entire message
_INLINE_NOISE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\[External\]\s*", re.IGNORECASE),
    re.compile(r"##-\s*Please\s+type\s+your\s+reply\s+above\s+this\s+line\s*-##", re.IGNORECASE),
    re.compile(r"Get\s+Outlook\s+for\s+(iOS|Android)", re.IGNORECASE),
    re.compile(r"Sent\s+from\s+(my\s+)?(iPhone|iPad|Android|Galaxy|Samsung)", re.IGNORECASE),
    re.compile(r"Sent\s+from\s+Mail\s+for\s+Windows\b", re.IGNORECASE),
    re.compile(r"\[cid:[^\]]+\]", re.IGNORECASE),  # Outlook image CID references
]

_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def strip_signatures(text: Optional[str]) -> tuple[str, bool]:
    """
    Remove email signatures and legal disclaimers from plain-text ticket content.

    Returns:
        (cleaned_text, was_signature_stripped)
    """
    if not text or not text.strip():
        return (text or "").strip(), False

    # Inline noise removal first
    cleaned = text
    for pat in _INLINE_NOISE_PATTERNS:
        cleaned = pat.sub("", cleaned)

    # Find earliest signature boundary
    earliest_pos: Optional[int] = None
    for pat in _SIG_BOUNDARY_PATTERNS:
        m = pat.search(cleaned)
        if m:
            if earliest_pos is None or m.start() < earliest_pos:
                earliest_pos = m.start()

    was_stripped = False
    if earliest_pos is not None and earliest_pos > 0:
        cleaned = cleaned[:earliest_pos]
        was_stripped = True

    # Final whitespace normalization
    cleaned = _MULTI_BLANK_RE.sub("\n\n", cleaned).strip()
    return cleaned, was_stripped
