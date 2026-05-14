"""
rag_engine/embedding/openai_provider.py

OpenAI embedding provider with production-grade retry + resilience.

Retry policy (all retried with exponential backoff + jitter):
  - httpx.ConnectError  — DNS failure, getaddrinfo failed  ← root cause of crash
  - httpx.NetworkError  — connection reset, broken pipe
  - httpx.TimeoutException — connect/read/write timeout
  - HTTP 429  — rate limit (honours Retry-After header)
  - HTTP 5xx  — server errors (500, 502, 503, 504)

Not retried (unrecoverable):
  - HTTP 400 — bad request (malformed payload)
  - HTTP 401/403 — auth failure (bad API key)

Timeout strategy (all configurable via env vars):
  - EMBEDDING_CONNECT_TIMEOUT_S  — DNS + TCP handshake (default 10s)
  - EMBEDDING_READ_TIMEOUT_S     — waiting for first response byte (default 90s)
  - EMBEDDING_WRITE_TIMEOUT_S    — sending request body (default 30s)
  - EMBEDDING_POOL_TIMEOUT_S     — waiting for a connection from pool (default 10s)

Connection pool:
  - max_keepalive_connections=5, max_connections=10
  - keepalive_expiry=30s
  - Reuses the same TCP connection across batches (saves DNS + TLS overhead)
"""
from __future__ import annotations

import logging
import random
import time
from typing import Optional

import httpx

from rag_engine.embedding.base import EmbeddingError, EmbeddingResult

LOGGER = logging.getLogger(__name__)

_MAX_INPUTS_PER_CALL = 2048

# Errors that represent transient network problems and should be retried
_NETWORK_ERRORS = (
    httpx.ConnectError,        # DNS failure, refused connection
    httpx.RemoteProtocolError, # Server closed connection unexpectedly
    httpx.ReadError,           # Connection dropped during read
    httpx.WriteError,          # Connection dropped during write
    httpx.NetworkError,        # Base class for all network errors
)

