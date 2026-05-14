"""
rag_engine/generation/llm_client.py

Thin httpx-based OpenAI chat completion client for the B1 generation layer.
Mirrors the retry/backoff pattern in app/chat.py (ChatGPTClient) but lives
inside rag_engine so the module is self-contained.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from typing import Any

import httpx

LOGGER = logging.getLogger(__name__)

_RETRYABLE = {408, 409, 429, 500, 502, 503, 504}


class B1LLMClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout_s: int = 60,
        max_retries: int = 3,
        retry_base_delay_s: float = 0.8,
        temperature: float = 0.2,
        max_output_tokens: int = 800,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required for B1LLMClient")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._retry_base_delay_s = retry_base_delay_s
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens

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
        with httpx.Client(timeout=self._timeout_s) as client:
            while True:
                try:
                    response = client.post(url, headers=headers, json=payload)
                except httpx.RequestError:
                    if attempt >= self._max_retries:
                        raise
                    time.sleep(self._backoff(attempt))
                    attempt += 1
                    continue

                if response.status_code < 400:
                    break
                if response.status_code in _RETRYABLE and attempt < self._max_retries:
                    time.sleep(self._backoff(attempt, response.headers.get("Retry-After")))
                    attempt += 1
                    continue
                # Sanitize: strip Authorization header echo and any Bearer token from body
                safe_body = _sanitize_response_body(response.text)
                raise RuntimeError(
                    f"OpenAI chat failed ({response.status_code}): {safe_body}"
                )

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("OpenAI returned no choices")
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


_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)
_SK_RE      = re.compile(r"sk-[A-Za-z0-9]{10,}", re.IGNORECASE)


def _sanitize_response_body(text: str) -> str:
    """Remove any API key patterns before logging the response body."""
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _SK_RE.sub("sk-[REDACTED]", text)
    return text[:300]


def _parse_json(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {"answer": content.strip(), "confidence": "low", "citations": [], "follow_up_question": None}
    if isinstance(parsed, dict):
        return parsed
    return {"answer": str(parsed), "confidence": "low", "citations": [], "follow_up_question": None}
