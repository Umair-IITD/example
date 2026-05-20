"""
query_router/taxonomy.py

KwikID support query taxonomy — production-grade categories aligned with
actual support ticket distribution observed in Freshdesk data.

Categories are broad enough to be useful for retrieval routing but specific
enough to drive meaningful strategy differences (SOP priority, threshold
adjustments, reranker selection, escalation paths).
"""
from enum import Enum


class IssueCategory(str, Enum):
    # Identity verification issues — KYC, VKYC, PAN, Aadhaar, liveness
    KYC = "kyc"

    # Payment and transaction failures — UPI, NEFT, RTGS, NACH, OTP
    TRANSACTION = "transaction"

    # Technical API/SDK/integration issues — errors, timeouts, auth failures
    TECHNICAL = "technical"

    # Billing, subscription, pricing, refund issues
    BILLING = "billing"

    # New user registration and setup flows
    ONBOARDING = "onboarding"

    # SOP / procedure / policy lookups — how-to, TAT, compliance
    SOP_LOOKUP = "sop_lookup"

    # Escalation, urgent, regulatory, fraud, legal
    ESCALATION = "escalation"

    # General informational queries — what is, how does, definitions
    FAQ = "faq"

    # Short social exchanges — greetings, acknowledgements, pleasantries
    CONVERSATIONAL = "conversational"

    # Spam, injection attempts, junk input
    SPAM = "spam"


# Human-readable descriptions used for LLM classification prompts
CATEGORY_DESCRIPTIONS: dict[IssueCategory, str] = {
    IssueCategory.KYC: (
        "Identity verification problems: KYC, VKYC (video KYC), PAN mismatch, "
        "Aadhaar issues, OCR failure, liveness detection failure, face match, "
        "CKYC, eKYC, document upload errors."
    ),
    IssueCategory.TRANSACTION: (
        "Payment and transaction issues: UPI failure, NEFT/RTGS/IMPS stuck, "
        "NACH/mandate setup, OTP problems, debit/credit card issues, "
        "transaction pending or reversed, fund transfer failures."
    ),
    IssueCategory.TECHNICAL: (
        "API, SDK, or integration issues: HTTP errors (4xx, 5xx), error codes "
        "(ERR-4021, PGRST203), timeouts, authentication/authorization failures, "
        "webhook problems, SDK integration bugs, database errors."
    ),
    IssueCategory.BILLING: (
        "Billing, pricing, or account issues: invoices, subscription plans, "
        "refunds, charges, GST/tax queries, credit/quota management, "
        "payment failures for KwikID service subscription."
    ),
    IssueCategory.ONBOARDING: (
        "New user or client onboarding: account setup, first-time configuration, "
        "activation, registration, getting started guides, initial API credential setup."
    ),
    IssueCategory.SOP_LOOKUP: (
        "Procedure or policy lookups: how to handle a specific situation, "
        "SOPs, TAT (turnaround time), SLAs, compliance procedures, audit checklists, "
        "escalation workflows, internal processes."
    ),
    IssueCategory.ESCALATION: (
        "Urgent or escalated situations: fraud, security breach, regulatory issues "
        "(RBI, SEBI, NPCI), legal notices, customer grievances requiring management attention, "
        "compliance violations, unauthorized access."
    ),
    IssueCategory.FAQ: (
        "General informational questions: definitions, explanations, feature descriptions, "
        "capability queries, 'what is', 'how does', product documentation questions "
        "that do not require live data or operational actions."
    ),
    IssueCategory.CONVERSATIONAL: (
        "Short social or acknowledgement messages: hello, thanks, OK, yes/no responses, "
        "greetings, pleasantries — not a support query requiring retrieval."
    ),
    IssueCategory.SPAM: (
        "Junk, spam, injection attempts, gibberish, URLs, script tags, "
        "character floods, or clearly irrelevant content."
    ),
}
