"""Shared fixtures: a fake backend with canned answers, and the app built on it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from quantra_mcp.backend.base import BackendResponse
from quantra_mcp.config import Settings
from quantra_mcp.errors import EngineError, TransportError
from quantra_mcp.server import build_server

EXAMPLES = Path(__file__).resolve().parents[1] / "src" / "quantra_mcp" / "examples"

CANNED_HOLIDAYS = {
    "start_date": "2024-04-29",
    "end_date": "2024-06-21",
    "dates": ["2024-06-14"],
    "count": 1,
}
CANNED_META = {"openapi_version": "0.7.0", "products": ["OisSwap"], "endpoints": ["POST /meta"]}


class FakeBackend:
    """Records every call; answers from ``responses`` keyed by endpoint."""

    def __init__(self, responses: dict[str, Any] | None = None, api_version: str = "0.7.0") -> None:
        self.responses: dict[str, Any] = {
            "/meta": CANNED_META,
            "/health": {"status": "healthy"},
            "/calendar-holidays": CANNED_HOLIDAYS,
            "/calendar-business-days": {"dates": ["2024-05-02", "2024-05-03"], "count": 2},
            "/calendar-advance": {"input_date": "2024-05-01", "advanced_date": "2024-05-02"},
        }
        if responses:
            self.responses.update(responses)
        self.api_version = api_version
        self.calls: list[tuple[str, str, Any, str | None]] = []
        self.fail_with: Exception | None = None

    @property
    def base_url(self) -> str:
        return "http://fake"

    async def post(
        self, endpoint: str, body: Any, request_id: str | None = None
    ) -> BackendResponse:
        self.calls.append(("POST", endpoint, body, request_id))
        if self.fail_with is not None:
            raise self.fail_with
        if endpoint not in self.responses:
            raise EngineError(404, f"Cannot POST {endpoint}", {"error": f"Cannot POST {endpoint}"})
        return BackendResponse(
            200,
            json.loads(json.dumps(self.responses[endpoint])),
            {"x-quantra-api-version": self.api_version, "x-request-id": request_id or ""},
        )

    async def get(self, path: str) -> BackendResponse:
        self.calls.append(("GET", path, None, None))
        if self.fail_with is not None:
            raise self.fail_with
        return BackendResponse(
            200, self.responses[path], {"x-quantra-api-version": self.api_version}
        )

    async def meta(self) -> BackendResponse:
        return await self.get("/meta")

    async def health(self) -> BackendResponse:
        return await self.get("/health")

    async def aclose(self) -> None:
        return None


@pytest.fixture
def fake_backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def app(fake_backend: FakeBackend):  # type: ignore[no-untyped-def]
    return build_server(Settings(engine_url="http://fake"), backend=fake_backend)


@pytest.fixture
def ois_example() -> dict[str, Any]:
    return json.loads((EXAMPLES / "sofr-ois-swap-request.json").read_text())


@pytest.fixture
def bootstrap_example() -> dict[str, Any]:
    return json.loads((EXAMPLES / "sofr-bootstrap-request.json").read_text())


__all__ = ["EngineError", "FakeBackend", "TransportError"]
