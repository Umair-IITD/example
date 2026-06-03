"""
executors — Sprint 2.5 production executors.

Public API:
    AddTicketNoteExecutor        — add a note to a Freshdesk ticket (REVERSIBLE)
    UpdateTicketStatusExecutor   — change a ticket's status (REVERSIBLE)
    IdentityResetOtpExecutor     — record an OTP reset action (IRREVERSIBLE)

All executors:
  - Inherit ActionExecutor (ABC from case_engine.action_executor)
  - Accept a ProviderRouter at construction (dependency injection)
  - Route all provider calls through the router (no direct Freshdesk imports)
  - Translate ProviderError → ActionExecutionError via _BaseProviderExecutor

Registration example (at application startup):
    from case_engine.executor_registry import ActionExecutorRegistry
    from case_engine.provider_router import ProviderRouter
    from executors import AddTicketNoteExecutor, UpdateTicketStatusExecutor, IdentityResetOtpExecutor

    registry = ActionExecutorRegistry()
    router   = ProviderRouter(provider_registry)   # provider_registry already populated

    registry.register_executor(AddTicketNoteExecutor(router))
    registry.register_executor(UpdateTicketStatusExecutor(router))
    registry.register_executor(IdentityResetOtpExecutor(router))

Dependency direction: executors → case_engine (never the reverse).
"""
from executors.add_ticket_note_executor import AddTicketNoteExecutor
from executors.identity_reset_otp_executor import IdentityResetOtpExecutor
from executors.update_ticket_status_executor import UpdateTicketStatusExecutor

__all__ = [
    "AddTicketNoteExecutor",
    "UpdateTicketStatusExecutor",
    "IdentityResetOtpExecutor",
]
