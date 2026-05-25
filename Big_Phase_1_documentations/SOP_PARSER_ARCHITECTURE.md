# SOP Parser Architecture

## Overview

The SOP parser (`rag_engine/sop/sop_parser.py`) replaces the previous keyword-tuple heuristic approach. It detects structural branches and safety elements in SOP (Standard Operating Procedure) documents using compiled regex patterns.

## Why This Was Built

The previous approach used keyword tuples with Python's `in` operator:
```python
# OLD (REMOVED) — multiple bugs:
_BRANCH_SECURITY_FREEZE_KW = ("security freeze", "pending.*review", ...)
if any(kw in text for kw in _BRANCH_SECURITY_FREEZE_KW):
    ...
```

**Problems with the old approach:**
1. `"pending.*review"` was treated as a literal substring (`.` and `*` are not special in `in` checks) — it never matched
2. No word-boundary enforcement — partial substring matches caused false positives
3. Not reusable outside the generation module
4. Regex patterns buried inside conditional logic were hard to test independently

## Architecture

### `SopDocumentFlags` (frozen dataclass)

```python
@dataclass(frozen=True)
class SopDocumentFlags:
    has_escalation_branches: bool
    has_denial_branches: bool
    has_security_freeze: bool
    has_post_resolution: bool
    has_mandatory_warnings: bool

    def any_set(self) -> bool: ...
    def to_dict(self) -> dict[str, bool]: ...
```

Frozen = immutable after construction = thread-safe = safely shared across concurrent requests.

### The 7 Regex Patterns

All patterns use:
- `re.IGNORECASE` — case-insensitive matching
- Bounded quantifiers only — no `.*` or `.+` without explicit bounds (ReDoS prevention)
- Anchored or word-boundary-aware alternatives

```python
# 1. Conditional bold formatting (SOP decision branches in markdown)
_CONDITIONAL_BOLD_RE = re.compile(
    r"\*\*(?:If|When|In\s+case)\b[^*]{2,120}?\*\*",
    re.IGNORECASE
)
# Matches: **If the customer fails OTP 3 times:** ...

# 2. Prohibitions — explicit denial of actions
_DENIAL_RE = re.compile(
    r"\b(?:do\s+not|never|must\s+not|cannot|do\s+NOT)\s+"
    r"(?:unlock|proceed|attempt|bypass|reveal|issue|remove|perform|reset|confirm)",
    re.IGNORECASE
)
# Matches: "do not unlock" / "never attempt" / "must not bypass"

# 3. Plain conditional sentence openers (non-bold variants)
_CONDITIONAL_PLAIN_RE = re.compile(
    r"^(?:If|When|In\s+case)\s+(?:the\s+)?(?:customer|agent|user|ticket)\b.{5,150}:",
    re.IGNORECASE | re.MULTILINE
)
# Matches: "If the customer fails verification:" at line start

# 4. Escalation triggers
_ESCALATION_RE = re.compile(
    r"\bescalate(?:\s+(?:to|this|the|immediately))?\b"
    r"|\bsecurity\s+team\b|\bfraud\s+team\b|\baccount\s+takeover\b"
    r"|\bsupervisor\b|\bhigh.priority\s+ticket\b"
    r"|\bunauthorized\s+access\b|\bcredential\s+stuffing\b|\bforeign\s+ip\b",
    re.IGNORECASE
)

# 5. Security freeze scenarios (requires ops-level intervention)
_SECURITY_FREEZE_RE = re.compile(
    r"security\s+freeze"
    r"|not\s+resolvable\s+by\s+front.?line"
    r"|cannot\s+be\s+resolved\s+by\s+front.?line"
    r"|database.level\s+intervention"
    r"|pending\s+(?:a\s+)?(?:security\s+)?review"  # Fixed: proper regex, not substring
    r"|clearance\s+required",
    re.IGNORECASE
)

# 6. Post-resolution checklists
_POST_RESOLUTION_RE = re.compile(
    r"post.(?:unlock|resolution)\s+(?:checklist|steps?|requirements?)"
    r"|log\s+the\s+identity\s+verification"
    r"|document\s+the\s+(?:unlock|resolution)"
    r"|record\s+(?:this|the)\s+(?:incident|case|ticket)",
    re.IGNORECASE
)

# 7. Absolute prohibitions and mandatory actions
_MANDATORY_RE = re.compile(
    r"\bNEVER\s+(?:unlock|bypass|skip|proceed|issue|allow|share|reveal)"
    r"|\bMandatory\b\s*[:\-]"
    r"|\bfailure\s+to\s+(?:do\s+so|comply)\b"
    r"|\bconstitutes?\s+a\s+security\s+violation\b"
    r"|\bunder\s+no\s+circumstances\b",
    re.IGNORECASE
)
```

