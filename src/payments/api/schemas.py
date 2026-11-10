import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from payments.domain.payment import Currency, PaymentStatus


class PaymentAccepted(BaseModel):
    payment_id: uuid.UUID
    status: PaymentStatus
    created_at: datetime


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
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
