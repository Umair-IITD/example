from enum import Enum

class IssueCategory(str, Enum):
    PAN_MISMATCH = "pan_mismatch"
    OCR_FAILURE = "ocr_failure"
    VKYC_ISSUE = "vkyc_issue"
    ONBOARDING_STUCK = "onboarding_stuck"
    API_TIMEOUT = "api_timeout"
    FACE_MATCH_FAILURE = "face_match_failure"
    VIDEO_NOT_AVAILABLE = "video_not_available"
    AUTH_FAILURE = "auth_failure"
    NETWORK_ISSUE = "network_issue"
    OTHER = "other"

# Description of categories for LLM classification
CATEGORY_DESCRIPTIONS = {
    IssueCategory.PAN_MISMATCH: "Issues where the PAN card details do not match the user's input or records.",
    IssueCategory.OCR_FAILURE: "Failure of the Optical Character Recognition system to read documents.",
    IssueCategory.VKYC_ISSUE: "Problems encountered during the Video KYC process.",
    IssueCategory.AUTH_FAILURE: "OTP failures, login issues, or unauthorized access errors.",
    IssueCategory.OTHER: "General queries or issues that do not fit into other categories."
}
