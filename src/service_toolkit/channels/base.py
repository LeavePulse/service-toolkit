"""Delivery-channel abstraction shared across services.

A *channel* is one way to deliver a message to a user: email (SMTP), SMS,
web-push, etc. The point of this module is that a sender (auth-service,
notification-service, …) builds a canonical :class:`Message` once and hands it
to a :class:`Channel`; swapping or adding transports never touches the caller.

Scope — this package hosts only **stateless transports** (no bot identity, no
per-user subscription state): SMTP today, SMS / web-push later. Bot-routed
channels (Discord, Telegram) are deliberately *not* here: their bot tokens and
``Subscription`` routing live in bot-service, and a notification-side
``DiscordChannel`` is expected to delegate to ``bot-service.NotifyService`` over
gRPC rather than own a transport. Keeping that split out of the toolkit avoids
duplicating bot tokens and bot-service's subscription machinery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class Message:
    """A channel-agnostic message.

    Only ``to`` and ``body`` are universal. ``subject`` is email-shaped and
    ignored by channels that have no concept of one. ``html`` is an optional
    rich alternative. ``meta`` carries per-send overrides a channel understands
    (e.g. SMTP ``from_email`` / ``from_name`` for multi-brand senders) — it is
    intentionally untyped so channels can read what they need without widening
    this struct for every transport.
    """

    to: str
    body: str
    subject: str | None = None
    html: str | None = None
    to_name: str | None = None
    meta: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class DeliveryResult:
    """Outcome of a single send.

    ``ok`` is the only thing most callers check. ``detail`` carries a short
    reason when a send is skipped or fails (e.g. ``"smtp_not_configured"``)
    without raising — channels prefer returning a falsy result over throwing so
    a best-effort notification never breaks the calling flow.
    """

    ok: bool
    detail: str | None = None


@runtime_checkable
class Channel(Protocol):
    """One delivery transport. Implementations must be safe to call from async.

    ``name`` identifies the channel in a :mod:`registry` (``"email"``, ``"sms"``)
    and in preferences. ``send`` is async even for transports backed by blocking
    libraries (those should offload to a thread) so callers never block the loop.
    """

    @property
    def name(self) -> str: ...

    async def send(self, message: Message) -> DeliveryResult: ...


__all__ = ["Channel", "DeliveryResult", "Message"]
