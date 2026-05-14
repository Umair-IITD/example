"""
HTML → plain text converter for Freshdesk Description fields.
Uses BeautifulSoup4 for structural parsing, then regex for residual cleanup.
"""
from __future__ import annotations

import html
import re
from typing import Optional

from bs4 import BeautifulSoup  # type: ignore

# Patterns that survive after BS4 stripping
_NBSP_RE = re.compile(r"\xa0+")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_TRAILING_WHITESPACE_RE = re.compile(r"[ \t]+\n")
_BULLET_NORMALIZE_RE = re.compile(r"^[•·▪▸\-–]\s*", re.MULTILINE)

# Block-level tags whose content should be preceded by a newline
_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "td", "th",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "hr", "table",
}


def strip_html(text: Optional[str]) -> tuple[str, bool]:
    """
    Convert HTML-contaminated ticket text to clean plain text.

    Returns:
        (cleaned_text, was_html_stripped)
    """
    if not text:
        return "", False

    raw = text.strip()
    if not raw:
        return "", False

    # Fast-path: no HTML at all
    if "<" not in raw and "&" not in raw:
        return _post_clean(raw), False

    # Unescape HTML entities first (handles &amp; &lt; &nbsp; &#160; etc.)
    raw = html.unescape(raw)

    was_html = bool(re.search(r"<[a-zA-Z/!]", raw))

    if was_html:
        cleaned = _bs4_extract(raw)
    else:
        # Had entities but no tags — just unescape + normalize
        cleaned = _post_clean(raw)

    return cleaned, was_html


def _bs4_extract(html_text: str) -> str:
    """Use BeautifulSoup to extract readable text, preserving block structure."""
    soup = BeautifulSoup(html_text, "html.parser")

    # Remove noise nodes entirely
    for tag in soup(["script", "style", "head", "meta", "link", "noscript"]):
        tag.decompose()

    # Insert newlines at block boundaries before stripping
    for tag in soup.find_all(True):
        if tag.name in _BLOCK_TAGS:
            tag.insert_before("\n")
            tag.insert_after("\n")

    text = soup.get_text(separator=" ")
    return _post_clean(text)


def _post_clean(text: str) -> str:
    """Final whitespace and formatting normalization."""
    text = _NBSP_RE.sub(" ", text)
    text = _BULLET_NORMALIZE_RE.sub("- ", text)
    # Collapse lines that are only whitespace
    lines = [line.rstrip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    return text.strip()
