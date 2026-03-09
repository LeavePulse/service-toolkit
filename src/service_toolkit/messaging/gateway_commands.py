"""Publish gateway commands to connected agents via NATS.

This is a small helper shared across services that need to trigger plugin/agent
actions via the `gateway.cmd` subject.

Notes:
- This bus is intentionally thin: it only publishes and does not handle results.
- Stream retention is controlled by the stream owner (usually gateway-ingest).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..ids.snowflake import generate_id
from .events import build_event
from .nats import DEFAULT_NATS_URL, NATSClient, NATSSettings

if TYPE_CHECKING:  # pragma: no cover
    pass

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GatewayCommandBusConfig:
    """Configuration for :class:`GatewayCommandBus`."""

    enabled: bool
    stream_name: str
    subject_cmd: str
    producer: str
    env_file: str = ".env"


class GatewayCommandBus:
    """Thin publisher for gateway command events."""

    def __init__(self, config: GatewayCommandBusConfig) -> None:
        self._config = config
        self._client: NATSClient | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._config.enabled)

    async def start(self) -> None:
        if not self.enabled:
            logger.info("Gateway command bus disabled; skipping connection")
            return

        try:
            nats_settings = NATSSettings.from_env(env_file=self._config.env_file)
            if not nats_settings.servers:
                nats_settings.servers = (DEFAULT_NATS_URL,)
            if not nats_settings.name:
                nats_settings.name = self._config.producer

            self._client = NATSClient(nats_settings)
            await self._client.connect()
            await self._client.ensure_stream(
                self._config.stream_name,
                [self._config.subject_cmd],
            )
            logger.info(
                "Connected to NATS for gateway commands (servers=%s)",
                ",".join(nats_settings.servers),
            )
        except Exception as exc:  # pragma: no cover
            self._client = None
            logger.exception("Failed to initialize gateway command bus", exc_info=exc)

    async def stop(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.close()
        finally:
            self._client = None

    async def publish_command(
        self,
        *,
        command_type: str,
        payload: dict[str, Any],
        server_id: int | str | None = None,
        agent_id: str | None = None,
        timeout_ms: int | None = None,
    ) -> str | None:
        if not self.enabled or self._client is None:
            return None

        cmd_id = str(generate_id())
        data: dict[str, Any] = {
            "cmd_id": cmd_id,
            "type": command_type,
            "payload": payload,
        }
        if timeout_ms is not None:
            data["timeout_ms"] = timeout_ms
        if server_id is not None:
            data["server_id"] = str(server_id)
        if agent_id is not None:
            data["agent_id"] = agent_id

        event = build_event(
            event_type=self._config.subject_cmd,
            producer=self._config.producer,
            schema_version=1,
            data=data,
        )
        try:
            await self._client.publish_json(self._config.subject_cmd, event)
        except Exception:  # pragma: no cover
            logger.exception("Failed to publish gateway command")
            return None

        return cmd_id


__all__ = ["GatewayCommandBus", "GatewayCommandBusConfig"]
