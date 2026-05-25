from __future__ import annotations

from service_toolkit.web.auth import (
    extract_bearer_token,
    extract_internal_token,
    internal_token_matches,
)


def test_extract_bearer_token_accepts_case_insensitive_scheme() -> None:
    assert extract_bearer_token({"Authorization": "bearer test.jwt"}) == "test.jwt"


def test_extract_bearer_token_rejects_other_schemes() -> None:
    assert extract_bearer_token({"Authorization": "Basic abc"}) is None


def test_extract_internal_token_prefers_bearer_header() -> None:
    headers = {
        "Authorization": "Bearer from-auth",
        "X-Internal-Token": "from-internal",
    }

    assert extract_internal_token(headers) == "from-auth"


def test_extract_internal_token_falls_back_to_internal_header() -> None:
    assert extract_internal_token({"x-internal-token": " secret "}) == "secret"


def test_internal_token_matches_uses_constant_time_compare() -> None:
    assert internal_token_matches({"X-Internal-Token": "secret"}, "secret")
    assert not internal_token_matches({"X-Internal-Token": "wrong"}, "secret")
    assert not internal_token_matches({"X-Internal-Token": "secret"}, "")
