import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx

from payments.db.models import Payment


class DeliveryFailure(Exception):
    """A technical error while handling a message (Webhook non-2xx or timeout)."""


def _iso_utc(value: datetime) -> str:
    """ISO 8601 in UTC with a `Z` suffix, the same form the API returns."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def webhook_payload(payment: Payment) -> dict[str, Any]:
    if payment.processed_at is None:
        raise ValueError(f"payment {payment.id} has no processed_at")
    return {
        "payment_id": str(payment.id),
        "status": payment.status.value,
        "amount": str(payment.amount),
        "currency": payment.currency.value,
        "processed_at": _iso_utc(payment.processed_at),
        "metadata": payment.metadata_,
    }


async def send_webhook(
    client: httpx.AsyncClient, url: str, payload: dict[str, Any], timeout: float
) -> None:
    """POST the Webhook; anything but a 2xx in `timeout` seconds in total fails.

    httpx timeouts apply per phase (a slow trickle never trips a read timeout),
    so the whole request also runs under one asyncio deadline.
    """
    try:
        async with asyncio.timeout(timeout):
            response = await client.post(url, json=payload, timeout=timeout)
    except (httpx.HTTPError, TimeoutError) as error:
        raise DeliveryFailure(f"webhook to {url} failed: {error!r}") from error
    if not response.is_success:
        raise DeliveryFailure(f"webhook to {url} returned {response.status_code}")
