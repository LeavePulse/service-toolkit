from __future__ import annotations

from typing import Any, cast

from service_toolkit.web.locale import (
    normalize_locale_tag,
    resolve_locale_from_request,
)


class FakeRequest:
    def __init__(
        self,
        *,
        headers: dict[str, str] | None = None,
        query_params: dict[str, str] | None = None,
    ) -> None:
        self.headers = headers or {}
        self.query_params = query_params or {}


def test_normalize_locale_tag_matches_exact_and_primary_tags() -> None:
    supported = ["en", "uk", "pt-br"]

    assert normalize_locale_tag("uk-UA", supported) == "uk"
    assert normalize_locale_tag("pt-BR", supported) == "pt-br"
    assert normalize_locale_tag("de-DE", supported) is None


def test_resolve_locale_from_request_prefers_query_param() -> None:
    request = cast(
        Any,
        FakeRequest(
            headers={"Accept-Language": "uk-UA, en;q=0.8"},
            query_params={"locale": "en-US"},
        ),
    )

    assert (
        resolve_locale_from_request(
            request,
            supported_locales=["en", "uk"],
            default_locale="uk",
            query_param="locale",
        )
        == "en"
    )


def test_resolve_locale_from_request_uses_accept_language_and_default() -> None:
    request = cast(
        Any,
        FakeRequest(headers={"Accept-Language": "de-DE, uk-UA;q=0.8"}),
    )

    assert (
        resolve_locale_from_request(
            request,
            supported_locales=["en", "uk"],
            default_locale="en",
        )
        == "uk"
    )
    assert (
        resolve_locale_from_request(
            None,
            supported_locales=["en", "uk"],
            default_locale="en",
        )
        == "en"
    )


def test_resolve_locale_from_request_accepts_lowercase_header_mapping() -> None:
    request = cast(
        Any,
        FakeRequest(headers={"accept-language": "uk-UA, en;q=0.8"}),
    )

    assert (
        resolve_locale_from_request(
            request,
            supported_locales=["en", "uk"],
            default_locale="en",
        )
        == "uk"
    )
