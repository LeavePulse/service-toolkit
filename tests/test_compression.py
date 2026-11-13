from __future__ import annotations

import gzip
from collections.abc import AsyncIterator
from compression import zstd

from litestar import Litestar, Response, get
from litestar.response import Stream
from litestar.testing import TestClient

from service_toolkit.web.compression import negotiate
from service_toolkit.web.transfer import Transfer

BIG = {"items": [{"id": i, "status": "online"} for i in range(400)]}


@get("/big")
async def get_big() -> dict[str, list[dict[str, object]]]:
    return BIG


@get("/small")
async def get_small() -> dict[str, str]:
    return {"id": "1"}


@get("/png")
async def get_png() -> Response[bytes]:
    return Response(b"\x89PNG" + b"0" * 4096, media_type="image/png")


@get("/asset")
async def get_asset() -> Response[str]:
    return Response(
        "x" * 8192,
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@get("/events")
async def get_events() -> Stream:
    async def gen() -> AsyncIterator[bytes]:
        for i in range(3):
            yield f"data: {i}\n\n".encode()

    return Stream(gen(), media_type="text/event-stream")


def _client(policy: Transfer = Transfer()) -> TestClient[Litestar]:
    app = Litestar(
        route_handlers=[get_big, get_small, get_png, get_asset, get_events],
        middleware=policy.middleware(),
    )
    return TestClient(app)


def _raw(client: TestClient[Litestar], path: str, encoding: str, **headers: str):
    with client.stream(
        "GET", path, headers={"Accept-Encoding": encoding, **headers}
    ) as resp:
        return resp, b"".join(resp.iter_raw())


def test_negotiate_prefers_zstd_and_honours_q() -> None:
    have = (b"zstd", b"gzip")
    assert negotiate(b"gzip, zstd", have) == b"zstd"
    assert negotiate(b"zstd;q=0.5, gzip", have) == b"gzip"
    assert negotiate(b"zstd;q=0, gzip;q=0", have) is None
    assert negotiate(b"identity", have) is None
    assert negotiate(b"*", have) == b"zstd"
    assert negotiate(None, have) is None


def test_json_is_compressed_and_round_trips() -> None:
    with _client() as client:
        resp, body = _raw(client, "/big", "zstd")
        assert resp.headers["content-encoding"] == "zstd"
        assert "accept-encoding" in resp.headers["vary"].lower()
        assert len(body) < len(client.get("/big", headers={"Accept-Encoding": "identity"}).content)
        assert zstd.decompress(body).startswith(b'{"items"')


def test_etag_is_shared_across_encodings_and_304_still_works() -> None:
    with _client() as client:
        resp, _ = _raw(client, "/big", "gzip")
        etag = resp.headers["etag"]
        assert etag.startswith('W/"')
        plain = client.get("/big", headers={"Accept-Encoding": "identity"})
        assert plain.headers["etag"] == etag.removeprefix("W/")
        again = client.get("/big", headers={"Accept-Encoding": "gzip", "If-None-Match": etag})
        assert again.status_code == 304


def test_small_and_binary_bodies_pass_through() -> None:
    with _client() as client:
        small, _ = _raw(client, "/small", "zstd")
        assert "content-encoding" not in small.headers
        png, _ = _raw(client, "/png", "zstd")
        assert "content-encoding" not in png.headers


def test_stream_is_compressed_per_chunk() -> None:
    with _client() as client:
        resp, body = _raw(client, "/events", "gzip")
        assert resp.headers["content-encoding"] == "gzip"
        assert gzip.decompress(body) == b"data: 0\n\ndata: 1\n\ndata: 2\n\n"


def test_immutable_asset_is_served_from_cache() -> None:
    with _client() as client:
        first, a = _raw(client, "/asset", "zstd")
        second, b = _raw(client, "/asset", "zstd")
        assert a == b
        assert int(second.headers["content-length"]) == len(b)
        assert zstd.decompress(b) == b"x" * 8192


def test_raw_paths_and_disabled_layers() -> None:
    with _client(Transfer(raw=(r"^/big$",))) as client:
        resp, _ = _raw(client, "/big", "zstd")
        assert "content-encoding" not in resp.headers
    with _client(Transfer(compress=False, conditional=False)) as client:
        resp, _ = _raw(client, "/big", "zstd")
        assert "content-encoding" not in resp.headers
        assert "etag" not in resp.headers


def test_brotli_when_installed() -> None:
    import brotli

    with _client() as client:
        resp, body = _raw(client, "/big", "br")
        assert resp.headers["content-encoding"] == "br"
        assert brotli.decompress(body).startswith(b'{"items"')
