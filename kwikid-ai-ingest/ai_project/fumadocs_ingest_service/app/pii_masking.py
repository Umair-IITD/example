"""
app/pii_masking.py

PII masking applied synchronously at webhook ingress.

RBI V-CIP requirement: Aadhaar numbers must be masked before any downstream
processing (classifier, embedder, RAG pipeline, or audit logging). Masking is
one-way; only the last 4 digits are preserved to allow limited correlation
without exposing the full number.
"""
from __future__ import annotations

import re

# Matches 12-digit Aadhaar in three common formats:
#   123456789012      (no separator)
#   1234 5678 9012    (space-separated)
#   1234-5678-9012    (hyphen-separated)
# Word boundaries prevent partial matches within longer digit strings (e.g. 13-digit numbers).
_AADHAAR_RE = re.compile(r"\b(\d{4})[\s-]?(\d{4})[\s-]?(\d{4})\b")


def mask_aadhaar(text: str) -> str:
    """Replace Aadhaar numbers in text with XXXX XXXX <last-4>.

    Preserves the last 4 digits for limited correlation.
    All other digits are replaced with X.
    """
    return _AADHAAR_RE.sub(r"XXXX XXXX \3", text)
