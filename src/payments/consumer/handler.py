import logging
import uuid
from collections.abc import Collection
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from payments.consumer.errors import Unprocessable
from payments.consumer.webhook import send_webhook, webhook_payload
from payments.db.models import Payment
from payments.domain.emulator import Emulator
from payments.domain.payment import PaymentStatus

logger = logging.getLogger(__name__)


class PaymentHandler:
    """Handles one `payments.new` message; safe to run twice for the same Payment."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        emulator: Emulator,
        http: httpx.AsyncClient,
        webhook_timeout: float,
        webhook_allowed_hosts: Collection[str],
    ) -> None:
        self._session_factory = session_factory
        self._emulator = emulator
        self._http = http
        self._webhook_timeout = webhook_timeout
        self._webhook_allowed_hosts = webhook_allowed_hosts

    async def handle(self, payment_id: uuid.UUID) -> None:
        """Settle the Payment if pending, then send its Webhook.

        Raises DeliveryFailure, EmulatorError or a DB error when the message
        should be retried (an EmulatorError rolls the transaction back and no
        Webhook is sent), and Unprocessable when it never can be processed
        (unknown payment_id, or a Webhook host that resolves to a Blocked address).
        """
        payload, url = await self._settle(payment_id)
        await send_webhook(
            self._http,
            url,
            payload,
            self._webhook_timeout,
            allowed_hosts=self._webhook_allowed_hosts,
        )

    async def _settle(self, payment_id: uuid.UUID) -> tuple[dict[str, Any], str]:
        # The Emulator runs inside the row lock, so a concurrent duplicate waits
        # for the outcome and then finds the Payment final.
        async with self._session_factory() as session, session.begin():
            payment = (
                await session.execute(
                    select(Payment).where(Payment.id == payment_id).with_for_update()
                )
            ).scalar_one_or_none()
            if payment is None:
                raise Unprocessable(f"unknown payment_id {payment_id}")
            if payment.status == PaymentStatus.pending:
                payment.status = await self._emulator.run()
                payment.processed_at = datetime.now(UTC)
                logger.info(
                    "payment %s", payment.status
                )  # tagged by the message context
            return webhook_payload(payment), payment.webhook_url
