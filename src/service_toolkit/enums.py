"""A string enum that tolerates values a newer backend may add.

A service returns a status as a free-form string. If a typed consumer decodes
it into a strict enum, the day the backend adds a value the consumer has not
learned yet, decoding raises and the endpoint that merely displays the status
goes down -- a forward-compatibility break dressed as a validation error.

:class:`TolerantStrEnum` removes that failure mode: a subclass lists only its
real values and gets ``UNKNOWN = "unknown"`` for free, and any value with no
exact member decodes to ``UNKNOWN`` instead of raising. Each coercion bumps a
Prometheus counter and logs a warning, so the unlearned value is visible in
telemetry and can be promoted to an explicit member later. Use it wherever a
field crosses a service boundary as a string and you are not certain its set of
values is frozen.

``UNKNOWN`` is injected in :meth:`__init_subclass__` *after* the stock ``enum``
machinery has frozen the class, so ``type(cls)`` stays the plain
:class:`enum.EnumMeta`. That identity is load-bearing: msgspec treats a type as
an enum only when its metaclass is exactly ``EnumMeta`` (see ``msgspec.inspect``),
and both its JSON-schema generation and its native decoding depend on it. A
custom metaclass made msgspec see a ``CustomType`` instead -- which dropped the
enum from the OpenAPI schema and turned an unknown value into a
``ValidationError`` rather than coercing it. Injecting the member
post-construction keeps the schema complete and :meth:`_missing_` tolerant.
"""

from __future__ import annotations

import enum
import logging
from typing import ClassVar, Self

from prometheus_client import Counter

logger = logging.getLogger(__name__)

#: Incremented whenever a value outside a tolerant enum is coerced. Kept
#: low-cardinality on purpose -- the raw value is logged, not labelled, so a
#: flood of junk values cannot blow up the time series. Alert on
#: ``increase(service_toolkit_unknown_enum_value_total[1h]) > 0``.
UNKNOWN_ENUM_VALUE_TOTAL = Counter(
    "service_toolkit_unknown_enum_value_total",
    "A value outside a tolerant enum was coerced to UNKNOWN.",
    labelnames=("enum",),
)

#: Canonical fallback value/member name shared by every tolerant enum.
_UNKNOWN = "unknown"


class TolerantStrEnum(enum.StrEnum):
    """A string enum that degrades gracefully on unknown input.

    Subclasses list only their real values; ``UNKNOWN = "unknown"`` is appended
    automatically by :meth:`__init_subclass__`. An unrecognised value decodes to
    ``UNKNOWN`` via :meth:`_missing_` rather than raising -- so a brand-new
    backend status never takes an endpoint down -- and each such coercion bumps a
    Prometheus counter and logs a warning. Use :meth:`coerce` at the gRPC
    boundary (clients/mappers) for explicit, type-narrowed conversion.
    """

    # A bare annotation (no value) so type-checkers know every concrete subclass
    # exposes ``UNKNOWN`` -- ``enum`` ignores valueless annotations, so it does
    # not become a phantom member; the real one is injected in
    # :meth:`__init_subclass__`.
    UNKNOWN: ClassVar[Self]

    def __init_subclass__(cls, **kwargs: object) -> None:
        # Append ``UNKNOWN = "unknown"`` *after* the stock enum machinery has
        # frozen the members. Enum members can't be inherited and can't be added
        # via a normal assignment post-construction, so we register the member
        # directly in the enum's lookup tables. This keeps ``type(cls)`` the
        # stock ``enum.EnumMeta`` (which msgspec requires to treat it as an
        # enum) instead of a custom metaclass.
        super().__init_subclass__(**kwargs)
        if "UNKNOWN" in cls._member_map_:
            return
        member = str.__new__(cls, _UNKNOWN)
        member._name_ = "UNKNOWN"
        member._value_ = _UNKNOWN  # type: ignore[assignment]  # enum stubs type _value_ as Self
        cls._member_map_["UNKNOWN"] = member
        cls._value2member_map_[_UNKNOWN] = member
        cls._member_names_.append("UNKNOWN")
        type.__setattr__(cls, "UNKNOWN", member)

    @classmethod
    def _missing_(cls, value: object) -> Self | None:
        """Coerce an unrecognised value to ``UNKNOWN`` instead of raising.

        Invoked by ``enum`` (and therefore msgspec's native decoder) whenever a
        value has no exact member, mapping it to ``UNKNOWN``. ``None`` is the one
        value that still falls through to the standard ``ValueError`` -- a real
        absence is handled by :meth:`coerce_optional`, not silently coerced.
        """
        unknown = cls._member_map_.get("UNKNOWN")
        if unknown is None or value is None:
            return None
        UNKNOWN_ENUM_VALUE_TOTAL.labels(enum=cls.__name__).inc()
        logger.warning(
            "Unknown %s value from backend: %r -- coerced to UNKNOWN",
            cls.__name__,
            value,
        )
        return unknown  # type: ignore[return-value]

    @classmethod
    def coerce(cls, raw: object) -> Self:
        """Map an upstream string to a member, falling back to ``UNKNOWN``.

        An unrecognised value is logged and counted (via :meth:`_missing_`),
        never raised.
        """
        return cls("" if raw is None else str(raw))

    @classmethod
    def coerce_optional(cls, raw: object) -> Self | None:
        """Like :meth:`coerce`, but keep a genuinely absent value as ``None``.

        ``None`` and the empty string both mean "the backend did not set this
        field", so they pass through as ``None`` rather than being counted as
        an unknown value.
        """
        if raw is None or raw == "":
            return None
        return cls.coerce(raw)
