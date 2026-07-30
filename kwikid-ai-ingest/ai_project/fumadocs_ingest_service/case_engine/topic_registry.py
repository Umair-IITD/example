"""
case_engine/topic_registry.py

Static slot definitions for all 5 topic families.

Sprint 2.5.6 change (critical bug fix):
  Required slots are now INVESTIGATION slots, NOT resolution slots.
  Blueprint §6 and §2 (L1 Responsibilities) are explicit:
    "If URN or Session ID is missing, the system MUST ask for them.
     The system MUST NOT ask for customer details (like mobile number) to
     attempt a resolution prematurely. L1's job is to investigate the portal/
     logs, which strictly requires URN/Session ID."

  Before: OTP_DELIVERY_FAILURE required phone_number + channel → triggers OTP resend
  After:  OTP_DELIVERY_FAILURE required urn + session_id → triggers investigation

Each TopicKey maps to a SlotRegistry that declares:
  - required: slots that must be FILLED before workflow investigation proceeds
  - optional: collected opportunistically; never block investigation progress

This module is a compile-time registry. All definitions are frozen dataclasses.
No DB access. No LLM. No runtime mutation.
"""
from __future__ import annotations

import re

from case_engine.models import TopicKey
from case_engine.slot_filling.models import SlotDefinition
from case_engine.slot_filling.slot_registry import SlotRegistry

# ── Valid value sets ──────────────────────────────────────────────────────────

_OTP_CHANNELS   = frozenset({"SMS", "EMAIL", "VOICE"})
_PORTAL_TYPES   = frozenset({"WEB", "MOBILE", "DESKTOP"})
_CALLBACK_TYPES = frozenset({"CBS", "DMS", "SFDC", "WEBHOOK"})
_DOC_TYPES      = frozenset({"AADHAAR", "PAN", "PASSPORT", "VOTERID"})

# URN: Unique Reference Number — any non-empty alphanumeric string
_URN_PATTERN = re.compile(r"[A-Za-z0-9\-]{3,64}")

# Session ID: KID-XXXXXXXX or similar alphanumeric session code
_SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9\-]{4,64}")

# Phone last-4: 4–10 digits
_PHONE_PATTERN = re.compile(r"\d{4,10}")

# Application/agent ID: free-form, non-empty
_FREE_FORM_PATTERN = re.compile(r".{1,128}", re.DOTALL)


# ── Topic slot registry ───────────────────────────────────────────────────────

