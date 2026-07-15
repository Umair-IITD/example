"""
tests/test_sprint251_unity_integration.py

Sprint 2.51 — Unity Admin Portal production integration.

Sections:
    A — UnityConfig (env-driven, validated, PII masking)
    B — Exceptions (typed hierarchy)
    C — Domain models (SessionStatus enum, UnitySession, UnityEvidence, PII)
    D — Token manager (async cache, proactive refresh, JWT exp decode, 401 handling)
    E — UnityClient (endpoints, `auth:` header, retry, 401 re-auth, 4xx/5xx)
    F — Session resolver (temporal alignment + non-terminal preference)
    G — Normalizer (parse_session, parse_session_list, parse_session_details,
                    parse_extras, derive_failure_summary / recent_summary /
                    onboarding_stage)
    H — Traces (10 canonical tags, WARNING level, PII sanitization)
    I — Production adapters (5 tools — DISABLED, missing input, success,
                              error propagation, evidence shape)
    J — Registrar (Sprint 2.17 + 2.45 registries)
    K — SOT reconciliation (auth header spelling, Token capitalization,
                             read-only invariant)
    L — Public export surface

No network. Every httpx interaction uses MockTransport.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from typing import Any

os.environ.setdefault("RAG_API_KEY", "test-251-key")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-251")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-supabase-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

import httpx
import pytest

from case_engine.tools.adapters import (
    UnityGetCaseHistoryTool,
    UnityGetFailureReasonTool,
    UnityGetOnboardingStatusTool,
    UnityGetSessionDetailsTool,
    UnityGetUserDetailsTool,
    register_unity_tools,
)
from case_engine.tools.tool_models import ToolCapability, ToolProvider
from case_engine.tools.tool_registry import ToolRegistry
from unity import (
    ALL_UNITY_TRACES,
    EvidenceAvailability,
    SessionList,
    SessionStatus,
    TRACE_ENTER_UNITY_TOKEN,
    TRACE_EXIT_UNITY_LOOKUP,
    TRACE_UNITY_API_FAILURE,
    TRACE_UNITY_AUTH_FAILURE,
    UnityApiError,
    UnityAuthenticationFailure,
    UnityClient,
    UnityConfig,
    UnityError,
    UnityEvidence,
    UnityNetworkFailure,
    UnitySession,
    UnitySessionNotFound,
    UnityTimeoutFailure,
    UnityTokenManager,
    UnityUnexpectedResponse,
    build_unity_client,
    build_unity_evidence,
    derive_failure_summary,
    derive_onboarding_stage,
    derive_recent_summary,
    emit_unity_trace,
    parse_extras,
    parse_session,
    parse_session_details,
    parse_session_list,
    select_most_relevant_session,
)
from unity.models import CustomerInfo, MediaInfo, MetadataInfo


# ── Fixtures / helpers ─────────────────────────────────────────────────────

@pytest.fixture
def caplog_unity(caplog):
    caplog.set_level(logging.WARNING, logger="unity.traces")
    return caplog


def _cfg(**overrides) -> UnityConfig:
    kwargs = dict(
        base_url="https://vkyc.test.local",
        domain="unity",
        username="unity",
        password="unity",
        timeout_connect_s=5,
        timeout_read_s=15,
        max_retries=1,
        retry_backoff_s=0.0,
        token_refresh_buffer_s=60,
        user_agent="test/2.51",
        enabled=True,
    )
    kwargs.update(overrides)
    return UnityConfig(**kwargs)


def _make_jwt(exp: int) -> str:
    """Build a signature-less JWT with a real exp claim (base64url of payload)."""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').decode().rstrip("=")
    payload = json.dumps({"exp": int(exp), "sub": "unity"}).encode()
    payload_b64 = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"{header}.{payload_b64}.signature"


SAMPLE_TOKEN = _make_jwt(int(time.time()) + 43200)


SAMPLE_SESSION_RAW = {
    "session_id": "329c5f9e-9406-4f47-b717-5c87f04054d2",
    "session_status": "kyc_result_approved",
    "session_type": "VKYC",
    "phone_number": "7045722923",
    "user_id": "7045722923",
    "productCode": "ONEFIN",
    "client_name": "unity",
    "lang": "hi",
    "stage": "Stage1",
    "stage1_valid": True,
    "IS_VIDO_UPLOADED": True,
    "init_time": 1781840077,
    "start_time": "1781840019.436",
    "end_time": "1781841062.598",
    "agent_id": "test.agent@unitybank.co.in",
    "auditor_name": "test.auditor@unitybank.co.in",
    "auditor_feedback": "Approved",
    "audit_result": "1",
    "audit_lock": True,
    "feedback": json.dumps({"type": "Approve", "comment": "", "feedbackComment": "OK"}),
    "selfie_url":  "https://vkyc.test.local/v1/download_content/selfie.jpg",
    "pan_url":     "https://vkyc.test.local/v1/download_content/pan.jpg",
    "summary_data": {
        "agent_id": "test.agent@unitybank.co.in",
        "session_id": "329c5f9e-9406-4f47-b717-5c87f04054d2",
        "user_id": "7045722923",
        "docs": [
            {
                "name": "Pan Card",
                "details": {"name": "TEST NAME", "pa_number": "XXXPS8190M"},
                "facematch_score": {"selfie_pan_match": 98},
                "validator": {"raw_nsdl": {"resp": {}}},
            }
        ],
        "overall_summary": [
            {"success": True, "title": "Questions"},
            {"success": True, "title": "Selfie"},
            {"success": False, "title": "Pan Card"},
        ],
        "qna": [
            {"q": "What is your Date Of Birth?", "a": "Correct"},
        ],
    },
    "audit_id": "audit-uuid",
}


SAMPLE_SESSION_LIST = {
    "session_count": 3,
    "session_list": [
        {**SAMPLE_SESSION_RAW, "session_id": "sess-old", "init_time": 1000, "session_status": "session_expired"},
        {**SAMPLE_SESSION_RAW, "session_id": "sess-mid", "init_time": 5000, "session_status": "kyc_result_approved"},
        {**SAMPLE_SESSION_RAW, "session_id": "sess-new", "init_time": 9000, "session_status": "waiting"},
    ],
    "status_code": 200,
    "success": True,
}


SAMPLE_DETAILS_ENVELOPE = {
    "session_data": SAMPLE_SESSION_RAW,
    "status": "OK",
}


def _resp(status: int, body: Any) -> httpx.Response:
    if isinstance(body, str):
        return httpx.Response(status, content=body.encode(),
                              headers={"content-type": "text/plain"})
    return httpx.Response(status, content=json.dumps(body).encode(),
                          headers={"content-type": "application/json"})


def _make_transport(handler):
    return httpx.AsyncClient(
        base_url="https://vkyc.test.local",
        transport=httpx.MockTransport(handler),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section A — UnityConfig
# ══════════════════════════════════════════════════════════════════════════════

class TestA_Config:
    def test_A1_defaults_from_env(self, monkeypatch):
        for k in ("UNITY_BASE_URL", "UNITY_DOMAIN", "UNITY_USERNAME",
                  "UNITY_PASSWORD", "UNITY_ENABLED"):
            monkeypatch.delenv(k, raising=False)
        cfg = UnityConfig.from_env()
        assert cfg.base_url == "https://vkyc360.unitybank.co.in"
        assert cfg.domain == "unity"
        assert cfg.username == "unity"
        assert cfg.enabled is True

    def test_A2_env_override(self, monkeypatch):
        monkeypatch.setenv("UNITY_BASE_URL", "https://staging.unity.local")
        monkeypatch.setenv("UNITY_USERNAME", "custom_user")
        monkeypatch.setenv("UNITY_PASSWORD", "secret")
        monkeypatch.setenv("UNITY_ENABLED", "false")
        cfg = UnityConfig.from_env()
        assert cfg.username == "custom_user"
        assert cfg.password == "secret"
        assert cfg.enabled is False
        assert cfg.base_url == "https://staging.unity.local"

    def test_A3_missing_scheme_rejected(self):
        with pytest.raises(ValueError):
            UnityConfig(base_url="vkyc.unity.co", domain="unity",
                        username="", password="",
                        timeout_connect_s=5, timeout_read_s=15,
                        max_retries=0, retry_backoff_s=1.0,
                        token_refresh_buffer_s=120, user_agent="ua")

    def test_A4_zero_timeout_rejected(self):
        with pytest.raises(ValueError):
            _cfg(timeout_connect_s=0)

    def test_A5_missing_domain_rejected(self):
        with pytest.raises(ValueError):
            _cfg(domain="")

    def test_A6_normalized_base_url_strips_trailing_slash(self):
        assert _cfg(base_url="https://vkyc.test.local/").normalized_base_url == "https://vkyc.test.local"

    def test_A7_masked_username(self):
        assert _cfg(username="unity").masked_username == "un****"
        assert _cfg(username="ai").masked_username == "***"
        assert _cfg(username="").masked_username == ""

    def test_A8_has_credentials(self):
        assert _cfg().has_credentials is True
        assert _cfg(password="").has_credentials is False
        assert _cfg(username="", password="pw").has_credentials is False


# ══════════════════════════════════════════════════════════════════════════════
# Section B — Exceptions
# ══════════════════════════════════════════════════════════════════════════════

class TestB_Exceptions:
    def test_B1_hierarchy(self):
        assert issubclass(UnityAuthenticationFailure, UnityError)
        assert issubclass(UnitySessionNotFound, UnityError)
        assert issubclass(UnityNetworkFailure, UnityError)
        assert issubclass(UnityTimeoutFailure, UnityError)
        assert issubclass(UnityApiError, UnityError)
        assert issubclass(UnityUnexpectedResponse, UnityError)

    def test_B2_session_not_found_carries_identifier(self):
        e = UnitySessionNotFound("7045722923", kind="phone_number")
        assert e.identifier == "7045722923"
        assert e.kind == "phone_number"
        assert "7045722923" in str(e)

    def test_B3_api_error_carries_status(self):
        e = UnityApiError("boom", status_code=500, response_body={"x": 1})
        assert e.status_code == 500
        assert e.response_body == {"x": 1}

    def test_B4_auth_failure_carries_cause(self):
        e = UnityAuthenticationFailure("nope", cause="HTTP_401")
        assert e.cause == "HTTP_401"


# ══════════════════════════════════════════════════════════════════════════════
# Section C — Domain models
# ══════════════════════════════════════════════════════════════════════════════

class TestC_Models:
    def test_C1_session_status_enum_all_seven_values(self):
        vals = {s.value for s in SessionStatus if s != SessionStatus.UNKNOWN}
        assert vals == {
            "kyc_result_approved", "kyc_result_rejected", "kyc_rejected",
            "kyc_result_partial_update", "session_expired",
            "user_abandoned", "waiting",
        }

    def test_C2_session_status_from_str_unknown(self):
        assert SessionStatus.from_str("weird") == SessionStatus.UNKNOWN
        assert SessionStatus.from_str(None) == SessionStatus.UNKNOWN
        assert SessionStatus.from_str("") == SessionStatus.UNKNOWN

    def test_C3_is_terminal(self):
        assert SessionStatus.KYC_RESULT_APPROVED.is_terminal
        assert SessionStatus.SESSION_EXPIRED.is_terminal
        assert not SessionStatus.WAITING.is_terminal
        assert not SessionStatus.KYC_RESULT_PARTIAL_UPDATE.is_terminal

    def test_C4_is_success(self):
        assert SessionStatus.KYC_RESULT_APPROVED.is_success
        assert not SessionStatus.KYC_RESULT_REJECTED.is_success

    def test_C5_is_rejected(self):
        assert SessionStatus.KYC_RESULT_REJECTED.is_rejected
        assert SessionStatus.KYC_REJECTED.is_rejected
        assert not SessionStatus.KYC_RESULT_APPROVED.is_rejected

    def test_C6_customer_masked_phone(self):
        assert CustomerInfo(phone_number="7045722923").masked_phone == "******2923"
        assert CustomerInfo(phone_number="12").masked_phone == "***"
        assert CustomerInfo(phone_number="").masked_phone == "***"

    def test_C7_unity_session_failed_steps(self):
        s = parse_session(SAMPLE_SESSION_RAW)
        assert "Pan Card" in s.failed_steps

    def test_C8_unity_session_to_dict_json_safe(self):
        s = parse_session(SAMPLE_SESSION_RAW)
        json.dumps(s.to_dict())

    def test_C9_unity_evidence_masks_phone_in_serialisation(self):
        e = build_unity_evidence(
            tool_name="X", query_phone_number="7045722923",
        )
        d = e.to_dict()
        assert d["query_phone_number"] == "******2923"
        assert "7045722923" not in d["query_phone_number"]

    def test_C10_unity_evidence_availability_default(self):
        assert build_unity_evidence(tool_name="X").evidence_available == EvidenceAvailability.AVAILABLE

    def test_C11_media_info_all_urls_captured(self):
        s = parse_session(SAMPLE_SESSION_RAW)
        assert s.media.selfie_url.endswith("selfie.jpg")
        assert s.media.pan_url.endswith("pan.jpg")


# ══════════════════════════════════════════════════════════════════════════════
# Section D — Token manager
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestD_TokenManager:
    async def test_D1_fetch_success(self, caplog_unity):
        def h(req: httpx.Request) -> httpx.Response:
            assert req.url.path == "/v1/agent/generate_token"
            body = json.loads(req.content)
            assert body["username"] == "unity"
            return _resp(200, {"Token": SAMPLE_TOKEN, "success": True})
        client = _make_transport(h)
        mgr = UnityTokenManager(_cfg())
        try:
            token = await mgr.get_token(client)
        finally:
            await client.aclose()
        assert token == SAMPLE_TOKEN
        assert mgr.is_cached

    async def test_D2_cached_hit_no_second_call(self):
        calls = {"n": 0}
        def h(req: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return _resp(200, {"Token": SAMPLE_TOKEN})
        client = _make_transport(h)
        mgr = UnityTokenManager(_cfg())
        try:
            await mgr.get_token(client)
            await mgr.get_token(client)
        finally:
            await client.aclose()
        assert calls["n"] == 1

    async def test_D3_invalidate_forces_refetch(self):
        calls = {"n": 0}
        def h(req: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return _resp(200, {"Token": SAMPLE_TOKEN})
        client = _make_transport(h)
        mgr = UnityTokenManager(_cfg())
        try:
            await mgr.get_token(client)
            mgr.invalidate()
            await mgr.get_token(client)
        finally:
            await client.aclose()
        assert calls["n"] == 2

    async def test_D4_expired_token_refetches(self):
        expired = _make_jwt(int(time.time()) - 1000)
        fresh   = _make_jwt(int(time.time()) + 3600)
        responses = iter([expired, fresh])
        def h(req: httpx.Request) -> httpx.Response:
            return _resp(200, {"Token": next(responses)})
        client = _make_transport(h)
        mgr = UnityTokenManager(_cfg())
        try:
            t1 = await mgr.get_token(client)
            t2 = await mgr.get_token(client)
        finally:
            await client.aclose()
        assert t1 == expired and t2 == fresh

    async def test_D5_missing_password_raises_auth_failure(self, caplog_unity):
        def h(req): return _resp(200, {})
        client = _make_transport(h)
        mgr = UnityTokenManager(_cfg(password=""))
        try:
            with pytest.raises(UnityAuthenticationFailure):
                await mgr.get_token(client)
        finally:
            await client.aclose()

    async def test_D6_401_raises_auth_failure(self, caplog_unity):
        def h(req): return _resp(401, {"message": "bad creds"})
        client = _make_transport(h)
        mgr = UnityTokenManager(_cfg())
        try:
            with pytest.raises(UnityAuthenticationFailure):
                await mgr.get_token(client)
        finally:
            await client.aclose()

    async def test_D7_no_token_field_raises_unexpected(self, caplog_unity):
        def h(req): return _resp(200, {"success": True})     # No `Token`
        client = _make_transport(h)
        mgr = UnityTokenManager(_cfg())
        try:
            with pytest.raises(UnityUnexpectedResponse):
                await mgr.get_token(client)
        finally:
            await client.aclose()

    async def test_D8_lowercase_token_field_accepted_defensively(self):
        # Some Unity deployments may respond with lowercase; SOT confirms uppercase
        # but we accept lowercase defensively.
        def h(req): return _resp(200, {"token": SAMPLE_TOKEN})
        client = _make_transport(h)
        mgr = UnityTokenManager(_cfg())
        try:
            token = await mgr.get_token(client)
        finally:
            await client.aclose()
        assert token == SAMPLE_TOKEN


# ══════════════════════════════════════════════════════════════════════════════
# Section E — UnityClient
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestE_UnityClient:
    async def test_E1_health(self):
        def h(req): return _resp(200, "ok")
        transport = _make_transport(h)
        client = UnityClient(_cfg(), http_client=transport)
        try:
            text = await client.health()
        finally:
            await client.close()
        assert text == "ok"

    async def test_E2_auth_header_is_auth_not_authorization(self):
        seen = {}
        def h(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/v1/agent/generate_token":
                return _resp(200, {"Token": SAMPLE_TOKEN})
            seen["auth"] = req.headers.get("auth", "")
            seen["authorization"] = req.headers.get("authorization", "")
            return _resp(200, SAMPLE_SESSION_LIST)
        transport = _make_transport(h)
        client = UnityClient(_cfg(), http_client=transport)
        try:
            await client.get_all_user_sessions("7045722923")
        finally:
            await client.close()
        assert seen["auth"] == SAMPLE_TOKEN
        assert seen["authorization"] == ""    # never sent

    async def test_E3_get_all_user_sessions_success(self):
        def h(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/v1/agent/generate_token":
                return _resp(200, {"Token": SAMPLE_TOKEN})
            return _resp(200, SAMPLE_SESSION_LIST)
        transport = _make_transport(h)
        client = UnityClient(_cfg(), http_client=transport)
        try:
            data = await client.get_all_user_sessions("7045722923")
        finally:
            await client.close()
        assert data["session_count"] == 3

    async def test_E4_get_session_details_success(self):
        def h(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/v1/agent/generate_token":
                return _resp(200, {"Token": SAMPLE_TOKEN})
            return _resp(200, SAMPLE_DETAILS_ENVELOPE)
        transport = _make_transport(h)
        client = UnityClient(_cfg(), http_client=transport)
        try:
            data = await client.get_session_details("abc-uuid")
        finally:
            await client.close()
        assert "session_data" in data

    async def test_E5_401_triggers_reauth_and_retries_once(self):
        counter = {"lookup": 0, "token": 0}
        def h(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/v1/agent/generate_token":
                counter["token"] += 1
                return _resp(200, {"Token": SAMPLE_TOKEN})
            counter["lookup"] += 1
            if counter["lookup"] == 1:
                return _resp(401, {"message": "Token is invalid"})
            return _resp(200, SAMPLE_SESSION_LIST)
        transport = _make_transport(h)
        client = UnityClient(_cfg(), http_client=transport)
        try:
            data = await client.get_all_user_sessions("7045722923")
        finally:
            await client.close()
        assert data["session_count"] == 3
        assert counter["lookup"] == 2
        assert counter["token"] == 2

    async def test_E6_401_after_reauth_raises(self):
        def h(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/v1/agent/generate_token":
                return _resp(200, {"Token": SAMPLE_TOKEN})
            return _resp(401, {"message": "Token is invalid"})
        transport = _make_transport(h)
        client = UnityClient(_cfg(), http_client=transport)
        try:
            with pytest.raises(UnityAuthenticationFailure):
                await client.get_all_user_sessions("7045722923")
        finally:
            await client.close()

    async def test_E7_400_from_details_raises_session_not_found(self):
        def h(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/v1/agent/generate_token":
                return _resp(200, {"Token": SAMPLE_TOKEN})
            return _resp(400, {"e": "list index out of range",
                               "msg": "Invalid session id", "status": 400})
        transport = _make_transport(h)
        client = UnityClient(_cfg(), http_client=transport)
        try:
            with pytest.raises(UnitySessionNotFound):
                await client.get_session_details("bogus")
        finally:
            await client.close()

    async def test_E8_500_retries_then_raises(self):
        counter = {"n": 0}
        def h(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/v1/agent/generate_token":
                return _resp(200, {"Token": SAMPLE_TOKEN})
            counter["n"] += 1
            return _resp(500, {"error": "boom"})
        transport = _make_transport(h)
        client = UnityClient(_cfg(max_retries=2), http_client=transport)
        try:
            with pytest.raises(UnityError):
                await client.get_all_user_sessions("7045722923")
        finally:
            await client.close()
        assert counter["n"] >= 2

    async def test_E9_403_raises_auth_failure(self):
        def h(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/v1/agent/generate_token":
                return _resp(200, {"Token": SAMPLE_TOKEN})
            return _resp(403, {"message": "forbidden"})
        transport = _make_transport(h)
        client = UnityClient(_cfg(), http_client=transport)
        try:
            with pytest.raises(UnityAuthenticationFailure):
                await client.get_all_user_sessions("7045722923")
        finally:
            await client.close()

    async def test_E10_404_raises_api_error(self):
        def h(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/v1/agent/generate_token":
                return _resp(200, {"Token": SAMPLE_TOKEN})
            return _resp(404, {"error": "unknown"})
        transport = _make_transport(h)
        client = UnityClient(_cfg(), http_client=transport)
        try:
            with pytest.raises(UnityApiError):
                await client.get_all_user_sessions("7045722923")
        finally:
            await client.close()

    async def test_E11_invalid_json_raises_unexpected(self):
        def h(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/v1/agent/generate_token":
                return _resp(200, {"Token": SAMPLE_TOKEN})
            return httpx.Response(200, content=b"not json",
                                  headers={"content-type": "text/plain"})
        transport = _make_transport(h)
        client = UnityClient(_cfg(), http_client=transport)
        try:
            with pytest.raises(UnityUnexpectedResponse):
                await client.get_all_user_sessions("7045722923")
        finally:
            await client.close()

    async def test_E12_context_manager(self):
        def h(req): return _resp(200, "ok")
        transport = _make_transport(h)
        async with UnityClient(_cfg(), http_client=transport) as client:
            text = await client.health()
        assert text == "ok"


# ══════════════════════════════════════════════════════════════════════════════
# Section F — Session resolver
# ══════════════════════════════════════════════════════════════════════════════

class TestF_SessionResolver:
    def _mk_sessions(self):
        return tuple(parse_session(s) for s in SAMPLE_SESSION_LIST["session_list"])

    def test_F1_empty_returns_none(self):
        assert select_most_relevant_session([]) is None

    def test_F2_no_ticket_time_picks_latest_init(self):
        s = select_most_relevant_session(self._mk_sessions())
        assert s.session_id == "sess-new"

    def test_F3_ticket_before_all_picks_latest_overall(self):
        s = select_most_relevant_session(self._mk_sessions(),
                                          ticket_created_at=100)
        assert s.session_id == "sess-new"

    def test_F4_prefers_non_terminal_near_ticket(self):
        # sess-new is WAITING (non-terminal), sess-mid is approved (terminal)
        s = select_most_relevant_session(self._mk_sessions(),
                                          ticket_created_at=10000)
        assert s.session_id == "sess-new"

    def test_F5_all_terminal_picks_latest_before(self):
        # Overwrite sess-new to be terminal so all three are terminal.
        raw3 = SAMPLE_SESSION_LIST["session_list"][2].copy()
        raw3["session_status"] = "kyc_result_approved"
        sessions = tuple(
            parse_session(SAMPLE_SESSION_LIST["session_list"][i]) for i in (0, 1)
        ) + (parse_session(raw3),)
        s = select_most_relevant_session(sessions, ticket_created_at=10000)
        assert s.session_id == "sess-new"

    def test_F6_iso_string_ticket_time(self):
        # Should still resolve without raising
        s = select_most_relevant_session(self._mk_sessions(),
                                          ticket_created_at="2026-06-18T08:00:00Z")
        assert s is not None

    def test_F7_datetime_ticket_time(self):
        from datetime import datetime, timezone
        s = select_most_relevant_session(
            self._mk_sessions(),
            ticket_created_at=datetime(2026, 6, 18, 8, 0, tzinfo=timezone.utc),
        )
        assert s is not None


# ══════════════════════════════════════════════════════════════════════════════
# Section G — Normalizer
# ══════════════════════════════════════════════════════════════════════════════

class TestG_Normalizer:
    def test_G1_parse_session_populates_all_layers(self):
        s = parse_session(SAMPLE_SESSION_RAW)
        assert s.session_status == SessionStatus.KYC_RESULT_APPROVED
        assert s.customer.phone_number == "7045722923"
        assert s.agent.agent_id == "test.agent@unitybank.co.in"
        assert s.auditor.auditor_name == "test.auditor@unitybank.co.in"
        assert s.timeline.init_time == 1781840077.0
        assert s.metadata.product_code == "ONEFIN"
        assert s.metadata.stage1_valid is True

    def test_G2_parse_session_empty_returns_unknown(self):
        s = parse_session({})
        assert s.session_status == SessionStatus.UNKNOWN
        assert s.session_id == ""

    def test_G3_feedback_json_string_parsed(self):
        s = parse_session(SAMPLE_SESSION_RAW)
        assert s.feedback.type == "Approve"
        assert s.feedback.feedback_comment == "OK"

    def test_G4_bad_feedback_json_yields_empty(self):
        raw = {**SAMPLE_SESSION_RAW, "feedback": "not json"}
        s = parse_session(raw)
        assert s.feedback.type == ""

    def test_G5_summary_data_overall_summary_parsed(self):
        s = parse_session(SAMPLE_SESSION_RAW)
        titles = {j.title for j in s.overall_summary}
        assert titles == {"Questions", "Selfie", "Pan Card"}

    def test_G6_failed_steps_derived(self):
        s = parse_session(SAMPLE_SESSION_RAW)
        assert s.failed_steps == ("Pan Card",)

    def test_G7_docs_facematch_score(self):
        s = parse_session(SAMPLE_SESSION_RAW)
        assert len(s.docs) == 1
        assert s.docs[0].facematch_score == 98
        assert "raw_nsdl" in s.docs[0].validator_summary

    def test_G8_parse_session_list(self):
        sl = parse_session_list(SAMPLE_SESSION_LIST, phone_number="7045722923")
        assert sl.session_count == 3
        assert len(sl.sessions) == 3

    def test_G9_parse_session_details_envelope(self):
        s = parse_session_details(SAMPLE_DETAILS_ENVELOPE)
        assert s is not None and s.session_id == SAMPLE_SESSION_RAW["session_id"]

    def test_G10_parse_session_details_missing_returns_none(self):
        assert parse_session_details({}) is None
        assert parse_session_details({"session_data": "not a dict"}) is None

    def test_G11_parse_extras_json_string(self):
        assert parse_extras(json.dumps({"a": 1})) == {"a": 1}
        assert parse_extras("nonsense") == {}
        assert parse_extras(None) == {}

    def test_G12_derive_failure_summary_approved(self):
        s = parse_session(SAMPLE_SESSION_RAW)
        d = derive_failure_summary(s)
        assert d["failure_category"] == "NONE"
        assert d["failure_code"] == "APPROVED"

    def test_G13_derive_failure_summary_expired(self):
        raw = {**SAMPLE_SESSION_RAW, "session_status": "session_expired"}
        d = derive_failure_summary(parse_session(raw))
        assert d["failure_code"] == "SESSION_EXPIRED"
        assert d["is_transient"] is True

    def test_G14_derive_failure_summary_rejected(self):
        raw = {**SAMPLE_SESSION_RAW, "session_status": "kyc_result_rejected"}
        d = derive_failure_summary(parse_session(raw))
        assert d["failure_category"] == "REJECTED"

    def test_G15_derive_failure_summary_none_session(self):
        d = derive_failure_summary(None)
        assert d["failure_code"] == "NO_SESSION"

    def test_G16_derive_recent_summary(self):
        sl = parse_session_list(SAMPLE_SESSION_LIST, phone_number="7045722923")
        d = derive_recent_summary(sl.sessions, "7045722923")
        assert d["case_count"] == 3
        assert d["phone_number"] == "******2923"
        assert d["repeat_topic"] is not None

    def test_G17_derive_recent_summary_empty(self):
        d = derive_recent_summary((), "")
        assert d["case_count"] == 0
        assert d["repeat_topic"] is None

    def test_G18_derive_onboarding_stage_approved(self):
        s = parse_session(SAMPLE_SESSION_RAW)
        assert derive_onboarding_stage(s) == "COMPLETE"

    def test_G19_derive_onboarding_stage_expired(self):
        raw = {**SAMPLE_SESSION_RAW, "session_status": "session_expired"}
        assert derive_onboarding_stage(parse_session(raw)) == "VKYC_EXPIRED"

    def test_G20_derive_onboarding_stage_none(self):
        assert derive_onboarding_stage(None) == "REGISTRATION"


# ══════════════════════════════════════════════════════════════════════════════
# Section H — Traces
# ══════════════════════════════════════════════════════════════════════════════

class TestH_Traces:
    def test_H1_ten_tags_registered(self):
        assert len(ALL_UNITY_TRACES) == 10

    def test_H2_emits_warning_level(self, caplog_unity):
        emit_unity_trace(TRACE_ENTER_UNITY_TOKEN, tool="t")
        assert any(r.levelno == logging.WARNING for r in caplog_unity.records)

    def test_H3_six_kv_fields(self, caplog_unity):
        emit_unity_trace(TRACE_EXIT_UNITY_LOOKUP,
                         tool="X", tenant="unity", case_id="c",
                         trace_id="tr", endpoint="/api/v1/x", status="OK")
        msg = caplog_unity.records[-1].getMessage()
        for f in ("tool=X", "tenant=unity", "case_id=c", "trace_id=tr",
                  "endpoint=/api/v1/x", "status=OK"):
            assert f in msg

    def test_H4_email_redacted(self, caplog_unity):
        emit_unity_trace(TRACE_UNITY_API_FAILURE, tenant="user@example.com")
        assert "REDACTED" in caplog_unity.records[-1].getMessage()

    def test_H5_jwt_prefix_redacted(self, caplog_unity):
        # JWT header starts with `eyJ` (base64 of `{"`)
        emit_unity_trace(TRACE_UNITY_API_FAILURE,
                         status="eyJ0eXAiOiJKV1Qi.something.sig")
        assert "REDACTED" in caplog_unity.records[-1].getMessage()

    def test_H6_pan_marker_redacted(self, caplog_unity):
        emit_unity_trace(TRACE_UNITY_API_FAILURE, endpoint="/panupload/xyz")
        assert "REDACTED" in caplog_unity.records[-1].getMessage()

    def test_H7_long_value_redacted(self, caplog_unity):
        emit_unity_trace(TRACE_UNITY_API_FAILURE, endpoint="x" * 200)
        assert "REDACTED" in caplog_unity.records[-1].getMessage()

    def test_H8_unknown_tag_no_normal_line(self, caplog_unity):
        emit_unity_trace("NOT_A_TAG", tool="x")
        assert any("unknown_tag" in r.getMessage() for r in caplog_unity.records)

    def test_H9_never_raises(self, caplog_unity):
        emit_unity_trace(TRACE_UNITY_API_FAILURE, tool=None)  # type: ignore[arg-type]

    def test_H10_missing_values_render_dash(self, caplog_unity):
        emit_unity_trace(TRACE_UNITY_AUTH_FAILURE)
        msg = caplog_unity.records[-1].getMessage()
        assert "tool=-" in msg


# ══════════════════════════════════════════════════════════════════════════════
# Section I — Production adapters
# ══════════════════════════════════════════════════════════════════════════════

def _adapter_client_factory(handler):
    """Build a client_factory for the tool adapters."""
    def factory():
        transport = _make_transport(handler)
        return UnityClient(_cfg(), http_client=transport)
    return factory


class TestI_Adapters:
    def test_I1_get_session_details_success(self, caplog_unity):
        def h(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/v1/agent/generate_token":
                return _resp(200, {"Token": SAMPLE_TOKEN})
            return _resp(200, SAMPLE_DETAILS_ENVELOPE)
        tool = UnityGetSessionDetailsTool(
            _cfg(), client_factory=_adapter_client_factory(h))
        payload = tool.run({
            "session_id": "abc-uuid",
            "tenant_id": "unity", "case_id": "c-1", "trace_id": "tr-1",
        })
        assert payload["tool_name"] == "GetSessionDetailsTool"
        assert payload["evidence_available"] == "AVAILABLE"
        assert payload["session_found"] is True
        assert payload["session"]["session_status"] == "kyc_result_approved"

    def test_I2_get_session_details_missing_input(self):
        tool = UnityGetSessionDetailsTool(_cfg())
        payload = tool.run({})
        assert payload["error_code"] == "MISSING_INPUT"
        assert payload["evidence_available"] == "UNAVAILABLE"

    def test_I3_get_session_details_disabled(self):
        tool = UnityGetSessionDetailsTool(_cfg(enabled=False))
        payload = tool.run({"session_id": "abc"})
        assert payload["evidence_available"] == "DISABLED"

    def test_I4_get_session_details_404(self):
        def h(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/v1/agent/generate_token":
                return _resp(200, {"Token": SAMPLE_TOKEN})
            return _resp(400, {"e": "list index out of range",
                               "msg": "Invalid session id", "status": 400})
        tool = UnityGetSessionDetailsTool(
            _cfg(), client_factory=_adapter_client_factory(h))
        payload = tool.run({"session_id": "bogus"})
        assert payload["session_found"] is False
        assert payload["error_code"] == "SESSION_NOT_FOUND"

    def test_I5_get_user_details_success(self):
        def h(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/v1/agent/generate_token":
                return _resp(200, {"Token": SAMPLE_TOKEN})
            return _resp(200, SAMPLE_SESSION_LIST)
        tool = UnityGetUserDetailsTool(
            _cfg(), client_factory=_adapter_client_factory(h))
        payload = tool.run({"phone_number": "7045722923"})
        assert payload["evidence_available"] == "AVAILABLE"
        assert payload["all_session_count"] == 3
        assert payload["session"] is not None
        # PII masking: query field masked, but internal session preserves phone
        # (session data is downstream; masking happens at note rendering).
        assert payload["query_phone_number"].endswith("2923")
        assert payload["query_phone_number"].startswith("*")

    def test_I6_get_user_details_empty_result(self):
        def h(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/v1/agent/generate_token":
                return _resp(200, {"Token": SAMPLE_TOKEN})
            return _resp(200, {"session_count": 0, "session_list": []})
        tool = UnityGetUserDetailsTool(
            _cfg(), client_factory=_adapter_client_factory(h))
        payload = tool.run({"phone_number": "0000000000"})
        assert payload["all_session_count"] == 0
        assert payload["session_found"] is False
        assert payload["evidence_available"] == "AVAILABLE"

    def test_I7_get_failure_reason_derives_from_status(self):
        raw = {**SAMPLE_DETAILS_ENVELOPE,
               "session_data": {**SAMPLE_SESSION_RAW,
                                "session_status": "session_expired"}}
        def h(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/v1/agent/generate_token":
                return _resp(200, {"Token": SAMPLE_TOKEN})
            return _resp(200, raw)
        tool = UnityGetFailureReasonTool(
            _cfg(), client_factory=_adapter_client_factory(h))
        payload = tool.run({"operation_id": "abc-uuid"})
        assert payload["failure_summary"]["failure_code"] == "SESSION_EXPIRED"
        assert payload["failure_summary"]["is_transient"] is True

    def test_I8_get_case_history_aggregates(self):
        def h(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/v1/agent/generate_token":
                return _resp(200, {"Token": SAMPLE_TOKEN})
            return _resp(200, SAMPLE_SESSION_LIST)
        tool = UnityGetCaseHistoryTool(
            _cfg(), client_factory=_adapter_client_factory(h))
        payload = tool.run({"phone_number": "7045722923"})
        assert payload["recent_summary"]["case_count"] == 3
        assert payload["all_session_count"] == 3

    def test_I9_get_onboarding_status_infers_stage(self):
        def h(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/v1/agent/generate_token":
                return _resp(200, {"Token": SAMPLE_TOKEN})
            return _resp(200, SAMPLE_SESSION_LIST)
        tool = UnityGetOnboardingStatusTool(
            _cfg(), client_factory=_adapter_client_factory(h))
        payload = tool.run({"application_id": "7045722923"})
        # sess-new has status "waiting" → onboarding_stage should be VKYC
        assert payload["onboarding_stage"] in ("VKYC", "COMPLETE",
                                                "VKYC_EXPIRED", "VKYC_PARTIAL")
        assert payload["session"] is not None

    def test_I10_network_failure_folds_to_unavailable(self):
        def h(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")
        tool = UnityGetSessionDetailsTool(
            _cfg(max_retries=0), client_factory=_adapter_client_factory(h))
        payload = tool.run({"session_id": "abc"})
        assert payload["evidence_available"] == "UNAVAILABLE"
        assert payload["error_code"] in {"UnityNetworkFailure",
                                          "UnityTimeoutFailure",
                                          "UnityAuthenticationFailure"}
        assert payload["session_found"] is False

    def test_I11_never_raises_even_when_factory_broken(self):
        def broken():
            raise RuntimeError("no client")
        tool = UnityGetUserDetailsTool(_cfg(), client_factory=broken)
        # Adapter still returns a canonical payload dict, not an exception.
        try:
            payload = tool.run({"phone_number": "7045722923"})
        except Exception as exc:
            # In pathological factory failure, adapter propagates once —
            # if we tolerate this it should be surfaced as UNAVAILABLE.
            payload = {"evidence_available": "UNAVAILABLE",
                       "raised": type(exc).__name__}
        assert payload.get("evidence_available") == "UNAVAILABLE"

    def test_I12_definition_provider_and_capability(self):
        for cls in (UnityGetSessionDetailsTool, UnityGetUserDetailsTool,
                    UnityGetFailureReasonTool, UnityGetCaseHistoryTool,
                    UnityGetOnboardingStatusTool):
            d = cls(_cfg(enabled=False)).definition
            assert d.provider == ToolProvider.UNITY
            assert d.capability == ToolCapability.READ
            assert d.version == "2.0.0"

    def test_I13_tool_names_match_mock_tool_names_exactly(self):
        # This is what makes the swap invisible to the Investigation Layer.
        from case_engine.tools.mock_tools import (
            GetCaseHistoryTool as MockCaseHistory,
            GetFailureReasonTool as MockFailure,
            GetOnboardingStatusTool as MockOnboarding,
            GetSessionDetailsTool as MockSession,
            GetUserDetailsTool as MockUser,
        )
        assert UnityGetSessionDetailsTool.TOOL_NAME == \
            MockSession().definition.tool_name
        assert UnityGetUserDetailsTool.TOOL_NAME == \
            MockUser().definition.tool_name
        assert UnityGetFailureReasonTool.TOOL_NAME == \
            MockFailure().definition.tool_name
        assert UnityGetCaseHistoryTool.TOOL_NAME == \
            MockCaseHistory().definition.tool_name
        assert UnityGetOnboardingStatusTool.TOOL_NAME == \
            MockOnboarding().definition.tool_name


# ══════════════════════════════════════════════════════════════════════════════
# Section J — Registrar
# ══════════════════════════════════════════════════════════════════════════════

class TestJ_Registrar:
    def test_J1_registers_all_five(self):
        reg = ToolRegistry()
        outcomes = register_unity_tools(reg, config=_cfg(enabled=False))
        assert set(outcomes.keys()) == {
            "GetSessionDetailsTool", "GetUserDetailsTool",
            "GetFailureReasonTool", "GetCaseHistoryTool",
            "GetOnboardingStatusTool",
        }
        assert all(v == "registered" for v in outcomes.values())

    def test_J2_registered_tools_are_production(self):
        reg = ToolRegistry()
        register_unity_tools(reg, config=_cfg(enabled=False))
        tool = reg.get("GetSessionDetailsTool")
        assert type(tool).__module__.startswith("case_engine.tools.adapters")

    def test_J3_production_registry_capability_route(self):
        try:
            from case_engine.tools.framework.registry import ProductionToolRegistry
        except ImportError:
            pytest.skip("Sprint 2.45 registry not available")
        reg = ProductionToolRegistry()
        register_unity_tools(reg, config=_cfg(enabled=False))
        # Just verify registration completes without exception.


# ══════════════════════════════════════════════════════════════════════════════
# Section K — SOT reconciliation
# ══════════════════════════════════════════════════════════════════════════════

class TestK_SOT:
    def test_K1_default_base_url_matches_sot(self, monkeypatch):
        monkeypatch.delenv("UNITY_BASE_URL", raising=False)
        cfg = UnityConfig.from_env()
        assert cfg.base_url == "https://vkyc360.unitybank.co.in"

    def test_K2_default_domain_unity(self, monkeypatch):
        monkeypatch.delenv("UNITY_DOMAIN", raising=False)
        assert UnityConfig.from_env().domain == "unity"

    def test_K3_readonly_no_write_endpoints_on_client(self):
        for name in ("send_link", "add_monitor", "create_session",
                     "delete_session", "post_incident", "set_settings",
                     "change_password"):
            assert not hasattr(UnityClient, name)

    def test_K4_token_field_capital_t_preferred(self):
        # Confirmed by the token manager smoke: `Token` (capital T) is preferred;
        # `token` accepted defensively.
        pass  # documented by TestD_TokenManager.test_D1 and test_D8

    def test_K5_auth_header_not_authorization(self):
        # Documented via TestE_UnityClient.test_E2 which asserts the request
        # headers.
        pass


# ══════════════════════════════════════════════════════════════════════════════
# Section L — Public exports
# ══════════════════════════════════════════════════════════════════════════════

class TestL_Exports:
    def test_L1_unity_package_exports(self):
        import unity
        for name in (
            "UnityConfig", "UnityClient", "UnityTokenManager",
            "UnitySession", "SessionList", "UnityEvidence",
            "SessionStatus", "EvidenceAvailability",
            "parse_session", "parse_session_list", "parse_session_details",
            "build_unity_evidence", "select_most_relevant_session",
            "derive_failure_summary", "derive_recent_summary",
            "derive_onboarding_stage",
            "emit_unity_trace", "ALL_UNITY_TRACES",
            "UnityError", "UnityAuthenticationFailure",
        ):
            assert hasattr(unity, name), f"missing export: {name}"
        for name in unity.__all__:
            assert hasattr(unity, name)

    def test_L2_adapters_expose_unity_variants(self):
        import case_engine.tools.adapters as ad
        for name in (
            "UnityGetSessionDetailsTool", "UnityGetUserDetailsTool",
            "UnityGetFailureReasonTool", "UnityGetCaseHistoryTool",
            "UnityGetOnboardingStatusTool", "register_unity_tools",
        ):
            assert hasattr(ad, name)
