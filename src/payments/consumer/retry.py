import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from faststream.rabbit import RabbitMessage

from payments.consumer.confirms import require_ack
from payments.consumer.errors import Unprocessable
from payments.consumer.topology import DLQ_NAME, DLX_EXCHANGE, retry_queue_name

logger = logging.getLogger(__name__)

ATTEMPT_HEADER = "x-attempt"
"""Number of the delivery attempt a message is on; absent means the first."""
LAST_ERROR_HEADER = "x-last-error"
MAX_ERROR_LENGTH = 500
MAX_REQUEUE_PAUSE = 3.0


Publish = Callable[..., Awaitable[Any]]
"""`RabbitBroker.publish`-shaped: returns the publisher confirmation."""


class FailureRouter:
    """Routes a failed message to the next Retry queue or the DLQ (ADR-0002)."""

    def __init__(self, publish: Publish, retry_delays: tuple[float, ...]) -> None:
        self._publish = publish
        self._retry_delays = retry_delays
        self._requeue_pause = min(
            retry_delays[0] if retry_delays else 1, MAX_REQUEUE_PAUSE
        )

    async def on_failed_message(self, message: RabbitMessage, error: Exception) -> None:
        """Republish `message` with a confirmed publish, then ack it.

        If the republish is not confirmed the message is nacked with requeue
        after a pause, so it is never lost and a broker that keeps refusing
        republishes cannot spin the consumer (it may be handled again).
        """
        attempt = _attempt(message)
        try:
            if isinstance(error, Unprocessable) or attempt > len(self._retry_delays):
                await self._to_dlq(message, error, attempt)
            else:
                await self._to_retry(message, attempt)
        except Exception:
            logger.exception("republish failed; requeueing message")
            await asyncio.sleep(self._requeue_pause)
            await message.nack(requeue=True)
            return
        await message.ack()

    async def _to_retry(self, message: RabbitMessage, attempt: int) -> None:
        queue = retry_queue_name(self._retry_delays[attempt - 1])
        logger.warning(
            "delivery failure on attempt %d; retrying via %s", attempt, queue
        )
        confirmation = await self._publish(
            message.body,
            routing_key=queue,
            headers={ATTEMPT_HEADER: attempt + 1},
            persist=True,
        )
        require_ack(confirmation, f"retry publish to {queue}")

    async def _to_dlq(
        self, message: RabbitMessage, error: Exception, attempt: int
    ) -> None:
        logger.error("giving up after attempt %d; parking in the DLQ", attempt)
        confirmation = await self._publish(
            message.body,
            exchange=DLX_EXCHANGE,
            routing_key=DLQ_NAME,
            headers={
                ATTEMPT_HEADER: attempt,
                LAST_ERROR_HEADER: f"{type(error).__name__}: {error}"[
                    :MAX_ERROR_LENGTH
                ],
            },
            persist=True,
        )
        require_ack(confirmation, "DLQ publish")


def _attempt(message: RabbitMessage) -> int:
    try:
        return max(1, int((message.headers or {}).get(ATTEMPT_HEADER, 1)))
    except (TypeError, ValueError):
        return 1
