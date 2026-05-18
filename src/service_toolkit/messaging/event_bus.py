"""Base event bus for NATS-backed publishers.

Eliminates the ~60 lines of identical ``start()`` / ``stop()`` / ``_publish()``
boilerplate found in every service's event bus class.

Usage::

    class BillingEventBus(BaseEventBus):
        def __init__(self) -> None:
            super().__init__(
                service_name=settings.service_name,
                enabled=settings.event_bus.enabled,
                stream_name=settings.event_bus.stream_domain,
                subjects=[
                    settings.event_bus.subject_order_paid,
                    settings.event_bus.subject_order_failed,
                ],
            )

        async def publish_order_paid(self, payload: dict[str, Any]) -> None:
            await self._publish(self.subjects[0], payload)
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from .events import build_event
from .nats import DEFAULT_NATS_URL, NATSClient, NATSSettings

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


class BaseEventBus:
    """Abstract base for NATS JetStream event publishers.

    Subclasses only need to define ``__init__`` (passing config) and
    thin ``publish_*`` methods that delegate to :meth:`_publish`.
    """

    def __init__(
        self,
        *,
        service_name: str,
        enabled: bool = True,
        stream_name: str,
        subjects: Sequence[str],
    ) -> None:
        self.service_name = service_name
        self.enabled = enabled
        self.stream_name = stream_name
        self.subjects = list(subjects)
        self._client: NATSClient | None = None

    @property
    def is_connected(self) -> bool:
        """Return whether the NATS client is connected."""
        return self._client is not None

    @property
    def client(self) -> NATSClient | None:
        """Return the underlying NATS client (or None when disabled)."""
        return self._client

    async def start(self) -> None:
        """Connect to NATS and ensure the JetStream stream exists."""
        if not self.enabled:
            logger.info("%s event bus disabled; skipping connection", self.service_name)
            return

        try:
            env_file = os.environ.get("ENV_FILE", ".env")
            nats_settings = NATSSettings.from_env(env_file=env_file)
            if not nats_settings.servers:
                nats_settings.servers = (DEFAULT_NATS_URL,)
            if not nats_settings.name:
                nats_settings.name = self.service_name

            client = NATSClient(nats_settings)
            await client.connect()
            await client.ensure_stream(self.stream_name, self.subjects)
            self._client = client
            logger.info(
                "Connected to NATS for %s events (servers=%s)",
                self.service_name,
                ",".join(nats_settings.servers),
            )
        except Exception:  # pragma: no cover
            if self._client is not None:
                await self._client.close()
                self._client = None
            logger.exception(
                "Failed to initialize %s event bus",
                self.service_name,
            )

    async def stop(self) -> None:
        """Close the NATS connection."""
        if self._client is None:
            return
        try:
            await self._client.close()
        finally:
            self._client = None

    async def _publish(self, subject: str, payload: dict[str, Any]) -> None:
        """Publish an event envelope to a NATS subject.

        Does nothing when the bus is disabled or not connected.
        """
        if not self.enabled or self._client is None:
            return

        event = build_event(
            event_type=subject,
            producer=self.service_name,
            schema_version=1,
            data=payload,
        )

        try:
            await self._client.publish_json(subject, event)
        except Exception:  # pragma: no cover
            logger.exception("Failed to publish %s event", subject)

    async def _publish_bool(self, subject: str, payload: dict[str, Any]) -> bool:
        """Like :meth:`_publish` but returns ``True`` on success, ``False`` otherwise."""
        if not self.enabled or self._client is None:
            return False

        event = build_event(
            event_type=subject,
            producer=self.service_name,
            schema_version=1,
            data=payload,
        )

        try:
            await self._client.publish_json(subject, event)
            return True
        except Exception:  # pragma: no cover
            logger.exception("Failed to publish %s event", subject)
            return False


__all__ = ["BaseEventBus"]
