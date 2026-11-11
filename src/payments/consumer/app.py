import logging
import uuid

import httpx
from faststream import FastStream
from faststream.rabbit import Channel, RabbitBroker, RabbitMessage
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from payments.consumer.confirms import require_ack
from payments.consumer.errors import Unprocessable
from payments.consumer.handler import PaymentHandler
from payments.consumer.relay import Relay
from payments.consumer.retry import FailureRouter
from payments.consumer.topology import (
    PAYMENTS_EXCHANGE,
    PAYMENTS_NEW_QUEUE,
    declare_retry_topology,
)
from payments.consumer.webhook import PinningTransport
from payments.core.logging import configure_logging, payment_context
from payments.core.settings import Settings, load_settings
from payments.db.models import OutboxEvent
from payments.domain.emulator import Emulator

logger = logging.getLogger(__name__)


POOL_MARGIN = 2
"""Connections beyond one per handler: the Relay's, plus one spare."""


class PaymentNewMessage(BaseModel):
    """The `payments.new` body: at least a `payment_id`."""

    payment_id: uuid.UUID


class _Resources:
    """What the consumer opens at startup and closes at shutdown."""

    def __init__(self, settings: Settings, broker: RabbitBroker) -> None:
        # Every in-flight handler holds a connection through the Emulator, and the
        # Relay holds one per iteration: size for both, with no overflow beyond it.
        self.engine: AsyncEngine = create_async_engine(
            settings.database_url,
            pool_size=settings.consumer_concurrency + POOL_MARGIN,
            max_overflow=0,
        )
        self.http = httpx.AsyncClient(transport=PinningTransport())
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
            webhook_allowed_hosts=settings.webhook_allowed_hosts,
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
            require_ack(confirmation, f"outbox event {event.id}")

        self.relay = Relay(
            session_factory=session_factory,
            publish=publish,
            batch_size=settings.relay_batch_size,
            poll_interval=settings.relay_poll_interval,
            publish_timeout=settings.relay_publish_timeout,
        )

    async def close(self) -> None:
        await self.http.aclose()
        await self.engine.dispose()


def create_app(settings: Settings) -> FastStream:
    # The library's own "faststream" logger prints a non-JSON format and does not
    # propagate; a plain stdlib logger sends its lines through the JSON handler.
    framework_logger = logging.getLogger("payments.faststream")
    # httpx logs every request's full URL at INFO, userinfo and query included.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    broker = RabbitBroker(
        settings.rabbitmq_url,
        graceful_timeout=settings.graceful_timeout,
        logger=framework_logger,
    )
    state: dict[str, _Resources] = {}
    failures = FailureRouter(broker.publish, settings.retry_delays)

    # RabbitMQ delivers at most `prefetch_count` unacked messages, each handled in
    # its own task, so the prefetch is the concurrency.
    @broker.subscriber(
        PAYMENTS_NEW_QUEUE,
        PAYMENTS_EXCHANGE,
        channel=Channel(prefetch_count=settings.consumer_concurrency),
    )
    async def handle_payment_new(message: RabbitMessage) -> None:
        # Parsing is inside the guard too: a malformed body is a failed message.
        payment_id: uuid.UUID | None = None
        try:
            try:
                payment_id = PaymentNewMessage.model_validate_json(
                    message.body
                ).payment_id
            except ValidationError as error:
                raise Unprocessable(f"malformed message: {error}") from error
            with payment_context(payment_id):
                await state["resources"].handler.handle(payment_id)
        except Exception as error:
            with payment_context(payment_id):
                await failures.on_failed_message(message, error)

    app = FastStream(broker, logger=framework_logger)

    @app.on_startup
    async def open_resources() -> None:
        await broker.connect()
        await declare_retry_topology(broker, settings.retry_delays)
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
    configure_logging()
    return create_app(load_settings())
