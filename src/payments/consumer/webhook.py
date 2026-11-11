import asyncio
import socket
from collections.abc import Awaitable, Callable, Collection
from datetime import UTC, datetime
from typing import Any

import httpx

from payments.consumer.errors import Unprocessable
from payments.db.models import Payment
from payments.domain.webhook_url import is_allowed_host, is_blocked_address, mask_url


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


Resolve = Callable[[str, int], Awaitable[list[str]]]
"""`(host, port)` -> every address the name resolves to."""


async def resolve_host(host: str, port: int) -> list[str]:
    """Resolve without blocking the event loop."""
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    )
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


async def send_webhook(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    timeout: float,
    *,
    allowed_hosts: Collection[str] = (),
    resolve: Resolve = resolve_host,
) -> None:
    """POST the Webhook; anything but a 2xx in `timeout` seconds in total fails.

    httpx timeouts apply per phase (a slow trickle never trips a read timeout),
    so the whole request also runs under one asyncio deadline.

    Unless its host is allowlisted, the host is resolved here and refused
    (Unprocessable) if any address is a Blocked address; the request then goes
    to the checked IP, so a DNS answer that changes later cannot redirect it.
    Every message shows the URL masked.
    """
    shown = mask_url(url)
    try:
        async with asyncio.timeout(timeout):
            target = httpx.URL(url)
            extensions: dict[str, Any] = {}
            headers: dict[str, str] = {}
            if not is_allowed_host(target.host, allowed_hosts):
                address = await _checked_address(target, resolve, shown)
                headers["Host"] = target.netloc.decode()
                if target.scheme == "https":
                    extensions["sni_hostname"] = target.host
                target = target.copy_with(host=address)
            response = await client.post(
                target,
                json=payload,
                headers=headers,
                extensions=extensions,
                timeout=timeout,
            )
    except socket.gaierror as error:
        raise DeliveryFailure(
            f"webhook to {shown}: cannot resolve host: {error}"
        ) from error
    except (httpx.HTTPError, TimeoutError) as error:
        detail = repr(error).replace(url, shown)
        raise DeliveryFailure(f"webhook to {shown} failed: {detail}") from error
    if not response.is_success:
        raise DeliveryFailure(f"webhook to {shown} returned {response.status_code}")


async def _checked_address(target: httpx.URL, resolve: Resolve, shown: str) -> str:
    port = target.port or (443 if target.scheme == "https" else 80)
    addresses = await resolve(target.host, port)
    blocked = [a for a in addresses if is_blocked_address(a)]
    if blocked or not addresses:
        raise Unprocessable(
            f"webhook to {shown} refused: host resolves to a blocked address"
            f" ({', '.join(blocked) or 'none'})"
        )
    return addresses[0]
