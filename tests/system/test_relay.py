"""Relay against a real Postgres, with a scripted publish standing in for the broker."""

import asyncio
import collections
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

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
