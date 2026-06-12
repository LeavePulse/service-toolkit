"""On-behalf-of actor context for bot accounts.

A bot account (``is_bot=true``) authenticates with its own JWT but may act on
behalf of a human, identified by an external identity. The bot declares the
target via the ``X-On-Behalf-Of`` request header in the form ``<source>:<subject>``:

    X-On-Behalf-Of: discord:123456789012345678   # external identity
    X-On-Behalf-Of: telegram:987654321           # external identity
    X-On-Behalf-Of: leavepulse:42                 # native platform user id

``source == "leavepulse"`` means ``subject`` is already a platform ``user_id``
and needs no resolution; any other source is an external identity resolved via
``auth.identity.ResolveIdentity(provider, subject)``.

INVARIANT — effective permissions are the INTERSECTION of the bot's own
permissions and the resolved human's permissions. On-behalf-of only NARROWS the
actor down to a specific person within what the bot is itself allowed to do; it
NEVER escalates. This module owns the parsing and the intersection rule so every
service applies it identically; the actual identity resolution (gRPC call) is
performed by the caller, which holds the client.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

import msgspec

ON_BEHALF_OF_HEADER = "X-On-Behalf-Of"
NATIVE_SOURCE = "leavepulse"


class ActorRef(msgspec.Struct, frozen=True, kw_only=True):
    """A parsed ``X-On-Behalf-Of`` value.

    ``source`` is lower-cased. When ``source == NATIVE_SOURCE`` the ``subject``
    is a platform ``user_id`` as a string; otherwise it is an external-identity
    subject for ``(provider=source, subject)``.
    """

    source: str
    subject: str

    @property
    def is_native(self) -> bool:
        """True when the subject is already a platform user id."""
        return self.source == NATIVE_SOURCE


class ActorContext(msgspec.Struct, frozen=True, kw_only=True):
    """Resolved on-behalf-of context bound for the current request.

    ``bot_user_id`` is the authenticated bot. ``on_behalf_user_id`` is the
    resolved human the bot acts for. Authorization must use the intersection of
    both subjects' permissions — see module docstring.
    """

    bot_user_id: int
    ref: ActorRef
    on_behalf_user_id: int


_CURRENT_ACTOR: ContextVar[ActorContext | None] = ContextVar(
    "leavepulse_current_actor", default=None
)


def current_actor() -> ActorContext | None:
    """Return the on-behalf-of actor bound to the current request, if any."""
    return _CURRENT_ACTOR.get()


def set_current_actor(actor: ActorContext | None) -> Token[ActorContext | None]:
    """Bind an actor context for the current task; returns a reset Token."""
    return _CURRENT_ACTOR.set(actor)


def reset_current_actor(token: Token[ActorContext | None]) -> None:
    """Restore the previous actor context."""
    _CURRENT_ACTOR.reset(token)


def parse_on_behalf_of(raw: str | None) -> ActorRef | None:
    """Parse a ``<source>:<subject>`` header value.

    Returns ``None`` for an absent/blank header. Raises ``InvalidInputError``
    for a malformed value (missing separator or empty side) so the caller can
    surface a 4xx rather than silently dropping the actor.
    """
    value = (raw or "").strip()
    if not value:
        return None

    source, separator, subject = value.partition(":")
    source = source.strip().lower()
    subject = subject.strip()
    if not separator or not source or not subject:
        from awesome_errors import InvalidInputError  # type: ignore[import-not-found]

        raise InvalidInputError(
            "X-On-Behalf-Of must be '<source>:<subject>'",
            field="X-On-Behalf-Of",
        )
    return ActorRef(source=source, subject=subject)


def intersect_scopes(bot_scopes: list[str], human_scopes: list[str]) -> list[str]:
    """Effective scope = bot ∩ human, preserving the bot's order.

    The bot can never gain a scope it does not already hold; the human can only
    narrow it further.
    """
    allowed = set(human_scopes)
    return [scope for scope in bot_scopes if scope in allowed]


def intersect_perms_bits(bot_bits: int, human_bits: int) -> int:
    """Effective platform-perms bitset = bot AND human."""
    return int(bot_bits) & int(human_bits)


__all__ = [
    "NATIVE_SOURCE",
    "ON_BEHALF_OF_HEADER",
    "ActorContext",
    "ActorRef",
    "current_actor",
    "intersect_perms_bits",
    "intersect_scopes",
    "parse_on_behalf_of",
    "reset_current_actor",
    "set_current_actor",
]
