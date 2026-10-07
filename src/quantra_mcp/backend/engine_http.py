"""The engine JSON gateway over HTTP (``httpx``). The only backend in M1."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from quantra_mcp.backend.base import API_VERSION_HEADER, REQUEST_ID_HEADER, BackendResponse
from quantra_mcp.errors import EngineError, TransportError, extract_error_text

log = logging.getLogger(__name__)

_KEEP_HEADERS = (API_VERSION_HEADER, REQUEST_ID_HEADER, "content-type")


def _decode(response: httpx.Response) -> Any:
    text = response.text
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _headers(response: httpx.Response) -> dict[str, str]:
    return {k: response.headers[k] for k in _KEEP_HEADERS if k in response.headers}


class EngineHttpBackend:
    """One ``httpx.AsyncClient`` for the server's lifetime."""

    def __init__(
        self,
        base_url: str,
        timeout_s: float = 60.0,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        user_agent: str = "quantra-mcp",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout_s),
            transport=transport,
            headers={"User-Agent": user_agent},
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    async def post(
        self, endpoint: str, body: Any, request_id: str | None = None
    ) -> BackendResponse:
        headers = {"Content-Type": "application/json"}
        if request_id:
            headers["X-Request-Id"] = request_id
        content = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
        try:
            response = await self._client.post(endpoint, content=content, headers=headers)
        except httpx.HTTPError as exc:
            raise TransportError(
                f"{type(exc).__name__}: {exc} (POST {self._base_url}{endpoint})"
            ) from exc
        return self._finish("POST", endpoint, response)

    async def get(self, path: str) -> BackendResponse:
        try:
            response = await self._client.get(path)
        except httpx.HTTPError as exc:
            raise TransportError(
                f"{type(exc).__name__}: {exc} (GET {self._base_url}{path})"
            ) from exc
        return self._finish("GET", path, response)

    async def meta(self) -> BackendResponse:
        return await self.get("/meta")

    async def health(self) -> BackendResponse:
        return await self.get("/health")

    async def aclose(self) -> None:
        await self._client.aclose()

    def _finish(self, method: str, path: str, response: httpx.Response) -> BackendResponse:
        decoded = _decode(response)
        headers = _headers(response)
        log.debug("%s %s -> %s", method, path, response.status_code)
        if 200 <= response.status_code < 300:
            return BackendResponse(status=response.status_code, body=decoded, headers=headers)
        raise EngineError(
            status=response.status_code,
            error=extract_error_text(response.status_code, decoded, response.text),
            body=decoded,
            headers=headers,
        )
