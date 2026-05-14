from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict
from datetime import datetime

class ChunkMetadata(BaseModel):
    id: str
    source_type: str
    similarity: float
    rerank_score: Optional[float] = None
    title: Optional[str] = None
    content_snippet: Optional[str] = None

class LLMResponseMetadata(BaseModel):
    model: str
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    latency_ms: Optional[float] = None
    finish_reason: Optional[str] = None

class Trace(BaseModel):
    trace_id: str = Field(..., description="Unique ID for the trace, often matching session_id or query_hash")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    query: str
    session_id: Optional[str] = None
    query_hash: Optional[str] = None
    classification: Optional[str] = None
    
    # Timings
    embedding_latency_ms: Optional[float] = None
    retrieval_latency_ms: Optional[float] = None
    generation_latency_ms: Optional[float] = None
    total_latency_ms: Optional[float] = None
    
    # Retrieval
    retrieval_candidates_count: int = 0
    top_matches: List[ChunkMetadata] = []
    selected_chunks_ids: List[str] = []
    
    # Generation
    llm_response: Optional[str] = None
    llm_metadata: Optional[LLMResponseMetadata] = None
    confidence_score: Optional[float] = None
    citations: List[str] = []
    
    # Metadata
    metadata: Dict[str, Any] = {}
    errors: List[str] = []

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
