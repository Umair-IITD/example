"""
audit — Sprint 2.10 audit subsystem.

Public API:
    AuditEvent                — immutable audit record
    AuditEventType            — canonical event type enum
    AuditRepository           — abstract storage interface
    InMemoryAuditRepository   — default in-process implementation
    SupabaseAuditRepository   — persistent Supabase implementation (Sprint 2.10)
    AuditService              — preferred entrypoint for emit + query
    AuditLogger               — repository-backed logger (backward compat)
    build_audit_repository    — factory: selects backend from AUDIT_BACKEND env var
"""
from audit.factory import build_audit_repository
from audit.logger import AuditLogger
from audit.models import AuditEvent, AuditEventType
from audit.repository import AuditRepository, InMemoryAuditRepository
from audit.repository_supabase import SupabaseAuditRepository
from audit.service import AuditService

__all__ = [
    "AuditEvent",
    "AuditEventType",
    "AuditRepository",
    "InMemoryAuditRepository",
    "SupabaseAuditRepository",
    "AuditService",
    "AuditLogger",
    "build_audit_repository",
]
