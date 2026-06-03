from __future__ import annotations

from litestar import Litestar, Response, get
from litestar.middleware.base import DefineMiddleware
from litestar.testing import TestClient

from service_toolkit.web.etag import etag_middleware


@get("/thing")
async def get_thing() -> dict[str, str]:
    return {"id": "1", "name": "alpha"}


@get("/live")
async def get_live() -> Response[dict[str, str]]:
    # Live endpoint opts out of caching.
    return Response(
        {"online": "42"}, headers={"Cache-Control": "no-store"}
    )


@get("/empty", status_code=204)
async def get_empty() -> None:
    return None


def _app() -> Litestar:
    return Litestar(
        route_handlers=[get_thing, get_live, get_empty],
        middleware=[DefineMiddleware(etag_middleware)],
    )


def test_get_response_carries_etag() -> None:
    with TestClient(_app()) as client:
        resp = client.get("/thing")
        assert resp.status_code == 200
        assert resp.headers.get("etag")
        assert resp.json() == {"id": "1", "name": "alpha"}


def test_matching_if_none_match_returns_304() -> None:
    with TestClient(_app()) as client:
        first = client.get("/thing")
        etag = first.headers["etag"]

        second = client.get("/thing", headers={"If-None-Match": etag})
        assert second.status_code == 304
        assert second.content == b""
        assert second.headers.get("etag") == etag


def test_stale_if_none_match_returns_full_body() -> None:
    with TestClient(_app()) as client:
        resp = client.get("/thing", headers={"If-None-Match": '"deadbeef"'})
        assert resp.status_code == 200
        assert resp.json() == {"id": "1", "name": "alpha"}


def test_no_store_response_is_not_tagged() -> None:
    with TestClient(_app()) as client:
        resp = client.get("/live")
        assert resp.status_code == 200
        assert resp.headers.get("etag") is None


def test_empty_response_passes_through() -> None:
    with TestClient(_app()) as client:
        resp = client.get("/empty")
        assert resp.status_code == 204
        assert resp.headers.get("etag") is None
