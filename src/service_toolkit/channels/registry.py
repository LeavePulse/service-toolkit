"""A small registry that maps a channel name to a :class:`Channel`.

Senders that fan one message out across several transports (notification-service
resolving a user's enabled channels per topic) look channels up by name here
instead of holding concrete references. A registry is just a dict wrapper — it
exists so the lookup-by-name contract is explicit and shared.
"""

from __future__ import annotations

from .base import Channel, DeliveryResult, Message


class ChannelRegistry:
    """Name → :class:`Channel`. Unknown names resolve to a skipped delivery."""

    def __init__(self, channels: list[Channel] | None = None) -> None:
        self._channels: dict[str, Channel] = {}
        for channel in channels or []:
            self.register(channel)

    def register(self, channel: Channel) -> None:
        self._channels[channel.name] = channel

    def get(self, name: str) -> Channel | None:
        return self._channels.get(name)

    @property
    def names(self) -> list[str]:
        return list(self._channels)

    async def send(self, name: str, message: Message) -> DeliveryResult:
        """Send via the named channel, or skip if it isn't registered."""
        channel = self._channels.get(name)
        if channel is None:
            return DeliveryResult(ok=False, detail=f"channel_unavailable:{name}")
        return await channel.send(message)


__all__ = ["ChannelRegistry"]
