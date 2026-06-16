"""
case_engine/ticket_orchestration

Sprint 2.27.5: Ticket lifecycle orchestration layer.

Per blueprint flow_diagram.mermaid:
  FD → TICKET → CASE → ... → CLOSE

TicketOrchestrator owns the ticket lifecycle from ingestion to closure.
It is the integration point between Freshdesk and the Support Agent Runtime.
"""
from case_engine.ticket_orchestration.models import (
    TicketContext,
    TicketLifecycleState,
    TicketOrchestrationResult,
)
from case_engine.ticket_orchestration.orchestrator import (
    TicketOrchestrator,
    build_ticket_orchestrator,
)

__all__ = [
    "TicketContext",
    "TicketLifecycleState",
    "TicketOrchestrationResult",
    "TicketOrchestrator",
    "build_ticket_orchestrator",
]
