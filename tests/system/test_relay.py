"""Relay against a real Postgres, with a scripted publish standing in for the broker."""

import asyncio
import collections
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm.session import SessionTransaction

from payments.consumer.relay import Publish, Relay
from payments.core.settings import Settings
from payments.db.models import OutboxEvent


@pytest.fixture
async def db(migrated_settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_settings.database_url)
    yield engine
    await engine.dispose()


async def _seed(db: AsyncEngine, count: int) -> None:
    """Leave exactly `count` unpublished events, numbered by payload `i`."""
    async with db.begin() as conn:
        await conn.execute(
            text("UPDATE outbox SET published_at = now() WHERE published_at IS NULL")
        )
        for i in range(count):
            await conn.execute(
                text(
                    "INSERT INTO outbox(aggregate_id, event_type, payload, created_at)"
                    " VALUES (:a, 'probe',"
                    " jsonb_build_object('i', CAST(:i AS int)), now())"
                ),
                {"a": uuid.uuid4(), "i": i},
            )


def _relay(db: AsyncEngine, publish: Publish, timeout: float = 5.0) -> Relay:
    return Relay(
        session_factory=async_sessionmaker(db, expire_on_commit=False),
        publish=publish,
        batch_size=100,
        poll_interval=0.05,
        publish_timeout=timeout,
    )


async def test_a_failed_event_does_not_republish_the_ones_confirmed_before_it(
    db: AsyncEngine,
) -> None:
    await _seed(db, 5)
    sent: list[int] = []
    failures_left = 1

    async def publish(event: OutboxEvent) -> None:
        nonlocal failures_left
        i = event.payload["i"]
        if i == 3 and failures_left:
            failures_left -= 1
            raise RuntimeError("nack")
        sent.append(i)

    relay = _relay(db, publish)
    with pytest.raises(RuntimeError, match="nack"):
        await relay.publish_batch()
    assert sent == [0, 1, 2]

    assert await relay.publish_batch() == 2
    assert sent == [0, 1, 2, 3, 4]
    assert collections.Counter(sent).most_common(1)[0][1] == 1


async def test_a_publish_without_confirm_fails_the_iteration_and_a_later_one_succeeds(
    db: AsyncEngine,
) -> None:
    await _seed(db, 2)
    sent: list[int] = []
    hang = True

    async def publish(event: OutboxEvent) -> None:
        if hang:
            await asyncio.Event().wait()
        sent.append(event.payload["i"])

    relay = _relay(db, publish, timeout=0.2)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(relay.publish_batch(), timeout=3)

    hang = False
    assert await relay.publish_batch() == 2
    assert sent == [0, 1]


async def test_a_timeout_on_a_later_event_keeps_the_confirmed_prefix(
    db: AsyncEngine,
) -> None:
    await _seed(db, 4)
    sent: list[int] = []
    hang = True

    async def publish(event: OutboxEvent) -> None:
        if hang and event.payload["i"] == 2:
            await asyncio.Event().wait()
        sent.append(event.payload["i"])

    relay = _relay(db, publish, timeout=0.2)
    with pytest.raises(TimeoutError):
        await relay.publish_batch()
    assert sent == [0, 1]

    hang = False
    assert await relay.publish_batch() == 2
    assert sent == [0, 1, 2, 3]


async def test_a_cancelled_batch_does_not_republish_the_confirmed_prefix(
    db: AsyncEngine,
) -> None:
    await _seed(db, 4)
    sent: list[int] = []
    blocked = asyncio.Event()
    hang = True

    async def publish(event: OutboxEvent) -> None:
        if event.payload["i"] == 2 and hang:
            blocked.set()
            await asyncio.Event().wait()
        sent.append(event.payload["i"])

    relay = _relay(db, publish)
    batch = asyncio.create_task(relay.publish_batch())
    await blocked.wait()
    batch.cancel()
    with pytest.raises(asyncio.CancelledError):
        await batch
    assert sent == [0, 1]

    hang = False
    assert await relay.publish_batch() == 2
    assert sent == [0, 1, 2, 3]


async def test_stopping_ends_the_batch_after_the_event_in_flight(
    db: AsyncEngine,
) -> None:
    await _seed(db, 4)
    sent: list[int] = []
    in_flight = asyncio.Event()
    release = asyncio.Event()

    async def publish(event: OutboxEvent) -> None:
        if event.payload["i"] == 1 and not sent.count(1):
            in_flight.set()
            await release.wait()
        sent.append(event.payload["i"])

    relay = _relay(db, publish)
    relay.start()
    await in_flight.wait()
    stopping = asyncio.create_task(relay.stop())
    await asyncio.sleep(0.05)  # stop() has flagged the Relay
    release.set()
    await stopping
    assert sent == [0, 1]

    assert await _relay(db, publish).publish_batch() == 2
    assert sent == [0, 1, 2, 3]


async def test_a_commit_error_after_a_publish_failure_keeps_the_publish_error(
    db: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db, 2)

    async def publish(event: OutboxEvent) -> None:
        if event.payload["i"] == 1:
            raise RuntimeError("nack")

    def broken_commit(self: object, *args: object, **kwargs: object) -> None:
        raise ConnectionError("commit failed")

    monkeypatch.setattr(SessionTransaction, "commit", broken_commit)
    with pytest.raises(ConnectionError) as error:
        await _relay(db, publish).publish_batch()
    assert isinstance(error.value.__cause__, RuntimeError)
    assert "nack" in str(error.value.__cause__)
