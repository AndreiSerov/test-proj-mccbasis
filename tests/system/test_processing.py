"""Seam 1: create over the API, the consumer (Relay + handler) runs in this loop."""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Any

import aio_pika
import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from webhook_receiver import WebhookReceiver, serve_webhook_receiver

from conftest import API_KEY
from payments.api.app import create_app as create_api
from payments.consumer.app import create_app as create_consumer
from payments.core.settings import Settings

AUTH = {"X-API-Key": API_KEY}


async def eventually[T](
    check: Callable[[], Awaitable[T | None]], timeout: float = 10.0
) -> T:
    """Poll until `check` returns something other than None, or fail."""
    deadline = time.monotonic() + timeout
    while True:
        result = await check()
        if result is not None:
            return result
        if time.monotonic() > deadline:
            pytest.fail(f"condition not met within {timeout}s")
        await asyncio.sleep(0.05)


@pytest.fixture
async def receiver() -> AsyncIterator[WebhookReceiver]:
    async with serve_webhook_receiver() as r:
        yield r


@pytest.fixture
async def db(migrated_settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_settings.database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def api(migrated_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_api(migrated_settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await app.state.engine.dispose()


async def _mark_all_published(db: AsyncEngine) -> None:
    async with db.begin() as conn:
        await conn.execute(
            text("UPDATE outbox SET published_at = now() WHERE published_at IS NULL")
        )


@pytest.fixture(autouse=True)
async def clean_slate(migrated_settings: Settings, db: AsyncEngine) -> None:
    """Start each test with an empty queue and no leftover unpublished events."""
    await _mark_all_published(db)
    connection = await aio_pika.connect_robust(migrated_settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        queue = await channel.declare_queue("payments.new", durable=True)
        await queue.purge()
    for name in [DLQ, *RETRY_QUEUES]:
        await _purge_if_exists(migrated_settings, name)


DLQ = "payments.new.dlq"
SHORT_DELAYS = (0.1, 0.2, 0.4)
RETRY_QUEUES = [f"payments.new.retry.{d}s" for d in SHORT_DELAYS]


async def _purge_if_exists(settings: Settings, name: str) -> None:
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        try:
            queue = await channel.declare_queue(name, passive=True)
        except aio_pika.exceptions.ChannelClosed:  # 404: not declared yet
            return
        await queue.purge()


async def dlq_messages(
    settings: Settings, payment_id: str | None = None, timeout: float = 10.0
) -> list[aio_pika.abc.AbstractIncomingMessage]:
    """Wait until the DLQ holds a message (for `payment_id`, if given); read them."""
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        queue = await channel.declare_queue(DLQ, passive=True)
        found: list[aio_pika.abc.AbstractIncomingMessage] = []

        async def arrived() -> bool | None:
            while (message := await queue.get(fail=False)) is not None:
                found.append(message)  # unacked; returned to the queue on close
            matching = [
                m for m in found if payment_id is None or payment_id.encode() in m.body
            ]
            return True if matching else None

        await eventually(arrived, timeout=timeout)
        return found


@pytest.fixture
async def start_consumer(
    migrated_settings: Settings,
) -> AsyncIterator[Callable[..., Awaitable[None]]]:
    """Start the consumer app with fast settings; stopped after the test."""
    apps: list[Any] = []

    async def start(**overrides: Any) -> None:
        settings = migrated_settings.model_copy(
            update={
                "emulator_min_delay": 0,
                "emulator_max_delay": 0,
                "emulator_success_rate": 1.0,
                "relay_poll_interval": 0.05,
                "webhook_timeout": 2,
                "retry_delays": SHORT_DELAYS,
                **overrides,
            }
        )
        app = create_consumer(settings)
        await app.start()
        apps.append(app)

    yield start
    for app in apps:
        await app.stop()


def body(webhook_url: str, **overrides: Any) -> dict[str, Any]:
    return {
        "amount": "10.50",
        "currency": "USD",
        "description": "Order 42",
        "metadata": {"order_id": 42},
        "webhook_url": webhook_url,
        **overrides,
    }


async def create_payment(api: httpx.AsyncClient, webhook_url: str) -> str:
    response = await api.post(
        "/api/v1/payments",
        json=body(webhook_url),
        headers={**AUTH, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 202
    return response.json()["payment_id"]


async def get_payment(api: httpx.AsyncClient, payment_id: str) -> dict[str, Any]:
    response = await api.get(f"/api/v1/payments/{payment_id}", headers=AUTH)
    return response.json()


async def test_successful_payment_is_webhooked_and_succeeded(
    api: httpx.AsyncClient,
    receiver: WebhookReceiver,
    start_consumer: Callable[..., Awaitable[None]],
) -> None:
    await start_consumer(emulator_success_rate=1.0)

    payment_id = await create_payment(api, receiver.url)

    calls = await webhooks_for(receiver, payment_id, 1)
    payment = await get_payment(api, payment_id)
    assert len(calls) == 1
    hook = calls[0]
    assert hook["status"] == "succeeded"
    assert hook["amount"] == "10.50"
    assert hook["currency"] == "USD"
    assert hook["metadata"] == {"order_id": 42}
    assert payment["status"] == "succeeded"
    assert payment["processed_at"] is not None
    assert datetime.fromisoformat(hook["processed_at"]) == datetime.fromisoformat(
        payment["processed_at"]
    )


async def webhooks_for(receiver: WebhookReceiver, payment_id: str, count: int):
    async def arrived() -> list[dict[str, Any]] | None:
        calls = receiver.for_payment(payment_id)
        return calls if len(calls) >= count else None

    return await eventually(arrived)


async def assert_dlq_empty(settings: Settings) -> None:
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        queue = await channel.declare_queue(DLQ, passive=True)
        assert queue.declaration_result.message_count == 0


async def test_declined_payment_is_failed_and_webhooked_once(
    migrated_settings: Settings,
    api: httpx.AsyncClient,
    receiver: WebhookReceiver,
    start_consumer: Callable[..., Awaitable[None]],
) -> None:
    await start_consumer(emulator_success_rate=0.0)

    payment_id = await create_payment(api, receiver.url)

    calls = await webhooks_for(receiver, payment_id, 1)
    await asyncio.sleep(0.3)  # a wrongly retried Decline would show up as a 2nd call
    assert [c["status"] for c in receiver.for_payment(payment_id)] == ["failed"]
    await assert_dlq_empty(migrated_settings)
    assert calls[0]["processed_at"] is not None
    assert (await get_payment(api, payment_id))["status"] == "failed"


async def test_outbox_event_is_marked_published_after_create(
    api: httpx.AsyncClient,
    receiver: WebhookReceiver,
    db: AsyncEngine,
    start_consumer: Callable[..., Awaitable[None]],
) -> None:
    payment_id = await create_payment(api, receiver.url)
    assert await published_at(db, payment_id) is None  # no consumer yet

    await start_consumer()

    assert await eventually(lambda: published_at(db, payment_id)) is not None


async def published_at(db: AsyncEngine, payment_id: str) -> datetime | None:
    async with db.connect() as conn:
        result = await conn.execute(
            text("SELECT published_at FROM outbox WHERE aggregate_id = :id"),
            {"id": payment_id},
        )
        return result.scalar_one()


async def test_event_published_twice_runs_the_emulator_once(
    api: httpx.AsyncClient,
    receiver: WebhookReceiver,
    migrated_settings: Settings,
    start_consumer: Callable[..., Awaitable[None]],
) -> None:
    await start_consumer()
    payment_id = await create_payment(api, receiver.url)
    await webhooks_for(receiver, payment_id, 1)
    before = await get_payment(api, payment_id)

    connection = await aio_pika.connect_robust(migrated_settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        exchange = await channel.get_exchange("payments")
        for _ in range(2):
            await exchange.publish(
                aio_pika.Message(
                    f'{{"payment_id": "{payment_id}"}}'.encode(),
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                ),
                routing_key="payments.new",
            )

    calls = await webhooks_for(receiver, payment_id, 3)
    after = await get_payment(api, payment_id)
    assert {c["status"] for c in calls} == {"succeeded"}
    assert {c["processed_at"] for c in calls} == {before["processed_at"]}
    assert after["status"] == "succeeded"
    assert after["processed_at"] == before["processed_at"]


async def test_payment_is_settled_even_if_the_webhook_endpoint_fails(
    api: httpx.AsyncClient,
    receiver: WebhookReceiver,
    start_consumer: Callable[..., Awaitable[None]],
) -> None:
    receiver.status = 500
    await start_consumer()

    payment_id = await create_payment(api, receiver.url)

    await webhooks_for(receiver, payment_id, 1)
    assert (await get_payment(api, payment_id))["status"] == "succeeded"


async def test_unroutable_event_stays_unpublished_until_it_can_be_routed(
    api: httpx.AsyncClient,
    receiver: WebhookReceiver,
    db: AsyncEngine,
    migrated_settings: Settings,
    start_consumer: Callable[..., Awaitable[None]],
) -> None:
    await start_consumer()
    connection = await aio_pika.connect_robust(migrated_settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        queue = await channel.declare_queue("payments.new", durable=True)
        exchange = await channel.get_exchange("payments")
        await queue.unbind(exchange, routing_key="payments.new")

        payment_id = await create_payment(api, receiver.url)
        await asyncio.sleep(0.5)  # several Relay iterations (poll interval 0.05 s)
        assert await published_at(db, payment_id) is None
        assert receiver.for_payment(payment_id) == []

        await queue.bind(exchange, routing_key="payments.new")

        await webhooks_for(receiver, payment_id, 1)
        assert await published_at(db, payment_id) is not None


async def test_webhook_timeout_is_a_total_deadline_not_per_phase(
    api: httpx.AsyncClient,
    receiver: WebhookReceiver,
    start_consumer: Callable[..., Awaitable[None]],
) -> None:
    # The receiver sends a byte every 0.1 s for 5 s: no single read ever times out,
    # so only a total deadline makes the consumer hang up early.
    receiver.drip_seconds = 5
    await start_consumer(webhook_timeout=0.5)

    payment_id = await create_payment(api, receiver.url)

    await eventually(lambda: _abandoned(receiver, payment_id), timeout=2.5)


async def _abandoned(receiver: WebhookReceiver, payment_id: str) -> bool | None:
    return payment_id in receiver.abandoned or None


@pytest.mark.parametrize(
    "garbage",
    [
        b"not json",
        b'{"payment_id": "nope"}',
        b'{"payment_id": "%s"}' % uuid.uuid4().bytes.hex().encode(),
    ],
    ids=["malformed", "bad-uuid", "unknown-payment"],
)
async def test_bad_messages_do_not_stop_the_consumer(
    api: httpx.AsyncClient,
    receiver: WebhookReceiver,
    migrated_settings: Settings,
    start_consumer: Callable[..., Awaitable[None]],
    garbage: bytes,
) -> None:
    await start_consumer()
    connection = await aio_pika.connect_robust(migrated_settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        exchange = await channel.get_exchange("payments")
        await exchange.publish(aio_pika.Message(garbage), routing_key="payments.new")

    payment_id = await create_payment(api, receiver.url)

    await webhooks_for(receiver, payment_id, 1)
    assert (await get_payment(api, payment_id))["status"] == "succeeded"


async def test_webhook_failing_twice_is_retried_and_delivered_on_the_third_call(
    api: httpx.AsyncClient,
    receiver: WebhookReceiver,
    migrated_settings: Settings,
    start_consumer: Callable[..., Awaitable[None]],
) -> None:
    receiver.script = [(500, 0), (503, 0)]
    await start_consumer()

    payment_id = await create_payment(api, receiver.url)

    calls = await webhooks_for(receiver, payment_id, 3)
    await asyncio.sleep(0.5)  # longer than all retry delays: no 4th call follows
    assert len(receiver.for_payment(payment_id)) == 3
    assert {c["status"] for c in calls} == {"succeeded"}
    assert len({c["processed_at"] for c in calls}) == 1  # the Emulator ran once
    payment = await get_payment(api, payment_id)
    assert payment["status"] == "succeeded"
    assert payment["processed_at"] == calls[0]["processed_at"]
    await assert_dlq_empty(migrated_settings)


async def test_webhook_always_failing_ends_in_the_dlq_after_four_calls(
    api: httpx.AsyncClient,
    receiver: WebhookReceiver,
    migrated_settings: Settings,
    start_consumer: Callable[..., Awaitable[None]],
) -> None:
    receiver.status = 500
    await start_consumer()

    payment_id = await create_payment(api, receiver.url)

    (message,) = await dlq_messages(migrated_settings, payment_id)
    assert json.loads(message.body)["payment_id"] == payment_id
    assert "500" in str(message.headers["x-last-error"])
    assert message.headers["x-attempt"] == 4
    await asyncio.sleep(0.5)
    assert len(receiver.for_payment(payment_id)) == 4
    payment = await get_payment(api, payment_id)
    assert payment["status"] == "succeeded"  # settled once; only the Webhook failed
    assert {c["processed_at"] for c in receiver.for_payment(payment_id)} == {
        payment["processed_at"]
    }


async def test_webhook_timeout_is_a_delivery_failure_and_is_retried(
    api: httpx.AsyncClient,
    receiver: WebhookReceiver,
    migrated_settings: Settings,
    start_consumer: Callable[..., Awaitable[None]],
) -> None:
    receiver.script = [(200, 1.0)]  # slower than the timeout, then healthy
    await start_consumer(webhook_timeout=0.3)

    payment_id = await create_payment(api, receiver.url)

    await webhooks_for(receiver, payment_id, 2)
    await asyncio.sleep(0.3)
    assert len(receiver.for_payment(payment_id)) == 2
    await assert_dlq_empty(migrated_settings)


@pytest.mark.parametrize(
    "garbage",
    [
        b"not json",
        b'{"payment_id": "nope"}',
        b'{"payment_id": "%s"}' % str(uuid.uuid4()).encode(),
        b'{"payment_id": 5}',
        b"[]",
    ],
    ids=["malformed", "bad-uuid", "unknown-payment", "int-id", "not-an-object"],
)
async def test_unprocessable_message_goes_straight_to_the_dlq(
    receiver: WebhookReceiver,
    migrated_settings: Settings,
    start_consumer: Callable[..., Awaitable[None]],
    garbage: bytes,
) -> None:
    await start_consumer(
        retry_delays=(30,)
    )  # a retry would take far longer than the wait

    await _publish_raw(migrated_settings, garbage)

    (message,) = await dlq_messages(migrated_settings, timeout=5)
    assert message.body == garbage
    assert message.headers["x-last-error"]


async def _publish_raw(settings: Settings, body: bytes) -> None:
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        exchange = await channel.get_exchange("payments")
        await exchange.publish(aio_pika.Message(body), routing_key="payments.new")


async def test_retry_and_dead_letter_topology_is_declared_durable_on_a_fresh_broker(
    migrated_settings: Settings,
    start_consumer: Callable[..., Awaitable[None]],
) -> None:
    connection = await aio_pika.connect_robust(migrated_settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        for name in [DLQ, *RETRY_QUEUES]:
            await channel.queue_delete(name)  # no error if it does not exist
        await channel.exchange_delete("payments.dlx")

        await start_consumer()

        # Re-declaring with the expected durability and arguments only succeeds
        # if the broker already holds an identical queue or exchange.
        await channel.declare_exchange(
            "payments.dlx", aio_pika.ExchangeType.DIRECT, durable=True
        )
        for name, delay_ms in zip(RETRY_QUEUES, (100, 200, 400), strict=True):
            await channel.declare_queue(
                name,
                durable=True,
                arguments={
                    "x-message-ttl": delay_ms,
                    "x-dead-letter-exchange": "payments",
                    "x-dead-letter-routing-key": "payments.new",
                },
            )
        await channel.declare_queue(DLQ, durable=True)
        # payments.dlx routes to the DLQ.
        dlx = await channel.get_exchange("payments.dlx")
        await dlx.publish(aio_pika.Message(b"probe"), routing_key=DLQ)

    (message,) = await dlq_messages(migrated_settings, timeout=5)
    assert message.body == b"probe"


async def test_garbage_attempt_header_is_treated_as_the_first_attempt(
    api: httpx.AsyncClient,
    receiver: WebhookReceiver,
    db: AsyncEngine,
    migrated_settings: Settings,
    start_consumer: Callable[..., Awaitable[None]],
) -> None:
    receiver.status = 500
    payment_id = await create_payment(api, receiver.url)
    await _mark_all_published(db)  # only the hand-published message below counts
    await start_consumer()

    connection = await aio_pika.connect_robust(migrated_settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        exchange = await channel.get_exchange("payments")
        await exchange.publish(
            aio_pika.Message(
                json.dumps({"payment_id": payment_id}).encode(),
                headers={"x-attempt": "abc"},
            ),
            routing_key="payments.new",
        )

    (message,) = await dlq_messages(migrated_settings, payment_id)
    assert message.headers["x-attempt"] == 4
    await asyncio.sleep(0.3)
    assert len(receiver.for_payment(payment_id)) == 4


async def test_refused_republish_is_paused_not_spun_in_a_hot_loop(
    api: httpx.AsyncClient,
    receiver: WebhookReceiver,
    migrated_settings: Settings,
    start_consumer: Callable[..., Awaitable[None]],
) -> None:
    receiver.status = 500
    await start_consumer(retry_delays=(1,))
    connection = await aio_pika.connect_robust(migrated_settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        # With the Retry queue gone the republish is returned as unroutable.
        await channel.queue_delete("payments.new.retry.1s")

        payment_id = await create_payment(api, receiver.url)
        await webhooks_for(receiver, payment_id, 1)
        await asyncio.sleep(2.5)

    # One call per ~1 s pause (about 3 in all); a hot loop would make hundreds.
    assert len(receiver.for_payment(payment_id)) <= 5
