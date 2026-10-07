"""The backend protocol every tool is written against."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

API_VERSION_HEADER = "x-quantra-api-version"
REQUEST_ID_HEADER = "x-request-id"


@dataclass(slots=True)
class BackendResponse:
    """A 2xx answer: decoded JSON body plus the headers we care about."""

    status: int
    body: Any
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def api_version(self) -> str | None:
        return self.headers.get(API_VERSION_HEADER)

    @property
    def request_id(self) -> str | None:
        return self.headers.get(REQUEST_ID_HEADER)


class Backend(Protocol):
    """What a backend must provide.

    ``post`` raises :class:`~quantra_mcp.errors.EngineError` on a non-2xx
    status (engine text verbatim) and
    :class:`~quantra_mcp.errors.TransportError` when the engine is unreachable.
    """

    @property
    def base_url(self) -> str: ...

    async def post(
        self, endpoint: str, body: Any, request_id: str | None = None
    ) -> BackendResponse: ...

    async def get(self, path: str) -> BackendResponse: ...

    async def meta(self) -> BackendResponse: ...

    async def health(self) -> BackendResponse: ...

    async def aclose(self) -> None: ...
