"""Seam 1: create over the API, the consumer (Relay + handler) runs in this loop."""

import asyncio
import io
import json
import logging
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
from payments.core.logging import json_handler
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
    # The API accepts the receiver's 127.0.0.1; the consumer's check is tested
    # on its own by starting it with another allowlist.
    app = create_api(
        migrated_settings.model_copy(update={"webhook_allowed_hosts": LOCAL})
    )
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
    for name in [MAIN_QUEUE, DLQ, *RETRY_QUEUES]:
        await _purge_if_exists(migrated_settings, name)


LOCAL = ("127.0.0.1",)
MAIN_QUEUE = "payments.new"
DLQ = "payments.new.dlq"
SHORT_DELAYS = (0.1, 0.2)
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

    async def start(**overrides: Any) -> Any:
        settings = migrated_settings.model_copy(
            update={
                "emulator_min_delay": 0,
                "emulator_max_delay": 0,
                "emulator_success_rate": 1.0,
                "relay_poll_interval": 0.05,
                "webhook_timeout": 2,
                "retry_delays": SHORT_DELAYS,
                "webhook_allowed_hosts": LOCAL,  # the receiver listens on 127.0.0.1
                **overrides,
            }
        )
        app = create_consumer(settings)
        await app.start()
        apps.append(app)
        return app

    yield start
    for app in apps:
        if app.brokers[0].running:  # a test may have stopped it already
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
        queue = await channel.declare_queue(MAIN_QUEUE, passive=True)
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


