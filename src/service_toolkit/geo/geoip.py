"""Resolve an IP to a place, using a local MaxMind GeoLite2 City database.

The mmdb is provided at runtime rather than built in (it is ~66MB and updates
weekly); when it is missing the reader is simply absent and every lookup
answers "unknown". The reader is opened lazily and cached for the process,
because opening it per call would read the file header on every request.

Configuration is read from the environment rather than a settings object: this
module is shared by services whose settings classes have nothing in common, and
the two values involved are paths with sane defaults.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import only for type checkers
    import geoip2.database

logger = logging.getLogger(__name__)

#: Where the mmdb is looked for, and what it is called. Environment rather than
#: a settings object: the services sharing this have unrelated settings classes.
_DATA_DIR_ENV = "GEOIP_DATA_DIR"
_DB_NAME_ENV = "GEOIP_CITY_DB_FILENAME"
_DEFAULT_DATA_DIR = "/data/geoip"
_DEFAULT_DB_NAME = "GeoLite2-City.mmdb"

#: Mean radius of the Earth. Distances here are used to rank candidates, not to
#: navigate, so a sphere is more precision than the question needs.
_EARTH_RADIUS_KM = 6371.0

_reader: geoip2.database.Reader | None = None
_reader_lock = Lock()
_reader_loaded = False


@dataclass(frozen=True, slots=True)
class Location:
    """Where an address is, as far as the database knows.

    Every field is optional and independently so. GeoLite2 knows the country of
    almost every routable address, the city of most, and the coordinates of
    fewer — a caller that needs coordinates has to cope with not having them
    for an address whose country is perfectly well known.
    """

    city: str | None = None
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None


#: The answer for an address nothing is known about. Shared rather than
#: constructed per call: it is returned from the hot path of every lookup that
#: misses, which on a private-addressed fleet is most of them.
_UNKNOWN = Location()


def _db_path() -> Path:
    data_dir = os.environ.get(_DATA_DIR_ENV) or _DEFAULT_DATA_DIR
    name = os.environ.get(_DB_NAME_ENV) or _DEFAULT_DB_NAME
    return Path(data_dir) / name


def _get_reader() -> geoip2.database.Reader | None:
    """Open the City reader once; return None if the mmdb is not available."""
    global _reader, _reader_loaded
    if _reader_loaded:
        return _reader
    with _reader_lock:
        if _reader_loaded:
            return _reader
        path = _db_path()
        try:
            if path.is_file():
                import geoip2.database

                _reader = geoip2.database.Reader(str(path))
        except Exception:
            logger.warning("Failed to open GeoIP database at %s", path, exc_info=True)
            _reader = None
        _reader_loaded = True
    return _reader


def locate(ip_address: str | None) -> Location:
    """Everything known about where *ip_address* is.

    Never raises. A missing database, a private or unroutable address, or a
    malformed one all answer with an empty :class:`Location` — which every
    caller must handle anyway, since the database is optional at runtime.
    """
    if not ip_address:
        return _UNKNOWN
    reader = _get_reader()
    if reader is None:
        return _UNKNOWN
    try:
        import geoip2.errors

        resp = reader.city(ip_address)
    except geoip2.errors.AddressNotFoundError, ValueError:
        # Private/unroutable/unknown address — expected, not worth logging. Our
        # own fleet talks over 10.x, so this is the ordinary case, not a fault.
        return _UNKNOWN
    except Exception:
        logger.warning("GeoIP lookup failed for %s", ip_address, exc_info=True)
        return _UNKNOWN
    return Location(
        city=resp.city.name or None,
        country=resp.country.iso_code or None,
        latitude=resp.location.latitude,
        longitude=resp.location.longitude,
    )


def resolve_geoip(ip_address: str | None) -> tuple[str | None, str | None]:
    """``(city, country_iso)`` for an IP, or ``(None, None)``.

    The older, narrower shape of :func:`locate`, kept because sessions store
    exactly these two columns and reach for them by name.
    """
    place = locate(ip_address)
    return place.city, place.country


def distance_km(a: Location, b: Location) -> float | None:
    """Great-circle distance between two located points, or None.

    None when either side has no coordinates, which is a real and common state
    — GeoLite2 knows the country of far more addresses than it knows the
    position of. Callers rank on it, so "unknown" has to be distinguishable
    from "far away": treating a missing distance as a large one would sort a
    nearby machine to the back for the crime of being imprecisely located.
    """
    if not (a.has_coordinates and b.has_coordinates):
        return None
    lat1, lon1 = math.radians(a.latitude), math.radians(a.longitude)  # type: ignore[arg-type]
    lat2, lon2 = math.radians(b.latitude), math.radians(b.longitude)  # type: ignore[arg-type]
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, h)))


def reset_reader_cache() -> None:
    """Drop the cached reader (used by tests; reader reopens on next call)."""
    global _reader, _reader_loaded
    with _reader_lock:
        if _reader is not None:
            try:
                _reader.close()
            except Exception:
                logger.debug("Error closing GeoIP reader", exc_info=True)
        _reader = None
        _reader_loaded = False


__all__ = [
    "Location",
    "distance_km",
    "locate",
    "reset_reader_cache",
    "resolve_geoip",
]
