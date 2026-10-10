"""Tests for the JetStream publish, request serving and durable consumer helpers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from nats.errors import ConnectionClosedError
from nats.js.errors import FetchTimeoutError

import service_toolkit.messaging.nats as nats_helpers
from service_toolkit.messaging.consumer import DurableConsumer, PoisonMessage
from service_toolkit.messaging.nats import MSG_ID_HEADER, NATSClient, NATSSettings


class FakeMsg:
    def __init__(
        self, subject: str = "demo.event", *, delivered: int = 1, reply: str = ""
    ) -> None:
        self.subject = subject
        self.data = b"{}"
        self.reply = reply
        self.metadata = SimpleNamespace(num_delivered=delivered)
        self.settled: list[tuple[str, Any]] = []

    async def ack(self) -> None:
        self.settled.append(("ack", None))

    async def nak(self, delay: float | None = None) -> None:
        self.settled.append(("nak", delay))

    async def term(self) -> None:
        self.settled.append(("term", None))

    async def respond(self, data: bytes) -> None:
        self.settled.append(("respond", data))


class FakeSubscription:
    def __init__(self, batches: list[Any]) -> None:
        self.batches = batches

    async def fetch(self, batch: int = 1, timeout: float | None = 5) -> list[FakeMsg]:
        await asyncio.sleep(0)
        if not self.batches:
            raise FetchTimeoutError()
        item = self.batches.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeJetStream:
    def __init__(self, subscriptions: list[FakeSubscription]) -> None:
        self.subscriptions = subscriptions
        self.every = list(subscriptions)
        self.binds = 0
        self.published: list[tuple[str, bytes, dict[str, str] | None]] = []

    async def publish(
        self,
        subject: str,
        payload: bytes,
        timeout: float | None = None,
        headers: Any = None,
    ) -> SimpleNamespace:
        self.published.append((subject, payload, headers))
        return SimpleNamespace(stream="DEMO", seq=len(self.published), duplicate=False)

    async def pull_subscribe_bind(self, durable: str, stream: str) -> FakeSubscription:
        self.binds += 1
        return self.subscriptions.pop(0)


class FakeClient:
    def __init__(self, js: FakeJetStream) -> None:
        self.js = js
        self.consumers: list[tuple[str, str, Any]] = []

    async def ensure_consumer(
        self, stream: str, durable: str, *, config: Any = None
    ) -> Any:
        self.consumers.append((stream, durable, config))
        return config

    async def jetstream(self) -> FakeJetStream:
        return self.js


def consumer_for(client: FakeClient, handler: Any, **kwargs: Any) -> DurableConsumer:
    return DurableConsumer(
        client=cast(NATSClient, client),
        stream="DEMO",
        durable="demo-group",
        subject="demo.event",
        handler=handler,
        reconnect_delay=0,
        **kwargs,
    )


async def run_until_idle(consumer: DurableConsumer, js: FakeJetStream) -> None:
    task = asyncio.create_task(consumer.run())
    for _ in range(50):
        await asyncio.sleep(0)
        if not js.subscriptions and all(not s.batches for s in js.every):
            break
    consumer.stop()
    await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_publish_stream_sets_the_dedup_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    js = FakeJetStream([])
    client = NATSClient(NATSSettings())

    async def fake_jetstream() -> FakeJetStream:
        return js

    monkeypatch.setattr(client, "jetstream", fake_jetstream)

    ack = await client.publish_stream(
        "demo.event", b"{}", msg_id="evt-1", headers={"a": "b"}
    )

    assert ack.seq == 1
    assert js.published == [("demo.event", b"{}", {"a": "b", MSG_ID_HEADER: "evt-1"})]


@pytest.mark.asyncio
async def test_publish_stream_without_id_sends_no_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    js = FakeJetStream([])
    client = NATSClient(NATSSettings())

    async def fake_jetstream() -> FakeJetStream:
        return js

    monkeypatch.setattr(client, "jetstream", fake_jetstream)

    await client.publish_stream("demo.event", b"{}")

    assert js.published[0][2] is None


@pytest.mark.asyncio
async def test_serve_replies_and_stays_silent_on_none_or_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Connection:
        is_connected = True
        is_closed = False

        async def subscribe(
            self, subject: str, queue: str | None = None, cb: Any = None
        ) -> str:
            captured.update(subject=subject, queue=queue, cb=cb)
            return "sub"

    async def fake_connect(**_: object) -> Connection:
        return Connection()

    monkeypatch.setattr(nats_helpers, "connect", fake_connect)
    answers: list[Any] = [b"pong", None, RuntimeError("boom")]

    async def handler(_: Any) -> bytes | None:
        answer = answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    client = NATSClient(NATSSettings())
    assert await client.serve("demo.ping", handler, queue="workers") == "sub"
    assert captured["subject"] == "demo.ping"
    assert captured["queue"] == "workers"

    first, second, third = (
        FakeMsg(reply="_INBOX.1"),
        FakeMsg(reply="_INBOX.2"),
        FakeMsg(reply="_INBOX.3"),
    )
    for msg in (first, second, third):
        await captured["cb"](msg)

    assert first.settled == [("respond", b"pong")]
    assert second.settled == []
    assert third.settled == []


@pytest.mark.asyncio
async def test_consumer_acks_naks_and_terminates() -> None:
    ok, failing, poison = FakeMsg(), FakeMsg(delivered=2), FakeMsg()

    async def handler(msg: FakeMsg) -> None:
        if msg is failing:
            raise RuntimeError("transient")
        if msg is poison:
            raise PoisonMessage("bad payload")

    js = FakeJetStream([FakeSubscription([[ok, failing, poison]])])
    client = FakeClient(js)
    consumer = consumer_for(client, handler, backoff=(1.0, 5.0))

    await run_until_idle(consumer, js)

    assert ok.settled == [("ack", None)]
    assert failing.settled == [("nak", 5.0)]
    assert poison.settled == [("term", None)]
    stream, durable, config = client.consumers[0]
    assert (stream, durable) == ("DEMO", "demo-group")
    assert config.filter_subject == "demo.event"
    assert config.ack_policy.value == "explicit"


@pytest.mark.asyncio
async def test_consumer_rebinds_after_losing_the_connection() -> None:
    handled: list[FakeMsg] = []

    async def handler(msg: FakeMsg) -> None:
        handled.append(msg)

    after = FakeMsg()
    js = FakeJetStream(
        [FakeSubscription([ConnectionClosedError()]), FakeSubscription([[after]])]
    )
    consumer = consumer_for(FakeClient(js), handler)

    await run_until_idle(consumer, js)

    assert js.binds == 2
    assert handled == [after]
    assert after.settled == [("ack", None)]


@pytest.mark.asyncio
async def test_consumer_keeps_its_binding_on_a_bare_fetch_timeout() -> None:
    handled: list[FakeMsg] = []

    async def handler(msg: FakeMsg) -> None:
        handled.append(msg)

    after = FakeMsg()
    js = FakeJetStream([FakeSubscription([TimeoutError(), [after]])])
    consumer = consumer_for(FakeClient(js), handler)

    await run_until_idle(consumer, js)

    assert js.binds == 1
    assert handled == [after]


@pytest.mark.asyncio
async def test_connect_reuses_a_reconnecting_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[SimpleNamespace] = []

    async def fake_connect(**_: object) -> SimpleNamespace:
        connection = SimpleNamespace(is_connected=True, is_closed=False)
        opened.append(connection)
        return connection

    monkeypatch.setattr(nats_helpers, "connect", fake_connect)
    client = NATSClient(NATSSettings())
    first = await client.connect()
    first.is_connected = False

    assert await client.connect() is first
    first.is_closed = True
    assert await client.connect() is not first
    assert len(opened) == 2


def test_backoff_repeats_the_last_delay() -> None:
    consumer = consumer_for(
        FakeClient(FakeJetStream([])), handler=None, backoff=(1.0, 5.0)
    )

    assert [consumer.delay_for(n) for n in (1, 2, 3, 9)] == [1.0, 5.0, 5.0, 5.0]
    assert (
        consumer_for(FakeClient(FakeJetStream([])), handler=None, backoff=()).delay_for(
            3
        )
        == 0.0
    )
