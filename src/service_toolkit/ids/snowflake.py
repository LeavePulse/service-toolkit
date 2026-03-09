"""Snowflake-style 64-bit identifier utilities."""

from __future__ import annotations

import threading
import time
from typing import Final, Optional


__all__ = [
    "DEFAULT_EPOCH_MS",
    "SnowflakeGenerator",
    "configure_default_generator",
    "reset_default_generator",
    "generate_id",
]

DEFAULT_EPOCH_MS: Final[int] = 1_735_682_400_000  # 2025-01-01T00:00:00Z


class SnowflakeGenerator:
    """Simple Snowflake generator (41-bit time, 10-bit worker, 12-bit sequence)."""

    _LOCK: Final[threading.Lock] = threading.Lock()

    def __init__(
        self,
        *,
        epoch_ms: int = DEFAULT_EPOCH_MS,
        worker_id: int,
        datacenter_id: int = 0,
        sequence_bits: int = 12,
        worker_bits: int = 5,
        datacenter_bits: int = 5,
    ) -> None:
        if worker_id < 0 or worker_id >= (1 << worker_bits):
            msg = "worker_id out of range"
            raise ValueError(msg)
        if datacenter_id < 0 or datacenter_id >= (1 << datacenter_bits):
            msg = "datacenter_id out of range"
            raise ValueError(msg)

        self.epoch_ms = epoch_ms
        self.worker_id = worker_id
        self.datacenter_id = datacenter_id
        self.sequence_bits = sequence_bits
        self.worker_bits = worker_bits
        self.datacenter_bits = datacenter_bits

        self.max_sequence = (1 << sequence_bits) - 1
        self.worker_shift = sequence_bits
        self.datacenter_shift = sequence_bits + worker_bits
        self.timestamp_shift = sequence_bits + worker_bits + datacenter_bits

        self._last_timestamp = -1
        self._sequence = 0

    def _current_millis(self) -> int:
        return int(time.time() * 1000)

    def _wait_next_millis(self, last_timestamp: int) -> int:
        timestamp = self._current_millis()
        while timestamp <= last_timestamp:
            timestamp = self._current_millis()
        return timestamp

    def generate(self) -> int:
        """Return the next snowflake identifier."""
        with self._LOCK:
            timestamp = self._current_millis()
            if timestamp < self._last_timestamp:
                timestamp = self._wait_next_millis(self._last_timestamp)

            if timestamp == self._last_timestamp:
                self._sequence = (self._sequence + 1) & self.max_sequence
                if self._sequence == 0:
                    timestamp = self._wait_next_millis(self._last_timestamp)
            else:
                self._sequence = 0

            self._last_timestamp = timestamp

            return (
                ((timestamp - self.epoch_ms) << self.timestamp_shift)
                | (self.datacenter_id << self.datacenter_shift)
                | (self.worker_id << self.worker_shift)
                | self._sequence
            )


class _Registry:
    lock: Final[threading.Lock] = threading.Lock()
    instance: Optional[SnowflakeGenerator] = None


def configure_default_generator(*, worker_id: int, datacenter_id: int = 0) -> None:
    with _Registry.lock:
        _Registry.instance = SnowflakeGenerator(
            worker_id=worker_id,
            datacenter_id=datacenter_id,
        )


def reset_default_generator() -> None:
    with _Registry.lock:
        _Registry.instance = None


def _get_default() -> SnowflakeGenerator:
    with _Registry.lock:
        if _Registry.instance is None:
            _Registry.instance = SnowflakeGenerator(worker_id=0, datacenter_id=0)
        return _Registry.instance


def generate_id() -> int:
    return _get_default().generate()
