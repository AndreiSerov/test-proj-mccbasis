from faststream.rabbit import ExchangeType, RabbitBroker, RabbitExchange, RabbitQueue

from payments.domain.payment import PAYMENT_NEW_EVENT

PAYMENTS_EXCHANGE = RabbitExchange("payments", type=ExchangeType.DIRECT, durable=True)
"""Where the Relay publishes Outbox events, routed by event type."""

DLX_EXCHANGE = RabbitExchange("payments.dlx", type=ExchangeType.DIRECT, durable=True)
"""Direct exchange that parks messages which are not retried any more."""

DLQ_NAME = "payments.new.dlq"
DLQ_QUEUE = RabbitQueue(DLQ_NAME, durable=True, routing_key=DLQ_NAME)

PAYMENTS_NEW_QUEUE = RabbitQueue(
    PAYMENT_NEW_EVENT,
    durable=True,
    routing_key=PAYMENT_NEW_EVENT,
    arguments={
        "x-dead-letter-exchange": DLX_EXCHANGE.name,
        "x-dead-letter-routing-key": DLQ_NAME,
    },
)
"""Durable queue the handler consumes; bound to the exchange by the subscriber.

A message rejected without requeue is dead-lettered to the DLQ by the broker.
Changing these arguments needs the queue deleted once on an existing broker.
"""


def retry_queue_name(delay: float) -> str:
    """`payments.new.retry.1s` for 1, `...0.1s` for 0.1: the name follows the delay."""
    return f"{PAYMENT_NEW_EVENT}.retry.{delay:g}s"


def retry_queue(delay: float) -> RabbitQueue:
    """Holds a message `delay` seconds, then dead-letters it to `payments.new`."""
    return RabbitQueue(
        retry_queue_name(delay),
        durable=True,
        arguments={
            "x-message-ttl": round(delay * 1000),
            "x-dead-letter-exchange": PAYMENTS_EXCHANGE.name,
            "x-dead-letter-routing-key": PAYMENT_NEW_EVENT,
        },
    )


async def declare_retry_topology(
    broker: RabbitBroker, retry_delays: tuple[float, ...]
) -> None:
    """Declare the Retry queues, the DLX and the DLQ (idempotent; broker connected).

    Runs before the subscriber starts, so the DLX exists when `payments.new` can
    first dead-letter a message.
    """
    for delay in retry_delays:
        await broker.declare_queue(retry_queue(delay))
    dlx = await broker.declare_exchange(DLX_EXCHANGE)
    dlq = await broker.declare_queue(DLQ_QUEUE)
    await dlq.bind(dlx, routing_key=DLQ_NAME)
