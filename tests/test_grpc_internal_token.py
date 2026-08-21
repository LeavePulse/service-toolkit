"""The internal-token gate, and the second credential a service may accept.

The alternate verifier exists so a service can admit callers it authenticates
its own way — the control-plane's per-host agent tokens — without every service
learning about them. These pin the part that matters: it must not loosen the
default, and it must only ever be consulted after the shared secret fails.
"""

from __future__ import annotations

import pytest

from service_toolkit.grpc.interceptors import InternalTokenInterceptor


class _Details:
    def __init__(self, method: str, token: str | None) -> None:
        self.method = method
        self.invocation_metadata = (
            [("x-internal-token", token)] if token is not None else []
        )


async def _continuation(_details: object) -> str:
    return "handler"


async def _refused(interceptor: InternalTokenInterceptor, token: str | None) -> bool:
    """True when the call was rejected rather than passed to the handler."""
    result = await interceptor.intercept_service(
        _continuation, _Details("/pkg.Svc/Method", token)
    )
    return result != "handler"


@pytest.mark.asyncio
async def test_shared_token_is_admitted() -> None:
    interceptor = InternalTokenInterceptor("secret")
    assert not await _refused(interceptor, "secret")


@pytest.mark.asyncio
async def test_wrong_token_is_refused() -> None:
    interceptor = InternalTokenInterceptor("secret")
    assert await _refused(interceptor, "wrong")


@pytest.mark.asyncio
async def test_missing_token_is_refused() -> None:
    interceptor = InternalTokenInterceptor("secret")
    assert await _refused(interceptor, None)


@pytest.mark.asyncio
async def test_exempt_method_needs_no_token() -> None:
    interceptor = InternalTokenInterceptor("secret", exempt_methods=["Svc/Method"])
    assert not await _refused(interceptor, None)


@pytest.mark.asyncio
async def test_no_verifier_means_shared_token_only() -> None:
    """The default must not widen just because the parameter exists."""
    interceptor = InternalTokenInterceptor("secret", alternate_verifier=None)
    assert await _refused(interceptor, "some-other-credential")


@pytest.mark.asyncio
async def test_alternate_verifier_can_admit_its_own_credential() -> None:
    seen: list[tuple[str, str]] = []

    async def verifier(method: str, token: str):  # type: ignore[no-untyped-def]
        seen.append((method, token))
        return lambda handler: handler

    interceptor = InternalTokenInterceptor("secret", alternate_verifier=verifier)

    assert not await _refused(interceptor, "per-host-token")
    assert seen == [("/pkg.Svc/Method", "per-host-token")]


@pytest.mark.asyncio
async def test_alternate_verifier_may_still_refuse() -> None:
    async def verifier(_method: str, _token: str):  # type: ignore[no-untyped-def]
        return None

    interceptor = InternalTokenInterceptor("secret", alternate_verifier=verifier)
    assert await _refused(interceptor, "not-a-real-one")


@pytest.mark.asyncio
async def test_shared_token_skips_the_verifier() -> None:
    """It is a fallback, not a second gate: a service caller must not pay a
    lookup — nor be re-judged by a verifier that knows nothing about it."""
    called = False

    async def verifier(_method: str, _token: str):  # type: ignore[no-untyped-def]
        nonlocal called
        called = True
        return None

    interceptor = InternalTokenInterceptor("secret", alternate_verifier=verifier)

    assert not await _refused(interceptor, "secret")
    assert called is False


@pytest.mark.asyncio
async def test_verifier_wraps_the_handler_it_admits() -> None:
    """What the verifier returns is applied to the handler, so it can bind
    whatever it proved for the call's duration."""

    async def verifier(_method: str, _token: str):  # type: ignore[no-untyped-def]
        return lambda handler: f"wrapped:{handler}"

    interceptor = InternalTokenInterceptor("secret", alternate_verifier=verifier)

    result = await interceptor.intercept_service(
        _continuation, _Details("/pkg.Svc/Method", "per-host-token")
    )
    assert result == "wrapped:handler"


@pytest.mark.asyncio
async def test_empty_shared_token_is_rejected_at_construction() -> None:
    """A blank secret would compare equal to a blank header."""
    with pytest.raises(ValueError):
        InternalTokenInterceptor("")


@pytest.mark.asyncio
async def test_health_and_reflection_bypass_the_gate() -> None:
    interceptor = InternalTokenInterceptor("secret")
    for method in ("/grpc.health.v1.Health/Check", "/grpc.reflection.v1.X/Y"):
        result = await interceptor.intercept_service(
            _continuation, _Details(method, None)
        )
        assert result == "handler"
