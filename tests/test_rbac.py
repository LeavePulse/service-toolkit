from service_toolkit.security.rbac import (
    bits_from_codes,
    codes_from_bits,
    has_code,
)

_PLATFORM_TEST_BITS = {
    "platform.audit.view": 0,
    "platform.staff.manage": 1,
}
_PROJECT_TEST_BITS = {
    "project.audit.view": 0,
    "bot.manage": 10,
}


def test_bits_from_codes_ignores_unknown():
    bits = bits_from_codes(["platform.audit.view", "unknown.perm"], _PLATFORM_TEST_BITS)
    assert has_code(bits, _PLATFORM_TEST_BITS, "platform.audit.view")
    assert not has_code(bits, _PLATFORM_TEST_BITS, "platform.staff.manage")


def test_codes_from_bits_sorted_by_bit():
    bits = (1 << _PROJECT_TEST_BITS["bot.manage"]) | (
        1 << _PROJECT_TEST_BITS["project.audit.view"]
    )
    assert codes_from_bits(bits, _PROJECT_TEST_BITS) == [
        "project.audit.view",
        "bot.manage",
    ]