# HTTP status codes that indicate a transient server-side problem
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class OpenAIEmbeddingProvider:
    """
    Calls the OpenAI Embeddings API with production-grade resilience.

    Thread-safety: one instance per pipeline run is the intended usage.
    The underlying httpx.Client is NOT safe to share across threads without locking.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        base_url: str = "https://api.openai.com/v1",
        dimensions: int = 1536,
        # Retry settings
        max_retries: int = 6,
        retry_base_delay_s: float = 1.0,
        retry_max_delay_s: float = 60.0,
        # Separate timeout controls (seconds)
        connect_timeout_s: float = 10.0,
        read_timeout_s: float = 90.0,
        write_timeout_s: float = 30.0,
        pool_timeout_s: float = 10.0,
    ) -> None:
        if not api_key or api_key.startswith("your-"):
            raise ValueError(
                "OPENAI_API_KEY is missing or is still a placeholder. "
                "Set it in your .env file."
            )

        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._dimensions = dimensions
        self._max_retries = max_retries
        self._retry_base_delay_s = retry_base_delay_s
        self._retry_max_delay_s = retry_max_delay_s

        # Persistent HTTP client — reused across all batches in a run.
        # Connection pooling avoids per-batch DNS resolution and TLS handshake.
        self._client = httpx.Client(
            timeout=httpx.Timeout(
                connect=connect_timeout_s,
                read=read_timeout_s,
                write=write_timeout_s,
                pool=pool_timeout_s,
            ),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            limits=httpx.Limits(
                max_keepalive_connections=5,
                max_connections=10,
                keepalive_expiry=30.0,
            ),
            follow_redirects=True,
        )

        LOGGER.debug(
            "OpenAIEmbeddingProvider ready: model=%s, dimensions=%d, "
            "max_retries=%d, connect_timeout=%.1fs, read_timeout=%.1fs, "
            "api_key=%s...",
            model, dimensions, max_retries,
            connect_timeout_s, read_timeout_s,
            api_key[:8],
        )

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model_name(self) -> str:
        return self._model

    def embed_single(self, text: str) -> list[float]:
        result = self.embed_batch([text])
        return result.embeddings[0]

    def embed_batch(self, texts: list[str]) -> EmbeddingResult:
        """
        Embed texts via OpenAI API with full retry resilience.

        Retries on:
          - DNS failures / ConnectError
          - Network drops / RemoteProtocolError
          - Timeouts (connect or read)
          - 429 rate limits (honours Retry-After)
          - 5xx server errors

        Raises EmbeddingError after all retries are exhausted or on unrecoverable errors.
        """
        if not texts:
            return EmbeddingResult(texts=[], embeddings=[], model=self._model,
                                   total_tokens=0, api_calls=0)

        if len(texts) > _MAX_INPUTS_PER_CALL:
            raise ValueError(
                f"Batch size {len(texts)} exceeds API limit {_MAX_INPUTS_PER_CALL}. "
                "Use BatchEmbeddingProcessor to split automatically."
            )

        payload: dict = {
            "model": self._model,
            "input": texts,
            "encoding_format": "float",
        }
        if "3-small" in self._model or "3-large" in self._model:
            payload["dimensions"] = self._dimensions

        last_exc: Optional[Exception] = None
        total_attempts = self._max_retries + 1  # 1 original attempt + N retries

        for attempt in range(1, total_attempts + 1):
            try:
                LOGGER.debug(
                    "Embedding API call: attempt %d/%d, batch_size=%d",
                    attempt, total_attempts, len(texts)
                )
                response = self._client.post(
                    f"{self._base_url}/embeddings",
                    json=payload,
                )

                # ── Rate limit ────────────────────────────────────────────────
                if response.status_code == 429:
                    wait = self._parse_retry_after(response)
                    LOGGER.warning(
                        "OpenAI rate limit (attempt %d/%d). Waiting %.1fs. "
                        "[Retry-After: %s]",
                        attempt, total_attempts, wait,
                        response.headers.get("Retry-After", "not set"),
                    )
                    if attempt < total_attempts:
                        time.sleep(wait)
                    continue

                # ── Server errors ─────────────────────────────────────────────
                if response.status_code in _RETRYABLE_STATUS_CODES - {429}:
                    wait = self._backoff_with_jitter(attempt)
                    LOGGER.warning(
                        "OpenAI server error HTTP %d (attempt %d/%d). "
                        "Retrying in %.1fs.",
                        response.status_code, attempt, total_attempts, wait,
                    )
                    if attempt < total_attempts:
                        time.sleep(wait)
                    continue

                # ── Auth / bad request — unrecoverable ───────────────────────
                if response.status_code in (400, 401, 403):
                    raise EmbeddingError(
                        f"OpenAI API returned HTTP {response.status_code} "
                        f"(unrecoverable): {response.text[:300]}"
                    )

                response.raise_for_status()

                data = response.json()
                embeddings = [
                    item["embedding"]
                    for item in sorted(data["data"], key=lambda x: x["index"])
                ]
                total_tokens = data.get("usage", {}).get("total_tokens", 0)

                if attempt > 1:
                    LOGGER.info(
                        "Embedding succeeded on attempt %d/%d after retries.",
                        attempt, total_attempts,
                    )

                return EmbeddingResult(
                    texts=texts,
                    embeddings=embeddings,
                    model=self._model,
                    total_tokens=total_tokens,
                    api_calls=1,
                )

            # ── Network errors (DNS, connection reset, etc.) ───────────────
            except _NETWORK_ERRORS as exc:
                last_exc = exc
                wait = self._backoff_with_jitter(attempt)
                LOGGER.warning(
                    "OpenAI network error (attempt %d/%d): %s: %s. "
                    "Retrying in %.1fs.",
                    attempt, total_attempts,
                    type(exc).__name__, _safe_exc_str(exc),
                    wait,
                )
                if attempt < total_attempts:
                    time.sleep(wait)

            # ── Timeout (connect or read) ──────────────────────────────────
            except httpx.TimeoutException as exc:
                last_exc = exc
                wait = self._backoff_with_jitter(attempt)
                LOGGER.warning(
                    "OpenAI timeout %s (attempt %d/%d). Retrying in %.1fs.",
                    type(exc).__name__, attempt, total_attempts, wait,
                )
                if attempt < total_attempts:
                    time.sleep(wait)

            # ── Unrecoverable HTTP errors ──────────────────────────────────
            except httpx.HTTPStatusError as exc:
                raise EmbeddingError(
                    f"OpenAI embedding failed with HTTP {exc.response.status_code}: "
                    f"{exc.response.text[:300]}"
                ) from exc

        raise EmbeddingError(
            f"OpenAI embedding failed after {total_attempts} attempts. "
            f"Last error: {type(last_exc).__name__}: {_safe_exc_str(last_exc)}"
        )

    def _backoff_with_jitter(self, attempt: int) -> float:
        """Exponential backoff with ±30% uniform jitter to avoid thundering herd."""
        delay = self._retry_base_delay_s * (2 ** (attempt - 1))
        jitter = random.uniform(0.0, delay * 0.3)
        return min(delay + jitter, self._retry_max_delay_s)

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> float:
        """Read Retry-After header; fall back to 30s if missing or non-numeric."""
        raw = response.headers.get("Retry-After", "")
        try:
            return max(float(raw), 1.0)
        except (ValueError, TypeError):
            return 30.0

    def close(self) -> None:
        """Close the underlying HTTP client and release connections."""
        self._client.close()

    def __enter__(self) -> "OpenAIEmbeddingProvider":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _safe_exc_str(exc: Optional[Exception]) -> str:
    """Return a safe (non-key-leaking) string representation of an exception."""
    if exc is None:
        return "unknown"
    msg = str(exc)
    # Truncate very long messages (avoid dumping huge TLS certs etc.)
    return msg[:200] if len(msg) > 200 else msg
