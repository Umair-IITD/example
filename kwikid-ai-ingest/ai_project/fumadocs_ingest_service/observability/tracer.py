import os
import json
import uuid
from datetime import datetime
from typing import Optional
from .models import Trace
from .logger import logger

class Tracer:
    def __init__(self, trace_dir: str = "./traces", enabled: bool = True):
        self.trace_dir = trace_dir
        self.enabled = enabled
        if self.enabled and not os.path.exists(self.trace_dir):
            os.makedirs(self.trace_dir, exist_ok=True)

    def start_trace(self, query: str, session_id: Optional[str] = None, **kwargs) -> Trace:
        trace_id = str(uuid.uuid4())
        trace = Trace(
            trace_id=trace_id,
            query=query,
            session_id=session_id,
            **kwargs
        )
        return trace

    def save_trace(self, trace: Trace):
        if not self.enabled:
            return

        try:
            filename = f"{trace.timestamp.strftime('%Y%m%d_%H%M%S')}_{trace.trace_id[:8]}.json"
            filepath = os.path.join(self.trace_dir, filename)
            
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(trace.json(indent=2))
            
            logger.info(f"Trace saved: {filepath}", extra={"trace_id": trace.trace_id})
        except Exception as e:
            logger.error(f"Failed to save trace: {e}", extra={"trace_id": trace.trace_id})

# Global tracer instance
tracer = Tracer(
    trace_dir=os.getenv("TRACE_DIR", "./traces"),
    enabled=os.getenv("DEBUG_TRACE", "true").lower() == "true"
)
