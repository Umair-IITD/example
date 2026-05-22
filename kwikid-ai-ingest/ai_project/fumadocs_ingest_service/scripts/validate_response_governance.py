"""
scripts/validate_response_governance.py

Phase B3 Response Governance Validation Suite.

Tests all Python-side governance logic introduced for strict SOP-grounded response
policy. Does NOT make LLM or Supabase calls unless --live is specified.

Test matrix covers:
  - Account Lockout SOP (AUTH_ISSUE)
  - OTP Delivery Failure SOP (AUTH_ISSUE / OTP_ISSUE)
  - Video KYC Session Failure SOP (VIDEO_KYC)
  - Unsupported workflows (no matching SOP)
  - Partial / related SOP coverage
  - Escalation scenarios
  - Automation safety gate

Usage:
  python scripts/validate_response_governance.py           # offline governance logic only
  python scripts/validate_response_governance.py --verbose # show all test details

Exit codes:
  0 — all tests pass
  1 — one or more tests failed
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Import governance helpers directly (no LLM/DB required for offline tests)
# ---------------------------------------------------------------------------
try:
    import os, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
    from rag_engine.generation.chat_generator import (
        _classify_workflow_match,
        _derive_requires_human,
        _is_automation_safe,
        _automation_block_reason,
        _escalation_trigger_reason,
        _check_answer_completeness,
        _WORKFLOW_EXACT_MATCH_SIMILARITY,
        _WORKFLOW_RELATED_MATCH_SIMILARITY,
    )
    from rag_engine.sop.sop_parser import parse_sop_content, parse_sop_sections
    _IMPORTS_OK = True
except ImportError as _e:
    print(f"[WARN] Could not import governance helpers: {_e}")
    print("       Run from the service root or activate the project virtualenv.")
    _IMPORTS_OK = False


# ---------------------------------------------------------------------------
# Hallucination marker patterns — detected in synthetic answer strings
# ---------------------------------------------------------------------------
# These patterns in an answer text indicate fabricated or leaked content.
_HALLUCINATION_PATTERNS: list[tuple[str, str]] = [
    (r"##\d+",                         "chunk ##N reference in answer prose"),
    (r"\bsop_id\s*=",                  "raw SOP ID leaked into answer"),
    (r"\bchunk_id\b",                  "chunk_id field leaked into answer"),
    (r"\bworkflow_match_type\b",       "diagnostic field leaked into answer"),
    (r"\bretrieval_confidence\b",      "diagnostic field leaked into answer"),
    (r"\bgrounding_confidence\b",      "diagnostic field leaked into answer"),
    (r"\bexact_sop_match\b",           "diagnostic field leaked into answer"),
    (r"\bpartial_match_detected\b",    "diagnostic field leaked into answer"),
    (r"\bautomation_safe\b",           "diagnostic field leaked into answer"),
    (r"\bautomation_block_reason\b",   "diagnostic field leaked into answer"),
    (r"\bescalation_trigger_reason\b", "diagnostic field leaked into answer"),
    (r"\bbest_sop_score\b",            "diagnostic field leaked into answer"),
    (r"Click (the )?Forgot Password",  "fabricated UI element: Forgot Password button"),
    (r"press the .{1,40} button",      "fabricated UI button reference"),
    (r"go to the .{1,40} page",        "fabricated page navigation"),
    (r"\bdiagnostics\b",               "diagnostic block term leaked into answer"),
    (r"rag_sop_chunks",                "internal table name leaked"),
    (r"rag_ticket_chunks",             "internal table name leaked"),
    (r"rag_knowledge_chunks",          "internal table name leaked"),
    (r"rag_knowledge_articles",        "internal table name leaked"),
    (r"rag_review_queue",              "internal table name leaked"),
    (r"\buuid\b",                      "raw UUID leaked into answer"),
]

# Phrasing that must NEVER appear in exact_match answers — indicates over-defensiveness
# or internal reference leakage despite a strong SOP match.
_EXACT_MATCH_FORBIDDEN_PHRASES: list[tuple[str, str]] = [
    ("according to the sop",           "defensive qualifier 'According to the SOP'"),
    ("the sop indicates",              "defensive qualifier 'The SOP indicates'"),
    ("the sop mentions",               "defensive qualifier 'The SOP mentions'"),
    ("i found procedures related",     "related-match opener in an exact-match answer"),
    ("based on retrieved documents",   "internal grounding language in answer"),
    ("the current sops",               "internal SOP reference in answer"),
    ("refer to sop",                   "direct SOP citation in answer prose"),
    ("based on the sop",               "defensive qualifier 'Based on the SOP'"),
    ("the sop states",                 "defensive qualifier 'The SOP states'"),
]


# ---------------------------------------------------------------------------
# Test scenario definition
# ---------------------------------------------------------------------------
@dataclass
class GovernanceScenario:
    name:                str
    query:               str
    has_sop_context:     bool
    has_knowledge_context: bool
    best_similarity:     float
    retrieval_confidence: str
    chunk_count:         int
    llm_confidence:      str          = "medium"
    llm_requires_human:  bool         = False
    expected_workflow_match: str      = "no_match"
    expected_requires_human: bool     = True
    expected_automation_safe: Optional[bool] = None
    synthetic_answer:    str          = ""    # used for hallucination marker checks
    required_answer_content: list[str] = field(default_factory=list)  # terms that MUST appear


# ---------------------------------------------------------------------------
# Test scenarios covering all 3 SOPs + edge cases
# ---------------------------------------------------------------------------
SCENARIOS: list[GovernanceScenario] = [
    # ── Account Lockout SOP ──────────────────────────────────────────────────
    GovernanceScenario(
        name="T01 — Account Lockout: exact query (high confidence)",
        query="account locked after multiple failed login attempts",
        has_sop_context=True, has_knowledge_context=False,
        best_similarity=0.78, retrieval_confidence="high", chunk_count=3,
        llm_confidence="high", llm_requires_human=False,
        expected_workflow_match="exact_match",
        expected_requires_human=False,
        expected_automation_safe=True,
        synthetic_answer="Verify identity via two factors. Unlock in admin panel under Customer > Account > Security Status.",
    ),
    GovernanceScenario(
        name="T02 — Account Lockout: paraphrased (high confidence)",
        query="user cannot log in, too many wrong attempts",
        has_sop_context=True, has_knowledge_context=False,
        best_similarity=0.71, retrieval_confidence="high", chunk_count=4,
        llm_confidence="high", llm_requires_human=False,
        expected_workflow_match="exact_match",
        expected_requires_human=False,
        expected_automation_safe=True,
        synthetic_answer="Step 1: Verify customer identity. Step 2: Check lockout type. Step 3: Unlock or escalate.",
    ),
    GovernanceScenario(
        name="T03 — Account Lockout: vague query (medium match)",
        query="user having trouble",
        has_sop_context=True, has_knowledge_context=False,
        best_similarity=0.41, retrieval_confidence="medium", chunk_count=2,
        llm_confidence="medium", llm_requires_human=False,
        expected_workflow_match="related_match",
        expected_requires_human=True,   # governance rule: related_match + medium confidence
        expected_automation_safe=False,
        synthetic_answer="I found procedures related to authentication issues but no specific workflow was identified for this vague query.",
    ),

    # ── Password Reset: unsupported (only Account Lockout SOP exists) ────────
    GovernanceScenario(
        name="T04 — Password Reset: no dedicated SOP (related match only)",
        query="how do I reset my password",
        has_sop_context=True, has_knowledge_context=False,
        best_similarity=0.51, retrieval_confidence="medium", chunk_count=2,
        llm_confidence="medium", llm_requires_human=False,
        expected_workflow_match="related_match",
        expected_requires_human=True,
        expected_automation_safe=False,
        synthetic_answer=(
            "I found procedures related to account authentication and lockout recovery, "
            "but no dedicated password reset workflow exists in the current SOPs. "
            "Please escalate to a human agent."
        ),
    ),
    GovernanceScenario(
        name="T05 — Password Reset: hallucination guard (must NOT have fabricated UI)",
        query="customer forgot their password",
        has_sop_context=True, has_knowledge_context=False,
        best_similarity=0.49, retrieval_confidence="medium", chunk_count=1,
        llm_confidence="low", llm_requires_human=False,
        expected_workflow_match="related_match",
        expected_requires_human=True,
        synthetic_answer=(
            "I found procedures related to account access issues but no dedicated "
            "password reset SOP. Escalating to human agent."
        ),
        # This answer should NOT contain "Click Forgot Password" or any fabricated UI
    ),

    # ── OTP Delivery Failure SOP ─────────────────────────────────────────────
    GovernanceScenario(
        name="T06 — OTP failure: exact query (high confidence)",
        query="OTP not received by customer",
        has_sop_context=True, has_knowledge_context=False,
        best_similarity=0.82, retrieval_confidence="high", chunk_count=4,
        llm_confidence="high", llm_requires_human=False,
        expected_workflow_match="exact_match",
        expected_requires_human=False,
        expected_automation_safe=True,
        synthetic_answer="Step 1: Confirm OTP delivery channel (SMS/email). Step 2: Check network status.",
    ),
    GovernanceScenario(
        name="T07 — OTP failure: paraphrased",
        query="SMS delivery failing for verification code",
        has_sop_context=True, has_knowledge_context=False,
        best_similarity=0.74, retrieval_confidence="high", chunk_count=3,
        llm_confidence="high", llm_requires_human=False,
        expected_workflow_match="exact_match",
        expected_requires_human=False,
        expected_automation_safe=True,
        synthetic_answer="Confirm the customer's registered mobile number and check carrier status.",
    ),
    GovernanceScenario(
        name="T08 — MFA reset: partial match (OTP SOP retrieved, not MFA SOP)",
        query="customer wants to reset MFA authenticator app",
        has_sop_context=True, has_knowledge_context=False,
        best_similarity=0.44, retrieval_confidence="medium", chunk_count=2,
        llm_confidence="medium", llm_requires_human=False,
        expected_workflow_match="related_match",
        expected_requires_human=True,   # governance: related_match + medium
        expected_automation_safe=False,
        synthetic_answer=(
            "I found procedures related to OTP and authentication verification but no "
            "dedicated MFA authenticator app reset workflow in the current SOPs."
        ),
    ),

    # ── Video KYC Session Failure SOP ────────────────────────────────────────
    GovernanceScenario(
        name="T09 — Video KYC: exact query (high confidence)",
        query="video KYC session failing for customer",
        has_sop_context=True, has_knowledge_context=False,
        best_similarity=0.76, retrieval_confidence="high", chunk_count=3,
        llm_confidence="high", llm_requires_human=False,
        expected_workflow_match="exact_match",
        expected_requires_human=False,
        expected_automation_safe=True,
        synthetic_answer="Step 1: Check network. Step 2: Verify device. Step 3: Retry session.",
    ),
    GovernanceScenario(
        name="T10 — Video KYC: low similarity (weak match)",
        query="camera not working",
        has_sop_context=False, has_knowledge_context=False,
        best_similarity=0.31, retrieval_confidence="low", chunk_count=1,
        llm_confidence="low", llm_requires_human=False,
        expected_workflow_match="weak_match",
        expected_requires_human=True,
        expected_automation_safe=False,
        synthetic_answer="No specific SOP found for this query. Escalating to human agent for review.",
    ),

    # ── Unsupported / no SOP ────────────────────────────────────────────────
    GovernanceScenario(
        name="T11 — No SOP: completely unsupported operation",
        query="how to apply for a personal loan",
        has_sop_context=False, has_knowledge_context=False,
        best_similarity=0.18, retrieval_confidence="low", chunk_count=0,
        llm_confidence="low", llm_requires_human=False,
        expected_workflow_match="no_match",
        expected_requires_human=True,
        expected_automation_safe=False,
        synthetic_answer="No relevant SOPs or knowledge found for this query.",
    ),
    GovernanceScenario(
        name="T12 — Escalation: fraud suspicion",
        query="possible account takeover suspicious login",
        has_sop_context=True, has_knowledge_context=False,
        best_similarity=0.45, retrieval_confidence="medium", chunk_count=2,
        llm_confidence="medium", llm_requires_human=True,   # LLM itself flags escalation
        expected_workflow_match="related_match",
        expected_requires_human=True,
        expected_automation_safe=False,
        synthetic_answer=(
            "Found procedures related to account security. This appears to involve "
            "potential fraud — escalating for human review immediately."
        ),
    ),
    GovernanceScenario(
        name="T13 — LLM flag overrides: high similarity but LLM says escalate",
        query="customer wants compliance exception for KYC",
        has_sop_context=True, has_knowledge_context=False,
        best_similarity=0.70, retrieval_confidence="high", chunk_count=3,
        llm_confidence="high", llm_requires_human=True,    # compliance → always escalate
        expected_workflow_match="exact_match",
        expected_requires_human=True,     # LLM flag respected even for exact_match
        expected_automation_safe=False,
    ),

    # ── Automation safety gate ────────────────────────────────────────────────
    GovernanceScenario(
        name="T14 — Automation safe: all four gates pass",
        query="account locked after failed attempts",
        has_sop_context=True, has_knowledge_context=False,
        best_similarity=0.80, retrieval_confidence="high", chunk_count=5,
        llm_confidence="high", llm_requires_human=False,
        expected_workflow_match="exact_match",
        expected_requires_human=False,
        expected_automation_safe=True,
        synthetic_answer="Verified identity. Unlocking soft lock via admin panel.",
    ),
    GovernanceScenario(
        name="T15 — Automation blocked: medium confidence even with exact match",
        query="account locked after failed attempts",
        has_sop_context=True, has_knowledge_context=False,
        best_similarity=0.75, retrieval_confidence="medium", chunk_count=2,
        llm_confidence="medium", llm_requires_human=False,
        expected_workflow_match="exact_match",
        # exact_match exempts the governance rule → requires_human=False.
        # Automation is still blocked via _is_automation_safe (confidence != "high").
        expected_requires_human=False,
        expected_automation_safe=False,
    ),
    GovernanceScenario(
        name="T16 — Knowledge context helps: no SOP but knowledge chunk present",
        query="why does KYC fail for NRI customers",
        has_sop_context=False, has_knowledge_context=True,
        best_similarity=0.58, retrieval_confidence="medium", chunk_count=3,
        llm_confidence="medium", llm_requires_human=False,
        expected_workflow_match="related_match",   # knowledge context, not SOP
        expected_requires_human=True,              # governance: related_match + medium
        expected_automation_safe=False,
    ),

    # ── New regression scenarios (T17-T20) ───────────────────────────────────
    GovernanceScenario(
        name="T17 — OTP failure: direct synthesis, no defensive qualifiers",
        query="OTP SMS not received, customer cannot complete verification",
        has_sop_context=True, has_knowledge_context=False,
        best_similarity=0.79, retrieval_confidence="high", chunk_count=4,
        llm_confidence="high", llm_requires_human=False,
        expected_workflow_match="exact_match",
        expected_requires_human=False,
        expected_automation_safe=True,
        synthetic_answer=(
            "To resolve the OTP delivery failure:\n"
            "1. Confirm the customer's registered mobile number matches what they provided.\n"
            "2. Ask the customer to check for network signal and confirm the number is not "
            "on DND (Do Not Disturb).\n"
            "3. Resend the OTP from the agent console and note the timestamp.\n"
            "4. If delivery still fails after two attempts, switch to the email OTP channel.\n"
            "5. If both channels fail, place the verification on hold and escalate to the "
            "technical team with the ticket ID and session timestamp."
        ),
    ),
    GovernanceScenario(
        name="T18 — Video KYC failure: operational synthesis, exact match",
        query="video KYC session keeps dropping, customer cannot complete KYC",
        has_sop_context=True, has_knowledge_context=False,
        best_similarity=0.77, retrieval_confidence="high", chunk_count=5,
        llm_confidence="high", llm_requires_human=False,
        expected_workflow_match="exact_match",
        expected_requires_human=False,
        expected_automation_safe=True,
        synthetic_answer=(
            "To resolve the video KYC session failure:\n"
            "1. Ask the customer to verify their internet connection (minimum 2 Mbps required).\n"
            "2. Have them close other apps to free bandwidth, then retry.\n"
            "3. Confirm the browser or app version is current and camera permissions are enabled.\n"
            "4. Retry the KYC session from the agent console.\n"
            "5. If three consecutive attempts fail, schedule a callback during off-peak hours.\n"
            "Escalate to the technical team if failures persist across multiple customers — "
            "this may indicate a platform-level incident."
        ),
    ),
    GovernanceScenario(
        name="T19 — No SOP: account merge request clearly states coverage gap",
        query="customer wants to merge two KwikID accounts",
        has_sop_context=False, has_knowledge_context=False,
        best_similarity=0.22, retrieval_confidence="low", chunk_count=0,
        llm_confidence="low", llm_requires_human=True,
        expected_workflow_match="no_match",
        expected_requires_human=True,
        expected_automation_safe=False,
        synthetic_answer=(
            "No dedicated SOP or knowledge base entry exists for account merging in KwikID. "
            "This request requires human review. Please escalate to the product team with "
            "the customer's account details and the specific merge scenario."
        ),
    ),
    GovernanceScenario(
        name="T21 — New threshold range: SOP at 0.58 sim must be exact_match (not related)",
        query="account locked after failed login attempts",
        has_sop_context=True, has_knowledge_context=False,
        # best_similarity=0.58 represents effective classification_sim (max of SOP boosted
        # score 0.58 and raw best 0.43). Previously classified as related_match at 0.62
        # threshold. Must be exact_match at 0.55 threshold — this is the production fix.
        best_similarity=0.58, retrieval_confidence="high", chunk_count=3,
        llm_confidence="high", llm_requires_human=False,
        expected_workflow_match="exact_match",    # 0.58 >= 0.55 threshold
        expected_requires_human=False,
        expected_automation_safe=True,
        synthetic_answer=(
            "To unlock the account, first verify the customer's identity using any two "
            "approved verification factors. Then check the account status under Customer > "
            "Account > Security Status. For a soft lock (3-5 failed attempts), the account "
            "can auto-unlock after 30 minutes or be manually unlocked from the admin panel. "
            "For a hard lock, perform a force unlock and trigger MFA re-enrollment before "
            "the customer retries login."
        ),
    ),
    GovernanceScenario(
        name="T20 — Security escalation: LLM flag preserved, automation blocked",
        query="customer reports unauthorized transactions after KYC verification",
        has_sop_context=True, has_knowledge_context=False,
        best_similarity=0.65, retrieval_confidence="high", chunk_count=3,
        llm_confidence="medium", llm_requires_human=True,   # fraud → LLM flags escalation
        expected_workflow_match="exact_match",               # sim=0.65 >= 0.62 with SOP
        expected_requires_human=True,                        # LLM flag respected
        expected_automation_safe=False,
        synthetic_answer=(
            "This ticket involves a potential fraud or security incident and requires "
            "immediate human review. Do not proceed with automated resolution.\n"
            "Escalate to the Security team with the full session log and the customer's "
            "account details."
        ),
    ),

    # ── Branch completeness scenarios (T22-T27) ───────────────────────────────
    # These validate that COMPLETE answers (including all decision-tree branches)
    # pass Test 7 (required_answer_content) and all existing tests.
    GovernanceScenario(
        name="T22 — Account Lockout: complete answer preserves all decision-tree branches",
        query="account locked after failed login attempts",
        has_sop_context=True, has_knowledge_context=False,
        best_similarity=0.80, retrieval_confidence="high", chunk_count=5,
        llm_confidence="high", llm_requires_human=False,
        expected_workflow_match="exact_match",
        expected_requires_human=False,
        expected_automation_safe=True,
        required_answer_content=["do not unlock", "escalat", "security team", "log", "confirm"],
        synthetic_answer=(
            "To resolve the account lockout, first identify the lockout type in "
            "Customer > Account > Security Status:\n"
            "- Soft Lock (3-5 failed attempts): auto-unlocks in 30 min, or perform "
            "agent-assisted unlock via Admin > Customer Management > Security > Unlock Account.\n"
            "- Hard Lock (6+ failed attempts): requires force unlock + mandatory MFA re-enrollment.\n"
            "- Security Freeze: cannot be resolved by front-line agents (see Step 5).\n\n"
            "Before any action, verify the customer's identity using TWO factors "
            "(name, DOB, mobile last 4, email, or PAN/Aadhaar last 4):\n"
            "- If only ONE factor verified: do not unlock. Ask the customer to visit the nearest "
            "branch with original ID documents.\n"
            "- If ZERO factors match: close the ticket and notify the Security team immediately "
            "— this is a suspected account takeover attempt.\n\n"
            "For Hard Lock: force unlock via Admin > Customer Management > Security > Force Unlock. "
            "Trigger mandatory MFA re-enrollment (Customer > Security > Reset MFA). "
            "Advise the customer to update their password immediately.\n\n"
            "Security Freeze: do not attempt to unlock via admin panel. Create a high-priority "
            "ticket tagged 'security-freeze' and assign to the Security team.\n\n"
            "Escalate to the Security team if: customer denies initiating the attempts, login "
            "attempts appear from foreign IPs, or multiple accounts share the same credentials.\n\n"
            "Post-unlock: log the identity verification in Freshdesk, confirm the customer "
            "successfully logs in, and verify ACTIVE status before closing the ticket."
        ),
    ),
    GovernanceScenario(
        name="T23 — Hard Lock: mandatory MFA re-enrollment step preserved",
        query="customer account hard locked, 6 plus failed login attempts",
        has_sop_context=True, has_knowledge_context=False,
        best_similarity=0.77, retrieval_confidence="high", chunk_count=4,
        llm_confidence="high", llm_requires_human=False,
        expected_workflow_match="exact_match",
        expected_requires_human=False,
        expected_automation_safe=True,
        required_answer_content=["mfa", "re-enroll", "force unlock", "password"],
        synthetic_answer=(
            "This is a hard lock (6+ failed attempts). Before acting, verify the customer's "
            "identity using two approved factors. If only one factor can be confirmed, do not "
            "proceed with the unlock.\n"
            "1. Force unlock the account: Admin > Customer Management > [Customer ID] "
            "> Security > Force Unlock (Hard Lock). This action is logged in the security audit trail.\n"
            "2. Mandatory: trigger MFA re-enrollment via Customer > Security > Reset MFA. "
            "The customer must set up a new authenticator on next login.\n"
            "3. Advise the customer to update their password immediately after unlocking.\n"
            "4. Document the unlock with your agent name, timestamp, verification factors used, "
            "and reason for unlock.\n"
            "If the customer denies initiating the failed attempts, escalate to the Security team."
        ),
    ),
    GovernanceScenario(
        name="T24 — Credential stuffing: escalation with required data items preserved",
        query="multiple customers locked out with same mobile number, credential stuffing pattern",
        has_sop_context=True, has_knowledge_context=False,
        best_similarity=0.65, retrieval_confidence="high", chunk_count=3,
        llm_confidence="medium", llm_requires_human=True,   # fraud pattern → escalate
        expected_workflow_match="exact_match",
        expected_requires_human=True,
        expected_automation_safe=False,
        required_answer_content=["escalat", "security team", "ip", "account"],
        synthetic_answer=(
            "This pattern — multiple accounts locked with the same mobile number — is a "
            "known credential stuffing indicator and requires immediate escalation.\n"
            "Do not unlock any affected accounts without Security team clearance.\n"
            "Escalate to the Security team with:\n"
            "- All affected account IDs\n"
            "- Lockout timestamps for each account\n"
            "- IP addresses from failed-attempt logs (Admin > Security Logs)\n"
            "- Any location information the customer provided\n"
            "Do not resolve these tickets — leave open for Security team investigation."
        ),
    ),
    GovernanceScenario(
        name="T25 — Security Freeze: front-line cannot resolve, must escalate to Security team",
        query="account has security freeze, how to unlock it",
        has_sop_context=True, has_knowledge_context=False,
        best_similarity=0.72, retrieval_confidence="high", chunk_count=3,
        llm_confidence="medium", llm_requires_human=True,   # Security Freeze → escalate
        expected_workflow_match="exact_match",
        expected_requires_human=True,
        expected_automation_safe=False,
        required_answer_content=["security team", "cannot", "do not attempt", "high-priority"],
        synthetic_answer=(
            "Security freezes cannot be resolved by front-line agents. Do not attempt "
            "to unlock via the admin panel — the action will be rejected at the database level.\n"
            "1. Inform the customer: 'Your account has been temporarily secured pending a "
            "review. This typically takes 24-48 business hours.'\n"
            "2. Create a high-priority ticket in Freshdesk tagged 'security-freeze' and "
            "assign it to the Security team.\n"
            "3. Do not share with the customer any details about why the freeze was triggered.\n"
            "The Security team will contact the customer directly with next steps."
        ),
    ),
    GovernanceScenario(
        name="T26 — Post-unlock checklist: identity log and login confirmation preserved",
        query="soft lock resolved, what are the post-resolution steps",
        has_sop_context=True, has_knowledge_context=False,
        best_similarity=0.71, retrieval_confidence="high", chunk_count=4,
        llm_confidence="high", llm_requires_human=False,
        expected_workflow_match="exact_match",
        expected_requires_human=False,
        expected_automation_safe=True,
        required_answer_content=["log", "confirm", "identity verification", "ticket"],
        synthetic_answer=(
            "After the soft lock is resolved:\n"
            "1. Log the identity verification in Freshdesk: 'Identity verified via [Factor 1] "
            "and [Factor 2] at [timestamp].'\n"
            "2. Log the unlock action in the admin panel with the reason selected.\n"
            "3. Advise the customer to reset their password immediately.\n"
            "4. Confirm the customer successfully logs in before closing the ticket.\n"
            "5. Verify the account status shows ACTIVE in the admin panel.\n"
            "Resolve the ticket with label AUTO_REPLY and resolution note: 'Account unlocked "
            "after identity verification. Customer confirmed successful login.'"
        ),
    ),
    GovernanceScenario(
        name="T27 — Partial verification: denial branch explicitly preserved in answer",
        query="customer can only provide one verification factor, can I still unlock the account",
        has_sop_context=True, has_knowledge_context=False,
        best_similarity=0.68, retrieval_confidence="high", chunk_count=3,
        llm_confidence="high", llm_requires_human=False,
        expected_workflow_match="exact_match",
        expected_requires_human=False,   # SOP gives definitive NO — this IS the safe automated answer
        expected_automation_safe=True,
        required_answer_content=["do not", "one factor", "branch", "visit"],
        synthetic_answer=(
            "No — do not unlock the account with only one verification factor. The procedure "
            "requires TWO verified factors before any account action. This is a mandatory "
            "security control and cannot be waived by front-line agents.\n"
            "With only one factor confirmed:\n"
            "- Inform the customer that a second verification factor is required.\n"
            "- Ask them to visit the nearest branch with original ID documents if they cannot "
            "provide a second factor remotely.\n"
            "- Do not proceed with any unlock, password reset, or account modification until "
            "a second factor is verified.\n"
            "If the customer insists or becomes hostile, escalate to a supervisor."
        ),
    ),
]


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------
@dataclass
class TestResult:
    scenario_name: str
    passed: bool
    failures: list[str] = field(default_factory=list)


def run_offline_tests(verbose: bool = False) -> list[TestResult]:
    results: list[TestResult] = []

    for scenario in SCENARIOS:
        failures: list[str] = []

        if not _IMPORTS_OK:
            results.append(TestResult(
                scenario_name=scenario.name,
                passed=False,
                failures=["Governance helpers could not be imported"],
            ))
            continue

        # ── Test 1: workflow_match_type classification ────────────────────────
        actual_wmt = _classify_workflow_match(
            has_sop_context=scenario.has_sop_context,
            has_knowledge_context=scenario.has_knowledge_context,
            best_similarity=scenario.best_similarity,
            chunk_count=scenario.chunk_count,
        )
        if actual_wmt != scenario.expected_workflow_match:
            failures.append(
                f"workflow_match_type: expected {scenario.expected_workflow_match!r}, "
                f"got {actual_wmt!r} "
                f"(sim={scenario.best_similarity}, sop={scenario.has_sop_context}, "
                f"know={scenario.has_knowledge_context}, chunks={scenario.chunk_count})"
            )

        # ── Test 2: requires_human derivation ────────────────────────────────
        actual_rh = _derive_requires_human(
            llm_flag=scenario.llm_requires_human,
            insufficient_context=scenario.chunk_count == 0,
            confidence=scenario.llm_confidence,
            has_sop_context=scenario.has_sop_context,
            workflow_match_type=actual_wmt,
            retrieval_confidence=scenario.retrieval_confidence,
        )
        if actual_rh != scenario.expected_requires_human:
            failures.append(
                f"requires_human: expected {scenario.expected_requires_human}, "
                f"got {actual_rh} "
                f"(wmt={actual_wmt}, rc={scenario.retrieval_confidence}, "
                f"conf={scenario.llm_confidence}, llm_flag={scenario.llm_requires_human})"
            )

        # ── Test 3: automation_safe gate ─────────────────────────────────────
        if scenario.expected_automation_safe is not None:
            actual_safe = _is_automation_safe(
                workflow_match_type=actual_wmt,
                confidence=scenario.llm_confidence,
                requires_human=actual_rh,
                retrieval_confidence=scenario.retrieval_confidence,
            )
            if actual_safe != scenario.expected_automation_safe:
                failures.append(
                    f"automation_safe: expected {scenario.expected_automation_safe}, "
                    f"got {actual_safe}"
                )

        # ── Test 4: hallucination markers in synthetic answer ─────────────────
        if scenario.synthetic_answer:
            for pattern, description in _HALLUCINATION_PATTERNS:
                if re.search(pattern, scenario.synthetic_answer, re.IGNORECASE):
                    failures.append(f"HALLUCINATION_MARKER: {description!r} in answer")

        # ── Test 5: related_match answer must begin with limitation statement ──
        if actual_wmt == "related_match" and scenario.synthetic_answer:
            limitation_phrases = [
                "found procedures related",
                "no dedicated",
                "not contain a dedicated",
                "does not include a",
                "current sop",
                "escalat",
                "no specific",
                "wasn't able to find",
                "unable to find",
                "no dedicated",
            ]
            has_limitation = any(
                phrase in scenario.synthetic_answer.lower()
                for phrase in limitation_phrases
            )
            if not has_limitation:
                failures.append(
                    "related_match answer should contain an explicit limitation statement"
                )

        # ── Test 6: exact_match answers must NOT contain defensive qualifiers ──
        if actual_wmt == "exact_match" and scenario.synthetic_answer:
            for phrase, description in _EXACT_MATCH_FORBIDDEN_PHRASES:
                if phrase in scenario.synthetic_answer.lower():
                    failures.append(
                        f"DEFENSIVE_LANGUAGE_IN_EXACT_MATCH: {description!r} "
                        f"found in answer for exact_match scenario"
                    )

        # ── Test 7: exact_match answers must contain required branch content ──
        # Validates that complete answers include all critical SOP decision-tree branches.
        # required_answer_content is empty for legacy scenarios — skipped automatically.
        if actual_wmt == "exact_match" and scenario.required_answer_content and scenario.synthetic_answer:
            for required_term in scenario.required_answer_content:
                if required_term.lower() not in scenario.synthetic_answer.lower():
                    failures.append(
                        f"BRANCH_OMISSION: Required content '{required_term}' "
                        f"missing from exact_match answer — possible branch compression"
                    )

        passed = len(failures) == 0
        results.append(TestResult(
            scenario_name=scenario.name,
            passed=passed,
            failures=failures,
        ))

        if verbose:
            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] {scenario.name}")
            if failures:
                for f in failures:
                    print(f"          FAIL: {f}")
            else:
                print(f"          wmt={actual_wmt} rh={actual_rh}")

    return results


def run_threshold_sanity_checks(verbose: bool = False) -> list[TestResult]:
    """Verify that configured similarity thresholds make sense relative to each other."""
    results = []

    checks = [
        (
            "Exact match threshold > related match threshold",
            _WORKFLOW_EXACT_MATCH_SIMILARITY > _WORKFLOW_RELATED_MATCH_SIMILARITY,
            f"exact={_WORKFLOW_EXACT_MATCH_SIMILARITY} related={_WORKFLOW_RELATED_MATCH_SIMILARITY}",
        ),
        (
            "Exact match threshold < 1.0 (reachable)",
            _WORKFLOW_EXACT_MATCH_SIMILARITY < 1.0,
            f"exact={_WORKFLOW_EXACT_MATCH_SIMILARITY}",
        ),
        (
            "Related match threshold > 0.0 (non-trivial)",
            _WORKFLOW_RELATED_MATCH_SIMILARITY > 0.0,
            f"related={_WORKFLOW_RELATED_MATCH_SIMILARITY}",
        ),
        (
            "Exact match threshold in sane range [0.50, 0.90]",
            0.50 <= _WORKFLOW_EXACT_MATCH_SIMILARITY <= 0.90,
            f"exact={_WORKFLOW_EXACT_MATCH_SIMILARITY}",
        ),
        (
            "Related match threshold in sane range [0.25, 0.65]",
            0.25 <= _WORKFLOW_RELATED_MATCH_SIMILARITY <= 0.65,
            f"related={_WORKFLOW_RELATED_MATCH_SIMILARITY}",
        ),
    ]

    for name, condition, detail in checks:
        passed = condition
        failures = [] if passed else [f"Threshold sanity failed: {detail}"]
        results.append(TestResult(scenario_name=f"[THRESHOLD] {name}", passed=passed, failures=failures))
        if verbose:
            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] {name} — {detail}")

    return results


# ---------------------------------------------------------------------------
# Branch completeness unit tests — directly test _check_answer_completeness()
# ---------------------------------------------------------------------------
def run_branch_completeness_tests(verbose: bool = False) -> list[TestResult]:
    """Directly test the _check_answer_completeness() heuristic.

    Verifies that:
    1. A bad (happy-path-only) answer is correctly flagged for missing branches.
    2. A complete answer (all branches present) produces zero warnings.
    """
    results: list[TestResult] = []

    if not _IMPORTS_OK:
        return [TestResult(
            scenario_name="[BRANCH] _check_answer_completeness import failed",
            passed=False,
            failures=["Governance helpers could not be imported"],
        )]

    # Full branch flags — all four types present in a hypothetical SOP
    full_flags: dict = {
        "has_escalation_branches": True,
        "has_denial_branches":     True,
        "has_security_freeze":     True,
        "has_post_resolution":     True,
    }

    # ── Test A: bad answer (happy path only) must generate >= 3 warnings ─────
    bad_answer = "Verify the customer's identity and then unlock the account via the admin panel."
    bad_warnings = _check_answer_completeness(bad_answer, full_flags)
    test_a_passed = len(bad_warnings) >= 3
    results.append(TestResult(
        scenario_name="[BRANCH] Bad (happy-path-only) answer correctly flagged for missing branches",
        passed=test_a_passed,
        failures=[] if test_a_passed else [
            f"Expected >= 3 branch warnings for a happy-path-only answer, got {len(bad_warnings)}: "
            f"{bad_warnings}"
        ],
    ))
    if verbose:
        status = "PASS" if test_a_passed else "FAIL"
        print(f"  [{status}] [BRANCH] Bad answer warnings={len(bad_warnings)}: {bad_warnings}")

    # ── Test B: complete answer must generate 0 warnings ─────────────────────
    good_answer = (
        "Verify via two factors. If only one factor verified, do not unlock — "
        "ask the customer to visit the nearest branch. If zero factors match, "
        "close the ticket and notify the Security team — suspected account takeover. "
        "For soft lock: unlock and reset password. For hard lock: force unlock + MFA re-enrollment. "
        "Security Freeze cannot be resolved by front-line agents: create a security-freeze ticket "
        "and assign to the Security team. "
        "Post-unlock: log the identity verification in Freshdesk and confirm the customer "
        "successfully logs in before closing the ticket."
    )
    good_warnings = _check_answer_completeness(good_answer, full_flags)
    test_b_passed = len(good_warnings) == 0
    results.append(TestResult(
        scenario_name="[BRANCH] Complete answer (all branches) produces zero warnings",
        passed=test_b_passed,
        failures=[] if test_b_passed else [
            f"Expected 0 warnings for complete answer, got {len(good_warnings)}: {good_warnings}"
        ],
    ))
    if verbose:
        status = "PASS" if test_b_passed else "FAIL"
        print(f"  [{status}] [BRANCH] Complete answer warnings={len(good_warnings)}")

    return results


# ---------------------------------------------------------------------------
# SOP structure parser unit tests
# ---------------------------------------------------------------------------
def run_sop_parser_tests(verbose: bool = False) -> list[TestResult]:
    """Unit-test parse_sop_content() and parse_sop_sections() directly.

    Verifies that:
    - Regex patterns fire on representative SOP prose
    - The old 'pending.*review' bug (now a proper regex, not substring) is fixed
    - Empty / whitespace input returns all-False flags
    - parse_sop_sections() correctly combines heading + content
    - has_knowledge_context derivation fix is exercised (indirectly via chat_generator)
    """
    results: list[TestResult] = []

    if not _IMPORTS_OK:
        return [TestResult(
            scenario_name="[PARSER] sop_parser import failed",
            passed=False,
            failures=["rag_engine.sop.sop_parser could not be imported"],
        )]

    def _check(name: str, flags, expected: dict) -> TestResult:
        failures = []
        for attr, want in expected.items():
            got = getattr(flags, attr, None)
            if got != want:
                failures.append(f"{attr}: expected {want}, got {got}")
        passed = len(failures) == 0
        if verbose:
            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] {name}")
            for f in failures:
                print(f"          FAIL: {f}")
        return TestResult(scenario_name=name, passed=passed, failures=failures)

    # P01 — Empty string → all False
    results.append(_check(
        "[PARSER] P01 — Empty text returns all-False flags",
        parse_sop_content(""),
        {
            "has_escalation_branches": False,
            "has_denial_branches": False,
            "has_security_freeze": False,
            "has_post_resolution": False,
            "has_mandatory_warnings": False,
        },
    ))

    # P02 — Whitespace-only → all False
    results.append(_check(
        "[PARSER] P02 — Whitespace-only text returns all-False flags",
        parse_sop_content("   \n\t  "),
        {"has_escalation_branches": False, "has_denial_branches": False},
    ))

    # P03 — Escalation keyword detection
    results.append(_check(
        "[PARSER] P03 — 'escalate to the security team' fires escalation flag",
        parse_sop_content("If three or more failed attempts are detected, escalate to the Security team immediately."),
        {"has_escalation_branches": True},
    ))

    # P04 — Bold conditional → denial branch detection
    results.append(_check(
        "[PARSER] P04 — Bold **If only one factor...** fires denial flag",
        parse_sop_content("**If only one factor can be verified**: do not unlock. Ask customer to visit the nearest branch."),
        {"has_denial_branches": True},
    ))

    # P05 — Plain-prose conditional → denial branch
    results.append(_check(
        "[PARSER] P05 — Plain 'if zero factors match' fires denial flag",
        parse_sop_content("if zero factors match, close the ticket and notify the Security team."),
        {"has_denial_branches": True},
    ))

    # P06 — Security Freeze with proper regex (was broken as literal substring 'pending.*review')
    results.append(_check(
        "[PARSER] P06 — 'pending a security review' fires freeze flag (regex, not substring)",
        parse_sop_content(
            "Security Freeze accounts are pending a security review and cannot be "
            "resolved by front-line agents."
        ),
        {"has_security_freeze": True},
    ))

    # P07 — 'security freeze' literal also fires
    results.append(_check(
        "[PARSER] P07 — 'Security Freeze' literal fires freeze flag",
        parse_sop_content("This is a Security Freeze — database-level intervention is required."),
        {"has_security_freeze": True},
    ))

    # P08 — Post-resolution: log identity verification
    results.append(_check(
        "[PARSER] P08 — 'log the identity verification' fires post-resolution flag",
        parse_sop_content(
            "Post-unlock checklist: log the identity verification in Freshdesk, "
            "confirm the customer successfully logs in before ticket closure."
        ),
        {"has_post_resolution": True},
    ))

    # P09 — Mandatory NEVER warning
    results.append(_check(
        "[PARSER] P09 — 'NEVER unlock without verifying' fires mandatory-warnings flag",
        parse_sop_content("NEVER unlock an account without verifying at least two identity factors."),
        {"has_mandatory_warnings": True},
    ))

    # P10 — 'failure to do so' mandatory phrase
    results.append(_check(
        "[PARSER] P10 — 'failure to comply' fires mandatory-warnings flag",
        parse_sop_content("Failure to comply constitutes a security violation and will be escalated."),
        {"has_mandatory_warnings": True},
    ))

    # P11 — parse_sop_sections() combines heading and content
    sections = [
        {"heading": "## Step 3: Verification", "content": "Verify identity via two factors."},
        {
            "heading": "## Step 5: Escalation",
            "content": "If account takeover is suspected, escalate to the Security team immediately.",
        },
    ]
    results.append(_check(
        "[PARSER] P11 — parse_sop_sections() detects escalation across sections",
        parse_sop_sections(sections),
        {"has_escalation_branches": True},
    ))

    # P12 — any_set() True when any flag set
    flags = parse_sop_content("escalate to the security team")
    p12_passed = flags.any_set() is True
    results.append(TestResult(
        scenario_name="[PARSER] P12 — any_set() returns True when at least one flag set",
        passed=p12_passed,
        failures=[] if p12_passed else ["any_set() returned False despite escalation flag set"],
    ))
    if verbose:
        status = "PASS" if p12_passed else "FAIL"
        print(f"  [{status}] [PARSER] P12 — any_set()")

    # P13 — to_dict() returns all five keys
    flags = parse_sop_content("NEVER unlock an account. Escalate to security team.")
    d = flags.to_dict()
    p13_keys_ok = set(d.keys()) == {
        "has_escalation_branches", "has_denial_branches", "has_security_freeze",
        "has_post_resolution", "has_mandatory_warnings",
    }
    results.append(TestResult(
        scenario_name="[PARSER] P13 — to_dict() returns all five flag keys",
        passed=p13_keys_ok,
        failures=[] if p13_keys_ok else [f"to_dict() keys mismatch: {set(d.keys())}"],
    ))
    if verbose:
        status = "PASS" if p13_keys_ok else "FAIL"
        print(f"  [{status}] [PARSER] P13 — to_dict() keys")

    # P14 — account takeover fires escalation, not denial
    flags = parse_sop_content("Suspected account takeover — escalate immediately.")
    results.append(_check(
        "[PARSER] P14 — 'account takeover' fires escalation but not denial",
        flags,
        {"has_escalation_branches": True, "has_denial_branches": False},
    ))

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Response governance validation — offline logic tests"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Show per-test details")
    args = parser.parse_args()

    print("=" * 72)
    print("KwikID Response Governance Validation Suite")
    print("=" * 72)
    print(f"  WORKFLOW_EXACT_MATCH_SIMILARITY:   {_WORKFLOW_EXACT_MATCH_SIMILARITY if _IMPORTS_OK else 'N/A'}")
    print(f"  WORKFLOW_RELATED_MATCH_SIMILARITY: {_WORKFLOW_RELATED_MATCH_SIMILARITY if _IMPORTS_OK else 'N/A'}")
    print()

    all_results: list[TestResult] = []

    # Threshold sanity checks
    print("-- Threshold sanity checks " + "-" * 45)
    all_results += run_threshold_sanity_checks(verbose=args.verbose)

    # Scenario-based governance tests
    print("-- Scenario governance tests " + "-" * 43)
    all_results += run_offline_tests(verbose=args.verbose)

    # Branch completeness unit tests
    print("-- Branch completeness tests " + "-" * 43)
    all_results += run_branch_completeness_tests(verbose=args.verbose)

    # SOP structure parser unit tests
    print("-- SOP structure parser tests " + "-" * 42)
    all_results += run_sop_parser_tests(verbose=args.verbose)

    # Summary
    total   = len(all_results)
    passed  = sum(1 for r in all_results if r.passed)
    failed  = total - passed

    print()
    print("=" * 72)
    print(f"Results: {passed}/{total} PASS   {failed} FAIL")
    print("=" * 72)

    if failed > 0:
        print("\nFailed tests:")
        for r in all_results:
            if not r.passed:
                print(f"  FAIL: {r.scenario_name}")
                for f in r.failures:
                    print(f"      {f}")
        return 1

    print("\nAll governance tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
