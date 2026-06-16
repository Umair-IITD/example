"""
case_engine/topic_registry.py

Static slot definitions for all 5 topic families.

Each TopicKey maps to a SlotRegistry that declares:
  - required: slots that must be FILLED before workflow execution proceeds
  - optional: collected opportunistically; never block progress

Slot definitions follow 03_CASE_STATE_AND_DECISIONING.md.

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

# Phone last-4: 4–10 digits (last 4 displayed; may include more context)
_PHONE_PATTERN = re.compile(r"\d{4,10}")

# Session ID: KID- prefix + 8+ alphanumeric chars
_SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9\-]{4,64}")

# Application/agent ID: free-form, non-empty
_FREE_FORM_PATTERN = re.compile(r".{1,128}", re.DOTALL)

# ── Topic slot registry ───────────────────────────────────────────────────────

_TOPIC_SLOT_REGISTRY: dict[TopicKey, SlotRegistry] = {

    TopicKey.VKYC_SESSION_FAILURE: SlotRegistry(
        required=(
            SlotDefinition(
                name="session_id",
                description="VKYC session identifier (e.g., KID-XXXXXXXX)",
                clarification_prompt=(
                    "Please provide the VKYC session ID. "
                    "It is usually visible in the session URL or error message (e.g., KID-AB12CD34)."
                ),
                validation_pattern=_SESSION_ID_PATTERN,
            ),
            SlotDefinition(
                name="phone_number",
                description="Last 4 digits of the customer's registered phone number",
                clarification_prompt=(
                    "Please provide the last 4 digits of the customer's registered phone number."
                ),
                validation_pattern=_PHONE_PATTERN,
            ),
        ),
        optional=(
            SlotDefinition(
                name="failure_code",
                description="Error code from the VKYC session (optional)",
                clarification_prompt="Do you have an error code from the VKYC session? (optional)",
                required=False,
            ),
        ),
    ),

    TopicKey.OTP_DELIVERY_FAILURE: SlotRegistry(
        required=(
            SlotDefinition(
                name="phone_number",
                description="Last 4 digits of the customer's registered phone number",
                clarification_prompt=(
                    "Please provide the last 4 digits of the customer's registered phone number."
                ),
                validation_pattern=_PHONE_PATTERN,
            ),
            SlotDefinition(
                name="channel",
                description="OTP delivery channel that is failing",
                clarification_prompt=(
                    "Which OTP channel is failing? Please specify: SMS, EMAIL, or VOICE."
                ),
                valid_values=_OTP_CHANNELS,
            ),
        ),
        optional=(
            SlotDefinition(
                name="attempt_count",
                description="Number of OTP delivery attempts made so far",
                clarification_prompt="How many OTP delivery attempts have been made so far? (optional)",
                required=False,
            ),
        ),
    ),

    TopicKey.DOCUMENT_OCR_FAILURE: SlotRegistry(
        required=(
            SlotDefinition(
                name="document_type",
                description="Type of document failing OCR",
                clarification_prompt=(
                    "Which document type is failing OCR? "
                    "Please specify: AADHAAR, PAN, PASSPORT, or VOTERID."
                ),
                valid_values=_DOC_TYPES,
            ),
            SlotDefinition(
                name="application_id",
                description="Customer application or onboarding ID (e.g., APP-XXXXXXXX)",
                clarification_prompt=(
                    "Please provide the customer's application ID "
                    "(e.g., APP-00129871 or similar onboarding reference)."
                ),
                validation_pattern=_FREE_FORM_PATTERN,
            ),
        ),
        optional=(
            SlotDefinition(
                name="error_code",
                description="OCR error code or failure message (optional)",
                clarification_prompt="Do you have an OCR error code or failure message? (optional)",
                required=False,
            ),
        ),
    ),

    TopicKey.AGENT_PORTAL_ISSUE: SlotRegistry(
        required=(
            SlotDefinition(
                name="agent_id",
                description="Agent's identifier in the portal system",
                clarification_prompt=(
                    "Please provide the agent ID (e.g., AGT-XXXXXXXX or the login username)."
                ),
                validation_pattern=_FREE_FORM_PATTERN,
            ),
            SlotDefinition(
                name="portal_type",
                description="Portal interface where the issue is occurring",
                clarification_prompt=(
                    "Which portal interface is affected? Please specify: WEB, MOBILE, or DESKTOP."
                ),
                valid_values=_PORTAL_TYPES,
            ),
        ),
        optional=(
            SlotDefinition(
                name="error_message",
                description="Error message or code displayed in the portal",
                clarification_prompt="What error message or code is displayed? (optional)",
                required=False,
            ),
        ),
    ),

    TopicKey.API_CALLBACK_FAILURE: SlotRegistry(
        required=(
            SlotDefinition(
                name="callback_type",
                description="Type of API callback that is failing",
                clarification_prompt=(
                    "Which callback type is failing? Please specify: CBS, DMS, SFDC, or WEBHOOK."
                ),
                valid_values=_CALLBACK_TYPES,
            ),
            SlotDefinition(
                name="application_id",
                description="Customer application or onboarding ID (e.g., APP-XXXXXXXX)",
                clarification_prompt=(
                    "Please provide the application ID "
                    "(e.g., APP-00129871 or similar onboarding reference)."
                ),
                validation_pattern=_FREE_FORM_PATTERN,
            ),
        ),
        optional=(
            SlotDefinition(
                name="error_code",
                description="Callback error code or HTTP status (optional)",
                clarification_prompt="What error code or HTTP status is returned by the callback? (optional)",
                required=False,
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
