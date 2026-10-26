"""Tavily search adapter and durable storage for its short result excerpts."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import SecretStr

from universal_video_maker.config import Settings
from universal_video_maker.providers.http import (
    HttpTransport,
    ProviderTransportError,
    UrllibTransport,
)
from universal_video_maker.research import CapturedSource


class TavilyClientError(Exception):
    """Sanitized Tavily request or response error."""


@dataclass(frozen=True)
class TavilySearchResult:
    title: str
    url: str
    content: str
    score: float | None = None
    published_at: date | None = None

    def __post_init__(self) -> None:
        parsed = urlsplit(self.url.strip())
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise ValueError("Tavily source URLs must be absolute HTTPS URLs.")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Tavily source URLs cannot include credentials.")
        if (
            not self.title.strip()
            or len(self.title.strip()) > 500
            or not self.content.strip()
            or len(self.content) > 10_000
            or len(self.url) > 2_048
        ):
            raise ValueError("Tavily result title and excerpt exceed the supported limits.")
        if self.score is not None and (not math.isfinite(self.score) or not 0 <= self.score <= 1):
            raise ValueError("Tavily result score must be between zero and one.")
        canonical_url = urlunsplit(
            (
                "https",
                parsed.netloc.lower(),
                parsed.path or "/",
                parsed.query,
                "",
            )
        )
        object.__setattr__(self, "title", self.title.strip())
        object.__setattr__(self, "url", canonical_url)
        object.__setattr__(self, "content", self.content.strip())


class TavilySearchClient:
    def __init__(
        self,
        *,
        api_key: SecretStr | str,
        api_base_url: str = "https://api.tavily.com",
        timeout_seconds: int = 30,
        default_max_results: int = 6,
        transport: HttpTransport | None = None,
    ) -> None:
        self._api_key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        if not self._api_key.strip():
            raise ValueError("Tavily API key cannot be empty.")
        parsed = urlsplit(api_base_url)
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Tavily API base URL must be an HTTPS origin.")
        if timeout_seconds < 1:
            raise ValueError("Tavily request timeout must be positive.")
        if not 1 <= default_max_results <= 20:
            raise ValueError("Tavily default max results must be between one and twenty.")
        self._api_base_url = api_base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._default_max_results = default_max_results
        self._transport = transport or UrllibTransport()

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        transport: HttpTransport | None = None,
    ) -> TavilySearchClient:
        if settings.tavily_api_key is None:
            raise ValueError("Configure UVM_TAVILY_API_KEY before using Tavily.")
        return cls(
            api_key=settings.tavily_api_key,
            api_base_url=settings.tavily_api_base_url,
            timeout_seconds=settings.tavily_request_timeout_seconds,
            default_max_results=settings.tavily_max_results,
            transport=transport,
        )

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> tuple[TavilySearchResult, ...]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("Tavily search query cannot be empty.")
        result_limit = self._default_max_results if max_results is None else max_results
        if not 1 <= result_limit <= 20:
            raise ValueError("Tavily max_results must be between one and twenty.")

        request_body = {
            "api_key": self._api_key,
            "query": normalized_query,
            "search_depth": "basic",
            "chunks_per_source": 1,
            "max_results": result_limit,
            "include_answer": False,
            "include_raw_content": False,
            "include_published_date": True,
        }
        try:
            raw_response = self._transport.request(
                "POST",
                f"{self._api_base_url}/search",
                headers={"Content-Type": "application/json"},
                body=json.dumps(request_body).encode("utf-8"),
                timeout=self._timeout_seconds,
            )
        except ProviderTransportError as error:
            raise TavilyClientError(str(error)) from error

        try:
            response = json.loads(raw_response)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TavilyClientError("Tavily returned invalid JSON.") from error
        if not isinstance(response, dict) or not isinstance(response.get("results"), list):
            raise TavilyClientError("Tavily returned an invalid search response.")

        results: list[TavilySearchResult] = []
        seen_urls: set[str] = set()
        for item in response["results"]:
            if not isinstance(item, dict):
                raise TavilyClientError("Tavily returned an invalid search result.")
            title, url, content = item.get("title"), item.get("url"), item.get("content")
            if not all(isinstance(value, str) for value in (title, url, content)):
                raise TavilyClientError("Tavily returned an incomplete search result.")
            if not title.strip() or not content.strip():
                continue

            score = item.get("score")
            if score is not None and (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
            ):
                raise TavilyClientError("Tavily returned an invalid relevance score.")
            published_at = _parse_published_date(item.get("published_date"))
            try:
                result = TavilySearchResult(
                    title=title,
                    url=url,
                    content=content,
                    score=float(score) if score is not None else None,
                    published_at=published_at,
                )
            except ValueError as error:
                raise TavilyClientError("Tavily returned invalid source metadata.") from error
            if result.url not in seen_urls:
                results.append(result)
                seen_urls.add(result.url)
        return tuple(results)


class LocalSourceSnapshotStore:
    """Persist only Tavily's short search excerpts, not full source pages."""

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)

    def capture(self, result: TavilySearchResult) -> CapturedSource:
        payload = result.content.encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        path = self._directory / f"{digest}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != payload:
                raise OSError("Source snapshot hash collision detected.")
        else:
            path.write_bytes(payload)
        publisher = urlsplit(result.url).hostname
        if publisher is None:
            raise ValueError("Tavily result must have a publisher hostname.")
        return CapturedSource(
            canonical_url=result.url,
            title=result.title,
            publisher=publisher,
            source_type="tavily_search_excerpt",
            snapshot_bytes=payload,
            storage_uri=path.resolve().as_uri(),
            published_at=result.published_at,
        )


def _parse_published_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None
