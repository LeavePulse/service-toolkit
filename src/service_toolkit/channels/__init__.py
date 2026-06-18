"""Delivery channels — stateless transports for user-facing messages.

See :mod:`service_toolkit.channels.base` for the abstraction and the explicit
scope note (bot-routed Discord/Telegram do NOT live here).
"""

from __future__ import annotations

from .base import Channel, DeliveryResult, Message
from .registry import ChannelRegistry
from .smtp import SmtpChannel, SmtpSettings

# The flat top-level namespace (`from service_toolkit import ...`) would make a
# bare `Message` ambiguous, so it is re-exported there as `DeliveryMessage`.
DeliveryMessage = Message

__all__ = [
    "Channel",
    "ChannelRegistry",
    "DeliveryMessage",
    "DeliveryResult",
    "Message",
    "SmtpChannel",
    "SmtpSettings",
]
