from __future__ import annotations

import dataclasses
import gzip
import random
import os
import threading
import time
from collections.abc import AsyncIterator
from compression import zstd

import pytest

from litestar import Litestar, Response, get
from litestar.params import FromPath, FromQuery
from litestar.response import Stream
from litestar.testing import TestClient

from service_toolkit.web import compression
from service_toolkit.web.compression import CompressionMiddleware, negotiate
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


_RNG = random.Random(7)
_VOCABULARY = ["".join(_RNG.choices("abcdefghijklmnop", k=_RNG.randint(3, 9))) for _ in range(400)]
WORDS = " ".join(_RNG.choices(_VOCABULARY, k=20000))


@get("/tagged")
async def get_tagged() -> Response[str]:
    return Response(
        WORDS,
        media_type="text/javascript",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": '"tagged-1"',
        },
    )


def test_immutable_asset_is_refined_into_cache() -> None:
    app = Litestar(route_handlers=[get_tagged], middleware=Transfer().middleware())
    with TestClient(app) as client:
        _, fast = _raw(client, "/tagged", "zstd")
        for _ in range(100):
            _, best = _raw(client, "/tagged", "zstd")
            if len(best) < len(fast):
                break
            time.sleep(0.02)
        assert len(best) < len(fast)
        assert zstd.decompress(best) == WORDS.encode()
        _, again = _raw(client, "/tagged", "zstd")
        assert again == best


def test_split_accept_encoding_and_q_out_of_range() -> None:
    assert negotiate(b"gzip;q=7, zstd;q=0.5", (b"zstd", b"gzip")) == b"gzip"
    assert negotiate(b"zstd;q=nan, gzip;q=0.1", (b"zstd", b"gzip")) == b"gzip"
    with _client() as client, client.stream(
        "GET",
        "/big",
        headers=[("Accept-Encoding", "identity;q=0.1"), ("Accept-Encoding", "gzip")],
    ) as resp:
        assert resp.headers["content-encoding"] == "gzip"


@get("/ranged")
async def get_ranged() -> Response[str]:
    return Response("y" * 8192, media_type="text/plain", headers={"Accept-Ranges": "bytes"})


def test_encoded_response_drops_accept_ranges_and_304_is_weak() -> None:
    app = Litestar(
        route_handlers=[get_ranged, get_big], middleware=Transfer().middleware()
    )
    with TestClient(app) as client:
        resp, _ = _raw(client, "/ranged", "zstd")
        assert "accept-ranges" not in resp.headers
        tag = client.get("/big", headers={"Accept-Encoding": "zstd"}).headers["etag"]
        again = client.get("/big", headers={"Accept-Encoding": "zstd", "If-None-Match": tag})
        assert again.status_code == 304
        assert again.headers["etag"] == tag


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


@get("/bundle")
async def get_bundle(v: FromQuery[str]) -> Response[str]:
    return Response(
        v * 8192,
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


def _settle(client: TestClient[Litestar], path: str) -> None:
    """Let background best-level passes for ``path`` finish."""
    for _ in range(50):
        _raw(client, path, "zstd")
        time.sleep(0.02)


def test_immutable_without_validator_is_never_served_another_body() -> None:
    app = Litestar(
        route_handlers=[get_bundle],
        middleware=Transfer(conditional=False).middleware(),
    )
    with TestClient(app) as client:
        _settle(client, "/bundle?v=a")
        _, body = _raw(client, "/bundle?v=b", "zstd")
        assert zstd.decompress(body) == b"b" * 8192


def test_not_modified_repeats_vary() -> None:
    with _client() as client:
        full, _ = _raw(client, "/big", "gzip")
        again = client.get(
            "/big", headers={"Accept-Encoding": "gzip", "If-None-Match": full.headers["etag"]}
        )
        assert again.status_code == 304
        assert "accept-encoding" in again.headers.get("vary", "").lower()


HALF_COMPRESSIBLE = os.urandom(8192).hex().encode()


@get("/oversized")
async def get_oversized() -> Response[bytes]:
    return Response(
        HALF_COMPRESSIBLE,
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


def test_asset_the_cache_cannot_hold_is_not_refined_on_every_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[bytes, ...]] = []
    refine = CompressionMiddleware.refine

    def spy(self: CompressionMiddleware, cache, key, codec, raw) -> None:  # type: ignore[no-untyped-def]
        calls.append(key)
        refine(self, cache, key, codec, raw)

    monkeypatch.setattr(CompressionMiddleware, "refine", spy)
    app = Litestar(
        route_handlers=[get_oversized],
        middleware=Transfer(immutable_cache_bytes=16 * 1024).middleware(),
    )
    with TestClient(app) as client:
        for _ in range(5):
            _, body = _raw(client, "/oversized", "zstd")
            assert zstd.decompress(body) == HALF_COMPRESSIBLE
            time.sleep(0.05)
    assert calls == []


@get("/asset/{n:int}")
async def get_numbered_asset(n: FromPath[int]) -> Response[str]:
    return Response(
        str(n) * 8192,
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


def test_best_level_passes_run_one_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    running = 0
    peak = 0
    lock = threading.Lock()
    codec = compression._CODECS[b"zstd"]

    def slow_whole(data: bytes, level: int) -> bytes:
        nonlocal running, peak
        if level != codec.best:
            return codec.whole(data, level)
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.1)
        with lock:
            running -= 1
        return codec.whole(data, level)

    monkeypatch.setitem(
        compression._CODECS, b"zstd", dataclasses.replace(codec, whole=slow_whole)
    )
    app = Litestar(route_handlers=[get_numbered_asset], middleware=Transfer().middleware())
    with TestClient(app) as client:
        for n in range(4):
            _raw(client, f"/asset/{n}", "zstd")
        time.sleep(0.6)
    assert peak == 1
