"""
app/query_preprocessor.py
--------------------------
Domain-specific query normalization for KwikID support queries.

Applied before embedding: expands acronyms, normalises common misspellings,
and strips noise tokens that degrade vector search quality. The preprocessed
text is embedded; the original text is preserved for display and BM25 reranking
(BM25 benefits from original casing and exact phrasing).

Keep this module side-effect free — no I/O, no globals beyond the constant maps.
"""
from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# Acronym / abbreviation expansion
# ---------------------------------------------------------------------------
# Keys: lowercase exact tokens as they appear in user queries.
# Values: expanded form used in the embedded query string.
# Add new expansions here — they are applied in a single pass.

_ACRONYM_MAP: dict[str, str] = {
    # Identity & compliance
    "otp": "one time password OTP",
    "kyc": "know your customer KYC",
    "pan": "PAN card",
    "aadhar": "Aadhaar",
    "aadhaar": "Aadhaar identity",
    "gstin": "GST identification number GSTIN",
    "gst": "goods and services tax GST",
    "tan": "tax deduction account number TAN",
    "cin": "corporate identification number CIN",

    # Banking & payments
    "upi": "unified payments interface UPI",
    "neft": "national electronic funds transfer NEFT",
    "rtgs": "real time gross settlement RTGS",
    "imps": "immediate payment service IMPS",
    "nach": "national automated clearing house NACH",
    "emi": "equated monthly instalment EMI",
    "ecs": "electronic clearing service ECS",
    "ifsc": "Indian financial system code IFSC",
    "micr": "magnetic ink character recognition MICR",
    "noc": "no objection certificate NOC",

    # KwikID / onboarding specific
    "vkyc": "video KYC verification",
    "ekyc": "electronic KYC verification",
    "ckyc": "central KYC registry",
    "digilocker": "DigiLocker document vault",
    "nsdl": "NSDL",
    "uidai": "UIDAI Aadhaar authority",

    # Support / ticket context
    "sop": "standard operating procedure SOP",
    "tat": "turnaround time TAT",
    "escalation": "escalation issue",
    "l1": "level one support L1",
    "l2": "level two support L2",
    "csat": "customer satisfaction score CSAT",
    "nps": "net promoter score NPS",

    # Technical
    "api": "API application programming interface",
    "sdk": "SDK software development kit",
    "ui": "user interface UI",
    "ux": "user experience UX",
    "2fa": "two factor authentication 2FA",
    "mfa": "multi factor authentication MFA",
    "sso": "single sign on SSO",
    "jwt": "JSON web token JWT",
}

# Pre-compile: word-boundary aware replacement for each acronym
# Pattern: match the exact token (not as a substring of another word)
_ACRONYM_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_ACRONYM_MAP, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _expand_acronyms(text: str) -> str:
    def _replace(m: re.Match) -> str:
        token = m.group(0).lower()
        return _ACRONYM_MAP.get(token, m.group(0))
    return _ACRONYM_RE.sub(_replace, text)


# ---------------------------------------------------------------------------
# Common misspelling corrections
# ---------------------------------------------------------------------------
_CORRECTIONS: dict[str, str] = {
    "verfication": "verification",
    "verificaiton": "verification",
    "verifiaction": "verification",
    "onboarding": "onboarding",
    "onbording": "onboarding",
    "authorisation": "authorization",
    "authorise": "authorize",
    "cancelled": "canceled",
    "cheque": "check",
    "centre": "center",
    "colour": "color",
    "customise": "customize",
    "initialise": "initialize",
    "signupp": "signup",
    "loginn": "login",
    "passowrd": "password",
    "pasword": "password",
    "documnet": "document",
    "documemnt": "document",
}

_CORRECTION_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_CORRECTIONS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _correct_spelling(text: str) -> str:
    def _replace(m: re.Match) -> str:
        token = m.group(0).lower()
        corrected = _CORRECTIONS.get(token, m.group(0))
        # Preserve original casing style if all-caps
        if m.group(0).isupper():
            return corrected.upper()
        return corrected
    return _CORRECTION_RE.sub(_replace, text)


# ---------------------------------------------------------------------------
# Noise removal
# ---------------------------------------------------------------------------
# Sequences that add no semantic signal for vector search.
_NOISE_RE = re.compile(
    r"\b(please|kindly|asap|urgent|hi|hello|dear|thanks|thank you|regards|sir|ma'am|madam)\b",
    re.IGNORECASE,
)

# Collapse multiple whitespace characters
_WHITESPACE_RE = re.compile(r"\s{2,}")


def _remove_noise(text: str) -> str:
    text = _NOISE_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


# ---------------------------------------------------------------------------
# Unicode normalization
# ---------------------------------------------------------------------------

def _normalize_unicode(text: str) -> str:
    # NFKC: compose + compatibility decomposition (handles fancy quotes, em-dashes, etc.)
    return unicodedata.normalize("NFKC", text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def preprocess_query(query: str) -> str:
    """
    Normalize a raw user query for embedding.

    Applies in order:
      1. Unicode normalization (NFKC)
      2. Spelling corrections
      3. Acronym expansion (adds full form alongside abbreviation)
      4. Noise removal (filler words that dilute semantic signal)

    The original query text is preserved by callers for BM25 reranking;
    only the embedding step uses the preprocessed form.

    Args:
        query: Raw user query string.

    Returns:
        Preprocessed query string, never empty — falls back to original if
        processing produces an empty string.
    """
    if not query or not query.strip():
        return query

    result = _normalize_unicode(query)
    result = _correct_spelling(result)
    result = _expand_acronyms(result)
    result = _remove_noise(result)

    # Safety: never return empty string
    return result.strip() or query.strip()
