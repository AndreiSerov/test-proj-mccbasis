import enum

PAYMENT_NEW_EVENT = "payments.new"
"""Event type of the Outbox event written with every new Payment."""


class Currency(enum.StrEnum):
    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"


class PaymentStatus(enum.StrEnum):
    pending = "pending"
    succeeded = "succeeded"
    failed = "failed"