async def test_webhook_always_failing_ends_in_the_dlq_after_three_calls(
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
    assert message.headers["x-attempt"] == 3
    await asyncio.sleep(0.5)
    assert len(receiver.for_payment(payment_id)) == 3
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
        for name, delay_ms in zip(RETRY_QUEUES, (100, 200), strict=True):
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
    assert message.headers["x-attempt"] == 3
    await asyncio.sleep(0.3)
    assert len(receiver.for_payment(payment_id)) == 3


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


async def create_payments(
    api: httpx.AsyncClient, receiver: WebhookReceiver, count: int
) -> list[str]:
    return [await create_payment(api, receiver.url) for _ in range(count)]


async def all_webhooked(receiver: WebhookReceiver, payment_ids: list[str]) -> None:
    async def arrived() -> bool | None:
        return True if all(receiver.for_payment(p) for p in payment_ids) else None

    await eventually(arrived, timeout=20)


async def test_ten_payments_are_processed_at_once_with_concurrency_ten(
    api: httpx.AsyncClient,
    receiver: WebhookReceiver,
    start_consumer: Callable[..., Awaitable[None]],
) -> None:
    await start_consumer(
        consumer_concurrency=10, emulator_min_delay=1, emulator_max_delay=1
    )

    payment_ids = await create_payments(api, receiver, 10)
    started = time.monotonic()
    await all_webhooked(receiver, payment_ids)

    assert time.monotonic() - started < 5  # serially this would take 10 s


async def test_payments_are_processed_one_at_a_time_with_concurrency_one(
    api: httpx.AsyncClient,
    receiver: WebhookReceiver,
    start_consumer: Callable[..., Awaitable[None]],
) -> None:
    await start_consumer(
        consumer_concurrency=1, emulator_min_delay=1, emulator_max_delay=1
    )

    payment_ids = await create_payments(api, receiver, 3)
    await all_webhooked(receiver, payment_ids)

    arrivals = sorted(receiver.arrivals[p][0] for p in payment_ids)
    gaps = [
        later - earlier for earlier, later in zip(arrivals, arrivals[1:], strict=False)
    ]
    assert all(gap >= 0.9 for gap in gaps), gaps  # each waits out the 1 s Emulator


async def test_stop_finishes_in_flight_messages_and_a_restart_does_the_rest(
    api: httpx.AsyncClient,
    receiver: WebhookReceiver,
    db: AsyncEngine,
    start_consumer: Callable[..., Awaitable[Any]],
) -> None:
    app = await start_consumer(
        consumer_concurrency=3, emulator_min_delay=1.5, emulator_max_delay=1.5
    )
    payment_ids = await create_payments(api, receiver, 6)
    for payment_id in payment_ids:
        await eventually(lambda p=payment_id: published_at(db, p))
    await asyncio.sleep(0.5)  # three Emulator runs are now under way

    await app.stop()

    settled = [p for p in payment_ids if receiver.for_payment(p)]
    assert len(settled) == 3  # the in-flight ones finished and sent their Webhooks
    for payment_id in settled:
        assert (await get_payment(api, payment_id))["status"] == "succeeded"
    waiting = [p for p in payment_ids if p not in settled]
    for payment_id in waiting:
        assert (await get_payment(api, payment_id))["status"] == "pending"

    await start_consumer(consumer_concurrency=3)
    await all_webhooked(receiver, payment_ids)
    await asyncio.sleep(0.3)  # a redelivered message would add a second call

    for payment_id in payment_ids:
        calls = receiver.for_payment(payment_id)
        payment = await get_payment(api, payment_id)
        assert len(calls) == 1  # acked once: no duplicate Emulator run or Webhook
        assert calls[0]["processed_at"] == payment["processed_at"]
        assert payment["status"] == calls[0]["status"] == "succeeded"


async def test_log_lines_from_api_and_consumer_are_json_with_the_payment_id(
    api: httpx.AsyncClient,
    receiver: WebhookReceiver,
    start_consumer: Callable[..., Awaitable[None]],
) -> None:
    stream = io.StringIO()
    handler = json_handler(stream)
    root = logging.getLogger()
    root.addHandler(handler)
    previous_level = root.level
    root.setLevel(logging.INFO)
    try:
        receiver.status = 500  # a Delivery failure makes the consumer log a retry
        await start_consumer()
        payment_id = await create_payment(api, receiver.url)
        await webhooks_for(receiver, payment_id, 2)
        other_id = await create_payment(api, receiver.url)
        await webhooks_for(receiver, other_id, 1)
        await get_payment(api, payment_id)
        await get_payment(api, other_id)
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)

    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert records
    for record in records:
        assert {"timestamp", "level", "logger", "message"} <= record.keys()
    about = [r for r in records if r.get("payment_id") == payment_id]
    assert {"payments.api.payments", "payments.consumer.handler"} <= {
        r["logger"] for r in about
    }, about
    api_messages = [
        r["message"] for r in about if r["logger"] == "payments.api.payments"
    ]
    assert sorted(api_messages) == ["payment created", "payment fetched"]
    framework = [r for r in records if r["logger"] == "payments.faststream"]
    assert any(r.get("message_id") for r in framework)  # FastStream's extras survive
    assert any("retrying" in r["message"] for r in about)  # failure routing is tagged


async def test_graceful_timeout_bounds_the_wait_for_in_flight_messages(
    api: httpx.AsyncClient,
    receiver: WebhookReceiver,
    db: AsyncEngine,
    start_consumer: Callable[..., Awaitable[Any]],
) -> None:
    app = await start_consumer(
        consumer_concurrency=3,
        emulator_min_delay=1.5,
        emulator_max_delay=1.5,
        graceful_timeout=0.2,
    )
    payment_ids = await create_payments(api, receiver, 3)
    for payment_id in payment_ids:
        await eventually(lambda p=payment_id: published_at(db, p))
    await asyncio.sleep(0.5)  # all three Emulator runs are under way

    await app.stop()

    assert not [p for p in payment_ids if receiver.for_payment(p)]  # gave up waiting
    await start_consumer(consumer_concurrency=3)
    await all_webhooked(receiver, payment_ids)
    await asyncio.sleep(0.3)
    for payment_id in payment_ids:
        (call,) = receiver.for_payment(payment_id)
        payment = await get_payment(api, payment_id)
        assert payment["status"] == call["status"] == "succeeded"
        assert payment["processed_at"] == call["processed_at"]


