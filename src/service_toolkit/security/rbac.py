"""RBAC helpers shared across services.

North Star: platform permissions live in auth-service (embedded into JWT),
project/root permissions live in server-service (distributed via events).

This module defines stable permission codes and their bit positions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

PLATFORM_PERMS_VERSION = 2
PROJECT_PERMS_VERSION = 1
PROJECT_SCOPE_PROJECT = "project"
PROJECT_SCOPE_SERVER = "server"
PROJECT_SCOPE_WHITELIST_POLICY = "whitelist_policy"
PROJECT_PERMISSION_SCOPE_TYPES = (
    PROJECT_SCOPE_PROJECT,
    PROJECT_SCOPE_SERVER,
    PROJECT_SCOPE_WHITELIST_POLICY,
)

# Platform-scoped permissions (auth-service).
PLATFORM_PERM_BITS: dict[str, int] = {
    "platform.audit.view": 0,
    "platform.staff.manage": 1,
    "platform.users.moderate": 2,
    "platform.content.moderate": 4,
    "platform.system.manage": 6,
    "platform.servers.moderate": 7,
    "platform.servers.profile.edit": 8,
    "platform.servers.media.edit": 9,
    "platform.servers.links.edit": 10,
    "platform.servers.team.manage": 11,
    "platform.servers.verification.manage": 12,
    "platform.servers.whitelist.config.edit": 13,
    "platform.servers.whitelist.review": 14,
    "platform.servers.whitelist.import": 15,
    "platform.servers.monitoring.view_private": 16,
    "platform.servers.monitoring.alerts.manage": 17,
    "platform.servers.community.moderate": 18,
    "platform.servers.community.official_reply": 19,
    "platform.servers.bot.manage": 20,
    "platform.servers.bot.debug": 21,
    "platform.servers.whitelist.direct_manage": 22,
}

# Project/root-scoped permissions (server-service).
PROJECT_PERM_BITS: dict[str, int] = {
    "project.audit.view": 0,
    "project.members.manage": 1,
    "project.roles.manage": 2,
    "server.profile.edit": 3,
    "server.media.edit": 4,
    "server.links.edit": 5,
    "server.verification.manage": 6,
    "whitelist.config.edit": 7,
    "whitelist.review": 8,
    "whitelist.import": 9,
    "bot.manage": 10,
    "bot.debug": 11,
    "monitoring.view_private": 12,
    "monitoring.alerts.manage": 13,
    "community.moderate": 14,
    "community.official_reply": 15,
    "whitelist.direct_manage": 16,
}


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


PLATFORM_PERMS_ALL_BITS = all_bits(PLATFORM_PERM_BITS)
PROJECT_PERMS_ALL_BITS = all_bits(PROJECT_PERM_BITS)


def platform_perms_bits_from_scope(scope: Iterable[str]) -> int:
    """Encode platform permission bits from a JWT `scope` list."""
    return bits_from_codes(scope, PLATFORM_PERM_BITS)


def project_perms_bits_from_codes(codes: Iterable[str]) -> int:
    """Encode project/root permission bits from a list of permission codes."""
    return bits_from_codes(codes, PROJECT_PERM_BITS)
