"""Local Ollama chat and embedding adapters."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

from universal_video_maker.config import Settings
from universal_video_maker.providers.http import (
    HttpTransport,
    ProviderTransportError,
    UrllibTransport,
)


class OllamaClientError(Exception):
    """Sanitized Ollama request, response, or structured-output error."""


class OllamaClient:
    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b",
        timeout_seconds: int = 600,
        transport: HttpTransport | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        hostname = (parsed.hostname or "").lower()
        is_local_http = parsed.scheme.lower() == "http" and hostname in {
            "localhost",
            "127.0.0.1",
            "::1",
        }
        if (
            (parsed.scheme.lower() != "https" and not is_local_http)
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Ollama base URL must be HTTPS, or HTTP on a loopback address.")
        if not model.strip():
            raise ValueError("Ollama chat model cannot be empty.")
        if timeout_seconds < 1:
            raise ValueError("Ollama request timeout must be positive.")
        self._base_url = base_url.rstrip("/")
        self._model = model.strip()
        self._timeout_seconds = timeout_seconds
        self._transport = transport or UrllibTransport()

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        transport: HttpTransport | None = None,
    ) -> OllamaClient:
        return cls(
            base_url=settings.ollama_base_url,
            model=settings.ollama_chat_model,
            timeout_seconds=settings.ollama_request_timeout_seconds,
            transport=transport,
        )

    def chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not system_prompt.strip() or not user_prompt.strip():
            raise ValueError("Ollama prompts cannot be empty.")
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "format": dict(response_schema),
            "options": {"temperature": 0.2},
        }
        response = self._post_json("/api/chat", body)
        message = response.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise OllamaClientError("Ollama returned no structured chat content.")
        try:
            generated = json.loads(content)
        except json.JSONDecodeError as error:
            raise OllamaClientError("Ollama returned invalid structured JSON.") from error
        if not isinstance(generated, dict):
            raise OllamaClientError("Ollama structured output must be a JSON object.")
        return generated

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("Ollama embedding inputs must be non-empty strings.")
        response = self._post_json(
            "/api/embed",
            {"model": self._model, "input": list(texts), "truncate": False},
        )
        embeddings = response.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise OllamaClientError("Ollama must return one embedding for each input text.")

        normalized: list[tuple[float, ...]] = []
        for embedding in embeddings:
            if not isinstance(embedding, list) or not embedding:
                raise OllamaClientError("Ollama returned an invalid embedding vector.")
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in embedding
            ):
                raise OllamaClientError("Ollama returned a non-numeric embedding vector.")
            normalized.append(tuple(float(value) for value in embedding))
        return tuple(normalized)

    def _post_json(self, path: str, body: Mapping[str, object]) -> dict[str, Any]:
        try:
            raw_response = self._transport.request(
                "POST",
                f"{self._base_url}{path}",
                headers={"Content-Type": "application/json"},
                body=json.dumps(body).encode("utf-8"),
                timeout=self._timeout_seconds,
            )
        except ProviderTransportError as error:
            raise OllamaClientError(str(error)) from error
        try:
            response = json.loads(raw_response)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OllamaClientError("Ollama returned invalid JSON.") from error
        if not isinstance(response, dict):
            raise OllamaClientError("Ollama returned an invalid response shape.")
        return response


class OllamaEmbeddingProvider:
    """EmbeddingProvider implementation for Ollama's /api/embed endpoint."""

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        model: str = "nomic-embed-text",
        timeout_seconds: int = 180,
        transport: HttpTransport | None = None,
    ) -> None:
        self._client = OllamaClient(
            base_url=base_url,
            model=model,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        transport: HttpTransport | None = None,
    ) -> OllamaEmbeddingProvider:
        return cls(
            base_url=settings.ollama_base_url,
            model=settings.ollama_embedding_model,
            timeout_seconds=settings.ollama_request_timeout_seconds,
            transport=transport,
        )

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return self._client.embed(texts)
