"""Generic RBAC bitset helpers shared across services.

Provider-owned permission maps live in service-owned SDK packages such as
`auth-service-sdk` and `server-service-sdk`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def bits_from_codes(codes: Iterable[str], mapping: Mapping[str, int]) -> int:
    """Return bitset encoded from permission codes.

    Unknown codes are ignored (forward-compatible).
    """
    bits = 0
    for code in codes:
        bit = mapping.get(str(code))
        if bit is None:
            continue
        bits |= 1 << int(bit)
    return bits


def codes_from_bits(bits: int, mapping: Mapping[str, int]) -> list[str]:
    """Return sorted permission codes present in the bitset."""
    selected: list[tuple[int, str]] = []
    for code, bit in mapping.items():
        if bits & (1 << int(bit)):
            selected.append((int(bit), str(code)))
    selected.sort(key=lambda item: item[0])
    return [code for _bit, code in selected]


def has_code(bits: int, mapping: Mapping[str, int], code: str) -> bool:
    """Return True if the bitset contains the given permission code."""
    bit = mapping.get(code)
    if bit is None:
        return False
    return bool(bits & (1 << int(bit)))


def all_bits(mapping: Mapping[str, int]) -> int:
    """Return a bitmask containing all bits used by the mapping."""
    bits = 0
    for bit in mapping.values():
        bits |= 1 << int(bit)
    return bits


__all__ = [
    "all_bits",
    "bits_from_codes",
    "codes_from_bits",
    "has_code",
]
