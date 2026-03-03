"""Tests for snowflake ID generator."""

import pytest

from service_toolkit import snowflake


def test_configure_default_generator_sets_new_instance() -> None:
    snowflake.reset_default_generator()
    snowflake.configure_default_generator(worker_id=2, datacenter_id=1)
    first = snowflake.generate_id()
    second = snowflake.generate_id()
    assert second > first


def test_generate_id_auto_configures(monkeypatch) -> None:
    snowflake.reset_default_generator()

    class DummyGenerator(snowflake.SnowflakeGenerator):
        def __init__(self, *, worker_id: int, datacenter_id: int = 0) -> None:
            super().__init__(worker_id=worker_id, datacenter_id=datacenter_id)
            self.generated = False

        def generate(self) -> int:  # type: ignore[override]
            self.generated = True
            return super().generate()

    dummy = DummyGenerator(worker_id=1)

    monkeypatch.setattr(
        snowflake, "_Registry", type("_R", (), {"lock": dummy._LOCK, "instance": dummy})
    )

    value = snowflake.generate_id()
    assert isinstance(value, int)
    assert value > 0
    assert dummy.generated


def test_configure_default_generator_invalid_worker() -> None:
    snowflake.reset_default_generator()
    with pytest.raises(ValueError, match="worker_id out of range"):
        snowflake.configure_default_generator(worker_id=64)
