from __future__ import annotations

from typing import Any, cast

from service_toolkit.web.request_ip import resolve_client_ip


class FakeClient:
    def __init__(self, host: str | None) -> None:
        self.host = host


class FakeRequest:
    def __init__(
        self,
        *,
        headers: dict[str, str] | None = None,
        host: str | None = "127.0.0.1",
    ) -> None:
        self.headers = headers or {}
        self.client = FakeClient(host)


def test_resolve_client_ip_prefers_cloudflare_header() -> None:
    request = cast(
        Any,
        FakeRequest(
            headers={"cf-connecting-ip": "203.0.113.10"},
            host="172.19.0.1",
        ),
    )

    assert resolve_client_ip(request) == "203.0.113.10"


def test_resolve_client_ip_uses_first_x_forwarded_for_value() -> None:
    request = cast(
        Any,
        FakeRequest(
            headers={"x-forwarded-for": "198.51.100.7, 172.19.0.1"},
            host="172.19.0.1",
        ),
    )

    assert resolve_client_ip(request) == "198.51.100.7"


def test_resolve_client_ip_falls_back_to_socket_host() -> None:
    request = cast(Any, FakeRequest(headers={}, host="192.0.2.5"))

    assert resolve_client_ip(request) == "192.0.2.5"


def test_resolve_client_ip_skips_invalid_forwarded_values() -> None:
    request = cast(
        Any,
        FakeRequest(
            headers={
                "x-forwarded-for": "unknown, not-an-ip",
                "forwarded": 'for=invalid;proto=https, for="[2001:db8::1]"',
            },
            host="172.19.0.1",
        ),
    )

    assert resolve_client_ip(request) == "2001:db8::1"
