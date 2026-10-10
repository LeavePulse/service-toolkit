"""Durable JetStream pull consumer with explicit acknowledgement."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from nats.aio.msg import Msg
from nats.errors import ConnectionClosedError, NoServersError
from nats.errors import TimeoutError as NATSTimeoutError
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
from nats.js.errors import FetchTimeoutError

from .nats import NATSClient

logger = logging.getLogger(__name__)

MessageHandler = Callable[[Msg], Awaitable[None]]

#: Redelivery delays in seconds, indexed by attempt; the last one repeats.
DEFAULT_BACKOFF: tuple[float, ...] = (1.0, 5.0, 30.0, 120.0)


class PoisonMessage(Exception):
    """Raised by a handler for a message that will never succeed.

    The consumer terminates it instead of scheduling a redelivery, so a
    malformed payload does not cycle through every retry before being dropped.
    """


@dataclass(slots=True)
class DurableConsumer:
    """Pull messages from a durable consumer and hand them to ``handler``.

    The message is acknowledged after the handler returns, so a crash between
    receiving and finishing means JetStream delivers it again: handlers must
    be idempotent. A failing handler gets the message back after the next
    ``backoff`` delay until ``max_deliver`` is reached; :class:`PoisonMessage`
    ends it at once. Losing the connection pauses the loop instead of ending
    it, so one consumer task survives a NATS restart.

    ``durable`` names the consumer group: every replica that uses the same
    name shares the work, a different name gets its own copy of each message.
    """

    client: NATSClient
    stream: str
    durable: str
    subject: str
    handler: MessageHandler
    batch: int = 10
    fetch_timeout: float = 5.0
    ack_wait: float = 30.0
    max_deliver: int = 10
    backoff: Sequence[float] = DEFAULT_BACKOFF
    deliver_policy: DeliverPolicy = DeliverPolicy.ALL
    reconnect_delay: float = 2.0
    _stopping: asyncio.Event = field(default_factory=asyncio.Event, init=False)

    def config(self) -> ConsumerConfig:
        """Consumer settings this loop expects JetStream to hold."""

        return ConsumerConfig(
            durable_name=self.durable,
            filter_subject=self.subject,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=self.ack_wait,
            max_deliver=self.max_deliver,
            deliver_policy=self.deliver_policy,
        )

    def stop(self) -> None:
        """Finish the current batch and leave :meth:`run`."""

        self._stopping.set()

    def delay_for(self, attempt: int) -> float:
        """Redelivery delay after the ``attempt``-th failed delivery (1-based)."""

        if not self.backoff:
            return 0.0
        return float(self.backoff[min(max(attempt, 1), len(self.backoff)) - 1])

    async def handle(self, msg: Msg) -> None:
        """Run the handler for one message and settle it with JetStream."""

        try:
            await self.handler(msg)
        except PoisonMessage:
            logger.warning(
                "Terminating poison message on %s (consumer %s)",
                msg.subject,
                self.durable,
            )
            await msg.term()
            return
        except Exception:
            attempt = msg.metadata.num_delivered if msg.metadata else 1
            logger.exception(
                "Handler failed on %s (consumer %s, attempt %s)",
                msg.subject,
                self.durable,
                attempt,
            )
            await msg.nak(delay=self.delay_for(attempt))
            return
        await msg.ack()

    async def run(self) -> None:
        """Consume until :meth:`stop` is called."""

        while not self._stopping.is_set():
            try:
                await self.client.ensure_consumer(
                    self.stream, self.durable, config=self.config()
                )
                js = await self.client.jetstream()
                subscription = await js.pull_subscribe_bind(self.durable, self.stream)
                while not self._stopping.is_set():
                    try:
                        messages = await subscription.fetch(
                            self.batch, timeout=self.fetch_timeout
                        )
                    except FetchTimeoutError, NATSTimeoutError:
                        continue
                    for msg in messages:
                        await self.handle(msg)
            except asyncio.CancelledError:
                raise
            except ConnectionClosedError, NoServersError, OSError:
                logger.warning(
                    "NATS unavailable for consumer %s, retrying in %ss",
                    self.durable,
                    self.reconnect_delay,
                )
            except Exception:
                logger.exception(
                    "Consumer %s stopped unexpectedly, restarting", self.durable
                )
            if not self._stopping.is_set():
                await asyncio.sleep(self.reconnect_delay)


__all__ = ["DEFAULT_BACKOFF", "DurableConsumer", "MessageHandler", "PoisonMessage"]
