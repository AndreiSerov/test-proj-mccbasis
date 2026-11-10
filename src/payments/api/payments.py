import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from payments.api.auth import ApiKeyAuth
from payments.api.schemas import PaymentAccepted, PaymentOut
from payments.db.models import OutboxEvent, Payment
from payments.domain.create_payment import CreatePayment
from payments.domain.payment import PAYMENT_NEW_EVENT
from payments.domain.request_hash import request_hash

router = APIRouter(prefix="/api/v1/payments", dependencies=[ApiKeyAuth])


async def get_session(request: Request):
    async with request.app.state.session_factory() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_session)]


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=PaymentAccepted)
async def create_payment(
    body: CreatePayment,
    session: DbSession,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=255)],
) -> PaymentAccepted:
    webhook_url = str(body.webhook_url)
    payment = Payment(
        id=uuid.uuid4(),
        amount=body.amount,
        currency=body.currency,
        description=body.description,
        metadata_=body.metadata,
        idempotency_key=idempotency_key,
        request_hash=request_hash(body),
        webhook_url=webhook_url,
    )
    async with session.begin():
        session.add(payment)
        await session.flush()
        session.add(
            OutboxEvent(
                aggregate_id=payment.id,
                event_type=PAYMENT_NEW_EVENT,
                payload={"payment_id": str(payment.id)},
            )
        )
    return PaymentAccepted(
        payment_id=payment.id, status=payment.status, created_at=payment.created_at
    )


@router.get("/{payment_id}", response_model=PaymentOut)
async def get_payment(payment_id: uuid.UUID, session: DbSession) -> Payment:
    payment = await session.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    return payment
