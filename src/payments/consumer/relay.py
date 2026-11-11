import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from payments.db.models import OutboxEvent

logger = logging.getLogger(__name__)

ERROR_PAUSE = 1.0
"""Seconds to wait after a failed iteration before trying again."""

STOP_TIMEOUT = 5.0
"""Seconds `stop` waits for the current iteration before cancelling it."""

Publish = Callable[[OutboxEvent], Awaitable[Any]]


class Relay:
    """Publishes Outbox events in id order and marks `published_at` (ADR-0001)."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        publish: Publish,
        batch_size: int,
        poll_interval: float,
        publish_timeout: float,
    ) -> None:
        self._session_factory = session_factory
        self._publish = publish
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._publish_timeout = publish_timeout
        self._stopping = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("Relay is already running")
        self._task = asyncio.create_task(self._run(), name="relay")

    async def stop(self) -> None:
        """Let the current iteration finish, then end; cancel after `STOP_TIMEOUT`."""
        if self._task is None:
            return
        self._stopping.set()
        done, _ = await asyncio.wait({self._task}, timeout=STOP_TIMEOUT)
        if not done:
            logger.warning("relay did not stop in %ss; cancelling", STOP_TIMEOUT)
            self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        self._stopping.clear()

    async def _pause(self, seconds: float) -> None:
        """Sleep, but wake at once when stopping."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), seconds)

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                published = await self.publish_batch()
            except Exception:
                logger.exception("relay iteration failed; retrying")
                await self._pause(ERROR_PAUSE)
                continue
            if published == 0:
                await self._pause(self._poll_interval)

    async def publish_batch(self) -> int:
        """Claim one batch, publish it in id order and mark each confirmed event.

        The claim keeps its row locks for the whole batch. The batch ends early,
        and the events confirmed so far are still marked and committed, when a
        publish fails or times out, when the Relay is stopping, or when the task
        is cancelled. A failure or cancellation is then raised: the failed event
        and the ones after it stay unpublished for the next iteration.
        """
        failure: BaseException | None = None
        published = 0
        try:
            async with self._session_factory() as session, session.begin():
                events = (
                    (
                        await session.execute(
                            select(OutboxEvent)
                            .where(OutboxEvent.published_at.is_(None))
                            .order_by(OutboxEvent.id)
                            .limit(self._batch_size)
                            .with_for_update(skip_locked=True)
                        )
                    )
                    .scalars()
                    .all()
                )
                for event in events:
                    if self._stopping.is_set():
                        break
                    try:
                        await asyncio.wait_for(
                            self._publish(event), self._publish_timeout
                        )
                    except BaseException as error:  # noqa: BLE001  # commit the prefix, then re-raise
                        # BaseException so a cancellation (`stop` after STOP_TIMEOUT)
                        # also commits it. A cancel is delivered once, so the commit
                        # below can still await; the error is re-raised after it.
                        failure = error
                        break
                    event.published_at = datetime.now(UTC)
                    published += 1
        except Exception as commit_error:
            if failure is not None:
                logger.error(
                    "relay publish failed before the commit failed: %r", failure
                )
                raise commit_error from failure
            raise
        if failure is not None:
            raise failure
        return published
