from decimal import Decimal
from typing import Annotated, Any

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator

from payments.domain.payment import Currency


def _has_nul(value: object) -> bool:
    """PostgreSQL text and JSONB cannot store U+0000, so reject it up front."""
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_has_nul(k) or _has_nul(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_nul(v) for v in value)
    return False


class CreatePayment(BaseModel):
    """The validated body of a create-Payment request."""

    amount: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]
    currency: Currency
    description: Annotated[str, Field(min_length=1, max_length=255)]
    metadata: dict[str, Any] = Field(default_factory=dict)
    webhook_url: AnyHttpUrl

    @field_validator("description", "metadata")
    @classmethod
    def _no_nul(cls, value: Any) -> Any:
        if _has_nul(value):
            raise ValueError("must not contain the NUL character")
        return value
