"""Where an IP address is, for services that have to choose by nearness.

Two callers with different questions share one answer. auth-service asks "what
city is this session in" to show a person where their account was used;
control-service asks "which of these machines is nearest the one calling" to
hand a new host the enrolment points it should try first. Both are the same
lookup against the same database, which is why this lives here rather than
twice.

Never raises, and never requires the database to exist. A missing mmdb, a
private address, a malformed one — all resolve to "unknown", and every caller
is written to work with that: a session falls back to showing the raw IP, and
an enrolment list falls back to the order it was already in. Geography is an
improvement on the answer, never a precondition for having one.
"""

from service_toolkit.geo.geoip import (
    Location,
    distance_km,
    locate,
    reset_reader_cache,
    resolve_geoip,
)

__all__ = [
    "Location",
    "distance_km",
    "locate",
    "reset_reader_cache",
    "resolve_geoip",
]
