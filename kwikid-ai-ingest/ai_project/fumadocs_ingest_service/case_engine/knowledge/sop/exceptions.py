"""
case_engine/knowledge/sop/exceptions.py

Sprint 2.41: SOP-specific exception hierarchy.

All SOP exceptions descend from SOPError, which itself descends from Exception.
Callers can catch SOPError to handle any SOP failure, or catch specific
subclasses for fine-grained control.
"""
from __future__ import annotations


class SOPError(Exception):
    """Base exception for all SOP-related errors."""


class SOPNotFoundError(SOPError):
    """Raised when a requested SOP cannot be found in the repository."""


class SOPValidationError(SOPError):
    """
    Raised when a SOPDocument fails one or more validation rules.

    errors: list of human-readable validation error strings.
    """
    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors: list[str] = errors or []


class DuplicateSOPError(SOPError):
    """
    Raised when registering a SOPDocument whose (sop_id, version) pair
    already exists in the repository and replace=False.
    """


class SOPVersionConflictError(SOPError):
    """
    Raised when a SOP version string is malformed or conflicts with
    an existing registration in an incompatible way.
    """


class SOPRegistryError(SOPError):
    """
    Raised for errors in the SOP singleton registry lifecycle
    (e.g., registry not initialised when expected).
    """


class SOPGraphCycleError(SOPValidationError):
    """
    Raised when step dependencies within a SOPDocument form a directed cycle.

    This is a specialisation of SOPValidationError (validation rule V05).
    """
