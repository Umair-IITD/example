# Query Router Foundation

## 🎯 Purpose
The Query Router is responsible for classifying incoming user queries into a predefined taxonomy. This classification is used to:
1. Apply category-specific confidence thresholds.
2. Route queries to specialized RAG prompts or human agents.
3. Improve diagnostic accuracy.

## 📁 Structure
- `taxonomy.py`: Definition of issue categories (IssueCategory Enum).
- `thresholds.py`: Category-specific confidence score thresholds.
- `classifier.py`: Logic for classifying queries (currently a skeleton).
- `models.py`: Pydantic schemas for classification and routing results.

## 🏷️ Taxonomy Example
- `AUTH_FAILURE`: OTP delivery issues, login problems.
- `VKYC_ISSUE`: Failures during video KYC.
- `OCR_FAILURE`: Document scanning errors.
- `VIDEO_NOT_AVAILABLE`: Browser/session issues during video call.

## 🚀 Future Integration
This system is designed to be integrated into the main `chat` flow as a pre-retrieval step to optimize search parameters and response constraints.