### `parse_sop_content(text: str) -> SopDocumentFlags`

Primary API — parses a raw text string:

```python
def parse_sop_content(text: str) -> SopDocumentFlags:
    if not text or not text.strip():
        return _EMPTY_FLAGS
    return SopDocumentFlags(
        has_escalation_branches=bool(_ESCALATION_RE.search(text)),
        has_denial_branches=(
            bool(_DENIAL_RE.search(text))
            or bool(_CONDITIONAL_BOLD_RE.search(text))
            or bool(_CONDITIONAL_PLAIN_RE.search(text))
        ),
        has_security_freeze=bool(_SECURITY_FREEZE_RE.search(text)),
        has_post_resolution=bool(_POST_RESOLUTION_RE.search(text)),
        has_mandatory_warnings=bool(_MANDATORY_RE.search(text)),
    )
```

### `parse_sop_sections(sections: Sequence[dict]) -> SopDocumentFlags`

For parsing multiple pre-split sections (OR-union of all section flags).

## Integration Points

### 1. Generation Pipeline (`chat_generator.py`)

Runs at inference time on the combined content of all retrieved SOP chunks:

```python
if sop_chunks_in_result and workflow_match_type in ("exact_match", "related_match"):
    combined_sop_text = "\n\n".join(c.content for c in sop_chunks_in_result)
    sop_flags = parse_sop_content(combined_sop_text)
    if sop_flags.any_set():
        diagnostics["sop_branch_flags"] = sop_flags.to_dict()
```

### 2. Context Assembler (`context_assembler.py`)

Annotates SOP chunk headers with detected flags to guide the LLM:

```python
flags = parse_sop_content(chunk.content)
branch_tags = []
if flags.has_escalation_branches: branch_tags.append("escalation:YES")
if flags.has_denial_branches:     branch_tags.append("denial:YES")
# ...
header = f"##1 [SOP | sop_id={chunk.sop_id} | score={score:.3f} | AUTHORITATIVE{branch_str}]"
```

### 3. Validation Suite (`validate_response_governance.py`)

14 unit tests (P01–P14) verify the parser against known SOP content:

- P01: Empty string → all False
- P02: Escalation keyword → `has_escalation_branches = True`
- P03: Bold conditional → `has_denial_branches = True`
- P04: "NEVER unlock" → `has_mandatory_warnings = True`
- P05: "security freeze" → `has_security_freeze = True`
- P06: "pending review" (the old bug case) → `has_security_freeze = True`
- P07: Post-resolution checklist → `has_post_resolution = True`
- P08: Full SOP with all branches → all five flags True
- P09–P14: Edge cases and cross-contamination prevention

## ReDoS Safety Analysis

All patterns use bounded quantifiers:
- `[^*]{2,120}` — hard max 120 chars
- `[^\s<>"']{10,}` — bounded by character class (URL matching)
- `(?:to|this|the|immediately)?` — optional group with finite alternatives
- `.{5,150}` — bounded length

No unbounded `.*` or `.+` patterns in the critical path. Worst-case backtracking is O(n) per pattern.

## Design Decisions

**Why regex over NLP/ML?**
- Zero latency overhead — regex runs in microseconds
- No model loading at startup
- Deterministic — same input always produces same output
- Testable — 14 unit tests cover all flag paths
- No GPU requirement

**Why frozen dataclass over dict?**
- Type safety — all flags are booleans with known names
- Immutability — cannot be accidentally mutated between pipeline stages
- `to_dict()` for serialization to JSON diagnostics
