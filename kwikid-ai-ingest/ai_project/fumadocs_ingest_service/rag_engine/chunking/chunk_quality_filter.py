"""
rag_engine/chunking/chunk_quality_filter.py

Semantic quality gate applied to all chunk types before embedding.

A chunk that fails is discarded with a logged reason rather than being
sent to the embedding API. This prevents three failure modes:

  1. Semantic space pollution — boilerplate embeddings cluster together,
     making them indistinguishable from each other and from real queries.

  2. Wasted embedding API cost — sending text with no retrievable signal
     burns API budget for zero retrieval benefit.

  3. Reranker signal dilution — when most candidates are near-identical
     boilerplate, the reranker loses the ability to discriminate.

Checks performed (in order, cheapest first):
  1.  min_content_chars        — fast gate before any regex work
  2.  min_alpha_chars          — rejects numeric/timestamp/status blobs
  2b. MIME / encoded content   — four sub-checks (see below)
  3.  min_token_count          — rejects one-liner fragments
  4.  min_unique_token_ratio   — rejects highly repetitive content
  5.  max_boilerplate_density  — rejects content dominated by known phrase patterns
  6.  within-batch dedup       — exact content-hash dedup within one ingestion run

MIME / encoded content sub-checks (step 2b):
  2b-i.   Full RFC 2047 encoded-word  =?charset?B?payload?=
  2b-ii.  Partial MIME header         =?charset?B? (no closing ?= — partial strip artifact)
  2b-iii. Long continuous base64 blob ≥40 chars (unspaced attachment payloads)
  2b-iv.  Encoded-token ratio         token-level classifier for space-split fragments
            e.g. "LTE7LTE7 Tw1MNQBl4lNN SGVsbG8=" — each is short but together dominate

Cross-batch dedup is handled by the existing content_hash column in the
chunk tables (upsert on conflict). This module adds a within-batch layer
so the same text is not embedded twice in a single run.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

LOGGER = logging.getLogger(__name__)

# ── Boilerplate phrase patterns ────────────────────────────────────────────────
_BOILERPLATE_PHRASES: tuple[str, ...] = (
    "within sla",
    "sla breach: no",
    "sla breach: false",
    "resolution status:",
    "resolved via sop",
    "resolved by sop",
    "resolved using sop",
    "auto-resolved",
    "auto resolved",
    "issue resolved",
    "ticket closed",
    "status: resolved",
    "status: closed",
    "no rca provided",
    "rca not available",
    "escalation: no",
    "escalation: false",
    "[troubleshooting and resolution]",
    "[issue summary]",
    "[customer query]",
    "[root cause and fix]",
)

_BOILERPLATE_RE = re.compile(
    "|".join(re.escape(p) for p in _BOILERPLATE_PHRASES),
    re.IGNORECASE,
)

_ALPHA_RE = re.compile(r"[a-zA-Z]")
_TOKEN_RE = re.compile(r"\S+")

# ── MIME / base64 encoded content detection ────────────────────────────────────
#
# Four patterns, ordered cheapest to most expensive.
#
# 2b-i. Full RFC 2047 encoded-word:  =?charset?B?base64text?=
#   Catches when the complete MIME structure survives preprocessing.
_MIME_ENCODED_RE = re.compile(
    r"=\?[a-zA-Z0-9_\-]+\?[BbQq]\?[A-Za-z0-9+/=]+\?=",
)

# 2b-ii. Partial MIME header:  =?charset?B?  (no closing ?=)
#   Catches when the email preprocessor strips the base64 payload body but
#   leaves the opening header fragment. E.g. "=?utf-8?B?" alone in a chunk.
#   Real prose never contains this character sequence.
_MIME_PARTIAL_RE = re.compile(
    r"=\?[a-zA-Z0-9_\-]+\?[BbQq]\?",
)

# 2b-iii. Long continuous base64 blob (≥40 chars, no whitespace breaks).
#   Catches raw pasted attachment payloads. Threshold kept at 40 to avoid
#   false-positives on long English words ("responsibilities", 16 chars).
_BASE64_RUN_RE = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")

# 2b-iv. Encoded-like token classifier (two-criterion function below).
#
#   Criterion A — mixed digit+letter (catches LTE7LTE7, Tw1MNQBl4lNN, SGVsbG8gV29ybGQ):
#     len ≥ 6, purely alphanumeric, contains ≥1 digit AND ≥1 letter.
#     Key insight: genuine English words almost never embed a digit mid-token.
#
#   Criterion B — pure-alpha with high case-switch ratio (catches YWJjMTIzZGVm, ZXhhbXBsZQ):
#     len ≥ 8, purely alphabetic, case transitions / (len-1) > 0.40.
#     Base64 output has random upper/lower alternation; English words are all-lower,
#     title-case, or all-upper — never random.
#
#   True positives:  LTE7LTE7 (A), Tw1MNQBl4lNN (A), YWJjMTIzZGVm (B, csr=0.45),
#                    ZXhhbXBsZQ (B, csr=0.44), SGVsbG8gV29ybGQ (A)
#   True negatives:  customer (all-lower, no digit), TIMEOUT_E001 (has _),
#                    req-a1b2c3 (has -), 9876543210 (no letter),
#                    KwikID (len=6 <8 and no digit → neither criterion),
#                    KwikVerify (csr=0.33 ≤ 0.40 → B does not fire),
#                    ThinkKYC (csr=0.29 → safe), QuickBrown (csr=0.33 → safe)


def _is_encoded_token(tok: str) -> bool:
    """
    Return True if `tok` looks like a base64 MIME payload fragment.

    Criterion A (digit+letter mix): catches tokens like LTE7LTE7, Tw1MNQBl4lNN.
      - len ≥ 6, purely alphanumeric, has ≥1 digit AND ≥1 letter.
      - Hyphens/underscores/dots are separators → structured IDs, not encoded.

    Criterion B (case-switch entropy): catches purely-alphabetic base64 output
    like YWJjMTIzZGVm (encodes "abc123") or ZXhhbXBsZQ (encodes "example.com").
      - len ≥ 8 (guards against short domain acronyms: KwikID=6, eKYC=4).
      - purely alphabetic (digits would be caught by criterion A).
      - case_switch_ratio = transitions / (len-1) > 0.40.
      - English words: all-lower → 0, title-case → 1/(len-1) ≈ 0.11–0.14,
        all-upper → 0. Random case alternation in base64: typically 0.40–0.55.
      - Verified safe: KwikVerify=0.33, ThinkKYC=0.29, QuickBrown=0.33 (all ≤ 0.40).
    """
    n = len(tok)
    if n < 6:
        return False
    # Criterion A
    if tok.isalnum():
        has_letter = any(c.isalpha() for c in tok)
        has_digit  = any(c.isdigit() for c in tok)
        if has_letter and has_digit:
            return True
    # Criterion B (purely-alpha, longer tokens only)
    if n >= 8 and tok.isalpha():
        switches = sum(1 for i in range(n - 1) if tok[i].isupper() != tok[i + 1].isupper())
        if switches / (n - 1) > 0.40:
            return True
    return False


# ── Result + config dataclasses ────────────────────────────────────────────────

@dataclass
class ChunkFilterResult:
    """Outcome of a single quality check."""
    is_valid: bool
    rejection_reason: Optional[str] = None
    # Low-level diagnostics — useful for debugging threshold tuning
    diagnostics: dict = field(default_factory=dict)


@dataclass
class ChunkQualityConfig:
    """
    Configurable thresholds for the semantic quality filter.

    Defaults are conservative — they catch obvious garbage while allowing
    borderline content through. Tighten thresholds only after measuring
    recall impact on benchmark queries.
    """
    # ── Basic content quality ──────────────────────────────────────────────────
    min_content_chars: int = 30
    min_alpha_chars: int = 20
    min_token_count: int = 8
    min_unique_token_ratio: float = 0.25
    max_boilerplate_density: float = 0.70

    # ── MIME / encoded content thresholds ─────────────────────────────────────
    # Master switch. Set to False to disable all MIME/base64 checks (rollback).
    reject_mime_encoded: bool = True

    # Hard reject if this fraction of meaningful tokens (len ≥ 3) are
    # encoded-like (digit + letter, len ≥ 6, purely alphanumeric).
    # At 0.40: a chunk with many transaction IDs (TXN..., REF..., ACC...) passes;
    # a MIME-dominated chunk (LTE7LTE7, Tw1MNQBl4lNN, ...) rejects.
    encoded_token_ratio_max: float = 0.40

    # Combined soft reject: used ONLY when encoded_token_ratio > 0.25.
    # If fewer than this fraction of tokens are pure alphabetic, the chunk
    # likely has no natural language anchor — reject alongside the encoded signal.
    # A real ticket with IDs still has >35% plain prose words.
    pure_alpha_ratio_min: float = 0.35

    # ── Within-batch dedup ─────────────────────────────────────────────────────
    enable_batch_dedup: bool = True


# ── Main filter class ──────────────────────────────────────────────────────────

class ChunkQualityFilter:
    """
    Applies semantic quality checks to chunk text before embedding.

    Per-chunk checks are stateless. Within-batch dedup uses a set that
    should be reset (via reset_batch()) at the start of each ingestion run.

    Thread safety: NOT thread-safe. Each worker thread should own its instance.
    """

    def __init__(self, config: Optional[ChunkQualityConfig] = None) -> None:
        self._cfg = config or ChunkQualityConfig()
        self._seen_hashes: set[str] = set()
        self._rejected_counts: dict[str, int] = {}

    def reset_batch(self) -> None:
        """Reset within-batch dedup state. Call at the start of each ingestion run."""
        self._seen_hashes.clear()
        self._rejected_counts.clear()

    def rejection_summary(self) -> dict[str, int]:
        """Return aggregated rejection reason counts since last reset_batch()."""
        return dict(self._rejected_counts)

    def check(self, content: str, *, context: str = "") -> ChunkFilterResult:
        """
        Run all quality checks on chunk text.

        Args:
            content: Raw chunk text (will be stripped internally).
            context: Optional tag for log messages, e.g. "ticket_id=123 section=RESOLUTION_RCA".

        Returns:
            ChunkFilterResult. is_valid=True means the chunk may be embedded.
        """
        content = content.strip()
        diag: dict = {}

        # ── 1. Minimum character count (fast gate) ─────────────────────────────
        char_count = len(content)
        diag["char_count"] = char_count
        if char_count < self._cfg.min_content_chars:
            return self._reject(f"too_short:{char_count}_chars", diag)

        # ── 2. Alphabetic character count ──────────────────────────────────────
        alpha_count = len(_ALPHA_RE.findall(content))
        diag["alpha_count"] = alpha_count
        if alpha_count < self._cfg.min_alpha_chars:
            return self._reject(f"low_alpha:{alpha_count}", diag)

        # ── 2b. MIME / encoded content (four sub-checks) ──────────────────────
        if self._cfg.reject_mime_encoded:

            # i. Full RFC 2047 encoded-word =?charset?B?payload?=
            if _MIME_ENCODED_RE.search(content):
                diag["mime_encoded"] = True
                return self._reject("mime_encoded_word", diag)

            # ii. Partial MIME header =?charset?B? (no closing ?=)
            #     The preprocessor may strip the body but leave the opening.
            if _MIME_PARTIAL_RE.search(content):
                diag["mime_partial_header"] = True
                return self._reject("mime_partial_header", diag)

            # iii. Long continuous base64 blob (≥40 chars without spaces).
            #      Covers unspaced raw attachment payloads.
            b64_runs = _BASE64_RUN_RE.findall(content)
            if b64_runs:
                b64_chars = sum(len(r) for r in b64_runs)
                b64_density = b64_chars / char_count
                diag["base64_run_density"] = round(b64_density, 4)
                if b64_density > 0.40:
                    return self._reject(f"base64_blob:{b64_density:.3f}", diag)

            # iv. Token-level encoded-fragment analysis.
            #     Catches space-split MIME payloads where each fragment is short
            #     (LTE7LTE7=8, Tw1MNQBl4lNN=12, YWJjMTIzZGVm=12) — individually
            #     below the 40-char run floor but collectively dominating the chunk.
            #
            #     Two ratios on tokens of length ≥ 3:
            #       enc_ratio   — fraction classified as encoded by _is_encoded_token()
            #       alpha_ratio — fraction that are purely alphabetic AND not encoded
            #                     (encoded pure-alpha base64 like YWJjMTIzZGVm must not
            #                     inflate alpha_ratio; criterion B of _is_encoded_token
            #                     catches them via case-switch entropy)
            meaningful = [t for t in _TOKEN_RE.findall(content) if len(t) >= 3]
            if len(meaningful) >= 3:
                enc_count     = sum(1 for t in meaningful if _is_encoded_token(t))
                alpha_count_t = sum(1 for t in meaningful
                                    if t.isalpha() and not _is_encoded_token(t))
                enc_ratio     = enc_count / len(meaningful)
                alpha_ratio   = alpha_count_t / len(meaningful)
                diag["encoded_token_ratio"] = round(enc_ratio, 4)
                diag["pure_alpha_ratio"]    = round(alpha_ratio, 4)

                # Hard reject: chunk dominated by encoded payload fragments
                if enc_ratio > self._cfg.encoded_token_ratio_max:
                    return self._reject(f"encoded_token_ratio:{enc_ratio:.3f}", diag)

                # Combined soft reject:
                #   elevated encoded tokens (>25%) AND very few real words (<35%).
                #   A legitimate ticket with many IDs still has >35% plain prose.
                if enc_ratio > 0.25 and alpha_ratio < self._cfg.pure_alpha_ratio_min:
                    return self._reject(
                        f"low_natural_language:enc={enc_ratio:.2f},alpha={alpha_ratio:.2f}",
                        diag,
                    )

        # ── 3. Token count ─────────────────────────────────────────────────────
        tokens = _TOKEN_RE.findall(content)
        token_count = len(tokens)
        diag["token_count"] = token_count
        if token_count < self._cfg.min_token_count:
            return self._reject(f"too_few_tokens:{token_count}", diag)

        # ── 4. Unique token ratio ──────────────────────────────────────────────
        unique_count = len(set(t.lower() for t in tokens))
        unique_ratio = unique_count / token_count
        diag["unique_token_ratio"] = round(unique_ratio, 4)
        if unique_ratio < self._cfg.min_unique_token_ratio:
            return self._reject(f"low_diversity:{unique_ratio:.3f}", diag)

        # ── 5. Boilerplate density ─────────────────────────────────────────────
        bp_matches = _BOILERPLATE_RE.findall(content)
        if bp_matches:
            matched_chars = sum(len(m) for m in bp_matches)
            bp_density = matched_chars / char_count
            diag["boilerplate_density"] = round(bp_density, 4)
            if bp_density > self._cfg.max_boilerplate_density:
                return self._reject(f"boilerplate:{bp_density:.3f}", diag)

        # ── 6. Within-batch exact dedup ────────────────────────────────────────
        if self._cfg.enable_batch_dedup:
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            diag["content_hash_prefix"] = content_hash[:12]
            if content_hash in self._seen_hashes:
                return self._reject("batch_duplicate", diag)
            self._seen_hashes.add(content_hash)

        return ChunkFilterResult(is_valid=True, diagnostics=diag)

    def filter_batch(
        self,
        chunks: list[dict],
        content_key: str = "content",
        context_key: str = "",
    ) -> tuple[list[dict], list[tuple[dict, ChunkFilterResult]]]:
        """
        Filter a list of chunk dicts in place.

        Args:
            chunks:      List of chunk dicts, each containing at minimum content_key.
            content_key: Dict key holding chunk text (default: "content").
            context_key: Optional dict key to use in log messages (e.g., "ticket_id").

        Returns:
            (valid_chunks, rejected_pairs) where rejected_pairs is a list of
            (chunk_dict, ChunkFilterResult) tuples for diagnostic logging.
        """
        valid: list[dict] = []
        rejected: list[tuple[dict, ChunkFilterResult]] = []

        for chunk in chunks:
            content = (chunk.get(content_key) or "").strip()
            ctx     = str(chunk.get(context_key, "")) if context_key else ""
            result  = self.check(content, context=ctx)

            if result.is_valid:
                valid.append(chunk)
            else:
                rejected.append((chunk, result))
                LOGGER.debug(
                    "QualityFilter: REJECT reason=%s chars=%d%s",
                    result.rejection_reason,
                    result.diagnostics.get("char_count", 0),
                    f" [{ctx}]" if ctx else "",
                )

        if rejected:
            LOGGER.info(
                "ChunkQualityFilter: %d/%d passed, %d rejected — reasons: %s",
                len(valid), len(chunks), len(rejected),
                self._reason_summary(rejected),
            )

        return valid, rejected

    # ── Private helpers ────────────────────────────────────────────────────────

    def _reject(self, reason: str, diagnostics: dict) -> ChunkFilterResult:
        base_reason = reason.split(":")[0]
        self._rejected_counts[base_reason] = self._rejected_counts.get(base_reason, 0) + 1
        return ChunkFilterResult(is_valid=False, rejection_reason=reason, diagnostics=diagnostics)

    @staticmethod
    def _reason_summary(rejected: list[tuple[dict, ChunkFilterResult]]) -> str:
        counts: dict[str, int] = {}
        for _, r in rejected:
            base = (r.rejection_reason or "unknown").split(":")[0]
            counts[base] = counts.get(base, 0) + 1
        return ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
