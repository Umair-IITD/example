from .taxonomy import IssueCategory

# Default thresholds for confidence scores to trigger auto-responses
# Categories with higher risk/complexity have higher thresholds
CATEGORY_THRESHOLDS = {
    IssueCategory.PAN_MISMATCH: 0.85,
    IssueCategory.OCR_FAILURE: 0.80,
    IssueCategory.VKYC_ISSUE: 0.75,
    IssueCategory.AUTH_FAILURE: 0.90,
    IssueCategory.VIDEO_NOT_AVAILABLE: 0.70,
    IssueCategory.NETWORK_ISSUE: 0.65,
    IssueCategory.OTHER: 0.75
}

DEFAULT_THRESHOLD = 0.75

def get_threshold_for_category(category: str) -> float:
    try:
        cat = IssueCategory(category)
        return CATEGORY_THRESHOLDS.get(cat, DEFAULT_THRESHOLD)
    except ValueError:
        return DEFAULT_THRESHOLD
