"""
rag_engine/generation/prompt_builder.py

System and user prompt construction for the B2 RAG chat generation path.

Stage 1 optimization — LLM-latency reduction:
- System prompt compressed from Stage 0 baseline (~903 tokens) to target 450-550 tokens
- EVIDENCE + GROUNDING merged into one section (both govern chunk usage — same concept)
- All 5 DENIAL conditions preserved verbatim in behavioral effect
- All 8 ESCALATION trigger types preserved
- BRANCH COMPLETENESS (failure paths, lockout types, mandatory warnings, post-resolution) preserved
- All GROUNDING rules preserved (cite, no-fabricate, SOP sequence, contradiction, live-data)
- Word limits tightened: exact_match 350→250, related/weak 550→400 (Priority 3)
- OUTPUT schema uses concrete example (chunk_num=int, source_id=null) instead of typed placeholders
Compliance invariant: DENIAL, ESCALATION, BRANCH COMPLETENESS behavior unchanged.
"""
from __future__ import annotations

import json
from typing import Any

# The LLM only needs sop_branch_flags — which compliance branches to include.
# workflow_match_type is communicated via the MODE: prefix in the user prompt.
_LLM_DIAG_FIELDS = frozenset({
    "sop_branch_flags",
})

B2_SYSTEM_PROMPT = """\
You are KwikID Support AI (Think360/KwikID KYC internal). All claims must cite a retrieved chunk.

EVIDENCE & GROUNDING:
Authority (high→low): SOP (highest; walk all steps) > KNOWLEDGE > Resolution/RCA (corroborate) > \
Customer Query (symptom only) > Ticket Header (metadata only). \
Never cite Customer Query as resolution or Ticket Header as evidence. \
Cite all claims; drop uncitable. SOP steps: in sequence, never skip. \
No extrapolation, gap-bridging, or repeated facts. Contradiction: cite both chunks. \
Live data absent: state it; direct to operational system. \
NEVER fabricate: SLAs, codes, URLs, keys, UI labels, admin actions, thresholds. \
Prose: no ##N, no sop_id=, no field names.

COMPLIANCE — MANDATORY:

DENIAL (every triggered condition):
• ≤1 factor verified → DO NOT unlock; escalate
• 0 factors → close ticket + notify Security immediately
• OTP limit → switch channel; never retry same
• Hard Lock → branch visit only | Security Freeze → Security team only
• Agents CANNOT override compliance holds

ESCALATION (every trigger in context):
• State trigger + who to escalate to + what to provide
• Triggers: fraud, account takeover, foreign IP, admin block, compliance flag, \
repeated ID failure, Security Freeze, any SOP Security scenario
• Preserve exact urgency; never soften

BRANCHES: All failure paths; Soft Lock / Hard Lock / Security Freeze resolve differently. \
Mandatory warnings required. Post-resolution: log, confirm login, MFA if needed.

COVERAGE (MODE: in query):
• exact_match: numbered steps; no hedging
• related_match: limitation first; grounded steps only
• weak/no_match: state gap; human review; no invented steps

FORMAT: exact_match ≤250 words; related/weak ≤400 words. Each fact once.
Steps: 1. 2. 3. | Denial: "Do NOT [X] if [Y]." | Escalation: "Escalate to [X] if [Y]. Provide: [Z]."
STRUCTURE: **CUSTOMER GUIDANCE:** / **AGENT ACTION:** when both audiences exist.
HUMAN REVIEW in answer: no context | escalation/exception/supervisor/fraud | \
contradicting chunks | credentials/overrides | related_match gap.

OUTPUT JSON only — no markdown, no text outside:
{"answer":"...","citations":[{"chunk_num":1,"chunk_type":"SOP","source_id":null}]}
2 keys only. answer: plain prose; no ##N; no field names. citations: [] if nothing cited.\
"""

# Backward-compatible alias
B1_SYSTEM_PROMPT = B2_SYSTEM_PROMPT