_TOPIC_SLOT_REGISTRY: dict[TopicKey, SlotRegistry] = {

    # ── OTP_DELIVERY_FAILURE ──────────────────────────────────────────────────
    # Investigation requires URN + Session ID to examine OTP delivery logs in
    # the Unity admin portal. Phone number and channel are optional — they
    # may be present in the ticket but are NOT needed to start investigation.
    # (Phone number is a RESOLUTION parameter: needed only to resend OTP after
    # investigation confirms that resend is the correct action.)
    TopicKey.OTP_DELIVERY_FAILURE: SlotRegistry(
        required=(
            SlotDefinition(
                name="urn",
                description="Customer's Unique Reference Number in the VKYC system",
                clarification_prompt=(
                    "To investigate this OTP delivery issue, please provide the customer's "
                    "URN (Unique Reference Number) and VKYC Session ID (KID-XXXXXXXX). "
                    "These are required to examine the OTP delivery logs in the admin portal."
                ),
                validation_pattern=_URN_PATTERN,
            ),
            SlotDefinition(
                name="session_id",
                description="VKYC session identifier (e.g., KID-XXXXXXXX)",
                clarification_prompt=(
                    "Please also provide the VKYC Session ID (format: KID-XXXXXXXX) "
                    "so I can look up the OTP delivery attempt in the session logs."
                ),
                validation_pattern=_SESSION_ID_PATTERN,
            ),
        ),
        optional=(
            SlotDefinition(
                name="phone_number",
                description="Last 4 digits of the customer's registered phone number (optional — used after investigation confirms OTP resend is needed)",
                clarification_prompt="Please provide the last 4 digits of the customer's registered phone number (optional).",
                required=False,
                validation_pattern=_PHONE_PATTERN,
            ),
            SlotDefinition(
                name="channel",
                description="OTP delivery channel that is failing (optional — extracted from investigation logs)",
                clarification_prompt="Which OTP channel is failing? Please specify: SMS, EMAIL, or VOICE. (optional)",
                required=False,
                valid_values=_OTP_CHANNELS,
            ),
            SlotDefinition(
                name="attempt_count",
                description="Number of OTP delivery attempts made so far (optional)",
                clarification_prompt="How many OTP delivery attempts have been made so far? (optional)",
                required=False,
            ),
        ),
    ),

    # ── VKYC_SESSION_FAILURE ──────────────────────────────────────────────────
    # Investigation requires URN + Session ID to examine session logs,
    # video, face-match scores, and summary data.
    TopicKey.VKYC_SESSION_FAILURE: SlotRegistry(
        required=(
            SlotDefinition(
                name="urn",
                description="Customer's Unique Reference Number in the VKYC system",
                clarification_prompt=(
                    "To investigate the VKYC session failure, please provide the customer's "
                    "URN (Unique Reference Number) and Session ID (KID-XXXXXXXX). "
                    "These are required to retrieve the session logs and video evidence."
                ),
                validation_pattern=_URN_PATTERN,
            ),
            SlotDefinition(
                name="session_id",
                description="VKYC session identifier (e.g., KID-XXXXXXXX)",
                clarification_prompt=(
                    "Please also provide the VKYC Session ID (format: KID-XXXXXXXX)."
                ),
                validation_pattern=_SESSION_ID_PATTERN,
            ),
        ),
        optional=(
            SlotDefinition(
                name="failure_code",
                description="Error code from the VKYC session (optional)",
                clarification_prompt="Do you have an error code from the VKYC session? (optional)",
                required=False,
            ),
            SlotDefinition(
                name="phone_number",
                description="Last 4 digits of the customer's registered phone number (optional)",
                clarification_prompt="Please provide the last 4 digits of the customer's registered phone number. (optional)",
                required=False,
                validation_pattern=_PHONE_PATTERN,
            ),
        ),
    ),

    # ── DOCUMENT_OCR_FAILURE ──────────────────────────────────────────────────
    # Investigation requires URN + Session ID to look up OCR attempt details,
    # document scan results, and face-match scores.
    TopicKey.DOCUMENT_OCR_FAILURE: SlotRegistry(
        required=(
            SlotDefinition(
                name="urn",
                description="Customer's Unique Reference Number in the VKYC system",
                clarification_prompt=(
                    "To investigate the document OCR failure, please provide the customer's "
                    "URN and VKYC Session ID. These are needed to examine the document scan logs."
                ),
                validation_pattern=_URN_PATTERN,
            ),
            SlotDefinition(
                name="session_id",
                description="VKYC session identifier (e.g., KID-XXXXXXXX)",
                clarification_prompt=(
                    "Please also provide the VKYC Session ID (format: KID-XXXXXXXX)."
                ),
                validation_pattern=_SESSION_ID_PATTERN,
            ),
        ),
        optional=(
            SlotDefinition(
                name="document_type",
                description="Type of document failing OCR (optional — extracted from session logs)",
                clarification_prompt=(
                    "Which document type is failing OCR? "
                    "Please specify: AADHAAR, PAN, PASSPORT, or VOTERID. (optional)"
                ),
                required=False,
                valid_values=_DOC_TYPES,
            ),
            SlotDefinition(
                name="application_id",
                description="Customer application or onboarding ID (optional)",
                clarification_prompt=(
                    "Please provide the customer's application ID (optional)."
                ),
                required=False,
                validation_pattern=_FREE_FORM_PATTERN,
            ),
            SlotDefinition(
                name="error_code",
                description="OCR error code or failure message (optional)",
                clarification_prompt="Do you have an OCR error code or failure message? (optional)",
                required=False,
            ),
        ),
    ),

    # ── AGENT_PORTAL_ISSUE ────────────────────────────────────────────────────
    # Investigation requires the agent's ID to look up their account,
    # permissions, and login history in the Unity admin portal.
    TopicKey.AGENT_PORTAL_ISSUE: SlotRegistry(
        required=(
            SlotDefinition(
                name="agent_id",
                description="Agent's identifier in the portal system",
                clarification_prompt=(
                    "To investigate the portal issue, please provide the agent's ID "
                    "(e.g., AGT-XXXXXXXX or the login username)."
                ),
                validation_pattern=_FREE_FORM_PATTERN,
            ),
        ),
        optional=(
            SlotDefinition(
                name="portal_type",
                description="Portal interface where the issue is occurring (optional)",
                clarification_prompt=(
                    "Which portal interface is affected? Please specify: WEB, MOBILE, or DESKTOP. (optional)"
                ),
                required=False,
                valid_values=_PORTAL_TYPES,
            ),
            SlotDefinition(
                name="error_message",
                description="Error message or code displayed in the portal (optional)",
                clarification_prompt="What error message or code is displayed? (optional)",
                required=False,
            ),
        ),
    ),

    # ── API_CALLBACK_FAILURE ──────────────────────────────────────────────────
    # Investigation requires the Application ID and callback type to trace
    # the callback chain and identify the failure point.
    TopicKey.API_CALLBACK_FAILURE: SlotRegistry(
        required=(
            SlotDefinition(
                name="application_id",
                description="Customer application or onboarding ID (e.g., APP-XXXXXXXX)",
                clarification_prompt=(
                    "To investigate the callback failure, please provide the Application ID "
                    "(e.g., APP-00129871 or similar onboarding reference)."
                ),
                validation_pattern=_FREE_FORM_PATTERN,
            ),
            SlotDefinition(
                name="callback_type",
                description="Type of API callback that is failing",
                clarification_prompt=(
                    "Which callback type is failing? Please specify: CBS, DMS, SFDC, or WEBHOOK."
                ),
                valid_values=_CALLBACK_TYPES,
            ),
        ),
        optional=(
            SlotDefinition(
                name="error_code",
                description="Callback error code or HTTP status (optional)",
                clarification_prompt="What error code or HTTP status is returned by the callback? (optional)",
                required=False,
            ),
            SlotDefinition(
                name="session_id",
                description="VKYC Session ID associated with this callback (optional)",
                clarification_prompt="What is the VKYC Session ID associated with this callback? (optional)",
                required=False,
                validation_pattern=_SESSION_ID_PATTERN,
            ),
        ),
    ),
}


def get_registry(topic: TopicKey) -> SlotRegistry | None:
    """Return the SlotRegistry for the given topic, or None for UNKNOWN."""
    return _TOPIC_SLOT_REGISTRY.get(topic)


def list_topics() -> list[TopicKey]:
    """Return all topics that have slot definitions."""
    return list(_TOPIC_SLOT_REGISTRY.keys())
