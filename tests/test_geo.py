"""Locating an IP, and what happens when the database is not there.

The absent-database path matters more than the present one here: the mmdb is
fetched at runtime and is legitimately missing on a fresh volume, in tests, and
on any deployment that turned the download off. Every caller is written to
degrade rather than fail, so these check that it can.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from service_toolkit.geo import fetch

from service_toolkit.geo import (
    Location,
    distance_km,
    locate,
    reset_reader_cache,
    resolve_geoip,
)


@pytest.fixture(autouse=True)
def _no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the lookup at a directory with no mmdb in it."""
    monkeypatch.setenv("GEOIP_DATA_DIR", "/nonexistent/geoip")
    reset_reader_cache()
    yield
    reset_reader_cache()


def test_a_missing_database_locates_nothing() -> None:
    """The mmdb is optional at runtime, so its absence cannot be an error."""
    assert locate("81.2.69.142") == Location()


def test_an_empty_address_locates_nothing() -> None:
    assert locate("") == Location()
    assert locate(None) == Location()


def test_the_session_shaped_answer_survives_a_missing_database() -> None:
    """Sessions fall back to showing the raw IP, which needs (None, None)."""
    assert resolve_geoip("81.2.69.142") == (None, None)


def test_distance_between_two_known_points() -> None:
    """Kyiv to Warsaw, roughly 690km. Checked loosely: this ranks candidates,
    it does not navigate."""
    kyiv = Location(latitude=50.45, longitude=30.52)
    warsaw = Location(latitude=52.23, longitude=21.01)
    measured = distance_km(kyiv, warsaw)
    assert measured is not None
    assert 650 < measured < 730


def test_distance_to_itself_is_zero() -> None:
    point = Location(latitude=50.45, longitude=30.52)
    assert distance_km(point, point) == pytest.approx(0.0, abs=1e-6)


def test_an_unlocated_point_has_no_distance() -> None:
    """None rather than a large number, so callers can tell "unknown" from
    "far": a known-country machine must not sort behind the world for being
    imprecisely placed."""
    known = Location(latitude=50.45, longitude=30.52)
    assert distance_km(known, Location(country="PL")) is None
    assert distance_km(Location(), known) is None


def test_a_country_without_coordinates_is_still_a_location() -> None:
    """GeoLite2 knows the country of far more addresses than the position."""
    place = Location(country="UA")
    assert not place.has_coordinates
    assert place.country == "UA"


# ── fetching the database ─────────────────────────────────────────────────────


def test_the_download_can_be_turned_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A deployment that supplies its own mmdb must not have one fetched over it."""
    monkeypatch.setenv("GEOIP_DOWNLOAD_ENABLED", "false")
    monkeypatch.setenv("GEOIP_DATA_DIR", str(tmp_path))

    assert fetch.main() == 0
    assert list(tmp_path.iterdir()) == []


def test_a_fresh_copy_is_not_downloaded_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The file is ~66MB and changes weekly; re-fetching it every boot is waste."""
    existing = tmp_path / "GeoLite2-City.mmdb"
    existing.write_bytes(b"not really a database")
    monkeypatch.setenv("GEOIP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GEOIP_MAX_AGE_DAYS", "30")

    def _refuse(*_a: object, **_kw: object) -> object:
        raise AssertionError("a fresh copy must not be re-downloaded")

    monkeypatch.setattr(fetch.httpx, "stream", _refuse)

    assert fetch.main() == 0
    assert existing.read_bytes() == b"not really a database"


def test_a_failed_download_keeps_the_copy_already_there(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unreachable network must not cost a database that already works."""
    existing = tmp_path / "GeoLite2-City.mmdb"
    existing.write_bytes(b"the old database")
    os.utime(existing, (0, 0))  # old enough to be refreshed
    monkeypatch.setenv("GEOIP_DATA_DIR", str(tmp_path))

    def _explode(*_a: object, **_kw: object) -> object:
        raise OSError("network is unreachable")

    monkeypatch.setattr(fetch.httpx, "stream", _explode)

    assert fetch.main() == 0, "a failed fetch is never a failed boot"
    assert existing.read_bytes() == b"the old database"


def test_a_failed_download_with_nothing_there_is_still_not_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Callers degrade without the database; refusing to boot helps nobody."""
    monkeypatch.setenv("GEOIP_DATA_DIR", str(tmp_path / "fresh"))

    def _explode(*_a: object, **_kw: object) -> object:
        raise OSError("network is unreachable")

    monkeypatch.setattr(fetch.httpx, "stream", _explode)

    assert fetch.main() == 0
