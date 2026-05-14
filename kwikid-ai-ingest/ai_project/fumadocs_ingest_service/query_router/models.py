from pydantic import BaseModel, Field
from typing import Optional, List
from .taxonomy import IssueCategory

class ClassificationResult(BaseModel):
    category: IssueCategory
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: Optional[str] = None
    suggested_labels: List[str] = []

class RoutingDecision(BaseModel):
    category: str
    threshold_met: bool
    action: str = Field(description="e.g., 'auto_reply', 'human_escalation', 'ask_clarification'")
    metadata: dict = {}
