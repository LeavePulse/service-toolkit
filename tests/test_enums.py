"""TolerantStrEnum keeps decoding forward-compatible across service versions."""

from __future__ import annotations

import enum

import msgspec
import pytest

from service_toolkit import TolerantStrEnum


class _State(TolerantStrEnum):
    APPLIED = "APPLIED"
    MISMATCH = "MISMATCH"


def test_unknown_member_is_injected_without_being_declared() -> None:
    # A subclass lists only its real values and gets UNKNOWN for free.
    assert _State.UNKNOWN.value == "unknown"
    assert {m.value for m in _State} == {"APPLIED", "MISMATCH", "unknown"}


def test_metaclass_stays_stock_enummeta_so_msgspec_treats_it_as_an_enum() -> None:
    # The whole reason UNKNOWN is injected post-construction: msgspec recognises
    # an enum only when its metaclass is exactly EnumMeta.
    assert type(_State) is enum.EnumMeta


def test_msgspec_decodes_a_known_value_natively() -> None:
    assert msgspec.json.decode(b'"APPLIED"', type=_State) is _State.APPLIED


def test_msgspec_coerces_an_unlearned_value_instead_of_raising() -> None:
    # The forward-compat guarantee: a value a newer backend added decodes to
    # UNKNOWN, so an endpoint that merely displays it does not go down.
    assert msgspec.json.decode(b'"NEW_STATE"', type=_State) is _State.UNKNOWN


def test_coerce_maps_unknown_to_unknown() -> None:
    assert _State.coerce("APPLIED") is _State.APPLIED
    assert _State.coerce("nope") is _State.UNKNOWN
    assert _State.coerce(None) is _State.UNKNOWN


def test_coerce_optional_keeps_a_real_absence_as_none() -> None:
    # None and "" mean "the field was not set", distinct from an unknown value.
    assert _State.coerce_optional(None) is None
    assert _State.coerce_optional("") is None
    assert _State.coerce_optional("MISMATCH") is _State.MISMATCH
    assert _State.coerce_optional("junk") is _State.UNKNOWN


def test_direct_construction_of_an_unknown_value_coerces() -> None:
    # enum's own _missing_ path, exercised by msgspec's native decoder too.
    assert _State("whatever") is _State.UNKNOWN


def test_none_still_raises_on_direct_construction() -> None:
    # A genuine absence is not a value to coerce; coerce_optional handles it.
    with pytest.raises(ValueError):
        _State(None)  # type: ignore[arg-type]
