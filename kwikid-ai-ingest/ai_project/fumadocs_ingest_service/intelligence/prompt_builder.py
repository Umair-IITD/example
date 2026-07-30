"""
intelligence/prompt_builder.py

Wave 3, Part B: Deterministic, versioned prompt assembly.

Every prompt template is a class with:
  - PROMPT_VERSION       (bumped on every semantic change)
  - system_prompt        (fixed instructional preamble)
  - user_prompt(context) (assembles the request from LLMContext)

No string concatenation in runtime code — callers pass an `LLMContext` and
receive a `PromptPair(system, user)` back.

The five templates:
  1. ReasoningPromptTemplate         — root cause + confidence + JSON schema
  2. ClarificationPromptTemplate     — which questions to ask the customer
  3. ObservationPromptTemplate       — L1 private-note body
  4. CustomerReplyPromptTemplate     — public reply for the customer
  5. ActionProposalPromptTemplate    — structured ActionProposal

Every user_prompt is deterministic given identical LLMContext — this is
what makes the layer reproducible and testable.

Dependency direction: prompt_builder.py → stdlib + intelligence.models
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final

from intelligence.models import LLMContext


# ── Result type ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PromptPair:
    """A system/user prompt pair ready to send to the LLM."""
    system:         str
    user:           str
    prompt_version: str
    template_name:  str

    def to_dict(self) -> dict[str, object]:
        return {
            "system":         self.system,
            "user":           self.user,
            "prompt_version": self.prompt_version,
            "template_name":  self.template_name,
        }


# ── Base template ─────────────────────────────────────────────────────────────

class _PromptTemplate:
    """Abstract base — subclasses set NAME, VERSION, SYSTEM, and USER_HEADER."""
    NAME:    str = ""
    VERSION: str = ""
    SYSTEM:  str = ""

    @classmethod
    def build(cls, context: LLMContext) -> PromptPair:
        return PromptPair(
            system=         cls.SYSTEM.strip(),
            user=           cls._user_prompt(context),
            prompt_version= cls.VERSION,
            template_name=  cls.NAME,
        )

    @classmethod
    def _user_prompt(cls, context: LLMContext) -> str:
        raise NotImplementedError


def _render_evidence_block(context: LLMContext) -> str:
    """Deterministic PII-safe evidence block."""
    if not context.evidence_hints:
        return "  (no evidence collected)"
    lines = []
    for hint in context.evidence_hints:
        lines.append(f"  - {hint.source} ({hint.kind}): {hint.summary}")
    return "\n".join(lines)


def _render_chunks_block(context: LLMContext, max_chars: int = 3500) -> str:
    """Bounded SOP / knowledge chunk rendering."""
    if not context.retrieved_chunks:
        return "  (no SOP or knowledge chunks retrieved)"
    lines = []
    total = 0
    for i, chunk in enumerate(context.retrieved_chunks, start=1):
        header = f"  [{i}] {chunk.source} — {chunk.title} (score={chunk.score:.2f})"
        body = chunk.content[:max_chars // max(1, len(context.retrieved_chunks))]
        entry = f"{header}\n    {body}\n"
        if total + len(entry) > max_chars * 2:
            lines.append(f"  ... {len(context.retrieved_chunks) - i + 1} more chunks omitted for brevity")
            break
        lines.append(entry)
        total += len(entry)
    return "\n".join(lines)


def _render_conversation_block(context: LLMContext) -> str:
    if not context.conversation:
        return "  (no prior conversation)"
    return "\n".join(
        f"  {turn.get('role', 'unknown')}: {turn.get('content', '')[:500]}"
        for turn in context.conversation
    )


def _render_ticket_block(context: LLMContext) -> str:
    return (
        f"  ticket_id:    {context.ticket_id}\n"
        f"  tenant:       {context.tenant_id}\n"
        f"  subject:      {context.ticket_subject}\n"
        f"  description:  {context.ticket_description[:1500]}\n"
        f"  classification: {context.classification or 'none'}\n"
        f"  workflow:     {context.workflow_id or 'none'}\n"
        f"  customer:     {context.customer_display or '(unknown)'}"
    )


# ── 1. Reasoning Prompt ───────────────────────────────────────────────────────

class ReasoningPromptTemplate(_PromptTemplate):
    NAME:    Final[str] = "reasoning"
    # v1.1.0 — Added VKYC telemetry field guidance based on real RBL Bank portal
    # data analysis (Sprint 2.5.8). Instructs LLM to locate specific evidence
    # keys in the EvidenceBundle before forming a root-cause hypothesis.
    VERSION: Final[str] = "1.1.0"
    SYSTEM:  Final[str] = """