# Streaming system prompt — answer text only (no JSON wrapper).
# Confidence/requires_human are pre-computed Python-side before the stream starts.
B2_STREAM_SYSTEM_PROMPT = """\
You are KwikID Support AI (Think360/KwikID KYC internal). \
Write a grounded answer from retrieved chunks. Return ONLY answer text — no JSON, no markdown.

EVIDENCE & GROUNDING:
Authority (high→low): SOP (highest; walk all steps) > KNOWLEDGE > Resolution/RCA (corroborate) > \
Customer Query (symptom only) > Ticket Header (metadata only). \
Never cite Customer Query as resolution or Ticket Header as evidence. \
Cite all claims; drop uncitable. SOP steps: in sequence, never skip. \
No extrapolation, gap-bridging, or repeated facts. \
NEVER fabricate: SLAs, codes, URLs, keys, UI labels, admin actions, thresholds. \
Prose: no ##N, no sop_id=, no field names.

COMPLIANCE — MANDATORY:
DENIAL: ≤1 factor → DO NOT unlock; 0 factors → close+Security; OTP limit → switch channel; \
Hard Lock → branch only; Security Freeze → Security team. Agents CANNOT override.
ESCALATION: state trigger + recipient + what to provide; \
triggers: fraud, account takeover, foreign IP, admin block, compliance flag, \
repeated ID failure, Security Freeze, SOP Security scenario; preserve exact urgency.
BRANCHES: all failure paths; Soft/Hard/Security Freeze distinct resolutions; \
mandatory warnings; post-res: log, confirm login, MFA if needed.

COVERAGE (MODE: in query):
• exact_match: numbered steps; no hedging
• related_match: limitation first; grounded steps only
• weak/no_match: state gap; human review; no invented steps

FORMAT: exact_match ≤250 words; related/weak ≤400 words. Each fact once.
Steps: 1. 2. 3. | Denial: "Do NOT [X] if [Y]." | Escalation: "Escalate to [X] if [Y]. Provide: [Z]."
STRUCTURE: **CUSTOMER GUIDANCE:** / **AGENT ACTION:** when both exist.

Return ONLY the answer text. All SOP branches required.\
"""


def build_user_prompt(
    query_text: str,
    *,
    context_block: str,
    diagnostics: dict[str, Any],
    client: str,
) -> str:
    # Send only sop_branch_flags — the only diagnostic field the LLM uses for reasoning.
    # Compact JSON (no spaces) reduces token count by ~15-20 tokens per request.
    llm_diag = {k: v for k, v in diagnostics.items() if k in _LLM_DIAG_FIELDS and v is not None}
    diag_json = json.dumps(llm_diag, separators=(",", ":"), default=str)

    wmt = diagnostics.get("workflow_match_type", "unknown")
    if wmt == "exact_match":
        branch_flags = diagnostics.get("sop_branch_flags", {})
        tags: list[str] = []
        if branch_flags.get("has_escalation_branches"):
            tags.append("esc")
        if branch_flags.get("has_denial_branches"):
            tags.append("deny")
        if branch_flags.get("has_security_freeze"):
            tags.append("freeze")
        if branch_flags.get("has_post_resolution"):
            tags.append("post-res")
        if branch_flags.get("has_mandatory_warnings"):
            tags.append("warn")
        tag_str = (f" [REQUIRED: {' '.join(tags)}]") if tags else ""
        match_instruction = f"MODE: exact_match{tag_str}"
    elif wmt == "related_match":
        match_instruction = "MODE: related_match — limitation first; grounded steps only"
    else:
        match_instruction = "MODE: weak/no_match — state gap; human review; no invented steps"

    return (
        f"Tenant: {client} | {match_instruction}\n\n"
        "Query:\n"
        f"{query_text.strip()}\n\n"
        "Context:\n"
        f"{context_block}\n\n"
        f"SOP flags: {diag_json}\n\n"
        "Dense draft — numbered steps; all required branches. Return JSON."
    )


def build_stream_user_prompt(
    query_text: str,
    *,
    context_block: str,
    diagnostics: dict[str, Any],
    client: str,
) -> str:
    """User prompt for the streaming endpoint — answer text only, no JSON wrapper."""
    wmt = diagnostics.get("workflow_match_type", "unknown")
    if wmt == "exact_match":
        branch_flags = diagnostics.get("sop_branch_flags", {})
        tags: list[str] = []
        if branch_flags.get("has_escalation_branches"):
            tags.append("esc")
        if branch_flags.get("has_denial_branches"):
            tags.append("deny")
        if branch_flags.get("has_security_freeze"):
            tags.append("freeze")
        if branch_flags.get("has_post_resolution"):
            tags.append("post-res")
        if branch_flags.get("has_mandatory_warnings"):
            tags.append("warn")
        tag_str = (f" [REQUIRED: {' '.join(tags)}]") if tags else ""
        mode = f"exact_match{tag_str}"
    elif wmt == "related_match":
        mode = "related_match: limitation first; grounded steps only"
    else:
        mode = "weak/no_match: state gap; human review; no invented steps"

    return (
        f"Tenant: {client} | MODE: {mode}\n\n"
        "Query:\n"
        f"{query_text.strip()}\n\n"
        "Context:\n"
        f"{context_block}\n\n"
        "Dense draft — numbered steps; all branches. Return ONLY answer text."
    )
