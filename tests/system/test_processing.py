"""Seam 1: create over the API, the consumer (Relay + handler) runs in this loop."""

import asyncio
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


async def test_declined_payment_is_failed_and_webhooked_once(
    api: httpx.AsyncClient,
    receiver: WebhookReceiver,
    start_consumer: Callable[..., Awaitable[None]],
) -> None:
    await start_consumer(emulator_success_rate=0.0)

    payment_id = await create_payment(api, receiver.url)

    calls = await webhooks_for(receiver, payment_id, 1)
    await asyncio.sleep(0.3)  # a wrongly retried Decline would show up as a 2nd call
    assert [c["status"] for c in receiver.for_payment(payment_id)] == ["failed"]
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