You are the KwikID support-automation Reasoning Engine.

You reason over a support ticket and its evidence to produce a STRUCTURED
JSON verdict. You NEVER take actions. You NEVER invent evidence. You NEVER
speculate beyond what the evidence supports.

Output MUST be a single JSON object with these EXACT fields:

{
  "outcome":                "RESOLVED" | "NEEDS_CLARIFICATION" | "ESCALATE" | "INSUFFICIENT_EVIDENCE",
  "summary":                "one-sentence issue statement",
  "root_cause":             "one-sentence root cause hypothesis, or 'unknown'",
  "confidence":             0.0-1.0 float,
  "evidence_used":          ["ref_id", ...],
  "missing_information":    ["what would you need to be more confident", ...],
  "clarification_required": true | false,
  "reasoning_notes":        "at most 3 sentences of internal reasoning"
}

Rules:
- If `confidence < 0.75` AND `missing_information` is non-empty →
  `outcome=NEEDS_CLARIFICATION` and `clarification_required=true`.
- If no evidence available → `outcome=INSUFFICIENT_EVIDENCE` and
  `confidence <= 0.30`.
- If evidence points to a platform issue you cannot fix → `outcome=ESCALATE`.
- Do NOT include markdown, comments, or explanatory prose OUTSIDE the JSON.
- Do NOT wrap the JSON in code fences.

VKYC SESSION FAILURE EVIDENCE — look for these fields in the EvidenceBundle:
- `nsdlPanResponse`: NSDL GRID PAN validation result. Check codes in the
  reasons array:
    YYN = name match Y, DOB match Y, Aadhaar seeding N
    YNN = name match Y, DOB N, Aadhaar seeding N
    RYN = restricted PAN, name Y, DOB N (customer name mismatch)
    RNY = restricted PAN, name N, DOB Y
  If a YNN or RYN code is present, root cause is PAN/NSDL validation failure.
- `ekyc_status_calc`: EKYC journey outcome (e.g. "KYC_PENDING", "FAILED").
  If non-success, the EKYC path itself failed.
- `ekyc_success`: Boolean. `false` means EKYC did not complete successfully.
- `ckycData`: CKYC data object. If null or missing, CKYC fetch failed.
  Present means the CKYC journey was used; absent means EKYC path was taken.
- `faceMatchThreshold` / `fmPath` / `fmTitle`: Face match result.
  Default threshold is 80. If session rejected, check if face match score
  was below threshold. `fmPath` identifies which document was matched
  (AadhaarXML, CKYC photo, PAN). `fmTitle` is human-readable document name.
- `concurrentReject.reasons[]`: Array of reason strings the bank agent
  selected when rejecting the session. Examples:
    "Video quality very bad"
    "PAN Card photo not clear"
    "As per NSDL GRID -YYN (grid shows N in DOB)"
    "As per NSDL GRID -RYN (shows N in DOB and n in customer name)"
  If reasons are present, the agent manually rejected. Use this as primary
  evidence of the rejection reason.
- `step_list`: List of VKYC steps completed. Identify the failure step:
    ID1 or ID-SELFIE = Selfie / liveness step
    ID2 or ID-PAN = PAN card capture
    ID3 or ID-QNA = Question & Answer set
    ID4 or ID-QNA-CKYC = CKYC Question & Answer set
- `dynamicStepSequence`: Modified step order. If it contains "CKYC" steps
  (e.g. ID-SELFIE-CKYC, ID-QNA-CKYC), the customer was in the CKYC journey
  rather than the standard EKYC journey. Report this in reasoning_notes.

DOCUMENT OCR FAILURE EVIDENCE — look for these fields:
- `PAN_VALIDATION` log entry: failure means OCR could not extract PAN data.
- `AADHAAR_VALIDATION` log entry: failure means Aadhaar OCR failed.
- `DMS_OPERATION_LOG`: Check if document image was received and stored.
- OCR confidence score in session summary: if below threshold, document was
  poor quality (glare, blur, damage). Note the score in reasoning_notes.
"""

    @classmethod
    def _user_prompt(cls, context: LLMContext) -> str:
        return (
            "SUPPORT TICKET\n"
            f"{_render_ticket_block(context)}\n\n"
            "EVIDENCE COLLECTED\n"
            f"{_render_evidence_block(context)}\n\n"
            "RELEVANT KNOWLEDGE (SOP / prior tickets)\n"
            f"{_render_chunks_block(context)}\n\n"
            "PRIOR CONVERSATION\n"
            f"{_render_conversation_block(context)}\n\n"
            "Return the JSON object described in the system prompt. "
            "No other text."
        )


# ── 2. Clarification Prompt ───────────────────────────────────────────────────

class ClarificationPromptTemplate(_PromptTemplate):
    NAME:    Final[str] = "clarification"
    VERSION: Final[str] = "1.0.0"
    SYSTEM:  Final[str] = """
