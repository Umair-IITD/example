"""
rag_engine/utils/tokens.py

Token counting and text splitting utilities using tiktoken.

Design:
  - Uses tiktoken (OpenAI's official tokenizer) for exact token counting.
  - Falls back to character-based estimation (4 chars ≈ 1 token) if tiktoken
    is not installed, so the pipeline degrades gracefully rather than crashing.
  - Encoders are cached globally — loading once per process, not per chunk.
  - Default model matches text-embedding-3-small (cl100k_base tokenizer).

All functions are stateless and safe for concurrent use once the cache is warm.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

LOGGER = logging.getLogger(__name__)

_WORD_RE = re.compile(r"\S+")

# Module-level encoder cache: model_name → tiktoken.Encoding | None
# None means tiktoken is unavailable; we only log the warning once.
_ENCODER_CACHE: dict[str, Any] = {}
_TIKTOKEN_WARNED = False


def _get_encoder(model: str) -> Any:
    """
    Return a cached tiktoken encoder for the given model name.
    Returns None if tiktoken is not installed; logs a one-time warning.
    """
    global _TIKTOKEN_WARNED  # noqa: PLW0603

    if model in _ENCODER_CACHE:
        return _ENCODER_CACHE[model]

    try:
        import tiktoken  # noqa: PLC0415
        try:
            enc = tiktoken.encoding_for_model(model)
        except KeyError:
            # Unknown model — cl100k_base covers all current OpenAI embedding models.
            LOGGER.debug(
                "tiktoken: no registered encoder for model %r; using cl100k_base", model
            )
            enc = tiktoken.get_encoding("cl100k_base")
        _ENCODER_CACHE[model] = enc
        return enc
    except ImportError:
        if not _TIKTOKEN_WARNED:
            LOGGER.warning(
                "tiktoken is not installed. Token counting will use character-based "
                "estimation (4 chars ≈ 1 token). Chunking may be slightly inaccurate. "
                "Install with: pip install tiktoken"
            )
            _TIKTOKEN_WARNED = True
        _ENCODER_CACHE[model] = None
        return None


def count_tokens(text: str, model: str = "text-embedding-3-small") -> int:
    """
    Return the number of tokens in text under the given model's tokenizer.
    Falls back to len(text) // 4 if tiktoken is unavailable.
    """
    if not text:
        return 0
    enc = _get_encoder(model)
    if enc is None:
        return max(1, len(text) // 4)
    return len(enc.encode(text))


def truncate_to_token_limit(
    text: str,
    max_tokens: int,
    model: str = "text-embedding-3-small",
) -> str:
    """
    Return text truncated to at most max_tokens tokens.
    Uses the model's exact tokenizer; safe for multi-byte characters.
    Falls back to character-based truncation if tiktoken is unavailable.
    """
    if not text:
        return text
    enc = _get_encoder(model)
    if enc is None:
        # 4 chars ≈ 1 token (conservative)
        return text[: max_tokens * 4]
    tokens = enc.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return enc.decode(tokens[:max_tokens])


def safe_split_by_tokens(
    text: str,
    max_tokens: int,
    overlap_tokens: int = 0,
    model: str = "text-embedding-3-small",
) -> list[str]:
    """
    Split text into chunks of at most max_tokens with optional token overlap.

    - Uses tiktoken for exact token boundaries.
    - Falls back to word-based splitting if tiktoken is unavailable.
    - Always returns at least one non-empty chunk for non-empty input.
    - Overlap_tokens must be < max_tokens; if not, overlap is set to max_tokens // 4.

    Args:
        text:           Input text to split.
        max_tokens:     Hard upper bound on tokens per chunk.
        overlap_tokens: Number of tokens to repeat at the start of the next chunk.
        model:          OpenAI model name (determines tokenizer).

    Returns:
        List of non-empty text chunks, each at most max_tokens tokens.
    """
    if not text or not text.strip():
        return []

    if overlap_tokens >= max_tokens:
        overlap_tokens = max_tokens // 4

    enc = _get_encoder(model)
    if enc is None:
        return _word_split_fallback(text, max_tokens, overlap_tokens)

    tokens = enc.encode(text)
    if len(tokens) <= max_tokens:
        return [text]

    step = max(1, max_tokens - overlap_tokens)
    chunks: list[str] = []
    start = 0

    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        chunk_text = enc.decode(tokens[start:end])
        if chunk_text.strip():
            chunks.append(chunk_text)
        if end >= len(tokens):
            break
        start += step

    return chunks if chunks else [text]


def recursive_split_if_oversized(
    text: str,
    max_tokens: int,
    model: str = "text-embedding-3-small",
    *,
    _depth: int = 0,
    _max_depth: int = 8,
) -> list[str]:
    """
    Last-resort recursive halving for pathological inputs.

    Use this when a single "word" is itself a multi-thousand-token blob
    (minified JSON, base64, hex dump, stack trace concatenated without spaces).
    ``safe_split_by_tokens`` handles normal prose; this handles edge cases.

    Guarantees:
      - Every returned part is <= max_tokens tokens.
      - At maximum recursion depth the part is hard-truncated (never raises).
      - Empty inputs return [].

    Args:
        text:       Input text; may be arbitrarily long.
        max_tokens: Hard upper bound per returned part.
        model:      Tokenizer model (passed to count_tokens / truncate_to_token_limit).
        _depth / _max_depth: Internal recursion guards — do not pass externally.

    Returns:
        Non-empty list of text parts, each <= max_tokens tokens.
    """
    if not text or not text.strip():
        return []

    token_count = count_tokens(text, model)
    if token_count <= max_tokens:
        return [text]

    if _depth >= _max_depth:
        LOGGER.warning(
            "recursive_split_if_oversized: depth limit %d reached with %d tokens. "
            "Truncating to %d tokens — some content will be lost.",
            _max_depth, token_count, max_tokens,
        )
        return [truncate_to_token_limit(text, max_tokens, model)]

    mid = len(text) // 2
    left, right = text[:mid], text[mid:]

    if not left.strip() or not right.strip():
        return [truncate_to_token_limit(text, max_tokens, model)]

    parts: list[str] = []
    parts.extend(recursive_split_if_oversized(left,  max_tokens, model, _depth=_depth + 1, _max_depth=_max_depth))
    parts.extend(recursive_split_if_oversized(right, max_tokens, model, _depth=_depth + 1, _max_depth=_max_depth))
    return [p for p in parts if p.strip()]


def _word_split_fallback(
    text: str,
    max_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    """
    Word-based split used when tiktoken is unavailable.
    Approximation: 0.75 words per token (English prose, conservative).
    """
    max_words = max(1, int(max_tokens * 0.75))
    overlap_words = max(0, int(overlap_tokens * 0.75))
    step = max(1, max_words - overlap_words)

    words = _WORD_RE.findall(text)
    if len(words) <= max_words:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + max_words, len(words))
        chunks.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start += step

    return chunks if chunks else [text]
