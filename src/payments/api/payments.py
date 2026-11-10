import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from payments.api.auth import ApiKeyAuth
from payments.api.schemas import PaymentAccepted, PaymentOut
from payments.core.logging import payment_context
from payments.db.models import IDEMPOTENCY_KEY_CONSTRAINT, OutboxEvent, Payment
from payments.domain.create_payment import CreatePayment
from payments.domain.payment import PAYMENT_NEW_EVENT
from payments.domain.request_hash import request_hash

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/payments", dependencies=[ApiKeyAuth])


async def get_session(request: Request):
    async with request.app.state.session_factory() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_session)]


async def _find_by_key(session: AsyncSession, key: str) -> Payment | None:
    return (
        await session.execute(select(Payment).where(Payment.idempotency_key == key))
    ).scalar_one_or_none()


def _replay(existing: Payment, new_hash: str) -> PaymentAccepted:
    """Answer a repeated create: the original Payment, or 409 for a different body."""
    if existing.request_hash != new_hash:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Idempotency-Key was already used with a different request body",
        )
    return PaymentAccepted.from_payment(existing)


def _is_idempotency_conflict(error: IntegrityError) -> bool:
    """True only for a unique violation of the Idempotency key constraint."""
    cause = error.orig.__cause__ if error.orig is not None else None
    return (
        getattr(error.orig, "sqlstate", None) == "23505"
        and getattr(cause, "constraint_name", None) == IDEMPOTENCY_KEY_CONSTRAINT
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=PaymentAccepted)
async def create_payment(
    body: CreatePayment,
    session: DbSession,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=255)],
) -> PaymentAccepted:
    new_hash = request_hash(body)
    async with session.begin():
        existing = await _find_by_key(session, idempotency_key)
        if existing is not None:
            return _replay(existing, new_hash)

    payment = Payment(
        id=uuid.uuid4(),
        amount=body.amount,
        currency=body.currency,
        description=body.description,
        metadata_=body.metadata,
        idempotency_key=idempotency_key,
        request_hash=new_hash,
        webhook_url=str(body.webhook_url),
    )
    try:
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
    except IntegrityError as error:
        if not _is_idempotency_conflict(error):
            raise
        # Lost a race: re-read the committed winner, apply the same rule.
        async with session.begin():
            winner = await _find_by_key(session, idempotency_key)
            if winner is None:  # pragma: no cover - the row cannot vanish
                raise
            return _replay(winner, new_hash)
    with payment_context(payment.id):
        logger.info("payment created")
    return PaymentAccepted.from_payment(payment)


@router.get("/{payment_id}", response_model=PaymentOut)
async def get_payment(payment_id: uuid.UUID, session: DbSession) -> Payment:
    payment = await session.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    with payment_context(payment_id):
        logger.info("payment fetched")
    return payment
