from service_toolkit.rbac import (
    PLATFORM_PERM_BITS,
    PROJECT_PERM_BITS,
    bits_from_codes,
    codes_from_bits,
    has_code,
)


def test_bits_from_codes_ignores_unknown():
    bits = bits_from_codes(["platform.audit.view", "unknown.perm"], PLATFORM_PERM_BITS)
    assert has_code(bits, PLATFORM_PERM_BITS, "platform.audit.view")
    assert not has_code(bits, PLATFORM_PERM_BITS, "platform.staff.manage")


def test_codes_from_bits_sorted_by_bit():
    bits = (1 << PROJECT_PERM_BITS["bot.manage"]) | (
        1 << PROJECT_PERM_BITS["project.audit.view"]
    )
    assert codes_from_bits(bits, PROJECT_PERM_BITS) == [
        "project.audit.view",
        "bot.manage",
    ]
