"""SMTP email channel.

The transport that used to live in ``auth-service/src/services/email.py``,
lifted into the toolkit as the first :class:`~service_toolkit.channels.base.Channel`.
Only the *transport* moved: which host/port/creds/TLS to use, and how to put a
``Message`` on the wire. The *brand/audience* layer (which ``from``/brand/link a
given product uses) stays with each sender and arrives per-message via
``Message.meta`` (``from_email`` / ``from_name``).

Backed by the stdlib :mod:`smtplib` (blocking) — no new dependency — wrapped in
:func:`asyncio.to_thread` so :meth:`SmtpChannel.send` is async-native and never
blocks the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import smtplib
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr

from .base import Channel, DeliveryResult, Message

logger = logging.getLogger(__name__)

DEFAULT_SMTP_PORT = 587
DEFAULT_SMTP_TIMEOUT_SECONDS = 10


@dataclass(slots=True)
class SmtpSettings:
    """SMTP transport configuration.

    Mirrors the env-driven settings style of ``NATSSettings``: a plain dataclass
    plus :meth:`from_env`. The ``from_email`` / ``from_name`` here are the
    *default* sender; a per-message ``meta`` override (multi-brand) takes
    precedence in :meth:`SmtpChannel.send`.
    """

    host: str | None = None
    port: int = DEFAULT_SMTP_PORT
    username: str | None = None
    password: str | None = None
    from_email: str | None = None
    from_name: str | None = None
    use_tls: bool = True
    use_ssl: bool = False
    timeout_seconds: int = DEFAULT_SMTP_TIMEOUT_SECONDS

    @property
    def enabled(self) -> bool:
        """Whether enough is configured to attempt a send."""
        return bool(self.host and self.from_email)

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        prefix: str = "SMTP_",
        env_file: str | os.PathLike[str] | None = None,
        case_sensitive: bool = False,
    ) -> SmtpSettings:
        """Create settings from environment variables.

        Prefers :mod:`env_settings` (the optional ``env`` extra) to stay
        consistent with the other toolkit settings; falls back to plain
        ``os.environ`` parsing so the channel works without that extra.
        """
        try:
            from env_settings import BaseSettings as _BaseSettings  # type: ignore
        except ModuleNotFoundError:
            base_settings_cls = None
        else:
            base_settings_cls = _BaseSettings

        if base_settings_cls is not None:

            class _SmtpConfig(base_settings_cls):  # type: ignore[misc, valid-type]
                host: str | None = None
                port: int = DEFAULT_SMTP_PORT
                username: str | None = None
                password: str | None = None
                from_email: str | None = None
                from_name: str | None = None
                use_tls: bool = True
                use_ssl: bool = False
                timeout_seconds: int = DEFAULT_SMTP_TIMEOUT_SECONDS

            loaded = _SmtpConfig.load(
                env=env,
                env_file=env_file,
                prefix=prefix,
                case_sensitive=case_sensitive,
            )
            return cls(
                host=loaded.host,
                port=loaded.port,
                username=loaded.username,
                password=loaded.password,
                from_email=loaded.from_email,
                from_name=loaded.from_name,
                use_tls=loaded.use_tls,
                use_ssl=loaded.use_ssl,
                timeout_seconds=loaded.timeout_seconds,
            )

        source = env if env is not None else os.environ
        return cls(
            host=source.get(f"{prefix}HOST"),
            port=_parse_int(source.get(f"{prefix}PORT"), DEFAULT_SMTP_PORT),
            username=source.get(f"{prefix}USERNAME"),
            password=source.get(f"{prefix}PASSWORD"),
            from_email=source.get(f"{prefix}FROM_EMAIL"),
            from_name=source.get(f"{prefix}FROM_NAME"),
            use_tls=_parse_bool(source.get(f"{prefix}USE_TLS"), default=True),
            use_ssl=_parse_bool(source.get(f"{prefix}USE_SSL"), default=False),
            timeout_seconds=_parse_int(
                source.get(f"{prefix}TIMEOUT_SECONDS"),
                DEFAULT_SMTP_TIMEOUT_SECONDS,
            ),
        )


class SmtpChannel(Channel):
    """Send :class:`Message` instances over SMTP."""

    name = "email"

    def __init__(self, settings: SmtpSettings) -> None:
        self._settings = settings
        # SSL and STARTTLS are mutually exclusive; SSL wins (same as the old
        # EmailService).
        if settings.use_ssl and settings.use_tls:
            settings.use_tls = False

    async def send(self, message: Message) -> DeliveryResult:
        """Deliver one message; offloads the blocking SMTP call to a thread."""
        return await asyncio.to_thread(self._send_blocking, message)

    def _send_blocking(self, message: Message) -> DeliveryResult:
        s = self._settings
        # Per-message brand override (multi-brand senders); falls back to the
        # configured default. Matches the old EmailService semantics: a custom
        # from_email also carries its own from_name.
        meta = message.meta or {}
        override_from = meta.get("from_email")
        sender_email = override_from or s.from_email
        sender_name = (
            meta.get("from_name")
            if override_from
            else (meta.get("from_name") or s.from_name)
        )

        if not s.enabled or not sender_email or not s.host:
            logger.warning(
                "SMTP not configured; skipping email send",
                extra={"to": message.to, "subject": message.subject},
            )
            return DeliveryResult(ok=False, detail="smtp_not_configured")

        email = EmailMessage()
        email["Subject"] = message.subject or ""
        email["From"] = (
            formataddr((sender_name, sender_email)) if sender_name else sender_email
        )
        email["To"] = (
            formataddr((message.to_name, message.to))
            if message.to_name
            else message.to
        )
        email.set_content(message.body)
        if message.html:
            email.add_alternative(message.html, subtype="html")

        try:
            smtp_class = smtplib.SMTP_SSL if s.use_ssl else smtplib.SMTP
            with smtp_class(s.host, s.port, timeout=s.timeout_seconds) as client:
                if s.use_tls and not s.use_ssl:
                    client.starttls()
                if s.username:
                    client.login(s.username, s.password or "")
                client.send_message(email)
        except Exception:  # pragma: no cover - network dependent
            logger.exception(
                "Failed to send email",
                extra={"to": message.to, "subject": message.subject},
            )
            return DeliveryResult(ok=False, detail="smtp_send_failed")
        return DeliveryResult(ok=True)


def _parse_int(value: str | None, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["DEFAULT_SMTP_PORT", "SmtpChannel", "SmtpSettings"]
