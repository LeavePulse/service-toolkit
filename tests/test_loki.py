from __future__ import annotations

import httpx
import pytest

from service_toolkit.observability.loki import LokiClient, LokiQueryError


def _client(handler: httpx.MockTransport) -> LokiClient:
    return LokiClient(
        base_url="http://loki.test",
        client=httpx.AsyncClient(transport=handler, base_url="http://loki.test"),
    )


@pytest.mark.asyncio
async def test_query_range_flattens_and_deterministically_merges_streams() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/loki/api/v1/query_range"
        assert request.url.params["start"] == "10"
        assert request.url.params["end"] == "20"
        assert request.url.params["limit"] == "2"
        assert request.url.params["direction"] == "backward"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "streams",
                    "result": [
                        {
                            "stream": {"container": "a", "host_id": "one"},
                            "values": [["12", "from-a"]],
                        },
                        {
                            "stream": {"container": "b", "host_id": "one"},
                            "values": [["19", "from-b"]],
                        },
                    ],
                },
            },
        )

    client = _client(httpx.MockTransport(handle))
    result = await client.query_range(
        query='{host_id="one"}', start_ns=10, end_ns=20, limit=2
    )

    assert [line.line for line in result.lines] == ["from-b", "from-a"]
    assert result.lines[0].labels == {"container": "b", "host_id": "one"}
    await client._client.aclose()


@pytest.mark.asyncio
async def test_query_range_rejects_non_stream_loki_response() -> None:
    client = _client(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"status": "success", "data": {"resultType": "vector", "result": []}},
            )
        )
    )

    with pytest.raises(LokiQueryError, match="non-stream"):
        await client.query_range(query="{job=\"x\"}", start_ns=1, end_ns=2, limit=1)

    await client._client.aclose()


@pytest.mark.asyncio
async def test_query_range_rejects_invalid_bounds_before_request() -> None:
    client = _client(httpx.MockTransport(lambda _request: pytest.fail("called")))

    with pytest.raises(ValueError, match="start_ns"):
        await client.query_range(query="{job=\"x\"}", start_ns=0, end_ns=2, limit=1)

    await client._client.aclose()
