from __future__ import annotations

from typing import cast

import msgspec
import pytest

from service_toolkit.auth import JWKSCache, JWTVerifier, build_shared_jwt_verifier
from service_toolkit.auth import verifier as verifier_module


class _GatewayPayload(msgspec.Struct, kw_only=True):
    sub: str
    type: str | None = None
    server_id: str | None = None


class _DummyJWKSCache:
    async def get_key(self, kid: str) -> dict[str, object] | None:
        if kid == "known-kid":
            return {"kid": kid}
        return None


def test_shared_jwks_cache_reuses_instance_for_same_config() -> None:
    cache_a = JWKSCache.shared(
        url="https://auth.example/.well-known/jwks.json",
        ttl_seconds=300,
        timeout_seconds=5.0,
    )
    cache_b = JWKSCache.shared(
        url="https://auth.example/.well-known/jwks.json",
        ttl_seconds=300,
        timeout_seconds=5.0,
    )
    cache_c = JWKSCache.shared(
        url="https://auth.example/.well-known/jwks.json",
        ttl_seconds=600,
        timeout_seconds=5.0,
    )

    assert cache_a is cache_b
    assert cache_a is not cache_c


def test_build_shared_jwt_verifier_reuses_process_wide_jwks_cache() -> None:
    verifier_a = build_shared_jwt_verifier(
        jwks_url="https://auth.example/.well-known/jwks.json",
        jwks_ttl_seconds=300,
        http_timeout_seconds=5.0,
        issuer="leavepulse-auth",
        audience="leavepulse.api",
    )
    verifier_b = build_shared_jwt_verifier(
        jwks_url="https://auth.example/.well-known/jwks.json",
        jwks_ttl_seconds=300,
        http_timeout_seconds=5.0,
        issuer="leavepulse-auth",
        audience="leavepulse.api",
    )

    assert verifier_a is not verifier_b
    assert verifier_a._jwks_cache is verifier_b._jwks_cache


@pytest.mark.asyncio
async def test_jwt_verifier_supports_custom_payload_and_optional_claim_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decode_kwargs: dict[str, object] = {}

    monkeypatch.setattr(
        verifier_module.jwt,
        "get_unverified_header",
        lambda token: {"kid": "known-kid"},
    )

    def _fake_decode(*args: object, **kwargs: object) -> dict[str, object]:
        decode_kwargs.update(kwargs)
        return {"sub": "42", "server_id": "777", "type": None}

    monkeypatch.setattr(verifier_module.jwt, "decode", _fake_decode)

    verifier = JWTVerifier(
        jwks_cache=cast("JWKSCache", _DummyJWKSCache()),
        issuer=None,
        audience=None,
        payload_type=_GatewayPayload,
        allowed_types={"access", "ws_access", None},
    )

    payload = await verifier.verify("token")

    assert payload.server_id == "777"
    assert payload.type is None
    assert decode_kwargs["issuer"] is None
    assert decode_kwargs["audience"] is None
    assert decode_kwargs["options"] == {"verify_aud": False, "verify_iss": False}
