"""Small injectable JSON HTTP transport shared by provider adapters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MAX_PROVIDER_RESPONSE_BYTES = 2_000_000


class ProviderTransportError(Exception):
    """Sanitized provider transport failure."""


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: int,
    ) -> bytes: ...


class UrllibTransport:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: int,
    ) -> bytes:
        request = Request(url, data=body, headers=dict(headers), method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
        except HTTPError as error:
            raise ProviderTransportError(f"Provider returned HTTP {error.code}.") from error
        except URLError as error:
            raise ProviderTransportError("Provider request failed.") from error
        except TimeoutError as error:
            raise ProviderTransportError("Provider request timed out.") from error
        except OSError as error:
            raise ProviderTransportError("Provider request failed.") from error

        if len(payload) > MAX_PROVIDER_RESPONSE_BYTES:
            raise ProviderTransportError("Provider response exceeded the size limit.")
        return payload
