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
    return Response({"online": "42"}, headers={"Cache-Control": "no-store"})


@get("/empty", status_code=204)
async def get_empty() -> None:
    return None


@get("/varying")
async def get_varying() -> Response[dict[str, str]]:
    # A handler that states the fields its representation depends on, and a
    # caching policy of its own. Both have to reach a client through a 304.
    return Response(
        {"id": "2"},
        headers={"Vary": "Authorization", "Cache-Control": "private, max-age=30"},
    )


def _app(exclude: tuple[str, ...] = ()) -> Litestar:
    return Litestar(
        route_handlers=[get_thing, get_live, get_empty, get_varying],
        middleware=[DefineMiddleware(etag_middleware, exclude=exclude)],
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


def test_excluded_path_is_not_tagged() -> None:
    with TestClient(_app(exclude=(r"^/thing$",))) as client:
        resp = client.get("/thing")
        assert resp.status_code == 200
        assert resp.headers.get("etag") is None


# ── What a 304 carries, and why a validator alone is not enough ─────────────


def test_a_tagged_response_states_a_caching_policy() -> None:
    """A validator without a policy is the half-answer that lets a cache decide
    for itself.

    RFC 9111 §4.2.2 permits a heuristic lifetime on a response that gives no
    explicit one, so an `ETag` with no `Cache-Control` invites an intermediary —
    or a browser — to start keeping a validator of its own. A client whose SDK
    keeps one too then has two caches for one URL, and its store can be handed a
    `304` for a body it never held: a resource that exists, read as absent.
    """
    with TestClient(_app()) as client:
        policy = client.get("/thing").headers.get("cache-control", "")

        assert "private" in policy, (
            "an operator's answer is not an intermediary's to hold"
        )
        assert "no-cache" in policy, "a validator is there to be revalidated against"


def test_a_handler_that_states_its_own_policy_keeps_it() -> None:
    """A default, not an override. A handler that has decided how long its
    answer stays good knows something this middleware does not."""
    with TestClient(_app()) as client:
        assert client.get("/varying").headers["cache-control"] == "private, max-age=30"


def test_a_304_repeats_the_fields_the_200_would_have_sent() -> None:
    """RFC 9110 §15.4.5. A cache UPDATES its stored response from a 304, so a
    field left out of one is a field the stored copy keeps stale — and
    `Cache-Control` absent from a 304 says "no policy" to everything on the
    path, which is the heuristic-caching invitation all over again."""
    with TestClient(_app()) as client:
        full = client.get("/varying")
        not_modified = client.get(
            "/varying", headers={"If-None-Match": full.headers["etag"]}
        )

        assert not_modified.status_code == 304
        for field in ("cache-control", "vary", "date"):
            assert not_modified.headers.get(field) == full.headers.get(field), field


def test_a_304_does_not_describe_a_body_it_is_not_sending() -> None:
    """The other half of the same rule. `Content-Length` and `Content-Type`
    describe a representation the 304 does not carry, and a client that believes
    them waits for bytes that never arrive."""
    with TestClient(_app()) as client:
        etag = client.get("/thing").headers["etag"]
        resp = client.get("/thing", headers={"If-None-Match": etag})

        assert resp.status_code == 304
        assert resp.headers.get("content-length") in (None, "0")
        assert resp.headers.get("content-type") is None


def test_the_default_policy_reaches_the_304_as_well() -> None:
    """The half the first version missed, found on the live fleet rather than
    here: the 200 came back `private, no-cache` and the 304 to the same URL
    carried `date` and `etag` alone.

    A 304 REPEATS the fields the response already had, so a default added after
    the two paths diverge never appears on it — and the 304 is the response that
    reaches a cache most often. "No policy at all" is exactly what invites the
    heuristic (RFC 9111 §4.2.2) this default exists to prevent.

    Distinct from the carried-fields test above, which uses a handler that states
    its OWN policy and therefore has one to carry. This one has none.
    """
    with TestClient(_app()) as client:
        full = client.get("/thing")
        not_modified = client.get(
            "/thing", headers={"If-None-Match": full.headers["etag"]}
        )

        assert not_modified.status_code == 304
        assert not_modified.headers.get("cache-control") == (
            full.headers.get("cache-control")
        )
        assert "no-cache" in not_modified.headers.get("cache-control", "")
