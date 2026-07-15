"""
unity/exceptions.py

Sprint 2.51: Typed error hierarchy for Unity Admin Portal integration.

All exceptions inherit from UnityError. Callers can catch the base to
convert transport / API failures into deterministic evidence with
evidence_available=False.

Dependency direction: exceptions.py → stdlib only.
"""
from __future__ import annotations

from typing import Any


class UnityError(RuntimeError):
    """Base error for all Unity Admin Portal failures."""


class UnityAuthenticationFailure(UnityError):
    """
    Token generation failed, or a subsequent 401 was returned even after
    a fresh token was fetched. Callers should surface this without retrying.
    """
    def __init__(self, message: str, *, cause: str = "") -> None:
        super().__init__(message)
        self.cause = cause


class UnityApiError(UnityError):
    """HTTP error response from the Unity API (non-401)."""
    def __init__(
        self, message: str, *, status_code: int, response_body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class UnitySessionNotFound(UnityError):
    """A specific session_id or phone_number lookup returned zero results."""
    def __init__(self, identifier: str, *, kind: str = "phone_number") -> None:
        super().__init__(f"no Unity {kind} match for {identifier!r}")
        self.identifier = identifier
        self.kind = kind


class UnityMultipleSessionCandidates(UnityError):
    """
    getAllUserSession returned multiple candidates and no selection heuristic
    could pick one. Callers may need to escalate to a human.
    """
    def __init__(self, count: int, phone_number: str) -> None:
        super().__init__(
            f"multiple session candidates ({count}) for phone_number {phone_number!r}"
        )
        self.count = count
        self.phone_number = phone_number


class UnityNetworkFailure(UnityError):
    """TCP / DNS / connection-level failure to Unity."""


class UnityTimeoutFailure(UnityError):
    """Connect or read timeout to Unity."""


class UnityServiceUnavailable(UnityError):
    """Unity returned 5xx after all retries exhausted."""
    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class UnityUnexpectedResponse(UnityError):
    """Unity returned a 2xx that we could not parse or validate."""
    def __init__(self, message: str, *, body: Any = None) -> None:
        super().__init__(message)
        self.body = body


class UnityDisabled(UnityError):
    """Integration is disabled by configuration (`UNITY_ENABLED=false`)."""
