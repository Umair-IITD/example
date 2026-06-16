"""
case_engine/response_generation

Sprint 2.27.5: Customer Response Layer.

Per blueprint flow_diagram.mermaid — USERRESPONSE node:
  FDUPDATE --> USERRESPONSE --> FD --> CLOSECHECK

Implements the USERRESPONSE node using deterministic templates.
Architecture designed for future LLM replacement — the generator
is injected so the LLM provider can replace it without structural changes.

Blueprint Section 24 (Customer Response Generation):
  Uses SOPs, root cause, and resolution outcome.
  LLM responsibilities: Rewrite, Summarize, Humanize.
  LLM never executes actions.
"""
from case_engine.response_generation.models import (
    ResponseContext,
    ResponseDraft,
    ResponseMetadata,
    ResponseType,
)
from case_engine.response_generation.service import (
    ResponseGenerationService,
    build_response_generation_service,
)

__all__ = [
    "ResponseContext",
    "ResponseDraft",
    "ResponseMetadata",
    "ResponseType",
    "ResponseGenerationService",
    "build_response_generation_service",
]
