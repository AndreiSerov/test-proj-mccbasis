from faststream.rabbit import ExchangeType, RabbitExchange, RabbitQueue

from payments.domain.payment import PAYMENT_NEW_EVENT

PAYMENTS_EXCHANGE = RabbitExchange("payments", type=ExchangeType.DIRECT, durable=True)
"""Where the Relay publishes Outbox events, routed by event type."""

PAYMENTS_NEW_QUEUE = RabbitQueue(
    PAYMENT_NEW_EVENT, durable=True, routing_key=PAYMENT_NEW_EVENT
)
"""Durable queue the handler consumes; bound to the exchange by the subscriber."""
