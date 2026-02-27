"""Base class for NATS listeners with leader election and automatic retry."""

from __future__ import annotations

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from service_toolkit.nats import DEFAULT_NATS_URL, NATSClient, NATSSettings
from service_toolkit.redis import Keyspace, LeaderLease, RedisClient, RedisSettings

if TYPE_CHECKING:
    from nats.aio.msg import Msg
    from nats.aio.subscription import Subscription

logger = logging.getLogger(__name__)


class LeaderElectedListener(ABC):
    """Base class for NATS JetStream listeners with Redis leader election and retry.

    Subclasses must implement:
    - _setup_subscriptions(): Create NATS subscriptions
    - _get_lease_key(): Return Redis key for leader lease
    - _get_service_name(): Return service name for logging
    - _get_retry_delay_seconds(): Return retry delay in seconds

    Optional overrides:
    - _on_start_once(): Called after successful leader acquisition
    - _on_stop(): Called during shutdown
    """

    def __init__(
        self,
        *,
        redis_enabled: bool = True,
        leader_ttl_seconds: int = 30,
        env_file: str = ".env",
    ) -> None:
        """Initialize leader-elected listener.

        Args:
            redis_enabled: Enable Redis leader election
            leader_ttl_seconds: Leader lease TTL in seconds
            env_file: Path to .env file for configuration
        """
        self._redis_enabled = redis_enabled
        self._leader_ttl_seconds = leader_ttl_seconds
        self._env_file = env_file

        self._client: NATSClient | None = None
        self._subscriptions: list[Subscription] = []
        self._redis: RedisClient | None = None
        self._lease: LeaderLease | None = None
        self._retry_task: asyncio.Task[None] | None = None
        self._stopping = False

    @abstractmethod
    def _get_lease_key(self) -> str:
        """Return Redis key for leader lease."""
        ...

    @abstractmethod
    def _get_service_name(self) -> str:
        """Return service name for NATS client and logging."""
        ...

    @abstractmethod
    def _get_retry_delay_seconds(self) -> float:
        """Return retry delay in seconds (should be < leader_ttl_seconds / 2)."""
        ...

    @abstractmethod
    async def _setup_subscriptions(self) -> list[Subscription]:
        """Setup NATS subscriptions and return list of subscription objects.

        Use self._client to access NATSClient instance.
        Must call self._client.ensure_stream() before subscribing.

        Returns:
            List of NATS subscription objects
        """
        ...

    async def _on_start_once(self) -> None:
        """Hook called after successful leader acquisition and subscription setup.

        Override to add custom initialization logic.
        """
        pass

    async def _on_stop(self) -> None:
        """Hook called during shutdown before closing resources.

        Override to add custom cleanup logic.
        """
        pass

    async def start(self) -> None:
        """Start the listener with leader election and retry on failure."""
        self._stopping = False
        started = await self._start_once()
        if not started:
            self._ensure_retry_task()

    async def _start_once(self) -> bool:
        """Attempt to start listener once. Returns True on success, False on failure."""
        if self._subscriptions:
            return True

        # Acquire leader lease if Redis is enabled
        if self._redis_enabled:
            env_file = os.environ.get("ENV_FILE", self._env_file)
            self._redis = RedisClient(RedisSettings.from_env(env_file=env_file))
            await self._redis.connect()

            lease_key = Keyspace(f"{self._get_service_name()}:leader").key(
                self._get_lease_key()
            )
            self._lease = LeaderLease(
                self._redis.client,
                lease_key,
                ttl_seconds=self._leader_ttl_seconds,
                on_lost=self._on_lease_lost,
            )
            if not await self._lease.acquire():
                retry_delay = self._get_retry_delay_seconds()
                logger.info(
                    "%s not leader; will retry in %.1fs",
                    self.__class__.__name__,
                    retry_delay,
                )
                await self._close_lease_and_redis()
                return False

        # Setup NATS client and subscriptions
        try:
            nats_settings = NATSSettings.from_env(env_file=self._env_file)
            if not nats_settings.servers:
                nats_settings.servers = (DEFAULT_NATS_URL,)
            if not nats_settings.name:
                nats_settings.name = self._get_service_name()

            self._client = NATSClient(nats_settings)
            await self._client.connect()

            self._subscriptions = await self._setup_subscriptions()

            await self._on_start_once()

            logger.info("%s started successfully", self.__class__.__name__)
            return True
        except Exception:
            self._client = None
            logger.exception("Failed to initialize %s", self.__class__.__name__)
            await self._close_runtime()
            return False

    async def stop(self) -> None:
        """Stop the listener and cleanup resources."""
        self._stopping = True

        # Cancel retry task
        retry_task = self._retry_task
        self._retry_task = None
        if retry_task is not None:
            retry_task.cancel()
            try:
                await retry_task
            except asyncio.CancelledError:
                pass

        await self._close_runtime()

    async def _close_runtime(self) -> None:
        """Close NATS subscriptions and connections."""
        await self._on_stop()

        # Unsubscribe from all NATS subscriptions
        for subscription in self._subscriptions:
            try:
                await subscription.unsubscribe()
            except Exception:
                logger.exception(
                    "Failed to unsubscribe in %s",
                    self.__class__.__name__,
                )
        self._subscriptions = []

        # Close NATS client
        if self._client is not None:
            await self._client.close()
            self._client = None

        await self._close_lease_and_redis()

    async def _close_lease_and_redis(self) -> None:
        """Release leader lease and close Redis connection."""
        lease = self._lease
        self._lease = None
        if lease is not None:
            await lease.release()

        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def _on_lease_lost(self) -> None:
        """Handle leader lease loss by restarting with retry."""
        retry_delay = self._get_retry_delay_seconds()
        logger.warning(
            "%s leader lease lost; restarting in %.1fs",
            self.__class__.__name__,
            retry_delay,
        )
        await self._close_runtime()
        self._ensure_retry_task()

    def _ensure_retry_task(self) -> None:
        """Ensure retry task is running."""
        if self._stopping:
            return
        task = self._retry_task
        if task is not None and not task.done():
            return
        self._retry_task = asyncio.create_task(self._retry_start())

    async def _retry_start(self) -> None:
        """Retry starting the listener until success or stopped."""
        retry_delay = self._get_retry_delay_seconds()
        while not self._stopping:
            await asyncio.sleep(retry_delay)
            if self._stopping or self._subscriptions:
                return
            started = await self._start_once()
            if started:
                logger.info("%s recovered", self.__class__.__name__)
                return


__all__ = ["LeaderElectedListener"]
