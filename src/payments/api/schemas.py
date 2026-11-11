import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from payments.db.models import Payment
from payments.domain.payment import Currency, PaymentStatus


class PaymentAccepted(BaseModel):
    payment_id: uuid.UUID
    status: PaymentStatus
    created_at: datetime

    @classmethod
    def from_payment(cls, payment: "Payment") -> "PaymentAccepted":
        return cls(
            payment_id=payment.id,
            status=payment.status,
            created_at=payment.created_at,
        )


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    payment_id: uuid.UUID = Field(validation_alias="id")
    amount: Decimal
    currency: Currency
    description: str
    metadata: dict[str, Any] = Field(validation_alias="metadata_")
    status: PaymentStatus
    webhook_url: str
    created_at: datetime
    processed_at: datetime | None

    @field_serializer("amount")
    def _amount_as_string(self, value: Decimal) -> str:
        return f"{value:.2f}"
