"""
intelligence/llm_client.py

Wave 3, Part D: Provider-agnostic LLM client.

The `LLMClient` protocol is what every downstream module targets. Two
concrete implementations ship in this module:

  1. `OpenAILLMClient`    — real OpenAI-compatible Chat Completions API.
                            Works with OpenAI, Azure OpenAI, or any
                            OpenAI-shaped endpoint.
  2. `MockLLMClient`       — deterministic canned responses for testing +
                            for the `INTELLIGENCE_LLM_PROVIDER=mock`
                            production dry-run mode.

The runtime NEVER imports `openai` or `anthropic` directly. All access
goes through the protocol.

Dependency direction: llm_client.py → httpx + stdlib + intelligence.{config, exceptions}
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Protocol, runtime_checkable

import httpx

from intelligence.config import IntelligenceConfig
from intelligence.exceptions import (
    IntelligenceConfigError,
    LLMAuthError,
    LLMRateLimitError,
    LLMRequestError,
    LLMServerError,
    LLMTimeoutError,
)

LOGGER = logging.getLogger(__name__)


# ── Protocol ─────────────────────────────────────────────────────────────────

@runtime_checkable
class LLMClient(Protocol):
    """
    The single contract every LLM implementation must satisfy.

    `complete_json(...)` returns the raw string body the LLM produced.
    Parsing / validation happens in `intelligence.reasoning_parser`.
    """

    async def complete_json(
        self, *, system: str, user: str,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> str: ...

    @property
    def model(self) -> str: ...

    async def close(self) -> None: ...


# ── OpenAI-compatible client (real production path) ──────────────────────────

class OpenAILLMClient:
    """
    Minimal, provider-agnostic OpenAI-Chat-Completions client.

    Supports `response_format={"type":"json_object"}` when the config has
    `llm_json_mode=True` (default). Providers that don't understand
    `response_format` will ignore it and the parser still validates.
    """

    def __init__(
        self,
        config: IntelligenceConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not config.has_api_key and not config.is_mock:
            raise IntelligenceConfigError(
                "LLM API key not set — populate INTELLIGENCE_LLM_API_KEY, "
                "OPENAI_CHAT_API_KEY, or OPENAI_API_KEY"
            )
        self._config = config
        self._own_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=config.normalized_base_url,
            headers={
                "Accept":        "application/json",
                "Content-Type":  "application/json",
                "Authorization": f"Bearer {config.llm_api_key}",
                "User-Agent":    config.user_agent,
            },
            timeout=httpx.Timeout(config.llm_timeout_s),
        )

    @property
    def model(self) -> str:
        return self._config.llm_model

    async def close(self) -> None:
        if self._own_client:
            try:
                await self._client.aclose()
            except Exception as exc:
                LOGGER.debug("intelligence.llm_client.close error=%s", exc)

    async def __aenter__(self) -> "OpenAILLMClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    # ── Public API ────────────────────────────────────────────────────────

    async def complete_json(
        self, *, system: str, user: str,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> str:
        """
        Call the LLM and return the raw response text. Never parses JSON —
        that's the caller's (parser's) job.
        """
        body: dict[str, Any] = {
            "model": self._config.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "temperature": self._config.llm_temperature if temperature is None else temperature,
            "max_tokens":  self._config.llm_max_output_tokens if max_output_tokens is None else max_output_tokens,
        }
        if self._config.llm_json_mode:
            body["response_format"] = {"type": "json_object"}

        return await self._request_with_retry(body)

    # ── Internal ──────────────────────────────────────────────────────────

    async def _request_with_retry(self, body: dict[str, Any]) -> str:
        max_attempts = max(1, self._config.llm_max_retries + 1)
        attempt = 0
        last_exc: Exception | None = None
        while True:
            attempt += 1
            try:
                response = await self._client.post("/chat/completions", json=body)
            except httpx.TimeoutException as exc:
                last_exc = LLMTimeoutError(f"LLM timeout: {exc}")
                if attempt >= max_attempts:
                    raise last_exc from exc
                await asyncio.sleep(self._backoff(attempt))
                continue
            except (httpx.ConnectError, httpx.RequestError) as exc:
                last_exc = LLMRequestError(f"LLM network error: {exc}")
                if attempt >= max_attempts:
                    raise last_exc from exc
                await asyncio.sleep(self._backoff(attempt))
                continue

            code = response.status_code
            if code == 401 or code == 403:
                raise LLMAuthError(
                    f"LLM auth failed (HTTP {code})",
                    status_code=code, response_body=_safe_body(response),
                )
            if code == 429:
                if attempt >= max_attempts:
                    raise LLMRateLimitError(
                        "LLM rate limit exceeded",
                        status_code=code, response_body=_safe_body(response),
                    )
                await asyncio.sleep(self._backoff(attempt))
                continue
            if 500 <= code < 600:
                if attempt >= max_attempts:
                    raise LLMServerError(
                        f"LLM server error (HTTP {code})",
                        status_code=code, response_body=_safe_body(response),
                    )
                await asyncio.sleep(self._backoff(attempt))
                continue
            if code >= 400:
                raise LLMRequestError(
                    f"LLM client error (HTTP {code})",
                    status_code=code, response_body=_safe_body(response),
                )

            try:
                data = response.json()
            except ValueError as exc:
                raise LLMRequestError(
                    f"LLM returned non-JSON body: {exc}",
                    status_code=code, response_body=response.text[:500],
                ) from exc

            return _extract_content(data)

    def _backoff(self, attempt: int) -> float:
        # 1s, 2s, 4s, 8s — capped at 10s
        return min(10.0, 1.0 * (2 ** (attempt - 1)))


# ── Deterministic Mock client ────────────────────────────────────────────────

class MockLLMClient:
    """
    Deterministic mock that returns canned JSON payloads for each prompt
    template. Used by:
      - unit tests (fast, offline, reproducible)
      - `INTELLIGENCE_LLM_PROVIDER=mock` dry-run mode

    The `responses` mapping lets tests override any template's canned
    response. Default responses are minimal but schema-valid.
    """

    def __init__(
        self,
        *,
        model_name: str = "mock-llm-v1",
        responses: dict[str, str] | None = None,
    ) -> None:
        self._model = model_name
        self._responses = dict(responses or {})
        self._calls: list[dict[str, str]] = []

    @property
    def model(self) -> str:
        return self._model

    @property
    def calls(self) -> list[dict[str, str]]:
        """History of every complete_json call — useful for test assertions."""
        return list(self._calls)

    async def close(self) -> None:
        return

    async def __aenter__(self) -> "MockLLMClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return

    async def complete_json(
        self, *, system: str, user: str,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> str:
        self._calls.append({"system": system[:500], "user": user[:500]})
        # Dispatch by template signature in the system prompt
        for key, payload in self._responses.items():
            if key in system:
                return payload
        return _default_mock_response(system)


# ── Factory ───────────────────────────────────────────────────────────────────

def build_llm_client(
    config: IntelligenceConfig | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> LLMClient:
    cfg = config or IntelligenceConfig.from_env()
    if cfg.llm_provider == "mock":
        return MockLLMClient(model_name=cfg.llm_model)
    return OpenAILLMClient(cfg, http_client=http_client)


# ── Utilities ────────────────────────────────────────────────────────────────

def _safe_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        try:
            return response.text[:500]
        except Exception:
            return None


def _extract_content(data: dict[str, Any]) -> str:
    """
    Pull the text content from an OpenAI Chat Completions response, tolerating
    minor provider variations. Raises `LLMRequestError` if the shape is
    unrecognizable.
    """
    try:
        choices = data.get("choices") or []
        if not choices:
            raise LLMRequestError("LLM response has no `choices`",
                                  status_code=200, response_body=data)
        message = choices[0].get("message") or {}
        content = message.get("content")
        if content is None:
            raise LLMRequestError("LLM response missing `message.content`",
                                  status_code=200, response_body=data)
        return str(content)
    except LLMRequestError:
        raise
    except Exception as exc:
        raise LLMRequestError(
            f"LLM response shape unrecognized: {exc}",
            status_code=200, response_body=data,
        ) from exc


def _default_mock_response(system_prompt: str) -> str:
    """Route to a default canned JSON by inspecting the system prompt text."""
    low = system_prompt.lower()
    if "reasoning engine" in low or "structured json verdict" in low:
        return json.dumps({
            "outcome": "NEEDS_CLARIFICATION",
            "summary": "Mock reasoning summary",
            "root_cause": "unknown",
            "confidence": 0.5,
            "evidence_used": [],
            "missing_information": ["session_id"],
            "clarification_required": True,
            "reasoning_notes": "mock response",
        })
    if "clarification request" in low:
        return json.dumps({
            "should_clarify": True,
            "questions": ["Could you please share the session ID?"],
            "reason": "session identifier is missing",
            "required_slots": ["session_id"],
        })
    if "internal observation note" in low:
        return json.dumps({
            "issue_summary": "Mock issue summary",
            "evidence": "- mock evidence line 1\n- mock evidence line 2",
            "root_cause": "mock root cause",
            "recommended_action": "Investigate further",
            "escalation": "None",
            "confidence_level": "MEDIUM",
            "body_html": "<p><strong>Mock Note</strong></p>",
        })
    if "customer reply" in low:
        return json.dumps({
            "reply_kind": "clarification",
            "body_html": "<p>Hi,</p><p>Could you please share the session ID?</p>"
                         "<p>Regards,<br>KwikID Support Team</p>",
            "confidence_level": "MEDIUM",
            "confidence": 0.6,
            "citations": [],
        })
    if "action gateway" in low or "action proposal" in low.replace(" ", ""):
        return json.dumps({
            "proposals": [
                {
                    "action_kind": "ASK_CLARIFICATION",
                    "parameters": {"questions": ["session_id"]},
                    "confidence": 0.6,
                    "risk": "SAFE",
                    "approval_required": False,
                    "rationale": "mock clarification proposal",
                }
            ]
        })
    return json.dumps({"error": "no mock response matched", "system_head": system_prompt[:120]})
