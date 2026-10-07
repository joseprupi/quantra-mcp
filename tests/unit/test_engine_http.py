import json

import httpx
import pytest

from quantra_mcp.backend.engine_http import EngineHttpBackend
from quantra_mcp.errors import EngineError, TransportError


def _backend(handler) -> EngineHttpBackend:  # type: ignore[no-untyped-def]
    return EngineHttpBackend("http://engine.test/", transport=httpx.MockTransport(handler))


async def test_post_sends_json_and_request_id_and_reads_headers() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["content_type"] = request.headers["content-type"]
        seen["rid"] = request.headers.get("x-request-id")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"dates": []},
            headers={"X-Quantra-Api-Version": "0.7.0", "X-Request-Id": "abc"},
        )

    b = _backend(handler)
    r = await b.post("/calendar-holidays", {"calendar": "TARGET"}, request_id="abc")
    assert seen == {
        "method": "POST",
        "url": "http://engine.test/calendar-holidays",
        "content_type": "application/json",
        "rid": "abc",
        "body": {"calendar": "TARGET"},
    }
    assert r.status == 200 and r.body == {"dates": []}
    assert r.api_version == "0.7.0" and r.request_id == "abc"
    await b.aclose()


async def test_non_2xx_raises_engine_error_verbatim() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "error": "curve not bootstrappable",
                "code": 3,
                "message": "curve not bootstrappable",
            },
            headers={"X-Quantra-Api-Version": "0.7.0"},
        )

    b = _backend(handler)
    with pytest.raises(EngineError) as exc:
        await b.post("/price-ois-swap", {})
    assert exc.value.status == 422
    assert exc.value.error == "curve not bootstrappable"
    assert exc.value.body["code"] == 3
    assert exc.value.headers["x-quantra-api-version"] == "0.7.0"


async def test_non_json_error_body_is_kept_as_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="Bad Gateway")

    b = _backend(handler)
    with pytest.raises(EngineError) as exc:
        await b.get("/meta")
    assert (
        exc.value.status == 502
        and exc.value.error == "Bad Gateway"
        and exc.value.body == "Bad Gateway"
    )


async def test_connection_failure_is_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    b = _backend(handler)
    with pytest.raises(TransportError) as exc:
        await b.health()
    assert "ConnectError" in exc.value.error and "/health" in exc.value.error
