from __future__ import annotations

import random
import time
from typing import Sequence

import httpx


class EmbeddingClient:
    def __init__(
        self,
        provider: str,
        api_key: str,
        model: str,
        base_url: str,
        timeout_s: int = 60,
        max_retries: int = 3,
        retry_base_delay_s: float = 0.8,
    ) -> None:
        self._provider = provider
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._retry_base_delay_s = retry_base_delay_s

    @staticmethod
    def _trim_text(text: str, max_chars: int = 7000) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars]

    @staticmethod
    def _is_context_length_error(status_code: int, detail: str) -> bool:
        detail_lower = detail.lower()
        return status_code == 400 and "context length" in detail_lower

    @staticmethod
    def _should_retry_status(status_code: int) -> bool:
        return status_code in {408, 409, 429, 500, 502, 503, 504}

    def _post_with_retries(
        self,
        client: httpx.Client,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
    ) -> httpx.Response:
        attempt = 0
        while True:
            try:
                response = client.post(url, headers=headers, json=payload)
            except httpx.RequestError:
                if attempt >= self._max_retries:
                    raise
                wait_s = self._retry_base_delay_s * (2**attempt) + random.uniform(0.0, min(1.0, self._retry_base_delay_s))
                time.sleep(wait_s)
                attempt += 1
                continue
            if response.status_code < 400:
                return response
            if not self._should_retry_status(response.status_code) or attempt >= self._max_retries:
                return response
            retry_after_header = response.headers.get("Retry-After")
            if retry_after_header:
                try:
                    wait_s = max(self._retry_base_delay_s, float(retry_after_header))
                except ValueError:
                    wait_s = self._retry_base_delay_s * (2**attempt)
            else:
                wait_s = self._retry_base_delay_s * (2**attempt)
            wait_s += random.uniform(0.0, min(1.0, self._retry_base_delay_s))
            time.sleep(wait_s)
            attempt += 1

    def embed_texts(self, inputs: Sequence[str]) -> list[list[float]]:
        if not inputs:
            return []
        # Guard against model context overflow in local embedding models.
        normalized_inputs = [self._trim_text(text) for text in inputs]
        if self._provider == "ollama":
            return self._embed_with_ollama(normalized_inputs)
        return self._embed_with_openai(normalized_inputs)

    def _embed_with_openai(self, inputs: Sequence[str]) -> list[list[float]]:
        if not self._api_key:
            raise ValueError("EMBEDDING_API_KEY is required when EMBEDDING_PROVIDER=openai")
        with httpx.Client(timeout=self._timeout_s) as client:
            response = self._post_with_retries(
                client,
                url=f"{self._base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                payload={"model": self._model, "input": list(inputs)},
            )
            response.raise_for_status()
            data = response.json()
        vectors = [item["embedding"] for item in data.get("data", [])]
        if len(vectors) != len(inputs):
            raise RuntimeError("Embedding provider returned unexpected vector count")
        return vectors

    def _embed_one_with_ollama(self, client: httpx.Client, text: str) -> list[float]:
        candidate = text
        min_chars = 300
        while True:
            single = self._post_with_retries(
                client,
                url=f"{self._base_url}/api/embed",
                headers={"Content-Type": "application/json"},
                payload={"model": self._model, "input": candidate},
            )
            if single.status_code < 400:
                data = single.json()
                vector = data.get("embedding")
                if vector is None:
                    embeddings = data.get("embeddings")
                    if isinstance(embeddings, list) and embeddings:
                        vector = embeddings[0]
                if not isinstance(vector, list):
                    raise RuntimeError("Ollama /api/embed returned invalid single embedding payload")
                return vector

            detail = single.text.strip()
            if self._is_context_length_error(single.status_code, detail) and len(candidate) > min_chars:
                # Shrink aggressively until it fits model context.
                candidate = candidate[: max(min_chars, len(candidate) // 2)]
                continue
            raise RuntimeError(f"Ollama /api/embed rejected request ({single.status_code}): {detail}")

    def _embed_with_ollama(self, inputs: Sequence[str]) -> list[list[float]]:
        with httpx.Client(timeout=self._timeout_s) as client:
            embed_response = self._post_with_retries(
                client,
                url=f"{self._base_url}/api/embed",
                headers={"Content-Type": "application/json"},
                payload={"model": self._model, "input": list(inputs)},
            )
            if embed_response.status_code < 400:
                data = embed_response.json()
                vectors = data.get("embeddings")
                if not isinstance(vectors, list) or len(vectors) != len(inputs):
                    raise RuntimeError("Ollama /api/embed returned invalid embeddings payload")
                return vectors

            # Some Ollama versions reject batched input with 400; retry per item.
            if embed_response.status_code == 400:
                vectors: list[list[float]] = []
                for text in inputs:
                    vectors.append(self._embed_one_with_ollama(client, text))
                return vectors

            # Compatibility fallback for older Ollama route.
            if embed_response.status_code != 404:
                detail = embed_response.text.strip()
                raise RuntimeError(f"Ollama /api/embed failed ({embed_response.status_code}): {detail}")

            vectors: list[list[float]] = []
            for text in inputs:
                candidate = text
                min_chars = 300
                while True:
                    response = self._post_with_retries(
                        client,
                        url=f"{self._base_url}/api/embeddings",
                        headers={"Content-Type": "application/json"},
                        payload={"model": self._model, "prompt": candidate},
                    )
                    if response.status_code < 400:
                        data = response.json()
                        vector = data.get("embedding")
                        if not isinstance(vector, list):
                            raise RuntimeError("Ollama /api/embeddings returned invalid embedding payload")
                        vectors.append(vector)
                        break
                    detail = response.text.strip()
                    if self._is_context_length_error(response.status_code, detail) and len(candidate) > min_chars:
                        candidate = candidate[: max(min_chars, len(candidate) // 2)]
                        continue
                    raise RuntimeError(f"Ollama /api/embeddings failed ({response.status_code}): {detail}")
            return vectors

    def healthcheck(self) -> bool:
        vectors = self.embed_texts(["healthcheck"])
        return bool(vectors and vectors[0])

