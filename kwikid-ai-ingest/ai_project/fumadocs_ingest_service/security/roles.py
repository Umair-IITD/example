"""
security/roles.py

Role definitions and permission matrix for Sprint 2.8 RBAC.

Roles are additive: ADMIN > OPERATOR > APPROVER in terms of permissions.
All permission checks go through has_permission() — never check role.value directly.
"""
from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    """Caller roles — assigned at API-key registration time."""
    APPROVER = "approver"
    OPERATOR = "operator"
    ADMIN    = "admin"


class Permission(str, Enum):
    """Fine-grained permission tokens checked by authorization logic."""
    APPROVE_ACTION  = "approve_action"
    REJECT_ACTION   = "reject_action"
    WORKER_TICK     = "worker_tick"
    WATCHDOG_RUN    = "watchdog_run"


# Permission matrix: each role grants a frozenset of permissions.
# ADMIN is a superset of everything.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.APPROVER: frozenset({
        Permission.APPROVE_ACTION,
        Permission.REJECT_ACTION,
    }),
    Role.OPERATOR: frozenset({
        Permission.WORKER_TICK,
        Permission.WATCHDOG_RUN,
    }),
    Role.ADMIN: frozenset({
        Permission.APPROVE_ACTION,
        Permission.REJECT_ACTION,
        Permission.WORKER_TICK,
        Permission.WATCHDOG_RUN,
    }),
}


def has_permission(role: Role, permission: Permission) -> bool:
    """Return True if the role grants the requested permission."""
    return permission in ROLE_PERMISSIONS.get(role, frozenset())