You are drafting a clarification request to a bank customer whose KYC
issue we cannot resolve without more information.

Output MUST be JSON with EXACT fields:

{
  "should_clarify":   true | false,
  "questions":        ["one clear question per array entry"],
  "reason":           "one-sentence internal reason we need more info",
  "required_slots":   ["session_id" | "phone_number" | "issue_time" | ...]
}

Rules:
- MAX 3 questions.
- Each question must be actionable and phrased plainly (Grade-8 reading level).
- NEVER request PAN, Aadhaar, DOB, or biometric identifiers.
- NEVER ask for their password or OTP.
- NEVER include internal reasoning in the questions themselves.
- Return raw JSON only. No markdown. No code fences.
"""

    @classmethod
    def _user_prompt(cls, context: LLMContext) -> str:
        return (
            "TICKET\n"
            f"{_render_ticket_block(context)}\n\n"
            "MISSING INFORMATION (from Reasoning Engine)\n"
            "  See tool_results['missing_information'] if present.\n\n"
            "EVIDENCE ALREADY COLLECTED\n"
            f"{_render_evidence_block(context)}\n\n"
            "PRIOR CONVERSATION\n"
            f"{_render_conversation_block(context)}\n\n"
            "Return the JSON object described in the system prompt."
        )


# ── 3. Observation Prompt ────────────────────────────────────────────────────

class ObservationPromptTemplate(_PromptTemplate):
    NAME:    Final[str] = "observation"
    # v1.1.0 — Added VKYC telemetry field guidance based on real RBL Bank portal
    # data analysis (Sprint 2.5.8). Instructs LLM to surface NSDL GRID codes,
    # agent rejection reasons, and CKYC/EKYC journey path in observation body.
    VERSION: Final[str] = "1.1.0"
    SYSTEM:  Final[str] = """
You are drafting an L1 internal observation note for a Freshdesk support
ticket. This note is visible to human agents only — never to the customer.

Output MUST be JSON with EXACT fields:

{
  "issue_summary":        "one-sentence customer-impact statement",
  "evidence":             "3-6 bulleted evidence lines (as a plain string, use `\\n- ` between bullets)",
  "root_cause":           "one-sentence RCA",
  "recommended_action":   "one clear next action for the agent",
  "escalation":           "None" | "L2 Engineering" | "Human Review",
  "confidence_level":     "HIGH" | "MEDIUM" | "LOW",
  "body_html":            "final ready-to-post HTML matching the KwikID note style"
}

Rules:
- `body_html` must be safe: no <script>, no external images with credentials.
- Include ticket_id and tenant in the note header.
- NEVER include full PAN, Aadhaar, phone, or DOB.
- Return raw JSON. No markdown outside the JSON. No code fences.

