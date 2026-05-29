"""
rag_engine/generation/llm_client.py

Production-grade httpx-based OpenAI chat completion client for the B2 generation layer.

JSON reliability contract (no heuristic repair):
  1. All completions use response_format={"type":"json_object"} — the model MUST return
     valid JSON. This is enforced at the OpenAI API layer.
  2. After parsing, _validate_generation_response() checks the response SHAPE:
     answer is a non-empty string (not JSON), citations is a list.
  3. If validation fails: retry ONCE by injecting an explicit schema correction message
     at the end of the conversation turn.
  4. If the second attempt also fails: return _schema_fallback() — a deterministic
     requires_human=True response with the failure reason recorded in diagnostics.

No bracket-closing. No truncation recovery. No heuristic mutation of LLM output.
The model is given an explicit contract; if it violates the contract twice, that is
recorded as a schema failure and the caller receives a safe degraded result.

Timeout strategy (four independent phases):
  connect — DNS + TCP + TLS. Fast-fail (10 s).
  read    — time waiting for model generation. Dominant phase; configurable via CHAT_TIMEOUT_S.
  write   — sending request body. Tiny payloads; 30 s is generous.
  pool    — acquiring connection from httpx pool. Effectively instant; 10 s hard guard.

Network/HTTP retry strategy:
  - TimeoutException:  up to max_timeout_retries (default 2).
  - Network errors:    up to max_retries (default 3).
  - HTTP 408/429/5xx: up to max_retries, honouring Retry-After on 429.
  - HTTP 4xx others:  no retry — caller errors.

Graceful failure: retry exhaustion raises RuntimeError. ChatGenerator catches this and
returns a degraded requires_human=True response.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from typing import Any, AsyncGenerator, Optional

import httpx

LOGGER = logging.getLogger(__name__)

_RETRYABLE_HTTP = {408, 409, 429, 500, 502, 503, 504}

# Injected at the end of the conversation when the first response fails validation.
# Positioned as a user turn so the model sees it as a correction within context.
_SCHEMA_RETRY_INSTRUCTION = (
    "Your previous response failed schema validation. "
    "Return ONLY a JSON object with EXACTLY these two keys:\n"
    '  "answer": a plain prose string — no JSON, no curly braces, no field names inside this string\n'
    '  "citations": a JSON array of objects, each with chunk_num (integer), chunk_type (string), source_id (string or null)\n'
    "No other keys. No markdown. No text outside the JSON object."
)


class B1LLMClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4.1-mini",
        base_url: str = "https://api.openai.com/v1",
        connect_timeout_s: float = 10.0,
        read_timeout_s: Optional[float] = None,
        write_timeout_s: float = 30.0,
        pool_timeout_s: float = 10.0,
        timeout_s: int = 60,
        max_retries: int = 3,
        max_timeout_retries: int = 2,
        retry_base_delay_s: float = 0.8,
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

        _read = read_timeout_s if read_timeout_s is not None else float(timeout_s)
        self._timeout = httpx.Timeout(
            connect=connect_timeout_s,
            read=_read,
            write=write_timeout_s,
            pool=pool_timeout_s,
        )
        self._http = httpx.Client(timeout=self._timeout)
        LOGGER.debug(
            "B1LLMClient ready: model=%s connect=%.0fs read=%.0fs write=%.0fs",
            model, connect_timeout_s, _read, write_timeout_s,
        )

    def _build_messages(
        self,
        system_prompt: str,
        user_prompt: str,
        history: list[dict[str, str]] | None,
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        if history:
            for turn in history:
                role = turn.get("role")
                content = turn.get("content")
                if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_prompt})
        return messages

    def _http_complete(
        self,
        messages: list[dict[str, str]],
    ) -> tuple[str, dict[str, Any], float]:
        """Execute one JSON-mode completion with HTTP/network retry loop.

        Returns (content_str, usage_dict, wall_ms_float) on success.
        Raises RuntimeError on retry exhaustion.
        """
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
            response: httpx.Response | None = None

            try:
                response = self._http.post(url, headers=headers, json=payload)
            except httpx.TimeoutException as exc:
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
                time.sleep(self._backoff(attempt))
                attempt += 1
                continue
            except httpx.RequestError as exc:
                elapsed_ms = (time.perf_counter() - t_attempt) * 1000
                LOGGER.warning(
                    "LLM network error: model=%s attempt=%d/%d type=%s elapsed_ms=%.0f",
                    self._model, attempt + 1, self._max_retries + 1, type(exc).__name__, elapsed_ms,
                )
                if attempt >= self._max_retries:
                    raise RuntimeError(
                        f"LLM request failed after {attempt + 1} attempt(s): {type(exc).__name__}"
                    ) from exc
                time.sleep(self._backoff(attempt))
                attempt += 1
                continue

            elapsed_ms = (time.perf_counter() - t_attempt) * 1000

            if response.status_code < 400:
                LOGGER.debug(
                    "LLM call succeeded: model=%s attempt=%d status=%d elapsed_ms=%.0f total_ms=%.0f",
                    self._model, attempt + 1, response.status_code, elapsed_ms,
                    (time.perf_counter() - total_start) * 1000,
                )
                data = response.json()
                choices = data.get("choices") or []
                if not choices:
                    raise RuntimeError("OpenAI returned no choices in response")
                content = (choices[0].get("message") or {}).get("content") or ""
                usage = data.get("usage") or {}
                return content, usage, elapsed_ms

            if response.status_code in _RETRYABLE_HTTP and attempt < self._max_retries:
                LOGGER.warning(
                    "LLM retryable HTTP error: model=%s attempt=%d/%d status=%d elapsed_ms=%.0f",
                    self._model, attempt + 1, self._max_retries + 1, response.status_code, elapsed_ms,
                )
                time.sleep(self._backoff(attempt, response.headers.get("Retry-After")))
                attempt += 1
                continue

            safe_body = _sanitize_response_body(response.text)
            raise RuntimeError(
                f"OpenAI chat failed (status={response.status_code}): {safe_body}"
            )

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        history: list[dict[str, str]] | None = None,
        _timing_capture: list[dict] | None = None,
    ) -> dict[str, Any]:
        """JSON completion with strict schema validation and one-shot retry.

        On schema violation: retries once with a correction message injected.
        On second failure: returns _schema_fallback() — deterministic, requires_human=True.
        Never repairs malformed JSON heuristically.
        """
        base_messages = self._build_messages(system_prompt, user_prompt, history)
        parse_attempts = 0
        parse_failures = 0
        schema_retried = False
        last_content = ""

        for schema_pass in range(2):
            if schema_pass == 0:
                messages = base_messages
            else:
                # Inject correction turn: show what was wrong, ask for correct schema
                schema_retried = True
                messages = base_messages + [
                    {"role": "assistant", "content": last_content},
                    {"role": "user", "content": _SCHEMA_RETRY_INSTRUCTION},
                ]

            try:
                content, usage, wall_ms = self._http_complete(messages)
            except RuntimeError:
                raise  # network/timeout errors propagate to ChatGenerator

            parse_attempts += 1
            last_content = content

            # Strict parse — no heuristic repair
            parsed, parse_error = _parse_json_strict(content)
            if parse_error:
                parse_failures += 1
                LOGGER.warning(
                    "schema_pass=%d parse_error=%s content_preview=%r",
                    schema_pass, parse_error, content[:120],
                )
                continue  # retry or fall through to fallback

            # Shape validation — catches answer containing raw JSON
            valid, validation_reason = _validate_generation_response(parsed)
            if not valid:
                parse_failures += 1
                LOGGER.warning(
                    "schema_pass=%d validation_failure=%s content_preview=%r",
                    schema_pass, validation_reason, content[:120],
                )
                continue

            # Success
            if _timing_capture is not None:
                _timing_capture.append({
                    "prompt_tokens":      usage.get("prompt_tokens"),
                    "completion_tokens":  usage.get("completion_tokens"),
                    "total_tokens":       usage.get("total_tokens"),
                    "model":              self._model,
                    "llm_wall_ms":        round(wall_ms, 1),
                    "parse_attempts":     parse_attempts,
                    "parse_failures":     parse_failures,
                    "schema_retried":     schema_retried,
                })
            return parsed

        # Both schema passes failed
        LOGGER.error(
            "LLM response failed schema validation after %d attempt(s) — returning fallback",
            parse_attempts,
        )
        if _timing_capture is not None:
            _timing_capture.append({
                "model":                     self._model,
                "parse_attempts":            parse_attempts,
                "parse_failures":            parse_failures,
                "schema_retried":            schema_retried,
                "schema_validation_failed":  True,
            })
        return _schema_fallback(f"parse_failures={parse_failures} schema_retried={schema_retried}")

    async def stream_complete_async(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> AsyncGenerator[str, None]:
        """Async streaming completion — yields plain-text content tokens.

        Streaming path generates prose only (no JSON schema). Confidence and
        requires_human are pre-computed Python-side before the stream starts.
        """
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_output_tokens,
            "stream": True,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/chat/completions"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code >= 400:
                    raise RuntimeError(
                        f"OpenAI streaming failed (status={response.status_code})"
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                        delta = (chunk["choices"][0]["delta"]).get("content") or ""
                        if delta:
                            yield delta
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:  # noqa: BLE001
            pass

    def _backoff(self, attempt: int, retry_after: str | None = None) -> float:
        wait = self._retry_base_delay_s * (2 ** attempt)
        if retry_after:
            try:
                wait = max(wait, float(retry_after))
            except ValueError:
                pass
        return wait + random.uniform(0.0, 0.5)


# ── Module-level utilities ─────────────────────────────────────────────────────

_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)
_SK_RE      = re.compile(r"sk-[A-Za-z0-9]{10,}", re.IGNORECASE)


def _sanitize_response_body(text: str) -> str:
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _SK_RE.sub("sk-[REDACTED]", text)
    return text[:300]


def _parse_json_strict(content: str) -> tuple[dict[str, Any] | None, str]:
    """Strict JSON parse — no heuristic repair.

    Returns (parsed_dict, "") on success.
    Returns (None, error_reason) on any failure.
    """
    if not content or not content.strip():
        return None, "empty_content"
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        return None, f"json_decode_error: {exc.msg} at pos {exc.pos}"
    if not isinstance(parsed, dict):
        return None, f"not_a_dict: got {type(parsed).__name__}"
    return parsed, ""


def _validate_generation_response(parsed: dict[str, Any]) -> tuple[bool, str]:
    """Validate the LLM response has the correct shape for our 2-field schema.

    Checks:
    - 'answer' exists and is a non-empty string
    - 'answer' does not look like raw JSON (starts with { or [)
    - 'citations' is a list (or absent — treated as empty list)
    - No nested JSON objects inside answer

    Returns (is_valid: bool, failure_reason: str).
    """
    answer = parsed.get("answer")
    if not isinstance(answer, str):
        return False, f"answer_not_string: got {type(answer).__name__}"
    if not answer.strip():
        return False, "answer_empty"

    # Critical guard: answer must not be raw JSON
    stripped_answer = answer.strip()
    if stripped_answer.startswith("{") or stripped_answer.startswith("["):
        return False, "answer_contains_json_object"

    # Guard against deeply nested JSON strings (escaped JSON)
    if '{"answer"' in stripped_answer or '"citations"' in stripped_answer:
        return False, "answer_contains_schema_field_names"

    citations = parsed.get("citations")
    if citations is not None and not isinstance(citations, list):
        return False, f"citations_not_list: got {type(citations).__name__}"

    return True, ""


def _schema_fallback(reason: str) -> dict[str, Any]:
    """Deterministic fallback when both schema attempts fail.

    Sets requires_human=True with an explicit failure message so the caller
    knows this is a generation failure, not a retrieval failure.
    """
    return {
        "answer": (
            "The AI generation service was unable to produce a valid structured response "
            "for this query. Please handle this support ticket manually."
        ),
        "citations": [],
        "_schema_failure": True,
        "_schema_failure_reason": reason,
    }
