"""
case_engine/tools/adapters/unity_tools.py

Sprint 2.51: Production Unity Admin Portal BaseTool adapters.

Replaces the five Sprint 2.17 mock tools in `case_engine/tools/mock_tools.py`
with real implementations that query Unity Admin Portal
(https://vkyc360.unitybank.co.in).

Tool contract (SAME as mocks — Investigation Layer cannot tell the
difference):
    GetSessionDetailsTool       inputs={session_id}          → session evidence
    GetUserDetailsTool          inputs={phone_number}        → session-derived user info
    GetFailureReasonTool        inputs={operation_id (=session_id)} → failure summary
    GetCaseHistoryTool          inputs={phone_number}        → aggregated recent sessions
    GetOnboardingStatusTool     inputs={application_id (=phone_number)}
                                                             → onboarding stage inferred

All five expose `provider=ToolProvider.UNITY` and `capability=ToolCapability.READ`.

Design rules:
- Never raise — every failure folds into a canonical UnityEvidence dict with
  `evidence_available` set to UNAVAILABLE / PARTIAL / DISABLED and `error`
  populated. Callers still get a `.run()` return dict.
- Async work happens under `asyncio.run(_async_impl())`; when we detect an
  existing running loop (pytest-asyncio) we spin up a fresh loop.
- 10 canonical traces via `unity.emit_unity_trace`; PII sanitizer strips
  any `@`, `password`, `Bearer`, JWT, `PAN`, `Aadhaar` values that leak.
- All context (tenant_id, case_id, trace_id, ticket_created_at) is
  passthrough — the collector sets these; the adapters preserve them into
  the evidence payload.

Dependency direction:
    unity_tools.py → case_engine.tools.tool_executor.BaseTool
                   → case_engine.tools.tool_models
                   → unity.*
                   → stdlib
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from case_engine.tools.tool_executor import BaseTool
from case_engine.tools.tool_models import (
    ToolCapability,
    ToolDefinition,
    ToolProvider,
)
from unity import (
    EvidenceAvailability,
    SessionList,
    UnityClient,
    UnityConfig,
    UnityError,
    UnitySession,
    UnitySessionNotFound,
    build_unity_evidence,
    derive_failure_summary,
    derive_onboarding_stage,
    derive_recent_summary,
    parse_session_details,
    parse_session_list,
    select_most_relevant_session,
    TRACE_ENTER_EVIDENCE_MAPPING,
    TRACE_EXIT_EVIDENCE_MAPPING,
    emit_unity_trace,
)

LOGGER = logging.getLogger(__name__)


# ── Shared async runner + fetchers ────────────────────────────────────────────

def _run_async(coro):
    """
    Bridge sync BaseTool.run() to async work.
    Works from both sync contexts and async contexts (FastAPI async background tasks).
    When a running event loop is detected, the coroutine is executed in a separate
    OS thread via ThreadPoolExecutor so asyncio.run() can create its own loop.
    """
    try:
        asyncio.get_running_loop()
        # Already inside a running event loop — run in a separate thread to avoid
        # "asyncio.run() cannot be called from a running event loop".
        import concurrent.futures  # noqa: PLC0415
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        # No running loop present — asyncio.run() is safe.
        return asyncio.run(coro)


async def _fetch_session_list(config: UnityConfig, phone_number: str,
                              *, client_factory=None) -> tuple[SessionList | None, str, str]:
    """Return (SessionList | None, error, error_code). Never raises."""
    client_factory = client_factory or (lambda: UnityClient(config))
    client = client_factory()
    try:
        raw = await client.get_all_user_sessions(phone_number)
        return parse_session_list(raw, phone_number=phone_number), "", ""
    except UnitySessionNotFound as exc:
        return None, str(exc), "SESSION_NOT_FOUND"
    except UnityError as exc:
        return None, str(exc), type(exc).__name__
    finally:
        await client.close()


async def _fetch_session_details(config: UnityConfig, session_id: str,
                                 *, client_factory=None) -> tuple[UnitySession | None, str, str]:
    client_factory = client_factory or (lambda: UnityClient(config))
    client = client_factory()
    try:
        raw = await client.get_session_details(session_id)
        session = parse_session_details(raw)
        if session is None:
            return None, "no session_data in response", "EMPTY_RESPONSE"
        return session, "", ""
    except UnitySessionNotFound as exc:
        return None, str(exc), "SESSION_NOT_FOUND"
    except UnityError as exc:
        return None, str(exc), type(exc).__name__
    finally:
        await client.close()


def _availability_for(config: UnityConfig, error_code: str) -> EvidenceAvailability:
    if not config.enabled:
        return EvidenceAvailability.DISABLED
    if not error_code:
        return EvidenceAvailability.AVAILABLE
    if error_code in {"SESSION_NOT_FOUND", "EMPTY_RESPONSE"}:
        return EvidenceAvailability.AVAILABLE   # not found is a business outcome
    return EvidenceAvailability.UNAVAILABLE


def _emit_mapping(
    tool: str, *, tenant: str, case_id: str, trace_id: str,
    endpoint: str, status: str,
) -> None:
    emit_unity_trace(
        TRACE_ENTER_EVIDENCE_MAPPING,
        tool=tool, tenant=tenant, case_id=case_id, trace_id=trace_id,
        endpoint=endpoint, status="STARTED",
    )


def _emit_mapping_done(
    tool: str, *, tenant: str, case_id: str, trace_id: str,
    endpoint: str, status: str,
) -> None:
    emit_unity_trace(
        TRACE_EXIT_EVIDENCE_MAPPING,
        tool=tool, tenant=tenant, case_id=case_id, trace_id=trace_id,
        endpoint=endpoint, status=status,
    )


# ── GetSessionDetailsTool (production) ─────────────────────────────────────

class GetSessionDetailsTool(BaseTool):
    """
    Production replacement for the Sprint 2.17 mock.

    Input: {"session_id": "<UUID v4>"} (required)
    Optional passthrough: tenant_id, case_id, trace_id.

    Behaviour:
      - Calls GET /v1/session/get_details/{session_id}
      - Normalises into UnitySession
      - Returns UnityEvidence.to_dict() with session_found true/false
      - Never raises
    """

    TOOL_NAME = "GetSessionDetailsTool"

    def __init__(
        self, config: UnityConfig | None = None, *, client_factory=None,
    ) -> None:
        self._config = config or UnityConfig.from_env()
        self._client_factory = client_factory

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_name=self.TOOL_NAME,
            description=(
                "Retrieve VKYC session details from Unity Admin Portal by session UUID. "
                "Returns canonical UnityEvidence with session status, timeline, "
                "auditor decision, feedback, failed steps, and PII-masked customer info."
            ),
            required_inputs=("session_id",),
            output_schema={
                "tool_name":          "str",
                "evidence_available": "AVAILABLE | PARTIAL | UNAVAILABLE | DISABLED",
                "session_found":      "bool",
                "session":            "UnitySession dict | null",
                "error":              "str",
            },
            version="2.0.0",
            tags=("unity", "vkyc", "session", "read_only"),
            provider=ToolProvider.UNITY,
            capability=ToolCapability.READ,
        )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        session_id = str(inputs.get("session_id") or "").strip()
        tenant  = str(inputs.get("tenant_id") or self._config.domain)
        case_id = str(inputs.get("case_id") or "")
        trace_id = str(inputs.get("trace_id") or "")

        if not self._config.enabled:
            return build_unity_evidence(
                tool_name=self.TOOL_NAME,
                availability=EvidenceAvailability.DISABLED,
                error="unity integration disabled by configuration",
                error_code="DISABLED",
                tenant_id=tenant, trace_id=trace_id, case_id=case_id,
                query_session_id=session_id,
            ).to_dict()

        if not session_id:
            return build_unity_evidence(
                tool_name=self.TOOL_NAME,
                availability=EvidenceAvailability.UNAVAILABLE,
                error="session_id input missing",
                error_code="MISSING_INPUT",
                tenant_id=tenant, trace_id=trace_id, case_id=case_id,
            ).to_dict()

        session, err, err_code = _run_async(
            _fetch_session_details(self._config, session_id,
                                   client_factory=self._client_factory)
        )
        _emit_mapping(self.TOOL_NAME, tenant=tenant, case_id=case_id,
                      trace_id=trace_id, endpoint=f"/v1/session/get_details/{session_id[:8]}",
                      status="STARTED")
        evidence = build_unity_evidence(
            tool_name=self.TOOL_NAME,
            session=session,
            session_found=session is not None,
            availability=_availability_for(self._config, err_code),
            error=err,
            error_code=err_code,
            tenant_id=tenant, trace_id=trace_id, case_id=case_id,
            query_session_id=session_id,
        )
        _emit_mapping_done(self.TOOL_NAME, tenant=tenant, case_id=case_id,
                           trace_id=trace_id, endpoint="mapping",
                           status=evidence.evidence_available.value)
        return evidence.to_dict()


# ── GetUserDetailsTool (production) ────────────────────────────────────────

class GetUserDetailsTool(BaseTool):
    """
    Production replacement for the Sprint 2.17 mock.

    Input: {"phone_number": "<10-digit>"} (required)

    Behaviour:
      - Calls GET /api/v1/getAllUserSession/{domain}/{phone_number}
      - Selects most-relevant session (if any)
      - Returns UnityEvidence with the selected session + all_session_count
    """

    TOOL_NAME = "GetUserDetailsTool"

    def __init__(
        self, config: UnityConfig | None = None, *, client_factory=None,
    ) -> None:
        self._config = config or UnityConfig.from_env()
        self._client_factory = client_factory

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_name=self.TOOL_NAME,
            description=(
                "Retrieve customer VKYC profile from Unity Admin Portal by "
                "phone number. Returns the most-relevant session (out of all "
                "sessions for the customer) plus total session count."
            ),
            required_inputs=("phone_number",),
            output_schema={
                "tool_name":         "str",
                "evidence_available": "enum",
                "session":           "UnitySession dict | null",
                "all_session_count": "int",
            },
            version="2.0.0",
            tags=("unity", "user", "kyc", "read_only"),
            provider=ToolProvider.UNITY,
            capability=ToolCapability.READ,
        )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        phone = str(inputs.get("phone_number") or "").strip()
        tenant  = str(inputs.get("tenant_id") or self._config.domain)
        case_id = str(inputs.get("case_id") or "")
        trace_id = str(inputs.get("trace_id") or "")
        ticket_created_at = inputs.get("ticket_created_at")

        if not self._config.enabled:
            return build_unity_evidence(
                tool_name=self.TOOL_NAME,
                availability=EvidenceAvailability.DISABLED,
                error="unity integration disabled by configuration",
                error_code="DISABLED",
                tenant_id=tenant, trace_id=trace_id, case_id=case_id,
                query_phone_number=phone,
            ).to_dict()

        if not phone:
            return build_unity_evidence(
                tool_name=self.TOOL_NAME,
                availability=EvidenceAvailability.UNAVAILABLE,
                error="phone_number input missing",
                error_code="MISSING_INPUT",
                tenant_id=tenant, trace_id=trace_id, case_id=case_id,
            ).to_dict()

        session_list, err, err_code = _run_async(
            _fetch_session_list(self._config, phone,
                                client_factory=self._client_factory)
        )
        _emit_mapping(self.TOOL_NAME, tenant=tenant, case_id=case_id,
                      trace_id=trace_id, endpoint="mapping", status="STARTED")

        if session_list is None:
            evidence = build_unity_evidence(
                tool_name=self.TOOL_NAME,
                availability=_availability_for(self._config, err_code),
                error=err, error_code=err_code,
                tenant_id=tenant, trace_id=trace_id, case_id=case_id,
                query_phone_number=phone,
            )
        else:
            selected = select_most_relevant_session(
                session_list.sessions,
                ticket_created_at=ticket_created_at,
            )
            evidence = build_unity_evidence(
                tool_name=self.TOOL_NAME,
                session=selected,
                session_found=selected is not None,
                all_session_count=session_list.session_count,
                all_sessions=session_list.sessions,
                availability=EvidenceAvailability.AVAILABLE,
                tenant_id=tenant, trace_id=trace_id, case_id=case_id,
                query_phone_number=phone,
            )
        _emit_mapping_done(self.TOOL_NAME, tenant=tenant, case_id=case_id,
                           trace_id=trace_id, endpoint="mapping",
                           status=evidence.evidence_available.value)
        return evidence.to_dict()


# ── GetFailureReasonTool (production) ──────────────────────────────────────

class GetFailureReasonTool(BaseTool):
    """
    Production replacement for the Sprint 2.17 mock.

    Input: {"operation_id": "<session_id UUID v4>"} (required)

    Derives failure category/code/message/action from session_status,
    feedback, and overall_summary.
    """

    TOOL_NAME = "GetFailureReasonTool"

    def __init__(
        self, config: UnityConfig | None = None, *, client_factory=None,
    ) -> None:
        self._config = config or UnityConfig.from_env()
        self._client_factory = client_factory

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_name=self.TOOL_NAME,
            description=(
                "Derive failure category / code / message / recommended action "
                "for a VKYC session from Unity Admin Portal data."
            ),
            required_inputs=("operation_id",),
            output_schema={
                "tool_name":       "str",
                "failure_summary": "dict[failure_category, failure_code, ...]",
            },
            version="2.0.0",
            tags=("unity", "vkyc", "failure_analysis", "read_only"),
            provider=ToolProvider.UNITY,
            capability=ToolCapability.READ,
        )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        # operation_id treated as session_id per SOT investigation_mapping §1
        session_id = str(inputs.get("operation_id") or inputs.get("session_id") or "").strip()
        tenant  = str(inputs.get("tenant_id") or self._config.domain)
        case_id = str(inputs.get("case_id") or "")
        trace_id = str(inputs.get("trace_id") or "")

        if not self._config.enabled:
            return build_unity_evidence(
                tool_name=self.TOOL_NAME,
                availability=EvidenceAvailability.DISABLED,
                error="unity integration disabled by configuration",
                error_code="DISABLED",
                tenant_id=tenant, trace_id=trace_id, case_id=case_id,
                query_session_id=session_id,
                failure_summary=derive_failure_summary(None),
            ).to_dict()

        if not session_id:
            return build_unity_evidence(
                tool_name=self.TOOL_NAME,
                availability=EvidenceAvailability.UNAVAILABLE,
                error="operation_id input missing",
                error_code="MISSING_INPUT",
                tenant_id=tenant, trace_id=trace_id, case_id=case_id,
                failure_summary=derive_failure_summary(None),
            ).to_dict()

        session, err, err_code = _run_async(
            _fetch_session_details(self._config, session_id,
                                   client_factory=self._client_factory)
        )
        _emit_mapping(self.TOOL_NAME, tenant=tenant, case_id=case_id,
                      trace_id=trace_id, endpoint="mapping", status="STARTED")
        evidence = build_unity_evidence(
            tool_name=self.TOOL_NAME,
            session=session,
            session_found=session is not None,
            availability=_availability_for(self._config, err_code),
            error=err, error_code=err_code,
            tenant_id=tenant, trace_id=trace_id, case_id=case_id,
            query_session_id=session_id,
            failure_summary=derive_failure_summary(session),
        )
        _emit_mapping_done(self.TOOL_NAME, tenant=tenant, case_id=case_id,
                           trace_id=trace_id, endpoint="mapping",
                           status=evidence.evidence_available.value)
        return evidence.to_dict()


# ── GetCaseHistoryTool (production) ────────────────────────────────────────

class GetCaseHistoryTool(BaseTool):
    """
    Production replacement for the Sprint 2.17 mock.

    Input: {"phone_number": "<10-digit>"} (required)

    Returns aggregated recent-sessions summary for the customer.
    """

    TOOL_NAME = "GetCaseHistoryTool"

    def __init__(
        self, config: UnityConfig | None = None, *, client_factory=None,
    ) -> None:
        self._config = config or UnityConfig.from_env()
        self._client_factory = client_factory

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_name=self.TOOL_NAME,
            description=(
                "Retrieve recent VKYC session history for a customer from "
                "Unity Admin Portal. Aggregates status counts and repeat topics."
            ),
            required_inputs=("phone_number",),
            output_schema={
                "tool_name":       "str",
                "recent_summary":  "dict[case_count, recent_cases, repeat_topic, escalation_rate]",
                "all_session_count": "int",
            },
            version="2.0.0",
            tags=("unity", "case_history", "read_only"),
            provider=ToolProvider.UNITY,
            capability=ToolCapability.READ,
        )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        phone = str(inputs.get("phone_number") or "").strip()
        tenant  = str(inputs.get("tenant_id") or self._config.domain)
        case_id = str(inputs.get("case_id") or "")
        trace_id = str(inputs.get("trace_id") or "")

        if not self._config.enabled:
            return build_unity_evidence(
                tool_name=self.TOOL_NAME,
                availability=EvidenceAvailability.DISABLED,
                error="unity integration disabled by configuration",
                error_code="DISABLED",
                tenant_id=tenant, trace_id=trace_id, case_id=case_id,
                query_phone_number=phone,
                recent_summary=derive_recent_summary((), phone),
            ).to_dict()

        if not phone:
            return build_unity_evidence(
                tool_name=self.TOOL_NAME,
                availability=EvidenceAvailability.UNAVAILABLE,
                error="phone_number input missing",
                error_code="MISSING_INPUT",
                tenant_id=tenant, trace_id=trace_id, case_id=case_id,
                recent_summary=derive_recent_summary((), ""),
            ).to_dict()

        session_list, err, err_code = _run_async(
            _fetch_session_list(self._config, phone,
                                client_factory=self._client_factory)
        )
        _emit_mapping(self.TOOL_NAME, tenant=tenant, case_id=case_id,
                      trace_id=trace_id, endpoint="mapping", status="STARTED")

        sessions = session_list.sessions if session_list is not None else ()
        count = session_list.session_count if session_list is not None else 0
        evidence = build_unity_evidence(
            tool_name=self.TOOL_NAME,
            all_sessions=sessions,
            all_session_count=count,
            availability=_availability_for(self._config, err_code),
            error=err, error_code=err_code,
            tenant_id=tenant, trace_id=trace_id, case_id=case_id,
            query_phone_number=phone,
            recent_summary=derive_recent_summary(sessions, phone),
        )
        _emit_mapping_done(self.TOOL_NAME, tenant=tenant, case_id=case_id,
                           trace_id=trace_id, endpoint="mapping",
                           status=evidence.evidence_available.value)
        return evidence.to_dict()


# ── GetOnboardingStatusTool (production) ───────────────────────────────────

class GetOnboardingStatusTool(BaseTool):
    """
    Production replacement for the Sprint 2.17 mock.

    Input: {"application_id": "<10-digit phone_number>"} (required)
    (In Unity's model, there is NO URN — the primary customer identifier
    is `phone_number`. Application ID maps to phone_number.)

    Returns inferred onboarding stage from the most-relevant session's status.
    """

    TOOL_NAME = "GetOnboardingStatusTool"

    def __init__(
        self, config: UnityConfig | None = None, *, client_factory=None,
    ) -> None:
        self._config = config or UnityConfig.from_env()
        self._client_factory = client_factory

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_name=self.TOOL_NAME,
            description=(
                "Derive KwikID onboarding stage for a customer from Unity Admin "
                "Portal session data. Maps VKYC session_status to onboarding stage."
            ),
            required_inputs=("application_id",),
            output_schema={
                "tool_name":        "str",
                "onboarding_stage": "COMPLETE | KYC_REJECTED | VKYC | VKYC_EXPIRED | VKYC_ABANDONED | VKYC_PARTIAL | REGISTRATION",
                "session":          "UnitySession dict | null",
            },
            version="2.0.0",
            tags=("unity", "onboarding", "kyc", "read_only"),
            provider=ToolProvider.UNITY,
            capability=ToolCapability.READ,
        )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        # application_id treated as phone_number per SOT investigation_mapping §1
        phone = str(inputs.get("application_id") or inputs.get("phone_number") or "").strip()
        tenant  = str(inputs.get("tenant_id") or self._config.domain)
        case_id = str(inputs.get("case_id") or "")
        trace_id = str(inputs.get("trace_id") or "")
        ticket_created_at = inputs.get("ticket_created_at")

        if not self._config.enabled:
            return build_unity_evidence(
                tool_name=self.TOOL_NAME,
                availability=EvidenceAvailability.DISABLED,
                error="unity integration disabled by configuration",
                error_code="DISABLED",
                tenant_id=tenant, trace_id=trace_id, case_id=case_id,
                query_phone_number=phone,
                onboarding_stage=derive_onboarding_stage(None),
            ).to_dict()

        if not phone:
            return build_unity_evidence(
                tool_name=self.TOOL_NAME,
                availability=EvidenceAvailability.UNAVAILABLE,
                error="application_id input missing",
                error_code="MISSING_INPUT",
                tenant_id=tenant, trace_id=trace_id, case_id=case_id,
                onboarding_stage="REGISTRATION",
            ).to_dict()

        session_list, err, err_code = _run_async(
            _fetch_session_list(self._config, phone,
                                client_factory=self._client_factory)
        )
        selected = None
        count = 0
        if session_list is not None:
            selected = select_most_relevant_session(
                session_list.sessions, ticket_created_at=ticket_created_at,
            )
            count = session_list.session_count

        _emit_mapping(self.TOOL_NAME, tenant=tenant, case_id=case_id,
                      trace_id=trace_id, endpoint="mapping", status="STARTED")
        evidence = build_unity_evidence(
            tool_name=self.TOOL_NAME,
            session=selected,
            session_found=selected is not None,
            all_session_count=count,
            availability=_availability_for(self._config, err_code),
            error=err, error_code=err_code,
            tenant_id=tenant, trace_id=trace_id, case_id=case_id,
            query_phone_number=phone,
            onboarding_stage=derive_onboarding_stage(selected),
        )
        _emit_mapping_done(self.TOOL_NAME, tenant=tenant, case_id=case_id,
                           trace_id=trace_id, endpoint="mapping",
                           status=evidence.evidence_available.value)
        return evidence.to_dict()


# ── Registrar ────────────────────────────────────────────────────────────────

def register_unity_tools(
    registry,
    *,
    config: UnityConfig | None = None,
    client_factory=None,
) -> dict[str, str]:
    """
    Register the five PRODUCTION Unity tools into a registry.

    Works with both Sprint 2.17 `ToolRegistry` (`.register(tool)`) and
    Sprint 2.45 `ProductionToolRegistry` (`.register(tool)` + optional
    `.register_capability(evidence_kind, tool_name)`).

    Returns a `{tool_name: outcome}` dict for auditability.
    """
    outcomes: dict[str, str] = {}
    for cls in (
        GetSessionDetailsTool,
        GetUserDetailsTool,
        GetFailureReasonTool,
        GetCaseHistoryTool,
        GetOnboardingStatusTool,
    ):
        tool = cls(config=config, client_factory=client_factory)
        try:
            registry.register(tool)
            outcomes[tool.TOOL_NAME] = "registered"
        except Exception as exc:
            outcomes[tool.TOOL_NAME] = f"error:{exc}"

    if hasattr(registry, "register_capability"):
        for kind, tool_name in (
            ("SESSION", "GetSessionDetailsTool"),
            ("USER",    "GetUserDetailsTool"),
            ("LOG",     "GetFailureReasonTool"),
            ("SUMMARY", "GetCaseHistoryTool"),
            ("SUMMARY", "GetOnboardingStatusTool"),
        ):
            try:
                registry.register_capability(kind, tool_name)
            except Exception as exc:
                LOGGER.debug("unity_tools.register_capability warn=%s", exc)
    return outcomes