async def test_stopping_does_not_wait_out_a_long_relay_poll_interval(
    start_consumer: Callable[..., Awaitable[Any]],
) -> None:
    app = await start_consumer(relay_poll_interval=30)
    await asyncio.sleep(0.3)  # the Relay is idle, in its poll pause

    started = time.monotonic()
    await app.stop()

    assert time.monotonic() - started < 1


async def test_webhook_to_a_blocked_address_goes_to_the_dlq_after_one_attempt(
    api: httpx.AsyncClient,
    receiver: WebhookReceiver,
    migrated_settings: Settings,
    start_consumer: Callable[..., Awaitable[None]],
) -> None:
    await start_consumer(webhook_allowed_hosts=())  # 127.0.0.1 is not allowlisted
    payment_id = await create_payment(api, receiver.url)

    (message,) = await dlq_messages(migrated_settings, payment_id)

    assert "blocked address" in str(message.headers["x-last-error"])
    assert message.headers["x-attempt"] == 1  # no retries
    await asyncio.sleep(0.5)  # longer than all retry delays
    assert receiver.requests == []  # nothing reached the host
    assert (await get_payment(api, payment_id))["status"] == "succeeded"


async def test_webhook_to_an_allowlisted_host_is_delivered(
    api: httpx.AsyncClient,
    receiver: WebhookReceiver,
    migrated_settings: Settings,
    start_consumer: Callable[..., Awaitable[None]],
) -> None:
    await start_consumer(webhook_allowed_hosts=("other.example", "127.0.0.1"))
    payment_id = await create_payment(api, receiver.url)

    await webhooks_for(receiver, payment_id, 1)

    await assert_dlq_empty(migrated_settings)


async def test_userinfo_and_query_never_reach_logs_or_x_last_error(
    api: httpx.AsyncClient,
    receiver: WebhookReceiver,
    migrated_settings: Settings,
    start_consumer: Callable[..., Awaitable[None]],
) -> None:
    secret_url = receiver.url.replace("http://", "http://user:secret@") + "?token=x"
    stream = io.StringIO()
    handler = json_handler(stream)
    root = logging.getLogger()
    root.addHandler(handler)
    previous_level = root.level
    root.setLevel(logging.DEBUG)
    try:
        receiver.status = 500  # fails every attempt: the DLQ gets the last error
        await start_consumer()
        payment_id = await create_payment(api, secret_url)
        (message,) = await dlq_messages(migrated_settings, payment_id)
        await start_consumer(webhook_allowed_hosts=())  # and a refused one
        blocked_id = await create_payment(api, secret_url)
        blocked, *_ = await dlq_messages(migrated_settings, blocked_id)
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)

    assert "500" in str(message.headers["x-last-error"])
    assert receiver.for_payment(payment_id)  # the URL did work
    stored = (await get_payment(api, payment_id))["webhook_url"]
    assert stored == secret_url  # the stored Payment keeps the URL as given
    shown = str(message.headers["x-last-error"]) + stream.getvalue()
    for output in (shown, str(blocked.headers["x-last-error"])):
        assert "secret" not in output
        assert "token=x" not in output
    assert "?…" in str(message.headers["x-last-error"])


async def test_message_rejected_without_requeue_is_dead_lettered_by_the_broker(
    migrated_settings: Settings,
    start_consumer: Callable[..., Awaitable[Any]],
) -> None:
    app = await start_consumer()  # declares the topology
    await app.stop()  # the queue is durable; nothing consumes it now
    payment_id = str(uuid.uuid4())
    payload = json.dumps({"payment_id": payment_id}).encode()
    connection = await aio_pika.connect_robust(migrated_settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        exchange = await channel.get_exchange("payments")
        await exchange.publish(aio_pika.Message(payload), routing_key=MAIN_QUEUE)
        queue = await channel.declare_queue(MAIN_QUEUE, passive=True)

        async def delivered() -> aio_pika.abc.AbstractIncomingMessage | None:
            return await queue.get(fail=False)

        message = await eventually(delivered)
        await message.reject(requeue=False)

    (dead,) = await dlq_messages(migrated_settings, payment_id)
    assert dead.body == payload
    assert dead.headers["x-first-death-queue"] == MAIN_QUEUE
