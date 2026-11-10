import json
import logging
import uuid

import httpx
from faststream import FastStream
from faststream.rabbit import RabbitBroker, RabbitMessage
from pamqp.commands import Basic
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from payments.consumer.handler import PaymentHandler
from payments.consumer.relay import Relay
from payments.consumer.topology import PAYMENTS_EXCHANGE, PAYMENTS_NEW_QUEUE
from payments.core.settings import Settings, load_settings
from payments.db.models import OutboxEvent
from payments.domain.emulator import Emulator

logger = logging.getLogger(__name__)


class PublishNotConfirmed(Exception):
    """The broker did not positively confirm a publish (nack or unroutable return)."""


class _Resources:
    """What the consumer opens at startup and closes at shutdown."""

    def __init__(self, settings: Settings, broker: RabbitBroker) -> None:
        self.engine: AsyncEngine = create_async_engine(settings.database_url)
        self.http = httpx.AsyncClient()
        session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.handler = PaymentHandler(
            session_factory=session_factory,
            emulator=Emulator(
                min_delay=settings.emulator_min_delay,
                max_delay=settings.emulator_max_delay,
                success_rate=settings.emulator_success_rate,
            ),
            http=self.http,
            webhook_timeout=settings.webhook_timeout,
        )

        async def publish(event: OutboxEvent) -> None:
            # A nacked or returned (unroutable) message must not be marked
            # published: raising keeps the event for the next Relay iteration.
            confirmation = await broker.publish(
                event.payload,
                exchange=PAYMENTS_EXCHANGE,
                routing_key=event.event_type,
                persist=True,
            )
            if not isinstance(confirmation, Basic.Ack):
                raise PublishNotConfirmed(
                    f"outbox event {event.id}: broker answered {confirmation!r}"
                )

        self.relay = Relay(
            session_factory=session_factory,
            publish=publish,
            batch_size=settings.relay_batch_size,
            poll_interval=settings.relay_poll_interval,
        )

    async def close(self) -> None:
        await self.http.aclose()
        await self.engine.dispose()


async def on_delivery_failure(message: RabbitMessage, error: Exception) -> None:
    """The one place a failed message ends up; it gets the whole message.

    Ticket 05 replaces this with retry routing (attempt header) and the DLQ.
    """
    logger.error(
        "delivery failure; dropping message body=%r headers=%r",
        message.body,
        message.headers,
        exc_info=error,
    )


def create_app(settings: Settings) -> FastStream:
    broker = RabbitBroker(settings.rabbitmq_url)
    state: dict[str, _Resources] = {}

    @broker.subscriber(PAYMENTS_NEW_QUEUE, PAYMENTS_EXCHANGE)
    async def handle_payment_new(message: RabbitMessage) -> None:
        # Parsing is inside the guard too: a malformed body is a failed message.
        try:
            payment_id = uuid.UUID(json.loads(message.body)["payment_id"])
            await state["resources"].handler.handle(payment_id)
        except Exception as error:
            await on_delivery_failure(message, error)

    app = FastStream(broker)

    @app.on_startup
    async def open_resources() -> None:
        state["resources"] = _Resources(settings, broker)

    @app.after_startup
    async def start_relay() -> None:
        state["resources"].relay.start()

    @app.on_shutdown
    async def stop_relay() -> None:
        if "resources" in state:
            await state["resources"].relay.stop()

    @app.after_shutdown
    async def close_resources() -> None:
        if "resources" in state:
            await state.pop("resources").close()

    return app


def app_factory() -> FastStream:
    return create_app(load_settings())