VKYC OBSERVATION GUIDANCE — when evidence contains VKYC telemetry fields,
include the following in the `evidence` bullet list if present:
- NSDL GRID code from `nsdlPanResponse` (e.g. "NSDL GRID code: YYN —
  name match OK, DOB mismatch, Aadhaar seeding N"). Never include full PAN.
- Agent rejection reason from `concurrentReject.reasons[]` if present
  (e.g. "Agent selected rejection reason: PAN Card photo not clear").
- EKYC/CKYC journey path from `dynamicStepSequence` (e.g. "Session used
  CKYC journey: ID-SELFIE-CKYC → ID2 → ID-QNA-CKYC").
- Face match result: if `fmPath` and score available, note document matched
  and whether it passed or failed threshold.
- Step where failure occurred (from `step_list` or session logs).

For NSDL GRID codes, always spell out what the code means in plain English
so the L2 engineering team immediately understands the rejection reason
without needing to decode the code themselves.
"""

    @classmethod
    def _user_prompt(cls, context: LLMContext) -> str:
        return (
            "TICKET\n"
            f"{_render_ticket_block(context)}\n\n"
            "EVIDENCE\n"
            f"{_render_evidence_block(context)}\n\n"
            "SOP / KNOWLEDGE\n"
            f"{_render_chunks_block(context)}\n\n"
            "Return the JSON object described in the system prompt."
        )


# ── 4. Customer Reply Prompt ─────────────────────────────────────────────────

class CustomerReplyPromptTemplate(_PromptTemplate):
    NAME:    Final[str] = "customer_reply"
    VERSION: Final[str] = "1.0.0"
    SYSTEM:  Final[str] = """
You are drafting a professional customer reply for KwikID Bank Support.

Tone: warm, factual, empathetic. Grade-8 reading level. British English
spelling.

Output MUST be JSON with EXACT fields:

{
  "reply_kind":       "resolution" | "clarification" | "escalation",
  "body_html":        "final ready-to-send HTML",
  "confidence_level": "HIGH" | "MEDIUM" | "LOW",
  "confidence":       0.0-1.0 float,
  "citations":        ["<sop_url>", ...]   // include when SOP was used
}

Rules:
- Reply MUST cite ONLY facts present in the evidence + SOP chunks.
- NEVER invent SLAs, refund dates, refund amounts, contact numbers, or URLs.
- NEVER reveal internal reasoning or tool names.
- NEVER include customer PII beyond a polite "we" reference.
- For clarification: ask AT MOST 2 direct questions.
- Sign off: "KwikID Support Team".
- Return raw JSON. No markdown outside JSON. No code fences.
"""

    @classmethod
    def _user_prompt(cls, context: LLMContext) -> str:
        return (
            "TICKET\n"
            f"{_render_ticket_block(context)}\n\n"
            "EVIDENCE\n"
            f"{_render_evidence_block(context)}\n\n"
            "SOP / KNOWLEDGE\n"
            f"{_render_chunks_block(context)}\n\n"
            "PRIOR CONVERSATION\n"
            f"{_render_conversation_block(context)}\n\n"
            "Return the JSON object described in the system prompt."
        )


# ── 5. Action Proposal Prompt ────────────────────────────────────────────────

class ActionProposalPromptTemplate(_PromptTemplate):
    NAME:    Final[str] = "action_proposal"
    VERSION: Final[str] = "1.0.0"
    SYSTEM:  Final[str] = """
You are proposing (NEVER executing) one or more concrete actions the
KwikID Action Gateway may take.

Output MUST be a JSON object with ONE field `proposals`, a list of items:

{
  "proposals": [
    {
      "action_kind":       "SEND_REPLY" | "POST_INTERNAL_NOTE" | "ASK_CLARIFICATION" | "UPDATE_TICKET_FIELDS" | "RESOLVE_TICKET" | "ESCALATE_TO_ENGINEERING" | "NO_ACTION",
      "parameters":        { ... proposal-specific },
      "confidence":        0.0-1.0 float,
      "risk":              "SAFE" | "MEDIUM" | "HIGH" | "CRITICAL",
      "approval_required": true | false,
      "rationale":         "one-sentence why this action"
    }
  ]
}

Rules:
- Never propose actions whose parameters are not present in the evidence
  or SOP.
- `risk=CRITICAL` proposals MUST have `approval_required=true`.
- ALWAYS include AT LEAST one proposal — if nothing fits, propose
  `action_kind=NO_ACTION` with `risk=SAFE`.
- Return raw JSON. No markdown outside JSON. No code fences.
"""

    @classmethod
    def _user_prompt(cls, context: LLMContext) -> str:
        return (
            "TICKET\n"
            f"{_render_ticket_block(context)}\n\n"
            "EVIDENCE\n"
            f"{_render_evidence_block(context)}\n\n"
            "SOP / KNOWLEDGE\n"
            f"{_render_chunks_block(context)}\n\n"
            "Return the JSON object described in the system prompt."
        )


# ── Convenience façade ────────────────────────────────────────────────────────

class PromptBuilder:
    """
    Convenience façade so callers can write one line instead of remembering
    which template class to instantiate.

    Usage:
        pb = PromptBuilder()
        pair = pb.reasoning(context)
    """

    def reasoning(self, context: LLMContext) -> PromptPair:
        return ReasoningPromptTemplate.build(context)

    def clarification(self, context: LLMContext) -> PromptPair:
        return ClarificationPromptTemplate.build(context)

    def observation(self, context: LLMContext) -> PromptPair:
        return ObservationPromptTemplate.build(context)

    def customer_reply(self, context: LLMContext) -> PromptPair:
        return CustomerReplyPromptTemplate.build(context)

    def action_proposal(self, context: LLMContext) -> PromptPair:
        return ActionProposalPromptTemplate.build(context)

    def versions(self) -> dict[str, str]:
        return {
            "reasoning":       ReasoningPromptTemplate.VERSION,
            "clarification":   ClarificationPromptTemplate.VERSION,
            "observation":     ObservationPromptTemplate.VERSION,
            "customer_reply":  CustomerReplyPromptTemplate.VERSION,
            "action_proposal": ActionProposalPromptTemplate.VERSION,
        }
