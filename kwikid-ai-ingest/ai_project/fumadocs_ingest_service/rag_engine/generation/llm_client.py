"""
rag_engine/generation/llm_client.py

Production-grade httpx-based OpenAI chat completion client for the B2 generation layer.

Timeout strategy (four independent phases):
  connect   — DNS resolution + TCP + TLS handshake. Fast-fail (10s). OpenAI's endpoint
              is a well-known stable IP; a 10s connect timeout covers any transient DNS blip.
  read      — Time between sending the request and receiving each TCP segment of the response.
              This is the dominant phase for LLM calls (the model must generate before responding).
              Defaults to 60s (configurable via CHAT_TIMEOUT_S env var).
  write     — Time to send the request body. Our payloads are tiny (<8KB); 30s is generous.
  pool      — Time to acquire a connection from httpx's internal pool. Since we create a fresh
              Client per attempt, this is effectively always instant (10s is a hard guard).

Retry strategy:
  - TimeoutException:  retry up to max_timeout_retries (default 2). Each retry creates a fresh
                       httpx.Client so stale TCP state from the previous attempt is discarded.
  - Network errors:    retry up to max_retries (default 3). Covers transient connect failures.
  - HTTP 408/429/5xx:  retry up to max_retries, honouring Retry-After header on 429.
  - HTTP 4xx others:   no retry — these are caller errors.

Graceful failure: all retry exhaustion raises RuntimeError. The caller (ChatGenerator) is
expected to catch this and return a degraded requires_human=True response rather than propagating
a 502 to the end user.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from typing import Any, Optional

import httpx

LOGGER = logging.getLogger(__name__)

_RETRYABLE_HTTP = {408, 409, 429, 500, 502, 503, 504}


class B1LLMClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        # ── Granular timeouts (preferred) ─────────────────────────────────────
        connect_timeout_s: float = 10.0,
        read_timeout_s: Optional[float] = None,   # defaults to timeout_s when None
        write_timeout_s: float = 30.0,
        pool_timeout_s: float = 10.0,
        # ── Legacy scalar (backward compat — sets read_timeout_s when not overridden) ─
        timeout_s: int = 60,
        # ── Retry ────────────────────────────────────────────────────────────
        max_retries: int = 3,
        max_timeout_retries: int = 2,   # separate cap for timeout retries (each costs timeout_s seconds)
        retry_base_delay_s: float = 0.8,
        # ── Generation ───────────────────────────────────────────────────────
        temperature: float = 0.2,
        max_output_tokens: int = 800,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required for B1LLMClient")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._max_timeout_retries = max_timeout_retries
        self._retry_base_delay_s = retry_base_delay_s
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens

        # Build granular httpx.Timeout — read defaults to legacy timeout_s for compat
        _read = read_timeout_s if read_timeout_s is not None else float(timeout_s)
        self._timeout = httpx.Timeout(
            connect=connect_timeout_s,
            read=_read,
            write=write_timeout_s,
            pool=pool_timeout_s,
        )
        LOGGER.debug(
            "B1LLMClient ready: model=%s connect=%.0fs read=%.0fs write=%.0fs",
            model, connect_timeout_s, _read, write_timeout_s,
        )

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        if history:
            for turn in history:
                role = turn.get("role")
                content = turn.get("content")
                if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_prompt})

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/chat/completions"

        attempt = 0
        timeout_attempts = 0
        total_start = time.perf_counter()

        while True:
            t_attempt = time.perf_counter()

            # ── Network call — fresh client per attempt (no stale TCP state) ──
            response: httpx.Response | None = None
            network_exc: Exception | None = None

            try:
                with httpx.Client(timeout=self._timeout) as http:
                    response = http.post(url, headers=headers, json=payload)
            except httpx.TimeoutException as exc:
                network_exc = exc
                elapsed_ms = (time.perf_counter() - t_attempt) * 1000
                timeout_attempts += 1
                LOGGER.warning(
                    "LLM timeout: model=%s attempt=%d/%d timeout_attempts=%d elapsed_ms=%.0f",
                    self._model, attempt + 1, self._max_retries + 1, timeout_attempts, elapsed_ms,
                )
                if attempt >= self._max_retries or timeout_attempts > self._max_timeout_retries:
                    raise RuntimeError(
                        f"LLM request timed out after {attempt + 1} attempt(s) "
                        f"(timeout_attempts={timeout_attempts})"
                    ) from exc
                delay = self._backoff(attempt)
                LOGGER.info("LLM retry in %.1fs (timeout)", delay)
                time.sleep(delay)
                attempt += 1
                continue
            except httpx.RequestError as exc:
                network_exc = exc
                elapsed_ms = (time.perf_counter() - t_attempt) * 1000
                LOGGER.warning(
                    "LLM network error: model=%s attempt=%d/%d type=%s elapsed_ms=%.0f",
                    self._model, attempt + 1, self._max_retries + 1, type(exc).__name__, elapsed_ms,
                )
                if attempt >= self._max_retries:
                    raise RuntimeError(
                        f"LLM request failed after {attempt + 1} attempt(s): {type(exc).__name__}"
                    ) from exc
                delay = self._backoff(attempt)
                LOGGER.info("LLM retry in %.1fs (network error)", delay)
                time.sleep(delay)
                attempt += 1
                continue

            # ── HTTP response received ────────────────────────────────────────
            elapsed_ms = (time.perf_counter() - t_attempt) * 1000

            if response.status_code < 400:
                LOGGER.debug(
                    "LLM call succeeded: model=%s attempt=%d status=%d elapsed_ms=%.0f total_ms=%.0f",
                    self._model, attempt + 1, response.status_code, elapsed_ms,
                    (time.perf_counter() - total_start) * 1000,
                )
                break

            if response.status_code in _RETRYABLE_HTTP and attempt < self._max_retries:
                LOGGER.warning(
                    "LLM retryable HTTP error: model=%s attempt=%d/%d status=%d elapsed_ms=%.0f",
                    self._model, attempt + 1, self._max_retries + 1, response.status_code, elapsed_ms,
                )
                delay = self._backoff(attempt, response.headers.get("Retry-After"))
                LOGGER.info("LLM retry in %.1fs (HTTP %d)", delay, response.status_code)
                time.sleep(delay)
                attempt += 1
                continue

            safe_body = _sanitize_response_body(response.text)
            raise RuntimeError(
                f"OpenAI chat failed (status={response.status_code}): {safe_body}"
            )

        # ── Parse response ────────────────────────────────────────────────────
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("OpenAI returned no choices in response")
        content = (choices[0].get("message") or {}).get("content") or ""
        return _parse_json(content)

    def _backoff(self, attempt: int, retry_after: str | None = None) -> float:
        wait = self._retry_base_delay_s * (2 ** attempt)
        if retry_after:
            try:
                wait = max(wait, float(retry_after))
            except ValueError:
                pass
        return wait + random.uniform(0.0, 0.5)


# ── Module-level sanitizers and parsers ───────────────────────────────────────

_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)
_SK_RE      = re.compile(r"sk-[A-Za-z0-9]{10,}", re.IGNORECASE)


def _sanitize_response_body(text: str) -> str:
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _SK_RE.sub("sk-[REDACTED]", text)
    return text[:300]


def _parse_json(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {
            "answer": content.strip(),
            "confidence": "low",
            "citations": [],
            "requires_human": True,
            "follow_up_question": None,
        }
    if isinstance(parsed, dict):
        return parsed
    return {
        "answer": str(parsed),
        "confidence": "low",
        "citations": [],
        "requires_human": True,
        "follow_up_question": None,
    }
